"""`make e2e`: run the Playwright end-to-end tests against the app built here.

    python scripts/e2e.py                 # build the web app, start both, test, stop
    python scripts/e2e.py --skip-build    # reuse web/.next from an earlier build
    python scripts/e2e.py -- --grep @smoke   # arguments after -- go to Playwright

What happens, in order:

1. The API starts in replay mode on ``E2E_API_PORT`` (8740) with a fresh SQLite file
   in a temporary folder, ``ADMIN_KEY=e2e`` and ``WORKER_KEY=e2e`` (test values; the
   real keys are removed from the environment, as ``make dev`` does) and the
   scheduler on, so the recorded fixtures load.
2. ``next build`` runs with ``NEXT_PUBLIC_*`` pointing at that API (they are baked
   into the bundle), then ``next start`` serves it on ``E2E_WEB_PORT`` (3140).
3. Both are polled until ``/health`` and the home page answer.
4. ``npx playwright test`` runs in ``web/`` with ``E2E_WEB_URL``, ``E2E_API_URL`` and
   ``E2E_ADMIN_KEY`` set. Its exit code is this script's exit code.
5. Both servers stop, whatever happened, and the temporary folder is removed.

The process handling (``start``, ``stop``, ``local_env``) comes from ``dev.py``, the
``make dev`` runner, so there is one way to run the app's servers.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DEFAULT_API_PORT = 8740
DEFAULT_WEB_PORT = 3140
ADMIN_KEY = "e2e"  # a test value, never a real key
WORKER_KEY = "e2e"
START_TIMEOUT_S = 180.0  # next start on a cold CI runner can take a while


def load_dev() -> ModuleType:
    """Import scripts/dev.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "openpath_dev", ROOT / "scripts" / "dev.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dev = load_dev()


def e2e_env(
    environ: Mapping[str, str], api_port: int, web_port: int, db_path: Path
) -> dict[str, str]:
    """The servers' environment: ``make dev``'s replay settings, then the test values.

    ``dev.local_env`` removes every real key and sets replay mode; on top of it the
    database is the temporary file, the admin and worker keys are test values, CORS
    allows the web port, and the ``NEXT_PUBLIC_*`` and ``E2E_*`` variables point at
    the two ports.
    """
    env = dev.local_env(environ)
    api = f"http://localhost:{api_port}"
    web = f"http://localhost:{web_port}"
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "ADMIN_KEY": ADMIN_KEY,
            "WORKER_KEY": WORKER_KEY,
            "SCHEDULER": "on",
            "CORS_ORIGINS": web,
            "NEXT_PUBLIC_API_URL": api,
            "NEXT_PUBLIC_API_BASE_URL": api,
            "NEXT_PUBLIC_WS_URL": f"ws://localhost:{api_port}/ws",
            "E2E_WEB_URL": web,
            "E2E_API_URL": api,
            "E2E_ADMIN_KEY": ADMIN_KEY,
            # Nothing here is a real deploy; Next's telemetry has no place in a test.
            "NEXT_TELEMETRY_DISABLED": "1",
        }
    )
    return env


def fetch(url: str, timeout_s: float = 5.0) -> tuple[int, str] | None:
    """``(status, body)`` for a GET, or None when nothing answered."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (urllib.error.URLError, OSError, ValueError):
        return None


def api_ready(result: tuple[int, str] | None) -> bool:
    """``/health`` answered 200 with ``status: ok``."""
    if result is None or result[0] != 200:
        return False
    try:
        return json.loads(result[1]).get("status") == "ok"
    except ValueError:
        return False


def web_ready(result: tuple[int, str] | None) -> bool:
    return result is not None and result[0] == 200


def wait_for(
    name: str,
    url: str,
    ready: Callable[[tuple[int, str] | None], bool],
    alive: Callable[[], bool],
    timeout_s: float = START_TIMEOUT_S,
    poll_s: float = 0.5,
    get: Callable[[str], tuple[int, str] | None] = fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll ``url`` until ``ready`` says so. False if ``alive`` turns false or time runs out."""
    deadline = time.monotonic() + timeout_s
    while True:
        if not alive():
            print(f"{name} stopped before it was ready.", file=sys.stderr)
            return False
        if ready(get(url)):
            return True
        if time.monotonic() >= deadline:
            print(
                f"{name} did not answer at {url} within {timeout_s:.0f} s.",
                file=sys.stderr,
            )
            return False
        sleep(poll_s)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Playwright end-to-end tests against the API and web app started here."
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="reuse web/.next from an earlier build",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=int(os.environ.get("E2E_API_PORT", DEFAULT_API_PORT)),
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=int(os.environ.get("E2E_WEB_PORT", DEFAULT_WEB_PORT)),
    )
    parser.add_argument(
        "playwright_args", nargs="*", help="arguments for `playwright test`, after --"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    problem = dev.web_problem()
    if problem is not None:
        print(problem, file=sys.stderr)
        return 1
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    assert npm is not None and npx is not None  # checked by web_problem()

    temp = Path(tempfile.mkdtemp(prefix="openpath-e2e-"))
    env = e2e_env(os.environ, args.api_port, args.web_port, temp / "e2e.db")
    procs: dict[str, subprocess.Popen[bytes]] = {}
    try:
        api_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "cloud.app.main:app",
            "--port",
            str(args.api_port),
        ]
        procs["API"] = dev.start(api_cmd, ROOT, env)

        if not args.skip_build:
            print("Building the web app (next build).")
            built = subprocess.run([npm, "run", "build"], cwd=WEB, env=env, check=False)
            if built.returncode != 0:
                print("next build failed.", file=sys.stderr)
                return built.returncode or 1
        elif not (WEB / ".next" / "BUILD_ID").is_file():
            print("--skip-build needs an earlier build in web/.next.", file=sys.stderr)
            return 1

        web_cmd = [npm, "run", "start", "--", "--port", str(args.web_port)]
        procs["Web app"] = dev.start(web_cmd, WEB, env)

        api = procs["API"]
        web = procs["Web app"]
        if not wait_for(
            "API", f"{env['E2E_API_URL']}/health", api_ready, lambda: api.poll() is None
        ):
            return 1
        if not wait_for(
            "Web app", env["E2E_WEB_URL"], web_ready, lambda: web.poll() is None
        ):
            return 1
        print(
            f"API: {env['E2E_API_URL']}   Web: {env['E2E_WEB_URL']}   Running Playwright."
        )
        test = subprocess.run(
            [npx, "playwright", "test", *args.playwright_args],
            cwd=WEB,
            env=env,
            check=False,
        )
        return test.returncode
    except KeyboardInterrupt:
        return 130
    finally:
        for proc in procs.values():
            dev.stop(proc)
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
