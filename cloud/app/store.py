"""The obstruction store: one row per obstruction, upserted by (source, source_ref).

The scheduler (OP-103) calls ``replace_source`` after each successful fetch, and
``purge_expired`` on a timer. Routing and the API read with ``active``. Every change
is logged as one line, for example
``CHANGE city_lrt_devices:1234 new elevator_out "Elevator out of service"``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint, delete, or_
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, col, select

from .schemas import BBox, Obstruction, as_utc

log = logging.getLogger("openpath.store")

# Stay well under SQLite's limit on bound parameters per statement.
_CHUNK = 500


class ObstructionRow(SQLModel, table=True):
    __tablename__ = "obstruction"
    __table_args__ = (
        UniqueConstraint("source", "source_ref", name="uq_obstruction_source_ref"),
        Index("ix_obstruction_expires_at", "expires_at"),
        Index("ix_obstruction_bbox", "min_lng", "max_lng", "min_lat", "max_lat"),
    )

    id: str = Field(primary_key=True, max_length=270)
    source: str = Field(index=True, max_length=64)
    source_ref: str = Field(max_length=200)
    category: str = Field(max_length=16)
    kind: str = Field(max_length=32)
    effect: str = Field(max_length=16)
    title: str = Field(max_length=120)
    detail: str | None = Field(default=None, max_length=500)
    geometry: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    confidence: str = Field(max_length=8)
    # All datetimes are stored in UTC.
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    observed_at: datetime
    expires_at: datetime
    url: str | None = Field(default=None, max_length=500)
    attribution: str = Field(max_length=200)
    min_lng: float
    min_lat: float
    max_lng: float
    max_lat: float
    changed_at: datetime


@dataclass(frozen=True)
class ChangeSet:
    """IDs that were created, materially updated or removed by one store call."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.removed)


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else as_utc(value)


