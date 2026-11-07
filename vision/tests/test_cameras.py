"""Tests for the 511 Alberta camera adapter (OP-112). No network calls."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from vision.cameras import (
    API_URL,
    CameraAdapter,
    RateLimiter,
    count_in_edmonton,
    load_boundary,
    main,
    parse_cameras,
    point_in_polygons,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = json.loads((FIXTURES / "alberta511_cameras_sample.json").read_text())
SECRET_KEY = "test-key-do-not-leak-123"
FIXED_NOW = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)

# Synthetic test boundary (lng, lat): a box over central Edmonton.
TEST_BOUNDARY = [
    [
        [
            [-113.70, 53.45],
            [-113.40, 53.45],
            [-113.40, 53.62],
            [-113.70, 53.62],
            [-113.70, 53.45],
        ]
    ]
]


class FakeClock:
    def __init__(self) -> None:
        self.now_s = 0.0
        self.slept_s: list[float] = []

    def __call__(self) -> float:
        return self.now_s

    def sleep(self, seconds: float) -> None:
        self.slept_s.append(seconds)
        self.now_s += seconds


def make_adapter(
    handler, cache_path: Path | None = None, limiter: RateLimiter | None = None
):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return CameraAdapter(
        key=SECRET_KEY,
        client=client,
        limiter=limiter or RateLimiter(clock=FakeClock(), sleep=lambda s: None),
        cache_path=cache_path,
        now=lambda: FIXED_NOW,
    )


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=SAMPLE)


# -- parsing ------------------------------------------------------------------


def test_parses_documented_fields():
    cameras = parse_cameras(SAMPLE)
    assert len(cameras) == 5
    first = cameras[0]
    assert first.id == 9001
    assert first.roadway == "Anthony Henday Dr"
    assert (first.latitude, first.longitude) == (53.4815, -113.6302)
    assert [v.status for v in first.views] == ["Enabled", "Disabled"]


def test_request_uses_documented_endpoint_and_params():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SAMPLE)

    make_adapter(handler).refresh()
    assert str(seen[0].url).startswith(API_URL)
    assert seen[0].url.params["key"] == SECRET_KEY
    assert seen[0].url.params["format"] == "json"


# -- counting -----------------------------------------------------------------


def test_count_with_boundary_is_exact():
    count = count_in_edmonton(parse_cameras(SAMPLE), TEST_BOUNDARY)
    assert count.method == "boundary"
    assert count.total == 5
    assert count.in_edmonton == 2  # cameras A and B
    assert count.enabled_views_in_edmonton == 2  # A has one enabled view, B has one


def test_count_without_boundary_is_labelled_approximate_and_over_counts():
    count = count_in_edmonton(parse_cameras(SAMPLE))
    assert count.method == "bbox_approximate"
    assert count.in_edmonton == 4  # the box also takes in D and E


def test_point_in_polygon_respects_holes():
    outer = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
    hole = [[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]]
    polygons = [[outer, hole]]
    assert point_in_polygons(lat=2.0, lng=2.0, polygons=polygons)
    assert not point_in_polygons(lat=5.0, lng=5.0, polygons=polygons)
    assert not point_in_polygons(lat=11.0, lng=5.0, polygons=polygons)


def test_load_boundary_reads_feature_collection_and_multipolygon(tmp_path: Path):
    path = tmp_path / "boundary.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": TEST_BOUNDARY[0],
                        },
                    },
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "MultiPolygon",
                            "coordinates": [TEST_BOUNDARY[0]],
                        },
                    },
                ],
            }
        )
    )
    assert len(load_boundary(path)) == 2


# -- rate limiting ------------------------------------------------------------


def test_rate_limiter_allows_ten_calls_then_waits_out_the_window():
    clock = FakeClock()
    limiter = RateLimiter(max_calls=10, period_s=60.0, clock=clock, sleep=clock.sleep)
    for _ in range(10):
        assert limiter.acquire() == 0.0
    assert limiter.acquire() == pytest.approx(60.0)
    assert clock.slept_s == [pytest.approx(60.0)]


def test_rate_limiter_never_exceeds_limit_in_any_window():
    clock = FakeClock()
    limiter = RateLimiter(max_calls=10, period_s=60.0, clock=clock, sleep=clock.sleep)
    times: list[float] = []
    for _ in range(35):
        limiter.acquire()
        times.append(clock.now_s)
        clock.now_s += 1.0  # callers come back every second
    for t in times:
        assert sum(1 for u in times if t <= u < t + 60.0) <= 10


# -- reliability --------------------------------------------------------------


def test_failed_fetch_keeps_last_good_list_and_marks_stale():
    responses = iter([httpx.Response(200, json=SAMPLE), httpx.Response(503)])
    adapter = make_adapter(lambda request: next(responses))

    good = adapter.refresh()
    assert not good.stale and len(good.cameras) == 5

    failed = adapter.refresh()
    assert failed.stale
    assert len(failed.cameras) == 5
    assert failed.fetched_at == FIXED_NOW
    assert "503" in (failed.error or "")


def test_network_error_keeps_last_good_list():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=SAMPLE)
        raise httpx.ConnectTimeout("timed out", request=request)

    adapter = make_adapter(handler)
    adapter.refresh()
    snapshot = adapter.refresh()
    assert snapshot.stale and len(snapshot.cameras) == 5


def test_changed_schema_is_rejected_and_keeps_last_good_list():
    broken = [{k: v for k, v in camera.items() if k != "Latitude"} for camera in SAMPLE]
    responses = iter(
        [httpx.Response(200, json=SAMPLE), httpx.Response(200, json=broken)]
    )
    adapter = make_adapter(lambda request: next(responses))

    adapter.refresh()
    snapshot = adapter.refresh()
    assert snapshot.stale
    assert len(snapshot.cameras) == 5
    assert "schema changed" in (snapshot.error or "")


def test_first_fetch_failing_returns_empty_stale_snapshot():
    snapshot = make_adapter(lambda request: httpx.Response(500)).refresh()
    assert snapshot.stale and snapshot.cameras == [] and snapshot.fetched_at is None


def test_api_key_never_appears_in_errors_or_logs(caplog: pytest.LogCaptureFixture):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed to reach {request.url}", request=request)

    caplog.set_level(logging.DEBUG, logger="vision.cameras")
    snapshot = make_adapter(handler).refresh()
    assert SECRET_KEY not in (snapshot.error or "")
    assert SECRET_KEY not in caplog.text


def test_last_good_list_survives_a_restart_through_the_cache(tmp_path: Path):
    cache = tmp_path / "cameras.json"
    make_adapter(ok_handler, cache_path=cache).refresh()

    cached = json.loads(cache.read_text())
    assert set(cached) == {"fetched_at", "cameras"}  # metadata only, never images

    restarted = make_adapter(lambda request: httpx.Response(500), cache_path=cache)
    snapshot = restarted.refresh()
    assert snapshot.stale and len(snapshot.cameras) == 5
    assert snapshot.fetched_at == FIXED_NOW


def test_change_is_logged_once_per_state_change(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="vision.cameras")
    adapter = make_adapter(ok_handler)
    adapter.refresh()
    adapter.refresh()
    changes = [r.getMessage() for r in caplog.records if "count" in r.getMessage()]
    assert changes == ["CHANGE alberta511_cameras count 0 -> 5"]


def test_missing_key_is_refused():
    with pytest.raises(ValueError):
        CameraAdapter(key="")


def test_cli_without_key_exits_cleanly(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.delenv("ALBERTA_511_KEY", raising=False)
    assert main([]) == 2
    assert "ALBERTA_511_KEY" in capsys.readouterr().err
