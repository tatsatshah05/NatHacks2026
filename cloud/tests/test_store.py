"""OP-101: the store upserts by (source, source_ref), reports changes and filters active items.

Every test gets a fresh in-memory SQLite database from the ``store`` fixture
(see ``conftest.py``), so tests cannot affect each other.
"""

from __future__ import annotations

import logging
from datetime import UTC, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from cloud.app.db import normalize_url
from cloud.app.schemas import BBox
from cloud.app.store import ObstructionRow, _fill_row

# --------------------------------------------------------------------- upsert


def test_upsert_creates_then_reports_no_change(store, make_obstruction, now) -> None:
    """First upsert creates; a timestamp-only refresh updates the row but is not a change."""
    item = make_obstruction()
    first = store.upsert([item], now)
    assert first.created == [item.id] and not first.updated

    # Same item two minutes later: only observed_at and expires_at moved.
    refreshed = make_obstruction(
        observed_at=now + timedelta(minutes=2), expires_at=now + timedelta(minutes=12)
    )
    second = store.upsert([refreshed], now + timedelta(minutes=2))
    assert not second.changed  # nothing to announce...
    assert store.get(item.id).expires_at == now + timedelta(
        minutes=12
    )  # ...but expiry moved


def test_upsert_reports_material_updates(store, make_obstruction, now) -> None:
    """A change the user would notice (here, the detail text) is reported as updated."""
    store.upsert([make_obstruction()], now)
    changes = store.upsert(
        [make_obstruction(detail="Central Station, East Platform")], now
    )
    assert changes.updated == ["city_lrt_devices:1234"]
    assert store.get("city_lrt_devices:1234").detail == "Central Station, East Platform"


def test_duplicate_items_in_one_batch_keep_the_last(
    store, make_obstruction, now
) -> None:
    """If a source lists the same id twice in one fetch, the last copy wins."""
    changes = store.upsert(
        [make_obstruction(title="First"), make_obstruction(title="Second")], now
    )
    assert changes.created == ["city_lrt_devices:1234"]  # created once, not twice
    assert store.get("city_lrt_devices:1234").title == "Second"


# ------------------------------------------------------------- replace_source


def test_replace_source_removes_items_no_longer_reported(
    store, make_obstruction, now
) -> None:
    """An item the source stops reporting (elevator fixed) is removed."""
    store.replace_source(
        "city_lrt_devices", [make_obstruction(), make_obstruction(source_ref="99")], now
    )
    changes = store.replace_source("city_lrt_devices", [make_obstruction()], now)
    assert changes.removed == ["city_lrt_devices:99"]
    assert store.get("city_lrt_devices:99") is None


def test_replace_source_leaves_other_sources_alone(
    store, make_obstruction, now
) -> None:
    """Replacing one source's items never touches another source's items."""
    other = make_obstruction(
        source="city_trail_closures",
        source_ref="7",
        category="sidewalk",
        kind="trail_closed",
    )
    store.upsert([other], now)
    store.replace_source("city_lrt_devices", [], now)  # LRT source now has nothing
    assert store.get(other.id) is not None  # the trail closure survives


def test_replace_source_rejects_foreign_items(store, make_obstruction, now) -> None:
    """An adapter bug that returns another source's items is caught, not applied."""
    with pytest.raises(ValueError, match="other sources"):
        store.replace_source("city_traffic_disruptions", [make_obstruction()], now)


# ------------------------------------------------------------------- reading


def test_active_excludes_expired_and_future_items(store, make_obstruction, now) -> None:
    """active() returns only items that have started and not expired."""
    store.upsert(
        [
            make_obstruction(source_ref="current"),
            make_obstruction(  # expired exactly now
                source_ref="expired",
                observed_at=now - timedelta(hours=1),
                expires_at=now,
            ),
            make_obstruction(  # a construction closure that starts tomorrow
                source_ref="future",
                starts_at=now + timedelta(days=1),
                expires_at=now + timedelta(days=2),
            ),
        ],
        now,
    )
    assert [item.source_ref for item in store.active(now)] == ["current"]


