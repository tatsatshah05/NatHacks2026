"""OP-105: LRT elevator and escalator adapter (issue #33, PR #83).

Check outage status, station matching, timestamps and failure recovery.

Use recorded City responses and small edited copies for edge cases. Every
test blocks network access so results do not depend on the live City feed.
"""

import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from cloud.app.adapters import lrt_devices as lrt

FIXTURES = Path(__file__).parent / "fixtures" / "lrt"
EDMONTON = ZoneInfo("America/Edmonton")
NOW = datetime(2026, 11, 7, 23, tzinfo=UTC)
Feeds = dict[str, list[lrt.Row]]


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


def normalize(
    feeds: Feeds, *, now: datetime = NOW, diagnostics: lrt.RowDiagnostics | None = None
) -> list[lrt.Row]:
    return lrt.normalize_outages(
        feeds[lrt.OUTAGES],
        feeds[lrt.DEVICES],
        feeds[lrt.STATIONS],
        observed_at=now,
        source_timezone=EDMONTON,
        diagnostics=diagnostics,
    )


def test_recorded_outages_have_station_coordinates_and_provenance(feeds: Feeds) -> None:
    result = normalize(feeds)
    assert {item["source_ref"] for item in result} == {
        row["device_id"] for row in feeds[lrt.OUTAGES]
    }
    city_coordinates = {
        (float(row["longitude"]), float(row["latitude"])) for row in feeds[lrt.STATIONS]
    }
    for item in result:
        assert item["id"] == f"city_lrt_devices:{item['source_ref']}"
        assert item["category"] == "indoor"
        assert item["confidence"] == "high"
        assert tuple(item["geometry"]["coordinates"]) in city_coordinates
        assert item["url"] == "https://data.edmonton.ca/d/snws-u3zx"
        assert item["attribution"] == "City of Edmonton Open Data"
        assert "since " not in item["detail"]
        assert item["starts_at"].endswith("Z")


def test_master_status_never_creates_an_outage(feeds: Feeds) -> None:
    # In the recorded City snapshot, Clareview elevator 29 is out in the master
    # list but absent from the fresher outage feed. It must not block a route.
    assert (
        next(r for r in feeds[lrt.DEVICES] if r["device_id"] == "29")[
            "device_op_status_code"
        ]
        == "1"
    )
    assert "29" not in {r["source_ref"] for r in normalize(feeds)}
    feeds[lrt.OUTAGES] = []
    assert normalize(feeds) == []


@pytest.mark.parametrize(
    ("device_type", "kind", "effect"),
    [
        ("Elevator", "elevator_out", "blocked"),
        ("Escalator", "escalator_out", "caution"),
    ],
)
def test_device_effects_with_explicitly_mutated_record(
    feeds: Feeds, device_type: str, kind: str, effect: str
) -> None:
    feeds[lrt.OUTAGES][0]["device_type"] = device_type
    result = normalize(feeds)
    item = next(r for r in result if r["source_ref"] == "43")
    assert (item["kind"], item["effect"]) == (kind, effect)


def test_outage_status_overrides_master_status(feeds: Feeds) -> None:
    outage = feeds[lrt.OUTAGES][0]
    master = next(
        r for r in feeds[lrt.DEVICES] if r["device_id"] == outage["device_id"]
    )
    master["device_op_status_code"] = "0"
    master["device_op_status"] = "In Service"
    assert outage["device_id"] in {r["source_ref"] for r in normalize(feeds)}


def test_master_metadata_fills_missing_outage_fields(feeds: Feeds) -> None:
    expected = normalize(feeds)
    for field in ("device_type", "lrt_station_name", "lrt_device_location"):
        del feeds[lrt.OUTAGES][0][field]
    assert normalize(feeds) == expected


def test_new_device_with_complete_outage_metadata_is_not_dropped(feeds: Feeds) -> None:
    feeds[lrt.OUTAGES][0]["device_id"] = "new-device"
    assert "new-device" in {r["source_ref"] for r in normalize(feeds)}


def test_station_alias_and_coordinate_selection_are_deterministic(feeds: Feeds) -> None:
    expected = normalize(feeds)
    government = next(r for r in expected if r["source_ref"] == "43")
    station = next(r for r in feeds[lrt.STATIONS] if r["lrt_stop_number"] == "19251")
    assert government["geometry"]["coordinates"] == [
        float(station["longitude"]),
        float(station["latitude"]),
    ]
    feeds[lrt.STATIONS].reverse()
    feeds[lrt.OUTAGES].reverse()
    assert normalize(feeds) == expected


