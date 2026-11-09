"""``GET /api/route``: up to three accessible routes between two points (OP-207).

Query: ``from=lat,lng`` ``to=lat,lng`` ``profile=wheelchair|walker_cane|low_vision|standard``
``mode=walk|walk_lrt`` ``transit=faster|always|only`` (read only with ``walk_lrt``).

The app loads the walking network once at startup (``app.state.router``). Hazards
come from ``app.state.hazard_provider``: OP-205's ``CameraIceHazardProvider``, which
adds camera ice risks on exact crossings to the OP-206 matcher's map (its ``base``).
The Maps links (OP-209) pin our detours as Google waypoints.
Each route carries a ``report`` (OP-208): every known item on it, within 50 m of it
or avoided by it, grouped by category.

``mode=walk_lrt`` (OP-311, only when ``LRT_TRIPS`` is on, so ``app.state.lrt_planner``
exists): when a walk and LRT trip is faster than walking, it comes first (rank 1, with
``legs`` and ``lrt``), and the slowest walking route is dropped so there are still at
most 3 routes. Otherwise the walking routes are unchanged. Without ``walk_lrt`` the
response is exactly the walking one: the endpoint leaves unset fields out, and the
OP-311 fields are set only for ``walk_lrt``. With ``TRANSIT_BUS`` on as well (OP-417)
the trip may ride one ETS bus instead; each ride leg then says ``vehicle`` (``lrt`` or
``bus``) and ``route_short_names``, both additive.

``transit`` (OP-420, additive, read only with ``mode=walk_lrt`` and a loaded planner)
chooses what the trip search is for. ``faster`` (the default) is the behaviour above.
``always`` includes the best trip even when it is slower than walking: it is ranked
after the walking routes that beat it, the slowest walking route makes room so there
are still at most 3, and its reason says by how much it is slower. ``only`` returns
transit trips alone (up to 3 distinct ones); when none exists the walking routes
come back with a warning saying so. The response shape does not change.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from . import lrt as lrt_trips
from .camera_ice_routing import CameraIceHazardProvider
from .maps_links import (
    APPLE_MAPS_NOTE,
    apple_maps_url,
    detour_waypoints,
    google_maps_url,
)
from .matching import HazardProvider
from .obstructions_api import get_now
from .report import ItemIndex, ReportRecord, RouteReportOut, build_report
from .routing import EdgeHazard, HazardMap, Route, Router, RouteResult, RoutingError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["routing"])


class HazardOut(BaseModel):
    item_id: str
    effect: str
    confidence: str
    title: str


class StepOut(BaseModel):
    text: str
    distance_m: float


class RouteOut(BaseModel):
    rank: int  # 1 = best for this profile
    geometry: dict[str, Any]  # GeoJSON LineString, [lng, lat]
    distance_m: float
    eta_min: float
    steps: list[StepOut]
    hazards_on_route: list[HazardOut]
    hazards_avoided: list[HazardOut]
    warnings: list[str]
    reason: str | None
    report: RouteReportOut  # OP-208: on the route, within 50 m, and avoided
    google_maps_url: (
        str  # walking directions with up to 3 waypoints that pin our detours
    )
    apple_maps_url: str  # start to end only; see apple_maps_note on the response
    # OP-311, set only with mode=walk_lrt ("walk" on walking routes there).
    mode: Literal["walk", "walk_lrt"] | None = None
    legs: list[lrt_trips.LegOut] | None = None  # walk, ride, transfer, ride, walk
    lrt: lrt_trips.LrtTripOut | None = None  # stations used and their devices


class RouteResponse(BaseModel):
    profile: str
    generated_at: datetime
    origin_snap_m: float
    destination_snap_m: float
    routes: list[RouteOut]
    warnings: list[str]
    apple_maps_note: str  # shown next to the Apple Maps button
    mode: Literal["walk", "walk_lrt"] | None = None  # OP-311: set only for walk_lrt


def parse_point(value: str, name: str) -> tuple[float, float]:
    try:
        lat_s, lng_s = value.split(",")
        lat, lng = float(lat_s), float(lng_s)
    except ValueError:
        raise HTTPException(
            422, f"{name} must be 'lat,lng' in decimal degrees"
        ) from None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(422, f"{name} is outside latitude/longitude ranges")
    return lat, lng


def get_router(request: Request) -> Router:
    router_ = getattr(request.app.state, "router", None)
    if router_ is None:
        raise HTTPException(
            503, "Routing is not available: the walking network is not loaded."
        )
    return router_


def get_hazards(
    request: Request, now: Annotated[datetime, Depends(get_now)]
) -> HazardMap:
    """The current hazards, at the app's time (the replay clock when it is frozen)."""
    provider = getattr(request.app.state, "hazard_provider", None)
    return provider(now) if provider else {}


