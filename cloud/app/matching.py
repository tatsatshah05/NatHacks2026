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
    Camera ice risks (``camera_model``)
                               never matched here: OP-205's ``CameraIceHazardProvider``
                               puts them on the exact crossings within 50 m of the
                               camera and adds them to this module's map.

Effects
    Each hazard carries the item's own ``effect``. Routing turns it into a cost or a
    removed edge per profile with ``config/profiles.json`` (the PLAN.md section 8
    table): ``blocked`` removes the edge, ``caution`` only reports it.

Speed and safety
    ``EdgeIndex`` splits every network segment into pieces of at most
    ``PIECE_M`` and puts their midpoints in a KD-tree, once at startup.
    ``HazardProvider`` rebuilds the map from the store at most every
    ``REFRESH_S`` seconds, in a background thread, so a route request only reads a
    dictionary. A failed rebuild keeps the last good map: routing never depends on
    a database read succeeding.
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
from .report import ItemIndex
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
# OP-205's camera ice risks are category "weather" but sit on exact crossings, which
# camera_ice.CrossingIndex finds; CameraIceHazardProvider adds them to this map. They
# never pass through the store, and if one ever does, it must not become a city-wide
# area or a generic 30 m point match, so the matcher leaves it alone.
CAMERA_SOURCE = "camera_model"

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
    # The same items' details and geometry, for the route report (OP-208).
    items: ItemIndex | None = None

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in self.matches.values():
            counts[match.method] = counts.get(match.method, 0) + 1
        return counts


class EdgeIndex:
    """Network edges as short pieces in a KD-tree, for "which edges are near here".

    Each piece is kept as its midpoint (``mid``, float64, which the KD-tree uses
    directly) and the half-vector to its far end (``half``, float32), so the piece
    runs from ``mid - half`` to ``mid + half``. Coordinates use the network's own
    equirectangular projection in metres, shifted to the network's centre.

    Memory (PLAN.md section 8 budget): the build projects the points in chunks,
    works in float32 and splits only the segments longer than ``PIECE_M`` (most are
    shorter), freeing each temporary as soon as it is used. For Edmonton the build
    peaks about 40 MB above the loaded network and keeps about 30 MB.
    """

    PROJECT_CHUNK = 65_536  # points projected at a time, to keep temporaries small

    def __init__(self, network: WalkNetwork) -> None:
        self.network = network
        coords = network.geom_coords  # float32 (lng, lat)
        # The projection is linear, so projecting the mean position gives the mean.
        self.origin = (
            network._to_xy(
                float(coords[:, 1].mean(dtype=np.float64)),
                float(coords[:, 0].mean(dtype=np.float64)),
            )[0]
            if len(coords)
            else np.zeros(2)
        )
        xy = np.empty((len(coords), 2), dtype=np.float32)
        for start in range(0, len(coords), self.PROJECT_CHUNK):
            chunk = coords[start : start + self.PROJECT_CHUNK]
            xy[start : start + len(chunk)] = self.to_xy(chunk)

        # Segment i joins point i and i+1, except where an edge ends.
        offsets = network.geom_offsets
        keep = np.ones(max(len(xy) - 1, 0), dtype=bool)
        keep[offsets[1:-1] - 1] = False
        starts = np.flatnonzero(keep).astype(np.int32)
        del keep
        point_edge = np.repeat(
            np.arange(network.edge_count, dtype=np.int32), np.diff(offsets)
        )
        seg_edge = point_edge[starts]
        del point_edge
        a, b = xy[starts], xy[starts + 1]
        del xy, starts

        # Pieces of at most PIECE_M: one per short segment, several per long one.
        pieces = np.ceil(
            np.hypot(b[:, 0] - a[:, 0], b[:, 1] - a[:, 1]) / np.float32(PIECE_M)
        ).astype(np.int32)
        np.maximum(pieces, 1, out=pieces)
        long = pieces > 1
        short = ~long
        n_short = int(np.count_nonzero(short))
        count = n_short + int(pieces[long].sum(dtype=np.int64))
        self.mid = np.empty((count, 2), dtype=np.float64)
        self.half = np.empty((count, 2), dtype=np.float32)
        self.edge = np.empty(count, dtype=np.int32)

        start_xy, end_xy = a[short], b[short]
        np.add(start_xy, end_xy, out=self.mid[:n_short], dtype=np.float64)
        self.mid[:n_short] *= 0.5
        np.subtract(end_xy, start_xy, out=self.half[:n_short])
        self.half[:n_short] *= 0.5
        self.edge[:n_short] = seg_edge[short]
        del start_xy, end_xy, short

        if count > n_short:
            first = a[long].astype(np.float64)
            per = pieces[long]
            step = (b[long].astype(np.float64) - first) / per[:, None]
            seg = np.repeat(np.arange(len(per)), per)
            j = np.arange(len(seg)) - np.repeat(
                np.cumsum(per, dtype=np.int64) - per, per
            )
            self.half[n_short:] = 0.5 * step[seg]
            self.mid[n_short:] = first[seg] + (j[:, None] + 0.5) * step[seg]
            self.edge[n_short:] = seg_edge[long][seg]
        del a, b, seg_edge, pieces, long

        # A piece's midpoint is at most this far from any point on the piece.
        self.half_piece_m = (
            float(np.hypot(self.half[:, 0], self.half[:, 1]).max()) if count else 0.0
        )
        self._tree = cKDTree(self.mid, copy_data=False) if count else None

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
        mid, half = self.mid[pieces], self.half[pieces]
        dist = point_segment_distance(xy[None, :], mid - half, mid + half)[0]
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
    if item.source == CAMERA_SOURCE:  # before the area check: see CAMERA_SOURCE
        return Match(
            item.id, "unmatched", note="camera ice risk: matched to crossings by OP-205"
        )
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
    """Match every item and collect the hazards per edge.

    An item that fails to match (a bug on one odd geometry) is logged and changes no
    edges, so it cannot stop every other item from reaching routing.
    """
    items = list(items)
    result = MatchResult(built_at=now)
    for item in items:
        try:
            match = match_item(item, index, elevator_edges or {})
        except Exception:
            log.exception("MATCH %s failed; it changes no paths", item.id)
            match = Match(item.id, "unmatched", note="matching failed")
        result.matches[item.id] = match
        hazard = EdgeHazard(
            item_id=item.id,
            effect=item.effect,
            confidence=item.confidence,
            title=item.title,
        )
        for edge in match.edges:
            result.hazards.setdefault(edge, []).append(hazard)
    methods = {item_id: match.method for item_id, match in result.matches.items()}
    try:
        result.items = ItemIndex.build(items, index.to_xy, methods)
    except Exception:  # the report must never stop hazards reaching routing
        log.exception("REPORT index failed; routes list only the hazards they meet")
    return result


