"""Purpose: test inactive feeds, verified plow times, and safe failure handling.

The NA fixture is an actual City response. Completed rows and service-time /
crossing evidence are explicitly synthetic test inputs, never live observations.
"""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from cloud.app.adapters.snow_clearing import (
    WARNING,
    MissingCrossings,
    MissingServiceTime,
    SnowClearingAdapter,
)

FIXTURES = Path(__file__).parent / "fixtures/snow_clearing"
NOW = datetime(2026, 11, 7, 22, tzinfo=timezone.utc)
CROSSING = ("crossing-test", {"type": "Point", "coordinates": [-113.59, 53.43]})


@pytest.fixture
def na_rows():
    return json.loads((FIXTURES / "na.json").read_text())


@pytest.fixture
def completed(na_rows):
    # Explicit mutation for a controlled test, not a recorded cleared-road claim.
    return {
        **copy.deepcopy(na_rows[0]),
        "route_stat": "Completed",
        "seg_stat": "Completed",
    }


def adapter(**kwargs):
    return SnowClearingAdapter(None, item_factory=dict, **kwargs)


def verified_adapter(**kwargs):
    defaults = {
        "verified_service_time": lambda row: NOW - timedelta(hours=1),
        "resolve_crossings": lambda row: [CROSSING],
    }
    return adapter(**{**defaults, **kwargs})


def test_recorded_na_feed_produces_no_items(na_rows):
    assert adapter().normalize(na_rows, NOW) == []


def test_actual_missing_status_records_fail_explicitly():
    rows = json.loads((FIXTURES / "missing_status.json").read_text())
    with pytest.raises(ValueError, match="route_stat"):
        adapter().normalize(rows, NOW)


def test_real_metadata_confirms_service_timestamp_is_unavailable():
    meta = json.loads((FIXTURES / "source_metadata.json").read_text())
    columns = {column["fieldName"]: column for column in meta["columns"]}
    assert "deprecated" in columns["last_updat"]["description"]
    assert "always empty" in columns["last_updat"]["description"]
    assert "Nonstable" in columns["fid"]["description"]


def test_completed_without_timing_evidence_raises(completed):
    with pytest.raises(MissingServiceTime):
        adapter().normalize([completed], NOW)


def test_deprecated_last_updat_and_service_hours_are_never_pass_time(completed):
    completed.update(last_updat=NOW.isoformat(), serv_hrs="1", serv_ach="TRUE")
    with pytest.raises(MissingServiceTime):
        adapter().normalize([completed], NOW)


@pytest.mark.parametrize("hours,count", [(0, 1), (11.99, 1), (12, 0), (13, 0)])
def test_only_passes_inside_twelve_hours_create_cautions(completed, hours, count):
    service = verified_adapter(
        verified_service_time=lambda row: NOW - timedelta(hours=hours)
    )
    items = service.normalize([completed], NOW)
    assert len(items) == count
    for item in items:
        assert item["effect"] == "caution"
        assert item["kind"] == "plow_passed"
        assert item["category"] == "road"
        assert item["title"] == WARNING
        assert item["geometry"] == CROSSING[1]
        assert item["starts_at"] == NOW - timedelta(hours=hours)
        assert NOW < item["expires_at"] <= NOW + timedelta(minutes=30)
        assert item["expires_at"] <= item["ends_at"]


@pytest.mark.parametrize(
    "bad_time",
    [NOW + timedelta(seconds=1), datetime(2026, 11, 7), "2026-11-07T22:00:00Z"],
)
def test_invalid_service_times_fail_refresh(completed, bad_time):
    with pytest.raises(ValueError):
        verified_adapter(verified_service_time=lambda row: bad_time).normalize(
            [completed], NOW
        )


def test_recent_service_needs_actual_crossings(completed):
    with pytest.raises(MissingCrossings):
        adapter(verified_service_time=lambda row: NOW).normalize([completed], NOW)
    with pytest.raises(MissingCrossings):
        verified_adapter(resolve_crossings=lambda row: []).normalize([completed], NOW)


