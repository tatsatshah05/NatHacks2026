"""``POST /api/ingest/camera`` and ``GET /api/cameras`` (OP-213).

The vision worker posts one reading per camera view with the ``X-Worker-Key``
header; the map reads the latest readings. The key is the ``WORKER_KEY`` the
API was started with (Render generates it; ``.env.example`` documents it).
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from .camera_readings import (
    CLOCK_SKEW,
    CameraCollection,
    CameraReadingIn,
    CameraReadingStore,
    Outcome,
    to_feature,
)
from .obstructions_api import get_now

router = APIRouter(prefix="/api", tags=["cameras"])


def get_camera_store(request: Request) -> CameraReadingStore:
    store = getattr(request.app.state, "camera_store", None)
    if store is None:
        raise HTTPException(503, "Camera readings are not available on this instance.")
    return store


def require_worker_key(x_worker_key: Annotated[str | None, Header()] = None) -> None:
    """Only the vision worker may post readings. Compared in constant time, as bytes."""
    expected = os.environ.get("WORKER_KEY")
    if not expected:
        raise HTTPException(503, "WORKER_KEY is not configured on this server.")
    # Bytes, so a header with non-ASCII characters is a plain mismatch, not a 500.
    if not x_worker_key or not secrets.compare_digest(
        x_worker_key.encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        raise HTTPException(401, "Missing or wrong X-Worker-Key header.")


class IngestResult(BaseModel):
    stored: bool  # False when a newer reading for this view is already stored
    source_ref: str
    label: str
    changed: bool  # True when the view's label differs from the previous reading
    outcome: Outcome


@router.post(
    "/ingest/camera",
    response_model=IngestResult,
    dependencies=[Depends(require_worker_key)],
)
def ingest_camera(
    reading: CameraReadingIn,
    request: Request,
    store: Annotated[CameraReadingStore, Depends(get_camera_store)],
) -> IngestResult:
    """Store the latest reading for one camera view (upsert by ``source_ref``)."""
    now = datetime.now(UTC)
    mode: str = request.app.state.mode
    if reading.mode != mode:
        # Never show a replayed reading on a live map, or a live one in a replay demo.
        raise HTTPException(
            409, f"This API runs in {mode} mode; the reading is {reading.mode}."
        )
    if reading.observed_at > now + CLOCK_SKEW:
        raise HTTPException(
            422, "observed_at is in the future; check the worker's clock."
        )
    outcome = store.upsert(reading, now)
    return IngestResult(
        stored=outcome is not Outcome.OLDER,
        source_ref=reading.source_ref,
        label=reading.label,
        changed=outcome in (Outcome.NEW, Outcome.CHANGED),
        outcome=outcome,
    )


@router.get("/cameras", response_model=CameraCollection)
def list_cameras(
    store: Annotated[CameraReadingStore, Depends(get_camera_store)],
    now: Annotated[datetime, Depends(get_now)],
) -> CameraCollection:
    """The latest unexpired reading for every camera view, as GeoJSON points.

    Read at the app's clock, like the map and route hazards, so a replay with a
    frozen or running clock (OP-305) shows the recorded readings of that time.
    """
    return CameraCollection(
        generated_at=now.isoformat().replace("+00:00", "Z"),
        features=[to_feature(row, now) for row in store.latest(now)],
    )
