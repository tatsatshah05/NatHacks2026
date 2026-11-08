"""``POST /api/admin/override``: seed a labelled test obstruction (testing only).

PLAN.md section 10 and AGENTS.md list this endpoint as the one write that is for
testing, behind the ``X-Admin-Key`` header. OP-406's end-to-end tests use it to
seed a live change: they add a ``blocked`` item on the route a page is following
and check that the page announces the change over ``WS /ws`` (OP-303). It is also
how a teammate can show a live reroute in a demo rehearsal without waiting for
the City to close a sidewalk.

What it does
    ``POST /api/admin/override``            stores one item in the shared store under
                                            the source ``admin_override``, with a
                                            short lifetime (``ttl_s``, 10 minutes by
                                            default, at most 1 hour), and rebuilds
                                            the hazard map at once so routing, the
                                            live hub and the saved-route watcher all
                                            see it within the same request.
    ``DELETE /api/admin/override/{ref}``    removes that item and rebuilds again.
    ``DELETE /api/admin/override``          removes every override.

Labelling
    Every override is a plain obstruction to the rest of the app (routing, the map,
    the route report), so it is labelled as a test in the data itself: the title
    starts with ``Test override:`` and the attribution reads ``Test override, not
    real data``. Nothing here can fake data in the demo path without that label.

Keys
    The key is the ``ADMIN_KEY`` the API was started with (Render generates one;
    ``.env.example`` documents it). Without it the endpoint answers 503, with a wrong
    one 401, exactly like ``X-Worker-Key`` on ``/api/ingest/camera``. Smoke tests
    against production never call it.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .obstructions_api import get_now, get_store
from .rate_limit import keyed_write_limit
from .schemas import Category, Confidence, Effect, Geometry, Kind, Obstruction
from .store import ObstructionStore
from .ws import matcher_of

log = logging.getLogger("openpath.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])

SOURCE = "admin_override"
ATTRIBUTION = "Test override, not real data"
TITLE_PREFIX = "Test override: "
DEFAULT_TTL_S = 600
MAX_TTL_S = 3600
REF_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def require_admin_key(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """Only the holder of ``ADMIN_KEY`` may write. Compared in constant time, as bytes."""
    expected = os.environ.get("ADMIN_KEY")
    if not expected:
        raise HTTPException(503, "ADMIN_KEY is not configured on this server.")
    if not x_admin_key or not secrets.compare_digest(
        x_admin_key.encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        raise HTTPException(401, "Missing or wrong X-Admin-Key header.")


class OverrideIn(BaseModel):
    """One test obstruction. The same fields as an ``Obstruction``, minus what the
    server fills in (source, id, times, attribution)."""

    model_config = ConfigDict(extra="forbid")

    source_ref: str = Field(pattern=REF_PATTERN, examples=["e2e-laptop"])
    title: str = Field(min_length=1, max_length=100, examples=["Sidewalk closed"])
    geometry: Geometry
    category: Category = Category.SIDEWALK
    kind: Kind = Kind.BLOCKED
    effect: Effect = Effect.BLOCKED
    confidence: Confidence = Confidence.HIGH
    detail: str | None = Field(default=None, max_length=500)
    # How long the item lives. Short on purpose: a forgotten override clears itself.
    ttl_s: int = Field(default=DEFAULT_TTL_S, ge=10, le=MAX_TTL_S)


class OverrideOut(BaseModel):
    id: str
    source: str
    source_ref: str
    title: str  # as stored, with the "Test override: " label
    effect: Effect
    observed_at: datetime
    expires_at: datetime
    # True when the hazard map was rebuilt in this request, so live clients and
    # saved-route checks already know. False only if a rebuild could not run.
    rebuilt: bool


def to_obstruction(body: OverrideIn, now: datetime) -> Obstruction:
    """The stored item: labelled as a test in its title and attribution."""
    return Obstruction(
        source=SOURCE,
        source_ref=body.source_ref,
        category=body.category,
        kind=body.kind,
        effect=body.effect,
        title=(TITLE_PREFIX + body.title)[:120],
        detail=body.detail,
        geometry=body.geometry,
        confidence=body.confidence,
        starts_at=None,
        ends_at=None,
        observed_at=now,
        expires_at=now + timedelta(seconds=body.ttl_s),
        url=None,
        attribution=ATTRIBUTION,
    )


def rebuild_hazards(request: Request, now: datetime) -> bool:
    """Rebuild the hazard map now, so the change reaches every listener at once.

    Routing itself never depends on this: the store already holds the change, and
    the next scheduled refresh picks it up. A failure is logged and reported as
    ``rebuilt: false``.
    """
    matcher = matcher_of(getattr(request.app.state, "hazard_provider", None))
    if matcher is None:
        return False
    try:
        return matcher.rebuild_now(now)
    except Exception:  # never turn a test write into a 500
        log.exception("ADMIN hazard rebuild failed after an override")
        return False


@router.post(
    "/override",
    response_model=OverrideOut,
    status_code=201,
    # The key first (OP-401): a request without it never counts against the lead.
    dependencies=[Depends(require_admin_key), Depends(keyed_write_limit)],
)
def set_override(
    body: OverrideIn,
    request: Request,
    store: Annotated[ObstructionStore, Depends(get_store)],
    now: Annotated[datetime, Depends(get_now)],
) -> OverrideOut:
    """Store one labelled test obstruction and rebuild the hazard map (testing only)."""
    item = to_obstruction(body, now)
    store.upsert([item], now)
    rebuilt = rebuild_hazards(request, now)
    log.info(
        "ADMIN override set %s %s ttl_s=%d rebuilt=%s",
        item.id,
        item.effect.value,
        body.ttl_s,
        rebuilt,
    )
    return OverrideOut(
        id=item.id,
        source=item.source,
        source_ref=item.source_ref,
        title=item.title,
        effect=item.effect,
        observed_at=item.observed_at,
        expires_at=item.expires_at,
        rebuilt=rebuilt,
    )


def _drop(
    request: Request,
    store: ObstructionStore,
    now: datetime,
    drop: Callable[[Obstruction], bool],
) -> list[str]:
    """Remove the active overrides ``drop`` picks; rebuild the map if any went."""
    held = store.active(now, source=SOURCE)
    kept = [item for item in held if not drop(item)]
    # replace_source keeps exactly ``kept``, so expired rows go too.
    removed = store.replace_source(SOURCE, kept, now).removed
    if removed:
        rebuild_hazards(request, now)
        log.info("ADMIN override removed %s", " ".join(removed))
    return removed


@router.delete(
    "/override/{source_ref}",
    status_code=204,
    response_class=Response,
    # The key first (OP-401): a request without it never counts against the lead.
    dependencies=[Depends(require_admin_key), Depends(keyed_write_limit)],
)
def remove_override(
    source_ref: str,
    request: Request,
    store: Annotated[ObstructionStore, Depends(get_store)],
    now: Annotated[datetime, Depends(get_now)],
) -> Response:
    """Remove one override by its ``source_ref`` (404 when none is active)."""
    if all(item.source_ref != source_ref for item in store.active(now, source=SOURCE)):
        raise HTTPException(404, f"No override {source_ref!r} is active.")
    _drop(request, store, now, lambda item: item.source_ref == source_ref)
    return Response(status_code=204)


@router.delete(
    "/override",
    status_code=204,
    response_class=Response,
    # The key first (OP-401): a request without it never counts against the lead.
    dependencies=[Depends(require_admin_key), Depends(keyed_write_limit)],
)
def clear_overrides(
    request: Request,
    store: Annotated[ObstructionStore, Depends(get_store)],
    now: Annotated[datetime, Depends(get_now)],
) -> Response:
    """Remove every override (a clean slate after a test run)."""
    _drop(request, store, now, lambda _item: True)
    return Response(status_code=204)
