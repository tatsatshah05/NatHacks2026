"""OP-213: the worker posts camera readings with a key; the map reads the latest per view."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cloud.app.camera_readings import CameraReadingIn, CameraReadingStore
from cloud.app.db import make_engine
from cloud.app.main import create_app

# The API compares expires_at with the real clock, so the sample reading is anchored
# to the real "now": observed five minutes ago, trusted for another thirty.
NOW = datetime.now(UTC).replace(microsecond=0)
OBSERVED = NOW - timedelta(minutes=5)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


KEY = "worker-test-key"


def reading(**overrides):
    base = {
        "source": "camera_model",
        "source_ref": "511:1234:1",
        "camera_id": 1234,
        "view_id": 1,
        "latitude": 53.5444,
        "longitude": -113.4909,
        "label": "wet",
        "score": 0.91,
        "reason": None,
        "observed_at": OBSERVED.isoformat(),
        "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
        "model_version": "a" * 64,
        "mode": "live",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.setenv("WORKER_KEY", KEY)
    for name in ("DATA_MODE", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def post(client, body, key=KEY):
    headers = {"X-Worker-Key": key} if key is not None else {}
    return client.post("/api/ingest/camera", json=body, headers=headers)


# --------------------------------------------------------------------- auth


def test_missing_or_wrong_key_is_rejected(client) -> None:
    assert post(client, reading(), key=None).status_code == 401
    assert post(client, reading(), key="nope").status_code == 401
    assert client.get("/api/cameras").json()["features"] == []  # nothing was stored


def test_server_without_a_worker_key_answers_503(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_KEY")
    with TestClient(create_app()) as c:
        assert post(c, reading()).status_code == 503


# ------------------------------------------------------------------- ingest


def test_post_stores_and_lists_the_reading(client) -> None:
    body = post(client, reading()).json()
    assert body == {
        "stored": True,
        "source_ref": "511:1234:1",
        "label": "wet",
        "changed": True,
    }
    cameras = client.get("/api/cameras").json()
    assert cameras["type"] == "FeatureCollection"
    (feature,) = cameras["features"]
    assert feature["id"] == "511:1234:1"
    assert feature["geometry"] == {"type": "Point", "coordinates": [-113.4909, 53.5444]}
    p = feature["properties"]
    assert p["label"] == "wet" and p["score"] == 0.91 and p["mode"] == "live"
    assert p["observed_at"] == iso(OBSERVED) and p["model_version"] == "a" * 64
    assert p["attribution"].startswith("Road camera data: 511 Alberta")
    assert 290 <= p["age_s"] <= 400  # about five minutes, however slow the test is


def test_a_view_has_one_row_and_changed_tracks_the_label(client) -> None:
    assert post(client, reading()).json()["changed"] is True
    assert (
        post(client, reading(score=0.95)).json()["changed"] is False
    )  # same label, fresher
    assert post(client, reading(label="ice", score=0.7)).json()["changed"] is True
    features = client.get("/api/cameras").json()["features"]
    assert len(features) == 1 and features[0]["properties"]["label"] == "ice"


def test_other_views_and_expired_readings(client) -> None:
    post(client, reading())
    post(client, reading(source_ref="511:1234:2", view_id=2, label="dry"))
    post(
        client,
        reading(
            source_ref="511:99:1",
            camera_id=99,
            observed_at=(NOW - timedelta(hours=2)).isoformat(),
            expires_at=(NOW - timedelta(hours=1)).isoformat(),
        ),
    )
    ids = [f["id"] for f in client.get("/api/cameras").json()["features"]]
    assert ids == [
        "511:1234:1",
        "511:1234:2",
    ]  # the expired one is left out; ordered by camera, view


@pytest.mark.parametrize(
    "bad",
    [
        {"label": "slush"},
        {"score": 1.5},
        {"observed_at": OBSERVED.replace(tzinfo=None).isoformat()},  # no time zone
        {"expires_at": OBSERVED.isoformat()},  # not after observed_at
        {"source": "camera_guess"},
        {"extra_field": 1},
        {"mode": "test"},
    ],
)
def test_invalid_payloads_are_422(client, bad) -> None:
    assert post(client, reading(**bad)).status_code == 422


def test_replay_readings_are_stored_but_marked(client) -> None:
    post(client, reading(mode="replay", label="snow_covered"))
    (feature,) = client.get("/api/cameras").json()["features"]
    assert feature["properties"]["mode"] == "replay"


def test_unknown_with_a_reason(client) -> None:
    post(client, reading(label="unknown", score=None, reason="camera_unavailable"))
    (feature,) = client.get("/api/cameras").json()["features"]
    assert (
        feature["properties"]["label"] == "unknown"
        and feature["properties"]["reason"] == "camera_unavailable"
    )


# -------------------------------------------------------------------- store


def test_store_purges_long_expired_rows() -> None:
    store = CameraReadingStore(make_engine("sqlite://"))
    store.create_tables()
    old = CameraReadingIn(
        **reading(
            observed_at=(NOW - timedelta(days=10)).isoformat(),
            expires_at=(NOW - timedelta(days=9)).isoformat(),
        )
    )
    fresh = CameraReadingIn(**reading(source_ref="511:1:1", camera_id=1))
    store.upsert(old, NOW)
    store.upsert(fresh, NOW)
    assert store.purge_expired(NOW) == 1
    assert [r.source_ref for r in store.latest(NOW)] == ["511:1:1"]


def test_openapi_lists_the_endpoints(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/ingest/camera" in paths and "/api/cameras" in paths
