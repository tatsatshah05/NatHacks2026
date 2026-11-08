"""OP-102: scripts/dev.py, which `make dev` and `make web` run."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


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


def test_local_env_removes_keys_and_sets_replay() -> None:
    env = dev.local_env(
        {
            "SOCRATA_APP_TOKEN": "secret",
            "WORKER_KEY": "k",
            "PATH": "p",
            "DATA_MODE": "live",
        }
    )
    assert "SOCRATA_APP_TOKEN" not in env
    assert "WORKER_KEY" not in env
    assert env["PATH"] == "p"
    assert env["DATA_MODE"] == "replay"
    assert env["REPLAY_SNAPSHOT"] == "cloud/tests/fixtures"
    assert (ROOT / env["REPLAY_SNAPSHOT"]).is_dir()


@pytest.mark.parametrize(("code", "status"), [(3, 3), (1, 1), (0, 1), (-15, 143)])
def test_exit_status(code: int, status: int) -> None:
    assert dev.exit_status(code) == status


def test_a_stopped_process_sets_the_exit_code_and_stops_the_rest() -> None:
    """For example, the API cannot bind port 8000: make dev must fail, not exit 0."""
    failing = dev.start([sys.executable, "-c", "raise SystemExit(3)"], ROOT)
    sleeper = dev.start([sys.executable, "-c", "import time; time.sleep(60)"], ROOT)
    assert dev.supervise({"API": failing, "Web app": sleeper}, poll_s=0.05) == 3
    assert sleeper.poll() is not None


def test_unknown_part_is_a_usage_error() -> None:
    assert dev.main(["worker"]) == 2


def test_make_web_fails_without_the_web_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dev, "WEB", tmp_path / "web")
    assert dev.main(["web"]) == 1
