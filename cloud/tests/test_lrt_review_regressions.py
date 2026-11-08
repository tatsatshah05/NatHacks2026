"""OP-105 / PR #83: regressions for Tatsat's review.

Use recorded City responses to check freshness, partial-feed recovery and cached
lookups. HTTP responses are mocked, and every test blocks live network access.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

from cloud.app.adapters import lrt_devices as lrt

FIXTURES = Path(__file__).parent / "fixtures/lrt"
NOW = datetime(2026, 11, 7, 23, tzinfo=UTC)
EDMONTON = ZoneInfo("America/Edmonton")
Feeds = dict[str, list[lrt.Row]]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not call the network")

    monkeypatch.setattr(lrt, "urlopen", blocked)


@pytest.fixture
def feeds() -> Feeds:
    return {
        dataset: json.loads((FIXTURES / filename).read_text("utf-8"))
        for dataset, filename in (
            (lrt.OUTAGES, "outages.json"),
            (lrt.DEVICES, "devices.json"),
            (lrt.STATIONS, "stations.json"),
        )
    }


class FakeReader:
    def __init__(self, feeds: Feeds) -> None:
        self.feeds = feeds
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def read(self, dataset: str) -> list[lrt.Row]:
        self.calls.append(dataset)
        if dataset in self.fail:
            raise lrt.FeedError("Simulated lookup failure")
        return deepcopy(self.feeds[dataset])


@pytest.mark.parametrize("minutes", [0, 10, 50, 70])
def test_unchanged_city_poll_does_not_make_outages_expire(
    feeds: Feeds, minutes: int
) -> None:
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)
    fetched_at = NOW + timedelta(minutes=minutes)
    result = adapter.fetch(fetched_at)
    assert len(result) == len(feeds[lrt.OUTAGES])
    assert {"101", "103"} <= {item["source_ref"] for item in result}
    assert all(
        datetime.fromisoformat(item["observed_at"]) == fetched_at for item in result
    )
    assert all(
        datetime.fromisoformat(item["expires_at"]) == fetched_at + timedelta(minutes=10)
        for item in result
    )


@pytest.mark.parametrize(
    "failed", [{lrt.DEVICES}, {lrt.STATIONS}, {lrt.DEVICES, lrt.STATIONS}]
)
def test_daily_lookup_failure_uses_cache_and_still_refreshes_outages(
    feeds: Feeds, failed: set[str]
) -> None:
    reader = FakeReader(feeds)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    initial = adapter.fetch(NOW)
    devices, stations = adapter._devices, adapter._stations
    reader.fail = failed
    fetched_at = NOW + timedelta(days=1)
    current = adapter.fetch(fetched_at)
    assert {item["source_ref"] for item in current} == {
        item["source_ref"] for item in initial
    }
    assert all(
        datetime.fromisoformat(item["observed_at"]) == fetched_at for item in current
    )
    assert reader.calls[-1] == lrt.OUTAGES
    assert adapter.last_success == fetched_at
    assert "Using cached" in adapter.last_error
    if lrt.DEVICES in failed:
        assert adapter._devices is devices
    if lrt.STATIONS in failed:
        assert adapter._stations is stations
    reader.fail.clear()
    adapter.fetch(fetched_at + timedelta(minutes=2))
    assert adapter.last_error is None


def test_station_lookup_failure_without_cache_fails_without_publishing_empty_data(
    feeds: Feeds,
) -> None:
    # OP-313: a device list failure without a cache still posts the outages, with
    # plain details (test_lrt_station_devices); the station table stays required.
    reader = FakeReader(feeds)
    reader.fail.add(lrt.STATIONS)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    with pytest.raises(lrt.FeedError):
        adapter.fetch(NOW)
    assert adapter.last_success is None
    assert adapter.last_error


def test_all_bad_outages_preserve_last_good_snapshot(feeds: Feeds) -> None:
    reader = FakeReader(feeds)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    initial = adapter.fetch(NOW)
    for row in feeds[lrt.OUTAGES]:
        row["device_op_status_code"] = "unknown"
    with pytest.raises(lrt.FeedError, match="All 7 outage rows"):
        adapter.fetch(NOW + timedelta(minutes=2))
    assert adapter.last_good == initial
    assert adapter.last_success == NOW
    assert "Skipped 7 invalid rows" in adapter.last_error


def test_unknown_bad_row_identity_does_not_delete_previous_outages(
    feeds: Feeds,
) -> None:
    reader = FakeReader(feeds)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    initial = adapter.fetch(NOW)
    bad, cleared = deepcopy(feeds[lrt.OUTAGES][:2])
    del bad["device_id"]
    cleared["device_op_status_code"] = "0"
    feeds[lrt.OUTAGES] = [bad, cleared]
    result = adapter.fetch(NOW + timedelta(minutes=2))
    assert {item["source_ref"] for item in result} == {
        item["source_ref"] for item in initial
    } - {cleared["device_id"]}
    assert all(item["observed_at"] == "2026-11-07T23:00:00Z" for item in result)
    assert "Skipped 1 invalid row" in adapter.last_error


def test_one_bad_lookup_row_does_not_drop_the_valid_station_table(
    feeds: Feeds, caplog: pytest.LogCaptureFixture
) -> None:
    feeds[lrt.STATIONS].append({"lrt_stop_description": "Malformed Station"})
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)
    assert len(adapter.fetch(NOW)) == len(feeds[lrt.OUTAGES])
    assert adapter.last_error == "Skipped 1 invalid row"
    assert "lrt_stop_number" in caplog.text


def test_stop_ids_are_sorted_numerically(feeds: Feeds) -> None:
    template = next(
        row for row in feeds[lrt.STATIONS] if row["lrt_stop_number"] == "19251"
    )
    numeric_first = {**template, "lrt_stop_number": "9", "longitude": "-113.51"}
    lexical_first = {**template, "lrt_stop_number": "10", "longitude": "-113.52"}
    feeds[lrt.STATIONS].extend([lexical_first, numeric_first])
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)
    government = next(item for item in adapter.fetch(NOW) if item["source_ref"] == "43")
    assert government["geometry"]["coordinates"][0] == -113.51


def test_missing_city_poll_timestamp_does_not_affect_freshness(feeds: Feeds) -> None:
    for row in feeds[lrt.OUTAGES]:
        row.pop("polled_time_stamp", None)
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)
    assert len(adapter.fetch(NOW)) == len(feeds[lrt.OUTAGES])
    assert adapter.last_error is None


def test_detail_remains_human_while_start_time_is_structured(feeds: Feeds) -> None:
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)
    government = next(item for item in adapter.fetch(NOW) if item["source_ref"] == "43")
    # OP-313 adds the station's in-service devices after "<station>, <location>".
    assert government["detail"] == (
        "Government Centre, South Entrance. Use the South Entrance elevator instead. "
        "Also in service at Government Centre: elevators at South Platform, North "
        "Platform and North Entrance; escalators at South Platform Up, North Platform "
        "and South Platform Down."
    )
    assert government["starts_at"].endswith("Z")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("120", 120),
        ("Sat, 07 Nov 2026 23:05:00 GMT", 300),
        ("Sat, 07 Nov 2026 22:00:00 GMT", 0),
        ("invalid", None),
    ],
)
def test_retry_after_is_reported_to_scheduler_without_retrying(
    monkeypatch: pytest.MonkeyPatch, header: str, expected: float | None
) -> None:
    calls: list[bool] = []

    def limited(*args: object, **kwargs: object) -> None:
        calls.append(True)
        raise HTTPError("x", 429, "Limited", {"Retry-After": header}, None)

    monkeypatch.setattr(lrt, "urlopen", limited)
    reader = lrt.SocrataReader(clock=lambda: NOW)
    with pytest.raises(lrt.FeedError) as error:
        reader.read(lrt.OUTAGES)
    assert error.value.retry_after_s == expected
    assert len(calls) == 1