def test_one_item_per_crossing_with_stable_identity(completed):
    crossings = [
        CROSSING,
        ("other-crossing", {"type": "Point", "coordinates": [-113.591, 53.431]}),
    ]
    service = verified_adapter(resolve_crossings=lambda row: crossings)
    items = service.normalize([completed], NOW)
    assert len(items) == 2
    assert len({item["id"] for item in items}) == 2
    changed_fid = {**completed, "fid": "different"}
    assert service.normalize([changed_fid], NOW) == items
    assert service.normalize([completed, changed_fid], NOW) == items


def test_conflicting_route_copies_fail(completed):
    with pytest.raises(ValueError, match="Conflicting"):
        verified_adapter().normalize([completed, {**completed, "seg_stat": "NA"}], NOW)


@pytest.mark.parametrize("status", ["Not Started", "Scheduled", "In Progress", "NA"])
def test_unserviced_segments_do_not_warn(completed, status):
    completed["seg_stat"] = status
    assert adapter().normalize([completed], NOW) == []


def test_unknown_status_does_not_silently_clear_data(completed):
    completed["seg_stat"] = "Unexpected new status"
    with pytest.raises(ValueError, match="Unrecognized"):
        adapter().normalize([completed], NOW)


@pytest.mark.parametrize(
    "crossings", [[("", CROSSING[1])], [("id", {})], [CROSSING, CROSSING]]
)
def test_invalid_crossing_mapping_is_rejected(completed, crossings):
    with pytest.raises(ValueError):
        verified_adapter(resolve_crossings=lambda row: crossings).normalize(
            [completed], NOW
        )


def test_fetch_pages_token_cadence_and_cache_copy(na_rows):
    requests = []

    def handler(request):
        requests.append(request)
        offset = int(request.url.params["$offset"])
        return httpx.Response(200, json=na_rows[offset : offset + 2])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = SnowClearingAdapter(client, app_token="test-token", page_size=2)
        assert service.fetch(NOW) == []
        assert service.cadence_s == 900
        assert service.last_good == []
        assert service.last_success == NOW
    assert [request.url.params["$offset"] for request in requests] == ["0", "2"]
    assert all(request.headers["X-App-Token"] == "test-token" for request in requests)
    assert all(request.url.params["$order"] == "route_id,fid" for request in requests)


@pytest.mark.parametrize(
    "failure", ["http", "timeout", "schema", "missing_time", "page_limit"]
)
def test_failure_keeps_last_successful_snapshot(completed, failure):
    fail = False

    def handler(request):
        if fail:
            if failure == "http":
                return httpx.Response(503)
            if failure == "timeout":
                raise httpx.ReadTimeout("Source timed out")
            if failure == "schema":
                return httpx.Response(200, json={"error": "Bad schema"})
            if failure == "page_limit":
                return httpx.Response(200, json=[completed, completed])
        return httpx.Response(200, json=[completed])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = SnowClearingAdapter(
            client,
            item_factory=dict,
            page_size=2,
            max_pages=1,
            retries=0,
            verified_service_time=lambda row: (
                None if fail and failure == "missing_time" else NOW
            ),
            resolve_crossings=lambda row: [CROSSING],
        )
        first = service.fetch(NOW)
        first.clear()
        assert len(service.last_good) == 1
        saved = copy.deepcopy(service.last_good)
        fail = True
        with pytest.raises((ValueError, httpx.HTTPError)):
            service.fetch(NOW + timedelta(minutes=15))
        assert service.last_good == saved
        assert service.last_success == NOW
        assert service.last_error is not None


def test_retry_delays_and_genuine_empty_success():
    calls = []
    delays = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) <= 3 else httpx.Response(200, json=[])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = SnowClearingAdapter(client, sleep=delays.append)
        assert service.fetch(NOW) == []
        assert service.last_error is None
        assert delays == [1, 2, 4]
        assert len(calls) == 4


def test_shared_contract_when_available(completed):
    """Automatically verify the real interface/model after OP-101 is merged."""
    schemas = pytest.importorskip(
        "cloud.app.schemas", reason="OP-101 is not merged yet"
    )
    service = SnowClearingAdapter(
        None,
        item_factory=schemas.Obstruction,
        verified_service_time=lambda row: NOW,
        resolve_crossings=lambda row: [CROSSING],
    )
    assert isinstance(service, schemas.SourceAdapter)
    item = service.normalize([completed], NOW)[0]
    assert isinstance(item, schemas.Obstruction)
    assert item.effect == "caution"