def get_report_items(
    request: Request, _hazards: Annotated[HazardMap, Depends(get_hazards)]
) -> list[ItemIndex]:
    """The records behind the hazards, for the route report.

    Depends on ``get_hazards`` so the providers have built their maps before they
    are read. ``CameraIceHazardProvider`` contributes an index of its camera ice
    risks (full ``Obstruction`` records, which never pass through the store) and
    wraps the matcher as its ``base``, which contributes the store's items. The
    classes are checked, not ``latest``: both have one, of different types. Any
    other provider gives no records, so the report lists only the hazards routing met.
    """
    provider = getattr(request.app.state, "hazard_provider", None)
    indexes: list[ItemIndex] = []
    if isinstance(provider, CameraIceHazardProvider):
        if provider.items is not None:
            indexes.append(provider.items)  # first, so its camera records win
        provider = provider.base
    if isinstance(provider, HazardProvider):
        # ``latest`` is read separately from ``get_hazards``, so a background rebuild
        # finishing in between can pair a newer index with older hazards. That is
        # harmless: a hazard missing from the index is listed from the hazard itself
        # (``ReportRecord.of_hazard``), and a newer item is at worst listed as nearby.
        latest = provider.latest
        if latest is not None and latest.items is not None:
            indexes.append(latest.items)
    return indexes


def route_report(route: Route, items: Sequence[ItemIndex]) -> RouteReportOut:
    """The route's report; if building it fails, the hazards routing met, never a 500.

    One odd item must never break routing (AGENTS.md, priority 1), so a failure is
    logged and the report falls back to what the route itself carries.
    """
    try:
        return RouteReportOut.of(build_report(route, items))
    except Exception:
        log.exception("REPORT failed; listing only the hazards this route met")
        return RouteReportOut.of(build_report(route, ()))


# exclude_unset keeps walking responses byte-for-byte as before OP-311: the optional
# OP-311 fields are left out unless set. Any field with a default must therefore be
# passed explicitly, or it disappears from responses.
@router.get("/route", response_model=RouteResponse, response_model_exclude_unset=True)
def get_route(
    request: Request,
    router_: Annotated[Router, Depends(get_router)],
    hazards: Annotated[HazardMap, Depends(get_hazards)],
    items: Annotated[list[ItemIndex], Depends(get_report_items)],
    now: Annotated[datetime, Depends(get_now)],
    from_: Annotated[
        str, Query(alias="from", description="lat,lng", examples=["53.5444,-113.4909"])
    ],
    to: Annotated[str, Query(description="lat,lng", examples=["53.5461,-113.4938"])],
    profile: Annotated[
        str, Query(description="wheelchair, walker_cane, low_vision or standard")
    ] = "standard",
    mode: Annotated[
        Literal["walk", "walk_lrt"],
        Query(
            description="walk, or walk_lrt for walk and LRT trips (LRT_TRIPS on; with "
            "TRANSIT_BUS on, walk and bus trips too)"
        ),
    ] = "walk",
    transit: Annotated[
        lrt_trips.TransitChoice,
        Query(
            description="Read only with mode=walk_lrt, ignored otherwise. faster "
            "(default): a bus or LRT trip appears only when it beats walking. "
            "always: the best trip is included even when it is slower, ranked "
            "after the walking routes that beat it. only: transit trips alone; "
            "when none exists, the walking routes come back with a warning."
        ),
    ] = "faster",
) -> RouteResponse:
    """Best route plus up to two alternates for the chosen mobility profile."""
    from_lat, from_lng = parse_point(from_, "from")
    to_lat, to_lng = parse_point(to, "to")
    try:
        result = router_.route(from_lat, from_lng, to_lat, to_lng, profile, hazards)
    except RoutingError as error:
        raise HTTPException(422, str(error)) from None
    response = RouteResponse(
        profile=result.profile,
        generated_at=datetime.now(UTC),
        origin_snap_m=round(result.origin[1], 1),
        destination_snap_m=round(result.destination[1], 1),
        warnings=result.warnings,
        apple_maps_note=APPLE_MAPS_NOTE,
        routes=[
            RouteOut(
                rank=i + 1,
                geometry={"type": "LineString", "coordinates": r.coordinates},
                distance_m=r.distance_m,
                eta_min=r.eta_min,
                steps=[
                    StepOut(text=s.text, distance_m=round(s.distance_m, 1))
                    for s in r.steps
                ],
                hazards_on_route=[
                    HazardOut(
                        item_id=h.item_id,
                        effect=h.effect.value,
                        confidence=h.confidence.value,
                        title=h.title,
                    )
                    for h in r.hazards_on_route
                ],
                hazards_avoided=[
                    HazardOut(
                        item_id=h.item_id,
                        effect=h.effect.value,
                        confidence=h.confidence.value,
                        title=h.title,
                    )
                    for h in r.hazards_avoided
                ],
                warnings=r.warnings,
                reason=r.reason,
                report=route_report(r, items),
                google_maps_url=google_maps_url(
                    (from_lat, from_lng),
                    (to_lat, to_lng),
                    detour_waypoints(router_.network, r.nodes, result.plain_nodes),
                ),
                apple_maps_url=apple_maps_url((from_lat, from_lng), (to_lat, to_lng)),
            )
            for i, r in enumerate(result.routes)
        ],
    )
    planner = getattr(request.app.state, "lrt_planner", None)
    if mode != "walk_lrt" or planner is None:
        return response  # LRT_TRIPS off: walking only, exactly as before
    return add_lrt_trip(
        response,
        result,
        planner,
        request,
        hazards,
        items,
        now,
        (from_lat, from_lng),
        (to_lat, to_lng),
        transit,
    )


