"""Commit staged changes with an embedded timestamp, source fingerprint and Git tag."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def git(*args, root=ROOT, env=None):
    return subprocess.check_output(["git", *args], cwd=root, env=env)


def commit_version(message, root=ROOT):
    if git("diff", "--name-only", root=root).strip():
        raise RuntimeError("Stage all tracked changes before creating a versioned commit")
    if not git("diff", "--cached", "--name-only", root=root).strip():
        raise RuntimeError("No staged changes to commit")
    if git("ls-files", "--unmerged", root=root).strip():
        raise RuntimeError("Resolve merge conflicts before creating a versioned commit")
    # Canonical Git index includes paths, file modes and content object IDs.
    # Exclude this generated file to avoid a self-referential fingerprint.
    entries = git("ls-files", "--stage", "-z", root=root).split(b"\0")
    source = b"\0".join(entry for entry in entries
                         if entry and entry.split(b"\t", 1)[1] != b"VERSION.json") + b"\0"
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    committed = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    version = "chestnut-" + now.strftime("%Y%m%dT%H%M%S%fZ")
    metadata = {
        "version": version, "code_ref": "refs/tags/" + version,
        "commit_time_utc": committed, "stamped_at_utc": stamp,
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }
    (root / "VERSION.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    git("add", "--", "VERSION.json", root=root)
    env = dict(os.environ, GIT_COMMITTER_DATE=committed)
    subprocess.run(["git", "commit", "-m", message], cwd=root, env=env, check=True)
    revision = git("rev-parse", "HEAD", root=root).decode().strip()
    git("tag", "-a", version, "-m", f"{version}\ncommit={revision}\nsource_sha256={metadata['source_sha256']}",
        root=root, env=env)
    print(json.dumps({**metadata, "git_commit": revision}, indent=2), flush=True)
    return metadata, revision


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-m", "--message", required=True)
    args = parser.parse_args()
    commit_version(args.message)
