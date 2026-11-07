"""`make dev` and `make web`: run OpenPath locally, with no API keys.

    python scripts/dev.py        # API on :8000 and web app on :3000 (`make dev`)
    python scripts/dev.py web    # only the web app on :3000 (`make web`)
    python scripts/dev.py api    # only the API on :8000

API keys are removed from the environment and DATA_MODE is set to replay, so
sources read the recorded fixtures in cloud/tests/fixtures/ instead of the
network, and /health reports "mode": "replay". Nothing here reads a .env file.

If one process stops on its own (for example because its port is already in use),
the others are stopped too, and this script exits with that process's exit code.
Ctrl+C stops everything and exits with 0.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
API_PORT = 8000
WEB_PORT = 3000

# Removed from the environment, so nothing started here can call a live source.
KEYS = (
    "SOCRATA_APP_TOKEN",
    "ALBERTA_511_KEY",
    "VAPID_PRIVATE_KEY",
    "ADMIN_KEY",
    "WORKER_KEY",
)
LOCAL_ENV = {
    "DATABASE_URL": "sqlite:///./openpath.db",
    "CORS_ORIGINS": f"http://localhost:{WEB_PORT}",
    "DATA_MODE": "replay",
    # A folder, not a file: one subfolder of recorded responses per source.
    "REPLAY_SNAPSHOT": "cloud/tests/fixtures",
    "NEXT_PUBLIC_API_URL": f"http://localhost:{API_PORT}",
    # The OP-113 web app (#94) reads NEXT_PUBLIC_API_BASE_URL, while AGENTS.md names
    # the variable NEXT_PUBLIC_API_URL. Both are set until the two agree.
    "NEXT_PUBLIC_API_BASE_URL": f"http://localhost:{API_PORT}",
    "NEXT_PUBLIC_WS_URL": f"ws://localhost:{API_PORT}/ws",
}
PARTS = ("api", "web")


def local_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The child processes' environment: no keys, with the local settings on top."""
    env = {name: value for name, value in environ.items() if name not in KEYS}
    env.update(LOCAL_ENV)
    return env


def start(
    cmd: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None
) -> subprocess.Popen[bytes]:
    """Start a process in its own process group, so it and its children stop together.

    Ctrl+C then reaches only this script, which stops each group itself.
    """
    if os.name == "nt":
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, process_group=0
    )


def stop(proc: subprocess.Popen[bytes], timeout_s: float = 10) -> None:
    """Stop a process and everything it started (uvicorn's worker, npm's node)."""
    if os.name == "nt":
        if proc.poll() is None:
            # terminate() would end only npm's cmd.exe and leave node holding the port.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # the whole group has already exited
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait()


def exit_status(code: int) -> int:
    """This script's exit status when a child stopped on its own with ``code``.

    A negative code means a POSIX signal ended the child, reported the way shells do
    (128 + the signal number). Exit code 0 still counts as a failure, because these
    servers run until they are stopped.
    """
    if code < 0:
        return 128 - code
    return code or 1


def supervise(procs: Mapping[str, subprocess.Popen[bytes]], poll_s: float = 0.5) -> int:
    """Wait for Ctrl+C or for one process to stop, then stop the rest.

    Returns 0 after Ctrl+C, otherwise ``exit_status`` of the process that stopped.
    """
    try:
        while True:
            for name, proc in procs.items():
                code = proc.poll()
                if code is not None:
                    print(
                        f"{name} stopped with exit code {code}, so everything stops.",
                        file=sys.stderr,
                    )
                    return exit_status(code)
            time.sleep(poll_s)
    except KeyboardInterrupt:
        return 0
    finally:
        for proc in procs.values():
            stop(proc)


def web_problem() -> str | None:
    """Why the web app cannot start, or None when it can."""
    if not (WEB / "package.json").is_file():
        return "web/ is not in this checkout yet (OP-113 adds the web app)."
    if shutil.which("npm") is None:
        return "npm was not found. Install Node.js LTS, then run `make setup`."
    if not (WEB / "node_modules").is_dir():
        return "web/node_modules is missing. Run `make setup` first."
    return None


def main(argv: Sequence[str]) -> int:
    parts = list(argv) or list(PARTS)
    if any(part not in PARTS for part in parts):
        print("usage: dev.py [api] [web]", file=sys.stderr)
        return 2

    commands: dict[str, tuple[list[str], Path]] = {}
    urls: list[str] = []
    if "api" in parts:
        api_cmd = [sys.executable, "-m", "uvicorn", "cloud.app.main:app", "--reload"]
        api_cmd += ["--reload-dir", "cloud", "--port", str(API_PORT)]
        commands["API"] = (api_cmd, ROOT)
        urls.append(f"API: http://localhost:{API_PORT}/docs")
    if "web" in parts:
        problem = web_problem()
        if problem is None:
            npm = shutil.which("npm")
            assert npm is not None  # checked by web_problem()
            web_cmd = [npm, "run", "dev", "--", "--port", str(WEB_PORT)]
            commands["Web app"] = (web_cmd, WEB)
            urls.append(f"Web: http://localhost:{WEB_PORT}")
        elif not argv and not (WEB / "package.json").is_file():
            # Plain `make dev` before the web app exists: run what there is.
            print(f"{problem} Starting the API only.", file=sys.stderr)
        else:
            print(problem, file=sys.stderr)
            return 1

    env = local_env(os.environ)
    procs: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for name, (cmd, cwd) in commands.items():
            procs[name] = start(cmd, cwd, env)
    except OSError as error:
        print(f"Could not start {cmd[0]}: {error}", file=sys.stderr)
        for proc in procs.values():
            stop(proc)
        return 1
    print("   ".join(urls) + "   (Ctrl+C stops everything)")
    return supervise(procs)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
