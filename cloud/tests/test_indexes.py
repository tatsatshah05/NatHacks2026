"""OP-404: database indexes exist on new and on older databases alike."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, inspect, text
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel, col, select

from cloud.app import main
from cloud.app.camera_readings import CameraReadingRow, CameraReadingStore
from cloud.app.db import ensure_indexes, make_engine
from cloud.app.push import PushSubscriptionRow, PushSubscriptionStore
from cloud.app.reports import ReportCounter, ReportLogRow
from cloud.app.saved_routes import SavedRouteRow, SavedRouteStore
from cloud.app.store import ObstructionRow, ObstructionStore

TABLES = (
    ObstructionRow.__table__,
    CameraReadingRow.__table__,
    SavedRouteRow.__table__,
    PushSubscriptionRow.__table__,
    ReportLogRow.__table__,
)


def declared(table: Table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def present(engine, table: Table) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes(table.name)}


def create_without_indexes(engine, table: Table) -> None:
    """Make the table as an older release might have: columns only, no indexes.

    ``CREATE TABLE`` alone; ``create_all`` would add the indexes as separate
    ``CREATE INDEX`` statements, which is exactly what an older release never ran.
    """
    with engine.begin() as connection:
        connection.execute(CreateTable(table))


def test_every_declared_index_exists_after_create_tables() -> None:
    engine = make_engine("sqlite://")
    ObstructionStore(engine).create_tables()
    CameraReadingStore(engine).create_tables()
    SavedRouteStore(engine).create_tables()
    PushSubscriptionStore(engine).create_tables()
    ReportCounter(engine).create_tables()
    for table in TABLES:
        assert declared(table) <= present(engine, table), table.name
    assert ensure_indexes(engine) == []  # nothing to add on a fresh database


def test_ensure_indexes_adds_what_an_older_table_lacks() -> None:
    engine = make_engine("sqlite://")
    create_without_indexes(engine, SavedRouteRow.__table__)
    create_without_indexes(engine, ObstructionRow.__table__)
    assert present(engine, SavedRouteRow.__table__) == set()
    created = ensure_indexes(engine)
    assert set(created) == declared(SavedRouteRow.__table__) | declared(
        ObstructionRow.__table__
    )
    assert "ix_saved_route_client_created" in created
    assert declared(SavedRouteRow.__table__) <= present(engine, SavedRouteRow.__table__)
    assert declared(ObstructionRow.__table__) <= present(
        engine, ObstructionRow.__table__
    )
    assert ensure_indexes(engine) == []  # the second call is a no-op


def test_ensure_indexes_skips_tables_that_do_not_exist() -> None:
    engine = make_engine("sqlite://")
    assert ensure_indexes(engine) == []
    assert inspect(engine).get_table_names() == []


def test_ensure_indexes_can_be_limited_to_some_tables() -> None:
    engine = make_engine("sqlite://")
    create_without_indexes(engine, SavedRouteRow.__table__)
    create_without_indexes(engine, CameraReadingRow.__table__)
    created = ensure_indexes(engine, [CameraReadingRow.__table__])
    assert created == ["ix_camera_reading_expires_at"]
    assert present(engine, SavedRouteRow.__table__) == set()


def test_saved_routes_by_client_use_the_new_index_without_a_sort() -> None:
    """EXPLAIN QUERY PLAN: the composite index serves the filter and the order."""
    engine = make_engine("sqlite://")
    SavedRouteStore(engine).create_tables()
    query = (
        select(SavedRouteRow)
        .where(SavedRouteRow.client_id == "c")
        .order_by(col(SavedRouteRow.created_at), col(SavedRouteRow.id))
    )
    sql = str(query.compile(engine, compile_kwargs={"literal_binds": True}))
    with engine.connect() as connection:
        plan = " ".join(
            row[-1] for row in connection.execute(text("EXPLAIN QUERY PLAN " + sql))
        )
    assert "ix_saved_route_client_created" in plan
    assert "TEMP B-TREE" not in plan


@pytest.fixture
def local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    for name in ("RENDER_GIT_COMMIT", "DATA_MODE", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)


def test_startup_ensures_the_indexes(local_env) -> None:
    with TestClient(main.create_app()) as client:
        engine = client.app.state.store.engine
        for table in (ObstructionRow.__table__, SavedRouteRow.__table__):
            assert declared(table) <= present(engine, table), table.name
        assert ensure_indexes(engine) == []


def test_startup_survives_a_failing_index_check(
    local_env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A database error here must never stop the API (AGENTS.md, priority 1)."""

    def boom(engine, tables=None):
        raise RuntimeError("no CREATE INDEX for you")

    monkeypatch.setattr(main, "ensure_indexes", boom)
    with TestClient(main.create_app()) as client:
        assert client.get("/health").status_code == 200
    assert "could not check indexes" in caplog.text


def test_metadata_knows_every_table() -> None:
    """``ensure_indexes`` defaults to the imported models: all five tables."""
    names = {table.name for table in SQLModel.metadata.sorted_tables}
    assert {table.name for table in TABLES} <= names
    # The stores stamp UTC on what they read back; a smoke check that the composite
    # index does not change what for_client returns.
    engine = make_engine("sqlite://")
    store = SavedRouteStore(engine)
    store.create_tables()
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    for name in ("b", "a"):
        store.add(
            client_id="c",
            name=name,
            from_lat=53.5,
            from_lng=-113.5,
            to_lat=53.6,
            to_lng=-113.4,
            profile="standard",
            baseline=None,
            now=now,
        )
    assert [row.name for row in store.for_client("c")] == ["b", "a"]
