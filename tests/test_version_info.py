from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import version_info
from scripts.commit_version import commit_version, git


ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_each_commit_gets_matching_tag_time_and_source_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git("init", "-q", root=root)
            for key, value in (("user.name", "Version Test"), ("user.email", "test@example.invalid"),
                               ("commit.gpgSign", "false"), ("tag.gpgSign", "false"),
                               ("core.hooksPath", str(root / "no-hooks"))):
                git("config", key, value, root=root)
            previous = None
            for content in ("first\n", "second\n"):
                (root / "app.txt").write_text(content, encoding="utf-8")
                git("add", "app.txt", root=root)
                with redirect_stdout(io.StringIO()):
                    metadata, revision = commit_version("test version", root=root)
                embedded = json.loads(git("show", "HEAD:VERSION.json", root=root))
                self.assertEqual(embedded, metadata)
                self.assertEqual(git("rev-list", "-1", metadata["code_ref"], root=root).decode().strip(), revision)
                actual_time = int(git("show", "-s", "--format=%ct", "HEAD", root=root))
                self.assertEqual(actual_time, int(datetime.fromisoformat(metadata["commit_time_utc"]).timestamp()))
                entries = git("ls-files", "--stage", "-z", root=root).split(b"\0")
                source = b"\0".join(entry for entry in entries
                                     if entry and entry.split(b"\t", 1)[1] != b"VERSION.json") + b"\0"
                self.assertEqual(metadata["source_sha256"], hashlib.sha256(source).hexdigest())
                self.assertFalse(git("status", "--porcelain", root=root).strip())
                if previous:
                    self.assertNotEqual(previous["version"], metadata["version"])
                    self.assertNotEqual(previous["source_sha256"], metadata["source_sha256"])
                previous = metadata
            with self.assertRaisesRegex(RuntimeError, "No staged changes"):
                commit_version("empty", root=root)

    def test_packaged_metadata_works_without_git_and_ignores_stale_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION.json").write_text(json.dumps({"version": "test-release", "code_ref": "refs/tags/test-release"}))
            with patch.object(version_info.subprocess, "check_output") as command:
                version_info.write_build_info(root)
                info = version_info.read_version(root)
                self.assertEqual(info["version"], "test-release")
                self.assertNotEqual(info["built_at_utc"], "local")
                self.assertNotIn("git_commit", info)
                command.assert_not_called()
                (root / ".build-info.json").write_text('{"version":"stale","built_at_utc":"old"}')
                self.assertEqual(version_info.read_version(root)["built_at_utc"], "local")

    def test_banner_is_once_per_process_and_does_not_read_credentials(self):
        output = io.StringIO()
        with patch.object(version_info, "_announced", False), \
                patch.object(version_info, "read_version", return_value={"version": "test-release"}), \
                patch.dict(os.environ, {"CHESTNUT_MYSQL_PASSWORD": "secret-do-not-log"}), redirect_stdout(output):
            version_info.announce_startup_version()
            version_info.announce_startup_version()
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["event"], "service_starting")
        self.assertNotIn("secret-do-not-log", lines[0])

    def test_banner_precedes_invalid_port_for_both_start_commands(self):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CHESTNUT_", "DASHSCOPE_", "BAILIAN_", "TENCENTCLOUD_"))}
        with tempfile.TemporaryDirectory() as directory:
            env.update(PORT="invalid-port", CHESTNUT_LOCAL_ADMIN_PORT="invalid-port",
                       CHESTNUT_ENV_FILE=str(Path(directory) / "absent.env"))
            for entry in ("server.py", "scripts/start_admin_local.py"):
                with self.subTest(entry=entry):
                    result = subprocess.run([sys.executable, entry], cwd=ROOT, env=env,
                                            capture_output=True, text=True, timeout=20)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(json.loads(result.stdout.splitlines()[0])["event"], "service_starting")
                    self.assertIn("invalid-port", result.stderr)
                    self.assertNotIn("event=service_started", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
