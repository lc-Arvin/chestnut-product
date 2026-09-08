"""Trial journey with a disposable database, fake microphone and fake translation."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["CHESTNUT_LOG_FILE"] = str(ROOT / "test-results/trial/check.log")
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright, expect
import server
from admin_http import STORE_KEY

RESULTS = ROOT / "test-results/trial"


async def verify():
    RESULTS.mkdir(parents=True, exist_ok=True)
    saved = []
    async def fake_model(browser, **kwargs):
        await browser.send(json.dumps({"type": "session.updated", "translation_target": "zh"}))
        await browser.send(json.dumps({"type": "response.text.done", "translation_target": "en", "text": "Welcome. Let's begin our conversation."}))
        async for message in browser:
            if isinstance(message, str) and json.loads(message).get("type") == "session.finish":
                return
    def save(payload, *_args):
        saved.append(payload)
        return {"filename": "trial-test.md", "storage": "local"}
    with tempfile.TemporaryDirectory() as directory, patch.multiple(server, AUTH_REQUIRED=False, AUTH_SECRET="", WEB_INVITATIONS=(),
            MEETING_REGISTRY=server.MeetingRegistry(), LOGIN_LIMITER=server.SlidingWindowLimiter(100, 60)), \
            patch.object(server, "handle_browser", side_effect=fake_model), patch.object(server, "save_meeting_transcript", side_effect=save):
        http = TestServer(server.create_app(admin_path=Path(directory) / "admin.sqlite3"))
        await http.start_server()
        registry = http.app[server.TRIALS_KEY]
        offset = [0]
        registry.clock = lambda: time.time() + offset[0]
        code = http.app[STORE_KEY].create_codes({"label": "Trial upgrade test"})[0]["code"]
        try:
            async with async_playwright() as playwright:
                executable = next(path for path in ("C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe") if Path(path).is_file())
                browser = await playwright.chromium.launch(headless=True, executable_path=executable,
                    args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
                page = await browser.new_page(permissions=["microphone"], viewport={"width": 1280, "height": 900})
                errors, sockets = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("websocket", lambda socket: sockets.append(socket))
                await page.goto(str(http.make_url("/")))
                await expect(page.locator("#access-gate")).to_be_hidden()
                await page.locator("#start-button").click()
                await expect(page.locator("#trial-button")).to_have_text("Try free for 3 minutes →")
                await page.screenshot(path=str(RESULTS / "entry-desktop.png"), full_page=True)
                await page.set_viewport_size({"width": 375, "height": 812})
                await page.screenshot(path=str(RESULTS / "entry-mobile.png"), full_page=True)
                await page.locator("#trial-button").click()
                await expect(page.locator("#audio-screen")).to_have_class("screen is-active")
                trial = next(iter(registry.by_id.values()))
                assert trial.deadline is None  # Mic check consumes no translation time.
                await expect(page.locator("#continue-button")).to_be_enabled(timeout=15000)
                await page.locator("#continue-button").click()
                await expect(page.locator("#trial-badge")).to_contain_text("Trial · 03:00")
                assert trial.deadline is not None
                await page.set_viewport_size({"width": 1440, "height": 1000})
                await page.screenshot(path=str(RESULTS / "live-desktop.png"), full_page=True)
                await page.locator("#pause-button").click()
                await page.wait_for_timeout(2100)
                assert await page.locator("#trial-badge").text_content() != "Trial · 03:00"
                assert await page.evaluate("isPaused && microphoneStream.getAudioTracks().every(t => !t.enabled)")
                offset[0] += 181  # Advance server time; client is still paused with time left.
                await expect(page.locator("#access-title")).to_have_text("Your trial is complete", timeout=12000)
                await expect(page.locator("#trial-button")).to_be_disabled()
                assert await page.evaluate("!microphoneStream || !microphoneStream.active")
                assert len(saved) == 1 and saved[0]["meeting_id"] == trial.meeting_id
                assert saved[0]["entries"]
                await page.screenshot(path=str(RESULTS / "complete-desktop.png"), full_page=True)
                client_id = await page.evaluate("createClientId()")
                repeat = await page.request.post(str(http.make_url("/api/auth/trial")), data={"client_id": client_id})
                assert repeat.status == 409
                await page.reload()
                await page.locator("#start-button").click()
                await expect(page.locator("#trial-button")).to_be_disabled()
                await page.set_viewport_size({"width": 375, "height": 812})
                await page.screenshot(path=str(RESULTS / "complete-mobile.png"), full_page=True)
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert len(sockets) == 1  # No reconnect loop after trial expiry.
                await page.locator("#invite-code").fill(code)
                await page.locator("#access-button").click()
                await expect(page.locator("#audio-screen")).to_have_class("screen is-active")
                await expect(page.locator("#continue-button")).to_be_enabled(timeout=15000)
                await page.locator("#continue-button").click()
                await expect(page.locator("#connection-label")).to_contain_text("connected")
                await expect(page.locator("#trial-badge")).to_be_hidden()
                assert not errors, errors
                await browser.close()
                print(json.dumps({"passed": True, "checks": ["public landing", "trial without code", "no timer during mic check", "180-second server grant", "countdown while paused", "server expiry and audio stop", "transcript saved", "no automatic reconnect", "repeat and reload denied", "invitation upgrade", "desktop/mobile layout", "no JS errors"]}), flush=True)
        finally:
            await http.close()


asyncio.run(verify())
