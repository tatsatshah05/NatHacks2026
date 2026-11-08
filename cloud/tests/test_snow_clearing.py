"""Purpose: test usable plow cautions, skipped rows, and single-attempt refreshes.

The NA and missing-status fixtures are actual City responses. Completed statuses
and verified service times below are controlled test evidence, never live claims.
"""

import copy
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from cloud.app.adapters.snow_clearing import WARNING, SnowClearingAdapter

FIXTURES = Path(__file__).parent / "fixtures/snow_clearing"
NOW = datetime(2026, 11, 7, 22, tzinfo=UTC)


@pytest.fixture
def na_rows():
    return json.loads((FIXTURES / "na.json").read_text())


@pytest.fixture
def completed(na_rows):
    return {
        **copy.deepcopy(na_rows[0]),
        "route_stat": "Completed",
        "seg_stat": "Completed",
    }


def adapter(**kwargs):
    return SnowClearingAdapter(None, item_factory=dict, **kwargs)


def verified_adapter(**kwargs):
    return adapter(
        **{"verified_service_time": lambda row: NOW - timedelta(hours=1), **kwargs}
    )


def test_recorded_na_feed_produces_no_items(na_rows):
    assert adapter().normalize(na_rows, NOW) == []


def test_actual_missing_status_records_are_skipped_and_logged(caplog):
    rows = json.loads((FIXTURES / "missing_status.json").read_text())
    assert adapter().normalize(rows, NOW) == []
    skips = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("SKIP ")
    ]
    assert len(skips) == len(rows)
    assert any("4ne Area 407" in message for message in skips)


def test_live_shaped_inactive_refresh_succeeds_despite_area_rows(na_rows, caplog):
    rows = na_rows + json.loads((FIXTURES / "missing_status.json").read_text())
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=rows))
    with httpx.Client(transport=transport) as client:
        service = SnowClearingAdapter(client)
        with caplog.at_level(logging.INFO):
            assert service.fetch(NOW) == []
        assert service.last_error is None
        assert service.last_success == NOW
        assert service.last_good == []
    assert "REFRESH city_snow_clearing success items=0" in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [("route_stat", None), ("seg_stat", None), ("route_stat", ""), ("seg_stat", 42)],
)
def test_statusless_row_does_not_block_valid_road(completed, field, value, caplog):
    bad = {**completed, "route_id": "bad-status-road", field: value}
    items = verified_adapter().normalize([bad, completed], NOW)
    assert len(items) == 1
    assert items[0]["source_ref"] == completed["route_id"]
    assert "row=bad-status-road" in caplog.text


def test_completed_without_timing_evidence_is_skipped(completed, caplog):
    assert adapter().normalize([completed], NOW) == []
    assert "No verified service time" in caplog.text


def test_metadata_fields_do_not_create_fake_service_evidence(completed, caplog):
    # Exercise adapter behavior instead of merely checking a metadata fixture string.
    completed.update(last_updat=NOW.isoformat(), serv_hrs="1", serv_ach="TRUE")
    assert adapter().normalize([completed], NOW) == []
    assert "No verified service time" in caplog.text


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
        assert item["geometry"] == completed["geometry_line"]
        assert item["source_ref"] == completed["route_id"]
        assert item["starts_at"] == NOW - timedelta(hours=hours)
        assert NOW < item["expires_at"] <= NOW + timedelta(minutes=30)
        assert item["expires_at"] <= item["ends_at"]


@pytest.mark.parametrize(
    "bad_time",
    [None, NOW + timedelta(seconds=6), datetime(2026, 11, 7), "2026-11-07T22:00:00Z"],
)
def test_unusable_service_time_skips_only_that_row(completed, bad_time, caplog):
    bad = {**completed, "route_id": "bad-time-road"}
    service = verified_adapter(
        verified_service_time=lambda row: (
            bad_time if row["route_id"] == "bad-time-road" else NOW
        )
    )
    items = service.normalize([bad, completed], NOW)
    assert len(items) == 1
    assert items[0]["source_ref"] == completed["route_id"]
    assert "row=bad-time-road" in caplog.text


@pytest.mark.parametrize("seconds", [1, 5])
def test_small_clock_skew_is_allowed_and_caution_is_immediately_active(
    completed, seconds
):
    items = verified_adapter(
        verified_service_time=lambda row: NOW + timedelta(seconds=seconds)
    ).normalize([completed], NOW)
    assert len(items) == 1
    assert items[0]["starts_at"] == NOW
    assert items[0]["ends_at"] == NOW + timedelta(hours=12)


def test_missing_service_time_does_not_fail_a_refresh(completed, caplog):
    missing = {**completed, "route_id": "missing-time-road"}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=[missing, completed])
    )
    with httpx.Client(transport=transport) as client:
        service = SnowClearingAdapter(
            client,
            item_factory=dict,
            verified_service_time=lambda row: (
                None if row["route_id"] == "missing-time-road" else NOW
            ),
        )
        result = service.fetch(NOW)
        assert len(result) == 1
        assert service.last_error is None
        assert service.last_success == NOW
    assert "row=missing-time-road" in caplog.text


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "LineString", "coordinates": [[-113.59, 53.43], [-113.60, 53.44]]},
        {
            "type": "MultiLineString",
            "coordinates": [
                [[-113.59, 53.43], [-113.60, 53.44]],
                [[-113.60, 53.44], [-113.61, 53.45]],
            ],
        },
    ],
)
def test_emits_road_line_geometry_without_crossing_resolver(completed, geometry):
    completed["geometry_line"] = geometry
    items = verified_adapter().normalize([completed], NOW)
    assert len(items) == 1
    assert items[0]["geometry"] == geometry
    items[0]["geometry"]["coordinates"].clear()
    assert completed["geometry_line"]["coordinates"]