def _aware(value: datetime | None) -> datetime | None:
    """Rows read back from SQLite can be naive; they are always UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _fill_row(row: ObstructionRow, item: Obstruction) -> None:
    box = item.geometry.bbox()
    row.source = item.source
    row.source_ref = item.source_ref
    row.category = item.category.value
    row.kind = item.kind.value
    row.effect = item.effect.value
    row.title = item.title
    row.detail = item.detail
    row.geometry = item.geometry.model_dump(mode="json")
    row.confidence = item.confidence.value
    row.starts_at = _utc(item.starts_at)
    row.ends_at = _utc(item.ends_at)
    row.observed_at = _utc(item.observed_at)
    row.expires_at = _utc(item.expires_at)
    row.url = item.url
    row.attribution = item.attribution
    row.min_lng, row.min_lat, row.max_lng, row.max_lat = (
        box.min_lng,
        box.min_lat,
        box.max_lng,
        box.max_lat,
    )


def _to_obstruction(row: ObstructionRow) -> Obstruction:
    return Obstruction(
        id=row.id,
        source=row.source,
        source_ref=row.source_ref,
        category=row.category,
        kind=row.kind,
        effect=row.effect,
        title=row.title,
        detail=row.detail,
        geometry=row.geometry,
        confidence=row.confidence,
        starts_at=_aware(row.starts_at),
        ends_at=_aware(row.ends_at),
        observed_at=_aware(row.observed_at),
        expires_at=_aware(row.expires_at),
        url=row.url,
        attribution=row.attribution,
    )


def _chunks(values: Sequence[str]) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


class ObstructionStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine, tables=[ObstructionRow.__table__])

    def upsert(
        self, items: Iterable[Obstruction], now: datetime | None = None
    ) -> ChangeSet:
        """Insert new items and update existing ones. Never removes anything."""
        changes = ChangeSet()
        latest = {item.id: item for item in items}  # the last copy of a duplicate wins
        if not latest:
            return changes
        changed_at = _utc(now or datetime.now(UTC))
        with Session(self.engine) as session:
            existing = self._rows(session, list(latest))
            for item_id, item in latest.items():
                row = existing.get(item_id)
                if row is None:
                    row = ObstructionRow(id=item_id, changed_at=changed_at)  # type: ignore[call-arg]
                    _fill_row(row, item)
                    session.add(row)
                    changes.created.append(item_id)
                    log.info(
                        "CHANGE %s new %s %r", item_id, item.kind.value, item.title
                    )
                    continue
                material = _to_obstruction(row).content_key() != item.content_key()
                _fill_row(row, item)
                if material:
                    row.changed_at = changed_at
                    changes.updated.append(item_id)
                    log.info(
                        "CHANGE %s updated %s %r", item_id, item.kind.value, item.title
                    )
                session.add(row)
            session.commit()
        return changes

    def replace_source(
        self, source: str, items: Iterable[Obstruction], now: datetime | None = None
    ) -> ChangeSet:
        """Make the store hold exactly ``items`` for ``source``. Call only after a successful fetch."""
        items = list(items)
        wrong = sorted({item.source for item in items if item.source != source})
        if wrong:
            raise ValueError(
                f"replace_source({source!r}) got items from other sources: {wrong}"
            )
        changes = self.upsert(items, now)
        keep = {item.id for item in items}
        with Session(self.engine) as session:
            held = session.exec(
                select(ObstructionRow.id).where(ObstructionRow.source == source)
            ).all()
            gone = sorted(set(held) - keep)
            self._delete(session, gone)
            session.commit()
        for item_id in gone:
            log.info("CHANGE %s removed", item_id)
        return ChangeSet(changes.created, changes.updated, gone)

    def purge_expired(self, now: datetime) -> list[str]:
        """Delete items whose ``expires_at`` has passed. Returns their IDs."""
        cutoff = _utc(now)
        with Session(self.engine) as session:
            gone = sorted(
                session.exec(
                    select(ObstructionRow.id).where(ObstructionRow.expires_at <= cutoff)
                ).all()
            )
            self._delete(session, gone)
            session.commit()
        for item_id in gone:
            log.info("CHANGE %s expired", item_id)
        return gone

    def active(
        self, now: datetime, bbox: BBox | None = None, source: str | None = None
    ) -> list[Obstruction]:
        """Items that have started and not expired, optionally inside ``bbox``."""
        cutoff = _utc(now)
        query = select(ObstructionRow).where(
            ObstructionRow.expires_at > cutoff,
            or_(
                col(ObstructionRow.starts_at).is_(None),
                ObstructionRow.starts_at <= cutoff,
            ),
        )
        if source is not None:
            query = query.where(ObstructionRow.source == source)
        if bbox is not None:
            query = query.where(
                ObstructionRow.min_lng <= bbox.max_lng,
                ObstructionRow.max_lng >= bbox.min_lng,
                ObstructionRow.min_lat <= bbox.max_lat,
                ObstructionRow.max_lat >= bbox.min_lat,
            )
        query = query.order_by(ObstructionRow.category, ObstructionRow.id)
        with Session(self.engine) as session:
            return [_to_obstruction(row) for row in session.exec(query).all()]

    def get(self, item_id: str) -> Obstruction | None:
        with Session(self.engine) as session:
            row = session.get(ObstructionRow, item_id)
            return None if row is None else _to_obstruction(row)

    @staticmethod
    def _rows(session: Session, ids: Sequence[str]) -> dict[str, ObstructionRow]:
        rows: dict[str, ObstructionRow] = {}
        for chunk in _chunks(ids):
            for row in session.exec(
                select(ObstructionRow).where(col(ObstructionRow.id).in_(chunk))
            ):
                rows[row.id] = row
        return rows

    @staticmethod
    def _delete(session: Session, ids: Sequence[str]) -> None:
        for chunk in _chunks(ids):
            session.exec(
                delete(ObstructionRow).where(col(ObstructionRow.id).in_(chunk))
            )  # type: ignore[call-overload]
