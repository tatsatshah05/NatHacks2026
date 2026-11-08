"""The routing engine (OP-207): best route plus alternates, per mobility profile.

How a route is built (PLAN.md section 8)
    1. Snap the from and to points to the nearest network nodes.
    2. Turn the current hazards into a weight per edge for the chosen profile:
       weight = length x multiplier, or 0 to remove the edge (blocked, or an
       effect the profile cannot pass, or stairs for wheelchair and walker).
       When several hazards sit on one edge, "blocked" wins; otherwise the
       largest multiplier applies.
    3. Run Dijkstra (SciPy, C implementation) on the sparse matrix.
    4. Alternates: penalise the edges of the routes found so far and search again.
       Keep a route only if it shares less than 70% of its length with the routes
       already kept. At most 3 routes.
    5. For each route compute distance, time (length / speed x multiplier), the
       hazards it passes and the hazards it avoided compared with the plain
       shortest route, text steps, and a one-line reason when it detours.

Where hazards come from
    OP-206 (matching) turns obstructions into a ``HazardMap``: edge index ->
    hazards on that edge. Routing only consumes that map, so the matcher can
    change without touching this file. Until OP-206 lands, callers pass an empty
    map and routes ignore hazards.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse.csgraph import dijkstra

from .network import EDGE_STEPS, WalkNetwork
from .profiles import Profile, RoutingConfig
from .schemas import Confidence, Effect

# ---------------------------------------------------------------- hazard input


@dataclass(frozen=True)
class EdgeHazard:
    """One obstruction's effect on one edge, as produced by the matcher (OP-206)."""

    item_id: str  # Obstruction.id, e.g. "city_lrt_devices:1234"
    effect: Effect
    confidence: Confidence
    title: str = ""


# edge index -> hazards on that edge
HazardMap = Mapping[int, Sequence[EdgeHazard]]
NO_HAZARDS: HazardMap = {}


# ---------------------------------------------------------------- the results


@dataclass(frozen=True)
class Step:
    """One line of text directions: follow one named way for some distance."""

    text: str
    distance_m: float
    edges: tuple[int, ...]


@dataclass(frozen=True)
class Route:
    nodes: tuple[int, ...]
    edges: tuple[int, ...]
    distance_m: float
    eta_min: float
    coordinates: list[list[float]]  # [lng, lat] polyline for the map
    steps: list[Step]
    hazards_on_route: list[EdgeHazard]
    hazards_avoided: list[EdgeHazard]
    warnings: list[str]
    reason: str | None  # why this route differs from the plain shortest route


@dataclass(frozen=True)
class RouteResult:
    profile: str
    origin: tuple[int, float]  # (snapped node, snap distance in metres)
    destination: tuple[int, float]
    routes: list[Route]
    warnings: list[str] = field(default_factory=list)


class RoutingError(ValueError):
    """A request that cannot be served: unknown profile or a point too far from any path."""


# ------------------------------------------------------------------- weights


def profile_weights(
    network: WalkNetwork, profile: Profile, hazards: HazardMap
) -> np.ndarray:
    """Cost per edge for this profile: length x multiplier, or 0 for a removed edge."""
    length = network.edge_length_m.astype(np.float64)
    multiplier = np.ones(network.edge_count, dtype=np.float64)
    removed = np.zeros(network.edge_count, dtype=bool)

    # Stairs: the network flags them; the profile says whether they are usable.
    stairs = (network.edge_flags & EDGE_STEPS).astype(bool)
    if profile.stairs is None:
        removed |= stairs
    else:
        multiplier[stairs] *= profile.stairs

    # Hazards: blocked wins; otherwise the largest multiplier on the edge applies.
    for edge, on_edge in hazards.items():
        if edge < 0 or edge >= network.edge_count:
            continue
        worst: float | None = 1.0
        for hazard in on_edge:
            mult = profile.multiplier(hazard.effect)
            if mult is None:
                worst = None
                break
            worst = max(worst, mult)  # type: ignore[arg-type]
        if worst is None:
            removed[edge] = True
        else:
            multiplier[edge] = max(multiplier[edge], worst)

    weights = length * multiplier
    weights[removed] = (
        0.0  # length_matrix() drops zero entries: no edge for this profile
    )
    return weights


# -------------------------------------------------------------------- router