# ------------------------------------------------------------ OP-311 (walk_lrt)

# OP-420: on every transit=only response that had to fall back to walking routes.
NO_TRANSIT_WARNING = (
    "No bus or LRT trip was found for this journey; these are walking routes."
)
TRANSIT_UNAVAILABLE_WARNING = "Walk and LRT trips are unavailable right now."


def lrt_device_status(
    request: Request, items: Sequence[ItemIndex], now: datetime
) -> tuple[list[ReportRecord] | None, str | None]:
    """The City elevator and escalator outages, and a warning when they may be old.

    They come from the report indexes (the store's items, already in memory); with
    no index, from the store. None means the status could not be read at all.
    """
    if items:
        records: list[ReportRecord] | None = lrt_trips.device_records(items)
    else:
        store = getattr(request.app.state, "store", None)
        try:
            records = (
                [
                    ReportRecord.of_item(item, "station")
                    for item in store.active(now, source=lrt_trips.DEVICE_SOURCE)
                ]
                if store is not None
                else None
            )
        except Exception:
            log.exception("LRT device status unavailable")
            records = None
    warning = None
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        try:
            status = scheduler.status(lrt_trips.DEVICE_SOURCE)
        except KeyError:
            status = None
        if status is not None and status.stale:
            checked = (
                f"last confirmed at {status.last_success:%H:%M} UTC"
                if status.last_success
                else "not confirmed yet"
            )
            warning = f"LRT elevator status is {checked}; check the station before you travel."
    return records, warning


def _hazards_out(hazards: Sequence[EdgeHazard]) -> list[HazardOut]:
    return [
        HazardOut(
            item_id=h.item_id,
            effect=h.effect.value,
            confidence=h.confidence.value,
            title=h.title,
        )
        for h in hazards
    ]


