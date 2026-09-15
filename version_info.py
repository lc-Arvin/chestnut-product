"""Read embedded release metadata and announce it before application setup."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent
_announced = False


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def read_version(root=ROOT):
    result = {
        "version": "unversioned", "code_ref": "unknown", "commit_time_utc": "unknown",
        "stamped_at_utc": "unknown", "source_sha256": "unknown", "built_at_utc": "local",
    }
    try:
        release = json.loads((root / "VERSION.json").read_text(encoding="utf-8"))
        for key in result:
            if key != "built_at_utc" and isinstance(release.get(key), str):
                result[key] = release[key]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        build = json.loads((root / ".build-info.json").read_text(encoding="utf-8"))
        if build.get("version") == result["version"] and isinstance(build.get("built_at_utc"), str):
            result["built_at_utc"] = build["built_at_utc"]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if (root / ".git").exists():
        try:
            result["git_commit"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL, timeout=2,
            ).decode().strip()
            result["working_tree_dirty"] = bool(subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=root, stderr=subprocess.DEVNULL, timeout=2,
            ).strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def announce_startup_version():
    global _announced
    if _announced:
        return
    # Stdlib only, flushed stdout: even an invalid .env, port or dependency
    # import must not hide which release is attempting to start.
    print(json.dumps({"time_utc": utc_now(), "level": "INFO", "event": "service_starting",
                      **read_version()}, ensure_ascii=True, sort_keys=True), flush=True)
    _announced = True


def write_build_info(root=ROOT):
    release = json.loads((root / "VERSION.json").read_text(encoding="utf-8"))
    (root / ".build-info.json").write_text(json.dumps({
        "version": release["version"], "built_at_utc": utc_now(),
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-build-info", action="store_true")
    args = parser.parse_args()
    if args.write_build_info:
        write_build_info()
    else:
        print(json.dumps(read_version(), indent=2))