class Router:
    def __init__(self, network: WalkNetwork, config: RoutingConfig) -> None:
        self.network = network
        self.config = config

    # -- public --------------------------------------------------------------

    def route(
        self,
        from_lat: float,
        from_lng: float,
        to_lat: float,
        to_lng: float,
        profile_key: str,
        hazards: HazardMap = NO_HAZARDS,
    ) -> RouteResult:
        """Best route plus alternates between two coordinates for one profile."""
        try:
            profile = self.config.profile(profile_key)
        except KeyError as error:
            raise RoutingError(str(error)) from None
        origin = self._snap(from_lat, from_lng, "from")
        destination = self._snap(to_lat, to_lng, "to")
        warnings = self._snap_warnings(origin, destination)
        if origin[0] == destination[0]:
            return RouteResult(
                profile_key,
                origin,
                destination,
                [],
                [*warnings, "The start and end are at the same place."],
            )

        weights = profile_weights(self.network, profile, hazards)
        # The plain shortest route ignores hazards (but keeps the profile's stair rule),
        # so "what did this route avoid?" and "why is it longer?" have a baseline.
        plain_weights = profile_weights(self.network, profile, NO_HAZARDS)
        plain = self._shortest(plain_weights, origin[0], destination[0])

        found: list[tuple[int, ...]] = []  # node paths kept so far
        penalised = weights.copy()
        alt = self.config.alternates
        for _ in range(alt.attempts):
            path = self._shortest(penalised, origin[0], destination[0])
            if path is None:
                break
            edges = self._edges_of(path, penalised)
            if self._distinct(edges, [self._edges_of(p, weights) for p in found]):
                found.append(path)
                if len(found) >= alt.max_routes:
                    break
            penalised[list(edges)] *= alt.penalty  # push the next search elsewhere

        if not found:
            warnings.append(
                f"No route is possible for the {profile.label} profile between these points."
            )
            return RouteResult(profile_key, origin, destination, [], warnings)

        plain_edges = set(self._edges_of(plain, plain_weights)) if plain else set()
        routes = [
            self._describe(path, weights, profile, hazards, plain_edges, plain_weights)
            for path in found
        ]
        return RouteResult(profile_key, origin, destination, routes, warnings)

    # -- pieces --------------------------------------------------------------

    def _snap(self, lat: float, lng: float, which: str) -> tuple[int, float]:
        node, distance = self.network.nearest_node(lat, lng)
        if distance > self.config.snap.max_m:
            raise RoutingError(
                f"The '{which}' point is {distance:.0f} m from the nearest walkable path "
                f"(limit {self.config.snap.max_m:.0f} m). Choose a point inside Edmonton."
            )
        return node, distance

    def _snap_warnings(
        self, origin: tuple[int, float], destination: tuple[int, float]
    ) -> list[str]:
        out = []
        for name, (_, distance) in (("start", origin), ("end", destination)):
            if distance > self.config.snap.warn_m:
                out.append(
                    f"The {name} point is {distance:.0f} m from the nearest walkable path."
                )
        return out

    def _shortest(
        self, weights: np.ndarray, src: int, dst: int
    ) -> tuple[int, ...] | None:
        """Dijkstra from src; returns the node path to dst or None when unreachable."""
        matrix = self.network.length_matrix(weights)
        _, predecessors = dijkstra(
            matrix, directed=True, indices=src, return_predecessors=True
        )
        if predecessors[dst] < 0 and src != dst:
            return None
        path = [dst]
        while path[-1] != src:
            prev = int(predecessors[path[-1]])
            if prev < 0:
                return None
            path.append(prev)
        return tuple(reversed(path))

    def _edges_of(self, path: Sequence[int], weights: np.ndarray) -> tuple[int, ...]:
        """The cheapest usable edge between each pair of consecutive nodes."""
        edges = []
        for u, v in pairwise(path):
            nbrs, eids = self.network.neighbors(u)
            candidates = [
                int(e)
                for n, e in zip(nbrs, eids, strict=True)
                if n == v and weights[e] > 0
            ]
            if (
                not candidates
            ):  # cannot happen for a path Dijkstra returned on these weights
                raise RuntimeError(f"no usable edge between nodes {u} and {v}")
            edges.append(min(candidates, key=lambda e: weights[e]))
        return tuple(edges)

    def _distinct(self, edges: Sequence[int], kept: Iterable[Sequence[int]]) -> bool:
        """True when this route shares less than the configured fraction with every kept route."""
        length = float(self.network.edge_length_m[list(edges)].sum()) or 1.0
        mine = set(edges)
        for other in kept:
            shared = float(self.network.edge_length_m[list(mine & set(other))].sum())
            if shared / length >= self.config.alternates.max_shared_fraction:
                return False
        return True

    def _describe(
        self,
        path: tuple[int, ...],
        weights: np.ndarray,
        profile: Profile,
        hazards: HazardMap,
        plain_edges: set[int],
        plain_weights: np.ndarray,
    ) -> Route:
        net = self.network
        edges = self._edges_of(path, weights)
        lengths = net.edge_length_m[list(edges)].astype(float)
        distance = float(lengths.sum())
        # time = length / speed x multiplier, where weight = length x multiplier.
        eta_min = float(weights[list(edges)].sum() / profile.speed_m_s / 60.0)

        coords = _polyline(net, path, edges)
        steps = _steps(net, edges)

        on_route: list[EdgeHazard] = []
        seen: set[str] = set()
        for edge in edges:
            for hazard in hazards.get(edge, ()):
                if hazard.item_id not in seen:
                    seen.add(hazard.item_id)
                    on_route.append(hazard)
        avoided: list[EdgeHazard] = []
        seen_avoided: set[str] = set()
        for edge in plain_edges - set(edges):
            for hazard in hazards.get(edge, ()):
                if (
                    profile.multiplier(hazard.effect) != 1.0
                    and hazard.item_id not in seen_avoided
                ):
                    seen_avoided.add(hazard.item_id)
                    avoided.append(hazard)

        # Blocked barriers first, then by id, so the reason line is stable between runs.
        avoided.sort(key=lambda h: (h.effect != Effect.BLOCKED, h.item_id))

        warnings = []
        low = [h for h in on_route if h.confidence == Confidence.LOW]
        if low:
            warnings.append(
                f"Low-confidence hazard on {len(low)} part{'s' if len(low) != 1 else ''} of this route."
            )

        reason = None
        if plain_edges and set(edges) != plain_edges and avoided:
            plain_eta = float(
                plain_weights[list(plain_edges)].sum() / profile.speed_m_s / 60.0
            )
            extra = max(0.0, eta_min - plain_eta)
            first = avoided[0]
            what = first.title or first.effect.value.replace("_", " ")
            reason = f"Avoids {what}" + (f"; +{extra:.0f} min" if extra >= 0.5 else "")
        return Route(
            path,
            edges,
            round(distance, 1),
            round(eta_min, 1),
            coords,
            steps,
            on_route,
            avoided,
            warnings,
            reason,
        )


