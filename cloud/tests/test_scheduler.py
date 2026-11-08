"""OP-103: the scheduler fetches each source on its cadence, keeps data on failure and reports freshness."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cloud.app.scheduler import Scheduler, Source, SourceStatus

NOW = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)


def lrt_row(ref: str = "1", **overrides: Any) -> dict[str, Any]:
    """A dict row in the contract shape, as the adapters produce."""
    row = {
        "source": "city_lrt_devices",
        "source_ref": ref,
        "category": "indoor",
        "kind": "elevator_out",
        "effect": "blocked",
        "title": "Elevator out of service",
        "detail": "Central Station, West Platform",
        "geometry": {"type": "Point", "coordinates": [-113.49, 53.544]},
        "confidence": "high",
        "observed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
        "url": "https://data.edmonton.ca/d/snws-u3zx",
        "attribution": "City of Edmonton Open Data",
    }
    row.update(overrides)
    return row


class Scripted:
    """A fake source whose fetch returns the next scripted outcome each call."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def fetch(self, now: datetime) -> Any:
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else []
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return await outcome()
        return outcome


def make(store, fetch: Callable, **kw: Any) -> tuple[Scheduler, Source]:
    source = Source(
        name=kw.pop("name", "city_lrt_devices"),
        cadence_s=kw.pop("cadence_s", 60),
        fetch=fetch,
        **kw,
    )
    scheduler = Scheduler(store, [source], clock=lambda: NOW, backoff_s=(0,))
    return scheduler, source


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------- run_once


def test_success_stores_items_and_reports_fresh(store) -> None:
    scripted = Scripted([lrt_row("1"), lrt_row("2")])
    scheduler, source = make(store, scripted.fetch)
    assert run(scheduler.run_once(source)) is True
    assert {i.source_ref for i in store.active(NOW)} == {"1", "2"}
    status = scheduler.status("city_lrt_devices")
    assert status.last_success == NOW and status.last_error is None
    assert (
        status.item_count == 2
        and status.stale is False
        and status.consecutive_failures == 0
    )


def test_failure_keeps_items_and_records_error(store, caplog) -> None:
    scripted = Scripted([lrt_row("1")], RuntimeError("City API down"))
    scheduler, source = make(store, scripted.fetch, retries=0)
    run(scheduler.run_once(source))
    with caplog.at_level(logging.WARNING, logger="openpath.scheduler"):
        assert run(scheduler.run_once(source)) is False
    assert [i.source_ref for i in store.active(NOW)] == [
        "1"
    ]  # a failed source never clears data
    status = scheduler.status("city_lrt_devices")
    assert status.last_error == "RuntimeError: City API down"
    assert status.consecutive_failures == 1 and status.stale is False
    assert (
        "SOURCE city_lrt_devices failed (1/3): RuntimeError: City API down"
        in caplog.text
    )


def test_retries_then_succeeds(store) -> None:
    scripted = Scripted(RuntimeError("1"), RuntimeError("2"), [lrt_row("1")])
    scheduler, source = make(store, scripted.fetch, retries=3)
    assert run(scheduler.run_once(source)) is True
    assert scripted.calls == 3
    assert scheduler.status("city_lrt_devices").consecutive_failures == 0


def test_stale_after_three_failures_and_recovery_logs(store, caplog) -> None:
    scripted = Scripted(
        [lrt_row("1")],
        RuntimeError("a"),
        RuntimeError("b"),
        RuntimeError("c"),
        [lrt_row("1")],
    )
    scheduler, source = make(store, scripted.fetch, retries=0)
    with caplog.at_level(logging.INFO, logger="openpath.scheduler"):
        for expected in (True, False, False, False):
            assert run(scheduler.run_once(source)) is expected
        status = scheduler.status("city_lrt_devices")
        assert status.stale is True and status.consecutive_failures == 3
        assert "SOURCE city_lrt_devices stale" in caplog.text
        assert run(scheduler.run_once(source)) is True
    status = scheduler.status("city_lrt_devices")
    assert status.stale is False and status.last_error is None
    assert "SOURCE city_lrt_devices recovered" in caplog.text
    assert [i.source_ref for i in store.active(NOW)] == ["1"]  # kept throughout


def test_timeout_counts_as_a_failure(store) -> None:
    async def slow() -> list:
        await asyncio.sleep(0.2)
        return []

    scheduler, source = make(store, Scripted(slow).fetch, retries=0, timeout_s=0.01)
    assert run(scheduler.run_once(source)) is False
    assert "timed out after 0.01 s" in scheduler.status("city_lrt_devices").last_error


def test_invalid_and_foreign_items_are_skipped_not_fatal(
    store, make_obstruction
) -> None:
    good = make_obstruction(source_ref="ok")  # a ready-made Obstruction also works
    bad = lrt_row("bad", kind="pothole")  # not in the Kind enum
    foreign = lrt_row("x", source="city_trail_closures")  # belongs to another source
    scheduler, source = make(store, Scripted([good, bad, foreign]).fetch)
    assert run(scheduler.run_once(source)) is True
    assert [i.source_ref for i in store.active(NOW)] == ["ok"]
    status = scheduler.status("city_lrt_devices")
    assert status.item_count == 1 and status.skipped_items == 2


