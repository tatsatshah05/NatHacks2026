"""Saved routes and push alerts (OP-304): the endpoints the alerts page (OP-306) uses.

- ``GET  /api/push/public-key``: the VAPID public key the browser subscribes with.
- ``POST /api/push/subscribe``: store a browser's push subscription for a ``client_id``.
- ``POST /api/push/test``: send one test notification to a client.
- ``POST /api/saved-routes``: save a trip and compute its baseline route now.
- ``GET  /api/saved-routes?client_id=``: a client's saved routes with their status.
- ``DELETE /api/saved-routes/{id}?client_id=``: stop watching one.

The ``client_id`` is a random, URL-safe string of 16 to 64 characters that the
browser generates and keeps (no accounts, decided November 7). It is the only
"identity" here: whoever knows it can read and delete that client's routes, which
is why it must be random and never shown in a URL users share.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import push as push_module
from . import saved_routes
from .obstructions_api import get_now
from .push import (
    PushSender,
    PushServices,
    PushSubscriptionStore,
    route_url,
    test_payload,
)
from .routing import HazardMap, Router, RoutingError
from .routing_api import get_hazards, get_router
from .saved_routes import SavedRouteRow, SavedRouteStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["alerts"])

CLIENT_ID_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"
ClientId = Annotated[
    str,
    Field(
        pattern=CLIENT_ID_PATTERN,
        description="A random URL-safe id (16 to 64 characters) the browser generated and keeps.",
        examples=["c7Hq2pL9xZ4vT1mK8nR3"],
    ),
]
ClientIdQuery = Annotated[
    str, Query(pattern=CLIENT_ID_PATTERN, description="The browser's anonymous id")
]


# ------------------------------------------------------------------- bodies


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=64)


class PushSubscription(BaseModel):
    """``PushSubscription.toJSON()`` from the browser, minus ``expirationTime``."""

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=1, max_length=1024)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def _https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError(
                "endpoint must be an https:// URL from the browser's push service"
            )
        return value


class SubscribeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: ClientId
    subscription: PushSubscription


class SubscribeOut(BaseModel):
    client_id: str
    created: bool  # False when this endpoint was already stored (refreshed)
    subscriptions: int  # how many endpoints this client now has
    push_enabled: bool  # False when the server has no VAPID keys


class PublicKeyOut(BaseModel):
    public_key: str | None  # the applicationServerKey; None when push is not configured
    push_enabled: bool


class TestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: ClientId


class TestOut(BaseModel):
    client_id: str
    sent: int  # subscriptions that accepted the test message
    subscriptions: int
    push_enabled: bool


class LatLng(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class SavedRouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    client_id: ClientId
    name: str = Field(min_length=1, max_length=80, examples=["Work"])
    from_: LatLng = Field(alias="from")
    to: LatLng
    profile: str = Field(
        min_length=1,
        max_length=16,
        description="wheelchair, walker_cane, low_vision or standard",
        examples=["wheelchair"],
    )

    @field_validator("name")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name must not be blank")
        return value


class SavedRouteOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    client_id: str
    name: str
    from_: LatLng = Field(alias="from")
    to: LatLng
    profile: str
    created_at: datetime
    status: saved_routes.Status  # ok, changed or impossible, as of the last check
    eta_min: float | None  # the route's ETA as of the last check; None when impossible
    baseline_eta_min: float | None  # the ETA when the route was saved
    last_checked_at: datetime | None
    last_push_at: datetime | None
    url: str  # opens this trip in the planner, e.g. /?from=..&to=..&profile=..&saved=3

    @classmethod
    def of(cls, row: SavedRouteRow) -> SavedRouteOut:
        assert row.id is not None
        return cls(
            id=row.id,
            client_id=row.client_id,
            name=row.name,
            from_=LatLng(lat=row.from_lat, lng=row.from_lng),
            to=LatLng(lat=row.to_lat, lng=row.to_lng),
            profile=row.profile,
            created_at=row.created_at,
            status=row.last_status,  # type: ignore[arg-type]  # stored from a Status
            eta_min=row.last_eta_min,
            baseline_eta_min=row.baseline_eta_min,
            last_checked_at=row.last_checked_at,
            last_push_at=row.last_push_at,
            url=route_url(row),
        )


class SavedRoutesOut(BaseModel):
    client_id: str
    routes: list[SavedRouteOut]


# ------------------------------------------------------------- dependencies


def get_push(request: Request) -> PushServices:
    services = getattr(request.app.state, "push", None)
    if services is None:
        raise HTTPException(
            503, "Saved routes and alerts are not available on this instance."
        )
    return services


def get_sender(push: Annotated[PushServices, Depends(get_push)]) -> PushSender:
    return push.sender


def get_subscriptions(
    push: Annotated[PushServices, Depends(get_push)],
) -> PushSubscriptionStore:
    return push.subscriptions


def get_saved_routes(
    push: Annotated[PushServices, Depends(get_push)],
) -> SavedRouteStore:
    return push.routes


def _mark_dirty(push: PushServices, added: int | None = None) -> None:
    if push.watcher is not None:
        push.watcher.mark_dirty(added)


# ------------------------------------------------------------------ push


@router.get("/push/public-key", response_model=PublicKeyOut)
def public_key(sender: Annotated[PushSender, Depends(get_sender)]) -> PublicKeyOut:
    """The VAPID public key for ``pushManager.subscribe({applicationServerKey})``."""
    return PublicKeyOut(public_key=sender.public_key, push_enabled=sender.enabled)


@router.post("/push/subscribe", response_model=SubscribeOut)
def subscribe(
    body: SubscribeIn,
    subscriptions: Annotated[PushSubscriptionStore, Depends(get_subscriptions)],
    sender: Annotated[PushSender, Depends(get_sender)],
    now: Annotated[datetime, Depends(get_now)],
) -> SubscribeOut:
    """Store (or refresh) a browser's push subscription for this client."""
    try:
        _, created = subscriptions.upsert(
            body.client_id,
            body.subscription.endpoint,
            body.subscription.keys.p256dh,
            body.subscription.keys.auth,
            now,
        )
    except push_module.LimitReached as error:
        raise HTTPException(429, str(error)) from None
    return SubscribeOut(
        client_id=body.client_id,
        created=created,
        subscriptions=subscriptions.count(body.client_id),
        push_enabled=sender.enabled,
    )


