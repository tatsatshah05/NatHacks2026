"""OP-412: ``/api/status`` says whether the cameras report and where ice risk comes from.

The block is read at the app's clock (``app.state.clock``), so these tests freeze
it and store readings relative to that time through the real camera store. One
test posts through ``POST /api/ingest/camera`` to cover the worker's path, and one
runs the API in replay mode with the recorded freeze-thaw night, where the watch
is active and the fixtures hold no camera readings.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cloud.app import camera_status as cs
from cloud.app.camera_readings import CameraReadingIn, CameraReadingStore
from cloud.app.camera_status import LIVE_WITHIN, CamerasStatus, summarize
from cloud.app.db import make_engine
from cloud.app.main import create_app
from cloud.app.weather_service import WeatherResponse, WeatherService

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)  # conftest's NOW
DEMO = datetime(
    2025, 11, 6, 14, 0, tzinfo=UTC
)  # inside the recorded watch (12 to 18 UTC)
KEY = "op-412-test-key"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.setenv("WORKER_KEY", KEY)
    for name in ("DATA_MODE", "REPLAY_SNAPSHOT", "REPLAY_AT", "WEATHER_REPLAY"):
        monkeypatch.delenv(name, raising=False)


def reading(
    ref: str,
    label: str,
    *,
    age: timedelta,
    score: float | None = 0.8,
    version: str = "a" * 64,
    at: datetime = NOW,
) -> CameraReadingIn:
    """A reading observed ``age`` before ``at`` and trusted for an hour after ``at``."""
    _, camera_id, view_id = ref.split(":")
    return CameraReadingIn(
        source_ref=ref,
        camera_id=int(camera_id),
        view_id=int(view_id),
        latitude=53.54,
        longitude=-113.49,
        label=label,  # type: ignore[arg-type]
        score=score,
        observed_at=at - age,
        expires_at=at - age + timedelta(hours=1, minutes=30),
        model_version=version,
        mode="live",
    )


def watch(active: bool | None) -> WeatherService:
    """A weather service whose cached watch is ``active`` (None: no reading yet)."""

    async def never(_: datetime) -> dict:
        raise AssertionError("not fetched in tests")

    service = WeatherService(never)
    if active is not None:
        service.last_good = WeatherResponse(
            active=active,
            reason="test",
            air_c=-1.0,
            ground_c=-1.0,
            dew_c=-1.5,
            observed_at=NOW - timedelta(minutes=5),
        )
    return service


@pytest.fixture
def frozen_app():
    """The app with its clock frozen at NOW, so ages are exact."""
    app = create_app()
    with TestClient(app) as client:
        app.state.clock = lambda: NOW
        yield app, client


def block(client: TestClient) -> dict:
    body = client.get("/api/status").json()
    assert "cameras" in body
    return body["cameras"]


# -------------------------------------------------------------------- summarize


def test_constants_are_the_documented_ones() -> None:
    assert LIVE_WITHIN == timedelta(minutes=15)
    assert cs.UNKNOWN_LABEL == "unknown"


def test_no_readings_is_none_and_falls_back_to_the_watch() -> None:
    status = summarize([], now=NOW, views_total=0, watch_active=None)
    assert status == CamerasStatus(
        state="none",
        ice_source="weather_watch",
        readings=0,
        usable=0,
        views_total=0,
        newest_observed_at=None,
        newest_age_s=None,
        oldest_age_s=None,
        model_versions=[],
        score_min=None,
        score_median=None,
        labels={},
        watch_active=None,
        live_within_s=900,
    )


def test_live_when_one_usable_reading_is_newer_than_15_minutes() -> None:
    rows = [
        reading("511:1:1", "wet", age=timedelta(minutes=40), score=0.7, version="v2"),
        reading("511:1:2", "ice", age=timedelta(minutes=3), score=0.9),
        reading("511:2:1", "unknown", age=timedelta(minutes=1), score=None),
    ]
    status = summarize(rows, now=NOW, views_total=5, watch_active=True)
    assert status.state == "live" and status.ice_source == "cameras"
    assert status.readings == 3 and status.usable == 2 and status.views_total == 5
    assert status.newest_observed_at == NOW - timedelta(minutes=3)
    assert status.newest_age_s == 180.0 and status.oldest_age_s == 2400.0
    assert status.model_versions == ["a" * 64, "v2"]  # distinct, sorted, opaque
    assert status.score_min == 0.7 and status.score_median == pytest.approx(0.8)
    assert status.labels == {"ice": 1, "unknown": 1, "wet": 1}
    assert status.watch_active is True


def test_stale_when_every_usable_reading_is_older_than_15_minutes() -> None:
    rows = [reading("511:1:1", "dry", age=LIVE_WITHIN)]  # exactly 15 minutes: not live
    status = summarize(rows, now=NOW, views_total=1, watch_active=False)
    assert status.state == "stale" and status.ice_source == "weather_watch"
    assert status.newest_age_s == 900.0 and status.watch_active is False
    just_inside = [reading("511:1:1", "dry", age=LIVE_WITHIN - timedelta(seconds=1))]
    assert (
        summarize(just_inside, now=NOW, views_total=1, watch_active=None).state
        == "live"
    )


def test_unknown_readings_never_make_the_cameras_live() -> None:
    """A worker that cannot see any road (511 down, model broken) posts "unknown"."""
    rows = [
        reading("511:1:1", "unknown", age=timedelta(minutes=1), score=None),
        reading("511:1:2", "unknown", age=timedelta(minutes=2), score=0.3),
    ]
    status = summarize(rows, now=NOW, views_total=2, watch_active=True)
    assert status.state == "none" and status.ice_source == "weather_watch"
    assert status.readings == 2 and status.usable == 0
    assert status.labels == {"unknown": 2}
    assert status.newest_observed_at is None and status.score_min is None


def test_a_frame_from_the_future_reads_as_just_now() -> None:
    rows = [reading("511:1:1", "wet", age=-timedelta(seconds=30))]
    status = summarize(rows, now=NOW, views_total=1, watch_active=None)
    assert status.newest_age_s == 0.0 and status.state == "live"


def test_long_model_version_strings_are_kept_whole() -> None:
    version = "b" * 64 + "+snow:" + "c" * 16  # the live worker's two-model form
    rows = [
        reading("511:1:1", "snow_covered", age=timedelta(minutes=1), version=version)
    ]
    status = summarize(rows, now=NOW, views_total=1, watch_active=None)
    assert status.model_versions == [version]


# ------------------------------------------------------------------ /api/status


def test_status_block_for_none_stale_and_live_at_the_frozen_clock(frozen_app) -> None:
    app, client = frozen_app
    store: CameraReadingStore = app.state.camera_store
    assert block(client)["state"] == "none"
    assert block(client)["ice_source"] == "weather_watch"

    store.upsert(reading("511:1:1", "wet", age=timedelta(minutes=40)), NOW)
    stale = block(client)
    assert stale["state"] == "stale" and stale["ice_source"] == "weather_watch"
    assert stale["newest_age_s"] == 2400.0
    assert stale["newest_observed_at"] == "2026-11-07T19:30:00Z"

    store.upsert(reading("511:2:1", "ice", age=timedelta(minutes=2)), NOW)
    live = block(client)
    assert live["state"] == "live" and live["ice_source"] == "cameras"
    assert live["readings"] == 2 and live["views_total"] == 2
    assert live["labels"] == {"ice": 1, "wet": 1}

    # Expired readings drop out of everything but views_total.
    app.state.clock = lambda: NOW + timedelta(hours=2)
    later = block(client)
    assert later["state"] == "none" and later["readings"] == 0
    assert later["views_total"] == 2


def test_watch_active_follows_the_weather_service(frozen_app) -> None:
    app, client = frozen_app
    assert block(client)["watch_active"] is None  # scheduler off: no service
    app.state.weather_service = watch(None)
    assert block(client)["watch_active"] is None  # no reading yet
    app.state.weather_service = watch(True)
    assert block(client)["watch_active"] is True
    app.state.weather_service = watch(False)
    assert block(client)["watch_active"] is False


def test_a_posted_reading_reaches_the_block(frozen_app) -> None:
    """The worker's own path: POST /api/ingest/camera, then the block says live."""
    app, client = frozen_app
    # Ingest checks observed_at against the real clock, so anchor to it, then move
    # the app's clock to the same moment.
    real_now = datetime.now(UTC).replace(microsecond=0)
    app.state.clock = lambda: real_now
    body = reading("511:7:1", "ice", age=timedelta(minutes=4), at=real_now).model_dump(
        mode="json"
    )
    response = client.post(
        "/api/ingest/camera", json=body, headers={"X-Worker-Key": KEY}
    )
    assert response.status_code == 200, response.text
    live = block(client)
    assert live["state"] == "live" and live["usable"] == 1
    assert live["model_versions"] == ["a" * 64]
    assert 240.0 <= live["newest_age_s"] < 300.0


