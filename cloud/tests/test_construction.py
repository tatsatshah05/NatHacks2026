"""OP-107: Construction sidewalk closures adapter (issue #35, PR #88).

Check which permits block sidewalks and how failures preserve saved data.

Use recorded City responses and small edited copies for edge cases. Every
test blocks network access so results do not depend on the live City feed.
"""

from __future__ import annotations

import json
import math
import sys
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from cloud.app.adapters import construction

FIXTURES = Path(__file__).parent / "fixtures" / "construction"
EDMONTON = ZoneInfo("America/Edmonton")
NOW = datetime(2026, 11, 7, 12, tzinfo=EDMONTON).astimezone(UTC)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not call the network")

    monkeypatch.setattr(construction, "urlopen", blocked)


@pytest.fixture
def samples() -> dict[str, list[construction.Row]]:
    manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
    return {
        entry["file"]: json.loads((FIXTURES / entry["file"]).read_text("utf-8"))
        for entry in manifest
    }


@pytest.fixture
def point(samples: dict[str, list[construction.Row]]) -> construction.Row:
    return deepcopy(samples["pedestrian_points.json"][0])


def normalize(
    rows: list[construction.Row], now: datetime = NOW
) -> construction.NormalizedPermits:
    return construction.normalize_permits(
        rows, observed_at=now, local_timezone=EDMONTON
    )


def test_recorded_18km_corridor_uses_permit_points(
    samples: dict[str, list[construction.Row]],
) -> None:
    rows = samples["pedestrian_lines.json"]
    result = normalize(rows)
    # The first two recorded rows describe the same segment; the third is a
    # different street under the same permit. Identical segments collapse.
    assert len(result.obstructions) == 2
    assert len(result.warnings) == 1
    assert "18165 m" in result.warnings[0]
    assert "not under 500 m" in result.warnings[0]
    assert "used City point" in result.warnings[0]
    assert result.skipped == 0
    for item in result.obstructions:
        assert item["geometry"] in [row["location"] for row in rows]
        assert item["geometry"]["type"] == "Point"
        assert item["category"] == "sidewalk"
        assert item["kind"] == "sidewalk_closed"
        assert item["effect"] == "blocked"
        assert item["confidence"] == "high"
        assert item["url"] == "https://data.edmonton.ca/d/7wiq-4rgy"
        assert item["attribution"] == "City of Edmonton Open Data"


def line_north_of(point: construction.Row, offset_m: float) -> construction.Row:
    """Build a 200 m east-west line that passes `offset_m` north of the point."""
    lon, lat = point["location"]["coordinates"]
    lat += offset_m * 180 / (math.pi * construction.EARTH_RADIUS_M)
    return {
        "type": "MultiLineString",
        "coordinates": [[[lon - 0.0015, lat], [lon + 0.0015, lat]]],
    }


@pytest.mark.parametrize(("offset_m", "kept"), [(0, True), (40, True), (60, False)])
def test_short_line_is_kept_only_within_50_m_of_permit_point(
    point: construction.Row, offset_m: float, kept: bool
) -> None:
    point["geometry_multiline"] = line_north_of(point, offset_m)
    result = normalize([point])
    item = result.obstructions[0]
    if kept:
        assert item["geometry"] == point["geometry_multiline"]
        assert not result.warnings
    else:
        assert item["geometry"] == point["location"]
        assert "not within 50 m" in result.warnings[0]
        assert "used City point" in result.warnings[0]
    assert result.skipped == 0


def test_recorded_point_fallback(point: construction.Row) -> None:
    item = normalize([point]).obstructions[0]
    assert item["geometry"] == point["location"]
    assert point["file_number"] in item["source_ref"]
    assert point["file_number"] in item["detail"]
    assert point["street_full_name"] in item["title"]


def test_latitude_longitude_fallback(point: construction.Row) -> None:
    del point["location"]
    assert normalize([point]).obstructions[0]["geometry"] == {
        "type": "Point",
        "coordinates": [float(point["longitude"]), float(point["latitude"])],
    }


def test_real_permit_segments_are_not_collapsed_and_ids_ignore_row_order(
    samples: dict[str, list[construction.Row]],
) -> None:
    rows = samples["pedestrian_lines.json"]
    assert len({row["file_number"] for row in rows}) == 1
    first = normalize(rows)
    assert len({item["source_ref"] for item in first.obstructions}) == 2
    assert normalize(list(reversed(rows))) == first
    assert normalize(rows + deepcopy(rows)) == first


