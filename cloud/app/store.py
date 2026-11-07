"""The obstruction store: the database table that holds every current barrier.

What this file does
-------------------
Adapters produce ``Obstruction`` objects (see ``schemas.py``). This store saves them
in one database table, one row per obstruction, and answers "what is active right
now?" for the API and the routing engine.

Who calls what
--------------
- The scheduler (OP-103) calls ``replace_source`` after each successful fetch and
  ``purge_expired`` on a timer.
- ``GET /api/obstructions`` and routing call ``active``.
- Live updates (OP-303) and push alerts (OP-304) use the returned ``ChangeSet`` to
  know which items were created, changed or removed.

Design choices
--------------
- Upsert by id (``source:source_ref``): a re-fetched elevator outage updates its row
  instead of adding a duplicate.
- "Material" change detection: a refresh that only moves the timestamps forward is
  not reported as a change, so users are not spammed with alerts every 2 minutes.
- Bounding box columns: each row stores min/max longitude and latitude, so the
  database can filter by area with plain comparisons instead of parsing GeoJSON.
- One log line per change, e.g. ``CHANGE city_lrt_devices:1234 new elevator_out
  'Elevator out of service'``. These logs are the team's debugging tool.
- Works on both SQLite (local, tests) and Postgres (Render) with the same code.
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

# SQLite limits how many "?" placeholders one SQL statement can have (999 on older
# builds). Queries with ``WHERE id IN (...)`` are split into chunks of this size so a
# 1,200-item fetch never hits that limit.
_CHUNK = 500


# ------------------------------------------------------------------- table model


class ObstructionRow(SQLModel, table=True):
    """The database row. Same fields as ``Obstruction`` plus bbox and bookkeeping.

    This is deliberately separate from ``Obstruction``: the Pydantic model is the
    contract the rest of the app uses, while this class is a storage detail. Enums are
    stored as plain strings and geometry as JSON, which both databases support.
    """

    __tablename__ = "obstruction"
    __table_args__ = (
        # Belt and braces: the id already encodes source + source_ref, but the database
        # also guarantees no two rows share them.
        UniqueConstraint("source", "source_ref", name="uq_obstruction_source_ref"),
        # purge_expired and active() filter on expires_at constantly.
        Index("ix_obstruction_expires_at", "expires_at"),
        # The ?bbox= filter compares these four columns.
        Index("ix_obstruction_bbox", "min_lng", "max_lng", "min_lat", "max_lat"),
    )

    id: str = Field(primary_key=True, max_length=270)  # "<source>:<source_ref>"
    source: str = Field(index=True, max_length=64)  # replace_source filters on this
    source_ref: str = Field(max_length=200)
    category: str = Field(max_length=16)
    kind: str = Field(max_length=32)
    effect: str = Field(max_length=16)
    title: str = Field(max_length=120)
    detail: str | None = Field(default=None, max_length=500)
    # GeoJSON geometry stored as a JSON document.
    geometry: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    confidence: str = Field(max_length=8)
    # All datetimes are stored in UTC.
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    observed_at: datetime
    expires_at: datetime
    url: str | None = Field(default=None, max_length=500)
    attribution: str = Field(max_length=200)
    # Bounding box of the geometry, precomputed for fast area filtering.
    min_lng: float
    min_lat: float
    max_lng: float
    max_lat: float
    # When the content last materially changed (not just refreshed).
    changed_at: datetime


# ------------------------------------------------------------------ change report


@dataclass(frozen=True)
class ChangeSet:
    """What one store call did: IDs created, materially updated, or removed.

    Live updates broadcast these over the WebSocket, and push alerts recheck saved
    routes that touch them. An empty ChangeSet means nothing to announce.
    """

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True when anything at all changed."""
        return bool(self.created or self.updated or self.removed)


# ---------------------------------------------------------------------- helpers


def _utc(value: datetime | None) -> datetime | None:
    """Normalise a datetime to UTC before it goes into the database."""
    return None if value is None else as_utc(value)


