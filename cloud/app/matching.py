"""OP-206: tie every obstruction to the walking-network paths it affects.

Routing (OP-207) never looks at obstruction geometry. It reads a ``HazardMap``
(edge index -> ``EdgeHazard``) that this module builds from the store's active
items, so every route sees the same matches and the route report can cite each
one by its source item ID.

How an item attaches (PLAN.md section 8, step 3)
    Point                      the nearest edge, if one is within ``POINT_RADIUS_M``
                               (30 m). Farther points match nothing.
    LineString / MultiLineString
                               every edge inside a ``LINE_BUFFER_M`` (15 m) buffer
                               around the line. An edge counts as inside when at
                               least half of its length is, or at least
                               ``LINE_MIN_INSIDE_M`` (25 m) of it is, so a long path
                               with a closure in the middle still matches.
    Polygon / MultiPolygon, 311 neighbourhood advisories (``snow_complaints``) and
    weather items             an area: kept for the route report, changes no edges.
    Elevator and escalator outages (``indoor``)
                               never snapped to a nearby sidewalk: the City gives a
                               station point, not the elevator (OP-105, OP-106). An
                               elevator outage blocks only the edges a reviewed
                               mapping assigns to its device (``elevator_edges``);
                               with no reviewed mapping it is kept as a station
                               warning.

Effects
    Each hazard carries the item's own ``effect``. Routing turns it into a cost or a
    removed edge per profile with ``config/profiles.json`` (the PLAN.md section 8
    table): ``blocked`` removes the edge, ``caution`` only reports it.

Speed
    ``EdgeIndex`` splits every network segment into pieces of at most
    ``PIECE_M`` and puts their midpoints in a KD-tree, once at startup.
    ``HazardProvider`` rebuilds the map from the store at most every
    ``REFRESH_S`` seconds, so a route request only reads a dictionary.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Literal

import numpy as np
from scipy.spatial import cKDTree

from .network import WalkNetwork
from .routing import EdgeHazard, HazardMap
from .schemas import Category, Kind, Obstruction
from .store import ObstructionStore

log = logging.getLogger(__name__)

POINT_RADIUS_M = 30.0  # a point snaps to the nearest edge within this distance
LINE_BUFFER_M = 15.0  # a line matches the edges inside this buffer
LINE_MIN_FRACTION = 0.5  # ...when this share of the edge is inside the buffer
LINE_MIN_INSIDE_M = 25.0  # ...or at least this many metres of it are
PIECE_M = 50.0  # index pieces are at most this long, so a midpoint is within 25 m
SAMPLE_M = 5.0  # spacing of the points used to measure how much of an edge is inside
REFRESH_S = 30.0  # the provider rebuilds the map at most this often

# Items that describe an area or the whole city, never particular paths.
AREA_KINDS = frozenset({Kind.SNOW_COMPLAINTS})
AREA_CATEGORIES = frozenset({Category.WEATHER})
AREA_TYPES = frozenset({"Polygon", "MultiPolygon"})

Method = Literal["point", "line", "area", "elevator", "station", "unmatched"]


@dataclass(frozen=True)
class Match:
    """How one item attached to the network. ``edges`` is empty for every method
    except ``point``, ``line`` and ``elevator``."""

    item_id: str
    method: Method
    edges: tuple[int, ...] = ()
    distance_m: float | None = None  # a point's distance to its edge
    note: str | None = None  # why an item changes no edges


@dataclass
class MatchResult:
    """Everything the matcher found at one moment."""

    built_at: datetime
    hazards: dict[int, list[EdgeHazard]] = field(default_factory=dict)
    matches: dict[str, Match] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in self.matches.values():
            counts[match.method] = counts.get(match.method, 0) + 1
        return counts


class EdgeIndex:
    """Network edges as short pieces in a KD-tree, for "which edges are near here".

    Coordinates use the network's own equirectangular projection in metres, shifted
    to the network's centre so float32 keeps millimetre precision.
    """

    def __init__(self, network: WalkNetwork) -> None:
        self.network = network
        coords = network.geom_coords.astype(np.float64)
        xy = network._to_xy(coords[:, 1], coords[:, 0])
        self.origin = xy.mean(axis=0) if len(xy) else np.zeros(2)
        xy -= self.origin

        # Segment i joins point i and i+1, except where an edge ends.
        offsets = network.geom_offsets
        point_edge = np.repeat(
            np.arange(network.edge_count, dtype=np.int32), np.diff(offsets)
        )
        keep = np.ones(max(len(xy) - 1, 0), dtype=bool)
        keep[offsets[1:-1] - 1] = False
        starts = np.flatnonzero(keep)
        a, b, edge = xy[starts], xy[starts + 1], point_edge[starts]

        # Split long segments into pieces of at most PIECE_M.
        length = np.linalg.norm(b - a, axis=1)
        pieces = np.maximum(1, np.ceil(length / PIECE_M)).astype(np.int64)
        seg = np.repeat(np.arange(len(a)), pieces)
        first = np.repeat(np.cumsum(pieces) - pieces, pieces)
        j = np.arange(len(seg)) - first
        t0 = (j / pieces[seg])[:, None]
        t1 = ((j + 1) / pieces[seg])[:, None]
        d = b[seg] - a[seg]
        self.a = (a[seg] + t0 * d).astype(np.float32)
        self.b = (a[seg] + t1 * d).astype(np.float32)
        self.edge = edge[seg]
        self._tree = cKDTree((self.a + self.b) / 2) if len(seg) else None
        # A piece's midpoint is at most this far from any point on the piece.
        self.half_piece_m = float(np.max(length / pieces) / 2) if len(seg) else 0.0

    def to_xy(self, positions: Sequence[tuple[float, float]]) -> np.ndarray:
        """(lng, lat) positions in the index's metres."""
        array = np.asarray(positions, dtype=np.float64).reshape(-1, 2)
        return self.network._to_xy(array[:, 1], array[:, 0]) - self.origin

    def pieces_near(self, xy: np.ndarray, radius_m: float) -> np.ndarray:
        """Indices of pieces that may lie within ``radius_m`` of any of ``xy``."""
        if self._tree is None or len(xy) == 0:
            return np.empty(0, dtype=np.int64)
        found = self._tree.query_ball_point(xy, radius_m + self.half_piece_m)
        if xy.ndim == 1:
            return np.asarray(found, dtype=np.int64)
        return np.unique(np.concatenate([np.asarray(f, dtype=np.int64) for f in found]))

    def nearest_edge(self, xy: np.ndarray, radius_m: float) -> tuple[int, float] | None:
        """The edge closest to point ``xy`` and its distance, if within ``radius_m``."""
        pieces = self.pieces_near(xy, radius_m)
        if len(pieces) == 0:
            return None
        dist = point_segment_distance(xy[None, :], self.a[pieces], self.b[pieces])[0]
        best = int(np.argmin(dist))
        if dist[best] > radius_m:
            return None
        return int(self.edge[pieces[best]]), float(dist[best])

    def edge_samples(self, edge: int) -> np.ndarray:
        """Points along an edge, at most ``SAMPLE_M`` apart, both ends included."""
        coords = self.network.edge_coords(edge).astype(np.float64)
        xy = self.network._to_xy(coords[:, 1], coords[:, 0]) - self.origin
        return densify(xy, SAMPLE_M)