def test_id_survives_permit_date_and_reason_edits(point: construction.Row) -> None:
    original = normalize([point]).obstructions[0]["id"]
    point["finish_date"] = "2028-01-01T00:00:00"
    point["work_reason"] = "Updated reason"
    assert normalize([point]).obstructions[0]["id"] == original


@pytest.mark.parametrize("flag", ["No", "Unknown", None, ""])
def test_only_explicit_pedestrian_yes_creates_a_block(
    point: construction.Row, flag: str | None
) -> None:
    point["pedestrian_affected"] = flag
    assert normalize([point]).obstructions == []


def test_non_pedestrian_recorded_permits_are_skipped(
    samples: dict[str, list[construction.Row]],
) -> None:
    assert normalize(samples["other_permits.json"]).obstructions == []


@pytest.mark.parametrize(
    ("start", "finish", "included"),
    [
        ("2026-11-07", "2026-11-07", True),
        ("2026-11-06", "2026-11-07", True),
        ("2026-11-07", "2026-11-08", True),
        ("2026-11-08", "2026-11-09", False),
        ("2026-11-01", "2026-11-06", False),
    ],
)
def test_active_dates_are_inclusive(
    point: construction.Row, start: str, finish: str, included: bool
) -> None:
    point["start_date"], point["finish_date"] = start, finish
    assert bool(normalize([point]).obstructions) is included


def test_active_day_is_edmonton_day_instead_of_utc_day(point: construction.Row) -> None:
    point["start_date"] = point["finish_date"] = "2024-01-02"
    assert normalize([point], datetime(2024, 1, 3, 6, 59, tzinfo=UTC)).obstructions
    assert normalize([point], datetime(2024, 1, 3, 7, 0, tzinfo=UTC)).obstructions == []


def test_expiry_is_capped_at_end_of_finish_day(point: construction.Row) -> None:
    point["start_date"] = point["finish_date"] = "2024-01-02"
    item = normalize([point], datetime(2024, 1, 3, 6, 59, tzinfo=UTC)).obstructions[0]
    assert item["starts_at"] == "2024-01-02T07:00:00Z"
    assert item["ends_at"] == "2024-01-03T07:00:00Z"
    assert item["expires_at"] == item["ends_at"]