def _aware(value: datetime | None) -> datetime | None:
    """Restore the UTC time zone on datetimes read back from the database.

    SQLite has no datetime type: it stores text and hands back a naive datetime.
    Postgres keeps the zone. Either way, every value we stored was UTC, so we can
    safely re-attach UTC here.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _fill_row(row: ObstructionRow, item: Obstruction) -> None:
    """Copy every field of ``item`` onto ``row`` (used for both insert and update)."""
    box = item.geometry.bbox()
    row.source = item.source
    row.source_ref = item.source_ref
    row.category = item.category.value  # enum -> plain string for the database
    row.kind = item.kind.value
    row.effect = item.effect.value
    row.title = item.title
    row.detail = item.detail
    row.geometry = item.geometry.model_dump(mode="json")  # Pydantic model -> dict
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
    """Rebuild the ``Obstruction`` contract object from a database row."""
    # Pydantic re-validates everything here, so a corrupt row fails loudly.
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
    """Split a list of IDs into slices of at most _CHUNK items (see _CHUNK)."""
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


# ------------------------------------------------------------------------ store


class ObstructionStore:
    """All reads and writes of the obstruction table go through this class."""

    def __init__(self, engine: Engine) -> None:
        # The engine comes from db.make_engine(): Postgres on Render, SQLite locally.
        self.engine = engine

    def create_tables(self) -> None:
        """Create the obstruction table if it does not exist (safe to call every start)."""
        SQLModel.metadata.create_all(self.engine, tables=[ObstructionRow.__table__])

    def upsert(
        self, items: Iterable[Obstruction], now: datetime | None = None
    ) -> ChangeSet:
        """Insert new items and update existing ones. Never removes anything.

        Returns which IDs were created and which were materially updated. A refresh
        that only moves ``observed_at`` / ``expires_at`` forward updates the row but is
        NOT reported as a change (see ``Obstruction.content_key``).
        """
        changes = ChangeSet()
        # Deduplicate by id: if a source lists the same item twice, the last copy wins.
        latest = {item.id: item for item in items}
        if not latest:
            return changes
        changed_at = _utc(now or datetime.now(UTC))
        with Session(self.engine) as session:
            # Load every existing row for these IDs in one go (chunked for SQLite).
            existing = self._rows(session, list(latest))
            for item_id, item in latest.items():
                row = existing.get(item_id)
                if row is None:
                    # Brand new item: insert and report as created.
                    row = ObstructionRow(id=item_id, changed_at=changed_at)  # type: ignore[call-arg]
                    _fill_row(row, item)
                    session.add(row)
                    changes.created.append(item_id)
                    log.info(
                        "CHANGE %s new %s %r", item_id, item.kind.value, item.title
                    )
                    continue
                # Existing item: compare the user-visible content BEFORE overwriting it,
                # so we know whether this is real news or just a timestamp refresh.
                material = _to_obstruction(row).content_key() != item.content_key()
                _fill_row(row, item)  # always refresh (keeps expires_at moving forward)
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
        """Make the store hold exactly ``items`` for ``source``.

        This is what the scheduler calls after a SUCCESSFUL fetch: items the source no
        longer reports (an elevator that is working again) are removed. It must never
        be called with a partial or failed result, because that would delete real
        barriers. Adapters enforce this by raising on failure.
        """
        items = list(items)
        # Guard against an adapter bug that returns another source's items, which would
        # otherwise delete that other source's data.
        wrong = sorted({item.source for item in items if item.source != source})
        if wrong:
            raise ValueError(
                f"replace_source({source!r}) got items from other sources: {wrong}"
            )
        changes = self.upsert(items, now)
        keep = {item.id for item in items}
        with Session(self.engine) as session:
            # Everything the store currently holds for this source...
            held = session.exec(
                select(ObstructionRow.id).where(ObstructionRow.source == source)
            ).all()
            # ...minus what the source still reports = what to remove.
            gone = sorted(set(held) - keep)
            self._delete(session, gone)
            session.commit()
        for item_id in gone:
            log.info("CHANGE %s removed", item_id)
        return ChangeSet(changes.created, changes.updated, gone)

    def purge_expired(self, now: datetime) -> list[str]:
        """Delete items whose ``expires_at`` has passed. Returns their IDs.

        This is the safety net for a source that goes silent: its items age out
        instead of staying on the map forever. The scheduler runs it on a timer.
        """
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
        """Items that have started and not expired, optionally inside ``bbox``.

        This is the read path for the API and routing. ``bbox`` filtering uses the
        precomputed bounding-box columns, so it is a plain indexed comparison.
        """
        cutoff = _utc(now)
        query = select(ObstructionRow).where(
            ObstructionRow.expires_at > cutoff,  # not expired
            or_(  # started, or has no scheduled start
                col(ObstructionRow.starts_at).is_(None),
                ObstructionRow.starts_at <= cutoff,
            ),
        )
        if source is not None:
            query = query.where(ObstructionRow.source == source)
        if bbox is not None:
            # Two boxes overlap when neither is entirely to one side of the other.
            query = query.where(
                ObstructionRow.min_lng <= bbox.max_lng,
                ObstructionRow.max_lng >= bbox.min_lng,
                ObstructionRow.min_lat <= bbox.max_lat,
                ObstructionRow.max_lat >= bbox.min_lat,
            )
        # Stable order: grouped by category (matches the route report), then by id.
        query = query.order_by(ObstructionRow.category, ObstructionRow.id)
        with Session(self.engine) as session:
            return [_to_obstruction(row) for row in session.exec(query).all()]

    def get(self, item_id: str) -> Obstruction | None:
        """One item by id, or None. Ignores expiry (useful for tests and debugging)."""
        with Session(self.engine) as session:
            row = session.get(ObstructionRow, item_id)
            return None if row is None else _to_obstruction(row)

    @staticmethod
    def _rows(session: Session, ids: Sequence[str]) -> dict[str, ObstructionRow]:
        """Load rows for the given IDs, chunked to respect SQLite's parameter limit."""
        rows: dict[str, ObstructionRow] = {}
        for chunk in _chunks(ids):
            for row in session.exec(
                select(ObstructionRow).where(col(ObstructionRow.id).in_(chunk))
            ):
                rows[row.id] = row
        return rows

    @staticmethod
    def _delete(session: Session, ids: Sequence[str]) -> None:
        """Delete rows by id, chunked like _rows."""
        for chunk in _chunks(ids):
            session.exec(
                delete(ObstructionRow).where(col(ObstructionRow.id).in_(chunk))
            )  # type: ignore[call-overload]
