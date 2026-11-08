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
    CameraSchemaError,
    RateLimiter,
    _protect_http_loggers,
    count_in_edmonton,
    load_boundary,
    main,
    parse_cameras,
    point_in_polygons,
)

FIXTURES = Path(__file__).parent / "fixtures"
_REAL_HTTPX_CLIENT = httpx.Client  # before any test patches it
SAMPLE = json.loads((FIXTURES / "alberta511_cameras_sample.json").read_text())
PLACEHOLDER_VALUE = "placeholder-value-0000"
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
        key=PLACEHOLDER_VALUE,
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
    assert seen[0].url.params["key"] == PLACEHOLDER_VALUE
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

    caplog.set_level(logging.DEBUG)  # root logger: catches httpx and httpcore too
    snapshot = make_adapter(handler).refresh()
    assert PLACEHOLDER_VALUE not in (snapshot.error or "")
    assert PLACEHOLDER_VALUE not in caplog.text


def test_httpx_request_log_never_shows_the_key(caplog: pytest.LogCaptureFixture):
    # httpx logs "HTTP Request: GET <url with ?key=...>" at INFO. Even with httpx
    # turned up to DEBUG, the redaction filter must hide the key.
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")
    make_adapter(ok_handler).refresh()
    request_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert request_lines, "httpx should have logged the request at this level"
    assert all("key=REDACTED" in line for line in request_lines)
    assert PLACEHOLDER_VALUE not in caplog.text


def test_httpx_request_log_is_quiet_by_default(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)  # what main() turns on
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    _protect_http_loggers()
    make_adapter(ok_handler).refresh()
    assert not [r for r in caplog.records if r.name == "httpx"]
    assert PLACEHOLDER_VALUE not in caplog.text


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


# -- review fixes (PR #89) ----------------------------------------------------


def test_one_malformed_camera_is_skipped_not_the_whole_list(
    caplog: pytest.LogCaptureFixture,
):
    rows = json.loads(json.dumps(SAMPLE))
    rows[2]["Latitude"] = None
    caplog.set_level(logging.WARNING, logger="vision.cameras")

    snapshot = make_adapter(lambda request: httpx.Response(200, json=rows)).refresh()

    assert not snapshot.stale
    assert [c.id for c in snapshot.cameras] == [9001, 9002, 9004, 9005]
    assert (
        "CHANGE alberta511_cameras:9003 skipped invalid camera (Latitude)"
        in caplog.text
    )


def test_payload_that_is_not_a_list_is_a_schema_change():
    with pytest.raises(CameraSchemaError):
        parse_cameras({"cameras": SAMPLE})
    responses = iter(
        [httpx.Response(200, json=SAMPLE), httpx.Response(200, json={"error": "x"})]
    )
    adapter = make_adapter(lambda request: next(responses))
    adapter.refresh()
    snapshot = adapter.refresh()
    assert snapshot.stale and len(snapshot.cameras) == 5


def test_empty_list_is_valid_not_a_schema_change():
    assert parse_cameras([]) == []


def test_unwritable_cache_never_fails_a_good_fetch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the cache folder should be")
    caplog.set_level(logging.WARNING, logger="vision.cameras")

    snapshot = make_adapter(ok_handler, cache_path=blocker / "cameras.json").refresh()

    assert not snapshot.stale and len(snapshot.cameras) == 5
    assert "Could not write camera cache" in caplog.text


def test_stale_and_recovery_are_logged_once_each(caplog: pytest.LogCaptureFixture):
    responses = iter(
        [
            httpx.Response(200, json=SAMPLE),
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json=SAMPLE),
        ]
    )
    adapter = make_adapter(lambda request: next(responses))
    caplog.set_level(logging.INFO, logger="vision.cameras")
    for _ in range(5):
        adapter.refresh()
    messages = [r.getMessage() for r in caplog.records if r.name == "vision.cameras"]
    assert sum("stale" in m for m in messages) == 1
    assert sum("fresh again" in m for m in messages) == 1


def test_rate_limiter_is_safe_across_threads():
    import threading

    limiter = RateLimiter(max_calls=5, period_s=60.0, sleep=lambda s: None)
    waits: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        waited = limiter.acquire()
        with lock:
            waits.append(waited)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert waits == [0.0] * 5
    assert len(limiter._calls) == 5


def _patch_http(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setattr(
        "vision.cameras.httpx.Client",
        lambda **kwargs: _REAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


def test_cli_uses_refresh_and_falls_back_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.setenv("ALBERTA_511_KEY", PLACEHOLDER_VALUE)
    cache = tmp_path / "cache" / "cameras.json"
    record = tmp_path / "recorded.json"

    _patch_http(monkeypatch, ok_handler)
    assert main(["--cache", str(cache), "--record", str(record)]) == 0
    assert json.loads(record.read_text()) == SAMPLE
    assert "5 total" in capsys.readouterr().out

    _patch_http(monkeypatch, lambda request: httpx.Response(503))
    assert main(["--cache", str(cache)]) == 0
    captured = capsys.readouterr()
    assert "5 total" in captured.out
    assert "Using the last good list" in captured.err
    assert PLACEHOLDER_VALUE not in captured.out + captured.err


def test_cli_refuses_to_record_a_failed_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ALBERTA_511_KEY", PLACEHOLDER_VALUE)
    _patch_http(monkeypatch, lambda request: httpx.Response(503))
    record = tmp_path / "recorded.json"
    assert main(["--cache", str(tmp_path / "none.json"), "--record", str(record)]) == 1
    assert not record.exists()