def test_cameras_endpoint_carries_the_same_summary(frozen_app) -> None:
    app, client = frozen_app
    app.state.camera_store.upsert(
        reading("511:1:1", "wet", age=timedelta(minutes=3)), NOW
    )
    cameras = client.get("/api/cameras").json()
    assert cameras["summary"] == block(client)
    assert cameras["summary"]["state"] == "live"
    assert [f["id"] for f in cameras["features"]] == ["511:1:1"]


def test_a_broken_camera_table_leaves_status_answering(frozen_app, caplog) -> None:
    app, client = frozen_app

    def broken(now: datetime):
        raise RuntimeError("database gone")

    app.state.camera_store.latest = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="openpath.cameras"):
        body = client.get("/api/status").json()
    assert body["cameras"] is None and body["mode"] == "live"
    assert any("camera status unavailable" in r.message for r in caplog.records)


def test_without_a_camera_store_the_block_is_absent_not_an_error(frozen_app) -> None:
    app, client = frozen_app
    del app.state.camera_store
    assert client.get("/api/status").json()["cameras"] is None


def test_the_block_is_in_the_openapi_schema() -> None:
    with TestClient(create_app()) as client:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "CamerasStatus" in schemas
    assert set(schemas["CamerasStatus"]["properties"]) == set(
        CamerasStatus.model_fields
    )
    assert "cameras" in schemas["StatusResponse"]["properties"]
    assert "summary" in schemas["CameraCollection"]["properties"]


