"""Run local administration and translation using local Bailian credentials."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_config import load_local_configuration

load_local_configuration(os.environ.get("CHESTNUT_ENV_FILE", ROOT / ".env"))
os.environ["CHESTNUT_ENV"] = "local"
os.environ["CHESTNUT_DATABASE_BACKEND"] = "sqlite"
os.environ["CHESTNUT_TRANSCRIPT_STORAGE"] = "local"
os.environ["CHESTNUT_COS_BUCKET"] = ""
os.environ["CHESTNUT_ADMIN_ENABLED"] = "1"
os.environ["CHESTNUT_HOST"] = "127.0.0.1"
os.environ["PORT"] = os.environ.get("CHESTNUT_LOCAL_ADMIN_PORT", os.environ.get("PORT", os.environ.get("CHESTNUT_PORT", "8080")))

if __name__ == "__main__":
    import server
    print(f"Local admin: http://127.0.0.1:{server.PORT}/admin", flush=True)
    print(f"Invitation login limit: {server.LOGIN_RATE_LIMIT} attempts / {server.LOGIN_RATE_WINDOW_SECONDS} seconds", flush=True)
    print("First visit: set your own administrator password. Data persists in data/admin.sqlite3.", flush=True)
    server.main()