def _trip_route(
    trip: lrt_trips.LrtTrip,
    plan: lrt_trips.LrtPlan,
    planner: lrt_trips.LrtPlanner,
    items: Sequence[ItemIndex],
    origin: tuple[float, float],
    destination: tuple[float, float],
    stale: str | None,
) -> RouteOut:
    """One walk and transit trip as a route (rank 0 until it is placed)."""
    try:
        report = RouteReportOut.of(lrt_trips.trip_report(trip, plan, items))
    except Exception:
        log.exception("LRT REPORT failed; listing only the hazards this trip met")
        report = RouteReportOut.of(build_report(trip.route, ()))
    google, apple = lrt_trips.transit_maps_urls(origin, destination)
    r = trip.route
    return RouteOut(
        rank=0,
        geometry={"type": "LineString", "coordinates": r.coordinates},
        distance_m=r.distance_m,
        eta_min=r.eta_min,
        steps=[
            StepOut(text=s.text, distance_m=round(s.distance_m, 1)) for s in r.steps
        ],
        hazards_on_route=_hazards_out(r.hazards_on_route),
        hazards_avoided=_hazards_out(r.hazards_avoided),
        warnings=[*r.warnings, *([stale] if stale else [])],
        reason=r.reason,
        report=report,
        google_maps_url=google,
        apple_maps_url=apple,
        mode="walk_lrt",
        legs=lrt_trips.legs_out(trip),
        lrt=lrt_trips.trip_out(trip, planner.schedule),
    )


def _place_trip(
    walks: Sequence[RouteOut], trip: RouteOut, trip_min: float, max_routes: int
) -> list[RouteOut]:
    """The walking routes with one trip ranked after those that beat it.

    The slowest walking route (the later one when two tie) makes room first, so
    the response keeps at most ``max_routes`` and the trip is never the one dropped.
    ``trip_min`` is the trip's unrounded time: a trip the planner found faster than
    the best walking route (``transit=faster``, and ``always`` when it wins) is
    placed first, exactly as before OP-420; a slower one (``always``) goes after
    every walking route whose ETA it does not beat. Ranks are renumbered from 1.
    """
    kept = list(walks)
    while kept and len(kept) + 1 > max_routes:
        slowest = max(range(len(kept)), key=lambda i: (kept[i].eta_min, i))
        kept.pop(slowest)
    index = sum(1 for route in kept if route.eta_min <= trip_min)
    routes = [*kept[:index], trip, *kept[index:]]
    for rank, route in enumerate(routes, start=1):
        route.rank = rank
    return routes


def add_lrt_trip(
    response: RouteResponse,
    result: RouteResult,
    planner: lrt_trips.LrtPlanner,
    request: Request,
    hazards: HazardMap,
    items: Sequence[ItemIndex],
    now: datetime,
    origin: tuple[float, float],
    destination: tuple[float, float],
    transit: lrt_trips.TransitChoice = "faster",
) -> RouteResponse:
    """The walking response with the walk and transit trips the policy keeps.

    ``faster``: the best trip, when it beats walking, becomes rank 1 and the slowest
    walking route is dropped, so the response keeps at most ``alternates.max_routes``
    (3) routes, best first; otherwise the walking routes are unchanged. ``always``:
    the best trip is kept whatever its time and placed after the walking routes that
    beat it. ``only``: the trips alone, fastest first; with none, the walking routes
    plus ``NO_TRANSIT_WARNING``. A failure here never loses the walking routes
    (AGENTS.md, priority 1).
    """
    for route in response.routes:
        route.mode = "walk"
    response.mode = "walk_lrt"
    try:
        records, stale = lrt_device_status(request, items, now)
        outages = (
            None
            if records is None
            else lrt_trips.match_outages(planner.schedule, records)
        )
        plan = planner.plan(
            origin_node=result.origin[0],
            destination_node=result.destination[0],
            profile=planner.router.config.profile(result.profile),
            hazards=hazards,
            outages=outages,
            now=now,
            walk_eta_min=result.routes[0].eta_min if result.routes else None,
            destination_point=destination,
            transit=transit,
        )
    except Exception:
        log.exception("LRT trip planning failed; returning walking routes only")
        response.warnings = [
            *response.warnings,
            TRANSIT_UNAVAILABLE_WARNING,
            *([NO_TRANSIT_WARNING] if transit == "only" else []),
        ]
        return response
    warnings = [*plan.warnings]
    if stale:
        warnings.append(stale)
    trips = [
        _trip_route(trip, plan, planner, items, origin, destination, stale)
        for trip in plan.trips
    ]
    if transit == "only":
        if trips:
            for rank, route in enumerate(trips, start=1):
                route.rank = rank
            response.routes = trips
        else:
            warnings.insert(0, NO_TRANSIT_WARNING)
    elif trips:
        response.routes = _place_trip(
            response.routes,
            trips[0],
            plan.trips[0].total_s / 60,
            planner.router.config.alternates.max_routes,
        )
    response.warnings = [*response.warnings, *warnings]
    return response