def test_churchill_prefers_station_over_surface_stop(feeds: Feeds) -> None:
    feeds[lrt.OUTAGES][0]["lrt_station_name"] = "Churchill"
    item = next(r for r in normalize(feeds) if r["source_ref"] == "43")
    station = next(r for r in feeds[lrt.STATIONS] if r["lrt_stop_number"] == "18761")
    assert item["geometry"]["coordinates"][0] == float(station["longitude"])


def test_start_uses_source_timezone_but_freshness_uses_our_fetch_time(
    feeds: Feeds,
) -> None:
    feeds[lrt.OUTAGES] = [feeds[lrt.OUTAGES][0]]
    # Historical dates avoid assuming that future civil-time rules never change.
    feeds[lrt.OUTAGES][0]["since_time_stamp"] = "2024-07-30T15:09:03.000"
    feeds[lrt.OUTAGES][0]["polled_time_stamp"] = "2024-11-07T15:03:03.000"
    item = normalize(feeds)[0]
    assert item["starts_at"] == "2024-07-30T21:09:03Z"  # Daylight time
    assert item["observed_at"] == "2026-11-07T23:00:00Z"
    assert item["expires_at"] == "2026-11-07T23:10:00Z"


def test_explicit_timestamp_offsets_are_preserved(feeds: Feeds) -> None:
    feeds[lrt.OUTAGES][0]["since_time_stamp"] = "2026-11-04T12:17:03+00:00"
    item = next(r for r in normalize(feeds) if r["source_ref"] == "43")
    assert item["starts_at"] == "2026-11-04T12:17:03Z"


@pytest.mark.parametrize(
    "value", ["not-a-date", "2024-11-03T01:30:00", "2024-03-10T02:30:00"]
)
def test_invalid_or_ambiguous_timestamp_skips_only_its_row(
    feeds: Feeds, value: str
) -> None:
    feeds[lrt.OUTAGES][0]["since_time_stamp"] = value
    diagnostics = lrt.RowDiagnostics()
    assert len(normalize(feeds, diagnostics=diagnostics)) == len(feeds[lrt.OUTAGES]) - 1
    assert "timestamp" in diagnostics.issues[0].message


