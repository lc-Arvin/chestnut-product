"""Exercise the admin through HTTP and the real meeting authorization path.

All storage is temporary; cloud translation and COS writes are mocked.
"""
import asyncio
import json
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

import server
from admin_http import STORE_KEY, local_admin_request
from admin_store import AdminStore

PASSWORD = "local-test-password-2026"


class AdminIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "admin.sqlite3"
        self.config = patch.multiple(server, AUTH_REQUIRED=False, AUTH_SECRET="", WEB_INVITATIONS=(),
                                    LOGIN_LIMITER=server.SlidingWindowLimiter(1000, 60),
                                    CONNECTION_LIMITER=server.SlidingWindowLimiter(1000, 60),
                                    MEETING_REGISTRY=server.MeetingRegistry())
        self.config.start()
        self.client = TestClient(TestServer(server.create_app(admin_path=self.path)), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")
        self.headers = {"Origin": self.origin}

    async def asyncTearDown(self):
        await self.client.close()
        self.config.stop()
        self.directory.cleanup()

    async def setup_admin(self):
        result = await self.client.post("/api/admin/setup", json={"password": PASSWORD}, headers=self.headers)
        self.assertEqual(result.status, 200, await result.text())
        self.headers["X-CSRF-Token"] = (await result.json())["csrf"]

    async def create_code(self, **fields):
        response = await self.client.post("/api/admin/codes", json={"label": "Test customer", **fields}, headers=self.headers)
        self.assertEqual(response.status, 201, await response.text())
        return (await response.json())["items"][0]

    async def redeem(self, code, client="browser-test-one", openid=""):
        headers = {"x-wx-openid": openid} if openid else {}
        response = await self.client.post("/api/auth/invite", json={"code": code, "client_id": client, "client_type": "miniprogram"}, headers=headers)
        return response, await response.json()

    async def test_first_run_requires_invite_and_admin_is_separate(self):
        self.assertEqual((await self.client.get("/admin")).status, 200)
        self.assertEqual((await self.client.get("/api/admin/codes")).status, 401)
        status = await (await self.client.get("/api/auth/status")).json()
        self.assertTrue(status["auth_required"])
        self.assertFalse(status["authenticated"])
        self.assertEqual((await self.client.post("/api/meetings", json={"entries": []})).status, 401)
        await self.setup_admin()
        self.assertFalse((await (await self.client.get("/api/auth/status")).json())["authenticated"])
        code = await self.create_code()
        _, auth = await self.redeem(code["code"])
        self.client.session.cookie_jar.clear()
        denied = await self.client.get("/api/admin/codes", headers={"Authorization": "Bearer " + auth["access_token"]})
        self.assertEqual(denied.status, 401)

    async def test_origin_csrf_local_boundary_and_static_allowlist(self):
        self.assertEqual((await self.client.post("/api/admin/setup", json={"password": PASSWORD})).status, 403)
        self.assertEqual((await self.client.post("/api/admin/setup", json={"password": PASSWORD}, headers={"Origin": "https://elsewhere.example"})).status, 403)
        await self.setup_admin()
        self.assertEqual((await self.client.post("/api/admin/codes", json={"label": "Blocked"}, headers={"Origin": self.origin})).status, 403)
        for header in ({"Host": "attacker.example"}, {"X-Forwarded-For": "127.0.0.1"}, {"X-WX-OpenID": "someone"}, {"Forwarded": "for=127.0.0.1"}):
            self.assertEqual((await self.client.get("/api/admin/codes", headers=header)).status, 404)
        for path in ("/data/admin.sqlite3", "/admin/admin.sqlite3", "/admin_store.py", "/.env"):
            self.assertEqual((await self.client.get(path)).status, 404)
        asset = await self.client.get("/admin/admin.js")
        self.assertEqual(asset.status, 200)
        self.assertEqual(asset.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", asset.headers["Content-Security-Policy"])

    async def test_batch_generation_search_pagination_reveal_and_validation(self):
        await self.setup_admin()
        first = await self.create_code(quantity=27, label="嘉宾组", note="秋季会议", max_uses=3)
        listing = await (await self.client.get("/api/admin/codes?q=秋季会议")).json()
        self.assertEqual(listing["total"], 27)
        self.assertEqual(len(listing["items"]), 25)
        self.assertNotIn("code", listing["items"][0])
        self.assertEqual(len((await (await self.client.get("/api/admin/codes?page=2")).json())["items"]), 2)
        revealed = await self.client.post(f"/api/admin/codes/{first['id']}/reveal", json={}, headers=self.headers)
        self.assertEqual((await revealed.json())["code"], first["code"])
        for payload in ({"label": ""}, {"label": "x", "quantity": 101}, {"label": "x", "quantity": 1.5},
                        {"label": "x", "max_uses": -1}, {"label": "x", "expires_at": "2020-01-01T00:00:00Z"}):
            self.assertEqual((await self.client.post("/api/admin/codes", json=payload, headers=self.headers)).status, 400)
        self.assertEqual((await self.client.get("/api/admin/codes?page=oops")).status, 400)

    async def test_quota_is_atomic_and_issued_token_keeps_working(self):
        await self.setup_admin()
        code = await self.create_code(max_uses=1)
        responses = await asyncio.gather(self.redeem(code["code"], "browser-test-one"), self.redeem(code["code"], "browser-test-two"))
        self.assertEqual(sorted(response.status for response, _ in responses), [200, 401])
        token = next(result["access_token"] for response, result in responses if response.status == 200)
        header = {"Authorization": "Bearer " + token}
        self.assertTrue((await (await self.client.get("/api/auth/status", headers=header)).json())["authenticated"])
        with patch.object(server, "save_meeting_transcript", return_value={"storage": "local", "filename": "test.md"}):
            self.assertEqual((await self.client.post("/api/meetings", headers=header, json={"entries": []})).status, 201)
        listing = await (await self.client.get("/api/admin/codes?status=exhausted")).json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["use_count"], 1)

    async def test_customer_sees_only_own_invitation_expiry(self):
        await self.setup_admin()
        self.assertNotIn("invitation_expires_at", await (await self.client.get("/api/auth/status")).json())
        for preset in ("1h", "forever"):
            code = await self.create_code(expiry_preset=preset)
            listing = await (await self.client.get("/api/admin/codes")).json()
            expiry = next(item["expires_at"] for item in listing["items"] if item["id"] == code["id"])
            _, auth = await self.redeem(code["code"])
            self.assertEqual(auth["invitation_expires_at"], expiry)
            headers = {"Authorization": "Bearer " + auth["access_token"]}
            status = await (await self.client.get("/api/auth/status", headers=headers)).json()
            self.assertEqual(status["invitation_expires_at"], expiry)
            self.assertEqual(set(status), {"auth_required", "authenticated", "max_meeting_seconds", "meeting_warning_seconds", "invitation_expires_at"})
            await self.client.patch(f"/api/admin/codes/{code['id']}", json={"enabled": False}, headers=self.headers)
            status = await (await self.client.get("/api/auth/status", headers=headers)).json()
            self.assertFalse(status["authenticated"])
            self.assertNotIn("invitation_expires_at", status)

    async def test_disabled_expired_and_reenabled_codes_revoke_old_tokens(self):
        await self.setup_admin()
        code = await self.create_code()
        _, result = await self.redeem(code["code"])
        token = result["access_token"]
        for enabled in (False, True):
            response = await self.client.patch(f"/api/admin/codes/{code['id']}", json={"enabled": enabled}, headers=self.headers)
            self.assertEqual(response.status, 200)
            self.assertFalse((await (await self.client.get("/api/auth/status", headers={"Authorization": "Bearer " + token})).json())["authenticated"])
        _, new = await self.redeem(code["code"])
        self.assertTrue((await (await self.client.get("/api/auth/status", headers={"Authorization": "Bearer " + new["access_token"]})).json())["authenticated"])
        store = self.client.server.app[STORE_KEY]
        with store.db:
            store.db.execute("UPDATE codes SET expires_at=? WHERE id=?", (time.time()-1, code["id"]))
        self.assertEqual((await self.redeem(code["code"]))[0].status, 401)
        self.assertFalse((await (await self.client.get("/api/auth/status", headers={"Authorization": "Bearer " + new["access_token"]})).json())["authenticated"])

    async def test_wechat_binding_and_live_revocation(self):
        await self.setup_admin()
        code = await self.create_code()
        _, result = await self.redeem(code["code"], openid="wechat-user-one")
        token = result["access_token"]
        for openid, expected in (("wechat-user-one", True), ("someone-else", False), ("", False)):
            response = await self.client.get("/api/auth/status", headers={"x-wx-openid": openid, "Authorization": "Bearer " + token})
            self.assertEqual((await response.json())["authenticated"], expected)
        started = asyncio.Event()

        async def fake_model(browser, **kwargs):
            await browser.send(json.dumps({"type": "session.updated"}))
            started.set()
            async for _ in browser:
                pass

        with patch.object(server, "handle_browser", side_effect=fake_model):
            socket = await self.client.ws_connect("/ws?auth=message&meeting_id=managed-meeting&languages=yue,zh", headers={"x-wx-openid": "wechat-user-one"})
            await socket.send_json({"type": "auth.authenticate", "token": token})
            self.assertEqual((await socket.receive_json())["type"], "session.updated")
            await started.wait()
            disable = asyncio.create_task(self.client.patch(f"/api/admin/codes/{code['id']}", json={"enabled": False}, headers=self.headers))
            self.assertEqual((await socket.receive_json(timeout=5))["type"], "access.denied")
            await socket.close()
            self.assertEqual((await disable).status, 200)
        events = await (await self.client.get("/api/admin/events?kind=meeting_started")).json()
        self.assertEqual(events["items"][0]["code_id"], code["id"])

    async def test_traffic_dedup_code_attribution_alerts_and_acknowledge(self):
        await self.setup_admin()
        code = await self.create_code()
        for client in ("browser-visitor-one", "browser-visitor-one", "browser-visitor-two"):
            response = await self.client.post("/api/visits", json={"client_id": client, "channel": "web"})
            self.assertTrue((await response.json())["recorded"])
        for i in range(5):
            self.assertEqual((await self.redeem(code["code"], f"browser-visitor-{i}"))[0].status, 200)
        for i in range(5):
            self.assertEqual((await self.redeem("DO-NOT-STORE-THIS-SECRET", f"browser-attacker-{i}"))[0].status, 401)
        overview = await (await self.client.get("/api/admin/overview")).json()
        self.assertEqual((overview["visits"], overview["visitors"], overview["successes"], overview["failures"]), (3, 2, 5, 5))
        self.assertEqual(overview["usage"][0]["clients"], 5)
        self.assertEqual(overview["usage"][0]["code_id"], code["id"])
        self.assertEqual(len(overview["daily"]), 7)
        alerts = await (await self.client.get("/api/admin/alerts")).json()
        self.assertEqual({item["kind"] for item in alerts["items"]}, {"shared_code", "repeated_failures"})
        alert_id = alerts["items"][0]["id"]
        self.assertEqual((await self.client.post(f"/api/admin/alerts/{alert_id}/acknowledge", json={}, headers=self.headers)).status, 200)
        self.assertEqual((await (await self.client.get("/api/admin/alerts?state=acknowledged")).json())["total"], 1)
        events = await (await self.client.get("/api/admin/events")).text()
        self.assertNotIn("DO-NOT-STORE-THIS-SECRET", events)
        self.assertNotIn("127.0.0.1", events)
        self.assertNotIn(code["code"], events)
        self.assertNotIn("browser-visitor", events)

    async def test_restart_retains_codes_settings_and_tokens(self):
        await self.setup_admin()
        code = await self.create_code()
        _, auth = await self.redeem(code["code"])
        await self.client.close()
        self.client = TestClient(TestServer(server.create_app(admin_path=self.path)), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        origin = str(self.client.make_url("/")).rstrip("/")
        self.assertFalse((await (await self.client.get("/api/admin/session")).json())["setup_required"])
        response = await self.client.post("/api/admin/login", json={"password": PASSWORD}, headers={"Origin": origin})
        self.assertEqual(response.status, 200)
        self.assertEqual((await (await self.client.get("/api/admin/codes")).json())["total"], 1)
        self.assertTrue((await (await self.client.get("/api/auth/status", headers={"Authorization": "Bearer " + auth["access_token"]})).json())["authenticated"])

    async def test_admin_password_login_logout_and_bruteforce(self):
        await self.setup_admin()
        self.assertNotIn(PASSWORD, self.client.server.app[STORE_KEY].setting("password"))
        self.assertEqual((await self.client.post("/api/admin/setup", json={"password": PASSWORD}, headers=self.headers)).status, 400)
        await self.client.post("/api/admin/logout", json={}, headers=self.headers)
        self.assertEqual((await self.client.get("/api/admin/codes")).status, 401)
        for _ in range(5):
            self.assertEqual((await self.client.post("/api/admin/login", json={"password": "wrong-password"}, headers=self.headers)).status, 401)
        self.assertEqual((await self.client.post("/api/admin/login", json={"password": PASSWORD}, headers=self.headers)).status, 429)


class AdminStoreTests(unittest.TestCase):
    def test_imported_codes_keep_disabled_state_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "store.sqlite3"
            store = AdminStore(path, [("legacy", "old-code")])
            code = store.list_codes()["items"][0]
            store.update_code(code["id"], {"enabled": False})
            store.close()
            store = AdminStore(path, [("legacy", "old-code")])
            self.assertEqual(store.list_codes()["items"][0]["status"], "disabled")
            self.assertIsNone(store.redeem("old-code", "someone", "network", "web"))
            store.close()

    def test_beijing_day_boundary_and_reconnect_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AdminStore(Path(directory) / "store.sqlite3")
            china = timezone(timedelta(hours=8))
            day = datetime.now(china).date() - timedelta(days=1)
            midnight = datetime.combine(day, datetime.min.time(), china).timestamp()
            with patch("admin_store.time.time", return_value=midnight-1):
                store.record("visit", client="one", channel="web")
            with patch("admin_store.time.time", return_value=midnight):
                store.record("visit", client="two", channel="web")
                store.record("meeting_started", client="two", meeting="one-meeting", channel="web")
                store.record("meeting_started", client="two", meeting="one-meeting", channel="web")
            result = store.overview(day.isoformat(), day.isoformat())
            self.assertEqual((result["visits"], result["visitors"], result["meetings"]), (1, 1, 1))
            self.assertEqual(result["daily"][0]["meetings"], 1)
            store.close()

    def test_expired_unavailable_code_alert_and_ack_reopens(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AdminStore(Path(directory) / "store.sqlite3")
            code = store.create_codes({"label": "expired"})[0]
            store.update_code(code["id"], {"enabled": False})
            for _ in range(3):
                store.redeem(code["code"], "client", "ip", "web")
            alert = store.alerts()["items"][0]
            self.assertEqual(alert["kind"], "unavailable_code")
            store.acknowledge(alert["id"])
            store.redeem(code["code"], "client", "ip", "web")
            self.assertEqual(store.alerts()["items"][0]["id"], alert["id"])
            store.close()

    def test_non_loopback_peer_cannot_reach_admin(self):
        from unittest.mock import Mock
        transport = Mock()
        transport.get_extra_info.return_value = ("192.168.1.20", 3000)
        request = make_mocked_request("GET", "/admin", headers={"Host": "127.0.0.1:8080"}, transport=transport)
        self.assertFalse(local_admin_request(request))


class AdminOptimizationsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "admin.sqlite3"
        self.store = AdminStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_six_digits_unique_collision_retry_and_old_code_compatibility(self):
        import re
        with patch("admin_store.secrets.randbelow", side_effect=[0, 0, 1]):
            codes = self.store.create_codes({"label": "simple", "quantity": 2})
        self.assertEqual([c["code"] for c in codes], ["100000", "100001"])
        self.store.update_code(codes[0]["id"], {"enabled": False})
        with patch("admin_store.secrets.randbelow", side_effect=[0, 1, 2]):
            self.assertEqual(self.store.create_codes({"label": "no reuse"})[0]["code"], "100002")
        batch = self.store.create_codes({"label": "batch", "quantity": 100})
        self.assertEqual(len({c["code"] for c in batch}), 100)
        self.assertTrue(all(re.fullmatch(r"[1-9][0-9]{5}", c["code"]) for c in batch))
        self.store.close()
        self.store = AdminStore(self.path, [("legacy", "CN-OLD-LONG-CODE-STILL-VALID")])
        self.assertIsNotNone(self.store.redeem("CN-OLD-LONG-CODE-STILL-VALID", "client", "ip", "web"))

    def test_presets_calculate_from_generation_and_custom_remains_available(self):
        now = time.time()
        for preset, seconds in {"1h": 3600, "4h": 14400, "12h": 43200, "24h": 86400, "7d": 604800, "forever": 0}.items():
            with patch("admin_store.time.time", return_value=now):
                code = self.store.create_codes({"label": preset, "expiry_preset": preset, "expires_at": "2020-01-01T00:00:00Z"})[0]
            actual = next(row for row in self.store.list_codes()["items"] if row["id"] == code["id"])
            self.assertEqual(actual["expires_at"], now+seconds if seconds else None)
        end = datetime.now(timezone.utc) + timedelta(days=3)
        code = self.store.create_codes({"label": "custom", "expiry_preset": "custom", "expires_at": end.isoformat()})[0]
        self.assertAlmostEqual(next(row for row in self.store.list_codes()["items"] if row["id"] == code["id"])["expires_at"], end.timestamp())
        for preset in ("not-supported", [], 3):
            with self.assertRaises(ValueError):
                self.store.create_codes({"label": "bad", "expiry_preset": preset})

    def test_sorting_expiry_highlights_and_long_term_last(self):
        now = time.time()
        for index, preset in enumerate(("forever", "7d", "4h", "1h")):
            with patch("admin_store.time.time", return_value=now+index):
                self.store.create_codes({"label": preset, "expiry_preset": preset})
        self.assertEqual([c["label"] for c in self.store.list_codes(sort="created_asc")["items"]], ["forever", "7d", "4h", "1h"])
        self.assertEqual([c["label"] for c in self.store.list_codes(sort="created_desc")["items"]], ["1h", "4h", "7d", "forever"])
        self.assertEqual([c["label"] for c in self.store.list_codes(sort="expires_asc")["items"]], ["1h", "4h", "7d", "forever"])
        self.assertEqual([c["label"] for c in self.store.list_codes(sort="expires_desc")["items"]], ["7d", "4h", "1h", "forever"])
        with patch("admin_store.time.time", return_value=now+10):
            codes = {c["label"]: c for c in self.store.list_codes()["items"]}
        self.assertEqual(codes["1h"]["expiry_warning"], "urgent")
        self.assertEqual(codes["4h"]["expiry_warning"], "soon")
        self.assertEqual(codes["7d"]["expiry_warning"], "")
        self.assertEqual(codes["forever"]["expiry_warning"], "")
        self.store.update_code(codes["4h"]["id"], {"enabled": False})
        self.assertEqual(self.store.list_codes(status="disabled")["items"][0]["expiry_warning"], "")
        with self.assertRaises(ValueError):
            self.store.list_codes(sort="created_at; DROP TABLE codes")

    def test_compact_activity_preserves_statistics_and_failure_detection(self):
        code = self.store.create_codes({"label": "reconnect"})[0]
        for _ in range(16):
            self.store.record("meeting_started", code_id=code["id"], client="client", meeting="same", channel="web")
            self.store.record("meeting_ended", code_id=code["id"], client="client", meeting="same", channel="web")
            self.store.record("visit", client="client", channel="web")
            self.store.reveal(code["id"])
        self.assertEqual(self.store.events(kind="meeting_started")["total"], 1)
        self.assertEqual(self.store.events(kind="meeting_started")["items"][0]["occurrences"], 16)
        self.assertEqual(self.store.events(kind="meeting_ended")["total"], 0)
        self.assertEqual(self.store.events(kind="visit")["total"], 0)
        self.assertEqual(self.store.events(kind="admin_revealed")["total"], 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM daily_visits").fetchone()[0], 1)
        result = self.store.overview()
        self.assertEqual((result["visits"], result["visitors"], result["meetings"]), (16, 1, 1))
        for _ in range(5):
            self.store.redeem("wrong", "client", "ip", "web")
        self.assertEqual(self.store.events(kind="invite_failure")["total"], 5)
        self.assertEqual(self.store.alerts()["items"][0]["kind"], "repeated_failures")

    def test_legacy_migration_is_backed_up_and_idempotent(self):
        import sqlite3
        now = time.time()
        with self.store.db:
            self.store.db.execute("DELETE FROM settings WHERE key='activity_compaction_v1'")
            self.store.db.executemany("INSERT INTO events(created_at,kind,client,channel,meeting) VALUES (?,?,?,?,?)",
                                     [(now, kind, "old-client", "web", "same") for _ in range(16) for kind in ("meeting_started", "meeting_ended", "visit")])
        self.store.close()
        self.store = AdminStore(self.path)
        backup = self.path.with_name(self.path.name + ".before-activity-v1.bak")
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as copy:
            self.assertEqual(copy.execute("SELECT count(*) FROM events").fetchone()[0], 48)
        self.assertEqual(self.store.events()["total"], 1)
        self.assertEqual(self.store.events()["items"][0]["occurrences"], 16)
        self.assertEqual(self.store.overview()["visits"], 16)
        self.store.close()
        self.store = AdminStore(self.path)
        self.assertEqual(self.store.overview()["visits"], 16)

    def test_retention_keeps_current_90_days_without_resetting_codes(self):
        from admin_store import day_start
        cutoff = day_start(time.time()) - 89*86400
        code = self.store.create_codes({"label": "permanent"})[0]
        for at in (cutoff-1, cutoff):
            with patch("admin_store.time.time", return_value=at):
                self.store.record("visit", client="client", channel="web")
                self.store.redeem(code["code"], "client", "ip", "web")
        self.store.maintenance()
        self.assertEqual(self.store.events(kind="invite_success")["total"], 1)
        self.assertEqual(self.store.db.execute("SELECT sum(visits) FROM daily_visits").fetchone()[0], 1)
        self.assertEqual(self.store.list_codes()["items"][0]["use_count"], 2)
        self.assertIsNotNone(self.store.redeem(code["code"], "client", "ip", "web"))


if __name__ == "__main__":
    unittest.main()
