"""Unified HTTP and WebSocket bridge for Chestnut Conference Console.

The browser sends 16 kHz PCM to this process. This process authenticates with
Alibaba Cloud Model Studio, so the DashScope API key never enters browser code.
The same single-port service runs locally and in WeChat CloudBase Run.
"""

import asyncio
import base64
import json
import logging
import os
import secrets
import time
from collections import Counter
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import WSMsgType, web
from qcloud_cos import CosConfig, CosS3Client
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


HOST = os.environ.get("CHESTNUT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("CHESTNUT_PORT", "8080")))
ROOT = Path(__file__).resolve().parent
MODEL = "qwen3.5-livetranslate-flash-realtime"
MAX_TRANSCRIPT_BYTES = 5_000_000
COS_BUCKET = os.environ.get("CHESTNUT_COS_BUCKET", "").strip()
COS_REGION = os.environ.get("TENCENTCLOUD_REGION", os.environ.get("CHESTNUT_COS_REGION", "")).strip()
LOG_PATH = Path(os.environ.get("CHESTNUT_LOG_FILE", ROOT / "logs" / "chestnut.log"))
CLOUD_SEND_TIMEOUT_SECONDS = float(os.environ.get("CHESTNUT_CLOUD_SEND_TIMEOUT", "10"))
CLOUD_RESPONSE_TIMEOUT_SECONDS = float(os.environ.get("CHESTNUT_CLOUD_RESPONSE_TIMEOUT", "60"))


def configure_logging():
    logger = logging.getLogger("chestnut")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as error:
        logger.warning("event=log_file_unavailable error_type=%s", type(error).__name__)
    return logger


LOGGER = configure_logging()


class SessionMetrics:
    def __init__(self):
        self.session_id = secrets.token_hex(6)
        self.started_at = time.monotonic()
        self.audio_chunks = 0
        self.audio_bytes = 0
        self.last_voice_at = None
        self.voice_wait_started_at = None
        self.last_cloud_event_at = time.monotonic()
        self.cloud_events = Counter()
        self.request_ids = {}

    def record_audio(self, message):
        self.audio_chunks += 1
        self.audio_bytes += len(message)
        if pcm_has_voice(message):
            self.last_voice_at = time.monotonic()

    def record_cloud_event(self, target_language, event):
        now = time.monotonic()
        self.last_cloud_event_at = now
        self.voice_wait_started_at = None
        event_type = str(event.get("type") or "unknown")
        self.cloud_events[f"{target_language}:{event_type}"] += 1
        request_id = event.get("request_id") or event.get("requestId")
        if request_id:
            self.request_ids[target_language] = str(request_id)[:128]

    def summary(self):
        return (
            f"duration_s={int(time.monotonic() - self.started_at)} "
            f"audio_chunks={self.audio_chunks} audio_bytes={self.audio_bytes} "
            f"cloud_events={sum(self.cloud_events.values())} "
            f"request_zh={self.request_ids.get('zh', '-')} "
            f"request_en={self.request_ids.get('en', '-')}"
        )


def pcm_has_voice(message):
    if len(message) < 2:
        return False
    try:
        samples = memoryview(message)[:len(message) - (len(message) % 2)].cast("h")
        return any(abs(sample) >= 700 for sample in samples[::32])
    except (TypeError, ValueError):
        return False


def transcript_time(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def markdown_quote(text):
    return "\n".join(f"> {line}" if line else ">" for line in str(text).splitlines())


def render_meeting_transcript(payload):
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")

    now = datetime.now().astimezone()
    filename = f"meeting-{now.strftime('%Y%m%d-%H%M%S')}.md"
    started_at = str(payload.get("started_at") or "")
    ended_at = str(payload.get("ended_at") or now.isoformat())
    duration = max(0, int(payload.get("duration_seconds") or 0))
    lines = [
        "---",
        "format: chestnut-meeting-transcript-v1",
        f"started_at: {json.dumps(started_at)}",
        f"ended_at: {json.dumps(ended_at)}",
        f"duration_seconds: {duration}",
        f"entries: {len(entries)}",
        "---",
        "",
        "# Chestnut Meeting Transcript",
        "",
        f"Duration: {transcript_time(duration)}",
        "",
    ]

    if not entries:
        lines.extend(["_No completed captions were recorded._", ""])
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get("text", "")).strip():
            continue
        language = "中文" if entry.get("language") == "zh" else "English"
        role = "ORIGINAL" if entry.get("role") == "original" else "TRANSLATION"
        timestamp = transcript_time(entry.get("time_seconds", 0))
        lines.extend([
            f"## {timestamp} · {language} · {role}",
            "",
            markdown_quote(str(entry["text"]).strip()),
            "",
        ])

    return filename, "\n".join(lines)


def safe_owner_id(value):
    owner = "".join(character for character in str(value or "") if character.isalnum() or character in "-_")
    return owner[:128] or "anonymous"


def save_local_transcript(filename, content):
    meetings_dir = ROOT / "meetings"
    meetings_dir.mkdir(exist_ok=True)
    output_path = meetings_dir / filename
    suffix = 2
    while output_path.exists():
        output_path = meetings_dir / filename.replace(".md", f"-{suffix}.md")
        suffix += 1
    temporary_path = output_path.with_suffix(".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(output_path)
    return {"filename": output_path.name, "storage": "local", "url": f"/meetings/{output_path.name}"}


def save_cos_transcript(filename, content, owner_id):
    secret_id = os.environ.get("TENCENTCLOUD_SECRETID") or os.environ.get("CHESTNUT_COS_SECRET_ID", "")
    secret_key = os.environ.get("TENCENTCLOUD_SECRETKEY") or os.environ.get("CHESTNUT_COS_SECRET_KEY", "")
    token = os.environ.get("TENCENTCLOUD_SESSIONTOKEN") or os.environ.get("CHESTNUT_COS_SESSION_TOKEN", "")
    if not COS_REGION:
        raise RuntimeError("TENCENTCLOUD_REGION is unavailable; configure CHESTNUT_COS_REGION")
    if not secret_id or not secret_key:
        raise RuntimeError("COS credentials are unavailable; configure a least-privilege COS service account")

    config = CosConfig(
        Region=COS_REGION,
        SecretId=secret_id,
        SecretKey=secret_key,
        Token=token or None,
        Scheme="https",
    )
    object_key = f"meetings/{safe_owner_id(owner_id)}/{filename}"
    CosS3Client(config).put_object(
        Bucket=COS_BUCKET,
        Key=object_key,
        Body=content.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    return {"filename": filename, "storage": "cos", "object_key": object_key}


def save_meeting_transcript(payload, owner_id=""):
    filename, content = render_meeting_transcript(payload)
    if COS_BUCKET:
        return save_cos_transcript(filename, content, owner_id)
    return save_local_transcript(filename, content)


class AiohttpSocket:
    """Small compatibility wrapper used by the existing relay functions."""

    def __init__(self, socket):
        self.socket = socket

    async def send(self, message):
        if isinstance(message, bytes):
            await self.socket.send_bytes(message)
        else:
            await self.socket.send_str(message)

    async def close(self):
        await self.socket.close()

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.socket.receive()
        if message.type in (WSMsgType.TEXT, WSMsgType.BINARY):
            return message.data
        raise StopAsyncIteration


async def send_cloud(cloud, target_language, payload, metrics):
    try:
        await asyncio.wait_for(cloud.send(payload), timeout=CLOUD_SEND_TIMEOUT_SECONDS)
    except Exception as error:
        LOGGER.error(
            "session=%s event=cloud_send_failed target=%s error_type=%s",
            metrics.session_id,
            target_language,
            type(error).__name__,
        )
        raise


async def relay_browser_audio(browser, clouds, metrics):
    async for message in browser:
        if isinstance(message, bytes):
            metrics.record_audio(message)
            event = {
                "event_id": f"audio_{os.urandom(8).hex()}",
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(message).decode("ascii"),
            }
            payload = json.dumps(event)
            await asyncio.gather(*(
                send_cloud(cloud, target_language, payload, metrics)
                for target_language, cloud in clouds
            ))
            continue

        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "session.finish":
            payload = json.dumps({"event_id": f"finish_{os.urandom(8).hex()}", "type": "session.finish"})
            LOGGER.info("session=%s event=browser_finish_requested", metrics.session_id)
            await asyncio.gather(*(
                send_cloud(cloud, target_language, payload, metrics)
                for target_language, cloud in clouds
            ))
            return True
    return False


async def relay_cloud_events(cloud, browser, target_language, browser_send_lock, metrics):
    async for message in cloud:
        try:
            event = json.loads(message)
            metrics.record_cloud_event(target_language, event)
            if event.get("type") == "error":
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                LOGGER.error(
                    "session=%s event=cloud_error target=%s code=%s request_id=%s",
                    metrics.session_id,
                    target_language,
                    error.get("code") or event.get("code") or "unknown",
                    metrics.request_ids.get(target_language, "-"),
                )
            event["translation_target"] = target_language
            async with browser_send_lock:
                await browser.send(json.dumps(event))
            if event.get("type") == "session.finished":
                LOGGER.info(
                    "session=%s event=cloud_session_finished target=%s",
                    metrics.session_id,
                    target_language,
                )
                return
        except (json.JSONDecodeError, TypeError):
            async with browser_send_lock:
                await browser.send(message)


async def monitor_cloud_responsiveness(metrics):
    next_progress_at = time.monotonic() + 60
    while True:
        await asyncio.sleep(5)
        now = time.monotonic()
        if now >= next_progress_at:
            LOGGER.info(
                "session=%s event=session_progress last_cloud_event_age_s=%d %s",
                metrics.session_id,
                int(now - metrics.last_cloud_event_at),
                metrics.summary(),
            )
            next_progress_at = now + 60
        voice_is_recent = metrics.last_voice_at is not None and now - metrics.last_voice_at <= 6
        if not voice_is_recent:
            metrics.voice_wait_started_at = None
            continue
        if metrics.voice_wait_started_at is None:
            metrics.voice_wait_started_at = now
            continue
        waiting_seconds = now - metrics.voice_wait_started_at
        if waiting_seconds >= CLOUD_RESPONSE_TIMEOUT_SECONDS:
            LOGGER.error(
                "session=%s event=cloud_response_timeout waiting_s=%d last_event_age_s=%d",
                metrics.session_id,
                int(waiting_seconds),
                int(now - metrics.last_cloud_event_at),
            )
            raise TimeoutError("Bailian stopped responding while speech audio was still arriving")


def session_update(target_language, include_transcription):
    return {
        "event_id": f"session_{target_language}_{os.urandom(8).hex()}",
        "type": "session.update",
        "session": {
            "modalities": ["text"],
            "sample_rate": 16000,
            "input_audio_format": "pcm",
            "input_audio_transcription": {
                "model": "qwen3-asr-flash-realtime",
            } if include_transcription else None,
            "translation": {
                "language": target_language,
                "same_language_skip_options": {"skip_text": True, "skip_audio": True},
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.2,
                "silence_duration_ms": 700,
            },
        },
    }


async def handle_browser(browser):
    metrics = SessionMetrics()
    LOGGER.info("session=%s event=browser_connected", metrics.session_id)
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    api_host = os.environ.get("BAILIAN_API_HOST")
    if not api_key or not api_host:
        LOGGER.error("session=%s event=credentials_missing", metrics.session_id)
        await browser.send(json.dumps({
            "type": "error",
            "error": {"message": "Bailian credentials are not configured. Restart Chestnut and enter your API key and host."},
        }))
        await browser.close()
        return

    url = f"wss://{api_host}/api-ws/v1/realtime?model={MODEL}"
    try:
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            open_timeout=20,
            close_timeout=5,
            ping_interval=15,
            ping_timeout=15,
            max_size=None,
        ) as chinese_cloud:
            LOGGER.info("session=%s event=cloud_connected target=zh", metrics.session_id)
            async with connect(
                url,
                additional_headers={"Authorization": f"Bearer {api_key}"},
                open_timeout=20,
                close_timeout=5,
                ping_interval=15,
                ping_timeout=15,
                max_size=None,
            ) as english_cloud:
                LOGGER.info("session=%s event=cloud_connected target=en", metrics.session_id)
                browser_send_lock = asyncio.Lock()
                first_chinese, first_english = await asyncio.gather(
                    asyncio.wait_for(chinese_cloud.recv(), timeout=20),
                    asyncio.wait_for(english_cloud.recv(), timeout=20),
                )
                for message, target in ((first_chinese, "zh"), (first_english, "en")):
                    event = json.loads(message)
                    metrics.record_cloud_event(target, event)
                    event["translation_target"] = target
                    await browser.send(json.dumps(event))

                await asyncio.gather(
                    send_cloud(
                        chinese_cloud,
                        "zh",
                        json.dumps(session_update("zh", include_transcription=True)),
                        metrics,
                    ),
                    send_cloud(
                        english_cloud,
                        "en",
                        json.dumps(session_update("en", include_transcription=False)),
                        metrics,
                    ),
                )
                browser_to_cloud = asyncio.create_task(
                    relay_browser_audio(
                        browser,
                        (("zh", chinese_cloud), ("en", english_cloud)),
                        metrics,
                    ),
                    name=f"browser-to-cloud-{metrics.session_id}",
                )
                chinese_to_browser = asyncio.create_task(
                    relay_cloud_events(chinese_cloud, browser, "zh", browser_send_lock, metrics),
                    name=f"cloud-zh-{metrics.session_id}",
                )
                english_to_browser = asyncio.create_task(
                    relay_cloud_events(english_cloud, browser, "en", browser_send_lock, metrics),
                    name=f"cloud-en-{metrics.session_id}",
                )
                watchdog = asyncio.create_task(
                    monitor_cloud_responsiveness(metrics),
                    name=f"watchdog-{metrics.session_id}",
                )
                cloud_tasks = {chinese_to_browser, english_to_browser}
                all_tasks = {browser_to_cloud, *cloud_tasks, watchdog}
                done, pending = await asyncio.wait(
                    all_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                browser_finished_session = False
                if browser_to_cloud in done and not browser_to_cloud.cancelled():
                    browser_finished_session = browser_to_cloud.result() is True

                if browser_finished_session:
                    await asyncio.wait(cloud_tasks, timeout=5, return_when=asyncio.ALL_COMPLETED)

                for task in all_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*all_tasks, return_exceptions=True)
                for task in done:
                    if task is browser_to_cloud and browser_finished_session:
                        continue
                    if task in cloud_tasks and task.exception() is None:
                        LOGGER.warning(
                            "session=%s event=cloud_relay_ended target_task=%s",
                            metrics.session_id,
                            task.get_name(),
                        )
                        raise ConnectionError(f"{task.get_name()} ended before the browser session")
                    task.result()
    except InvalidStatus as error:
        status = getattr(error.response, "status_code", None)
        if status == 401:
            message = (
                "Bailian rejected this API Key (401). Create a new pay-as-you-go API Key "
                "in the same workspace as BAILIAN_API_HOST, update .env, and restart Chestnut."
            )
        elif status == 403:
            message = (
                "This Bailian workspace cannot use the live translation model (403). "
                "Check the API Key permissions and model access."
            )
        else:
            message = f"Bailian connection was rejected (HTTP {status or 'unknown'})."
        LOGGER.error(
            "session=%s event=cloud_connection_rejected status=%s %s",
            metrics.session_id,
            status or "unknown",
            metrics.summary(),
        )
        try:
            await browser.send(json.dumps({"type": "error", "error": {"message": message}}))
        except Exception:
            pass
    except Exception as error:
        message = str(error) or type(error).__name__
        LOGGER.exception(
            "session=%s event=realtime_session_failed error_type=%s %s",
            metrics.session_id,
            type(error).__name__,
            metrics.summary(),
        )
        try:
            await browser.send(json.dumps({"type": "error", "error": {"message": message}}))
        except Exception:
            pass
    finally:
        LOGGER.info("session=%s event=browser_session_closed %s", metrics.session_id, metrics.summary())


async def health_handler(_request):
    return web.json_response({"status": "ok", "service": "chestnut-api"})


async def websocket_handler(request):
    socket = web.WebSocketResponse(max_msg_size=0, heartbeat=10)
    await socket.prepare(request)
    await handle_browser(AiohttpSocket(socket))
    if not socket.closed:
        await socket.close()
    return socket


async def save_meeting_handler(request):
    try:
        if request.content_length is not None and request.content_length > MAX_TRANSCRIPT_BYTES:
            raise ValueError("Invalid transcript size")
        raw = await request.read()
        if not raw or len(raw) > MAX_TRANSCRIPT_BYTES:
            raise ValueError("Invalid transcript size")
        payload = json.loads(raw)
        owner_id = request.headers.get("x-wx-openid", "")
        saved = await asyncio.to_thread(save_meeting_transcript, payload, owner_id)
        LOGGER.info(
            "event=transcript_saved storage=%s entries=%d filename=%s",
            saved["storage"],
            len(payload.get("entries", [])),
            saved["filename"],
        )
        return web.json_response(saved, status=201)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except Exception as error:
        LOGGER.exception("event=transcript_save_failed error_type=%s", type(error).__name__)
        return web.json_response({"error": "会议稿云端保存失败，请稍后重试"}, status=503)


async def static_handler(request):
    relative_path = request.match_info.get("path") or "index.html"
    candidate = (ROOT / relative_path).resolve()
    public_files = {"index.html", "style.css", "app.js"}
    if candidate.parent != ROOT or candidate.name not in public_files:
        raise web.HTTPNotFound()
    response = web.FileResponse(candidate)
    response.headers["Cache-Control"] = "no-store"
    return response


async def meeting_file_handler(request):
    filename = Path(request.match_info["filename"]).name
    candidate = (ROOT / "meetings" / filename).resolve()
    if candidate.parent != (ROOT / "meetings").resolve() or not candidate.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(candidate, headers={"Cache-Control": "no-store"})


def create_app():
    app = web.Application(client_max_size=MAX_TRANSCRIPT_BYTES)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_post("/api/meetings", save_meeting_handler)
    app.router.add_get("/meetings/{filename}", meeting_file_handler)
    app.router.add_get("/{path:.*}", static_handler)
    return app


def main():
    display_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
    LOGGER.info("event=service_started url=http://%s:%d", display_host, PORT)
    if HOST == "0.0.0.0":
        LOGGER.info("event=network_access_enabled")
    LOGGER.info("event=realtime_endpoint_ready path=/ws")
    web.run_app(create_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
