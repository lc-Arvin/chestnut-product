import json
import unittest
from unittest.mock import patch, AsyncMock
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import CookieJar
import server


class SharedAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = patch.multiple(server, AUTH_REQUIRED=True,
            AUTH_SECRET='test-secret-with-at-least-thirty-two-characters',
            WEB_INVITATIONS=[('customer', 'invite-good')],
            LOGIN_LIMITER=server.SlidingWindowLimiter(100, 60),
            CONNECTION_LIMITER=server.SlidingWindowLimiter(100, 60),
            MEETING_REGISTRY=server.MeetingRegistry())
        self.config.start()
        self.client = TestClient(TestServer(server.create_app()), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.config.stop()

    async def login(self, openid='person-a', code='invite-good'):
        response = await self.client.post('/api/auth/invite', headers={'x-wx-openid': openid}, json={
            'client_id': 'test-client-one', 'client_type': 'miniprogram', 'code': code,
        })
        return response, await response.json()

    async def test_public_page_but_openid_alone_is_not_authorized(self):
        self.assertEqual((await self.client.get('/')).status, 200)
        self.assertEqual((await self.client.get('/api/languages')).status, 200)
        status = await (await self.client.get('/api/auth/status', headers={'x-wx-openid': 'person-a'})).json()
        self.assertTrue(status['auth_required'])
        self.assertFalse(status['authenticated'])
        self.assertEqual((await self.client.post('/api/meetings', headers={'x-wx-openid':'person-a'}, json={'entries':[]})).status, 401)

    async def test_wrong_invite_and_rate_limit(self):
        response, _ = await self.login(code='wrong')
        self.assertEqual(response.status, 401)
        with patch.object(server, 'LOGIN_LIMITER', server.SlidingWindowLimiter(1, 60)):
            await self.login(code='wrong')
            response, _ = await self.login()
            self.assertEqual(response.status, 429)

    async def test_binding_and_preserved_transcript_owner(self):
        response, result = await self.login()
        self.assertEqual(response.status, 200)
        token = result['access_token']
        identity = server.verify_access_token(token)
        self.assertEqual(identity.subject, 'wechat-person-a')
        self.assertEqual(identity.kind, 'wechat')
        for openid, expected in [('person-a', True), ('person-b', False), ('', False)]:
            status = await (await self.client.get('/api/auth/status', headers={'x-wx-openid':openid, 'Authorization': f'Bearer {token}'})).json()
            self.assertEqual(status['authenticated'], expected)
        with patch.object(server, 'save_meeting_transcript', return_value={'storage':'local','filename':'test.md'}) as save:
            response = await self.client.post('/api/meetings', headers={'x-wx-openid':'person-a','Authorization': f'Bearer {token}'}, json={'entries':[]})
            self.assertEqual(response.status, 201)
            self.assertEqual(save.call_args.args[1], 'wechat-person-a')
        other = await self.client.get('/meetings/wechat-person-b/test.md', headers={'x-wx-openid':'person-a','Authorization':f'Bearer {token}'})
        self.assertEqual(other.status, 403)

    async def test_web_cookie_flow_remains_valid(self):
        response = await self.client.post('/api/auth/invite', json={'client_id':'web-client-one','code':'invite-good'})
        self.assertNotIn('access_token', await response.json())
        self.assertTrue((await (await self.client.get('/api/auth/status')).json())['authenticated'])
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        # A Web token must never bypass the WeChat invitation check.
        self.assertFalse((await (await self.client.get('/api/auth/status', headers={'x-wx-openid':'person-a'})).json())['authenticated'])
        await self.client.post('/api/auth/logout')
        self.assertFalse((await (await self.client.get('/api/auth/status')).json())['authenticated'])

    async def test_websocket_checks_first_message_before_model(self):
        _, result = await self.login()
        token = result['access_token']
        with patch.object(server, 'handle_browser', new_callable=AsyncMock) as model:
            socket = await self.client.ws_connect('/ws?auth=message&languages=yue,zh', headers={'x-wx-openid':'person-a'})
            model.assert_not_called()
            await socket.send_json({'type':'auth.authenticate','token':token})
            await socket.receive()
            model.assert_awaited_once()
            self.assertEqual(model.call_args.kwargs['language_pair'], ('yue','zh'))
            await socket.close()

    async def test_missing_foreign_expired_and_malformed_socket_auth_rejected(self):
        _, result = await self.login()
        with patch.object(server, 'WEB_TOKEN_TTL_SECONDS', -1):
            expired = server.issue_access_token('test-client-one', wechat_openid='person-a')
        cases = [({}, 'person-a'), ({'type':'auth.authenticate','token':result['access_token']}, 'person-b'),
                 ({'type':'auth.authenticate','token':expired}, 'person-a'),
                 ({'type':'auth.authenticate','token':['bad']}, 'person-a')]
        for payload, openid in cases:
            with patch.object(server, 'handle_browser', new_callable=AsyncMock) as model:
                socket = await self.client.ws_connect('/ws?auth=message', headers={'x-wx-openid':openid})
                await socket.send_json(payload)
                self.assertEqual((await socket.receive_json())['type'], 'access.denied')
                model.assert_not_called()
                await socket.close()
        with patch.object(server, 'handle_browser', new_callable=AsyncMock) as model:
            socket = await self.client.ws_connect('/ws', headers={'x-wx-openid':'person-a'})
            self.assertEqual((await socket.receive_json())['type'], 'access.denied')
            model.assert_not_called()
            await socket.close()

    async def test_expired_save_can_retry_with_same_owner(self):
        with patch.object(server, 'WEB_TOKEN_TTL_SECONDS', -1):
            expired = server.issue_access_token('test-client-one', wechat_openid='person-a')
        response = await self.client.post('/api/meetings', headers={'x-wx-openid':'person-a','Authorization': f'Bearer {expired}'}, json={'entries':[]})
        self.assertEqual(response.status, 401)
        _, result = await self.login()
        self.assertEqual(server.verify_access_token(result['access_token']).subject, 'wechat-person-a')
