"""Unified HTTP and WebSocket bridge for Chestnut Conference Console.

The browser sends 16 kHz PCM to this process. This process authenticates with
Alibaba Cloud Model Studio, so the DashScope API key never enters browser code.
The same single-port service runs locally and in WeChat CloudBase Run.
"""

import asyncio
import base64
import json
import os
from datetime import datetime
from pathlib import Path

from aiohttp import WSMsgType, web
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


HOST = os.environ.get("CHESTNUT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("CHESTNUT_PORT", "8080")))
ROOT = Path(__file__).resolve().parent
MODEL = "qwen3.5-livetranslate-flash-realtime"
MAX_TRANSCRIPT_BYTES = 5_000_000


def transcript_time(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def markdown_quote(text):
    return "\n".join(f"> {line}" if line else ">" for line in str(text).splitlines())


def save_meeting_transcript(payload):
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")

    now = datetime.now().astimezone()
    meetings_dir = ROOT / "meetings"
    meetings_dir.mkdir(exist_ok=True)
    filename = f"meeting-{now.strftime('%Y%m%d-%H%M%S')}.md"
    output_path = meetings_dir / filename
    suffix = 2
    while output_path.exists():
        filename = f"meeting-{now.strftime('%Y%m%d-%H%M%S')}-{suffix}.md"
        output_path = meetings_dir / filename
        suffix += 1

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

    temporary_path = output_path.with_suffix(".tmp")
    temporary_path.write_text("\n".join(lines), encoding="utf-8")
    temporary_path.replace(output_path)
    return filename


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


async def relay_browser_audio(browser, clouds):
    async for message in browser:
        if isinstance(message, bytes):
            event = {
                "event_id": f"audio_{os.urandom(8).hex()}",
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(message).decode("ascii"),
            }
            payload = json.dumps(event)
            await asyncio.gather(*(cloud.send(payload) for cloud in clouds))
            continue

        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "session.finish":
            payload = json.dumps({"event_id": f"finish_{os.urandom(8).hex()}", "type": "session.finish"})
            await asyncio.gather(*(cloud.send(payload) for cloud in clouds))
            return True
    return False


async def relay_cloud_events(cloud, browser, target_language, browser_send_lock):
    async for message in cloud:
        try:
            event = json.loads(message)
            event["translation_target"] = target_language
            async with browser_send_lock:
                await browser.send(json.dumps(event))
            if event.get("type") == "session.finished":
                return
        except (json.JSONDecodeError, TypeError):
            async with browser_send_lock:
                await browser.send(message)


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
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    api_host = os.environ.get("BAILIAN_API_HOST")
    if not api_key or not api_host:
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
            max_size=None,
        ) as chinese_cloud:
            async with connect(
                url,
                additional_headers={"Authorization": f"Bearer {api_key}"},
                open_timeout=20,
                close_timeout=5,
                max_size=None,
            ) as english_cloud:
                browser_send_lock = asyncio.Lock()
                first_chinese, first_english = await asyncio.gather(
                    asyncio.wait_for(chinese_cloud.recv(), timeout=20),
                    asyncio.wait_for(english_cloud.recv(), timeout=20),
                )
                for message, target in ((first_chinese, "zh"), (first_english, "en")):
                    event = json.loads(message)
                    event["translation_target"] = target
                    await browser.send(json.dumps(event))

                await asyncio.gather(
                    chinese_cloud.send(json.dumps(session_update("zh", include_transcription=True))),
                    english_cloud.send(json.dumps(session_update("en", include_transcription=False))),
                )
                browser_to_cloud = asyncio.create_task(
                    relay_browser_audio(browser, (chinese_cloud, english_cloud))
                )
                chinese_to_browser = asyncio.create_task(
                    relay_cloud_events(chinese_cloud, browser, "zh", browser_send_lock)
                )
                english_to_browser = asyncio.create_task(
                    relay_cloud_events(english_cloud, browser, "en", browser_send_lock)
                )
                cloud_tasks = {chinese_to_browser, english_to_browser}
                all_tasks = {browser_to_cloud, *cloud_tasks}
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
                    if task is browser_to_cloud:
                        continue
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
        try:
            await browser.send(json.dumps({"type": "error", "error": {"message": message}}))
        except Exception:
            pass
    except Exception as error:
        message = str(error) or type(error).__name__
        try:
            await browser.send(json.dumps({"type": "error", "error": {"message": message}}))
        except Exception:
            pass


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
        filename = await asyncio.to_thread(save_meeting_transcript, payload)
        return web.json_response({
            "filename": filename,
            "url": f"/meetings/{filename}",
            "storage": "container-local",
        }, status=201)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return web.json_response({"error": str(error)}, status=400)


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
    print(f"Chestnut is ready at http://{display_host}:{PORT}")
    if HOST == "0.0.0.0":
        print("Cloud/LAN access is enabled.")
    print("HTTP and WebSocket share one port; realtime endpoint: /ws")
    web.run_app(create_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
