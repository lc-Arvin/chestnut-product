import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
import server
from admin_http import STORE_KEY
from deployment import Deployment, DEPLOYMENT_KEY, client_ip, wechat_openid


class ConfigurationTests(unittest.TestCase):
    def test_invalid_configuration_does_not_announce_successful_startup(self):
        with patch.object(server, "load_bailian_credentials"), patch.object(server, "create_app", side_effect=RuntimeError("missing configuration")), \
                patch.object(server, "LOGGER") as logger, patch.object(server.web, "run_app") as run:
            with self.assertRaisesRegex(RuntimeError, "missing configuration"):
                server.main()
            logger.info.assert_not_called()
            run.assert_not_called()

    def test_startup_message_waits_for_aiohttp_listening_callback(self):
        with patch.object(server, "load_bailian_credentials"), patch.object(server, "create_app", return_value=object()), \
                patch.object(server, "LOGGER") as logger, patch.object(server.web, "run_app") as run:
            server.main()
            logger.info.assert_not_called()
            run.call_args.kwargs["print"]("listening")
            self.assertIn("event=service_started", logger.info.call_args_list[0].args[0])

    def test_cos_download_reads_complete_document_and_closes_stream(self):
        from unittest.mock import Mock
        stream = Mock()
        stream.read.return_value = b"a" * (server.MAX_TRANSCRIPT_BYTES + 100)
        client = Mock()
        client.get_object.return_value = {"Body": Mock(get_raw_stream=Mock(return_value=stream))}
        with patch.object(server, "cos_client", return_value=client):
            self.assertEqual(len(server.read_cos_transcript("owner", "meeting.md")), server.MAX_TRANSCRIPT_BYTES + 100)
        stream.read.assert_called_once_with()
        stream.close.assert_called_once()

    def test_cloud_fails_closed_without_required_configuration(self):
        with patch.dict(os.environ, {"CHESTNUT_ENV": "cloud"}, clear=True):
            with self.assertRaises(RuntimeError):
                Deployment.from_env()
        with patch.dict(os.environ, {"CHESTNUT_ENV": "cloud", "CHESTNUT_PUBLIC_ORIGIN": "https://meet.example.com",
                                     "CHESTNUT_AUTH_SECRET": "s" * 48}, clear=True):
            config = Deployment.from_env()
            self.assertEqual(config.database, "mysql")
            self.assertEqual(config.transcript_storage, "mysql")
            self.assertTrue(config.admin_enabled)
            for name, value in (("CHESTNUT_DATABASE_BACKEND", "sqlite"), ("CHESTNUT_TRANSCRIPT_STORAGE", "local"),
                                ("CHESTNUT_PUBLIC_ORIGIN", "https://meet.example.com/path")):
                with patch.dict(os.environ, {name: value}):
                    with self.assertRaises(RuntimeError):
                        Deployment.from_env()

    def test_proxy_identity_requires_configured_peer_and_ignores_spoofed_prefix(self):
        import ipaddress
        app = web.Application()
        app[DEPLOYMENT_KEY] = Deployment(cloud=True, trusted_proxies=(ipaddress.ip_network("10.20.0.0/24"),))
        request = make_mocked_request("GET", "/api/auth/status", app=app,
                                     headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.10, 10.20.0.2", "X-WX-OpenID": "wx-user"})
        with patch.object(type(request), "remote", new=property(lambda self: "10.20.0.3")):
            self.assertEqual(client_ip(request), "203.0.113.10")
            self.assertEqual(wechat_openid(request), "wx-user")
        with patch.object(type(request), "remote", new=property(lambda self: "198.51.100.8")):
            self.assertEqual(client_ip(request), "198.51.100.8")
            self.assertEqual(wechat_openid(request), "")


class CloudBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {"CHESTNUT_ENV": "local", "CHESTNUT_DATABASE_BACKEND": "sqlite",
                                          "CHESTNUT_TRANSCRIPT_STORAGE": "local", "CHESTNUT_ADMIN_ENABLED": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.multiple(server, AUTH_REQUIRED=False, AUTH_SECRET="", WEB_INVITATIONS=(),
                                     LOGIN_LIMITER=server.SlidingWindowLimiter(100, 60))
        self.config.start()
        self.addCleanup(self.config.stop)
        app = server.create_app(admin_path=Path(self.directory.name) / "test.sqlite3")
        app[DEPLOYMENT_KEY] = Deployment(cloud=True, public_origin="https://meet.example.com", admin_enabled=True)
        self.store = app[STORE_KEY]
        self.store.setup("test-admin-password-123")
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.origin = {"Origin": "https://meet.example.com"}

    async def test_cloud_login_csrf_secure_cookie_and_disabled_public_setup(self):
        self.assertEqual((await self.client.get("/admin")).status, 200)
        result = await (await self.client.get("/api/admin/session")).json()
        self.assertEqual(result["environment"], "cloud")
        self.assertFalse(result["setup_required"])
        self.assertEqual((await self.client.post("/api/admin/setup", json={"password": "attempted-takeover"}, headers=self.origin)).status, 404)
        self.assertEqual((await self.client.post("/api/admin/login", json={"password": "test-admin-password-123"})).status, 403)
        response = await self.client.post("/api/admin/login", json={"password": "test-admin-password-123"}, headers=self.origin)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.cookies["chestnut_admin"]["secure"])
        cookie = "chestnut_admin=" + response.cookies["chestnut_admin"].value
        csrf = (await response.json())["csrf"]
        headers = dict(self.origin, Cookie=cookie)
        self.assertEqual((await self.client.post("/api/admin/codes", json={"label": "blocked"}, headers=headers)).status, 403)
        headers["X-CSRF-Token"] = csrf
        self.assertEqual((await self.client.post("/api/admin/codes", json={"label": "cloud"}, headers=headers)).status, 201)
        headers["X-WX-OpenID"] = "mini-user"
        self.assertEqual((await self.client.get("/api/admin/codes", headers=headers)).status, 404)

    async def test_web_trial_and_invitation_cookies_are_secure_behind_http_gateway(self):
        code = self.store.create_codes({"label": "web"})[0]["code"]
        response = await self.client.post("/api/auth/invite", json={"client_id": "cloud-web-client", "code": code}, headers=self.origin)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.cookies["chestnut_access"]["secure"])
        trial = await self.client.post("/api/auth/trial", json={"client_id": "cloud-trial-client"}, headers=self.origin)
        self.assertEqual(trial.status, 200)
        self.assertTrue(trial.cookies["chestnut_access"]["secure"])

    async def test_cloud_origin_is_not_derived_from_spoofed_host(self):
        response = await self.client.post("/api/auth/invite", json={}, headers={"Origin": "https://evil.example", "Host": "evil.example"})
        self.assertEqual(response.status, 403)

    async def test_database_outage_is_reported_by_health_probe(self):
        with patch.object(self.store, "dialect", "mysql"), patch.object(self.store, "health", create=True, side_effect=ConnectionError):
            response = await self.client.get("/health")
            self.assertEqual(response.status, 503)
            result = await response.json()
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["checks"]["database"], "unavailable")
            self.assertEqual(result["version"], response.headers["X-Chestnut-Version"])

    async def test_release_headers_identify_success_and_admin_rejection(self):
        for path, headers, status in (("/health", {}, 200), ("/admin", {}, 200),
                                      ("/admin", {"X-WX-OpenID": "mini-user"}, 404),
                                      ("/missing-path", {}, 404)):
            response = await self.client.get(path, headers=headers)
            self.assertEqual(response.status, status)
            self.assertEqual(response.headers["X-Chestnut-Version"], server.runtime_version()["version"])
            self.assertEqual(response.headers["X-Chestnut-Boot-ID"], server.BOOT_ID)

    async def test_health_logs_transitions_only_and_false_is_unavailable(self):
        with patch.object(self.store, "dialect", "mysql"), \
                patch.object(self.store, "health", create=True, side_effect=[True, True, False, False, True]), \
                patch.object(server.LOGGER, "log") as log:
            statuses = [(await self.client.get("/health")).status for _ in range(5)]
            self.assertEqual(statuses, [200, 200, 503, 503, 200])
            self.assertEqual(log.call_count, 3)
