"""OP-313: other elevators and escalators at the station (issue #149).

Each LRT outage's ``detail`` names the station's other devices that are in service,
from the City device list (``javh-pjj2``) grouped by station. A device is in service
unless the outage feed (``snws-u3zx``) lists it; the device list's own status is
ignored. Tests use the recorded November 7 snapshot in ``fixtures/lrt`` and edit
copies of its rows in memory; every test blocks the network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from cloud.app import sources
from cloud.app.adapters import lrt_devices as lrt
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionStore

SNAPSHOT = Path(__file__).parent / "fixtures"
FIXTURES = SNAPSHOT / "lrt"
EDMONTON = ZoneInfo("America/Edmonton")
NOW = datetime(2026, 11, 7, 23, tzinfo=UTC)
Feeds = dict[str, list[lrt.Row]]

CENTRAL = (
    "Central, West Platform. In service at Central: elevators at Platform and North "
    "Entrance; escalators at East Platform and North Entrance."
)
HEALTH_SCIENCES = (
    "Health Sciences/Jubilee, Platform. Use the Platform elevator instead."
)
HEALTH_SCIENCES_ALL_OUT = (
    "Health Sciences/Jubilee, Platform. No elevators or escalators at Health "
    "Sciences/Jubilee are in service."
)
# The plain detail OP-105 posts, and this story keeps without a device list.
PLAIN = {
    "43": "Government Centre, South Entrance",
    "45": "Corona, South Entrance",
    "54": "Central, West Platform",
    "99": "Health Sciences/Jubilee, Platform",
    "101": "University, East Platform Up",
    "103": "University, West Platform Down",
    "119": "Southgate, East Entrance",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not call the network")

    monkeypatch.setattr(lrt, "urlopen", blocked)


@pytest.fixture
def feeds() -> Feeds:
    return {
        dataset: json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
        for dataset, filename in (
            (lrt.OUTAGES, "outages.json"),
            (lrt.DEVICES, "devices.json"),
            (lrt.STATIONS, "stations.json"),
        )
    }


def normalize(feeds: Feeds) -> dict[str, str]:
    """Device id -> detail for the recorded (or edited) feeds."""
    items = lrt.normalize_outages(
        feeds[lrt.OUTAGES],
        feeds[lrt.DEVICES],
        feeds[lrt.STATIONS],
        observed_at=NOW,
        source_timezone=EDMONTON,
    )
    return {item["source_ref"]: item["detail"] for item in items}


def device(feeds: Feeds, device_id: str) -> lrt.Row:
    return next(row for row in feeds[lrt.DEVICES] if row["device_id"] == device_id)


def put_out(feeds: Feeds, device_id: str) -> None:
    """List a recorded device in a copy of the outage feed, as the City would."""
    row = deepcopy(device(feeds, device_id))
    row.update(
        device_op_status_code="1",
        device_op_status="Out of Service",
        since_time_stamp="2026-11-07T08:00:03.000",
        polled_time_stamp="2026-11-07T15:03:03.000",
    )
    feeds[lrt.OUTAGES].append(row)


class FakeReader:
    def __init__(self, feeds: Feeds) -> None:
        self.feeds = feeds
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def read(self, dataset: str) -> list[lrt.Row]:
        self.calls.append(dataset)
        if dataset in self.fail:
            raise lrt.FeedError("Simulated device list failure")
        return deepcopy(self.feeds[dataset])


def replay_lrt_source(snapshot: Path) -> sources.Source:
    """The LRT source exactly as ``make dev`` builds it in replay mode."""
    built = sources.build_sources(
        mode="replay",
        snapshot=snapshot,
        app_token=None,
        async_client=httpx.AsyncClient(),
        sync_client=httpx.Client(),
        weather_service=None,
        configs=[sources.SourceConfig(key="lrt")],
    )
    assert [source.name for source in built] == [lrt.SOURCE]
    return built[0]


# ---------------------------------------------------------- the recorded snapshot


def test_health_sciences_suggests_the_elevator_with_the_same_label(
    feeds: Feeds,
) -> None:
    assert normalize(feeds)["99"] == HEALTH_SCIENCES


def test_central_lists_in_service_devices_with_other_labels(feeds: Feeds) -> None:
    assert normalize(feeds)["54"] == CENTRAL


def test_every_recorded_outage_names_its_station_devices(feeds: Feeds) -> None:
    details = normalize(feeds)
    assert details["119"] == (
        "Southgate, East Entrance. Use the East Entrance elevator instead. Also in "
        "service at Southgate: elevators at Platform and West Entrance; escalator at "
        "Platform."
    )
    # University has two escalators out; neither is listed as in service.
    for device_id in ("101", "103"):
        assert details[device_id].startswith(f"{PLAIN[device_id]}. In service at ")
        assert "East Platform Up" not in details[device_id].split(": ", 1)[1]
        assert "West Platform Down" not in details[device_id].split(": ", 1)[1]
    for device_id, detail in details.items():
        assert detail.startswith(f"{PLAIN[device_id]}. ")
        assert len(detail) <= lrt.DETAIL_LIMIT


def test_elevator_out_with_no_same_label_elevator_says_so(feeds: Feeds) -> None:
    put_out(feeds, "29")  # Clareview's only elevator, at Platform
    put_out(feeds, "92")  # Central's Platform elevator
    details = normalize(feeds)
    # The in-service escalator at Platform is listed, never suggested instead.
    assert details["29"] == (
        "Clareview, Platform. No other elevator at Platform is in service. In service "
        "at Clareview: escalator at Platform."
    )
    assert details["92"] == (
        "Central, Platform. No other elevator at Platform is in service. In service "
        "at Central: elevator at North Entrance; escalators at East Platform and North "
        "Entrance."
    )
    # Central's escalator outage no longer lists the Platform elevator.
    assert details["54"] == (
        "Central, West Platform. In service at Central: elevator at North Entrance; "
        "escalators at East Platform and North Entrance."
    )


def test_an_elevator_with_the_same_label_is_suggested_for_an_elevator(
    feeds: Feeds,
) -> None:
    second = {**device(feeds, "100"), "device_id": "900"}  # an edited copy
    feeds[lrt.DEVICES].append(second)
    put_out(feeds, "100")
    assert normalize(feeds)["100"] == HEALTH_SCIENCES


def test_every_device_at_the_station_out(feeds: Feeds) -> None:
    put_out(feeds, "100")  # the master list still says this elevator is in service
    assert device(feeds, "100")["device_op_status_code"] == "0"
    details = normalize(feeds)
    assert details["99"] == HEALTH_SCIENCES_ALL_OUT
    assert details["100"] == HEALTH_SCIENCES_ALL_OUT


def test_the_device_list_status_is_ignored(feeds: Feeds) -> None:
    # The recorded master list marks Clareview elevator 29 out, but the outage feed
    # (the authority) does not list it, so it is in service.
    assert device(feeds, "29")["device_op_status_code"] == "1"
    put_out(feeds, "28")
    assert (
        normalize(feeds)["28"]
        == "Clareview, Platform. Use the Platform elevator instead."
    )


def test_an_outage_row_without_a_device_id_keeps_plain_details(feeds: Feeds) -> None:
    # Nobody can tell which device that row means, so nothing is called in service.
    unknown = deepcopy(feeds[lrt.OUTAGES][0])
    del unknown["device_id"]
    feeds[lrt.OUTAGES].append(unknown)
    assert normalize(feeds) == PLAIN


def test_a_station_with_an_incomplete_device_row_keeps_plain_details(
    feeds: Feeds,
) -> None:
    device(feeds, "92")["lrt_device_location"] = " "
    details = normalize(feeds)
    assert details["54"] == PLAIN["54"]
    assert details["99"] == HEALTH_SCIENCES


# ------------------------------------------------------ a missing device list


@pytest.mark.parametrize(
    "devices",
    [[], {"error": "upstream failure"}, [{"device_type": "Elevator"}]],
    ids=["empty", "changed-envelope", "no-valid-rows"],
)
def test_missing_or_invalid_device_list_posts_plain_details(
    feeds: Feeds, devices: object
) -> None:
    feeds[lrt.DEVICES] = devices  # type: ignore[assignment]
    assert normalize(feeds) == PLAIN


def test_device_list_failure_without_cache_still_posts_outages(feeds: Feeds) -> None:
    reader = FakeReader(feeds)
    reader.fail.add(lrt.DEVICES)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    result = adapter.fetch(NOW)
    assert {item["source_ref"]: item["detail"] for item in result} == PLAIN
    assert adapter.last_success == NOW
    assert "No javh-pjj2 device list" in adapter.last_error
    # The next poll tries the list again and names the station devices.
    reader.fail.clear()
    result = adapter.fetch(NOW + timedelta(minutes=2))
    assert reader.calls == [lrt.DEVICES, lrt.STATIONS, lrt.OUTAGES] + [
        lrt.DEVICES,
        lrt.OUTAGES,
    ]
    assert {item["source_ref"]: item["detail"] for item in result}["99"] == (
        HEALTH_SCIENCES
    )
    assert adapter.last_error is None


def test_failed_daily_refresh_keeps_the_last_good_device_list(feeds: Feeds) -> None:
    reader = FakeReader(feeds)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    adapter.fetch(NOW)
    grouped = adapter._station_devices
    reader.fail.add(lrt.DEVICES)
    result = adapter.fetch(NOW + timedelta(days=1))
    assert reader.calls[-3] == lrt.DEVICES
    assert adapter._station_devices is grouped
    details = {item["source_ref"]: item["detail"] for item in result}
    assert details["54"] == CENTRAL
    assert details["99"] == HEALTH_SCIENCES
    assert "Using cached javh-pjj2" in adapter.last_error


# ----------------------------------------------------------------- replay mode


def test_replay_mode_reads_the_recorded_device_list() -> None:
    source = replay_lrt_source(SNAPSHOT)
    details = {
        item["source_ref"]: item["detail"] for item in asyncio.run(source.fetch(NOW))
    }
    assert details["99"] == HEALTH_SCIENCES
    assert details["54"] == CENTRAL


def test_replay_snapshot_without_a_device_list_still_posts_outages(
    tmp_path: Path,
) -> None:
    (tmp_path / "lrt").mkdir()
    for name in ("outages.json", "stations.json"):
        shutil.copy(FIXTURES / name, tmp_path / "lrt" / name)
    source = replay_lrt_source(tmp_path)
    items = asyncio.run(source.fetch(NOW))
    assert {item["source_ref"]: item["detail"] for item in items} == PLAIN


# --------------------------------------------------------- limits and the store


def test_detail_limit_matches_the_schema() -> None:
    limits = [
        meta.max_length
        for meta in Obstruction.model_fields["detail"].metadata
        if hasattr(meta, "max_length")
    ]
    assert limits == [lrt.DETAIL_LIMIT]


def test_many_devices_shorten_the_list_between_words() -> None:
    devices = [
        lrt.StationDevice(
            str(n), "elevator" if n < 5 else "escalator", f"Concourse Level {n} Walkway"
        )
        for n in range(1, 40)
    ]
    detail = lrt.station_detail(
        "University", "Platform", "elevator", "999", devices, set()
    )
    assert len(detail) <= lrt.DETAIL_LIMIT
    assert detail.startswith(
        "University, Platform. No other elevator at Platform is in service. In "
        "service at University: elevators at Concourse Level 1 Walkway, Concourse "
        "Level 2 Walkway, Concourse Level 3 Walkway and Concourse Level 4 Walkway; "
        "escalators at Concourse Level 5 Walkway, "
    )
    assert detail.endswith(" more.")
    hidden = int(detail.rsplit(" and ", 1)[1].split()[0])
    named = detail.count("Walkway")
    assert named + hidden == len(devices)
    Obstruction.model_validate(
        {
            "source": lrt.SOURCE,
            "source_ref": "999",
            "category": "indoor",
            "kind": "elevator_out",
            "effect": "blocked",
            "title": "Elevator out of service",
            "detail": detail,
            "geometry": {"type": "Point", "coordinates": [-113.52, 53.52]},
            "confidence": "high",
            "observed_at": NOW,
            "expires_at": NOW + timedelta(minutes=10),
            "url": "https://data.edmonton.ca/d/snws-u3zx",
            "attribution": "City of Edmonton Open Data",
        }
    )


def test_an_overlong_detail_is_cut_at_a_word() -> None:
    station = "Station " * 80
    detail = lrt.station_detail(station.strip(), "Platform", "elevator", "1", [], set())
    assert len(detail) <= lrt.DETAIL_LIMIT
    assert detail.endswith("Station…")


def test_a_station_change_logs_one_change_line_per_item(
    feeds: Feeds, store: ObstructionStore, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)

    def poll(at: datetime) -> list[str]:
        caplog.clear()
        items = [Obstruction.model_validate(item) for item in adapter.fetch(at)]
        store.replace_source(lrt.SOURCE, items, at)
        return [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("CHANGE")
        ]

    caplog.set_level(logging.INFO)
    assert len(poll(NOW)) == len(PLAIN)
    assert poll(NOW + timedelta(minutes=2)) == []  # same statuses: no news
    put_out(feeds, "92")
    changes = poll(NOW + timedelta(minutes=4))
    assert sorted(changes) == [
        "CHANGE city_lrt_devices:54 updated escalator_out 'Escalator out of service'",
        "CHANGE city_lrt_devices:92 new elevator_out 'Elevator out of service'",
    ]
