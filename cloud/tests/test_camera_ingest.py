"""OP-213: the worker posts camera readings with a key; the map reads the latest per view."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cloud.app.camera_readings import CameraReadingIn, CameraReadingStore, Outcome
from cloud.app.db import make_engine
from cloud.app.main import create_app

# The API compares times with the real clock, so the sample reading is anchored to
# the real "now": observed five minutes ago, trusted for another thirty.
NOW = datetime.now(UTC).replace(microsecond=0)
OBSERVED = NOW - timedelta(minutes=5)
KEY = "worker-test-key"


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


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


def labels(client) -> dict[str, str]:
    features = client.get("/api/cameras").json()["features"]
    return {f["id"]: f["properties"]["label"] for f in features}


# --------------------------------------------------------------------- auth


def test_missing_or_wrong_key_is_rejected(client) -> None:
    assert post(client, reading(), key=None).status_code == 401
    assert post(client, reading(), key="nope").status_code == 401
    assert client.get("/api/cameras").json()["features"] == []  # nothing was stored


def test_non_ascii_key_is_a_401_not_a_500(client) -> None:
    response = client.post(
        "/api/ingest/camera",
        json=reading(),
        headers={"X-Worker-Key": "clé".encode("latin-1")},
    )
    assert response.status_code == 401


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
        "outcome": "new",
    }
    cameras = client.get("/api/cameras").json()
    assert cameras["type"] == "FeatureCollection"
    (feature,) = cameras["features"]
    assert feature["type"] == "Feature" and feature["id"] == "511:1234:1"
    assert feature["geometry"] == {"type": "Point", "coordinates": [-113.4909, 53.5444]}
    p = feature["properties"]
    assert p["source"] == "camera_model"  # stored, so OP-205 can read rows directly
    assert p["label"] == "wet" and p["score"] == 0.91 and p["mode"] == "live"
    assert p["observed_at"] == iso(OBSERVED) and p["model_version"] == "a" * 64
    assert p["attribution"].startswith("Road camera data: 511 Alberta")
    assert 290 <= p["age_s"] <= 400  # about five minutes, however slow the test is


def test_a_view_has_one_row_and_changed_tracks_the_label(client) -> None:
    assert post(client, reading()).json()["outcome"] == "new"
    same = post(client, reading(score=0.95)).json()  # same label, same time: a retry
    assert same["changed"] is False and same["outcome"] == "same"
    later = (OBSERVED + timedelta(minutes=1)).isoformat()
    assert (
        post(client, reading(label="ice", observed_at=later)).json()["changed"] is True
    )
    assert labels(client) == {"511:1234:1": "ice"}


def test_an_older_reading_never_replaces_a_newer_one(client) -> None:
    post(client, reading(label="ice"))
    older = (OBSERVED - timedelta(minutes=10)).isoformat()
    body = post(client, reading(label="dry", observed_at=older)).json()
    assert body["stored"] is False and body["outcome"] == "older"
    assert labels(client) == {"511:1234:1": "ice"}


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
    # The expired one is left out; ordered by camera, then view.
    assert ids == ["511:1234:1", "511:1234:2"]


@pytest.mark.parametrize(
    "bad",
    [
        {"label": "slush"},
        {"label": "unusable"},  # the worker sends unknown for unusable frames
        {"score": 1.5},
        {"score": "0.9"},  # numbers must be JSON numbers
        {"camera_id": True, "source_ref": "511:1:1"},
        {"latitude": "53.5"},
        {"observed_at": OBSERVED.replace(tzinfo=None).isoformat()},  # no time zone
        {"expires_at": OBSERVED.isoformat()},  # not after observed_at
        {"expires_at": "9999-01-01T00:00:00Z"},  # longer than the 2 h limit
        {"source_ref": "511:2:7"},  # does not match camera_id 1234 / view_id 1
        {"source": "camera_guess"},
        {"extra_field": 1},
        {"image": "base64..."},  # frames are never sent or stored
        {"mode": "test"},
    ],
)
def test_invalid_payloads_are_422(client, bad) -> None:
    assert post(client, reading(**bad)).status_code == 422


def test_future_observation_beyond_clock_skew_is_422(client) -> None:
    ahead = datetime.now(UTC) + timedelta(minutes=5)
    body = reading(
        observed_at=ahead.isoformat(),
        expires_at=(ahead + timedelta(minutes=15)).isoformat(),
    )
    assert post(client, body).status_code == 422
    slightly = datetime.now(UTC) + timedelta(seconds=10)  # within the 60 s allowance
    body = reading(
        observed_at=slightly.isoformat(),
        expires_at=(slightly + timedelta(minutes=15)).isoformat(),
    )
    assert post(client, body).status_code == 200


def test_mode_must_match_the_api(client, monkeypatch) -> None:
    # A live API refuses replayed readings, so the live map never shows them.
    assert post(client, reading(mode="replay")).status_code == 409
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", "cloud/tests/fixtures")
    with TestClient(create_app()) as replay:
        assert post(replay, reading(mode="live")).status_code == 409
        assert (
            post(replay, reading(mode="replay", label="snow_covered")).status_code
            == 200
        )
        (feature,) = replay.get("/api/cameras").json()["features"]
        assert feature["properties"]["mode"] == "replay"


def test_unknown_with_a_reason(client) -> None:
    post(client, reading(label="unknown", score=None, reason="camera_unavailable"))
    (feature,) = client.get("/api/cameras").json()["features"]
    assert feature["properties"]["label"] == "unknown"
    assert feature["properties"]["reason"] == "camera_unavailable"


# -------------------------------------------------------------------- store


def test_store_rows_carry_every_payload_field() -> None:
    store = CameraReadingStore(make_engine("sqlite://"))
    store.create_tables()
    incoming = CameraReadingIn(**reading())
    assert store.upsert(incoming, NOW) is Outcome.NEW
    (row,) = store.latest(NOW)
    for name, value in incoming.model_dump().items():
        if name == "incident":
            # OP-415: its own table (camera_incident), never a camera_reading column.
            assert value is None and not hasattr(row, "incident")
            continue
        assert getattr(row, name) == value, name
    assert row.observed_at.tzinfo is not None and row.received_at.tzinfo is not None


def test_store_purges_long_expired_rows() -> None:
    store = CameraReadingStore(make_engine("sqlite://"))
    store.create_tables()
    old = CameraReadingIn(
        **reading(
            observed_at=(NOW - timedelta(days=10)).isoformat(),
            expires_at=(NOW - timedelta(days=10) + timedelta(minutes=15)).isoformat(),
        )
    )
    fresh = CameraReadingIn(**reading(source_ref="511:1:1", camera_id=1))
    store.upsert(old, NOW)
    store.upsert(fresh, NOW)
    assert store.purge_expired(NOW) == 1
    assert [r.source_ref for r in store.latest(NOW)] == ["511:1:1"]


def test_openapi_types_both_endpoints(client) -> None:
    spec = client.get("/openapi.json").json()
    assert "/api/ingest/camera" in spec["paths"]
    cameras = spec["paths"]["/api/cameras"]["get"]["responses"]["200"]["content"]
    assert cameras["application/json"]["schema"]["$ref"].endswith("/CameraCollection")
