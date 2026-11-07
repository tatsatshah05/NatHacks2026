"""`make dev`: run the API (:8000) and web app (:3000) together, with no API keys.

Keys are removed from the environment and DATA_MODE is set to replay, so sources
read the recorded fixtures in cloud/tests/fixtures/ instead of the network.
Press Ctrl+C to stop both.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = (
    "SOCRATA_APP_TOKEN",
    "ALBERTA_511_KEY",
    "VAPID_PRIVATE_KEY",
    "ADMIN_KEY",
    "WORKER_KEY",
)
LOCAL_DEFAULTS = {
    "DATABASE_URL": "sqlite:///./openpath.db",
    "CORS_ORIGINS": "http://localhost:3000",
    "DATA_MODE": "replay",
    "REPLAY_SNAPSHOT": "cloud/tests/fixtures",
    "NEXT_PUBLIC_API_URL": "http://localhost:8000",
    "NEXT_PUBLIC_WS_URL": "ws://localhost:8000/ws",
}


def main() -> int:
    npm = shutil.which("npm")
    if npm is None:
        print(
            "npm not found. Install Node.js LTS, then run `make setup`.",
            file=sys.stderr,
        )
        return 1
    if not (ROOT / "web" / "node_modules").is_dir():
        print("web/node_modules is missing. Run `make setup` first.", file=sys.stderr)
        return 1

    env = {k: v for k, v in os.environ.items() if k not in KEYS}
    env.update(LOCAL_DEFAULTS)

    api_cmd = [sys.executable, "-m", "uvicorn", "cloud.app.main:app", "--reload"]
    api_cmd += ["--reload-dir", "cloud", "--port", "8000"]
    api = subprocess.Popen(api_cmd, cwd=ROOT, env=env)
    web = subprocess.Popen(
        [npm, "run", "dev", "--", "--port", "3000"], cwd=ROOT / "web", env=env
    )
    print(
        "API: http://localhost:8000/docs   Web: http://localhost:3000   (Ctrl+C stops both)"
    )

    try:
        while api.poll() is None and web.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (api, web):
            if proc.poll() is None:
                proc.terminate()
        for proc in (api, web):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
