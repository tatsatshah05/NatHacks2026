"""Camera readings: what the vision worker saw on each public camera (OP-213).

The flow
    The vision worker (OP-204) classifies a frame from a 511 Alberta camera view
    and posts one ``CameraReadingIn`` to ``POST /api/ingest/camera``. This module
    stores the latest reading per view (``camera_reading`` table) and serves them
    on ``GET /api/cameras``. The black-ice rule (OP-205) reads the same table to
    raise ``ice_risk`` obstructions near a camera when the conditions call for it.

The payload is the shape Kaleb proposed in docs/OP-204.md and the lead accepted on
November 8, 2026. Changing it means changing the worker too (AGENTS.md, Contracts).

Readings are not obstructions. A camera says "the road here looks wet"; whether that
is a hazard depends on the weather and on nearby crossings, which OP-205 decides.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
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
DEFAULT_ATTRIBUTION = "Road camera data: 511 Alberta, Government of Alberta"


class CameraReadingIn(BaseModel):
    """One reading as the worker posts it (docs/OP-204.md, "Proposed ingest payload")."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["camera_model"] = "camera_model"
    source_ref: str = Field(min_length=1, max_length=64, examples=["511:1234:1"])
    camera_id: int = Field(ge=0)
    view_id: int = Field(ge=0)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: Condition
    score: float | None = Field(default=None, ge=0, le=1)  # model confidence, if any
    reason: str | None = Field(
        default=None, max_length=120
    )  # worker reason code for unknown
    observed_at: datetime  # when the frame was taken
    expires_at: datetime  # when the reading stops being trusted without a newer one
    model_version: str = Field(
        min_length=1, max_length=128
    )  # the model asset's SHA-256
    mode: Mode  # replay readings are accepted but never presented as live
    attribution: str = Field(default=DEFAULT_ATTRIBUTION, max_length=200)

    @field_validator("observed_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("camera times must include a time zone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _ordered(self) -> CameraReadingIn:
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be after observed_at")
        return self


class CameraReadingRow(SQLModel, table=True):
    """Latest reading per camera view. ``source_ref`` is the key, so a view has one row."""

    __tablename__ = "camera_reading"
    __table_args__ = (Index("ix_camera_reading_expires_at", "expires_at"),)

    source_ref: str = Column(primary_key=True, max_length=64)
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


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; everything stored is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class CameraReadingStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine, tables=[CameraReadingRow.__table__])

    def upsert(self, reading: CameraReadingIn, now: datetime) -> bool:
        """Store the reading for its view. Returns True when the label changed."""
        now = now.astimezone(UTC)
        with Session(self.engine) as session:
            row = session.get(CameraReadingRow, reading.source_ref)
            if row is None:
                row = CameraReadingRow(
                    source_ref=reading.source_ref,
                    camera_id=reading.camera_id,
                    view_id=reading.view_id,
                    latitude=reading.latitude,
                    longitude=reading.longitude,
                    label=reading.label,
                    score=reading.score,
                    reason=reading.reason,
                    observed_at=reading.observed_at,
                    expires_at=reading.expires_at,
                    model_version=reading.model_version,
                    mode=reading.mode,
                    attribution=reading.attribution,
                    received_at=now,
                    changed_at=now,
                )
                session.add(row)
                session.commit()
                log.info("CHANGE camera %s new %s", reading.source_ref, reading.label)
                return True
            changed = row.label != reading.label
            for name in (
                "camera_id", "view_id", "latitude", "longitude", "label", "score", "reason",
                "observed_at", "expires_at", "model_version", "mode", "attribution",
            ):  # fmt: skip
                setattr(row, name, getattr(reading, name))
            row.received_at = now
            if changed:
                row.changed_at = now
                log.info("CHANGE camera %s %s", reading.source_ref, reading.label)
            session.add(row)
            session.commit()
            return changed

    def latest(self, now: datetime) -> list[CameraReadingRow]:
        """Every view's latest reading that has not expired, oldest camera id first."""
        cutoff = now.astimezone(UTC)
        with Session(self.engine) as session:
            rows = session.exec(
                select(CameraReadingRow)
                .where(CameraReadingRow.expires_at > cutoff)
                .order_by(CameraReadingRow.camera_id, CameraReadingRow.view_id)
            ).all()
            for row in rows:
                row.observed_at = _aware(row.observed_at)
                row.expires_at = _aware(row.expires_at)
                row.received_at = _aware(row.received_at)
                row.changed_at = _aware(row.changed_at)
                session.expunge(row)
            return list(rows)

    def purge_expired(self, now: datetime, older_than_days: int = 7) -> int:
        """Delete readings that expired more than ``older_than_days`` ago. Returns the count."""
        from datetime import timedelta

        cutoff = now.astimezone(UTC) - timedelta(days=older_than_days)
        with Session(self.engine) as session:
            rows = session.exec(
                select(CameraReadingRow).where(CameraReadingRow.expires_at < cutoff)
            ).all()
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)


def to_feature(row: CameraReadingRow, now: datetime) -> dict[str, Any]:
    """A GeoJSON point for the map, with the reading and its age."""
    observed = _aware(row.observed_at)
    return {
        "type": "Feature",
        "id": row.source_ref,
        "geometry": {"type": "Point", "coordinates": [row.longitude, row.latitude]},
        "properties": {
            "source_ref": row.source_ref,
            "camera_id": row.camera_id,
            "view_id": row.view_id,
            "label": row.label,
            "score": row.score,
            "reason": row.reason,
            "observed_at": observed.isoformat().replace("+00:00", "Z"),
            "expires_at": _aware(row.expires_at).isoformat().replace("+00:00", "Z"),
            "age_s": round((now.astimezone(UTC) - observed).total_seconds(), 1),
            "model_version": row.model_version,
            "mode": row.mode,
            "attribution": row.attribution,
        },
    }
