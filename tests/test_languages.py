import unittest
from unittest.mock import patch, AsyncMock

from aiohttp.test_utils import TestClient, TestServer
import server


class LanguageTests(unittest.TestCase):
    def test_legacy_default(self):
        for value in (None, "", "  "):
            self.assertEqual(server.parse_language_pair(value), ("zh", "en"))

    def test_non_chinese_pair(self):
        self.assertEqual(server.parse_language_pair("ja,fr"), ("ja", "fr"))
        self.assertEqual(server.parse_language_pair("en,zh"), ("en", "zh"))

    def test_invalid_pairs(self):
        for value in ("zh,zh", "ja", "ja,xx", "zh,en,fr", ",en"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.parse_language_pair(value)

    def test_transcript_preserves_languages(self):
        _, rendered = server.render_meeting_transcript({"entries": [
            {"language": "ja", "text": "こんにちは", "role": "original"},
            {"language": "fr", "text": "Bonjour", "role": "translation"},
        ]})
        self.assertIn("日本語", rendered)
        self.assertIn("Français", rendered)
        self.assertNotIn("English", rendered)

    def test_model_target_and_single_transcription(self):
        for code in server.LANGUAGE_LABELS:
            primary = server.session_update(code, True)["session"]
            secondary = server.session_update(code, False)["session"]
            self.assertEqual(primary["translation"]["language"], code)
            self.assertTrue(primary["input_audio_transcription"])
            self.assertIsNone(secondary["input_audio_transcription"])
            self.assertTrue(primary["translation"]["same_language_skip_options"]["skip_text"])


class LanguageSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(server.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_catalog(self):
        response = await self.client.get('/api/languages')
        self.assertEqual(await response.json(), server.LANGUAGE_LABELS)

    async def test_socket_forwards_selection_and_legacy_default(self):
        for query, expected in (("", ("zh", "en")), ("?languages=ja,fr", ("ja", "fr"))):
            with patch.object(server, 'request_identity', return_value=server.ClientIdentity('local-anonymous', 'local')), patch.object(server, 'handle_browser', new_callable=AsyncMock) as bridge:
                socket = await self.client.ws_connect('/ws' + query)
                await socket.receive()
                self.assertEqual(bridge.call_args.kwargs['language_pair'], expected)
                await socket.close()

    async def test_invalid_selection_never_connects_to_model(self):
        with patch.object(server, 'request_identity', return_value=server.ClientIdentity('local-anonymous', 'local')), patch.object(server, 'handle_browser', new_callable=AsyncMock) as bridge:
            socket = await self.client.ws_connect('/ws?languages=zh,xx')
            event = await socket.receive_json()
            self.assertEqual(event['type'], 'meeting.rejected')
            bridge.assert_not_called()
            await socket.close()
