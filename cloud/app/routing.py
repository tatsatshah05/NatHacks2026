"""The routing engine (OP-207): best route plus alternates, per mobility profile.

How a route is built (PLAN.md section 8)
    1. Snap the from and to points to the nearest network nodes with a usable path.
       If no route is found because an end sits in a pocket cut off from the rest
       of the network for this profile (between stairs and a closure, say), move
       that end to the nearest node of the main connected piece, within
       ``snap.max_m``, and search again.
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

Where the time goes (OP-404, measured on the city network)
    A route runs Dijkstra four or five times (the best route, up to three more
    attempts for alternates, and the plain route that hazards are compared
    against), about 12 ms each on a laptop, which is over 90% of a request. The
    weights (0.3 ms) and the sparse matrix (about 1 ms, built once per Dijkstra
    run) are cached per profile and hazard map in ``WeightCache``, and the
    penalised searches reuse the cached matrix structure. Routes are identical
    with and without the cache; ``docs/PERFORMANCE.md`` has the numbers.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Literal

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from .network import EDGE_STEPS, WalkNetwork
from .profiles import Profile, RoutingConfig
from .schemas import Confidence, Effect

log = logging.getLogger(__name__)

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
    # The plain shortest route's nodes (hazards ignored), so the Maps hand-off (OP-209)
    # can pin the places where a route departs from it. None when unreachable.
    plain_nodes: tuple[int, ...] | None = None


class RoutingError(ValueError):
    """A request that cannot be served: unknown profile or a point too far from any path."""


@dataclass(frozen=True)
class _Snap:
    """Where one end of a trip joins the network."""

    which: Literal["start", "end"]
    lat: float
    lng: float
    node: int
    distance_m: float
    # Why the node is not simply the nearest one: "blocked" (the nearest node has no
    # usable path) or "cut_off" (its paths do not reach the rest of the network).
    moved: Literal["blocked", "cut_off"] | None = None

    def warning(self, warn_m: float) -> str | None:
        verb = "begins" if self.which == "start" else "ends"
        if self.moved == "blocked":
            return (
                f"The {self.which} point is inside a blocked stretch; the route {verb} "
                f"{self.distance_m:.0f} m away at the nearest open path."
            )
        if self.moved == "cut_off":
            return (
                f"The {self.which} point is on paths cut off from the rest of the network "
                f"for this profile; the route {verb} {self.distance_m:.0f} m away at the "
                "nearest connected path."
            )
        if self.distance_m > warn_m:
            return (
                f"The {self.which} point is {self.distance_m:.0f} m from the nearest "
                "walkable path."
            )
        return None


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


# ------------------------------------------------------------- weight cache


@dataclass(frozen=True)
class ProfileWeights:
    """The cost per edge for one profile on one hazard map, ready for SciPy (OP-404).

    ``weights`` is ``profile_weights()`` for the pair, read-only so no caller can
    change what every other request sees (copy it first, as ``Router._search``
    does). ``indices`` and ``indptr`` are the structure of ``length_matrix(weights)``
    once its zero entries are gone, and ``kept`` is the edge behind each stored
    entry, in order. ``matrix()`` rebuilds the matrix from them with one gather
    instead of a full ``length_matrix()`` (about a third of the time on the city
    network; the measurements are in ``docs/PERFORMANCE.md``), and ``matrix(other)``
    does the same for any weights with this entry's zero pattern, such as the
    penalised copies the alternates search makes. Either way the result is exactly
    what ``length_matrix()`` would return.
    """

    network: WalkNetwork
    profile: Profile
    weights: np.ndarray  # float64 [E], read-only; 0 = removed edge
    kept: np.ndarray  # int32 [nnz]: the edge of each stored matrix entry
    indices: np.ndarray  # the matrix's CSR structure, read-only
    indptr: np.ndarray
    removed: np.ndarray  # bool [E]: True where the weight is 0 (no edge)

    @classmethod
    def build(
        cls, network: WalkNetwork, profile: Profile, hazards: HazardMap
    ) -> ProfileWeights:
        weights = profile_weights(network, profile, hazards)
        matrix = network.length_matrix(weights)
        removed = weights == 0
        kept = network.edge_ids[~removed[network.edge_ids]]
        for array in (weights, kept, matrix.indices, matrix.indptr, removed):
            array.flags.writeable = False
        return cls(
            network, profile, weights, kept, matrix.indices, matrix.indptr, removed
        )

    def matrix(self, weights: np.ndarray | None = None) -> csr_matrix:
        """``length_matrix()`` of these weights, or of ``weights`` when given.

        ``weights`` with this entry's zero pattern (the same edges removed) reuse
        the stored structure; any other weights get a full ``length_matrix()`` build,
        so the answer is always the one ``length_matrix()`` gives.
        """
        if weights is None:
            weights = self.weights
        elif weights is not self.weights and not np.array_equal(
            weights == 0, self.removed
        ):
            return self.network.length_matrix(weights)
        n = self.network.node_count
        return csr_matrix((weights[self.kept], self.indices, self.indptr), shape=(n, n))


class WeightCache:
    """``ProfileWeights`` per profile and hazard map, shared by every router on a network.

    Keyed by the hazard map *object*: the providers (OP-205, OP-206) build a new map
    whenever anything changes and never edit one they have handed out, so "the same
    object" means "the same hazards", and a rebuild simply misses the cache. The
    cache keeps a reference to each map it holds entries for, so Python cannot give
    a new map the id of a cached one. Every empty map shares the plain entries.

    Memory stays bounded: only the ``max_maps`` newest hazard maps are kept, and a
    map holds one entry per profile used (about 5 MB each on the city network:
    weights, kept, indices and indptr). Entries are immutable, the tables change
    only under a lock, and a miss builds outside it, so two threads may build the
    same entry once; the second build is simply dropped.
    """

    def __init__(self, network: WalkNetwork, *, max_maps: int = 1) -> None:
        self.network = network
        self.max_maps = max(0, max_maps)
        self._plain: dict[str, ProfileWeights] = {}
        # Newest last. Each map's entries by profile key.
        self._maps: list[tuple[HazardMap, dict[str, ProfileWeights]]] = []
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, profile: Profile, hazards: HazardMap) -> ProfileWeights:
        """The entry for this profile on this map, built on the first request."""
        with self._lock:
            entries = self._entries(hazards)
            found = entries.get(profile.key)
            if found is not None and (
                found.profile is profile or found.profile == profile
            ):
                self.hits += 1
                return found
            self.misses += 1
        built = ProfileWeights.build(self.network, profile, hazards)
        with self._lock:
            entries = self._entries(hazards)
            return entries.setdefault(profile.key, built)

    def _entries(self, hazards: HazardMap) -> dict[str, ProfileWeights]:
        """The entry table for ``hazards``; the caller holds the lock."""
        if not hazards:
            return self._plain
        for i, (held, entries) in enumerate(self._maps):
            if held is hazards:
                if i != len(self._maps) - 1:  # most recently used last
                    self._maps.append(self._maps.pop(i))
                return entries
        entries = {}
        if self.max_maps:
            self._maps.append((hazards, entries))
            while len(self._maps) > self.max_maps:
                _, dropped = self._maps.pop(0)
                log.debug(
                    "ROUTE weight cache: new hazard map, %d entries dropped",
                    len(dropped),
                )
        return entries

    def clear(self) -> None:
        with self._lock:
            self._plain.clear()
            self._maps.clear()

    @property
    def size(self) -> int:
        """How many entries the cache holds right now."""
        with self._lock:
            return len(self._plain) + sum(len(e) for _, e in self._maps)


# -------------------------------------------------------------------- router


class Router:
    def __init__(
        self,
        network: WalkNetwork,
        config: RoutingConfig,
        cache: WeightCache | None = None,
    ) -> None:
        self.network = network
        self.config = config
        # Shared between routers on one network (ws.py keeps a best-route-only one).
        if cache is not None and cache.network is not network:
            raise ValueError("the weight cache belongs to another network")
        self.cache = cache if cache is not None else WeightCache(network)

    def weights(self, profile: Profile, hazards: HazardMap) -> ProfileWeights:
        """This profile's cached weights and matrix on ``hazards`` (OP-404)."""
        return self.cache.get(profile, hazards)

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
        # Weights first: snapping must know which paths this profile can use right now,
        # otherwise a start inside a blocked stretch snaps to a node with no way out and
        # the whole trip fails even though an open path is a few metres away.
        entry = self.cache.get(profile, hazards)
        weights = entry.weights
        start = self._snap(from_lat, from_lng, "start", weights)
        end = self._snap(to_lat, to_lng, "end", weights)
        found = (
            self._search(entry, start.node, end.node) if start.node != end.node else []
        )
        if not found and start.node != end.node:
            # An end may sit in a pocket of paths cut off from the rest of the network
            # for this profile (between stairs and a closure, say): its node has usable
            # paths, but none of them leads out. Move such an end to the nearest node
            # that connects, and search again.
            joined = self._join_main_piece(start, end, entry)
            if joined is not None:
                start, end = joined
                if start.node != end.node:
                    found = self._search(entry, start.node, end.node)

        warn_m = self.config.snap.warn_m
        warnings = [w for s in (start, end) if (w := s.warning(warn_m)) is not None]
        origin = (start.node, start.distance_m)
        destination = (end.node, end.distance_m)
        if start.node == end.node:
            return RouteResult(
                profile_key,
                origin,
                destination,
                [],
                [*warnings, "The start and end are at the same place."],
            )
        if not found:
            warnings.append(
                f"No route is possible for the {profile.label} profile between these points."
            )
            return RouteResult(profile_key, origin, destination, [], warnings)

        # The plain shortest route ignores hazards (but keeps the profile's stair rule),
        # so "what did this route avoid?" and "why is it longer?" have a baseline.
        plain_entry = self.cache.get(profile, NO_HAZARDS)
        plain_weights = plain_entry.weights
        if np.array_equal(weights, plain_weights):
            # No hazard changes a weight for this profile, so the plain search would
            # run Dijkstra on the very same matrix as the first search did and find
            # the very same path: the best route is the plain route.
            plain: tuple[int, ...] | None = found[0][0]
        else:
            plain = self._shortest(plain_entry.matrix(), start.node, end.node)
        plain_edges = set(self._edges_of(plain, plain_weights)) if plain else set()
        routes = [
            self._describe(
                path, edges, weights, profile, hazards, plain_edges, plain_weights
            )
            for path, edges in found
        ]
        return RouteResult(profile_key, origin, destination, routes, warnings, plain)

    # -- pieces --------------------------------------------------------------

    # How many nearby nodes to consider when the nearest one has no usable path.
    SNAP_CANDIDATES = 12

    def _snap(
        self,
        lat: float,
        lng: float,
        which: Literal["start", "end"],
        weights: np.ndarray,
    ) -> _Snap:
        """Snap a coordinate to the nearest node with a usable path.

        A node is usable when at least one of its edges is usable for this profile
        (weight above 0). Among the nearest candidates the first usable one wins; if
        none is usable, the nearest node is returned and the search reports no route.
        Whether that node's paths reach the rest of the network is checked only if
        the search fails (``_join_main_piece``).
        """
        nodes, distances = self.network.nearest_nodes(lat, lng, self.SNAP_CANDIDATES)
        nearest, nearest_m = int(nodes[0]), float(distances[0])
        if nearest_m > self.config.snap.max_m:
            raise RoutingError(
                f"The {which} point is {nearest_m:.0f} m from the nearest walkable path "
                f"(limit {self.config.snap.max_m:.0f} m). Choose a point inside Edmonton."
            )
        chosen, chosen_m = nearest, nearest_m
        for node, distance in zip(nodes, distances, strict=True):
            if distance > self.config.snap.max_m:
                break  # never snap beyond the limit, even to an open path
            _, edge_ids = self.network.neighbors(int(node))
            if len(edge_ids) and bool((weights[edge_ids] > 0).any()):
                chosen, chosen_m = int(node), float(distance)
                break
        moved: Literal["blocked"] | None = "blocked" if chosen != nearest else None
        return _Snap(which, lat, lng, chosen, chosen_m, moved)

    def _join_main_piece(
        self, start: _Snap, end: _Snap, entry: ProfileWeights
    ) -> tuple[_Snap, _Snap] | None:
        """Move each end outside the main connected piece to its nearest node in it.

        The main piece is the largest set of nodes this profile can travel between
        right now (stairs and blocked paths removed). An end moves only within
        ``snap.max_m``. Returns None when no end moved.

        Runs only after a search has failed, so ordinary routes never pay for it
        (about 12 ms on the city network).
        """
        _, labels = connected_components(entry.matrix(), directed=False)
        main = labels == np.bincount(labels).argmax()
        joined: list[_Snap] = []
        for snap in (start, end):
            found = None if main[snap.node] else self._nearest_where(snap, main)
            if found is None:
                joined.append(snap)
            else:
                node, distance = found
                joined.append(
                    replace(snap, node=node, distance_m=distance, moved="cut_off")
                )
        if joined == [start, end]:
            return None
        return joined[0], joined[1]

    def _nearest_where(
        self, snap: _Snap, allowed: np.ndarray
    ) -> tuple[int, float] | None:
        """The node nearest the end's coordinate where ``allowed`` holds, within
        ``snap.max_m``; the candidate list widens until one is found or the limit."""
        limit = self.config.snap.max_m
        k = self.SNAP_CANDIDATES
        while True:
            nodes, distances = self.network.nearest_nodes(snap.lat, snap.lng, k)
            ok = allowed[nodes] & (distances <= limit)
            if ok.any():
                i = int(np.argmax(ok))  # nearest first, so the first allowed one
                return int(nodes[i]), float(distances[i])
            if len(nodes) < k or distances[-1] > limit:
                return None
            k *= 4

    def _search(
        self, entry: ProfileWeights, src: int, dst: int
    ) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
        """The best route plus alternates by the penalty method, best first.

        Each kept route is (node path, the exact edges the search used). Keeping the
        edges matters where two parallel edges join the same nodes: rebuilding them
        from the node path would pick the same edge every time and repeat a route.
        """
        found: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
        penalised = entry.weights.copy()  # the cached array is read-only
        alt = self.config.alternates
        for _ in range(alt.attempts):
            # Penalising multiplies weights, so the penalised copy keeps the entry's
            # zero pattern and its matrix reuses the cached structure.
            path = self._shortest(entry.matrix(penalised), src, dst)
            if path is None:
                break
            edges = self._edges_of(path, penalised)
            if self._distinct(edges, [kept_edges for _, kept_edges in found]):
                found.append((path, edges))
                if len(found) >= alt.max_routes:
                    break
            # Penalise every edge on the stretches just used, parallel edges included,
            # so the next search cannot come back along the same streets.
            penalised[self._stretch_edges(path)] *= alt.penalty
        return found

    def _shortest(
        self, matrix: csr_matrix, src: int, dst: int
    ) -> tuple[int, ...] | None:
        """Dijkstra from src on ``matrix`` (a ``length_matrix``); the node path to
        dst, or None when unreachable."""
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

    def _stretch_edges(self, path: Sequence[int]) -> list[int]:
        """Every edge (parallel ones too) joining consecutive nodes of a path."""
        ids: list[int] = []
        for u, v in pairwise(path):
            nbrs, eids = self.network.neighbors(u)
            ids.extend(int(e) for n, e in zip(nbrs, eids, strict=True) if n == v)
        return ids

    def _stretch(self, edge: int) -> tuple[int, int]:
        """The undirected node pair an edge joins; parallel edges share a stretch."""
        u, v = int(self.network.edge_u[edge]), int(self.network.edge_v[edge])
        return (u, v) if u <= v else (v, u)

    def _distinct(self, edges: Sequence[int], kept: Iterable[Sequence[int]]) -> bool:
        """True when this route shares less than the configured fraction with every kept route.

        Overlap is measured by stretch (the pair of nodes an edge joins), so a route
        that only swaps to a parallel edge between the same nodes counts as the same.
        """
        lengths = self.network.edge_length_m
        length = float(lengths[list(edges)].sum()) or 1.0
        for other in kept:
            theirs = {self._stretch(e) for e in other}
            shared = float(sum(lengths[e] for e in edges if self._stretch(e) in theirs))
            if shared / length >= self.config.alternates.max_shared_fraction:
                return False
        return True

    def _describe(
        self,
        path: tuple[int, ...],
        edges: tuple[int, ...],
        weights: np.ndarray,
        profile: Profile,
        hazards: HazardMap,
        plain_edges: set[int],
        plain_weights: np.ndarray,
    ) -> Route:
        net = self.network
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
                    and hazard.item_id
                    not in seen  # still crossed elsewhere: not avoided
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