def test_outage_start_after_observation_skips_only_its_row(feeds: Feeds) -> None:
    feeds[lrt.OUTAGES][0]["since_time_stamp"] = "2099-01-01T00:00:00Z"
    diagnostics = lrt.RowDiagnostics()
    assert len(normalize(feeds, diagnostics=diagnostics)) == len(feeds[lrt.OUTAGES]) - 1
    assert "starts after" in diagnostics.issues[0].message


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("device_id", None, "device_id"),
        ("since_time_stamp", None, "since_time_stamp"),
        ("device_op_status_code", "7", "status code"),
        ("device_type", "Travelator", "device type"),
        ("lrt_station_name", "Unknown Station", "No City station"),
    ],
)
def test_bad_row_is_logged_and_other_outages_still_flow(
    feeds: Feeds,
    field: str,
    value: str | None,
    message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    feeds[lrt.OUTAGES][0][field] = value
    diagnostics = lrt.RowDiagnostics()
    result = normalize(feeds, diagnostics=diagnostics)
    assert len(result) == len(feeds[lrt.OUTAGES]) - 1
    assert len(diagnostics.issues) == 1
    assert message in diagnostics.issues[0].message
    assert message in caplog.text


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not-a-number", "181"])
def test_invalid_station_coordinates_fail(feeds: Feeds, value: str) -> None:
    for station in feeds[lrt.STATIONS]:
        station["longitude"] = value
    with pytest.raises(lrt.FeedError, match="no valid rows"):
        normalize(feeds)


def test_duplicate_outage_is_logged_without_dropping_other_outages(
    feeds: Feeds,
) -> None:
    expected = normalize(feeds)
    feeds[lrt.OUTAGES].append(deepcopy(feeds[lrt.OUTAGES][0]))
    diagnostics = lrt.RowDiagnostics()
    assert normalize(feeds, diagnostics=diagnostics) == expected
    assert "Duplicate" in diagnostics.issues[0].message


def test_in_service_record_is_not_an_obstruction(feeds: Feeds) -> None:
    device_id = feeds[lrt.OUTAGES][0]["device_id"]
    feeds[lrt.OUTAGES][0]["device_op_status_code"] = "0"
    assert device_id not in {r["source_ref"] for r in normalize(feeds)}


@pytest.mark.parametrize("dataset", [lrt.DEVICES, lrt.STATIONS])
def test_empty_lookup_feed_fails(feeds: Feeds, dataset: str) -> None:
    feeds[dataset] = []
    with pytest.raises(lrt.FeedError, match="must not be empty"):
        normalize(feeds)


class FakeReader:
    def __init__(self, feeds: Feeds) -> None:
        self.feeds = feeds
        self.calls: list[str] = []
        self.fail: str | None = None

    def read(self, dataset: str) -> list[lrt.Row]:
        self.calls.append(dataset)
        if self.fail == dataset:
            raise lrt.FeedError("Simulated upstream outage")
        return deepcopy(self.feeds[dataset])


def test_scheduler_owns_poll_timing_while_lookups_refresh_daily(feeds: Feeds) -> None:
    reader = FakeReader(feeds)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    initial = adapter.fetch(now=NOW)
    assert reader.calls == [lrt.DEVICES, lrt.STATIONS, lrt.OUTAGES]
    cached_devices, cached_stations = adapter._devices, adapter._stations
    second = adapter.fetch(now=NOW + timedelta(seconds=119))
    assert len(second) == len(initial)
    assert second[0]["observed_at"] != initial[0]["observed_at"]
    assert reader.calls[3:] == [lrt.OUTAGES]
    assert adapter._devices is cached_devices and adapter._stations is cached_stations
    assert adapter.source == lrt.SOURCE
    assert adapter.cadence_s == 120
    adapter.fetch(now=NOW + timedelta(minutes=2))
    assert reader.calls[3:] == [lrt.OUTAGES, lrt.OUTAGES]
    adapter.fetch(now=NOW + timedelta(days=1))
    assert reader.calls[5:] == [lrt.DEVICES, lrt.STATIONS, lrt.OUTAGES]


def test_failure_preserves_last_good_data_and_freshness(
    feeds: Feeds,
) -> None:
    reader = FakeReader(feeds)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    initial = adapter.fetch(now=NOW)
    reader.fail = lrt.OUTAGES
    with pytest.raises(lrt.FeedError):
        adapter.fetch(now=NOW + timedelta(days=1))
    assert adapter.last_good == initial
    assert adapter.last_success == NOW
    assert adapter.last_error == "Simulated upstream outage"
    reader.fail = None
    adapter.fetch(now=NOW + timedelta(days=1, seconds=1))
    assert adapter.last_error is None
    assert adapter.last_success == NOW + timedelta(days=1, seconds=1)


def test_bad_row_retains_its_last_good_outage_without_stalling_valid_rows(
    feeds: Feeds,
) -> None:
    reader = FakeReader(feeds)
    adapter = lrt.LrtDevicesAdapter(reader, source_timezone=EDMONTON)
    initial = adapter.fetch(now=NOW)
    bad_id = feeds[lrt.OUTAGES][0]["device_id"]
    del feeds[lrt.OUTAGES][0]["since_time_stamp"]
    updated = adapter.fetch(now=NOW + timedelta(minutes=2))
    assert len(updated) == len(initial)
    previous = next(item for item in initial if item["source_ref"] == bad_id)
    assert next(item for item in updated if item["source_ref"] == bad_id) == previous
    assert all(
        item["observed_at"] == "2026-11-07T23:02:00Z"
        for item in updated
        if item["source_ref"] != bad_id
    )
    assert adapter.last_success == NOW + timedelta(minutes=2)
    assert adapter.last_error == "Skipped 1 invalid row"
    feeds[lrt.OUTAGES] = feeds[lrt.OUTAGES][1:]
    resolved = adapter.fetch(now=NOW + timedelta(minutes=4))
    assert bad_id not in {item["source_ref"] for item in resolved}
    assert adapter.last_error is None


def test_successful_empty_feed_clears_previous_outages(feeds: Feeds) -> None:
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)
    assert adapter.fetch(now=NOW)
    feeds[lrt.OUTAGES] = []
    assert adapter.fetch(now=NOW + timedelta(minutes=2)) == []
    assert adapter.last_good == []
    assert adapter.last_error is None


