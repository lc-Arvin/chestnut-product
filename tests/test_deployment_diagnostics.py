from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import Mock, patch

from scripts.verify_deployment import check_once
from version_info import BOOT_ID, startup_stage


class DeploymentDiagnosticsTests(unittest.TestCase):
    def test_stage_failure_has_correlation_without_exception_details(self):
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(ValueError):
                with startup_stage("configuration"):
                    raise ValueError("password-do-not-log")
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["event"] for event in events], ["startup_stage_started", "startup_stage_failed"])
        self.assertTrue(all(event["boot_id"] == BOOT_ID for event in events))
        self.assertEqual(events[-1]["stage"], "configuration")
        self.assertNotIn("password-do-not-log", output.getvalue())

    def response(self, version, data=None, status=200):
        response = Mock(status=status, headers={"X-Chestnut-Version": version})
        response.read.return_value = json.dumps(data or {}).encode()
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        return context

    def test_old_healthy_release_does_not_pass_rollout_verification(self):
        opener = Mock(return_value=self.response("old"))
        self.assertFalse(check_once("https://test.invalid", "new", opener)["ready"])
        self.assertEqual(opener.call_count, 1)

    def test_mixed_version_routes_do_not_pass_rollout_verification(self):
        health = {"status": "ok", "version": "new", "checks": {"database": "ok"}}
        opener = Mock(side_effect=[self.response("new", health), self.response("old")])
        self.assertFalse(check_once("https://test.invalid", "new", opener)["ready"])

    def test_cloud_admin_and_all_routes_must_match_the_release(self):
        health = {"status": "ok", "version": "new", "checks": {"database": "ok"}}
        opener = Mock(side_effect=[self.response("new", health), self.response("new"),
                                  self.response("new"), self.response("new"),
                                  self.response("new", {"environment": "cloud"})])
        result = check_once("https://test.invalid", "new", opener)
        self.assertTrue(result["ready"])
        self.assertEqual(len(result["checks"]), 5)


if __name__ == "__main__":
    unittest.main()
