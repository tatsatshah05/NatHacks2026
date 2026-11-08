"""OP-401: what the database holds is exactly what the privacy page says, and no image
is ever stored.

``web/src/app/privacy/page.tsx`` describes the tables below in plain words. A new
table or column means that page (and docs/SECURITY.md) must be reviewed in the same
pull request; then update ``INVENTORY`` here.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BINARY, VARBINARY, LargeBinary, PickleType, inspect, text
from sqlmodel import SQLModel

from cloud.app import (  # noqa: F401  (importing registers every table)
    camera_readings,
    push,
    reports,
    saved_routes,
    store,
)
from cloud.app.main import create_app
from cloud.tests.conftest import REPO, image_files
from cloud.tests.test_reports import (  # noqa: F401  (disk_writes is a fixture)
    CLIENT,
    JPEG,
    MARKER,
    disk_writes,
    report,
)

PRIVACY_PAGE = REPO / "web" / "src" / "app" / "privacy" / "page.tsx"

# Every table and column the API can create, by name. Personal data lives only in
# saved_route, push_subscription and community_report_log, each under an anonymous
# client_id; obstruction and camera_reading hold public conditions.
INVENTORY: dict[str, set[str]] = {
    "obstruction": {
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
        "min_lng",
        "min_lat",
        "max_lng",
        "max_lat",
        "changed_at",
    },
    "camera_reading": {
        "source_ref",
        "source",
        "camera_id",
        "view_id",
        "latitude",
        "longitude",
        "label",
        "score",
        "reason",
        "observed_at",
        "expires_at",
        "model_version",
        "mode",
        "attribution",
        "received_at",
        "changed_at",
    },
    "community_report_log": {"id", "client_id", "report_id", "created_at"},
    "saved_route": {
        "id",
        "client_id",
        "name",
        "from_lat",
        "from_lng",
        "to_lat",
        "to_lng",
        "profile",
        "created_at",
        "baseline_eta_min",
        "baseline_edge_ids",
        "last_eta_min",
        "last_edge_ids",
        "last_status",
        "last_checked_at",
        "last_push_at",
        "notified_status",
        "pending",
    },
    "push_subscription": {
        "id",
        "client_id",
        "endpoint",
        "p256dh",
        "auth",
        "created_at",
        "last_ok_at",
        "failures",
    },
}

# A column with one of these in its name would be a place to keep an image.
SUSPECT_NAME = re.compile(
    r"photo|image|img|jpe?g|png|webp|base64|blob|frame|picture|thumb", re.IGNORECASE
)
BINARY_TYPES = (LargeBinary, PickleType, BINARY, VARBINARY)


def tables() -> dict[str, set[str]]:
    return {
        name: {column.name for column in table.columns}
        for name, table in SQLModel.metadata.tables.items()
    }


# -------------------------------------------------------------------- the schema


def test_the_tables_and_columns_are_the_documented_inventory() -> None:
    assert tables() == INVENTORY, (
        "the database inventory changed: review web/src/app/privacy/page.tsx and "
        "docs/SECURITY.md, then update INVENTORY"
    )


def test_no_column_could_hold_an_image() -> None:
    for table in SQLModel.metadata.tables.values():
        for column in table.columns:
            where = f"{table.name}.{column.name}"
            assert not SUSPECT_NAME.search(column.name), (
                f"{where} is named like an image"
            )
            assert not isinstance(column.type, BINARY_TYPES), (
                f"{where} is a binary column ({column.type!r})"
            )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    for name in ("DATA_MODE", "RENDER_GIT_COMMIT", "MODEL_FILE", "MODEL_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def client(env: None) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


def test_the_live_database_creates_only_those_tables(client: TestClient) -> None:
    assert client.post("/api/reports", json=report()).status_code == 201  # the log
    engine = client.app.state.store.engine
    with engine.connect() as connection:
        names = set(inspect(connection).get_table_names())
    assert names <= set(INVENTORY)
    assert {"obstruction", "community_report_log", "saved_route"} <= names


# --------------------------------------------------------------------- the photo


def test_a_report_photo_touches_neither_disk_nor_database_nor_log(
    client: TestClient, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture
) -> None:
    """The full app, not a cut-down one: middleware, limiter and store included."""
    caplog.set_level(logging.INFO)
    # The spy starts after the app is up, so only the request itself is watched.
    writes: list[str] = request.getfixturevalue("disk_writes")
    photo = base64.b64encode(JPEG).decode()
    response = client.post("/api/reports", json={**report(), "photo": photo})
    assert response.status_code == 201, response.text
    assert response.json()["photo"]["received"] is True
    assert writes == []  # no file, temporary file or spool
    engine = client.app.state.store.engine
    encoded = {MARKER, base64.b64encode(MARKER), photo.encode()}
    with engine.connect() as connection:
        for table in inspect(connection).get_table_names():
            rows = connection.execute(text(f'SELECT * FROM "{table}"')).fetchall()
            dump = repr(rows).encode()
            for needle in encoded:
                assert needle not in dump, f"a copy of the photo is in {table}"
    assert MARKER.decode() not in caplog.text
    assert photo not in caplog.text


# ---------------------------------------------------------------- the image check


def test_image_files_sees_new_images_and_skips_dependency_folders(
    tmp_path: Path,
) -> None:
    (tmp_path / "kept.PNG").write_bytes(b"x")
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "frame.jpg").write_bytes(b"x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "icon.png").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    assert image_files(tmp_path) == {
        tmp_path / "kept.PNG",
        tmp_path / "deep" / "frame.jpg",
    }


def test_the_repository_check_watches_the_right_folder() -> None:
    assert (REPO / "AGENTS.md").is_file()
    assert (REPO / "cloud" / "app" / "main.py").is_file()


# -------------------------------------------------------------- the privacy page


def test_the_privacy_page_states_the_real_limits_and_expiries() -> None:
    page = PRIVACY_PAGE.read_text(encoding="utf-8")
    config = reports.load_config()
    assert f"{config.expiry_hours['ice']:g} hours for ice" in page
    assert f"{config.expiry_hours['blocked']:g} hours for a blocked path" in page
    assert f"{config.expiry_hours['snow']:g} hours for snow" in page
    assert f"{config.per_client_per_hour} reports" in page
    assert f"{int(reports.LOG_KEEP.total_seconds() // 3600)} hours" in page
    assert f"{push.MAX_FAILURES} failed deliveries" in page
    for phrase in (
        "Photos are never stored",
        "Anthropic",
        "511 Alberta",
        "Rate limits",
        "anonymous id",
    ):
        assert phrase in page, phrase
