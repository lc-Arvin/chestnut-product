"""Local HTTP and WebSocket bridge for Chestnut Conference Console.

The browser sends 16 kHz PCM to this process. This process authenticates with
Alibaba Cloud Model Studio, so the DashScope API key never enters browser code.
"""

import asyncio
import base64
import json
import mimetypes
import os
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus


HOST = "127.0.0.1"
HTTP_PORT = int(os.environ.get("CHESTNUT_PORT", "8080"))
WS_PORT = int(os.environ.get("CHESTNUT_WS_PORT", "8765"))
ROOT = Path(__file__).resolve().parent
MODEL = "qwen3.5-livetranslate-flash-realtime"


class ChestnutHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def is_private_path(self):
        path = urlsplit(self.path).path
        private_prefixes = ("/.env", "/.git", "/.venv", "/__pycache__", "/sources/")
        return path == "/AGENTS.md" or path.startswith(private_prefixes)

    def do_GET(self):
        if self.is_private_path():
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        if self.is_private_path():
            self.send_error(404)
            return
        super().do_HEAD()

    def do_POST(self):
        if self.path != "/api/meetings":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 5_000_000:
                raise ValueError("Invalid transcript size")
            payload = json.loads(self.rfile.read(length))
            filename = save_meeting_transcript(payload)
            response = json.dumps({
                "filename": filename,
                "url": f"/meetings/{quote(filename)}",
            }).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            response = json.dumps({"error": str(error)}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)


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


def start_http_server():
    mimetypes.add_type("application/javascript", ".js")
    server = ThreadingHTTPServer((HOST, HTTP_PORT), ChestnutHandler)
    server.serve_forever()


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
            return


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
                done, pending = await asyncio.wait(
                    {browser_to_cloud, chinese_to_browser, english_to_browser},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
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


async def main():
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    print(f"Chestnut is ready at http://{HOST}:{HTTP_PORT}")
    print("Bailian live translation bridge is ready.")
    print("Press Control-C to stop.")
    async with serve(handle_browser, HOST, WS_PORT, max_size=None):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
