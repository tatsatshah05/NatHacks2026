"""OP-204 -> OP-213: real worker readings through HttpPublisher into the real API.

Each reading comes from a real CameraWorker pass (synthetic 511 metadata and
frames, a stub classifier) and is posted by HttpPublisher to cloud.app.main with
in-memory SQLite. Keys are placeholders; nothing contacts a live service and no
image is stored.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.app.camera_readings import MAX_LIFETIME
from cloud.app.main import create_app
from vision import worker
from vision.cameras import API_URL, CameraAdapter, RateLimiter
from vision.inference import Incident, Prediction
from vision.tests.test_worker import CAMERA, Clock
from vision.worker import CameraWorker, FrameFetcher, HttpPublisher, Reading

KEY = "worker-ingest-test-key"
# HttpPublisher sets a request timeout, which only Starlette's TestClient warns about.
pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument:DeprecationWarning"
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not call the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.setenv("WORKER_KEY", KEY)
    for name in ("DATA_MODE", "RENDER_GIT_COMMIT", "REPLAY_SNAPSHOT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def api() -> Iterator[TestClient]:
    # HttpPublisher allows plain HTTP only for localhost.
    with TestClient(create_app(), base_url="http://localhost") as client:
        yield client


class StubClassifier:
    version = "c" * 64  # the worker reports the model SHA-256

    def __init__(self, predictions: list[Prediction]) -> None:
        self.predictions = iter(predictions)

    def predict(self, frame: bytes) -> Prediction:
        return next(self.predictions)


def live_worker(
    api: TestClient,
    predictions: list[Prediction],
    *,
    key: str = KEY,
    interval_s: float = worker.DEFAULT_INTERVAL_S,
    frame_status: int = 200,
) -> CameraWorker:
    """A real worker whose clock is the real one, because the API checks times."""
    frames = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal frames
        if str(request.url).startswith(API_URL):
            return httpx.Response(200, json=[CAMERA])
        frames += 1
        return httpx.Response(
            frame_status,
            content=f"synthetic-frame-{frames}".encode(),
            headers={"content-type": "image/jpeg"},
        )

    camera_http = httpx.Client(transport=httpx.MockTransport(transport))
    clock = Clock()
    limiter = RateLimiter(clock=clock, sleep=clock.sleep)
    return CameraWorker(
        CameraAdapter("placeholder", client=camera_http, limiter=limiter),
        FrameFetcher(camera_http, limiter),
        StubClassifier(predictions),
        HttpPublisher(api, "http://localhost", key),
        interval_s=interval_s,
        clock=clock,
    )


def confirmed(camera_worker: CameraWorker) -> Reading:
    assert not camera_worker.cycle(), "One frame must never confirm a reading"
    (reading,) = camera_worker.cycle()
    return reading


def test_confirmed_reading_is_accepted_and_listed_field_by_field(
    api: TestClient,
) -> None:
    camera_worker = live_worker(api, [Prediction("wet", 0.91)] * 2)
    reading = confirmed(camera_worker)
    assert camera_worker.publish_succeeded == 1 and camera_worker.publish_failed == 0

    body = reading.model_dump(mode="json")
    # OP-213 accepts only JSON numbers here, never strings or booleans.
    assert type(body["camera_id"]) is int and type(body["view_id"]) is int
    assert all(type(body[k]) is float for k in ("latitude", "longitude", "score"))
    assert body["mode"] == "live" and body["source"] == "camera_model"

    (feature,) = api.get("/api/cameras").json()["features"]
    properties = feature["properties"]
    assert feature["id"] == reading.source_ref == f"511:{CAMERA['Id']}:1"
    assert feature["geometry"]["coordinates"] == [reading.longitude, reading.latitude]
    for name in (
        "source",
        "source_ref",
        "camera_id",
        "view_id",
        "label",
        "score",
        "reason",
        "observed_at",
        "expires_at",
        "model_version",
        "mode",
        "attribution",
    ):
        assert properties[name] == body[name], name
    # OP-415: the check did not run, so the body has no incident and the API says null.
    assert "incident" not in reading.payload()
    assert properties["incident"] is None
    assert api.get("/api/status").json()["cameras"]["incidents"] == 0


def test_a_reading_with_the_checks_incident_answer_is_shown_and_counted(
    api: TestClient,
) -> None:
    """OP-415: the same worker path, with the Claude answer riding on the prediction."""
    seen = Incident(True, "A car sits across both lanes facing the barrier.")
    camera_worker = live_worker(api, [Prediction("wet", 0.91, incident=seen)] * 2)
    reading = confirmed(camera_worker)
    assert camera_worker.publish_succeeded == 1
    body = reading.payload()
    assert body["incident"] == {
        "seen": True,
        "detail": "A car sits across both lanes facing the barrier.",
    }
    (feature,) = api.get("/api/cameras").json()["features"]
    incident = feature["properties"]["incident"]
    assert incident == {
        "seen": True,
        "detail": "A car sits across both lanes facing the barrier.",
        "observed_at": body["observed_at"],
    }
    status = api.get("/api/status").json()["cameras"]
    assert status["incidents"] == 1 and status["usable"] == 1


def test_unknown_reading_without_a_score_is_accepted(api: TestClient) -> None:
    camera_worker = live_worker(api, [], frame_status=503)
    reading = confirmed(camera_worker)
    assert reading.label == "unknown" and reading.score is None
    assert camera_worker.publish_succeeded == 1
    (feature,) = api.get("/api/cameras").json()["features"]
    assert feature["properties"]["label"] == "unknown"
    assert feature["properties"]["reason"] == "camera_http_error"


def test_long_interval_expiry_is_capped_at_the_api_limit(api: TestClient) -> None:
    assert worker.MAX_EXPIRY == MAX_LIFETIME
    camera_worker = live_worker(api, [Prediction("ice", 0.95)] * 2, interval_s=3600)
    reading = confirmed(camera_worker)
    assert reading.expires_at - reading.observed_at == MAX_LIFETIME
    assert camera_worker.publish_succeeded == 1
    # Without the cap, three one-hour intervals would be rejected.
    uncapped = reading.model_dump(mode="json") | {
        "expires_at": (reading.observed_at + timedelta(hours=3)).isoformat()
    }
    response = api.post(
        "/api/ingest/camera", json=uncapped, headers={"X-Worker-Key": KEY}
    )
    assert response.status_code == 422


def test_wrong_key_is_a_failed_post_that_the_worker_keeps_for_retry(
    api: TestClient,
) -> None:
    camera_worker = live_worker(api, [Prediction("dry", 0.8)] * 2, key="wrong")
    reading = confirmed(camera_worker)
    assert camera_worker.publish_failed == 1 and camera_worker.publish_succeeded == 0
    assert camera_worker._pending_posts[reading.source_ref] == reading
    assert reading.source_ref not in camera_worker.last_good
    assert api.get("/api/cameras").json()["features"] == []


def test_replay_api_refuses_live_worker_readings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A replay demo never shows live readings, and the worker never posts replay.
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", "cloud/tests/fixtures")
    with TestClient(create_app(), base_url="http://localhost") as replay_api:
        camera_worker = live_worker(replay_api, [Prediction("wet", 0.9)] * 2)
        reading = confirmed(camera_worker)
        assert camera_worker.publish_failed == 1
        response = replay_api.post(
            "/api/ingest/camera",
            json=reading.model_dump(mode="json"),
            headers={"X-Worker-Key": KEY},
        )
        assert response.status_code == 409
        assert replay_api.get("/api/cameras").json()["features"] == []


def test_observed_at_is_the_workers_utc_clock(api: TestClient) -> None:
    before = datetime.now(UTC)
    reading = confirmed(live_worker(api, [Prediction("snow_partial", 0.7)] * 2))
    assert before <= reading.observed_at <= datetime.now(UTC)
    assert reading.observed_at.utcoffset() == timedelta(0)
