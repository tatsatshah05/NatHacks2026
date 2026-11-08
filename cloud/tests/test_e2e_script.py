"""OP-406: scripts/e2e.py, which `make e2e` runs (the servers' environment and waits)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_e2e() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "openpath_e2e", ROOT / "scripts" / "e2e.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e2e = load_e2e()


def test_env_is_replay_with_test_keys_and_matching_urls(tmp_path: Path) -> None:
    env = e2e.e2e_env(
        {"SOCRATA_APP_TOKEN": "secret", "ADMIN_KEY": "real", "PATH": "p"},
        8741,
        3141,
        tmp_path / "e2e.db",
    )
    assert "SOCRATA_APP_TOKEN" not in env  # dev.local_env removed the real keys
    assert env["PATH"] == "p"
    assert env["DATA_MODE"] == "replay"
    assert env["REPLAY_SNAPSHOT"] == "cloud/tests/fixtures"
    assert env["SCHEDULER"] == "on"  # the fixtures must load
    assert env["ADMIN_KEY"] == env["E2E_ADMIN_KEY"] == "e2e"  # a test value, not "real"
    assert env["WORKER_KEY"] == "e2e"
    assert env["DATABASE_URL"] == f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}"
    assert env["E2E_API_URL"] == "http://localhost:8741"
    assert env["E2E_WEB_URL"] == env["CORS_ORIGINS"] == "http://localhost:3141"
    assert env["NEXT_PUBLIC_API_URL"] == env["NEXT_PUBLIC_API_BASE_URL"]
    assert env["NEXT_PUBLIC_API_URL"] == "http://localhost:8741"
    assert env["NEXT_PUBLIC_WS_URL"] == "ws://localhost:8741/ws"


def test_readiness_checks() -> None:
    assert e2e.api_ready((200, '{"status": "ok", "mode": "replay"}'))
    assert not e2e.api_ready((200, '{"status": "starting"}'))
    assert not e2e.api_ready((200, "not json"))
    assert not e2e.api_ready((503, ""))
    assert not e2e.api_ready(None)
    assert e2e.web_ready((200, "<html>"))
    assert not e2e.web_ready((500, ""))
    assert not e2e.web_ready(None)


def test_wait_for_polls_until_ready() -> None:
    answers = iter([None, (503, ""), (200, '{"status": "ok"}')])
    slept: list[float] = []
    ok = e2e.wait_for(
        "API",
        "http://localhost:1/health",
        e2e.api_ready,
        alive=lambda: True,
        timeout_s=10,
        poll_s=0.01,
        get=lambda _url: next(answers),
        sleep=slept.append,
    )
    assert ok and slept == [0.01, 0.01]


def test_wait_for_gives_up_when_the_process_dies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ok = e2e.wait_for(
        "Web app",
        "http://localhost:1/",
        e2e.web_ready,
        alive=lambda: False,
        get=lambda _url: None,
        sleep=lambda _s: None,
    )
    assert not ok
    assert "stopped before it was ready" in capsys.readouterr().err


def test_wait_for_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(e2e.time, "monotonic", lambda: clock[0])

    def tick(_s: float) -> None:
        clock[0] += 1.0

    ok = e2e.wait_for(
        "API",
        "http://localhost:1/health",
        e2e.api_ready,
        alive=lambda: True,
        timeout_s=3,
        get=lambda _url: None,
        sleep=tick,
    )
    assert not ok


def test_arguments() -> None:
    args = e2e.parse_args(
        ["--skip-build", "--api-port", "8800", "--", "--grep", "@smoke"]
    )
    assert args.skip_build and args.api_port == 8800
    assert args.web_port == e2e.DEFAULT_WEB_PORT
    assert args.playwright_args == ["--grep", "@smoke"]


def test_without_the_web_app_it_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(e2e.dev, "WEB", tmp_path / "web")
    assert e2e.main([]) == 1