def point_segment_distance(
    points: np.ndarray, a: np.ndarray, b: np.ndarray
) -> np.ndarray:
    """Distance from each point to each segment ``a[j]``-``b[j]``: shape (P, S)."""
    p = points[:, None, :].astype(np.float64)
    a = a[None, :, :].astype(np.float64)
    ab = b[None, :, :].astype(np.float64) - a
    length_sq = np.einsum("...i,...i", ab, ab)
    t = np.einsum("...i,...i", p - a, ab) / np.where(length_sq > 0, length_sq, 1.0)
    closest = a + np.clip(t, 0.0, 1.0)[..., None] * ab
    return np.linalg.norm(p - closest, axis=-1)


def densify(xy: np.ndarray, spacing_m: float) -> np.ndarray:
    """A polyline's points plus extra points so no gap exceeds ``spacing_m``."""
    if len(xy) < 2:
        return xy
    out = [xy[:1]]
    for start, end in pairwise(xy):
        steps = max(1, math.ceil(float(np.linalg.norm(end - start)) / spacing_m))
        t = (np.arange(1, steps + 1) / steps)[:, None]
        out.append(start + t * (end - start))
    return np.concatenate(out)


def line_parts(item: Obstruction) -> list[list[tuple[float, float]]]:
    """A LineString or MultiLineString as a list of (lng, lat) polylines."""
    coords = item.geometry.coordinates
    parts = [coords] if item.geometry.type == "LineString" else coords
    return [[(float(p[0]), float(p[1])) for p in part] for part in parts]


