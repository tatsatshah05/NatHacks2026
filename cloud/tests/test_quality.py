"""OP-407: data quality checks.

The ``RowQuality`` helper the adapters call, the daily item counts with the drop
rule, how the scheduler copies both onto each source's status, and the additive
fields on ``GET /api/status``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cloud.app.adapters._quality import (
    UNEXPECTED_FIELDS_MAX,
    VALIDATION_ERROR_MAX_CHARS,
    QualityReport,
    RowQuality,
)
from cloud.app.main import create_app
from cloud.app.quality import (
    DROP_FRACTION,
    DROP_MIN_YESTERDAY,
    CountChange,
    DailyCounts,
    is_drop,
    utc_day,
)
from cloud.app.scheduler import Scheduler, Source

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
DAY = timedelta(days=1)
SOURCE = "city_lrt_devices"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def items(make_obstruction: Any, count: int) -> list[Any]:
    """``count`` items that are active at this file's NOW (conftest's is 3 days earlier)."""
    return [
        make_obstruction(
            source_ref=str(n), observed_at=NOW, expires_at=NOW + timedelta(hours=1)
        )
        for n in range(count)
    ]


def make(store: Any, fetch: Any, **kw: Any) -> tuple[Scheduler, Source]:
    source = Source(name=kw.pop("name", SOURCE), cadence_s=60, fetch=fetch, **kw)
    scheduler = Scheduler(store, [source], clock=lambda: NOW, backoff_s=(0,))
    return scheduler, source


def returning(result: Any) -> Any:
    async def fetch(now: datetime) -> Any:
        return result

    return fetch


# ------------------------------------------------------------------ RowQuality


def test_row_quality_counts_rejections_and_keeps_the_first_message() -> None:
    quality = RowQuality({"a"})
    assert quality.report() == QualityReport()
    quality.reject("first problem")
    quality.reject(ValueError("second problem"))
    report = quality.report()
    assert report.rejected_rows == 2
    assert report.last_validation_error == "first problem"
    quality.start()  # the next fetch starts clean
    assert quality.report() == QualityReport()


def test_row_quality_notes_unknown_fields_but_not_system_fields() -> None:
    quality = RowQuality({"a", "b"})
    quality.see({"a": 1, "b": 2, "c": 3, ":id": 4, ":@computed_region_x": 5})
    quality.see({"a": 1, "c": 3})  # seen twice, listed once
    assert quality.report().unexpected_fields == ("c",)


def test_unexpected_fields_are_sorted_and_capped() -> None:
    quality = RowQuality(set())
    names = [f"field_{n:02d}" for n in range(UNEXPECTED_FIELDS_MAX + 5)]
    quality.see(dict.fromkeys(reversed(names), 1))
    listed = quality.report().unexpected_fields
    assert len(listed) == UNEXPECTED_FIELDS_MAX == 20
    assert list(listed) == sorted(names)[:UNEXPECTED_FIELDS_MAX]


def test_validation_error_is_one_line_and_bounded() -> None:
    quality = RowQuality(set())
    quality.reject("first\nline " + "x" * 500)
    message = quality.report().last_validation_error
    assert message is not None and "\n" not in message
    assert len(message) <= VALIDATION_ERROR_MAX_CHARS == 200
    assert message.startswith("first line") and message.endswith("...")


# ------------------------------------------------------------------- the rule


@pytest.mark.parametrize(
    ("today", "yesterday", "drop"),
    [
        (5, None, False),  # no row for yesterday
        (40, 120, True),  # fell to a third
        (59, 120, True),  # just under half
        (60, 120, False),  # exactly half is not a drop
        (110, 120, False),  # a normal day
        (4, 9, False),  # yesterday too small to judge
        (5, 10, False),  # half of the minimum
        (4, 10, True),  # under half of the minimum
        (0, 10, True),  # an empty feed after a full one
    ],
)
def test_the_drop_rule(today: int, yesterday: int | None, drop: bool) -> None:
    assert is_drop(today, yesterday) is drop


def test_the_constants_are_what_agents_md_documents() -> None:
    assert DROP_MIN_YESTERDAY == 10 and DROP_FRACTION == 0.5


def test_the_day_is_utc() -> None:
    edmonton_evening = datetime(
        2026, 11, 10, 23, 30, tzinfo=timezone(-timedelta(hours=7))
    )
    assert utc_day(edmonton_evening) == date(2026, 11, 11)
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_day(datetime(2026, 11, 10))


# ---------------------------------------------------------------- DailyCounts


def test_the_first_day_has_no_yesterday(store: Any) -> None:
    counts = DailyCounts(store.engine)
    assert counts.record("s", 12, NOW) == CountChange(
        today=12, yesterday=None, drop=False
    )
    (row,) = counts.counts("s")
    assert (row.day, row.item_count) == (date(2026, 11, 10), 12)


def test_later_fetches_the_same_day_update_the_one_row(store: Any) -> None:
    counts = DailyCounts(store.engine)
    counts.record("s", 12, NOW)
    change = counts.record("s", 15, NOW + timedelta(hours=3))
    assert change.today == 15
    (row,) = counts.counts("s")
    assert row.item_count == 15 and row.updated_at.hour == 21


def test_a_normal_day_compares_with_yesterday(store: Any) -> None:
    counts = DailyCounts(store.engine)
    counts.record("s", 120, NOW - DAY)
    assert counts.record("s", 110, NOW) == CountChange(
        today=110, yesterday=120, drop=False
    )
    # Sources are independent.
    assert counts.record("other", 1, NOW).yesterday is None


def test_a_drop_day_is_flagged_and_logged_once(
    store: Any, caplog: pytest.LogCaptureFixture
) -> None:
    counts = DailyCounts(store.engine)
    counts.record("city_x", 120, NOW - DAY)
    with caplog.at_level(logging.WARNING, logger="openpath.quality"):
        first = counts.record("city_x", 40, NOW)
        second = counts.record("city_x", 41, NOW + timedelta(hours=1))
    assert first.drop is True and second.drop is True
    assert caplog.text.count("WARN quality city_x: items fell from 120 to 40") == 1
    assert "fell from 120 to 41" not in caplog.text  # once per day, not per fetch
    assert len(counts.counts("city_x")) == 2


def test_a_gap_day_means_no_yesterday(store: Any) -> None:
    counts = DailyCounts(store.engine)
    counts.record("s", 120, NOW - 2 * DAY)
    assert counts.record("s", 1, NOW).yesterday is None  # only the previous day counts


# ------------------------------------------------------------ the scheduler


def test_the_scheduler_records_the_daily_count(
    store: Any, make_obstruction: Any
) -> None:
    scheduler, source = make(store, returning(items(make_obstruction, 2)))
    assert run(scheduler.run_once(source)) is True
    status = scheduler.status(SOURCE)
    assert status.count_change == CountChange(today=2, yesterday=None, drop=False)
    assert status.rejected_rows == 0 and status.unexpected_fields == []
    assert status.last_validation_error is None


def test_a_drop_is_flagged_but_never_clears_data(
    store: Any, make_obstruction: Any, caplog: pytest.LogCaptureFixture
) -> None:
    scheduler, source = make(store, returning(items(make_obstruction, 2)))
    assert scheduler.counts is not None
    scheduler.counts.record(SOURCE, 120, NOW - DAY)
    with caplog.at_level(logging.WARNING, logger="openpath.quality"):
        assert run(scheduler.run_once(source)) is True
    assert scheduler.status(SOURCE).count_change == CountChange(
        today=2, yesterday=120, drop=True
    )
    assert len(store.active(NOW)) == 2  # the two items are stored as usual
    assert "WARN quality city_lrt_devices: items fell from 120 to 2" in caplog.text


def test_a_status_only_source_has_no_count_change(store: Any) -> None:
    scheduler, source = make(
        store, returning(None), name="weather_watch", provides_items=False
    )
    assert run(scheduler.run_once(source)) is True
    assert scheduler.status("weather_watch").count_change is None


def test_a_store_without_an_engine_gets_no_daily_check(make_obstruction: Any) -> None:
    class FakeStore:
        def replace_source(self, *a: Any, **k: Any) -> None:
            pass

        def purge_expired(self, now: datetime) -> list[str]:
            return []

    scheduler, source = make(FakeStore(), returning(items(make_obstruction, 1)))
    assert scheduler.counts is None
    assert run(scheduler.run_once(source)) is True
    assert scheduler.status(SOURCE).count_change is None


def test_a_failing_daily_count_never_fails_the_fetch(
    store: Any, make_obstruction: Any, caplog: pytest.LogCaptureFixture
) -> None:
    scheduler, source = make(store, returning(items(make_obstruction, 3)))
    assert scheduler.counts is not None

    def broken(*a: Any, **k: Any) -> CountChange:
        raise RuntimeError("disk full")

    scheduler.counts.record = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="openpath.scheduler"):
        assert run(scheduler.run_once(source)) is True
    status = scheduler.status(SOURCE)
    assert status.item_count == 3 and status.count_change is None
    assert len(store.active(NOW)) == 3
    assert "QUALITY city_lrt_devices daily count not recorded" in caplog.text


def test_the_quality_report_reaches_the_status_and_new_fields_log_once(
    store: Any, make_obstruction: Any, caplog: pytest.LogCaptureFixture
) -> None:
    reports = [QualityReport(3, ("bar", "foo"), "disruption 9: missing point")]
    scheduler, source = make(
        store, returning(items(make_obstruction, 1)), quality=lambda: reports[0]
    )
    with caplog.at_level(logging.INFO, logger="openpath.scheduler"):
        run(scheduler.run_once(source))
        run(scheduler.run_once(source))  # the same report again: nothing new to log
        reports[0] = QualityReport(0, ("bar", "baz", "foo"), None)
        run(scheduler.run_once(source))
    status = scheduler.status(SOURCE)
    assert status.rejected_rows == 0 and status.last_validation_error is None
    assert status.unexpected_fields == ["bar", "baz", "foo"]
    assert caplog.text.count("WARN quality city_lrt_devices: new fields bar, foo") == 1
    assert caplog.text.count("WARN quality city_lrt_devices: new fields baz") == 1
    assert "SOURCE city_lrt_devices ok items=1 skipped=0 rejected=3" in caplog.text
    assert "SOURCE city_lrt_devices ok items=1 skipped=0 rejected=0" in caplog.text


def test_a_quality_reader_that_raises_keeps_the_previous_report(
    store: Any, make_obstruction: Any, caplog: pytest.LogCaptureFixture
) -> None:
    calls = [0]

    def reader() -> QualityReport:
        calls[0] += 1
        if calls[0] > 1:
            raise RuntimeError("no report")
        return QualityReport(2, ("foo",), "bad row")

    scheduler, source = make(
        store, returning(items(make_obstruction, 1)), quality=reader
    )
    run(scheduler.run_once(source))
    with caplog.at_level(logging.ERROR, logger="openpath.scheduler"):
        assert run(scheduler.run_once(source)) is True
    status = scheduler.status(SOURCE)
    assert status.rejected_rows == 2 and status.unexpected_fields == ["foo"]
    assert "QUALITY city_lrt_devices report unavailable" in caplog.text


def test_the_scheduler_clock_decides_the_day(store: Any, make_obstruction: Any) -> None:
    """A frozen replay clock (``app.state.clock``) makes the replay day the day."""
    clock_value = [NOW - DAY]
    source = Source(
        name=SOURCE, cadence_s=60, fetch=returning(items(make_obstruction, 12))
    )
    scheduler = Scheduler(store, [source], clock=lambda: clock_value[0], backoff_s=(0,))
    run(scheduler.run_once(source))
    clock_value[0] = NOW
    source.fetch = returning(items(make_obstruction, 5))
    run(scheduler.run_once(source))
    assert scheduler.status(SOURCE).count_change == CountChange(
        today=5, yesterday=12, drop=True
    )
    assert scheduler.counts is not None
    assert [row.day for row in scheduler.counts.counts(SOURCE)] == [
        date(2026, 11, 9),
        date(2026, 11, 10),
    ]


# ------------------------------------------------------------- /api/status


@pytest.fixture
def quiet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    for name in ("DATA_MODE", "REPLAY_SNAPSHOT", "REPLAY_AT", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)


def test_status_shows_the_quality_fields(
    quiet_env: None, make_obstruction: Any
) -> None:
    report = QualityReport(
        1, ("feed_version", "location"), "disruption 1309: missing point"
    )
    with TestClient(create_app()) as client:
        store = client.app.state.store  # type: ignore[attr-defined]
        counts = client.app.state.daily_counts  # type: ignore[attr-defined]
        counts.record(SOURCE, 12, NOW - DAY)
        scheduler = Scheduler(
            store,
            [
                Source(
                    SOURCE,
                    120,
                    returning(items(make_obstruction, 2)),
                    quality=lambda: report,
                )
            ],
            clock=lambda: NOW,
            counts=counts,
        )
        run(scheduler.run_once(scheduler.sources[0]))
        client.app.state.scheduler = scheduler  # type: ignore[attr-defined]
        body = client.get("/api/status").json()
    (source,) = body["sources"]
    assert (
        source["item_count"] == 2 and source["skipped_items"] == 0
    )  # unchanged fields
    assert source["rejected_rows"] == 1
    assert source["unexpected_fields"] == ["feed_version", "location"]
    assert source["last_validation_error"] == "disruption 1309: missing point"
    assert source["count_change"] == {"today": 2, "yesterday": 12, "drop": True}


def test_the_new_fields_are_in_the_openapi_schema(quiet_env: None) -> None:
    with TestClient(create_app()) as client:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
    status = schemas["SourceStatus"]["properties"]
    for name in (
        "rejected_rows",
        "unexpected_fields",
        "last_validation_error",
        "count_change",
    ):
        assert name in status, name
    assert set(schemas["CountChange"]["properties"]) == {"today", "yesterday", "drop"}
    # Additive only: nothing the web app already reads was renamed or made required.
    assert {"source", "item_count", "skipped_items", "stale", "last_error"} <= set(
        status
    )
