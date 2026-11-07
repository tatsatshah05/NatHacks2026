"""Offline coverage of OP-110 using recorded City responses and explicit mutations."""

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from cloud.app.adapters.city_advisories import (
    SnowComplaintsAdapter,
    TrailClosuresAdapter,
    city_time,
    end_time,
    iso,
)

FIXTURES = Path(__file__).parent / "fixtures/city_advisories"
NOW = datetime(2026, 11, 7, 21, 30, tzinfo=timezone.utc)


@pytest.fixture
def records():
    return (
        json.loads((FIXTURES / "snow.json").read_text()),
        json.loads((FIXTURES / "trails.json").read_text()),
    )


def test_recorded_open_complaint_is_an_area_advisory(records):
    snow, _ = records
    reference = city_time(snow[0]["date_created"]) + timedelta(hours=1)
    items = SnowComplaintsAdapter(None).normalize(snow, reference)
    assert len(items) == 1
    item = items[0]
    assert item["source_ref"] == snow[0]["neighbourhood_id"]
    assert item["geometry"] == snow[0]["nbhd_location"]
    assert item["effect"] == "caution"
    assert item["kind"] == "snow_complaints"
    assert item["confidence"] == "low"
    assert "area, not exact location" in item["detail"].lower()
    assert "1 open" in item["detail"]


def test_groups_neighbourhoods_and_deduplicates_request_ids(records):
    row = records[0][0]
    reference = city_time(row["date_created"]) + timedelta(hours=1)
    second = {**row, "row_id": "second"}
    other = {
        **row,
        "row_id": "other",
        "neighbourhood_id": "other",
        "neighbourhood": "Other",
    }
    items = SnowComplaintsAdapter(None).normalize([row, row, second, other], reference)
    assert len(items) == 2
    assert "2 open" in items[0]["detail"]
    assert "1 open" in items[1]["detail"]
    assert items[0]["id"] != items[1]["id"]


@pytest.mark.parametrize("hours,expected", [(0, 1), (72, 1), (72.001, 0), (-1, 0)])
def test_72_hour_window_and_future_complaints(records, hours, expected):
    row = records[0][0]
    now = city_time(row["date_created"]) + timedelta(hours=hours)
    assert len(SnowComplaintsAdapter(None).normalize([row], now)) == expected


def test_closed_complaints_are_excluded(records):
    row = {**records[0][0], "request_status": "Closed"}
    assert SnowComplaintsAdapter(None).normalize([row], NOW) == []


def test_local_dates_convert_to_utc_and_dst_is_explicit():
    assert iso(city_time("2026-11-07T12:00:00.000")) == "2026-11-07T18:00:00Z"
    assert iso(city_time("2026-07-07T12:00:00.000")) == "2026-07-07T18:00:00Z"
    assert iso(city_time("2026-11-07T12:00:00Z")) == "2026-11-07T12:00:00Z"
    for ambiguous in ("2025-11-02T01:30:00", "2026-03-08T02:30:00"):
        with pytest.raises(ValueError, match="Ambiguous"):
            city_time(ambiguous)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("November 27th 2026", "2026-11-28T06:00:00Z"),
        ("2026-11-27", "2026-11-28T06:00:00Z"),
        ("November 1, 2026", "2026-11-02T06:00:00Z"),
        ("End of December 2026.", "2027-01-01T06:00:00Z"),
        ("March 2027", "2027-04-01T06:00:00Z"),
        ("Until Further Notice", None),
        ("1 Year", None),
        ("Spring 2027", None),
    ],
)
def test_trail_end_dates_are_inclusive_and_vague_dates_remain_unknown(value, expected):
    result = end_time(value)
    assert (iso(result) if result else None) == expected


def test_recorded_trails_map_structured_types_and_skip_expired(records):
    _, trails = records
    items = TrailClosuresAdapter(None).normalize(trails, NOW)
    by_id = {item["source_ref"]: item for item in items}
    assert by_id["1"]["effect"] == "blocked"  # Permanent
    assert by_id["565"]["effect"] == "blocked"  # Temporary
    assert by_id["544"]["effect"] == "caution"
    assert by_id["544"]["kind"] == "trail_caution"
    assert "477" not in by_id  # Definite end: November 1, 2026
    assert "488" not in by_id  # Expired, missing closure type
    assert by_id["1"]["confidence"] == "high"
    assert by_id["1"]["observed_at"] == iso(NOW)
    assert by_id["9"]["starts_at"] == "2011-07-18T00:00:00Z"


def test_future_trail_is_not_active_and_current_day_end_is_inclusive(records):
    row = {**records[1][0], "start_date": "2026-11-08T00:00:00"}
    assert TrailClosuresAdapter(None).normalize([row], NOW) == []
    row["start_date"] = "2026-11-01T00:00:00"
    row["end_date"] = "November 7, 2026"
    assert len(TrailClosuresAdapter(None).normalize([row], NOW)) == 1