class HazardProvider:
    """``app.state.hazard_provider``: the current ``HazardMap`` for routing.

    Called on every route request, so it must answer fast and must never fail:

    - **The first call** builds the map before answering, so no route ignores hazards.
    - **Later calls** that find the map older than ``refresh_s`` start one rebuild in
      a background thread and answer with the current map straight away; a request
      never waits for matching. ``background=False`` rebuilds inside the call
      instead (tools and tests).
    - **A failed rebuild** (a database read error, say) is logged; routing keeps the
      last good map, and the next attempt is ``refresh_s`` later. Before any map has
      been built, routes get no hazards.
    """

    def __init__(
        self,
        store: ObstructionStore,
        network: WalkNetwork,
        *,
        refresh_s: float = REFRESH_S,
        elevator_edges: Mapping[str, Sequence[int]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        background: bool = True,
    ) -> None:
        self.store = store
        self.index = EdgeIndex(network)
        self.refresh_s = refresh_s
        self.elevator_edges = dict(elevator_edges or {})
        self.clock = clock
        self.background = background
        self.latest: MatchResult | None = None
        self.worker: threading.Thread | None = None  # the last background rebuild
        self._attempted_at = -math.inf  # clock() when the last rebuild started
        self._lock = threading.Lock()  # held while a rebuild runs

    def __call__(self, now: datetime) -> HazardMap:
        if self.latest is None or not self.background:
            # No map yet: build it before answering. Waiting on the lock also makes a
            # concurrent first request wait for the build already running.
            with self._lock:
                if self._due():
                    self._rebuild(now)
        elif self._due() and self._lock.acquire(blocking=False):
            self._rebuild_in_background(now)  # releases the lock when done
        latest = self.latest
        return latest.hazards if latest is not None else {}

    def _due(self) -> bool:
        return self.clock() - self._attempted_at >= self.refresh_s

    def _rebuild(self, now: datetime) -> None:
        """One rebuild attempt that never raises. The caller holds the lock."""
        self._attempted_at = self.clock()
        try:
            self.refresh(now)
        except Exception:  # routing must keep working
            log.exception("MATCH failed; keeping the last good hazard map")

    def _rebuild_in_background(self, now: datetime) -> None:
        """Run ``_rebuild`` in a thread that releases the lock (held by the caller)."""

        def work() -> None:
            try:
                self._rebuild(now)
            finally:
                self._lock.release()

        try:
            self.worker = threading.Thread(target=work, name="hazard-map", daemon=True)
            self.worker.start()
        except Exception:  # could not start a thread: try again on a later call
            self._lock.release()
            log.exception("MATCH could not start a background rebuild")

    def refresh(self, now: datetime) -> MatchResult:
        """Match the store's active items now. Raises if that fails."""
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
        return result
