import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from local_config import load_bailian_credentials, load_local_configuration
import server
from websockets.exceptions import InvalidStatus


class LocalCredentialTests(unittest.TestCase):
    def test_local_settings_load_before_server_configuration_and_preserve_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('CHESTNUT_LOGIN_RATE_LIMIT="10"\nCHESTNUT_TRIAL_SECONDS=180\nCHESTNUT_PORT=8090\nPATH=ignored\n', encoding="utf-8")
            env = {"CHESTNUT_TRIAL_SECONDS": "60"}
            load_local_configuration(path, env)
            self.assertEqual(env, {"CHESTNUT_LOGIN_RATE_LIMIT": "10", "CHESTNUT_TRIAL_SECONDS": "60", "CHESTNUT_PORT": "8090"})

    def test_quoted_credentials_bom_comments_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('\ufeff# local\nexport DASHSCOPE_API_KEY="local-key" # comment\nBAILIAN_API_HOST=workspace.example\nCHESTNUT_HOST=0.0.0.0\n', encoding="utf-8")
            env = {"DASHSCOPE_API_KEY": "cloud-key"}
            load_bailian_credentials(path, env)
            self.assertEqual(env, {"DASHSCOPE_API_KEY": "cloud-key", "BAILIAN_API_HOST": "workspace.example"})
            env = {}
            load_bailian_credentials(path, env)
            self.assertEqual(env["DASHSCOPE_API_KEY"], "local-key")

    def test_missing_file_and_invalid_format_do_not_expose_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            env = {}
            load_bailian_credentials(path, env)
            self.assertEqual(env, {})
            path.write_text('DASHSCOPE_API_KEY="secret-unclosed', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"^Invalid credential format in .env at line 1$"):
                load_bailian_credentials(path, env)


class RealtimeErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_credentials_are_terminal_and_never_connect(self):
        browser = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": " ", "BAILIAN_API_HOST": ""}), patch.object(server, "connect") as connect:
            await server.handle_browser(browser)
        connect.assert_not_called()
        event = json.loads(browser.send.call_args.args[0])
        self.assertFalse(event["retryable"])
        self.assertEqual(event["error"]["code"], "credentials_missing")
        browser.close.assert_awaited_once()

    async def test_auth_errors_stop_retrying_but_upstream_outage_can_recover(self):
        for status in (401, 403, 503):
            with self.subTest(status=status):
                browser = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
                response = SimpleNamespace(status_code=status, reason_phrase="test")
                with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test", "BAILIAN_API_HOST": "test.invalid"}), patch.object(server, "connect", side_effect=InvalidStatus(response)):
                    await server.handle_browser(browser)
                event = json.loads(browser.send.call_args.args[0])
                self.assertEqual(event["retryable"], status == 503)
                self.assertIn(str(status), event["error"]["message"])
