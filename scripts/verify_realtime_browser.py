"""Verify terminal errors in Chrome; --bailian also checks real sessions without audio."""
import asyncio
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["CHESTNUT_LOG_FILE"] = str(ROOT / "test-results/realtime/check.log")
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright, expect
import server


async def verify():
    with patch.dict(os.environ, {"CHESTNUT_ADMIN_ENABLED": "0", "DASHSCOPE_API_KEY": "", "BAILIAN_API_HOST": ""}), patch.multiple(server, AUTH_REQUIRED=False, WEB_INVITATIONS=(), MEETING_REGISTRY=server.MeetingRegistry()):
        http = TestServer(server.create_app())
        await http.start_server()
        try:
            async with async_playwright() as playwright:
                executable = next(path for path in (
                    "C:/Program Files/Google/Chrome/Application/chrome.exe",
                    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                ) if Path(path).is_file())
                browser = await playwright.chromium.launch(headless=True, executable_path=executable,
                    args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
                page = await browser.new_page(permissions=["microphone"], viewport={"width": 1440, "height": 1000})
                errors, sockets = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("websocket", lambda socket: sockets.append(socket))
                await page.goto(str(http.make_url("/")))
                await page.locator("#start-button").click()
                await expect(page.locator("#continue-button")).to_be_enabled(timeout=15000)
                # Exercise the real UI and bridge, but never stream microphone frames.
                await page.evaluate("startPcmStreaming = () => {}")
                await page.locator("#continue-button").click()
                label = page.locator("#connection-label")
                await expect(label).to_contain_text("Bailian credentials are missing")
                await page.wait_for_timeout(11000)
                await expect(label).to_contain_text("Bailian credentials are missing")
                assert len(sockets) == 1
                assert await page.evaluate("isPaused && microphoneStream.getAudioTracks().every(t => !t.enabled)")
                print("Terminal error persists; microphone muted; no automatic reconnect.", flush=True)
                if "--bailian" in sys.argv:
                    server.load_bailian_credentials()
                    await page.evaluate("() => { window.readyTargets = []; const original = handleRealtimeEvent; handleRealtimeEvent = e => { if(e.type === 'session.updated') readyTargets.push(e.translation_target); original(e); }; }")
                    await page.locator("#retry-button").click()
                    await page.wait_for_function("new Set(window.readyTargets).size === 2", timeout=45000)
                    await expect(label).to_contain_text("connected")
                    assert await page.evaluate("!isPaused && microphoneStream.getAudioTracks().every(t => t.enabled)")
                    print("Manual recovery passed: both zh/en Bailian sessions updated through HTTP/WebSocket bridge; no audio sent.", flush=True)
                assert not errors, errors
                await browser.close()
        finally:
            await http.close()


asyncio.run(verify())