def test_active_filters_by_bbox_and_source(store, make_obstruction, now) -> None:
    """The bbox filter keeps items whose box overlaps; the source filter keeps one source."""
    downtown = make_obstruction(source_ref="downtown")  # inside the box
    south = make_obstruction(  # well south of the box
        source_ref="south", geometry={"type": "Point", "coordinates": [-113.49, 53.43]}
    )
    road = make_obstruction(  # a line that crosses the box
        source="city_traffic_disruptions",
        source_ref="1309",
        category="road",
        kind="road_closed",
        effect="caution",
        geometry={
            "type": "LineString",
            "coordinates": [[-113.60, 53.53], [-113.40, 53.53]],
        },
    )
    store.upsert([downtown, south, road], now)
    box = BBox(min_lng=-113.52, min_lat=53.52, max_lng=-113.47, max_lat=53.56)
    assert {item.id for item in store.active(now, bbox=box)} == {downtown.id, road.id}
    assert [
        item.id for item in store.active(now, source="city_traffic_disruptions")
    ] == [road.id]


# ------------------------------------------------------------ expiry and logs


def test_purge_expired_deletes_and_logs(store, make_obstruction, now, caplog) -> None:
    """Ten minutes later the item has expired: purge deletes it and logs one line."""
    store.upsert([make_obstruction()], now)
    with caplog.at_level(logging.INFO, logger="openpath.store"):
        gone = store.purge_expired(now + timedelta(minutes=10))
    assert gone == ["city_lrt_devices:1234"]
    assert "CHANGE city_lrt_devices:1234 expired" in caplog.text


def test_changes_are_logged_one_line_each(store, make_obstruction, now, caplog) -> None:
    """The log format from AGENTS.md: CHANGE <id> <what> <kind> <title>."""
    with caplog.at_level(logging.INFO, logger="openpath.store"):
        store.upsert([make_obstruction()], now)
    assert (
        "CHANGE city_lrt_devices:1234 new elevator_out 'Elevator out of service'"
        in caplog.text
    )


# ------------------------------------------------------------ storage details


def test_round_trip_keeps_utc_and_geometry(store, make_obstruction, now) -> None:
    """What goes in comes back out identical, including a nested geometry and UTC zones."""
    item = make_obstruction(
        geometry={
            "type": "MultiLineString",
            "coordinates": [[[-113.5, 53.5], [-113.49, 53.51]]],
        },
    )
    store.upsert([item], now)
    loaded = store.get(item.id)
    assert loaded == item
    assert (
        loaded.observed_at.tzinfo == UTC
    )  # SQLite returns naive; the store restores UTC


def test_large_batches_are_chunked(store, make_obstruction, now) -> None:
    """1,200 items exceed SQLite's per-statement parameter limit unless queries are chunked."""
    items = [make_obstruction(source_ref=str(n)) for n in range(1200)]
    assert len(store.replace_source("city_lrt_devices", items, now).created) == 1200
    assert (
        len(store.replace_source("city_lrt_devices", items[:10], now).removed) == 1190
    )


def test_source_and_ref_are_unique(store, make_obstruction, now) -> None:
    """The database itself refuses two rows with the same (source, source_ref)."""
    store.upsert([make_obstruction()], now)
    clash = ObstructionRow(id="different-id", changed_at=now)
    _fill_row(clash, make_obstruction())  # same source and source_ref, different id
    with Session(store.engine) as session, pytest.raises(IntegrityError):
        session.add(clash)
        session.commit()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),  # Render's form
        ("postgresql://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./openpath.db", "sqlite:///./openpath.db"),  # unchanged
    ],
)
def test_render_database_urls_are_normalized(url, expected) -> None:
    """Render's postgres:// URL is rewritten for SQLAlchemy; SQLite passes through."""
    assert normalize_url(url) == expected
