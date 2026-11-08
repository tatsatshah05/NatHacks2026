"""Daily item counts per source and the sudden-drop check (OP-407).

What it does
    After every successful fetch the scheduler records how many items the source
    holds in ``source_count_daily``: one row per source and UTC day. The first
    fetch of a day inserts the row; later fetches that day update it. The write
    returns a ``CountChange`` that compares today with the previous day's row,
    which ``GET /api/status`` shows per source as ``count_change``.

The drop rule
    ``drop`` is true when today's count is below ``DROP_FRACTION`` of yesterday's
    and yesterday held at least ``DROP_MIN_YESTERDAY`` items. A feed that went from
    120 items to 40 is probably broken or changed; one that went from 3 to 1 is
    just quiet. A drop is logged once per source and day as
    ``WARN quality <source>: items fell from <m> to <n>``. It never clears data,
    never fails the fetch and never changes routing: it is a flag for people.

Replay
    The scheduler passes its own clock, so under ``REPLAY_AT`` the day is the
    replay day and the comparison works the same against the fixtures.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, select

log = logging.getLogger("openpath.quality")

# Yesterday must have held at least this many items before a fall counts as a
# drop: a source with a handful of items swings by half every day for no reason.
DROP_MIN_YESTERDAY = 10
# Today below this fraction of yesterday is a drop.
DROP_FRACTION = 0.5


class SourceCountDailyRow(SQLModel, table=True):
    """One source's item count on one UTC day."""

    __tablename__ = "source_count_daily"

    source: str = Field(primary_key=True, max_length=64)
    day: date = Field(primary_key=True)  # the UTC calendar day
    item_count: int
    updated_at: datetime  # when the row was last written, UTC


class CountChange(BaseModel):
    """Today's item count against yesterday's, as ``GET /api/status`` shows it."""

    today: int
    yesterday: int | None = None  # None when no row exists for the previous day
    drop: bool = False


def is_drop(today: int, yesterday: int | None) -> bool:
    """The drop rule: below half of yesterday, and yesterday was at least 10."""
    if yesterday is None or yesterday < DROP_MIN_YESTERDAY:
        return False
    return today < yesterday * DROP_FRACTION


def utc_day(now: datetime) -> date:
    """The UTC calendar day of an aware datetime."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC).date()


class DailyCounts:
    """Reads and writes ``source_count_daily``."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._tables_ready = False
        # (source, day) pairs whose drop was already logged, so a drop is one line
        # per day however many fetches repeat it. In memory: a restart may log again.
        self._drop_logged: set[tuple[str, date]] = set()

    def create_tables(self) -> None:
        """Create the table if it does not exist (safe to call every start)."""
        SQLModel.metadata.create_all(
            self.engine, tables=[SourceCountDailyRow.__table__]
        )
        self._tables_ready = True

    def record(self, source: str, item_count: int, now: datetime) -> CountChange:
        """Write today's count for ``source`` and compare it with yesterday's row."""
        if not self._tables_ready:
            self.create_tables()
        today = utc_day(now)
        yesterday = today - timedelta(days=1)
        written_at = now.astimezone(UTC)
        with Session(self.engine) as session:
            row = session.get(SourceCountDailyRow, (source, today))
            if row is None:
                row = SourceCountDailyRow(
                    source=source,
                    day=today,
                    item_count=item_count,
                    updated_at=written_at,
                )
            else:
                row.item_count = item_count
                row.updated_at = written_at
            session.add(row)
            previous = session.get(SourceCountDailyRow, (source, yesterday))
            before = previous.item_count if previous is not None else None
            session.commit()
        change = CountChange(
            today=item_count, yesterday=before, drop=is_drop(item_count, before)
        )
        if change.drop and (source, today) not in self._drop_logged:
            self._drop_logged.add((source, today))
            log.warning(
                "WARN quality %s: items fell from %d to %d", source, before, item_count
            )
        return change

    def counts(self, source: str) -> list[SourceCountDailyRow]:
        """Every recorded day for ``source``, oldest first (tests and debugging)."""
        if not self._tables_ready:
            self.create_tables()
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(SourceCountDailyRow)
                    .where(SourceCountDailyRow.source == source)
                    .order_by(SourceCountDailyRow.day)  # type: ignore[arg-type]
                ).all()
            )


__all__ = [
    "DROP_FRACTION",
    "DROP_MIN_YESTERDAY",
    "CountChange",
    "DailyCounts",
    "SourceCountDailyRow",
    "is_drop",
    "utc_day",
]
