"""Shutdown order: every thread that uses the store stops before the engine closes.

CI crashed with a segmentation fault when the lifespan disposed the in-memory
SQLite engine (one shared connection) while a scheduler worker thread was still
writing on it. These tests hold a store write open across shutdown and check that
the scheduler waits for it, and that no store call starts after ``dispose()``.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from cloud.app import main
from cloud.app.camera_readings import CameraReadingStore
from cloud.app.main import create_app, stop_store_users
from cloud.app.scheduler import Scheduler
from cloud.app.store import ObstructionStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SLOW_WRITE_S = 0.3


@pytest.fixture(autouse=True)
def replay_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``make dev`` settings: recorded fixtures, the scheduler on, no keys."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(FIXTURES))
    monkeypatch.setenv("SCHEDULER", "on")
    for name in ("REPLAY_AT", "REPLAY_CLOCK", "WEATHER_REPLAY", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)


class Timeline:
    """What happened during one app run, in order, from any thread."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.late: list[str] = []  # store calls that started after dispose()
        self.disposed = threading.Event()
        self.write_started = threading.Event()
        self._lock = threading.Lock()

    def add(self, event: str) -> None:
        with self._lock:
            self.events.append(event)

    def index(self, event: str) -> int:
        with self._lock:
            return self.events.index(event)

    def matching(self, prefix: str) -> list[int]:
        with self._lock:
            return [i for i, e in enumerate(self.events) if e.startswith(prefix)]


def spy_on_store_calls(
    monkeypatch: pytest.MonkeyPatch, timeline: Timeline, cls: type, names: list[str]
) -> None:
    """Record any of ``cls``'s methods that starts after the engine was disposed."""

    def wrap(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
            if timeline.disposed.is_set():
                timeline.late.append(f"{cls.__name__}.{name}")
            return original(self, *args, **kwargs)

        return spy

    for name in names:
        monkeypatch.setattr(cls, name, wrap(name, getattr(cls, name)))


@pytest.fixture
def timeline(monkeypatch: pytest.MonkeyPatch) -> Timeline:
    """Instrument the app: slow store writes, ``Scheduler.stop`` and ``dispose``."""
    timeline = Timeline()
    spy_on_store_calls(
        monkeypatch,
        timeline,
        ObstructionStore,
        ["upsert", "replace_source", "purge_expired", "active", "get"],
    )
    spy_on_store_calls(
        monkeypatch, timeline, CameraReadingStore, ["upsert", "latest", "purge_expired"]
    )

    replace_source = ObstructionStore.replace_source

    def slow_replace_source(self: ObstructionStore, source: str, *args: Any) -> Any:
        timeline.add(f"write started {source}")
        timeline.write_started.set()
        time.sleep(SLOW_WRITE_S)  # a slow CI runner: still writing at shutdown
        result = replace_source(self, source, *args)
        timeline.add(f"write finished {source}")
        return result

    monkeypatch.setattr(ObstructionStore, "replace_source", slow_replace_source)

    stop = Scheduler.stop

    async def recorded_stop(self: Scheduler, *args: Any, **kwargs: Any) -> bool:
        result = await stop(self, *args, **kwargs)
        timeline.add("scheduler stopped")
        return result

    monkeypatch.setattr(Scheduler, "stop", recorded_stop)

    dispose = Engine.dispose

    def recorded_dispose(self: Engine, *args: Any, **kwargs: Any) -> None:
        timeline.add("dispose")
        timeline.disposed.set()
        dispose(self, *args, **kwargs)

    monkeypatch.setattr(Engine, "dispose", recorded_dispose)
    return timeline


def test_shutdown_waits_for_a_running_store_write_before_closing_the_database(
    timeline: Timeline,
) -> None:
    with TestClient(create_app()):
        # Exit as soon as one source is inside a store write.
        assert timeline.write_started.wait(10), "no source wrote to the store"

    started = timeline.matching("write started")
    finished = timeline.matching("write finished")
    stopped = timeline.index("scheduler stopped")
    disposed = timeline.index("dispose")
    # Scheduler.stop() returned only after every write that had started finished...
    assert len(finished) == len(started) >= 1
    assert max(finished) < stopped < disposed
    # ...and nothing touched the store once the engine was disposed.
    assert timeline.late == []


def test_the_app_stops_and_starts_again_cleanly(timeline: Timeline) -> None:
    """Two runs back to back (as the test suite does) each shut down in order."""
    for _ in range(2):
        timeline.write_started.clear()
        timeline.disposed.clear()
        with TestClient(create_app()) as client:
            assert client.get("/health").status_code == 200
            assert timeline.write_started.wait(10)
        assert timeline.late == []
    assert len(timeline.matching("dispose")) == 2


# ------------------------------------------------------------ the shutdown order


class Recorder:
    """Fakes of every store user; ``fail`` names the one whose stop raises."""

    def __init__(self, fail: str | None = None, busy: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail, self.busy = fail, busy

    def _stop(self, name: str) -> bool:
        self.calls.append(name)
        if name == self.fail:
            raise RuntimeError(f"{name} broke")
        return name != self.busy

    def app(self) -> tuple[FastAPI, Any]:
        recorder = self

        class FakeScheduler:
            async def stop(self) -> bool:
                return recorder._stop("scheduler")

        class FakeWatcher:
            def stop(self) -> bool:
                return recorder._stop("watcher")

        class FakeHub:
            async def stop(self) -> bool:
                return recorder._stop("hub")

        class FakeProvider:
            def stop(self) -> bool:
                return recorder._stop("provider")

        app = FastAPI()
        app.state.push = SimpleNamespace(watcher=FakeWatcher())
        app.state.live = FakeHub()
        app.state.hazard_provider = FakeProvider()
        return app, FakeScheduler()


ORDER = ["scheduler", "watcher", "hub", "provider"]


def test_store_users_stop_in_order() -> None:
    recorder = Recorder()
    app, scheduler = recorder.app()
    assert asyncio.run(stop_store_users(app, scheduler)) is True
    assert recorder.calls == ORDER


@pytest.mark.parametrize("name", ORDER)
def test_one_failing_stop_never_skips_the_others(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = Recorder(fail=name)
    app, scheduler = recorder.app()
    assert asyncio.run(stop_store_users(app, scheduler)) is False
    assert recorder.calls == ORDER
    assert f"{name} broke" in caplog.text


def test_a_busy_store_user_keeps_the_engine_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Closing the connection under a running call is the crash; never do that."""
    monkeypatch.setenv("SCHEDULER", "off")

    async def busy(app: FastAPI, scheduler: Scheduler | None) -> bool:
        return False

    monkeypatch.setattr(main, "stop_store_users", busy)
    disposed: list[Engine] = []
    monkeypatch.setattr(Engine, "dispose", lambda self, *a, **k: disposed.append(self))
    with TestClient(create_app()):
        pass
    assert disposed == []
    assert "not disposing the engine" in caplog.text
