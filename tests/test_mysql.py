"""Opt-in real MySQL integration tests in a disposable, uniquely named database.

CHESTNUT_TEST_MYSQL=1 enables these tests. Connection settings come from .env and
process overrides. Production tables and local SQLite files are never touched.
"""
import asyncio
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
import server
from admin_http import STORE_KEY
from local_config import load_local_configuration
from mysql_store import MySQLAdminStore, mysql_options


@unittest.skipUnless(os.environ.get("CHESTNUT_TEST_MYSQL") == "1", "Set CHESTNUT_TEST_MYSQL=1 for isolated real MySQL tests")
class MySQLIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        import pymysql
        values = {}
        load_local_configuration(os.environ.get("CHESTNUT_ENV_FILE"), environ=values)
        values.update(os.environ)
        with patch.dict(os.environ, values):
            cls.options = mysql_options()
        cls.database = "chestnut_test_" + secrets.token_hex(8)
        cls.options["database"] = cls.database
        connection = pymysql.connect(**{k: v for k, v in cls.options.items() if k != "database"})
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        finally:
            connection.close()
        cls.addClassCleanup(cls.drop_test_database)

    @classmethod
    def drop_test_database(cls):
        import pymysql
        if not re.fullmatch(r"chestnut_test_[0-9a-f]{16}", cls.database):
            raise RuntimeError("Refusing cleanup outside the generated test database")
        connection = pymysql.connect(**{k: v for k, v in cls.options.items() if k != "database"})
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{cls.database}`")
        finally:
            connection.close()

    async def asyncSetUp(self):
        values = {"CHESTNUT_ENV": "cloud", "CHESTNUT_DATABASE_BACKEND": "mysql", "CHESTNUT_TRANSCRIPT_STORAGE": "mysql",
                  "CHESTNUT_AUTH_SECRET": "test-cloud-signing-secret-" * 2,
                  "CHESTNUT_PUBLIC_ORIGIN": "https://meet.example.com", "CHESTNUT_ADMIN_ENABLED": "1",
                  "CHESTNUT_ADMIN_BOOTSTRAP_PASSWORD": "cloud-test-password-123456", "CHESTNUT_TRUSTED_PROXY_CIDRS": "127.0.0.1/32"}
        self.environment = patch.dict(os.environ, values)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.options_patch = patch("mysql_store.mysql_options", return_value=self.options)
        self.options_patch.start()
        self.addCleanup(self.options_patch.stop)
        self.server_patch = patch.multiple(server, AUTH_REQUIRED=False, AUTH_SECRET=values["CHESTNUT_AUTH_SECRET"],
                                          WEB_INVITATIONS=(("must-not-import", "OLDLOCALCODE"),),
                                          LOGIN_LIMITER=server.SlidingWindowLimiter(1000, 60), MEETING_REGISTRY=server.MeetingRegistry())
        self.server_patch.start()
        self.addCleanup(self.server_patch.stop)
        app = server.create_app()
        self.store = app[STORE_KEY]
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.origin = {"Origin": values["CHESTNUT_PUBLIC_ORIGIN"]}

    def second_store(self):
        store = MySQLAdminStore(self.options)
        self.addCleanup(store.close)
        return store

    async def test_real_cloud_process_passes_release_and_admin_verification(self):
        from pathlib import Path
        from scripts.verify_deployment import check_once
        from version_info import runtime_version
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
            env = dict(os.environ, PYTHONIOENCODING="utf-8", CHESTNUT_HOST="127.0.0.1", PORT=str(port), CHESTNUT_LOG_FILE="",
                       CHESTNUT_ENV_FILE=str(Path(directory) / "absent.env"))
            for name in ("host", "port", "database", "user", "password"):
                env["CHESTNUT_MYSQL_" + name.upper()] = str(self.options[name])
            process = subprocess.Popen([sys.executable, "server.py"], cwd=server.ROOT,
                                       env=env, stdout=output, stderr=subprocess.STDOUT)
            result = {"ready": False}
            try:
                deadline = time.monotonic() + 20
                while process.poll() is None and time.monotonic() < deadline:
                    result = await asyncio.to_thread(check_once, f"http://127.0.0.1:{port}", runtime_version()["version"])
                    if result["ready"]:
                        break
                    await asyncio.sleep(0.2)
                self.assertTrue(result["ready"], result)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            output.seek(0)
            logs = output.read()
            self.assertFalse(self.options["password"] in logs, "Database credential leaked in process output")
            self.assertIn('"event": "service_starting"', logs.splitlines()[0])
            self.assertIn('"stage": "mysql_initialization"', logs)
            self.assertIn('"event": "administrator_state"', logs)
            self.assertIn("event=service_started", logs)

    async def test_quota_is_atomic_between_connections_and_survives_reopen(self):
        second = self.second_store()
        item = self.store.create_codes({"label": "quota", "max_uses": 1})[0]
        results = await asyncio.gather(
            asyncio.to_thread(self.store.redeem, item["code"], "client-one", "network-one", "web"),
            asyncio.to_thread(second.redeem, item["code"], "client-two", "network-two", "wechat"))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertTrue(second.token_valid(item["id"], 1))
        self.store.update_code(item["id"], {"enabled": False})
        self.assertFalse(second.token_valid(item["id"], 1))
        self.store.update_code(item["id"], {"enabled": True})
        self.assertFalse(second.token_valid(item["id"], 1))
        self.assertEqual(second.signing_secret, self.store.signing_secret)
        self.assertEqual(second.list_codes(search="OLDLOCALCODE")["total"], 0)

    async def test_transaction_rolls_back_on_failure(self):
        before = self.store.list_codes(search="rollback-case")["total"]
        with patch.object(self.store, "_event", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.store.create_codes({"label": "rollback-case", "quantity": 3})
        self.assertEqual(self.store.list_codes(search="rollback-case")["total"], before)
        self.assertEqual(len(self.store.create_codes({"label": "after-rollback"})), 1)

    async def test_unicode_sorting_events_alert_upserts_and_retention(self):
        one = self.store.create_codes({"label": "排序嘉宾🌰", "expiry_preset": "1h"})[0]
        self.store.create_codes({"label": "排序嘉宾🌰", "expiry_preset": "7d"})
        self.store.create_codes({"label": "排序嘉宾🌰", "expiry_preset": "forever"})
        listing = self.store.list_codes(search="排序嘉宾", sort="expires_asc")
        self.assertEqual(listing["items"][0]["id"], one["id"])
        self.assertIsNone(listing["items"][-1]["expires_at"])
        for _ in range(2):
            self.store.record("visit", client="visitor", channel="web")
            self.store.record("meeting_started", code_id=one["id"], client="visitor", meeting="same-meeting", channel="web")
        for _ in range(6):
            self.store.record("admin_login_failure", ip="failed-network", channel="admin")
        events = self.store.events(code_id=one["id"], kind="meeting_started")["items"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["occurrences"], 2)
        self.assertGreaterEqual(self.store.overview()["visits"], 2)
        alerts = self.store.alerts()["items"]
        alert = next(item for item in alerts if item["kind"] == "admin_login_failure")
        self.assertEqual(alert["occurrences"], 4)
        self.store.acknowledge(alert["id"])
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO events (created_at,kind) VALUES (?,?)", (time.time()-100*86400, "old-test"))
        self.store.maintenance()
        self.assertEqual(self.store.events(kind="old-test")["total"], 0)

    async def test_web_admin_login_and_session_persistence(self):
        response = await self.client.post("/api/admin/login", json={"password": "cloud-test-password-123456"}, headers=self.origin)
        self.assertEqual(response.status, 200, await response.text())
        token = response.cookies["chestnut_admin"].value
        csrf = (await response.json())["csrf"]
        self.assertEqual(self.second_store().session(token), csrf)
        headers = dict(self.origin, Cookie="chestnut_admin=" + token, **{"X-CSRF-Token": csrf})
        response = await self.client.post("/api/admin/codes", json={"label": "http-admin", "expiry_preset": "4h"}, headers=headers)
        self.assertEqual(response.status, 201, await response.text())
        self.assertEqual((await self.client.get("/api/admin/overview", headers=headers)).status, 200)

    async def test_web_and_miniprogram_auth_and_private_transcript_persistence(self):
        code = self.store.create_codes({"label": "HTTP客户"})[0]["code"]
        response = await self.client.post("/api/auth/invite", json={"code": code, "client_id": "web-client-http", "client_type": "miniprogram"})
        self.assertEqual(response.status, 200)
        token = (await response.json())["access_token"]
        headers = {"Authorization": "Bearer " + token}
        response = await self.client.post("/api/meetings", json={"meeting_id": "cloud-save-test", "entries": [{"text": "你好", "language": "zh", "role": "translation", "time_seconds": 1}]}, headers=headers)
        self.assertEqual(response.status, 201, await response.text())
        saved = await response.json()
        self.assertEqual(saved["storage"], "mysql")
        read = await self.client.get(saved["url"], headers=headers)
        self.assertEqual(read.status, 200)
        self.assertIn("你好", await read.text())
        self.assertEqual((await self.client.get(saved["url"])).status, 401)
        other = await self.client.post("/api/auth/invite", json={"code": code, "client_id": "another-client", "client_type": "miniprogram"}, headers={"X-WX-OpenID": "wx-user"})
        wx_token = (await other.json())["access_token"]
        wx_headers = {"Authorization": "Bearer " + wx_token, "X-WX-OpenID": "wx-user"}
        self.assertTrue((await (await self.client.get("/api/auth/status", headers=wx_headers)).json())["authenticated"])
        self.assertEqual((await self.client.get(saved["url"], headers=wx_headers)).status, 403)
        owner = server.verify_access_token(token).subject
        self.assertIn("你好", self.second_store().read_transcript(owner, saved["filename"]))
        self.assertEqual((await self.client.get("/health")).status, 200)
