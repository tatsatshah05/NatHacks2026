"""``GET /api/priority``: which barrier to clear first (OP-310, the city dashboard).

Every active obstruction in the store gets three numbers and one sentence:

``saved_routes_affected``
    How many saved routes (OP-304's ``saved_route`` table) use one of the edges the
    matcher (OP-206, through ``app.state.hazard_provider``) put the obstruction on.
    A route uses an edge when the edge is on its current route, its usual route (as
    saved), or its **plain route**: the shortest way for its profile with no hazards
    at all. The plain route matters for closures: a route saved after a sidewalk
    closed already detours around it, so only the plain route shows that the
    closure is what pushed the trip off its direct way. Plain routes depend only on
    the network and the profile, so each is computed once and cached; a request
    computes at most ``PLAIN_BUDGET_S`` seconds of them and leaves the rest for the
    next one (``saved_routes_pending``).

``pedestrian_weight``
    People counted nearby on a typical day: City counters (``sw7k-ptx8``) within
    400 m, each scaled down linearly with distance (``pedestrian_counts.py``).

``score``
    ``severity * (1 + saved_routes_affected) * (1 + pedestrian_weight / PEOPLE_SCALE)``

    - ``severity = (1 + profiles it stops) / (1 + profiles)``, from
      ``config/profiles.json``: a closure stops all four profiles (1.0), snow stops
      wheelchairs and ice risk stops walkers and canes (0.4 each), and "take care"
      stops none (0.2). A barrier that stops more people comes first.
    - The ``1 +`` terms keep a barrier with no saved route, or no counter nearby,
      in the ranking: it still counts once for itself.
    - ``PEOPLE_SCALE`` is 1,000: a place where City counters see about 1,000 people
      a day weighs as much as one more saved trip. This scale is our choice, not a
      measured value.

    City-wide items (the weather watch, 311 neighbourhood advisories) change no
    paths and have no single place to clear, so their score is 0 and they sort last.

``why``
    One plain sentence for the row: "Blocks 3 saved routes; City counters nearby see
    about 1,200 people walking a day."

Nothing here changes the obstruction contract (``schemas.py``) or routing. The
endpoint never fails because counts, saved routes or the network are missing: each
missing part simply counts as zero, and the response says so.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from . import pedestrian_counts as counts_module
from .matching import AREA_CATEGORIES, AREA_KINDS
from .obstructions_api import get_now, get_store
from .pedestrian_counts import PedestrianCounts
from .profiles import RoutingConfig, load_routing_config
from .routing import NO_HAZARDS, HazardMap, Router, RoutingError
from .routing_api import get_hazards
from .saved_routes import SavedRouteRow, watched_edges
from .schemas import Category, Confidence, Effect, Kind, Obstruction
from .store import ObstructionStore

log = logging.getLogger("openpath.priority")

router = APIRouter(prefix="/api", tags=["city"])

PEOPLE_SCALE = 1000.0  # people a day that weigh as much as one more saved trip (chosen)
PLAIN_BUDGET_S = 2.0  # most time one request spends computing uncached plain routes


# ------------------------------------------------------------------ response


class NearestCounter(BaseModel):
    name: str
    distance_m: float
    daily_pedestrians: int


class PriorityItem(BaseModel):
    rank: int = Field(description="1 is the barrier to clear first")
    id: str
    title: str
    detail: str | None
    kind: Kind
    effect: Effect
    category: Category
    confidence: Confidence
    source: str
    attribution: str
    url: str | None
    observed_at: datetime  # when OpenPath last confirmed it with its source
    age_s: float  # seconds since observed_at, at generated_at
    lng: float  # one point of the geometry, for the map
    lat: float
    on_network: bool  # the matcher put it on at least one walking path
    saved_routes_affected: int
    pedestrian_weight: float  # distance-weighted people a day at counters within 400 m
    nearest_counter: NearestCounter | None
    score: float
    why: str


class PedestrianCountsInfo(BaseModel):
    dataset: str
    url: str
    attribution: str
    counters: int
    window_start: date | None
    window_end: date | None
    radius_m: float
    statistic: str


class PriorityResponse(BaseModel):
    generated_at: datetime
    mode: str  # "live" or "replay", as on /health
    saved_routes: int  # saved routes checked
    saved_routes_pending: int  # saved routes whose plain route is not computed yet
    people_scale: float
    pedestrian_counts: PedestrianCountsInfo | None  # None when the file is missing
    items: list[PriorityItem]


# --------------------------------------------------------------- the ranking


@dataclass(frozen=True)
class RouteEdges:
    """The edges that matter for one saved route: current, usual and plain."""

    route_id: int
    edges: frozenset[int]


def item_edges(hazards: HazardMap) -> dict[str, set[int]]:
    """Invert the hazard map: obstruction id -> the edges it is on."""
    by_item: dict[str, set[int]] = {}
    for edge, on_edge in hazards.items():
        for hazard in on_edge:
            by_item.setdefault(hazard.item_id, set()).add(int(edge))
    return by_item


def severity(effect: Effect, config: RoutingConfig) -> float:
    """``(1 + profiles the effect stops) / (1 + profiles)``: 1.0 for a closure."""
    profiles = list(config.profiles.values())
    stopped = sum(1 for profile in profiles if profile.multiplier(effect) is None)
    return (1 + stopped) / (1 + len(profiles))


def is_citywide(item: Obstruction) -> bool:
    """An area item (weather watch, 311 advisory): it changes no paths."""
    return item.category in AREA_CATEGORIES or item.kind in AREA_KINDS


def representative_point(item: Obstruction) -> tuple[float, float]:
    """(lng, lat): the point itself, or the middle position of a line or ring."""
    positions = list(item.geometry.positions())
    return positions[len(positions) // 2]


def people_text(weight: float) -> str:
    """'about 1,200': two significant figures, so the number does not look exact."""
    if weight < 10:
        return f"about {max(1, round(weight))}"
    digits = len(str(int(weight))) - 2  # keep two significant figures
    rounded = round(weight, -digits) if digits > 0 else weight
    return f"about {round(rounded):,}"


def routes_text(count: int) -> str:
    return f"{count} saved {'route' if count == 1 else 'routes'}"


def why_sentence(
    item: Obstruction,
    on_network: bool,
    affected: int,
    weight: float,
    counts_loaded: bool,
) -> str:
    """One plain sentence: what the barrier does to saved trips, and who walks there."""
    if is_citywide(item):
        return (
            "Covers a whole area, so it changes no routes and has no single place "
            "to clear."
        )
    if not on_network:
        if item.category == Category.INDOOR:
            routes = "Station warning, not tied to a walking path yet"
        else:
            routes = "Not on a mapped walking path, so it changes no saved routes"
    elif affected == 0:
        routes = "On no saved route"
    elif item.effect == Effect.BLOCKED:
        routes = f"Blocks {routes_text(affected)}"
    elif item.effect == Effect.SNOW:
        routes = f"Snow on {routes_text(affected)}"
    elif item.effect == Effect.ICE_RISK:
        routes = f"Ice risk on {routes_text(affected)}"
    else:
        routes = f"On {routes_text(affected)}"
    if not counts_loaded:
        people = "pedestrian counts are not loaded"
    elif weight <= 0:
        people = "no City pedestrian counter within 400 m"
    else:
        people = f"City counters nearby see {people_text(weight)} people walking a day"
    return f"{routes}; {people}."


def rank(
    items: Iterable[Obstruction],
    hazards: HazardMap,
    routes: Sequence[RouteEdges],
    counts: PedestrianCounts | None,
    config: RoutingConfig,
    now: datetime,
) -> list[PriorityItem]:
    """Score every item and sort: highest score first (rank 1)."""
    edges_of = item_edges(hazards)
    ranked: list[PriorityItem] = []
    for item in items:
        edges = edges_of.get(item.id, set())
        citywide = is_citywide(item)
        affected = 0 if citywide else sum(1 for route in routes if route.edges & edges)
        weight, nearest = (0.0, None)
        if counts is not None and not citywide:
            weight, nearest = counts.weight(item.geometry)
        score = (
            0.0
            if citywide
            else severity(item.effect, config)
            * (1 + affected)
            * (1 + weight / PEOPLE_SCALE)
        )
        lng, lat = representative_point(item)
        ranked.append(
            PriorityItem(
                rank=0,
                id=item.id,
                title=item.title,
                detail=item.detail,
                kind=item.kind,
                effect=item.effect,
                category=item.category,
                confidence=item.confidence,
                source=item.source,
                attribution=item.attribution,
                url=item.url,
                observed_at=item.observed_at,
                age_s=round(max(0.0, (now - item.observed_at).total_seconds()), 1),
                lng=lng,
                lat=lat,
                on_network=bool(edges),
                saved_routes_affected=affected,
                pedestrian_weight=round(weight, 1),
                nearest_counter=(
                    NearestCounter(
                        name=nearest.counter.name,
                        distance_m=round(nearest.distance_m, 1),
                        daily_pedestrians=nearest.counter.daily_pedestrians,
                    )
                    if nearest is not None
                    else None
                ),
                score=round(score, 4),
                why=why_sentence(
                    item, bool(edges), affected, weight, counts is not None
                ),
            )
        )
    ranked.sort(
        key=lambda row: (
            -row.score,
            -row.saved_routes_affected,
            -row.pedestrian_weight,
            -row.observed_at.timestamp(),
            row.id,
        )
    )
    return [
        row.model_copy(update={"rank": index}) for index, row in enumerate(ranked, 1)
    ]


# ------------------------------------------------------------- plain routes


class PlainRoutes:
    """Each saved route's plain route (no hazards), computed once per route."""

    def __init__(self, router_: Router) -> None:
        self.router = router_
        self._edges: dict[tuple[Any, ...], frozenset[int]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(row: SavedRouteRow) -> tuple[Any, ...]:
        return (row.id, row.from_lat, row.from_lng, row.to_lat, row.to_lng, row.profile)

    def cached(self, row: SavedRouteRow) -> frozenset[int] | None:
        return self._edges.get(self.key(row))

    def compute(self, row: SavedRouteRow) -> frozenset[int]:
        try:
            result = self.router.route(
                row.from_lat,
                row.from_lng,
                row.to_lat,
                row.to_lng,
                row.profile,
                NO_HAZARDS,
            )
            edges = frozenset(result.routes[0].edges) if result.routes else frozenset()
        except (RoutingError, ValueError):
            edges = frozenset()  # an odd saved route never breaks the ranking
        with self._lock:
            self._edges[self.key(row)] = edges
        return edges


def route_edges(
    rows: Sequence[SavedRouteRow],
    plain: PlainRoutes | None,
    budget_s: float | None = None,
) -> tuple[list[RouteEdges], int]:
    """Every saved route's edges, plus how many still lack their plain route."""
    budget = PLAIN_BUDGET_S if budget_s is None else budget_s
    started = time.perf_counter()
    result: list[RouteEdges] = []
    pending = 0
    for row in rows:
        if row.id is None:
            continue
        edges = set(watched_edges(row))
        if plain is not None:
            known = plain.cached(row)
            if known is None and time.perf_counter() - started < budget:
                known = plain.compute(row)
            if known is None:
                pending += 1
            else:
                edges |= known
        result.append(RouteEdges(row.id, frozenset(edges)))
    return result, pending


# ------------------------------------------------------------------ endpoint


_COUNTS_LOCK = threading.Lock()


def get_counts(request: Request) -> PedestrianCounts | None:
    """The committed pedestrian counts, read once per app (None when missing)."""
    state = request.app.state
    with _COUNTS_LOCK:
        if not hasattr(state, "pedestrian_counts"):
            state.pedestrian_counts = counts_module.load()
    return state.pedestrian_counts


def _plain_routes(request: Request) -> PlainRoutes | None:
    state = request.app.state
    router_ = getattr(state, "router", None)
    if router_ is None:
        return None
    with _COUNTS_LOCK:
        plain = getattr(state, "priority_plain_routes", None)
        if plain is None or plain.router is not router_:
            plain = PlainRoutes(router_)
            state.priority_plain_routes = plain
    return plain


def _saved_rows(request: Request) -> list[SavedRouteRow]:
    services = getattr(request.app.state, "push", None)
    if services is None:
        return []
    try:
        return services.routes.all()
    except Exception:  # the ranking still works with no saved routes
        log.exception("PRIORITY could not read saved routes; counting none")
        return []


def counts_info(counts: PedestrianCounts | None) -> PedestrianCountsInfo | None:
    if counts is None:
        return None
    return PedestrianCountsInfo(
        dataset=counts_module.DATASET,
        url=counts_module.DATASET_URL,
        attribution=counts_module.ATTRIBUTION,
        counters=len(counts.counters),
        window_start=counts.window_start,
        window_end=counts.window_end,
        radius_m=counts.radius_m,
        statistic=counts_module.STATISTIC,
    )


@router.get("/priority", response_model=PriorityResponse)
def get_priority(
    request: Request,
    store: Annotated[ObstructionStore, Depends(get_store)],
    now: Annotated[datetime, Depends(get_now)],
    hazards: Annotated[HazardMap, Depends(get_hazards)],
    counts: Annotated[PedestrianCounts | None, Depends(get_counts)],
) -> PriorityResponse:
    """Every active obstruction, ranked clear-first, with a sentence on why."""
    router_ = getattr(request.app.state, "router", None)
    config = router_.config if router_ is not None else load_routing_config()
    rows = _saved_rows(request)
    routes, pending = route_edges(rows, _plain_routes(request))
    items = rank(store.active(now), hazards, routes, counts, config, now)
    return PriorityResponse(
        generated_at=now,
        mode=getattr(request.app.state, "mode", "live"),
        saved_routes=len(routes),
        saved_routes_pending=pending,
        people_scale=PEOPLE_SCALE,
        pedestrian_counts=counts_info(counts),
        items=items,
    )