@pytest.mark.parametrize(
    "change",
    [
        "missing_geometry",
        "bad_coordinates",
        "missing_type",
        "unknown_type",
        "duplicate_conflict",
    ],
)
def test_invalid_active_trail_fails_instead_of_clearing(records, change):
    row = copy.deepcopy(records[1][0])
    rows = [row]
    if change == "missing_geometry":
        del row["geometry_point"]
    elif change == "bad_coordinates":
        row["geometry_point"]["coordinates"] = [200, 53]
    elif change == "missing_type":
        del row["type_of_closure"]
    elif change == "unknown_type":
        row["type_of_closure"] = "Unknown"
    else:
        rows.append({**row, "details": "Different details"})
    with pytest.raises(ValueError):
        TrailClosuresAdapter(None).normalize(rows, NOW)


def test_invalid_neighbourhood_geometry_fails(records):
    row = copy.deepcopy(records[0][0])
    reference = city_time(row["date_created"]) + timedelta(hours=1)
    del row["nbhd_location"]
    with pytest.raises(ValueError):
        SnowComplaintsAdapter(None).normalize([row], reference)


def test_planned_fields_and_refresh_cadences(records):
    fields = {
        "id",
        "source",
        "source_ref",
        "category",
        "kind",
        "effect",
        "title",
        "detail",
        "geometry",
        "confidence",
        "starts_at",
        "ends_at",
        "observed_at",
        "expires_at",
        "url",
        "attribution",
    }
    assert SnowComplaintsAdapter.poll_interval_seconds == 3600
    assert TrailClosuresAdapter.poll_interval_seconds == 900
    for item in TrailClosuresAdapter(None).normalize(records[1], NOW):
        assert set(item) == fields
        assert city_time(item["expires_at"]) <= NOW + timedelta(minutes=30)


def test_pagination_token_and_filters_use_real_field_names(records):
    requests = []
    rows = records[1][:3]

    def handler(request):
        requests.append(request)
        offset = int(request.url.params["$offset"])
        return httpx.Response(200, json=rows[offset : offset + 2])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = TrailClosuresAdapter(
                client, app_token="test-only-token", page_size=2
            )
            items = await adapter.fetch(NOW)
            assert len(items) == 3
            assert len(requests) == 2
            assert [r.url.params["$offset"] for r in requests] == ["0", "2"]
            assert all(r.headers["X-App-Token"] == "test-only-token" for r in requests)
            assert all(r.url.params["$order"] == "id_number" for r in requests)
            items.clear()
            assert len(adapter.last_good) == 3

    asyncio.run(run())
    where = SnowComplaintsAdapter(None).query(NOW)["$where"]
    assert "upper(request_status) = 'OPEN'" in where
    assert "2026-11-04T15:30:00" in where
    assert "2026-11-07T15:30:00" in where


@pytest.mark.parametrize("failure", ["http", "schema", "row", "page_limit", "timeout"])
def test_failed_refresh_preserves_entire_previous_snapshot(records, failure):
    state = {"fail": False}
    row = records[1][0]

    def handler(request):
        if state["fail"]:
            if failure == "http":
                return httpx.Response(503)
            if failure == "schema":
                return httpx.Response(200, json={"error": "schema changed"})
            if failure == "row":
                return httpx.Response(
                    200, json=[{**row, "type_of_closure": "unexpected"}]
                )
            if failure == "timeout":
                raise httpx.ReadTimeout("Timed out")
            return httpx.Response(200, json=[row, row])
        return httpx.Response(200, json=[row])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = TrailClosuresAdapter(client, page_size=2, max_pages=1, retries=0)
            old = await adapter.fetch(NOW)
            state["fail"] = True
            with pytest.raises((httpx.HTTPError, ValueError)):
                await adapter.fetch(NOW + timedelta(minutes=15))
            assert adapter.last_good == old
            assert adapter.last_success == NOW
            assert adapter.last_error
            state["fail"] = False
            assert await adapter.fetch(NOW + timedelta(minutes=15))
            assert adapter.last_error is None

    asyncio.run(run())


def test_three_retries_with_backoff_and_empty_success_clears_snapshot(records):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) <= 3 else httpx.Response(200, json=[])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = TrailClosuresAdapter(client)
            adapter.last_good = adapter.normalize(records[1][:1], NOW)
            with patch(
                "cloud.app.adapters.city_advisories.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep:
                assert await adapter.fetch(NOW) == []
                assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4]
            assert adapter.last_good == []
            assert len(calls) == 4

    asyncio.run(run())
