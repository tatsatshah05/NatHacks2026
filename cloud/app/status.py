"""``GET /api/status``: how fresh each data source is (OP-103).

The web app's ``/sources`` page and the city dashboard read this to show, per
source, when it last succeeded, whether it is stale, how many items it holds and
the last error. It also repeats the data mode and the deployed version, so one
call tells an operator everything about the API's health.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .scheduler import SourceStatus

router = APIRouter(prefix="/api", tags=["ops"])


class StatusResponse(BaseModel):
    mode: str  # "live" or "replay"; replay must be shown as a badge in the web app
    version: str  # the deployed git commit, or "dev"
    scheduler_running: bool
    generated_at: datetime
    sources: list[SourceStatus]


@router.get("/status", response_model=StatusResponse)
def get_status(request: Request) -> StatusResponse:
    """Freshness, item count and last error for every scheduled source."""
    state = request.app.state
    scheduler = getattr(state, "scheduler", None)
    return StatusResponse(
        mode=getattr(state, "mode", "live"),
        version=getattr(state, "version", "dev"),
        scheduler_running=bool(scheduler and scheduler.running),
        generated_at=datetime.now(UTC),
        sources=scheduler.statuses() if scheduler else [],
    )