# ------------------------------------------------------------------- helpers


def _polyline(
    net: WalkNetwork, path: Sequence[int], edges: Sequence[int]
) -> list[list[float]]:
    """Join the edges' polylines in travel direction into one [lng, lat] list."""
    out: list[list[float]] = []
    for u, edge in zip(path, edges, strict=False):
        pts = net.edge_coords(edge)
        if int(net.edge_u[edge]) != u:  # stored u -> v; we travel v -> u
            pts = pts[::-1]
        for lng, lat in pts.tolist():
            if not out or out[-1] != [lng, lat]:
                out.append([round(lng, 6), round(lat, 6)])
    return out


def _steps(net: WalkNetwork, edges: Sequence[int]) -> list[Step]:
    """Merge consecutive edges with the same label into one text step."""
    steps: list[Step] = []
    for edge in edges:
        label = net.edge_label(edge)
        length = float(net.edge_length_m[edge])
        if (
            steps
            and steps[-1].text.startswith(f"Follow {label} ")
            or (steps and _label_of(steps[-1]) == label)
        ):
            last = steps[-1]
            total = last.distance_m + length
            steps[-1] = Step(_step_text(label, total), total, (*last.edges, edge))
        else:
            steps.append(Step(_step_text(label, length), length, (edge,)))
    return steps


def _label_of(step: Step) -> str:
    return step.text.removeprefix("Follow ").rsplit(" for ", 1)[0]


def _step_text(label: str, distance_m: float) -> str:
    metres = round(distance_m / 10.0) * 10 if distance_m >= 10 else round(distance_m)
    return f"Follow {label} for {metres} m"