@pytest.mark.parametrize(
    "geometry",
    [
        None,
        {},
        {"type": "Point", "coordinates": [-113.59, 53.43]},
        {"type": "MultiLineString", "coordinates": []},
        {"type": "LineString", "coordinates": [[-113.59, 53.43]]},
        {"type": "LineString", "coordinates": [[999, 53.43], [-113.6, 53.44]]},
    ],
)
def test_bad_road_geometry_skips_only_that_row(completed, geometry, caplog):
    bad = {**completed, "route_id": "bad-geometry-road", "geometry_line": geometry}
    items = verified_adapter().normalize([bad, completed], NOW)
    assert len(items) == 1
    assert "row=bad-geometry-road" in caplog.text


def test_one_item_per_road_with_stable_identity(completed):
    service = verified_adapter()
    items = service.normalize([completed], NOW)
    changed_fid = {**completed, "fid": "different"}
    assert service.normalize([changed_fid], NOW) == items
    assert service.normalize([completed, changed_fid], NOW) == items
    assert items[0]["id"] == service.source + ":" + completed["route_id"]


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


def test_fetch_pages_token_and_cadence(na_rows):
    requests = []

    def handler(request):
        requests.append(request)
        offset = int(request.url.params["$offset"])
        limit = int(request.url.params["$limit"])
        return httpx.Response(200, json=na_rows[offset : offset + limit])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = SnowClearingAdapter(client, app_token="test-token", page_size=2)
        assert service.fetch(NOW) == []
        assert service.cadence_s == 900
        assert service.last_good == []
        assert service.last_success == NOW
    assert [request.url.params["$offset"] for request in requests] == ["0", "2"]
    assert all(request.headers["X-App-Token"] == "test-token" for request in requests)
    assert all(request.url.params["$order"] == "route_id,fid" for request in requests)


def test_exact_multiple_of_page_size_is_a_complete_snapshot(na_rows):
    rows = na_rows[:2]
    requests = []

    def handler(request):
        requests.append(request)
        offset = int(request.url.params["$offset"])
        limit = int(request.url.params["$limit"])
        return httpx.Response(200, json=rows[offset : offset + limit])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = SnowClearingAdapter(client, page_size=2, max_pages=1)
        assert service.fetch(NOW) == []
    assert [request.url.params["$offset"] for request in requests] == ["0", "2"]
    assert requests[-1].url.params["$limit"] == "1"


@pytest.mark.parametrize(
    "failure", ["http", "timeout", "schema", "conflict", "page_limit"]
)
def test_source_failure_keeps_last_successful_snapshot(completed, failure, caplog):
    fail = False

    def handler(request):
        if fail:
            if failure == "http":
                return httpx.Response(503)
            if failure == "timeout":
                raise httpx.ReadTimeout("Source timed out")
            if failure == "schema":
                return httpx.Response(200, json={"error": "Bad schema"})
            if failure == "conflict":
                return httpx.Response(
                    200, json=[completed, {**completed, "seg_stat": "NA"}]
                )
            return httpx.Response(200, json=[completed, completed])
        return httpx.Response(200, json=[completed])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = SnowClearingAdapter(
            client,
            item_factory=dict,
            page_size=3,
            max_pages=1,
            verified_service_time=lambda row: NOW,
        )
        first = service.fetch(NOW)
        first.clear()
        assert len(service.last_good) == 1
        saved = copy.deepcopy(service.last_good)
        fail = True
        if failure == "page_limit":
            service.page_size = 2
        with pytest.raises((ValueError, httpx.HTTPError)) as error:
            service.fetch(NOW + timedelta(minutes=15))
        assert service.last_good == saved
        assert service.last_success == NOW
        assert service.last_error == str(error.value)
        assert "REFRESH city_snow_clearing failed:" in caplog.text


@pytest.mark.parametrize("failure", ["timeout", "503", "429"])
def test_retry_policy_is_left_to_scheduler(failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Source timed out")
        return httpx.Response(int(failure))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = SnowClearingAdapter(client)
        with pytest.raises(httpx.HTTPError):
            service.fetch(NOW)
    assert len(calls) == 1


def test_genuine_empty_success_clears_previous_snapshot(completed):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    with httpx.Client(transport=transport) as client:
        service = SnowClearingAdapter(client, item_factory=dict)
        service.last_good = verified_adapter().normalize([completed], NOW)
        assert service.fetch(NOW) == []
        assert service.last_good == []
        assert service.last_error is None


def test_multiline_error_message_is_kept_and_logged_as_one_line(caplog):
    def handler(request):
        raise httpx.ReadTimeout("Source\ntimed out")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = SnowClearingAdapter(client)
        with pytest.raises(httpx.ReadTimeout):
            service.fetch(NOW)
        assert service.last_error == "Source\ntimed out"
    outcomes = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("REFRESH ")
    ]
    assert len(outcomes) == 1
    assert "\n" not in outcomes[0]


def test_shared_contract_when_available(completed):
    schemas = pytest.importorskip(
        "cloud.app.schemas", reason="OP-101 is not merged yet"
    )
    service = SnowClearingAdapter(
        None, item_factory=schemas.Obstruction, verified_service_time=lambda row: NOW
    )
    assert isinstance(service, schemas.SourceAdapter)
    item = service.normalize([completed], NOW)[0]
    assert isinstance(item, schemas.Obstruction)
    assert item.geometry.type == "MultiLineString"
    assert item.effect == "caution"
