"""OP-101: the store upserts by (source, source_ref), reports changes and filters active items."""

from __future__ import annotations

import logging
from datetime import UTC, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from cloud.app.db import normalize_url
from cloud.app.schemas import BBox
from cloud.app.store import ObstructionRow, _fill_row


def test_upsert_creates_then_reports_no_change(store, make_obstruction, now) -> None:
    item = make_obstruction()
    first = store.upsert([item], now)
    assert first.created == [item.id] and not first.updated

    refreshed = make_obstruction(
        observed_at=now + timedelta(minutes=2), expires_at=now + timedelta(minutes=12)
    )
    second = store.upsert([refreshed], now + timedelta(minutes=2))
    assert not second.changed
    assert store.get(item.id).expires_at == now + timedelta(minutes=12)


def test_upsert_reports_material_updates(store, make_obstruction, now) -> None:
    store.upsert([make_obstruction()], now)
    changes = store.upsert(
        [make_obstruction(detail="Central Station, East Platform")], now
    )
    assert changes.updated == ["city_lrt_devices:1234"]
    assert store.get("city_lrt_devices:1234").detail == "Central Station, East Platform"


def test_duplicate_items_in_one_batch_keep_the_last(
    store, make_obstruction, now
) -> None:
    changes = store.upsert(
        [make_obstruction(title="First"), make_obstruction(title="Second")], now
    )
    assert changes.created == ["city_lrt_devices:1234"]
    assert store.get("city_lrt_devices:1234").title == "Second"


def test_replace_source_removes_items_no_longer_reported(
    store, make_obstruction, now
) -> None:
    store.replace_source(
        "city_lrt_devices", [make_obstruction(), make_obstruction(source_ref="99")], now
    )
    changes = store.replace_source("city_lrt_devices", [make_obstruction()], now)
    assert changes.removed == ["city_lrt_devices:99"]
    assert store.get("city_lrt_devices:99") is None


def test_replace_source_leaves_other_sources_alone(
    store, make_obstruction, now
) -> None:
    other = make_obstruction(
        source="city_trail_closures",
        source_ref="7",
        category="sidewalk",
        kind="trail_closed",
    )
    store.upsert([other], now)
    store.replace_source("city_lrt_devices", [], now)
    assert store.get(other.id) is not None


def test_replace_source_rejects_foreign_items(store, make_obstruction, now) -> None:
    with pytest.raises(ValueError, match="other sources"):
        store.replace_source("city_traffic_disruptions", [make_obstruction()], now)


def test_active_excludes_expired_and_future_items(store, make_obstruction, now) -> None:
    store.upsert(
        [
            make_obstruction(source_ref="current"),
            make_obstruction(
                source_ref="expired",
                observed_at=now - timedelta(hours=1),
                expires_at=now,
            ),
            make_obstruction(
                source_ref="future",
                starts_at=now + timedelta(days=1),
                expires_at=now + timedelta(days=2),
            ),
        ],
        now,
    )
    assert [item.source_ref for item in store.active(now)] == ["current"]


def test_active_filters_by_bbox_and_source(store, make_obstruction, now) -> None:
    downtown = make_obstruction(source_ref="downtown")
    south = make_obstruction(
        source_ref="south", geometry={"type": "Point", "coordinates": [-113.49, 53.43]}
    )
    road = make_obstruction(
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


def test_purge_expired_deletes_and_logs(store, make_obstruction, now, caplog) -> None:
    store.upsert([make_obstruction()], now)
    with caplog.at_level(logging.INFO, logger="openpath.store"):
        gone = store.purge_expired(now + timedelta(minutes=10))
    assert gone == ["city_lrt_devices:1234"]
    assert "CHANGE city_lrt_devices:1234 expired" in caplog.text


def test_changes_are_logged_one_line_each(store, make_obstruction, now, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="openpath.store"):
        store.upsert([make_obstruction()], now)
    assert (
        "CHANGE city_lrt_devices:1234 new elevator_out 'Elevator out of service'"
        in caplog.text
    )


def test_round_trip_keeps_utc_and_geometry(store, make_obstruction, now) -> None:
    item = make_obstruction(
        geometry={
            "type": "MultiLineString",
            "coordinates": [[[-113.5, 53.5], [-113.49, 53.51]]],
        },
    )
    store.upsert([item], now)
    loaded = store.get(item.id)
    assert loaded == item
    assert loaded.observed_at.tzinfo == UTC


def test_large_batches_are_chunked(store, make_obstruction, now) -> None:
    items = [make_obstruction(source_ref=str(n)) for n in range(1200)]
    assert len(store.replace_source("city_lrt_devices", items, now).created) == 1200
    assert (
        len(store.replace_source("city_lrt_devices", items[:10], now).removed) == 1190
    )


def test_source_and_ref_are_unique(store, make_obstruction, now) -> None:
    store.upsert([make_obstruction()], now)
    clash = ObstructionRow(id="different-id", changed_at=now)
    _fill_row(clash, make_obstruction())  # same source and source_ref, different id
    with Session(store.engine) as session, pytest.raises(IntegrityError):
        session.add(clash)
        session.commit()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./openpath.db", "sqlite:///./openpath.db"),
    ],
)
def test_render_database_urls_are_normalized(url, expected) -> None:
    assert normalize_url(url) == expected
