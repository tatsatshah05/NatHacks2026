"""Camera readings: what the vision worker saw on each public camera (OP-213).

The flow
    The vision worker (OP-204) classifies a frame from a 511 Alberta camera view
    and posts one ``CameraReadingIn`` to ``POST /api/ingest/camera``. This module
    stores the latest reading per view (``camera_reading`` table) and serves them
    on ``GET /api/cameras``. The black-ice rule (OP-205) reads the same table to
    raise ``ice_risk`` obstructions near a camera when the conditions call for it.

The payload is the shape Kaleb proposed for OP-204, accepted by the lead on
November 8, 2026 and posted on issue #47. Changing it means changing the worker
too (AGENTS.md, Contracts).

Readings are not obstructions. A camera says "the road here looks wet"; whether that
is a hazard depends on the weather and on nearby crossings, which OP-205 decides.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Index
from sqlalchemy.engine import Engine
from sqlmodel import Field as Column
from sqlmodel import Session, SQLModel, select

log = logging.getLogger("openpath.cameras")

# The six classes the model knows plus "unknown" for frames it could not judge.
Condition = Literal["dry", "wet", "snow_partial", "snow_covered", "ice", "unknown"]
Mode = Literal["live", "replay"]
SOURCE = "camera_model"
DEFAULT_ATTRIBUTION = "Road camera data: 511 Alberta, Government of Alberta"

# Limits we chose, not measured values. The worker posts every 5 minutes and sets
# readings to expire 15 minutes after the frame, so these leave plenty of room.
MAX_LIFETIME = timedelta(hours=2)  # longest a single reading may stay trusted
CLOCK_SKEW = timedelta(seconds=60)  # how far the worker's clock may run ahead of ours


def _plain_number(value: Any) -> Any:
    """JSON numbers only: no booleans and no numeric strings (``true`` is not camera 1)."""
    if isinstance(value, bool | str):
        # ValueError on purpose: pydantic turns it into a 422; a TypeError would be a 500.
        raise ValueError("must be a JSON number")  # noqa: TRY004
    return value


class CameraReadingIn(BaseModel):
    """One reading as the worker posts it (agreed payload, issue #47)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["camera_model"] = SOURCE
    source_ref: str = Field(min_length=1, max_length=64, examples=["511:1234:1"])
    camera_id: int = Field(ge=0)
    view_id: int = Field(ge=0)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: Condition
    score: float | None = Field(default=None, ge=0, le=1)  # model confidence, if any
    reason: str | None = Field(default=None, max_length=120)  # why it is unknown
    observed_at: datetime  # when the frame was taken
    expires_at: datetime  # when the reading stops being trusted without a newer one
    model_version: str = Field(min_length=1, max_length=128)  # model SHA-256
    mode: Mode  # must match the API's DATA_MODE (checked in ingest_api)
    attribution: str = Field(default=DEFAULT_ATTRIBUTION, max_length=200)

    _numbers = field_validator(
        "camera_id", "view_id", "latitude", "longitude", "score", mode="before"
    )(_plain_number)

    @field_validator("observed_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("camera times must include a time zone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _consistent(self) -> CameraReadingIn:
        expected_ref = f"511:{self.camera_id}:{self.view_id}"
        if self.source_ref != expected_ref:
            raise ValueError(f"source_ref must be {expected_ref!r} for these ids")
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be after observed_at")
        if self.expires_at - self.observed_at > MAX_LIFETIME:
            raise ValueError("expires_at may be at most 2 hours after observed_at")
        return self


class CameraReadingRow(SQLModel, table=True):
    """Latest reading per camera view. ``source_ref`` is the key, so a view has one row."""

    __tablename__ = "camera_reading"
    __table_args__ = (Index("ix_camera_reading_expires_at", "expires_at"),)

    source_ref: str = Column(primary_key=True, max_length=64)
    source: str = Column(default=SOURCE, max_length=32)
    camera_id: int
    view_id: int
    latitude: float
    longitude: float
    label: str = Column(max_length=16)
    score: float | None = None
    reason: str | None = Column(default=None, max_length=120)
    observed_at: datetime
    expires_at: datetime
    model_version: str = Column(max_length=128)
    mode: str = Column(max_length=8)
    attribution: str = Column(max_length=200)
    received_at: datetime  # when the API stored it
    changed_at: datetime  # when the label last changed


# Every payload field the row copies on each upsert.
_COPIED = (
    "source", "camera_id", "view_id", "latitude", "longitude", "label", "score",
    "reason", "observed_at", "expires_at", "model_version", "mode", "attribution",
)  # fmt: skip


class Outcome(StrEnum):
    NEW = "new"  # first reading for this view
    CHANGED = "changed"  # the label differs from the stored one
    SAME = "same"  # same label, fresher times
    OLDER = "older"  # older than the stored reading; ignored


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; everything stored is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _aware_row(row: CameraReadingRow) -> CameraReadingRow:
    row.observed_at = _aware(row.observed_at)
    row.expires_at = _aware(row.expires_at)
    row.received_at = _aware(row.received_at)
    row.changed_at = _aware(row.changed_at)
    return row


class CameraReadingStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine, tables=[CameraReadingRow.__table__])

    def upsert(self, reading: CameraReadingIn, now: datetime) -> Outcome:
        """Store the reading for its view, unless a newer one is already stored.

        Retries and out-of-order deliveries are expected, so an older reading never
        replaces a newer one. A reading with the same ``observed_at`` replaces it
        (an idempotent retry).
        """
        now = now.astimezone(UTC)
        with Session(self.engine) as session:
            row = session.get(CameraReadingRow, reading.source_ref)
            if row is None:
                row = CameraReadingRow(
                    source_ref=reading.source_ref,
                    **{name: getattr(reading, name) for name in _COPIED},
                    received_at=now,
                    changed_at=now,
                )
                session.add(row)
                session.commit()
                log.info("CHANGE camera %s new %s", reading.source_ref, reading.label)
                return Outcome.NEW
            if reading.observed_at < _aware(row.observed_at):
                return Outcome.OLDER
            changed = row.label != reading.label
            for name in _COPIED:
                setattr(row, name, getattr(reading, name))
            row.received_at = now
            if changed:
                row.changed_at = now
                log.info("CHANGE camera %s %s", reading.source_ref, reading.label)
            session.add(row)
            session.commit()
            return Outcome.CHANGED if changed else Outcome.SAME

    def latest(self, now: datetime) -> list[CameraReadingRow]:
        """Every view's latest reading that has not expired, by camera then view."""
        cutoff = now.astimezone(UTC)
        with Session(self.engine) as session:
            rows = session.exec(
                select(CameraReadingRow)
                .where(CameraReadingRow.expires_at > cutoff)
                .order_by(CameraReadingRow.camera_id, CameraReadingRow.view_id)
            ).all()
            for row in rows:
                _aware_row(row)
                session.expunge(row)
            return list(rows)

    def purge_expired(self, now: datetime, older_than_days: int = 7) -> int:
        """Delete readings that expired more than ``older_than_days`` ago. Returns the count."""
        cutoff = now.astimezone(UTC) - timedelta(days=older_than_days)
        with Session(self.engine) as session:
            rows = session.exec(
                select(CameraReadingRow).where(CameraReadingRow.expires_at < cutoff)
            ).all()
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)


