"""Read-only rollout verification: require expected version, healthy DB and Web admin."""
import argparse
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def check_once(origin, expected, opener=urlopen):
    checks = []
    for path in ("/health", "/", "/app.js", "/admin", "/api/admin/session"):
        url = origin.rstrip("/") + path + "?" + urlencode({"deployment_check": expected, "nonce": time.time_ns()})
        try:
            with opener(Request(url, headers={"Cache-Control": "no-cache"}), timeout=10) as response:
                check = {"path": path, "status": response.status,
                         "version": response.headers.get("X-Chestnut-Version", "missing")}
                checks.append(check)
                if response.status != 200 or check["version"] != expected:
                    return {"ready": False, "reason": "response_version_or_status_mismatch", "checks": checks}
                if path == "/health":
                    data = json.loads(response.read(16384))
                    if data.get("status") != "ok" or data.get("version") != expected or data.get("checks", {}).get("database") != "ok":
                        return {"ready": False, "reason": "database_or_health_version_mismatch", "checks": checks}
                elif path == "/api/admin/session":
                    data = json.loads(response.read(16384))
                    if data.get("environment") != "cloud":
                        return {"ready": False, "reason": "admin_not_in_cloud_mode", "checks": checks}
        except HTTPError as error:
            checks.append({"path": path, "status": error.code,
                           "version": error.headers.get("X-Chestnut-Version", "missing")})
            error.close()
            return {"ready": False, "reason": "http_error", "checks": checks}
        except (URLError, OSError, ValueError, TypeError, AttributeError) as error:
            return {"ready": False, "reason": type(error).__name__, "checks": checks}
    return {"ready": True, "reason": "expected_release_serving_all_routes", "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--expected-version", default=json.loads((ROOT / "VERSION.json").read_text())["version"])
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--interval", type=int, default=20)
    args = parser.parse_args()
    parsed = urlsplit(args.origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path.strip("/") or parsed.query or parsed.fragment:
        parser.error("--origin must be an HTTPS origin without credentials or a path")
    if not 1 <= args.attempts <= 30 or not 1 <= args.interval <= 60:
        parser.error("--attempts must be 1..30 and --interval 1..60")
    for attempt in range(1, args.attempts + 1):
        result = check_once(args.origin, args.expected_version)
        print(json.dumps({"attempt": attempt, "expected_version": args.expected_version, **result}), flush=True)
        if result["ready"]:
            return 0
        if attempt < args.attempts:
            time.sleep(args.interval)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
