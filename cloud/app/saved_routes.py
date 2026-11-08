"""Saved routes (OP-304): the trips a browser asked us to watch, and the change rules.

A saved route is a from/to pair with a profile and a name, owned by an anonymous
``client_id`` that the browser generates and keeps (no accounts, decided November 7).
When it is saved, the baseline route (the "usual route") is computed at once: its
edges and ETA. The ``RouteWatcher`` in ``push.py`` rechecks a saved route whenever
a hazard changes on one of its current or usual edges, and ``decide`` below says
whether that recheck is worth a push:

- **changed**: the best route's edge set differs from the last notified one, or its
  ETA is at least ``ETA_RISE`` (20%) above the last notified ETA;
- **impossible**: no route exists for the profile any more;
- **clear**: a route that was changed or impossible has recovered: the usual route
  is open again and both it and the best route are within ``RECOVERY_TOLERANCE``
  (5%) of the baseline ETA ("Your usual route to <name> is clear again").

One refinement keeps alerts honest: when the usual route is still open and as fast
as it was, a different best route (a hazard elsewhere cleared and something better
appeared) is recorded without a push, because nothing got in the user's way.

Nothing here sends anything or touches the network; ``push.py`` does that.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import JSON, Column, Index
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, col, select

from .routing import Route

log = logging.getLogger("openpath.saved_routes")

MAX_ROUTES_PER_CLIENT = 10
ETA_RISE = 1.2  # push when the ETA is at least this multiple of the last notified ETA
RECOVERY_TOLERANCE = 1.05  # "clear again" when back within 5% of the baseline ETA

Status = Literal["ok", "changed", "impossible"]
Notice = Literal["changed", "impossible", "clear"]


class LimitReached(Exception):
    """A client already holds as many rows as it may (``429`` at the API)."""


# ------------------------------------------------------------------- table


class SavedRouteRow(SQLModel, table=True):
    """One watched trip. Coordinates, a name and a profile: no personal data."""

    __tablename__ = "saved_route"
    __table_args__ = (
        # for_client() filters on client_id and orders by created_at; this index
        # serves both, so the database no longer sorts the rows (OP-404). The older
        # single-column index stays: nothing is dropped from a live database.
        Index("ix_saved_route_client_created", "client_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    client_id: str = Field(index=True, max_length=64)
    name: str = Field(max_length=80)
    from_lat: float
    from_lng: float
    to_lat: float
    to_lng: float
    profile: str = Field(max_length=16)
    created_at: datetime
    # The usual route, as saved: what "clear again" means.
    baseline_eta_min: float | None = None
    baseline_edge_ids: list[int] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    # The route as last notified (or as saved): the recheck compares against these.
    last_eta_min: float | None = None
    last_edge_ids: list[int] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    last_status: str = Field(default="ok", max_length=10)
    last_checked_at: datetime | None = None
    last_push_at: datetime | None = None
    # What the browser was last told, and whether a push is owed (rate limit).
    notified_status: str = Field(default="ok", max_length=10)
    pending: bool = False


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything stored is UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def aware_row(row: SavedRouteRow) -> SavedRouteRow:
    row.created_at = _aware(row.created_at)  # type: ignore[assignment]
    row.last_checked_at = _aware(row.last_checked_at)
    row.last_push_at = _aware(row.last_push_at)
    return row


# ------------------------------------------------------------------- store


class SavedRouteStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine, tables=[SavedRouteRow.__table__])

    def add(
        self,
        *,
        client_id: str,
        name: str,
        from_lat: float,
        from_lng: float,
        to_lat: float,
        to_lng: float,
        profile: str,
        baseline: Route | None,
        now: datetime,
    ) -> SavedRouteRow:
        """Store a route with its baseline. Raises ``LimitReached`` at the client's cap."""
        now = now.astimezone(UTC)
        with Session(self.engine) as session:
            count = len(
                session.exec(
                    select(SavedRouteRow.id).where(SavedRouteRow.client_id == client_id)
                ).all()
            )
            if count >= MAX_ROUTES_PER_CLIENT:
                raise LimitReached(
                    f"A client may save at most {MAX_ROUTES_PER_CLIENT} routes."
                )
            row = SavedRouteRow(
                client_id=client_id,
                name=name,
                from_lat=from_lat,
                from_lng=from_lng,
                to_lat=to_lat,
                to_lng=to_lng,
                profile=profile,
                created_at=now,
                baseline_eta_min=baseline.eta_min if baseline else None,
                baseline_edge_ids=list(baseline.edges) if baseline else [],
                last_eta_min=baseline.eta_min if baseline else None,
                last_edge_ids=sorted(set(baseline.edges)) if baseline else [],
                last_status="ok" if baseline else "impossible",
                notified_status="ok" if baseline else "impossible",
                last_checked_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            log.info(
                "SAVED route %s %s %s eta %s min, %d edges",
                row.id,
                row.profile,
                row.name,
                row.baseline_eta_min,
                len(row.last_edge_ids),
            )
            return aware_row(row)

    def for_client(self, client_id: str) -> list[SavedRouteRow]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(SavedRouteRow)
                .where(SavedRouteRow.client_id == client_id)
                .order_by(col(SavedRouteRow.created_at), col(SavedRouteRow.id))
            ).all()
            for row in rows:
                session.expunge(row)
            return [aware_row(row) for row in rows]

    def all(self) -> list[SavedRouteRow]:
        """Every saved route, for the watcher's edge index."""
        with Session(self.engine) as session:
            rows = session.exec(
                select(SavedRouteRow).order_by(col(SavedRouteRow.id))
            ).all()
            for row in rows:
                session.expunge(row)
            return [aware_row(row) for row in rows]

    def get(self, route_id: int) -> SavedRouteRow | None:
        with Session(self.engine) as session:
            row = session.get(SavedRouteRow, route_id)
            if row is None:
                return None
            session.expunge(row)
            return aware_row(row)

    def delete(self, route_id: int, client_id: str) -> bool:
        """Remove a client's route. False when it is not theirs or does not exist."""
        with Session(self.engine) as session:
            row = session.get(SavedRouteRow, route_id)
            if row is None or row.client_id != client_id:
                return False
            session.delete(row)
            session.commit()
            log.info("SAVED route %s deleted", route_id)
            return True

    def update(self, route_id: int, **fields: Any) -> SavedRouteRow | None:
        """Set bookkeeping columns on one row; None when the row is gone (deleted)."""
        with Session(self.engine) as session:
            row = session.get(SavedRouteRow, route_id)
            if row is None:
                return None
            for name, value in fields.items():
                setattr(row, name, value)
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return aware_row(row)


