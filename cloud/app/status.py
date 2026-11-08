"""``GET /api/status``: how fresh each data source is (OP-103).

The web app's ``/sources`` page and the city dashboard read this to show, per
source, when it last succeeded, whether it is stale, how many items it holds and
the last error. It also repeats the data mode and the deployed version, so one
call tells an operator everything about the API's health.

OP-412 adds ``cameras``: whether the road cameras are reporting and where ice
risk comes from right now (``camera_status.py``). It is measured at the app's
clock, like the map and route hazards, so a replay describes the recorded moment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .camera_status import CamerasStatus, camera_status
from .obstructions_api import get_now
from .scheduler import SourceStatus

router = APIRouter(prefix="/api", tags=["ops"])


class StatusResponse(BaseModel):
    mode: str  # "live" or "replay"; replay must be shown as a badge in the web app
    version: str  # the deployed git commit, or "dev"
    scheduler_running: bool
    generated_at: datetime
    sources: list[SourceStatus]
    # OP-412: camera readings and the ice-risk fallback. The endpoint always sets it;
    # None means the camera table could not be read, and the web app then says
    # nothing about cameras. Optional so other code that builds this model is unchanged.
    cameras: CamerasStatus | None = None


@router.get("/status", response_model=StatusResponse)
def get_status(
    request: Request, now: Annotated[datetime, Depends(get_now)]
) -> StatusResponse:
    """Freshness, item count and last error for every scheduled source."""
    state = request.app.state
    scheduler = getattr(state, "scheduler", None)
    return StatusResponse(
        mode=getattr(state, "mode", "live"),
        version=getattr(state, "version", "dev"),
        scheduler_running=bool(scheduler and scheduler.running),
        generated_at=datetime.now(UTC),
        sources=scheduler.statuses() if scheduler else [],
        cameras=camera_status(
            getattr(state, "camera_store", None),
            getattr(state, "weather_service", None),
            now,
        ),
    )
