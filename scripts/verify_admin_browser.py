"""Local end-to-end checks with a disposable database and a headless browser.

Install playwright in the development venv first. Uses installed Chrome/Edge;
does not call a paid model, collect real microphone audio, or change user data.
"""
import asyncio
import json
import os
import re
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright, expect
import server

RESULTS = ROOT / "test-results" / "admin"


async def verify():
    RESULTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory, patch.multiple(server, AUTH_REQUIRED=False, AUTH_SECRET="", WEB_INVITATIONS=(),
            LOGIN_LIMITER=server.SlidingWindowLimiter(1000, 60), MEETING_REGISTRY=server.MeetingRegistry()):
        http = TestServer(server.create_app(admin_path=Path(directory) / "admin.sqlite3"))
        await http.start_server()
        origin = str(http.make_url("/")).rstrip("/")
        try:
            async with async_playwright() as playwright:
                executable = next((path for path in (
                    os.environ.get("CHESTNUT_TEST_BROWSER", ""),
                    "C:/Program Files/Google/Chrome/Application/chrome.exe",
                    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                ) if path and Path(path).is_file()), None)
                browser = await playwright.chromium.launch(headless=True, executable_path=executable,
                    args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
                context = await browser.new_context(viewport={"width": 1440, "height": 1050}, permissions=["clipboard-read", "clipboard-write"])
                page = await context.new_page()
                errors, console_errors = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" and "Content Security Policy" in message.text else None)
                await page.goto(origin + "/admin")
                await expect(page.locator("#login-title")).to_have_text("设置管理工作台")
                await page.locator("#password").fill("Browser-test-password-2026")
                await page.locator("#confirm-password").fill("Browser-test-password-2026")
                await page.locator("#login-submit").click()
                await expect(page.locator("#codes-empty")).to_be_visible()
                await page.screenshot(path=str(RESULTS / "empty.png"), full_page=True)
                await page.locator("#new-code").click()
                await page.locator("#code-label").fill("秋季国际会议 · 嘉宾组")
                await page.locator('[name="quantity"]').fill("3")
                await page.locator('[name="max_uses"]').fill("10")
                await page.locator('[name="note"]').fill("本地验收数据 · 仅用于测试")
                for preset in ("1h", "4h", "12h", "24h", "7d", "forever"):
                    await page.locator(f'[data-expiry="{preset}"]').click()
                    await expect(page.locator(f'[data-expiry="{preset}"]')).to_have_attribute("aria-pressed", "true")
                await page.locator('[data-expiry="1h"]').click()
                await page.screenshot(path=str(RESULTS / "create-presets.png"), full_page=True)
                await page.locator("#create-submit").click()
                await expect(page.locator("#generated-dialog")).to_be_visible()
                await expect(page.locator(".generated-item")).to_have_count(3)
                codes = await page.locator(".generated-item code").all_text_contents()
                assert len(set(codes)) == 3
                assert all(re.fullmatch(r"[1-9][0-9]{5}", code) for code in codes)
                await page.locator("#copy-batch").click()
                copied = await page.evaluate("navigator.clipboard.readText()")
                assert all(code in copied for code in codes)
                await page.get_by_role("button", name="完成", exact=True).click()
                await expect(page.locator("#code-rows tr")).to_have_count(3)
                first = page.locator("#code-rows tr").first
                await first.get_by_role("button", name="查看", exact=True).click()
                await expect(first.locator(".code")).to_have_text(re.compile(r"[1-9][0-9]{5}"))
                target_code = await first.locator(".code").inner_text()
                await first.get_by_role("button", name="隐藏", exact=True).click()
                await expect(first.locator(".code")).to_contain_text("••")
                await first.get_by_role("button", name="复制", exact=True).click()
                assert await page.evaluate("navigator.clipboard.readText()") == target_code

                # A generated code goes through the existing public Web dialog.
                customer = await browser.new_context(permissions=["microphone"])
                public = await customer.new_page()
                public.on("pageerror", lambda error: errors.append(str(error)))
                await public.goto(origin + "/")
                await public.locator("#start-button").click()
                await expect(public.locator("#access-gate")).to_be_visible()
                await expect(public.locator("#invitation-validity")).to_be_hidden()
                await public.locator("#access-cancel").focus()
                await public.screenshot(path=str(RESULTS / "invitation-dialog-desktop.png"), full_page=True)
                await public.set_viewport_size({"width": 375, "height": 812})
                await public.screenshot(path=str(RESULTS / "invitation-dialog-mobile.png"), full_page=True)
                await public.locator("#access-cancel").click()
                await expect(public.locator("#access-gate")).to_be_hidden()
                await public.locator("#start-button").click()
                await public.locator("#invite-code").fill(target_code)
                await public.locator("#access-button").click()
                await expect(public.locator("#audio-screen")).to_have_class("screen is-active")
                await public.reload()  # Returning customer: real cookie restores the expiry hint.
                await expect(public.locator("#invitation-validity")).to_contain_text("UTC+8")
                await public.screenshot(path=str(RESULTS / "setup-expiry-mobile.png"), full_page=True)
                assert await public.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await public.set_viewport_size({"width": 1440, "height": 1050})
                await public.screenshot(path=str(RESULTS / "setup-expiry-desktop.png"), full_page=True)
                await public.close()  # Fake microphone only; do not start a model session.

                for index in range(4):
                    response = await customer.request.post(origin + "/api/auth/invite", data={"code": target_code, "client_id": f"test-browser-{index}", "client_type": "miniprogram"})
                    assert response.status == 200
                for index in range(5):
                    response = await customer.request.post(origin + "/api/auth/invite", data={"code": "invalid-test-only", "client_id": f"failed-browser-{index}"})
                    assert response.status == 401
                # Real HTTP visits demonstrate PV vs distinct clients, no fake dashboard totals.
                for index in (0, 0, 1, 2):
                    await customer.request.post(origin + "/api/visits", data={"client_id": f"visitor-test-{index}", "channel": "web"})
                await page.locator("#refresh-all").click()
                await expect(page.locator("#summary-successes")).to_have_text("5")
                await expect(page.locator("#summary-alerts")).to_have_text("2")
                await page.screenshot(path=str(RESULTS / "codes.png"), full_page=True)

                # Disable, reject the already-issued cookie, then restore and re-authenticate.
                first = page.locator("#code-rows tr").first
                await first.get_by_role("button", name="停用", exact=True).click()
                await page.locator("#confirm-submit").click()
                await expect(first.locator(".badge")).to_have_text("已停用")
                status = await (await customer.request.get(origin + "/api/auth/status")).json()
                assert not status["authenticated"]
                rejected = await customer.request.post(origin + "/api/auth/invite", data={"code": target_code, "client_id": "disabled-test-client"})
                assert rejected.status == 401
                await first.get_by_role("button", name="恢复", exact=True).click()
                await page.locator("#confirm-submit").click()
                await expect(first.locator(".badge")).to_have_text("可使用")
                accepted = await customer.request.post(origin + "/api/auth/invite", data={"code": target_code, "client_id": "restored-test-client"})
                assert accepted.status == 200
                await first.get_by_role("button", name="记录", exact=True).click()
                await expect(page.locator("#events-panel")).to_be_visible()
                await expect(page.locator("#event-scope")).to_contain_text("嘉宾组")
                await page.screenshot(path=str(RESULTS / "events.png"), full_page=True)

                await page.locator('[data-tab="overview"]').click()
                await expect(page.locator("#metrics .metric")).to_have_count(4)
                await expect(page.locator("#usage-rows tr")).to_have_count(1)
                await page.locator("#today").click()
                await expect(page.locator("#daily-table tbody tr")).to_have_count(1)
                await page.locator("#week").click()
                await expect(page.locator("#daily-table tbody tr")).to_have_count(7)
                await page.screenshot(path=str(RESULTS / "overview.png"), full_page=True)
                await page.locator('[data-tab="alerts"]').click()
                await expect(page.locator(".alert-item")).to_have_count(2)
                await page.screenshot(path=str(RESULTS / "alerts.png"), full_page=True)
                await page.locator(".alert-item").first.get_by_role("button", name="标记已核实").click()
                await expect(page.locator(".alert-item")).to_have_count(1)
                await page.locator('[data-state="acknowledged"]').click()
                await expect(page.locator(".alert-item")).to_have_count(1)

                await page.locator('[data-tab="codes"]').click()
                await page.locator("#search").fill("不存在的标签")
                await page.locator("#search-form").get_by_role("button").click()
                await expect(page.locator("#codes-no-match")).to_be_visible()
                await page.locator("#search").fill("嘉宾组")
                await page.locator("#search-form").get_by_role("button").click()
                await expect(page.locator("#code-rows tr")).to_have_count(3)
                await expect(page.locator("#code-rows tr.expiry-urgent")).to_have_count(3)
                # Exercise sorting through the API/UI, mixing finite and long-term codes.
                for label, preset in (("长效组", "7d"), ("长期组", "forever"), ("当天组", "4h")):
                    await page.locator("#new-code").click()
                    await page.locator("#code-label").fill(label)
                    await page.locator(f'[data-expiry="{preset}"]').click()
                    await page.locator("#create-submit").click()
                    await expect(page.locator("#generated-dialog")).to_be_visible()
                    await page.get_by_role("button", name="完成", exact=True).click()
                await page.locator("#search").fill("")
                await page.locator("#search-form").get_by_role("button").click()
                await expect(page.locator("#code-rows tr")).to_have_count(6)
                for sort in ("created_asc", "created_desc", "expires_asc", "expires_desc"):
                    async with page.expect_response(lambda response: "/api/admin/codes?" in response.url and "sort=" + sort in response.url):
                        await page.locator("#code-sort").select_option(sort)
                    await page.wait_for_function("sort => document.querySelector('#code-sort').value === sort", arg=sort)
                    # Wait for rows to be rendered from that response.
                    field = "createdAt" if sort.startswith("created") else "expiresAt"
                    await expect(page.locator("#code-rows tr")).to_have_count(6)
                    if sort.startswith("expires"):
                        await expect(page.locator("#code-rows tr").last.locator("td").first).to_contain_text("长期组")
                    values = await page.locator("#code-rows tr").evaluate_all("(rows, field) => rows.map(row => Number(row.dataset[field]) || null)", field)
                    nonnull = [value for value in values if value is not None]
                    assert nonnull == sorted(nonnull, reverse=sort.endswith("desc")), (sort, values)
                await page.screenshot(path=str(RESULTS / "codes-sorted.png"), full_page=True)
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.screenshot(path=str(RESULTS / "mobile.png"), full_page=True)
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.locator("#new-code").click()
                await expect(page.locator("#create-dialog")).to_be_visible()
                await page.screenshot(path=str(RESULTS / "mobile-create.png"), full_page=True)
                await page.get_by_role("button", name="取消", exact=True).click()
                await page.locator("#logout").click()
                await expect(page.locator("#login-screen")).to_be_visible()
                await page.locator("#password").fill("Browser-test-password-2026")
                await page.locator("#login-submit").click()
                await expect(page.locator("#workspace")).to_be_visible()
                await page.reload()
                await expect(page.locator("#workspace")).to_be_visible()
                assert not errors, errors
                assert not console_errors, console_errors
                await context.close()
                await customer.close()
                await browser.close()
                report = {"passed": True, "checks": ["first-run setup", "empty state", "six-digit unique codes", "expiry presets", "sorting and expiry highlights", "reveal and clipboard", "public Web invitation flow", "real HTTP analytics", "disable and restore", "activity attribution", "date filtering", "alert evidence and acknowledge", "search", "mobile overflow and dialog", "logout/login/session restore", "no JavaScript or CSP errors"], "screenshots": [p.name for p in RESULTS.glob("*.png")]}
                (RESULTS / "browser-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report), flush=True)
        finally:
            await http.close()


if __name__ == "__main__":
    asyncio.run(verify())
