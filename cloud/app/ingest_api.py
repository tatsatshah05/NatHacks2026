"""``POST /api/ingest/camera`` and ``GET /api/cameras`` (OP-213).

The vision worker posts one reading per camera view with the ``X-Worker-Key``
header; the map reads the latest readings. The key is the ``WORKER_KEY`` the
API was started with (Render generates it; ``.env.example`` documents it).
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from .camera_readings import CameraReadingIn, CameraReadingStore, to_feature

router = APIRouter(prefix="/api", tags=["cameras"])


def get_camera_store(request: Request) -> CameraReadingStore:
    store = getattr(request.app.state, "camera_store", None)
    if store is None:
        raise HTTPException(503, "Camera readings are not available on this instance.")
    return store


def require_worker_key(x_worker_key: Annotated[str | None, Header()] = None) -> None:
    """Only the vision worker may post readings. Compared in constant time."""
    expected = os.environ.get("WORKER_KEY")
    if not expected:
        raise HTTPException(503, "WORKER_KEY is not configured on this server.")
    if not x_worker_key or not secrets.compare_digest(x_worker_key, expected):
        raise HTTPException(401, "Missing or wrong X-Worker-Key header.")


class IngestResult(BaseModel):
    stored: bool
    source_ref: str
    label: str
    changed: bool  # True when the view's label differs from the previous reading


@router.post(
    "/ingest/camera",
    response_model=IngestResult,
    dependencies=[Depends(require_worker_key)],
)
def ingest_camera(
    reading: CameraReadingIn,
    store: Annotated[CameraReadingStore, Depends(get_camera_store)],
) -> IngestResult:
    """Store the latest reading for one camera view (upsert by ``source_ref``)."""
    changed = store.upsert(reading, datetime.now(UTC))
    return IngestResult(
        stored=True, source_ref=reading.source_ref, label=reading.label, changed=changed
    )


@router.get("/cameras")
def list_cameras(
    store: Annotated[CameraReadingStore, Depends(get_camera_store)],
) -> dict[str, Any]:
    """The latest unexpired reading for every camera view, as GeoJSON points."""
    now = datetime.now(UTC)
    rows = store.latest(now)
    return {
        "type": "FeatureCollection",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "features": [to_feature(row, now) for row in rows],
    }
