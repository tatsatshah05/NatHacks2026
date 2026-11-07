"""``GET /api/obstructions``: current obstructions as GeoJSON.

OP-102's app includes this router and sets ``app.state.store`` in its lifespan::

    app.state.store = ObstructionStore(make_engine())
    app.include_router(obstructions_api.router)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from .schemas import BBox, ObstructionCollection
from .store import ObstructionStore

router = APIRouter(prefix="/api", tags=["obstructions"])


def get_store(request: Request) -> ObstructionStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise RuntimeError("app.state.store is not set; create it in the app lifespan")
    return store


def get_now() -> datetime:
    return datetime.now(UTC)


def parse_bbox(value: str | None) -> BBox | None:
    if value is None:
        return None
    parts = value.split(",")
    try:
        if len(parts) != 4:
            raise ValueError
        min_lng, min_lat, max_lng, max_lat = (float(part) for part in parts)
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
    items = store.active(now, bbox=parse_bbox(bbox))
    return ObstructionCollection(
        generated_at=now, features=[item.to_feature() for item in items]
    )
