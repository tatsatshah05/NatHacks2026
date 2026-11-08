"""OP-415: a camera reading may carry the Claude check's vehicle-incident answer.

The worker posts ``incident: {seen, detail}`` only when its check ran. The API keeps
it in its own table, ``camera_incident`` (never a new column on ``camera_reading``),
shows it on ``GET /api/cameras`` and counts the unexpired ``seen`` rows on
``GET /api/status``. Display only: nothing in matching, routing or the route report
reads it, and a reading without the field behaves exactly as before. Accuracy is
not measured anywhere.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from cloud.app import sources
from cloud.app.camera_readings import (
    INCIDENT_DETAIL_MAX,
    CameraIncidentRow,
    CameraReadingIn,
    CameraReadingRow,
    CameraReadingStore,
    Outcome,
)
from cloud.app.camera_status import CamerasStatus, summarize
from cloud.app.db import make_engine
from cloud.app.main import create_app
from cloud.tests.conftest import REPO
from cloud.tests.test_camera_ingest import KEY, NOW, OBSERVED, iso, post, reading

SEEN = {"seen": True, "detail": "A car sits across both lanes facing the barrier."}
QUIET = {"seen": False, "detail": "Traffic moves normally."}
# The OP-213 columns of camera_reading: OP-415 adds none of them.
READING_COLUMNS = {
    "source_ref", "source", "camera_id", "view_id", "latitude", "longitude", "label",
    "score", "reason", "observed_at", "expires_at", "model_version", "mode",
    "attribution", "received_at", "changed_at",
}  # fmt: skip


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.setenv("WORKER_KEY", KEY)
    for name in ("DATA_MODE", "RENDER_GIT_COMMIT", "REPLAY_SNAPSHOT", "REPLAY_AT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def feature(client: TestClient, ref: str = "511:1234:1") -> dict:
    features = client.get("/api/cameras").json()["features"]
    (found,) = [f for f in features if f["id"] == ref]
    return found


def incidents(client: TestClient) -> int:
    return client.get("/api/status").json()["cameras"]["incidents"]


def store_with(*readings: CameraReadingIn, at: datetime = NOW) -> CameraReadingStore:
    store = CameraReadingStore(make_engine("sqlite://"))
    store.create_tables()
    for item in readings:
        store.upsert(item, at)
        store.upsert_incident(item)
    return store


# ------------------------------------------------------------------- the API


def test_a_reading_with_an_incident_is_accepted_stored_shown_and_counted(
    client,
) -> None:
    response = post(client, reading(incident=SEEN))
    assert response.status_code == 200, response.text
    # The ingest answer is the OP-213 shape, unchanged.
    assert response.json() == {
        "stored": True,
        "source_ref": "511:1234:1",
        "label": "wet",
        "changed": True,
        "outcome": "new",
    }
    properties = feature(client)["properties"]
    assert properties["label"] == "wet"  # the reading itself is as before
    assert properties["incident"] == {**SEEN, "observed_at": iso(OBSERVED)}
    assert incidents(client) == 1
    cameras = client.get("/api/cameras").json()
    assert cameras["summary"]["incidents"] == 1


def test_a_reading_without_the_field_behaves_exactly_as_before(client) -> None:
    body = reading()
    assert "incident" not in body
    response = post(client, body)
    assert response.status_code == 200 and response.json()["outcome"] == "new"
    assert feature(client)["properties"]["incident"] is None
    assert incidents(client) == 0
    # An explicit null is the same as leaving it out.
    assert post(client, reading(incident=None)).status_code == 200
    assert feature(client)["properties"]["incident"] is None


def test_a_quiet_answer_is_shown_but_not_counted(client) -> None:
    assert post(client, reading(incident=QUIET)).status_code == 200
    assert feature(client)["properties"]["incident"] == {
        **QUIET,
        "observed_at": iso(OBSERVED),
    }
    assert incidents(client) == 0


def test_an_older_observation_never_replaces_a_newer_one(client, caplog) -> None:
    caplog.set_level(logging.INFO, logger="openpath.cameras")
    assert post(client, reading(incident=SEEN)).status_code == 200
    older = reading(
        incident=QUIET, observed_at=(OBSERVED - timedelta(minutes=10)).isoformat()
    )
    response = post(client, older)
    assert response.status_code == 200 and response.json()["outcome"] == "older"
    assert feature(client)["properties"]["incident"]["seen"] is True
    assert incidents(client) == 1
    # A newer quiet answer clears it, with one CHANGE line each way.
    newer = reading(
        incident=QUIET, observed_at=(OBSERVED + timedelta(minutes=5)).isoformat()
    )
    assert post(client, newer).status_code == 200
    shown = feature(client)["properties"]["incident"]
    assert shown["seen"] is False
    assert shown["observed_at"] == iso(OBSERVED + timedelta(minutes=5))
    assert incidents(client) == 0
    lines = [r.message for r in caplog.records if "camera_incident" in r.message]
    assert lines == [
        f"CHANGE camera_incident 511:1234:1 seen: {SEEN['detail']}",
        f"CHANGE camera_incident 511:1234:1 clear: {QUIET['detail']}",
    ]


def test_repeating_the_same_answer_logs_nothing_new(client, caplog) -> None:
    caplog.set_level(logging.INFO, logger="openpath.cameras")
    assert post(client, reading(incident=SEEN)).status_code == 200
    later = reading(
        incident=SEEN, observed_at=(OBSERVED + timedelta(minutes=5)).isoformat()
    )
    assert post(client, later).status_code == 200
    lines = [r.message for r in caplog.records if "camera_incident" in r.message]
    assert len(lines) == 1  # the first "seen"; a renewal is not a state change
    assert feature(client)["properties"]["incident"]["observed_at"] == iso(
        OBSERVED + timedelta(minutes=5)
    )


@pytest.mark.parametrize(
    "bad",
    [
        "yes",
        [],
        {"seen": True},
        {"detail": "x"},
        {"seen": "true", "detail": "x"},
        {"seen": 1, "detail": "x"},
        {"seen": True, "detail": 7},
        {"seen": True, "detail": "x", "observed_at": "2026-11-08T00:00:00Z"},
        {"seen": True, "detail": "x" * (INCIDENT_DETAIL_MAX + 1)},
    ],
)
def test_a_malformed_incident_is_422_and_nothing_is_stored(client, bad) -> None:
    assert post(client, reading(incident=bad)).status_code == 422
    assert client.get("/api/cameras").json()["features"] == []
    assert incidents(client) == 0


# ------------------------------------------------------------------- expiry


def test_the_incident_expires_with_the_reading_that_carried_it() -> None:
    first = CameraReadingIn(**reading(incident=SEEN))
    store = store_with(first)
    assert store.count_incidents(NOW) == 1
    assert set(store.latest_incidents(NOW)) == {"511:1234:1"}
    after = first.expires_at + timedelta(seconds=1)
    assert store.latest_incidents(after) == {} and store.count_incidents(after) == 0
    assert store.latest(after) == []  # the reading is gone too


def test_a_later_reading_without_the_field_does_not_extend_the_incident() -> None:
    """The check did not run on the renewing frame, so the old answer keeps its expiry."""
    first = CameraReadingIn(**reading(incident=SEEN))
    store = store_with(first)
    renewed = CameraReadingIn(
        **reading(
            observed_at=(OBSERVED + timedelta(minutes=20)).isoformat(),
            expires_at=(NOW + timedelta(hours=1)).isoformat(),
        )
    )
    assert store.upsert(renewed, NOW) is Outcome.SAME
    assert store.upsert_incident(renewed) is False  # nothing to write
    at = first.expires_at + timedelta(minutes=1)
    assert [r.source_ref for r in store.latest(at)] == ["511:1234:1"]  # still shown
    assert store.latest_incidents(at) == {}  # the incident has expired


def test_expired_incidents_disappear_from_the_api_at_the_apps_clock(client) -> None:
    app = client.app
    assert post(client, reading(incident=SEEN)).status_code == 200
    assert incidents(client) == 1
    app.state.clock = lambda: NOW + timedelta(hours=3)
    assert client.get("/api/cameras").json()["features"] == []
    assert incidents(client) == 0


def test_purge_removes_long_expired_incident_rows() -> None:
    old = CameraReadingIn(
        **reading(
            incident=SEEN,
            observed_at=(NOW - timedelta(days=10)).isoformat(),
            expires_at=(NOW - timedelta(days=10) + timedelta(minutes=15)).isoformat(),
        )
    )
    fresh = CameraReadingIn(
        **reading(source_ref="511:1:1", camera_id=1, incident=QUIET)
    )
    store = store_with(old, fresh)
    assert store.purge_expired(NOW) == 1  # readings purged, as before
    assert set(store.latest_incidents(NOW)) == {"511:1:1"}
    with store.engine.connect() as connection:
        rows = connection.execute(
            CameraIncidentRow.__table__.select()  # type: ignore[attr-defined]
        ).fetchall()
    assert [row.source_ref for row in rows] == ["511:1:1"]


# ------------------------------------------------------------------ the store


def test_the_new_table_is_created_and_camera_reading_is_not_altered(client) -> None:
    engine = client.app.state.store.engine
    with engine.connect() as connection:
        inspector = inspect(connection)
        names = set(inspector.get_table_names())
        reading_columns = {c["name"] for c in inspector.get_columns("camera_reading")}
        incident_columns = {c["name"] for c in inspector.get_columns("camera_incident")}
    assert {"camera_reading", "camera_incident"} <= names
    assert reading_columns == READING_COLUMNS
    assert incident_columns == {
        "source_ref",
        "camera_id",
        "view_id",
        "seen",
        "detail",
        "observed_at",
        "expires_at",
    }
    assert {c.name for c in CameraReadingRow.__table__.columns} == READING_COLUMNS  # type: ignore[attr-defined]


def test_the_store_row_carries_the_answer_and_the_readings_times() -> None:
    incoming = CameraReadingIn(**reading(incident=SEEN))
    store = store_with(incoming)
    (row,) = store.latest_incidents(NOW).values()
    assert (row.camera_id, row.view_id, row.seen) == (1234, 1, True)
    assert row.detail == SEEN["detail"]
    assert row.observed_at == incoming.observed_at
    assert row.expires_at == incoming.expires_at
    assert row.observed_at.tzinfo is not None


def test_the_status_block_defaults_to_zero_incidents() -> None:
    status = summarize([], now=NOW, views_total=0, watch_active=None)
    assert status.incidents == 0
    assert "incidents" in CamerasStatus.model_fields
    assert (
        summarize([], now=NOW, views_total=0, watch_active=None, incidents=2).incidents
        == 2
    )


def test_a_broken_incident_table_leaves_status_answering(client, caplog) -> None:
    def broken(now: datetime) -> int:
        raise RuntimeError("database gone")

    client.app.state.camera_store.count_incidents = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="openpath.cameras"):
        body = client.get("/api/status").json()
    assert body["cameras"] is None and body["mode"] == "live"


# ------------------------------------------------------------------- replay


def test_a_recorded_incident_replays_and_a_malformed_one_is_dropped(
    tmp_path: Path, client
) -> None:
    """``make record`` copies GET /api/cameras, where incident carries observed_at."""
    assert post(client, reading(incident=SEEN)).status_code == 200
    recorded = client.get("/api/cameras").json()
    (good,) = recorded["features"]
    bad = json.loads(json.dumps(good))
    bad["id"] = bad["properties"]["source_ref"] = "511:1234:2"
    bad["properties"]["view_id"] = 2
    bad["properties"]["incident"] = {"seen": "yes", "detail": "x"}
    recorded["features"] = [good, bad]
    snapshot = tmp_path / "snapshot"
    path = snapshot / sources.CAMERAS_FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(recorded), encoding="utf-8")

    store = CameraReadingStore(make_engine("sqlite://"))
    store.create_tables()
    assert sources.load_replay_cameras(snapshot, store, NOW) == 2
    assert [r.mode for r in store.latest(NOW)] == ["replay", "replay"]
    replayed = store.latest_incidents(NOW)
    assert set(replayed) == {"511:1234:1"}  # the malformed one is no information
    assert replayed["511:1234:1"].seen is True
    assert replayed["511:1234:1"].observed_at == datetime.fromisoformat(
        good["properties"]["observed_at"]
    ).astimezone(UTC)


# ------------------------------------------------------------ display only


def test_nothing_in_matching_routing_or_the_report_reads_incidents() -> None:
    """Display only: the signal never changes a route or a route report."""
    app = REPO / "cloud" / "app"
    for name in (
        "matching.py",
        "routing.py",
        "routing_api.py",
        "report.py",
        "camera_ice.py",
        "camera_ice_routing.py",
    ):
        text = (app / name).read_text(encoding="utf-8")
        assert not re.search(r"incident", text, re.IGNORECASE), name


def test_the_additive_fields_are_in_the_openapi_schema(client) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "incident" in schemas["CameraReadingIn"]["properties"]
    assert "incident" in schemas["CameraProperties"]["properties"]
    assert "incidents" in schemas["CamerasStatus"]["properties"]
    assert set(schemas["CameraIncident"]["properties"]) == {
        "seen",
        "detail",
        "observed_at",
    }
    assert set(schemas["CameraIncidentIn"]["properties"]) == {"seen", "detail"}
