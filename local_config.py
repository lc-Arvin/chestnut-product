"""Read local Bailian credentials without executing shell code or changing other settings."""
import os
from pathlib import Path
import shlex


def load_bailian_credentials(path=None, environ=None):
    environ = os.environ if environ is None else environ
    path = Path(path) if path is not None else Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw = line.partition("=")
        key = key.strip()
        if not separator or key not in {"DASHSCOPE_API_KEY", "BAILIAN_API_HOST"} or environ.get(key):
            continue
        try:
            values = shlex.split(raw, comments=True, posix=True)
            if len(values) > 1:
                raise ValueError()
        except ValueError:
            # Never include credential values in exceptions or logs.
            raise ValueError(f"Invalid credential format in .env at line {number}") from None
        if values:
            environ[key] = values[0].strip()
