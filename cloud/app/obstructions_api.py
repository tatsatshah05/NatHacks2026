"""``GET /api/obstructions``: the current obstructions as GeoJSON.

This is the endpoint the web app's map layer reads. It returns a GeoJSON
FeatureCollection (see ``schemas.ObstructionCollection``) that MapLibre can render
directly, with an optional ``?bbox=`` filter so a phone only downloads the items
on screen.

Wiring (done by OP-102)
-----------------------
The router is self-contained. The main app includes it and creates the store in
its lifespan::

    app.state.store = ObstructionStore(make_engine())
    app.include_router(obstructions_api.router)

``get_store`` and ``get_now`` are FastAPI dependencies, so tests can swap them out
(a test store, a fixed "now") without touching the endpoint code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from .schemas import BBox, ObstructionCollection
from .store import ObstructionStore

# All routes in this file live under /api and appear under "obstructions" in /docs.
router = APIRouter(prefix="/api", tags=["obstructions"])


def get_store(request: Request) -> ObstructionStore:
    """Dependency: the store the app created at startup."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        # A clear error beats an AttributeError deep inside a request.
        raise RuntimeError("app.state.store is not set; create it in the app lifespan")
    return store


def get_now(request: Request) -> datetime:
    """Dependency: the app's current time, from the same clock as the scheduler.

    In replay mode with ``REPLAY_AT`` the scheduler's clock is frozen so recorded
    closures stay in force; the map and route hazards must ask the store at that
    time too, or every recorded item looks expired. The app sets ``app.state.clock``
    at startup; without it (a bare test app) this is the real UTC time. Tests can
    still override this dependency to freeze the clock.
    """
    clock = getattr(request.app.state, "clock", None)
    return clock() if clock is not None else datetime.now(UTC)


def parse_bbox(value: str | None) -> BBox | None:
    """Turn the ``?bbox=min_lng,min_lat,max_lng,max_lat`` string into a BBox.

    Returns None when no bbox was given. Any problem (wrong number of parts, not a
    number, out of range, corners swapped) becomes a 422 with a message that tells
    the caller the expected format.
    """
    if value is None:
        return None
    parts = value.split(",")
    try:
        if len(parts) != 4:
            raise ValueError
        min_lng, min_lat, max_lng, max_lat = (float(part) for part in parts)
        # BBox itself validates the ranges and that min <= max.
        return BBox(min_lng=min_lng, min_lat=min_lat, max_lng=max_lng, max_lat=max_lat)
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=422,
            detail="bbox must be min_lng,min_lat,max_lng,max_lat in degrees, with min <= max",
        ) from error


@router.get("/obstructions", response_model=ObstructionCollection)
def list_obstructions(
    store: Annotated[ObstructionStore, Depends(get_store)],
    now: Annotated[datetime, Depends(get_now)],
    bbox: Annotated[
        str | None,
        Query(
            description="Only items inside this box: min_lng,min_lat,max_lng,max_lat",
            examples=["-113.60,53.50,-113.40,53.60"],
        ),
    ] = None,
) -> ObstructionCollection:
    """Every obstruction that has started and not expired, as a GeoJSON FeatureCollection."""
    # The store applies the start/expiry rules and the bbox filter in SQL.
    items = store.active(now, bbox=parse_bbox(bbox))
    return ObstructionCollection(
        generated_at=now, features=[item.to_feature() for item in items]
    )