# ------------------------------------------------------------------- replay run


def test_replay_run_falls_back_to_the_active_watch(monkeypatch) -> None:
    """``make dev`` with the freeze-thaw night: no cameras, the watch is on."""
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", str(FIXTURES))
    monkeypatch.setenv("REPLAY_AT", DEMO.isoformat().replace("+00:00", "Z"))
    monkeypatch.setenv("SCHEDULER", "on")
    monkeypatch.setenv("WEATHER_REPLAY", "freeze_thaw")
    app = create_app()
    with TestClient(app) as client:
        assert client.portal.call(app.state.weather_service.refresh)
        status = client.get("/api/status").json()
        weather = client.get("/api/weather").json()
    assert status["mode"] == "replay"
    assert weather["active"] is True
    cameras = status["cameras"]
    assert cameras["state"] == "none" and cameras["ice_source"] == "weather_watch"
    assert cameras["readings"] == 0 and cameras["watch_active"] is True
    assert "weather_watch" in {s["source"] for s in status["sources"]}


def test_count_views_counts_rows_whatever_their_expiry() -> None:
    store = CameraReadingStore(make_engine("sqlite://"))
    store.create_tables()
    assert store.count_views() == 0
    store.upsert(reading("511:1:1", "dry", age=timedelta(minutes=1)), NOW)
    store.upsert(reading("511:1:1", "wet", age=timedelta(seconds=30)), NOW)  # same view
    store.upsert(reading("511:1:2", "dry", age=timedelta(hours=1)), NOW)
    assert store.count_views() == 2
    assert (
        len(store.latest(NOW + timedelta(hours=1))) == 1
    )  # one expired, still counted
    assert store.count_views() == 2