def is_area(item: Obstruction) -> bool:
    return (
        item.geometry.type in AREA_TYPES
        or item.kind in AREA_KINDS
        or item.category in AREA_CATEGORIES
    )


def match_item(
    item: Obstruction,
    index: EdgeIndex,
    elevator_edges: Mapping[str, Sequence[int]],
) -> Match:
    """Decide which edges one item affects."""
    if is_area(item):
        return Match(item.id, "area", note="area advisory: changes no paths")

    if item.category == Category.INDOOR:
        edges = tuple(sorted(set(elevator_edges.get(item.source_ref, ()))))
        if item.kind == Kind.ELEVATOR_OUT and edges:
            return Match(item.id, "elevator", edges)
        return Match(
            item.id,
            "station",
            note="station-level location; no reviewed elevator mapping",
        )

    if item.geometry.type == "Point":
        xy = index.to_xy(list(item.geometry.positions()))[0]
        found = index.nearest_edge(xy, POINT_RADIUS_M)
        if found is None:
            return Match(
                item.id, "unmatched", note=f"no path within {POINT_RADIUS_M:g} m"
            )
        edge, distance = found
        return Match(item.id, "point", (edge,), distance_m=round(distance, 1))

    # LineString or MultiLineString.
    parts = [index.to_xy(part) for part in line_parts(item)]
    starts = np.concatenate(
        [part[:-1] for part in parts if len(part) > 1] or [np.empty((0, 2))]
    )
    ends = np.concatenate(
        [part[1:] for part in parts if len(part) > 1] or [np.empty((0, 2))]
    )
    if len(starts) == 0:  # a one-position "line": treat it as a point
        starts = ends = np.concatenate(parts)
    samples = np.concatenate([densify(part, SAMPLE_M) for part in parts])
    pieces = index.pieces_near(samples, LINE_BUFFER_M + SAMPLE_M)
    edges = []
    for edge in np.unique(index.edge[pieces]):
        edge_points = index.edge_samples(int(edge))
        inside = (
            point_segment_distance(edge_points, starts, ends).min(axis=1)
            <= LINE_BUFFER_M
        )
        length = float(index.network.edge_length_m[edge])
        inside_m = length * float(inside.mean())
        if inside_m >= min(LINE_MIN_FRACTION * length, LINE_MIN_INSIDE_M):
            edges.append(int(edge))
    if not edges:
        return Match(
            item.id, "unmatched", note=f"no path inside a {LINE_BUFFER_M:g} m buffer"
        )
    return Match(item.id, "line", tuple(edges))


def match_items(
    items: Iterable[Obstruction],
    index: EdgeIndex,
    now: datetime,
    elevator_edges: Mapping[str, Sequence[int]] | None = None,
) -> MatchResult:
    """Match every item and collect the hazards per edge."""
    result = MatchResult(built_at=now)
    for item in items:
        match = match_item(item, index, elevator_edges or {})
        result.matches[item.id] = match
        hazard = EdgeHazard(
            item_id=item.id,
            effect=item.effect,
            confidence=item.confidence,
            title=item.title,
        )
        for edge in match.edges:
            result.hazards.setdefault(edge, []).append(hazard)
    return result


class HazardProvider:
    """``app.state.hazard_provider``: the current ``HazardMap`` for routing.

    Called on every route request. The map is rebuilt from the store's active items
    when it is older than ``refresh_s``; otherwise the cached one is returned.
    """

    def __init__(
        self,
        store: ObstructionStore,
        network: WalkNetwork,
        *,
        refresh_s: float = REFRESH_S,
        elevator_edges: Mapping[str, Sequence[int]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.index = EdgeIndex(network)
        self.refresh_s = refresh_s
        self.elevator_edges = dict(elevator_edges or {})
        self.clock = clock
        self.latest: MatchResult | None = None
        self._built_at = -math.inf
        self._lock = threading.Lock()

    def refresh(self, now: datetime) -> MatchResult:
        """Match the store's active items now."""
        started = time.perf_counter()
        result = match_items(
            self.store.active(now), self.index, now, self.elevator_edges
        )
        log.info(
            "MATCH %d items, %d edges with hazards, %s in %.0f ms",
            len(result.matches),
            len(result.hazards),
            result.counts(),
            (time.perf_counter() - started) * 1000,
        )
        self.latest = result
        self._built_at = self.clock()
        return result

    def __call__(self, now: datetime) -> HazardMap:
        with self._lock:
            if self.latest is None or self.clock() - self._built_at >= self.refresh_s:
                self.refresh(now)
            assert self.latest is not None
            return self.latest.hazards