# --------------------------------------------------------------- GET /api/cameras


def _iso(value: datetime) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")


class CameraPoint(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]  # [lng, lat], as GeoJSON requires


class CameraProperties(BaseModel):
    source: str
    source_ref: str
    camera_id: int
    view_id: int
    label: Condition
    score: float | None
    reason: str | None
    observed_at: str  # ISO 8601 UTC
    expires_at: str
    age_s: float  # seconds since the frame was taken
    model_version: str
    mode: Mode
    attribution: str


class CameraFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: CameraPoint
    properties: CameraProperties


class CameraCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    generated_at: str
    features: list[CameraFeature]


def to_feature(row: CameraReadingRow, now: datetime) -> CameraFeature:
    """A GeoJSON point for the map, with the reading and its age."""
    observed = _aware(row.observed_at)
    return CameraFeature(
        id=row.source_ref,
        geometry=CameraPoint(coordinates=(row.longitude, row.latitude)),
        properties=CameraProperties(
            source=row.source,
            source_ref=row.source_ref,
            camera_id=row.camera_id,
            view_id=row.view_id,
            label=row.label,  # type: ignore[arg-type]  # stored from a validated Condition
            score=row.score,
            reason=row.reason,
            observed_at=_iso(observed),
            expires_at=_iso(row.expires_at),
            age_s=round((now.astimezone(UTC) - observed).total_seconds(), 1),
            model_version=row.model_version,
            mode=row.mode,  # type: ignore[arg-type]  # stored from a validated Mode
            attribution=row.attribution,
        ),
    )
