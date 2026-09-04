import unittest

import server


class AccessTokenTests(unittest.TestCase):
    def setUp(self):
        self.original_secret = server.AUTH_SECRET
        self.original_ttl = server.WEB_TOKEN_TTL_SECONDS
        server.AUTH_SECRET = "test-secret-that-is-longer-than-thirty-two-characters"
        server.WEB_TOKEN_TTL_SECONDS = 3600

    def tearDown(self):
        server.AUTH_SECRET = self.original_secret
        server.WEB_TOKEN_TTL_SECONDS = self.original_ttl

    def test_signed_token_round_trip(self):
        token = server.issue_access_token("browser-client-one", "customer-a")
        identity = server.verify_access_token(token)
        self.assertIsNotNone(identity)
        self.assertEqual(identity.kind, "web")
        self.assertTrue(identity.subject.startswith("web-"))
        self.assertEqual(identity.label, "customer-a")

    def test_tampered_token_is_rejected(self):
        token = server.issue_access_token("browser-client-one")
        payload, signature = token.split(".", 1)
        self.assertIsNone(server.verify_access_token(f"{payload}.{signature[:-1]}x"))

    def test_expired_token_is_rejected(self):
        server.WEB_TOKEN_TTL_SECONDS = -1
        token = server.issue_access_token("browser-client-one")
        self.assertIsNone(server.verify_access_token(token))


class LimiterTests(unittest.TestCase):
    def test_sliding_window_rejects_excess_attempts(self):
        limiter = server.SlidingWindowLimiter(2, 60)
        self.assertTrue(limiter.allow("client"))
        self.assertTrue(limiter.allow("client"))
        self.assertFalse(limiter.allow("client"))


class MeetingRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_cannot_start_a_second_meeting(self):
        registry = server.MeetingRegistry()
        identity = server.ClientIdentity("web-user", "web")
        first_socket = object()
        key, previous, error = await registry.acquire(identity, "meeting-a", first_socket)
        self.assertIsNotNone(key)
        self.assertIsNone(previous)
        self.assertIsNone(error)

        second_key, _, second_error = await registry.acquire(identity, "meeting-b", object())
        self.assertIsNone(second_key)
        self.assertIn("active meeting", second_error)
        await registry.release(identity, key)

    async def test_reconnect_replaces_same_meeting(self):
        registry = server.MeetingRegistry()
        identity = server.ClientIdentity("web-user", "web")
        first_socket = object()
        first_key, _, _ = await registry.acquire(identity, "meeting-a", first_socket)
        second_key, previous, error = await registry.acquire(identity, "meeting-a", object())
        self.assertIsNone(error)
        self.assertIs(previous, first_socket)
        await registry.release(identity, first_key)
        self.assertIn(identity.subject, registry.active)
        await registry.release(identity, second_key)
        self.assertNotIn(identity.subject, registry.active)

    async def test_global_capacity_rejects_a_new_user(self):
        original_limit = server.MAX_CONCURRENT_MEETINGS
        server.MAX_CONCURRENT_MEETINGS = 1
        try:
            registry = server.MeetingRegistry()
            first = server.ClientIdentity("web-one", "web")
            second = server.ClientIdentity("web-two", "web")
            first_key, _, _ = await registry.acquire(first, "meeting-a", object())
            second_key, _, error = await registry.acquire(second, "meeting-b", object())
            self.assertIsNone(second_key)
            self.assertIn("capacity", error)
            await registry.release(first, first_key)
        finally:
            server.MAX_CONCURRENT_MEETINGS = original_limit


class ConfigurationTests(unittest.TestCase):
    def test_authentication_requires_a_strong_signing_secret(self):
        original_required = server.AUTH_REQUIRED
        original_secret = server.AUTH_SECRET
        server.AUTH_REQUIRED = True
        server.AUTH_SECRET = "too-short"
        try:
            with self.assertRaisesRegex(RuntimeError, "at least 32 characters"):
                server.create_app()
        finally:
            server.AUTH_REQUIRED = original_required
            server.AUTH_SECRET = original_secret

    def test_negative_meeting_limit_is_rejected(self):
        original_limit = server.MAX_MEETING_SECONDS
        server.MAX_MEETING_SECONDS = -1
        try:
            with self.assertRaisesRegex(RuntimeError, "zero or greater"):
                server.validate_configuration()
        finally:
            server.MAX_MEETING_SECONDS = original_limit

    def test_negative_warning_limit_is_rejected(self):
        original_warning = server.MEETING_WARNING_SECONDS
        server.MEETING_WARNING_SECONDS = -1
        try:
            with self.assertRaisesRegex(RuntimeError, "zero or greater"):
                server.validate_configuration()
        finally:
            server.MEETING_WARNING_SECONDS = original_warning


class InvitationTests(unittest.TestCase):
    def test_labeled_and_legacy_invitation_codes_are_parsed_without_exposing_codes(self):
        invitations = server.parse_web_invitations("customer-a=secret-code,legacy-code")
        self.assertEqual(invitations[0], ("customer-a", "secret-code"))
        self.assertTrue(invitations[1][0].startswith("invite-"))
        self.assertNotIn("legacy-code", invitations[1][0])

    def test_web_transcript_uses_label_not_invitation_code(self):
        filename, _ = server.render_meeting_transcript({"entries": []}, "customer-a")
        self.assertRegex(filename, r"^web-\d{4}-\d{2}-\d{2}-customer-a-\d{6}\.md$")


class DurationLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_limit_applies_to_web_and_wechat_but_not_local_desktop(self):
        self.assertTrue(server.meeting_limit_applies_to(server.ClientIdentity("web-user", "web")))
        self.assertTrue(server.meeting_limit_applies_to(server.ClientIdentity("wechat-user", "wechat")))
        self.assertFalse(server.meeting_limit_applies_to(server.ClientIdentity("local-user", "local")))

    async def test_duration_limit_notifies_browser(self):
        class Browser:
            def __init__(self):
                self.messages = []

            async def send(self, message):
                self.messages.append(message)

        original_limit = server.MAX_MEETING_SECONDS
        original_warning = server.MEETING_WARNING_SECONDS
        server.MAX_MEETING_SECONDS = 0.002
        server.MEETING_WARNING_SECONDS = 0.001
        browser = Browser()
        try:
            await server.enforce_meeting_duration(browser, server.SessionMetrics())
            self.assertIn("meeting.limit_warning", browser.messages[0])
            self.assertIn("meeting.limit_reached", browser.messages[1])
        finally:
            server.MAX_MEETING_SECONDS = original_limit
            server.MEETING_WARNING_SECONDS = original_warning


if __name__ == "__main__":
    unittest.main()
