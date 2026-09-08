import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
import server
from admin_http import STORE_KEY
from trial_access import TrialRegistry, TrialSocket


class TrialRegistryTests(unittest.TestCase):
    def test_pending_active_expired_and_short_term_cleanup(self):
        now = [1000.0]
        registry = TrialRegistry(clock=lambda: now[0])
        trial = registry.claim("guest")
        now[0] += 300
        self.assertEqual(trial.info(now[0])["remaining_seconds"], 180)
        self.assertIs(registry.claim("guest"), trial)
        registry.start(trial)
        deadline = trial.deadline
        now[0] += 120
        registry.start(trial)
        self.assertEqual(trial.deadline, deadline)
        self.assertEqual(trial.info(now[0])["remaining_seconds"], 60)
        now[0] += 60
        self.assertEqual(trial.state(now[0]), "ended")
        with self.assertRaises(ValueError):
            registry.claim("guest")
        self.assertIsNotNone(registry.get(trial.id, "guest"))
        self.assertIsNone(registry.get(trial.id, "another-user"))
        now[0] += 601
        self.assertIsNone(registry.get(trial.id, "guest"))
        now[0] += 6 * 3600
        self.assertNotEqual(registry.claim("guest").id, trial.id)

    def test_stop_is_terminal_and_cannot_extend_save_grace(self):
        now = [1000.0]
        registry = TrialRegistry(clock=lambda: now[0])
        trial = registry.claim("guest")
        registry.start(trial)
        now[0] = 1190
        registry.finish(trial)
        self.assertEqual(trial.finished_at, 1180)
        now[0] += 200
        registry.finish(trial)
        self.assertEqual(trial.finished_at, 1180)
        with self.assertRaises(ValueError):
            registry.claim("guest")


class TrialSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_is_forwarded_only_inside_the_trial_window(self):
        now = [1000.0]
        registry = TrialRegistry(clock=lambda: now[0])
        trial = registry.claim("guest")
        class Browser:
            async def __aiter__(self):
                yield b"before-ready"
                registry.start(trial)
                yield b"allowed-audio"
                now[0] += 180
                yield b"after-expiry"
        received = [frame async for frame in TrialSocket(Browser(), registry, trial)]
        self.assertEqual(received, [b"allowed-audio"])


class TrialHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = patch.multiple(server, AUTH_REQUIRED=False, AUTH_SECRET="", WEB_INVITATIONS=(),
                                    LOGIN_LIMITER=server.SlidingWindowLimiter(100, 60),
                                    CONNECTION_LIMITER=server.SlidingWindowLimiter(100, 60),
                                    MEETING_REGISTRY=server.MeetingRegistry())
        self.config.start()
        self.client = TestClient(TestServer(server.create_app(admin_path=Path(self.directory.name) / "admin.sqlite3")), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.registry = self.client.server.app[server.TRIALS_KEY]
        self.now = time.time()
        self.registry.clock = lambda: self.now
        self.client_id = "trial-browser-one"
        self.origin = str(self.client.make_url("/")).rstrip("/")
        self.finalized = asyncio.Event()

    async def asyncTearDown(self):
        await self.client.close()
        self.config.stop()
        self.directory.cleanup()

    async def status(self, **headers):
        return await (await self.client.get("/api/auth/status", headers={"X-Chestnut-Client-ID": self.client_id, **headers})).json()

    async def claim(self, **payload):
        return await self.client.post("/api/auth/trial", json={"client_id": self.client_id, **payload})

    async def fake_model(self, browser, **_kwargs):
        try:
            await browser.send(json.dumps({"type": "session.updated", "translation_target": "zh"}))
            async for _message in browser:
                pass
        finally:
            self.finalized.set()

    async def ready(self, socket):
        for _ in range(4):
            event = await socket.receive_json(timeout=3)
            if event["type"] == "session.updated":
                return
        self.fail("Session did not become ready")

    async def test_trial_is_idempotent_and_has_no_admin_or_invite_privileges(self):
        self.assertTrue((await self.status())["trial"]["available"])
        responses = await asyncio.gather(self.claim(), self.claim())
        first, second = [await response.json() for response in responses]
        self.assertEqual(first["trial"]["meeting_id"], second["trial"]["meeting_id"])
        self.assertEqual(len(self.registry.by_id), 1)
        status = await self.status()
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["access_mode"], "trial")
        self.assertNotIn("invitation_expires_at", status)
        self.assertEqual((await self.client.get("/api/admin/codes")).status, 401)
        self.assertFalse((await self.client.get("/api/admin/session")).cookies)

    async def test_server_stops_idle_trial_and_preserves_deadline_on_reconnect(self):
        data = await (await self.claim()).json()
        meeting = data["trial"]["meeting_id"]
        with patch.object(server, "handle_browser", side_effect=self.fake_model):
            socket = await self.client.ws_connect("/ws?meeting_id=" + meeting)
            await self.ready(socket)
            trial = next(iter(self.registry.by_id.values()))
            deadline = trial.deadline
            await socket.close()
            await asyncio.wait_for(self.finalized.wait(), 2)
            self.finalized.clear()
            self.now += 120
            socket = await self.client.ws_connect("/ws?meeting_id=" + meeting)
            event = await socket.receive_json(timeout=3)
            self.assertEqual(event["remaining_seconds"], 60)
            await self.ready(socket)
            self.assertEqual(trial.deadline, deadline)
            self.now += 61  # No client audio or timer: server still cuts access.
            self.assertEqual((await socket.receive_json(timeout=3))["type"], "trial.ended")
            await socket.close()
            await asyncio.wait_for(self.finalized.wait(), 2)
        self.assertFalse((await self.status())["authenticated"])
        self.assertEqual((await self.claim()).status, 409)
        await self.client.post("/api/auth/logout")
        self.assertFalse((await self.status())["trial"]["available"])
        self.assertEqual((await self.claim()).status, 409)

    async def test_expired_trial_can_save_only_its_own_meeting_once_and_retry_failures(self):
        data = await (await self.claim()).json()
        trial = next(iter(self.registry.by_id.values()))
        self.registry.start(trial)
        self.now += 181
        payload = {"meeting_id": data["trial"]["meeting_id"], "entries": []}
        bad = await self.client.post("/api/meetings", json={**payload, "meeting_id": "someone-else"})
        self.assertEqual(bad.status, 403)
        with patch.object(server, "save_meeting_transcript", side_effect=OSError("temporary")):
            self.assertEqual((await self.client.post("/api/meetings", json=payload)).status, 503)
        with patch.object(server, "save_meeting_transcript", return_value={"filename": "trial.md", "storage": "local"}) as save:
            for _ in range(2):
                self.assertEqual((await self.client.post("/api/meetings", json=payload)).status, 201)
            save.assert_called_once()
        self.now += 600
        self.assertEqual((await self.client.post("/api/meetings", json=payload)).status, 401)

    async def test_no_upstream_for_expired_or_wrong_meeting_and_invite_upgrades(self):
        await self.claim()
        trial = next(iter(self.registry.by_id.values()))
        with patch.object(server, "handle_browser") as model:
            socket = await self.client.ws_connect("/ws?meeting_id=wrong-meeting")
            self.assertEqual((await socket.receive_json())["type"], "trial.ended")
            await socket.close()
            self.registry.finish(trial)
            socket = await self.client.ws_connect("/ws?meeting_id=" + trial.meeting_id)
            self.assertEqual((await socket.receive_json())["type"], "trial.ended")
            await socket.close()
            model.assert_not_called()
        store = self.client.server.app[STORE_KEY]
        code = store.create_codes({"label": "guest upgrade"})[0]["code"]
        response = await self.client.post("/api/auth/invite", json={"code": code, "client_id": self.client_id})
        self.assertEqual(response.status, 200)
        status = await self.status()
        self.assertEqual(status["access_mode"], "invitation")
        self.assertTrue(status["authenticated"])
        self.assertFalse(status["trial"]["available"])

    async def test_wechat_binding_and_header_independent_repeated_claim(self):
        headers = {"x-wx-openid": "wechat-trial-user"}
        async def claim(client):
            response = await self.client.post("/api/auth/trial", headers=headers, json={"client_id": client, "client_type": "miniprogram"})
            return await response.json()
        first, second = await claim("first-device"), await claim("second-device")
        self.assertEqual(first["trial"]["meeting_id"], second["trial"]["meeting_id"])
        token = {"Authorization": "Bearer " + first["access_token"]}
        self.assertTrue((await self.status(**headers, **token))["authenticated"])
        self.assertFalse((await self.status(**token))["authenticated"])
        self.assertFalse((await self.status(**token, **{"x-wx-openid": "other-user"}))["authenticated"])

    async def test_origin_limits_and_explicit_stop(self):
        self.assertEqual((await self.client.post("/api/auth/trial", headers={"Origin": "https://other.invalid"}, json={"client_id": self.client_id})).status, 403)
        self.assertEqual((await self.client.post("/api/auth/trial", json={"client_id": "x"})).status, 400)
        await self.claim()
        self.assertEqual((await self.client.post("/api/auth/trial/finish")).status, 200)
        self.assertEqual((await self.claim()).status, 409)
        self.registry.duration = 0
        self.assertEqual((await self.claim()).status, 403)