def test_items_no_longer_returned_are_removed(store) -> None:
    scripted = Scripted([lrt_row("1"), lrt_row("2")], [lrt_row("2")])
    scheduler, source = make(store, scripted.fetch)
    run(scheduler.run_once(source))
    run(scheduler.run_once(source))
    assert [i.source_ref for i in store.active(NOW)] == ["2"]


def test_status_only_source_does_not_touch_the_store(store) -> None:
    scheduler, source = make(
        store, Scripted(None).fetch, name="weather_watch", provides_items=False
    )
    assert run(scheduler.run_once(source)) is True
    assert store.active(NOW) == []
    status = scheduler.status("weather_watch")
    assert (
        status.last_success == NOW and status.item_count == 0 and status.stale is False
    )


def test_statuses_report_age(store) -> None:
    scripted = Scripted([lrt_row("1")])
    source = Source(name="city_lrt_devices", cadence_s=60, fetch=scripted.fetch)
    clock_value = [NOW]
    scheduler = Scheduler(store, [source], clock=lambda: clock_value[0], backoff_s=(0,))
    run(scheduler.run_once(source))
    clock_value[0] = NOW + timedelta(seconds=90)
    (status,) = scheduler.statuses()
    assert isinstance(status, SourceStatus) and status.age_s == 90.0


def test_duplicate_source_names_are_rejected(store) -> None:
    fetch = Scripted([]).fetch
    with pytest.raises(ValueError, match="duplicate"):
        Scheduler(store, [Source("a", 1, fetch), Source("a", 1, fetch)])


# --------------------------------------------------------------- the loops


def test_start_runs_sources_on_their_cadence_and_purges(store) -> None:
    async def scenario() -> tuple[int, list[str]]:
        scripted = Scripted(
            *(
                [[lrt_row("1", expires_at=(NOW + timedelta(seconds=1)).isoformat())]]
                * 50
            )
        )
        source = Source(name="city_lrt_devices", cadence_s=0.01, fetch=scripted.fetch)
        clock_value = [NOW]
        scheduler = Scheduler(
            store,
            [source],
            clock=lambda: clock_value[0],
            purge_every_s=0.01,
            backoff_s=(0,),
        )
        await scheduler.start()
        await asyncio.sleep(0.08)
        calls = scripted.calls
        await scheduler.stop()
        assert scheduler.running is False
        assert [i.source_ref for i in store.active(NOW)] == ["1"]  # the loop stored it
        clock_value[0] = NOW + timedelta(hours=1)  # now everything has expired
        await scheduler.purge_once()
        remaining = [i.source_ref for i in store.active(NOW)]
        return calls, remaining

    calls, remaining = run(scenario())
    assert calls >= 3  # the loop ran repeatedly
    assert remaining == []  # the purge deleted the expired item


def test_a_bad_return_value_is_a_failure_and_the_loop_keeps_running(store) -> None:
    async def scenario() -> tuple[int, str | None]:
        class Weird:
            calls = 0

            async def fetch(self, now: datetime) -> Any:
                self.calls += 1
                return 42  # not iterable: a programming error in the adapter

        weird = Weird()
        source = Source("city_lrt_devices", 0.01, weird.fetch, retries=0)
        scheduler = Scheduler(store, [source], clock=lambda: NOW, backoff_s=(0,))
        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()
        return weird.calls, scheduler.status("city_lrt_devices").last_error

    calls, error = run(scenario())
    assert calls >= 2  # the loop kept going
    assert error is not None and error.startswith("store: TypeError")


def test_many_sources_can_write_at_the_same_time(store) -> None:
    """Writes from several worker threads are serialised, so SQLite never sees two at once."""

    async def scenario() -> list[bool]:
        sources = []
        for n in range(8):
            name = f"src_{n}"
            rows = [lrt_row(str(i), source=name) for i in range(25)]
            sources.append(Source(name=name, cadence_s=60, fetch=Scripted(rows).fetch))
        scheduler = Scheduler(store, sources, clock=lambda: NOW, backoff_s=(0,))
        return await asyncio.gather(*(scheduler.run_once(s) for s in sources))

    assert all(run(scenario()))
    assert len(store.active(NOW)) == 8 * 25


def test_a_store_failure_is_reported_as_a_source_failure(store) -> None:
    class Broken:
        def replace_source(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("disk full")

        def purge_expired(self, now: datetime) -> list[str]:
            return []

    scheduler = Scheduler(
        Broken(),
        [Source("city_lrt_devices", 60, Scripted([lrt_row("1")]).fetch)],
        clock=lambda: NOW,
        backoff_s=(0,),
    )  # type: ignore[arg-type]
    assert run(scheduler.run_once(scheduler.sources[0])) is False
    assert (
        scheduler.status("city_lrt_devices").last_error
        == "store: RuntimeError: disk full"
    )