def test_external_mutation_cannot_corrupt_cached_data(feeds: Feeds) -> None:
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)
    result = adapter.fetch(now=NOW)
    result[0]["geometry"]["coordinates"][0] = 0
    assert adapter.last_good[0]["geometry"]["coordinates"][0] != 0


def test_naive_poll_time_is_rejected(feeds: Feeds) -> None:
    adapter = lrt.LrtDevicesAdapter(FakeReader(feeds), source_timezone=EDMONTON)
    with pytest.raises(ValueError, match="timezone"):
        adapter.fetch(now=NOW.replace(tzinfo=None))


def test_http_pagination_token_ordering_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []
    pages = [[{"device_id": "1"}, {"device_id": "2"}], [{"device_id": "3"}]]

    def fake_open(request: lrt.Request, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(json.dumps(pages.pop(0)).encode())

    monkeypatch.setattr(lrt, "urlopen", fake_open)
    reader = lrt.SocrataReader("test-token", page_size=2)
    assert reader.read(lrt.OUTAGES) == [
        {"device_id": "1"},
        {"device_id": "2"},
        {"device_id": "3"},
    ]
    assert len(requests) == 2
    for index, (request, timeout) in enumerate(requests):
        query = parse_qs(urlsplit(request.full_url).query)
        assert query["$offset"] == [str(index * 2)]
        assert query["$order"] == ["device_id"]
        assert query["$limit"] == ["2"]
        assert request.get_header("X-app-token") == "test-token"
        assert timeout == 10


@pytest.mark.parametrize(
    "error",
    [
        URLError("offline"),
        TimeoutError(),
        HTTPError("x", 503, "down", {}, None),
        IncompleteRead(b"partial"),
    ],
)
def test_http_failure_is_attempted_once_for_the_scheduler_to_retry(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    calls = []

    def failed_open(*args: object, **kwargs: object) -> None:
        calls.append(True)
        raise error

    monkeypatch.setattr(lrt, "urlopen", failed_open)
    reader = lrt.SocrataReader()
    with pytest.raises(lrt.FeedError):
        reader.read(lrt.OUTAGES)
    assert len(calls) == 1


def test_rate_limit_is_passed_to_scheduler_before_a_later_call_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_open(*args: object, **kwargs: object) -> BytesIO:
        calls.append(True)
        if len(calls) == 1:
            raise HTTPError("x", 429, "rate limited", {"Retry-After": "120"}, None)
        return BytesIO(b"[]")

    monkeypatch.setattr(lrt, "urlopen", fake_open)
    reader = lrt.SocrataReader()
    with pytest.raises(lrt.FeedError) as error:
        reader.read(lrt.OUTAGES)
    assert error.value.retry_after_s == 120
    assert len(calls) == 1
    assert reader.read(lrt.OUTAGES) == []
    assert len(calls) == 2


def test_permanent_http_error_does_not_retry_or_leak_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def failed_open(*args: object, **kwargs: object) -> None:
        raise HTTPError("x", 403, "Forbidden", {}, None)

    monkeypatch.setattr(lrt, "urlopen", failed_open)
    with pytest.raises(lrt.FeedError, match="HTTP 403") as error:
        lrt.SocrataReader("secret-token").read(lrt.OUTAGES)
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize(
    "payload", [b"not-json", b'{"error":"upstream failure"}', b"[1]"]
)
def test_bad_json_or_changed_envelope_fails(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    monkeypatch.setattr(lrt, "urlopen", lambda *a, **k: BytesIO(payload))
    with pytest.raises(lrt.FeedError):
        lrt.SocrataReader().read(lrt.OUTAGES)


def test_offline_cli_labels_replay_and_timezone_assumption(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lrt_devices",
            "--fixtures",
            str(FIXTURES),
            "--source-timezone",
            "America/Edmonton",
            "--at",
            NOW.isoformat(),
        ],
    )
    lrt.main()
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "replay"
    assert result["source_timezone_assumption"] == "America/Edmonton"
    assert result["obstructions"]
    assert all(
        item["observed_at"] == "2026-11-07T23:00:00Z" for item in result["obstructions"]
    )


def test_cli_requires_an_explicit_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["lrt_devices", "--fixtures", str(FIXTURES)])
    with pytest.raises(SystemExit) as error:
        lrt.main()
    assert error.value.code == 2
