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
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve


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


def start_http_server():
    mimetypes.add_type("application/javascript", ".js")
    server = ThreadingHTTPServer((HOST, HTTP_PORT), ChestnutHandler)
    server.serve_forever()


async def relay_browser_audio(browser, cloud):
    async for message in browser:
        if isinstance(message, bytes):
            event = {
                "event_id": f"audio_{os.urandom(8).hex()}",
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(message).decode("ascii"),
            }
            await cloud.send(json.dumps(event))
            continue

        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "session.finish":
            await cloud.send(json.dumps({"event_id": f"finish_{os.urandom(8).hex()}", "type": "session.finish"}))
            return


async def relay_cloud_events(cloud, browser):
    async for message in cloud:
        await browser.send(message)
        try:
            if json.loads(message).get("type") == "session.finished":
                return
        except (json.JSONDecodeError, TypeError):
            pass


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
        ) as cloud:
            first_event = await asyncio.wait_for(cloud.recv(), timeout=20)
            await browser.send(first_event)
            session_update = {
                "event_id": f"session_{os.urandom(8).hex()}",
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "sample_rate": 16000,
                    "input_audio_format": "pcm",
                    "input_audio_transcription": {
                        "model": "qwen3-asr-flash-realtime",
                        "language": "en",
                    },
                    "translation": {
                        "language": "zh",
                        "same_language_skip_options": {"skip_text": True, "skip_audio": True},
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.2,
                        "silence_duration_ms": 700,
                    },
                },
            }
            await cloud.send(json.dumps(session_update))
            browser_to_cloud = asyncio.create_task(relay_browser_audio(browser, cloud))
            cloud_to_browser = asyncio.create_task(relay_cloud_events(cloud, browser))
            done, pending = await asyncio.wait(
                {browser_to_cloud, cloud_to_browser},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
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