def test_observation_and_lifetime_use_utc(point: construction.Row) -> None:
    item = normalize([point]).obstructions[0]
    assert item["observed_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert datetime.fromisoformat(item["expires_at"]) - NOW == timedelta(hours=3)


@pytest.mark.parametrize("field", ["start_date", "finish_date", "file_number"])
def test_missing_required_field_is_skipped(point: construction.Row, field: str) -> None:
    del point[field]
    result = normalize([point])
    assert not result.obstructions
    assert field in result.warnings[0]


@pytest.mark.parametrize("field", ["start_date", "finish_date"])
def test_invalid_dates_are_skipped(point: construction.Row, field: str) -> None:
    point[field] = "not-a-date"
    result = normalize([point])
    assert not result.obstructions
    assert field in result.warnings[0]


def test_reversed_dates_are_skipped(point: construction.Row) -> None:
    point["start_date"], point["finish_date"] = "2026-11-08", "2026-11-07"
    result = normalize([point])
    assert not result.obstructions
    assert "precedes" in result.warnings[0]


@pytest.mark.parametrize(
    "geometry",
    [
        {},
        {"type": "LineString", "coordinates": [[-113, 53], [-113.1, 53.1]]},
        {"type": "MultiLineString", "coordinates": []},
        {"type": "MultiLineString", "coordinates": [[[-113, 53]]]},
        {"type": "MultiLineString", "coordinates": [[[181, 53], [-113, 53]]]},
    ],
)
def test_malformed_line_uses_point_with_visible_warning(
    point: construction.Row, geometry: construction.Row
) -> None:
    point["geometry_multiline"] = geometry
    result = normalize([point])
    assert result.obstructions[0]["geometry"] == point["location"]
    assert "used City point" in result.warnings[0]


@pytest.mark.parametrize(
    "coordinates", [[float("nan"), 53], [-113, 91], [False, 53], [1]]
)
def test_bad_point_geometry_is_skipped(
    point: construction.Row, coordinates: list[object]
) -> None:
    point["location"]["coordinates"] = coordinates
    result = normalize([point])
    assert not result.obstructions
    assert "coordinate" in result.warnings[0]


def test_recorded_unlocated_permits_produce_visible_diagnostics(
    samples: dict[str, list[construction.Row]],
) -> None:
    rows = samples["unlocated_permits.json"]
    result = normalize(rows)
    assert not result.obstructions
    assert len(result.warnings) == len(rows)
    assert result.skipped == len(rows)
    for row in rows:
        assert any(row["file_number"] in warning for warning in result.warnings)


def test_conflicting_duplicate_keeps_fuller_reason_and_logs(
    point: construction.Row, caplog: pytest.LogCaptureFixture
) -> None:
    changed = deepcopy(point)
    changed["work_reason"] = "Updated construction reason with more permit detail"
    result = normalize([point, changed])
    assert len(result.obstructions) == 1
    assert changed["work_reason"] in result.obstructions[0]["detail"]
    assert "conflicting duplicate" in result.warnings[0]
    assert "conflicting duplicate" in caplog.text
    assert normalize([changed, point]) == result


def test_conflicting_duplicate_keeps_later_finish_then_later_start(
    point: construction.Row, caplog: pytest.LogCaptureFixture
) -> None:
    later_finish = deepcopy(point)
    later_finish["finish_date"] = "2027-03-31"
    later_start = deepcopy(point)
    later_start["start_date"] = "2026-06-01"
    for rows, winner in (
        ([point, later_finish], later_finish),
        ([later_finish, point], later_finish),
        ([point, later_start], later_start),
        ([later_start, point], later_start),
    ):
        result = normalize(rows)
        assert result.obstructions == normalize([winner]).obstructions
        assert result.skipped == 0
        assert "conflicting duplicate" in result.warnings[0]
    assert "conflicting duplicate" in caplog.text


@pytest.mark.parametrize("defect", ["three-coordinates", "missing-file-number"])
def test_one_bad_row_is_skipped_and_counted_while_the_rest_are_returned(
    samples: dict[str, list[construction.Row]],
    defect: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    rows = deepcopy(samples["pedestrian_points.json"])
    if defect == "three-coordinates":
        rows[1]["location"]["coordinates"] = [0, 0, 0]
    else:
        del rows[1]["file_number"]
    result = normalize(rows)
    assert result.obstructions == normalize([rows[0], rows[2]]).obstructions
    assert len(result.obstructions) == 2
    assert result.skipped == 1
    assert len(result.warnings) == 1
    assert "row skipped" in result.warnings[0]
    assert "row skipped" in caplog.text


def test_naive_time_rejected(point: construction.Row) -> None:
    with pytest.raises(ValueError, match="timezone"):
        normalize([point], NOW.replace(tzinfo=None))


class FakeReader:
    def __init__(self, rows: list[construction.Row]) -> None:
        self.rows = rows
        self.calls: list[date] = []
        self.fail = False

    def read(self, today: date) -> list[construction.Row]:
        self.calls.append(today)
        if self.fail:
            raise construction.FeedError("Simulated unavailable source")
        return deepcopy(self.rows)


def test_every_call_fetches_and_last_good_is_not_mutable(
    point: construction.Row,
) -> None:
    reader = FakeReader([point])
    adapter = construction.ConstructionAdapter(reader, local_timezone=EDMONTON)
    result = adapter.fetch(now=NOW)
    result.obstructions[0]["geometry"]["coordinates"][0] = 0
    cached = adapter.last_good
    assert len(reader.calls) == 1
    assert cached.obstructions[0]["geometry"]["coordinates"][0] != 0
    adapter.fetch(now=NOW + timedelta(seconds=1))
    assert len(reader.calls) == 2


def test_day_rollover_does_not_reuse_yesterdays_permits(
    point: construction.Row,
) -> None:
    point["start_date"] = point["finish_date"] = "2024-01-02"
    reader = FakeReader([point])
    adapter = construction.ConstructionAdapter(reader, local_timezone=EDMONTON)
    assert adapter.fetch(now=datetime(2024, 1, 3, 6, 59, tzinfo=UTC)).obstructions
    assert not adapter.fetch(now=datetime(2024, 1, 3, 7, 1, tzinfo=UTC)).obstructions
    assert reader.calls == [date(2024, 1, 2), date(2024, 1, 3)]


def test_failed_fetch_keeps_last_good_and_last_success(point: construction.Row) -> None:
    reader = FakeReader([point])
    adapter = construction.ConstructionAdapter(reader, local_timezone=EDMONTON)
    initial = adapter.fetch(now=NOW)
    reader.fail = True
    with pytest.raises(construction.FeedError):
        adapter.fetch(now=NOW + timedelta(hours=1))
    assert adapter.last_good == initial
    assert adapter.last_success == NOW
    assert adapter.last_error == "Simulated unavailable source"
    reader.fail = False
    adapter.fetch(now=NOW + timedelta(hours=1, minutes=1))
    assert adapter.last_error is None


def test_successful_empty_feed_clears_old_permits(point: construction.Row) -> None:
    reader = FakeReader([point])
    adapter = construction.ConstructionAdapter(reader, local_timezone=EDMONTON)
    assert adapter.fetch(now=NOW).obstructions
    reader.rows = []
    assert not adapter.fetch(now=NOW + timedelta(hours=1)).obstructions


@pytest.mark.parametrize("failure", ["missing-flag", "missing-date", "unlocated"])
def test_bad_row_keeps_previous_data_without_failing_snapshot(
    point: construction.Row, failure: str
) -> None:
    reader = FakeReader([point])
    adapter = construction.ConstructionAdapter(reader, local_timezone=EDMONTON)
    initial = adapter.fetch(now=NOW)
    if failure == "missing-flag":
        del reader.rows[0]["pedestrian_affected"]
    elif failure == "missing-date":
        del reader.rows[0]["start_date"]
    else:
        for field in ("location", "latitude", "longitude"):
            reader.rows[0].pop(field, None)
    result = adapter.fetch(now=NOW + timedelta(hours=1))
    assert result.obstructions == initial.obstructions
    assert result.warnings
    assert adapter.last_success == NOW + timedelta(hours=1)
    assert adapter.last_error


def test_http_filter_pagination_token_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[construction.Request, float]] = []
    pages = [[{"file_number": "A"}, {"file_number": "B"}], [{"file_number": "C"}]]

    def fake_open(request: construction.Request, timeout: float) -> BytesIO:
        requests.append((request, timeout))
        return BytesIO(json.dumps(pages.pop(0)).encode())

    monkeypatch.setattr(construction, "urlopen", fake_open)
    result = construction.ConstructionReader("test-token", page_size=2).read(
        date(2026, 11, 7)
    )
    assert len(result) == 3
    for index, (request, timeout) in enumerate(requests):
        query = parse_qs(urlsplit(request.full_url).query)
        assert query["$where"] == [
            "pedestrian_affected = 'Yes' AND start_date < '2026-11-08T00:00:00' AND finish_date >= '2026-11-07T00:00:00'"
        ]
        assert query["$offset"] == [str(index * 2)]
        assert query["$order"] == ["file_number,street_full_name,:id"]
        assert request.get_header("X-app-token") == "test-token"
        assert timeout == 10


@pytest.mark.parametrize(
    "error",
    [
        URLError("offline"),
        TimeoutError(),
        IncompleteRead(b"partial"),
        HTTPError("x", 503, "down", {}, None),
    ],
)
def test_transient_errors_return_immediately_for_scheduler_retry(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    calls: list[bool] = []

    def failed_open(*args: object, **kwargs: object) -> None:
        calls.append(True)
        raise error

    monkeypatch.setattr(construction, "urlopen", failed_open)
    with pytest.raises(construction.FeedError):
        construction.ConstructionReader().read(NOW.date())
    assert len(calls) == 1


def test_permanent_http_error_fails_without_retry_or_token_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_open(*args: object, **kwargs: object) -> None:
        raise HTTPError("x", 403, "Forbidden", {}, None)

    monkeypatch.setattr(construction, "urlopen", failed_open)
    with pytest.raises(construction.FeedError, match="HTTP 403") as error:
        construction.ConstructionReader("secret-token").read(NOW.date())
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize("payload", [b"not-json", b'{"error":"bad feed"}'])
def test_bad_json_or_envelope_fails(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    monkeypatch.setattr(construction, "urlopen", lambda *a, **k: BytesIO(payload))
    with pytest.raises(construction.FeedError):
        construction.ConstructionReader().read(NOW.date())


def test_offline_cli_labels_replay_and_reports_unlocated_permits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["construction", "--fixtures", str(FIXTURES), "--at", NOW.isoformat()],
    )
    construction.main()
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "replay"
    assert result["calendar_timezone"] == "America/Edmonton"
    assert result["obstructions"]
    assert result["warnings"]
    # The three recorded permits without any map location are skipped.
    assert result["skipped"] == 3


def test_live_cli_rejects_historical_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["construction", "--live", "--at", NOW.isoformat()]
    )
    with pytest.raises(SystemExit) as error:
        construction.main()
    assert error.value.code == 2