@router.post("/push/test", response_model=TestOut)
def send_test(
    body: TestIn,
    subscriptions: Annotated[PushSubscriptionStore, Depends(get_subscriptions)],
    sender: Annotated[PushSender, Depends(get_sender)],
) -> TestOut:
    """Send one test notification to every subscription of this client."""
    if not sender.enabled:
        raise HTTPException(
            503, "Push is not configured on this server (no VAPID keys)."
        )
    count = subscriptions.count(body.client_id)
    if count == 0:
        raise HTTPException(
            404, "This client has no push subscription. Subscribe first."
        )
    sent = sender.send_to_client(body.client_id, test_payload(body.client_id))
    return TestOut(
        client_id=body.client_id,
        sent=sent,
        subscriptions=subscriptions.count(body.client_id),
        push_enabled=True,
    )


# ---------------------------------------------------------- saved routes


@router.post("/saved-routes", response_model=SavedRouteOut, status_code=201)
def save_route(
    body: SavedRouteIn,
    push: Annotated[PushServices, Depends(get_push)],
    router_: Annotated[Router, Depends(get_router)],
    hazards: Annotated[HazardMap, Depends(get_hazards)],
    now: Annotated[datetime, Depends(get_now)],
) -> SavedRouteOut:
    """Save a trip and compute its baseline route (edges and ETA) right away."""
    try:
        result = router_.route(
            body.from_.lat,
            body.from_.lng,
            body.to.lat,
            body.to.lng,
            body.profile,
            hazards,
        )
    except RoutingError as error:
        raise HTTPException(422, str(error)) from None
    if not result.routes and result.origin[0] == result.destination[0]:
        raise HTTPException(422, "The start and end are at the same place.")
    # No route at all is still saved: the watcher says when one becomes possible.
    baseline = result.routes[0] if result.routes else None
    try:
        row = push.routes.add(
            client_id=body.client_id,
            name=body.name,
            from_lat=body.from_.lat,
            from_lng=body.from_.lng,
            to_lat=body.to.lat,
            to_lng=body.to.lng,
            profile=body.profile,
            baseline=baseline,
            now=now,
        )
    except saved_routes.LimitReached as error:
        raise HTTPException(429, str(error)) from None
    _mark_dirty(push, row.id)
    return SavedRouteOut.of(row)


@router.get("/saved-routes", response_model=SavedRoutesOut)
def list_routes(
    client_id: ClientIdQuery,
    routes: Annotated[SavedRouteStore, Depends(get_saved_routes)],
) -> SavedRoutesOut:
    """A client's saved routes, oldest first, with their current status."""
    return SavedRoutesOut(
        client_id=client_id,
        routes=[SavedRouteOut.of(row) for row in routes.for_client(client_id)],
    )


@router.delete("/saved-routes/{route_id}", status_code=204, response_class=Response)
def delete_route(
    route_id: int,
    client_id: ClientIdQuery,
    push: Annotated[PushServices, Depends(get_push)],
) -> Response:
    """Stop watching a route. 404 when it is not this client's."""
    if not push.routes.delete(route_id, client_id):
        raise HTTPException(404, "No saved route with that id for this client.")
    _mark_dirty(push)
    return Response(status_code=204)
