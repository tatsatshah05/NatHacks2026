"""OP-107 / PR #88: keep construction closures local and isolate bad permits.

Exercise Tatsat's review cases using recorded City data and small synthetic
amendments. Network access is always blocked in these tests.
"""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
from urllib.error import HTTPError

import pytest

from cloud.app.adapters import construction
from cloud.tests.test_construction import EDMONTON, FIXTURES, NOW, FakeReader, normalize


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not call the network")

    monkeypatch.setattr(construction, "urlopen", blocked)


@pytest.fixture
def point() -> construction.Row:
    return json.loads((FIXTURES / "pedestrian_points.json").read_text("utf-8"))[0]


def line_at_point(
    point: construction.Row, *, offset_lon: float = 0
) -> construction.Row:
    lon, lat = point["location"]["coordinates"]
    return {
        "type": "MultiLineString",
        "coordinates": [
            [[lon - 0.002 + offset_lon, lat], [lon + 0.002 + offset_lon, lat]]
        ],
    }


def test_short_line_near_segment_interior_is_used(point: construction.Row) -> None:
    # The point is over 100 m from either endpoint, but lies on the actual line.
    point["geometry_multiline"] = line_at_point(point)
    result = normalize([point])
    assert result.obstructions[0]["geometry"] == point["geometry_multiline"]
    assert not result.warnings


@pytest.mark.parametrize(
    "case", ["long", "distant", "total-length", "distant-component"]
)
def test_unsafe_corridors_use_only_city_point(
    point: construction.Row, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    line = line_at_point(point)
    if case == "long":
        line["coordinates"][0][1][0] += 0.02
    elif case == "distant":
        line = line_at_point(point, offset_lon=0.02)
    elif case == "total-length":
        line["coordinates"] *= 2
    else:
        # One short component near the point must not authorize a remote component.
        lon, lat = point["location"]["coordinates"]
        line["coordinates"].append([[lon + 0.02, lat], [lon + 0.0201, lat]])
    point["geometry_multiline"] = line
    result = normalize([point])
    assert result.obstructions[0]["geometry"] == point["location"]
    assert "used City point" in result.warnings[0]
    assert point["file_number"] in caplog.text


def test_short_line_without_permit_point_is_not_trusted(
    point: construction.Row,
) -> None:
    point["geometry_multiline"] = line_at_point(point)
    for field in ("location", "latitude", "longitude"):
        point.pop(field, None)
    result = normalize([point])
    assert not result.obstructions
    assert any("cannot be checked" in warning for warning in result.warnings)


def test_repeated_coordinates_do_not_divide_by_zero(point: construction.Row) -> None:
    position = point["location"]["coordinates"]
    point["geometry_multiline"] = {
        "type": "MultiLineString",
        "coordinates": [[position, position]],
    }
    assert normalize([point]).obstructions


def test_amended_dates_keep_longer_closure_in_either_order(
    point: construction.Row,
) -> None:
    changed = deepcopy(point)
    changed["start_date"] = "2026-11-01"
    changed["finish_date"] = "2027-01-31"
    changed["work_reason"] = "Amended permit"
    expected = normalize([changed]).obstructions
    for rows in ([point, changed], [changed, point]):
        result = normalize(rows)
        assert result.obstructions == expected
        assert len(result.warnings) == 1


@pytest.mark.parametrize(
    "defect", ["object", "date", "id", "flag", "point", "overflow"]
)
def test_malformed_row_never_discards_valid_neighbor(
    point: construction.Row, defect: str, caplog: pytest.LogCaptureFixture
) -> None:
    bad = deepcopy(point)
    bad["file_number"] = "BROKEN"
    if defect == "object":
        bad = None
    elif defect == "date":
        bad["finish_date"] = "invalid"
    elif defect == "id":
        bad.pop("file_number")
    elif defect == "flag":
        bad.pop("pedestrian_affected")
    elif defect == "point":
        bad["location"]["coordinates"] = [False, 53]
    else:
        bad["finish_date"] = "9999-12-31"
    result = normalize([bad, point])
    assert result.obstructions == normalize([point]).obstructions
    assert result.warnings
    assert "row skipped" in caplog.text


def test_partial_fetch_updates_good_permits_without_renewing_bad_ones(
    point: construction.Row,
) -> None:
    reader = FakeReader([point])
    adapter = construction.ConstructionAdapter(reader, local_timezone=EDMONTON)
    saved = adapter.fetch(now=NOW).obstructions[0]
    bad = deepcopy(point)
    bad["finish_date"] = "invalid"
    new = deepcopy(point)
    new["file_number"] = "NEW-PERMIT"
    reader.rows = [bad, new]
    later = NOW + timedelta(minutes=1)
    result = adapter.fetch(now=later)
    old = next(item for item in result.obstructions if item["id"] == saved["id"])
    assert old == saved
    fresh = next(
        item for item in result.obstructions if item["source_ref"].startswith("NEW-")
    )
    assert fresh["observed_at"] == later.isoformat().replace("+00:00", "Z")
    assert adapter.last_error
    reader.rows = [point, new]
    assert not adapter.fetch(now=later + timedelta(seconds=1)).warnings
    assert adapter.last_error is None


def test_unidentified_bad_row_preserves_saved_records_without_renewal(
    point: construction.Row,
) -> None:
    reader = FakeReader([point])
    adapter = construction.ConstructionAdapter(reader, local_timezone=EDMONTON)
    saved = adapter.fetch(now=NOW).obstructions
    reader.rows = [None]
    assert adapter.fetch(now=NOW + timedelta(minutes=1)).obstructions == saved
    reader.rows = []
    assert not adapter.fetch(now=NOW + timedelta(minutes=2)).obstructions


def test_http_page_can_contain_one_bad_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(construction, "urlopen", lambda *a, **k: BytesIO(b"[null]"))
    rows = construction.ConstructionReader().read(NOW.date())
    assert rows == [None]
    assert normalize(rows).warnings


def test_rate_limit_does_not_retry_or_delay_next_scheduler_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def failed(*args: object, **kwargs: object) -> None:
        calls.append(True)
        raise HTTPError("x", 429, "rate limit", {"Retry-After": "30"}, None)

    monkeypatch.setattr(construction, "urlopen", failed)
    adapter = construction.ConstructionAdapter(
        construction.ConstructionReader(), local_timezone=EDMONTON
    )
    for _ in range(2):
        with pytest.raises(construction.FeedError, match="HTTP 429"):
            adapter.fetch(now=NOW)
    assert len(calls) == 2


@pytest.mark.parametrize("at", ["2026-11-07T19:00:00", "not-a-date"])
def test_cli_bad_time_is_usage_error_without_traceback(
    at: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["construction", "--fixtures", str(FIXTURES), "--at", at]
    )
    with pytest.raises(SystemExit) as error:
        construction.main()
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "timezone" in output
    assert "Traceback" not in output


def test_cli_unknown_timezone_is_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "construction",
            "--fixtures",
            str(FIXTURES),
            "--local-timezone",
            "Invalid/Zone",
        ],
    )
    with pytest.raises(SystemExit) as error:
        construction.main()
    assert error.value.code == 2


def test_recorded_fixture_manifest_has_no_duplicate_payloads() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
    hashes = []
    for entry in manifest:
        payload = (FIXTURES / entry["file"]).read_text("utf-8").replace("\r\n", "\n")
        digest = hashlib.sha256(payload.encode()).hexdigest()
        assert digest == entry["sha256_lf"]
        hashes.append(digest)
    assert len(hashes) == len(set(hashes))