# ------------------------------------------------------------- change rules


@dataclass(frozen=True)
class RouteState:
    """What the watcher knows about a saved route before a recheck."""

    status: Status
    last_eta_min: float | None
    last_edge_ids: Sequence[int]
    baseline_eta_min: float | None

    @classmethod
    def of(cls, row: SavedRouteRow) -> RouteState:
        return cls(
            row.last_status,  # type: ignore[arg-type]  # stored from a Status
            row.last_eta_min,
            tuple(row.last_edge_ids),
            row.baseline_eta_min,
        )


@dataclass(frozen=True)
class Usual:
    """The usual (baseline) route under the current hazards."""

    blocked: bool  # one of its edges is removed for this profile right now
    eta_min: float | None  # its ETA now; None when blocked


@dataclass(frozen=True)
class Decision:
    """What a recheck found: the new state and whether it deserves a push."""

    status: Status
    notice: Notice | None
    eta_min: float | None
    edge_ids: tuple[int, ...]
    # True for a route saved while impossible: the first route found becomes its
    # usual route.
    rebaseline: bool = False

    @property
    def changed(self) -> bool:
        return self.notice is not None


def decide(previous: RouteState, best: Route | None, usual: Usual | None) -> Decision:
    """Apply the change rules to the best route found now (``None`` when no route).

    ``usual`` is the baseline route under the current hazards (``None`` when the
    route has no baseline: it was impossible when saved).
    """
    if best is None:
        notice: Notice | None = (
            "impossible" if previous.status != "impossible" else None
        )
        return Decision("impossible", notice, None, tuple(previous.last_edge_ids))

    edges = tuple(sorted(set(best.edges)))
    baseline = previous.baseline_eta_min
    if baseline is None or usual is None:
        # Saved while impossible: this route is the first one, so it becomes the
        # usual route, and the browser hears that the trip is possible again.
        notice = "clear" if previous.status != "ok" else None
        return Decision("ok", notice, best.eta_min, edges, rebaseline=True)

    edges_changed = set(edges) != set(previous.last_edge_ids)
    eta_up = previous.last_eta_min is not None and best.eta_min >= (
        ETA_RISE * previous.last_eta_min
    )
    limit = RECOVERY_TOLERANCE * baseline
    usual_fine = (
        not usual.blocked and usual.eta_min is not None and usual.eta_min <= limit
    )
    recovered = usual_fine and best.eta_min <= limit

    if previous.status == "ok":
        if not edges_changed and not eta_up:
            return Decision(
                "ok", None, previous.last_eta_min, tuple(previous.last_edge_ids)
            )
        if recovered:
            # The usual route is still open and as fast: a better route appeared.
            return Decision("ok", None, best.eta_min, edges)
        return Decision("changed", "changed", best.eta_min, edges)

    if recovered:
        return Decision("ok", "clear", best.eta_min, edges)
    if previous.status == "impossible" or edges_changed or eta_up:
        # A route exists again but not the usual one, or the detour changed again.
        return Decision("changed", "changed", best.eta_min, edges)
    return Decision(
        previous.status, None, previous.last_eta_min, tuple(previous.last_edge_ids)
    )


def watched_edges(row: SavedRouteRow) -> set[int]:
    """The edges whose hazards matter to a route: the current and the usual one."""
    return {int(edge) for edge in (*row.last_edge_ids, *row.baseline_edge_ids)}


def edge_index(rows: Iterable[SavedRouteRow]) -> tuple[dict[int, set[int]], set[int]]:
    """``edge -> saved route ids`` plus the ids of routes that are impossible right now.

    Each route is indexed by its current and its usual edges, so a detoured route is
    rechecked when the closure on its usual route clears. Impossible routes may be
    freed by any change, so the watcher rechecks them on every change.
    """
    by_edge: dict[int, set[int]] = {}
    impossible: set[int] = set()
    for row in rows:
        if row.id is None:
            continue
        if row.last_status == "impossible":
            impossible.add(row.id)
        for edge in watched_edges(row):
            by_edge.setdefault(edge, set()).add(row.id)
    return by_edge, impossible
