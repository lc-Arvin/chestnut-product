import os
import unittest
from unittest.mock import patch

import pymysql
import server
from deployment import Deployment
from mysql_store import MySQLSetupError, mysql_failure_details, mysql_options


class MySQLDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "CHESTNUT_MYSQL_HOST": "database.example",
            "CHESTNUT_MYSQL_USER": "test-user",
            "CHESTNUT_MYSQL_PASSWORD": "secret-do-not-log",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_settings_identify_the_variable_without_connecting(self):
        for name in ("USER", "PASSWORD", "HOST", "DATABASE"):
            with self.subTest(name=name), patch.dict(os.environ, {"CHESTNUT_MYSQL_" + name: ""}):
                with self.assertRaises(MySQLSetupError) as caught:
                    mysql_options()
                self.assertEqual(caught.exception.reason, "missing_" + name.lower())
                self.assertIn("CHESTNUT_MYSQL_" + name, str(caught.exception))

    def test_legacy_credentials_and_default_database_still_work(self):
        with patch.dict(os.environ, {
            "CHESTNUT_MYSQL_USER": "", "CHESTNUT_MYSQL_PASSWORD": "",
            "CHESTNUT_CLOUD_DB_ADMIN": "legacy-user", "CHESTNUT_CLOUD_DB_PASSWORD": "legacy-password",
        }):
            options = mysql_options()
        self.assertEqual(options["user"], "legacy-user")
        self.assertEqual(options["password"], "legacy-password")
        self.assertEqual(options["database"], "chestnut")
        self.assertEqual(options["port"], 3306)

    def test_invalid_numbers_do_not_echo_the_supplied_value(self):
        for name, reason, values in (
            ("PORT", "invalid_port", ("secret-do-not-log", "0", "65536")),
            ("TIMEOUT_SECONDS", "invalid_timeout", ("secret-do-not-log", "0", "61")),
        ):
            for value in values:
                with self.subTest(name=name, value=value), patch.dict(os.environ, {"CHESTNUT_MYSQL_" + name: value}):
                    with self.assertRaises(MySQLSetupError) as caught:
                        mysql_options()
                    self.assertEqual(caught.exception.reason, reason)
                    self.assertNotIn("secret-do-not-log", str(caught.exception))
                    self.assertTrue(caught.exception.__suppress_context__ or value.isdigit())

    def test_startup_reports_safe_diagnostics_and_never_driver_messages(self):
        failures = [(MySQLSetupError("missing_host"), "missing_host")]
        failures += [(pymysql.err.OperationalError(code, "secret-do-not-log"), f"mysql_{code}")
                     for code in (1044, 1045, 1049, 1142, 2003, 2006, 2013, 9999)]
        failures.append((RuntimeError("secret-do-not-log"), "internal_error"))
        for error, reason in failures:
            with self.subTest(reason=reason), \
                    patch.object(server, "validate_configuration"), \
                    patch.object(server.Deployment, "from_env", return_value=Deployment(cloud=True, database="mysql")), \
                    patch("mysql_store.MySQLAdminStore", side_effect=error), \
                    self.assertLogs(server.LOGGER, level="ERROR") as logs:
                with self.assertRaises(RuntimeError) as caught:
                    server.create_app()
                self.assertIn(f"[{reason}]", str(caught.exception))
                self.assertIn(f"reason={reason}", " ".join(logs.output))
                self.assertNotIn("secret-do-not-log", str(caught.exception) + " ".join(logs.output))
                self.assertTrue(caught.exception.__suppress_context__)

    def test_known_internal_failures_have_specific_safe_hints(self):
        for reason in ("write_lock_timeout", "unsupported_schema"):
            reported_reason, hint = mysql_failure_details(MySQLSetupError(reason))
            self.assertEqual(reported_reason, reason)
            self.assertTrue(hint)


if __name__ == "__main__":
    unittest.main()
