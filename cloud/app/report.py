"""OP-208: the route report, every known obstruction on or near a route.

The web app shows it as a grouped text list (OP-211), so the map is never the only
way to learn what a route will meet.

What a report lists (PLAN.md section 8, step 6)
    on_route   the matcher (OP-206) tied the item to a path the route uses, or the
               route passes through the item's area (a polygon). A ``weather`` item
               the matcher keeps as an area (the city-wide black-ice watch) is on
               every route, wherever its geometry sits.
    nearby     any other active item within ``NEAR_M`` (50 m) of the route line.
    avoided    an item on the plain shortest route that this route leaves out
               (routing's ``hazards_avoided``), at any distance.

Groups
    Indoor, Sidewalks and paths, Roads and crossings, and Weather and ice, always in
    that order and always present, even when empty. Transit follows only when it
    has items. Inside a group: on the route first, then nearby, then avoided; then
    blocked before snow, ice risk and caution; then nearest first.

    Each item keeps what a person needs to judge it: source and attribution, title
    and detail, confidence, when OpenPath last confirmed it with its source
    (``observed_at``; not when the source last changed it), the source's own dates
    and a link to the official source.

Records from more than one place
    A report reads one or more ``ItemIndex``: the matcher's, and any other provider's
    items that never pass through the store. OP-205's camera ice risks are full
    ``Obstruction`` objects, and ``CameraIceHazardProvider.items`` indexes them, so
    camera items have the same details as every other item.

Hazards with no record
    If no index has a hazard's item, the report still lists it, from what the hazard
    carries: its ID, effect, confidence and title. Snow and ice risk go under
    Weather and ice; anything else under Sidewalks and paths, because a hazard always
    sits on a walking path.

Speed
    The matcher builds an ``ItemIndex`` with every match (at most every 30 s). It
    keeps each item's geometry as segments cut into pieces of at most ``PIECE_M``,
    with their midpoints in a KD-tree. A report cuts the route the same way, pairs
    route pieces with item pieces that may be within reach, and measures the exact
    distance between each pair of segments.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import numpy as np
from pydantic import BaseModel, Field
from scipy.spatial import cKDTree

from .routing import EdgeHazard, Route
from .schemas import Category, Confidence, Effect, Kind, Obstruction

NEAR_M = 50.0  # the report lists every item within this distance of the route line
PIECE_M = 25.0  # segments are cut into pieces of at most this length for the KD-tree

# (lng, lat) positions -> metres in one flat projection (matching.EdgeIndex.to_xy).
ToXY = Callable[[Sequence[Sequence[float]]], np.ndarray]

POLYGON_TYPES = frozenset({"Polygon", "MultiPolygon"})


class Status(StrEnum):
    """How an item relates to one route."""

    ON_ROUTE = "on_route"  # on a path the route uses, or the route crosses its area
    NEARBY = "nearby"  # within 50 m of the route line
    AVOIDED = "avoided"  # on the plain shortest route, and this route avoids it


# The groups the web app shows (OP-211), in this order and even when empty.
GROUPS: tuple[tuple[Category, str], ...] = (
    (Category.INDOOR, "Indoor"),
    (Category.SIDEWALK, "Sidewalks and paths"),
    (Category.ROAD, "Roads and crossings"),
    (Category.WEATHER, "Weather and ice"),
)
# Groups shown only when they have items.
EXTRA_GROUPS: tuple[tuple[Category, str], ...] = ((Category.TRANSIT, "Transit"),)

_STATUS_ORDER = {Status.ON_ROUTE: 0, Status.NEARBY: 1, Status.AVOIDED: 2}
_EFFECT_ORDER = {
    Effect.BLOCKED: 0,
    Effect.SNOW: 1,
    Effect.ICE_RISK: 2,
    Effect.CAUTION: 3,
}

# Why an item cannot change a route, by how the matcher attached it (matching.Method).
NOTES = {
    "station": (
        "Reported for the whole station: the exact elevator or escalator is not "
        "mapped, so routes cannot avoid it."
    ),
    "area": "Area advisory: shown for information; it does not change routes.",
    "unmatched": "Not on a mapped path, so it does not change routes.",
    "citywide": "Covers the whole city, so it applies to every route and changes none.",
}


# ------------------------------------------------------------------- records


@dataclass(frozen=True)
class ReportRecord:
    """What the report shows for one item: every detail except the geometry."""

    id: str
    source: str
    category: Category
    kind: Kind | None
    effect: Effect
    confidence: Confidence
    title: str
    detail: str | None = None
    observed_at: datetime | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    url: str | None = None
    attribution: str | None = None
    note: str | None = None
    area: bool = False  # a polygon: a route that passes through it is on it
    everywhere: bool = False  # a weather area (the black-ice watch): on every route

    @classmethod
    def of_item(cls, item: Obstruction, method: str | None = None) -> ReportRecord:
        """``method`` is how the matcher attached the item (``matching.Method``)."""
        everywhere = item.category == Category.WEATHER and method == "area"
        return cls(
            id=item.id,
            source=item.source,
            category=item.category,
            kind=item.kind,
            effect=item.effect,
            confidence=item.confidence,
            title=item.title,
            detail=item.detail,
            observed_at=item.observed_at,
            starts_at=item.starts_at,
            ends_at=item.ends_at,
            url=item.url,
            attribution=item.attribution,
            note=NOTES["citywide"] if everywhere else NOTES.get(method or ""),
            area=item.geometry.type in POLYGON_TYPES,
            everywhere=everywhere,
        )

    @classmethod
    def of_hazard(cls, hazard: EdgeHazard) -> ReportRecord:
        """A hazard whose item has no record: list it with what the hazard carries."""
        weather = hazard.effect in (Effect.SNOW, Effect.ICE_RISK)
        return cls(
            id=hazard.item_id,
            source=hazard.item_id.split(":", 1)[0],
            category=Category.WEATHER if weather else Category.SIDEWALK,
            kind={Effect.SNOW: Kind.SNOW, Effect.ICE_RISK: Kind.ICE_RISK}.get(
                hazard.effect
            ),
            effect=hazard.effect,
            confidence=hazard.confidence,
            title=hazard.title or hazard.effect.value.replace("_", " ").capitalize(),
        )


# --------------------------------------------------------------- the index


class ItemIndex:
    """Every active item's report record, with its geometry indexed for "near the route".

    Built by the matcher (``matching.match_items``) from the same items it matched,
    so the report and routing describe the same moment. A provider whose items never
    pass through the store (OP-205's camera ice risks) can build one too, with no
    ``methods``.
    """

    def __init__(
        self,
        records: list[ReportRecord],
        to_xy: ToXY | None,
        a: np.ndarray,
        b: np.ndarray,
        owner: np.ndarray,
        half_piece_m: float,
        polygons: list[tuple[int, np.ndarray, np.ndarray, list[list[np.ndarray]]]],
    ) -> None:
        self.records = records
        self.to_xy = to_xy
        self._by_id = {record.id: i for i, record in enumerate(records)}
        self.everywhere = [record for record in records if record.everywhere]
        self.a, self.b, self.owner = a, b, owner  # item pieces and the record of each
        self.half_piece_m = half_piece_m
        self._tree = cKDTree((a + b) / 2) if len(a) else None
        # (record, bbox min, bbox max, polygons as lists of rings) for "inside an area".
        self.polygons = polygons

    @classmethod
    def build(
        cls,
        items: Iterable[Obstruction],
        to_xy: ToXY,
        methods: Mapping[str, str] | None = None,
    ) -> ItemIndex:
        """Index ``items``; ``methods`` says how the matcher attached each one."""
        methods = methods or {}
        records: list[ReportRecord] = []
        positions: list[tuple[float, float]] = []
        sizes: list[int] = []  # positions per part (a line, a ring or one point)
        part_owner: list[int] = []
        polygon_parts: list[tuple[int, list[list[int]]]] = []
        for item in items:
            n = len(records)
            records.append(ReportRecord.of_item(item, methods.get(item.id)))
            polygons = _parts(item)
            rings_of_item: list[list[int]] = []
            for polygon in polygons:
                ring_ids = []
                for part in polygon:
                    ring_ids.append(len(sizes))
                    positions.extend(part)
                    sizes.append(len(part))
                    part_owner.append(n)
                rings_of_item.append(ring_ids)
            if item.geometry.type in POLYGON_TYPES and rings_of_item:
                polygon_parts.append((n, rings_of_item))

        # One projection for every position, then segments inside each part: point i
        # to i + 1, or a zero-length segment for a part with a single position.
        xy = to_xy(positions) if positions else np.empty((0, 2))
        size = np.asarray(sizes, dtype=np.int64)
        offset = np.cumsum(size) - size
        segments = np.maximum(size - 1, 1)
        part = np.repeat(np.arange(len(size)), segments)
        k = np.arange(len(part)) - np.repeat(np.cumsum(segments) - segments, segments)
        start = offset[part] + k
        end = np.where(size[part] > 1, start + 1, start)
        a, b, seg, half = split(xy[start], xy[end], PIECE_M)
        owner = np.asarray(part_owner, dtype=np.int32)[part][seg]

        polygons_xy = []
        for n, rings_of_item in polygon_parts:
            rings = [
                [xy[offset[r] : offset[r] + size[r]] for r in ring_ids]
                for ring_ids in rings_of_item
            ]
            every = np.concatenate([ring for polygon in rings for ring in polygon])
            polygons_xy.append((n, every.min(axis=0), every.max(axis=0), rings))
        return cls(records, to_xy, a, b, owner, half, polygons_xy)

    def get(self, item_id: str) -> ReportRecord | None:
        index = self._by_id.get(item_id)
        return None if index is None else self.records[index]

    def near(
        self, coordinates: Sequence[Sequence[float]], within_m: float = NEAR_M
    ) -> dict[str, float]:
        """Item ID -> distance in metres, for every item within ``within_m`` of a line."""
        if not self.records or self.to_xy is None or len(coordinates) == 0:
            return {}
        route = self.to_xy(coordinates)
        if len(route) == 1:
            ra, rb = route, route
        else:
            ra, rb = route[:-1], route[1:]
        ra, rb, _, route_half = split(ra, rb, PIECE_M)
        best = np.full(len(self.records), np.inf)

        if self._tree is not None:
            # Midpoints within reach: no point on a piece is farther than half its
            # length from its midpoint, so no pair within ``within_m`` is missed.
            reach = within_m + route_half + self.half_piece_m
            pairs = cKDTree((ra + rb) / 2).sparse_distance_matrix(
                self._tree, reach, output_type="ndarray"
            )
            if len(pairs):
                i, j = pairs["i"], pairs["j"]
                distance = segment_distance(ra[i], rb[i], self.a[j], self.b[j])
                np.minimum.at(best, self.owner[j], distance)

        # A route that runs inside an area without crossing its edge is still in it.
        low, high = route.min(axis=0), route.max(axis=0)
        for n, box_low, box_high, polygons in self.polygons:
            if best[n] == 0 or (box_low > high).any() or (box_high < low).any():
                continue
            if any(inside(route, rings).any() for rings in polygons):
                best[n] = 0.0

        return {
            self.records[n].id: float(best[n]) for n in np.flatnonzero(best <= within_m)
        }


def _parts(item: Obstruction) -> list[list[list[tuple[float, float]]]]:
    """An item's geometry as polygons of parts of (lng, lat) positions.

    A point, a line or each line of a MultiLineString is a "polygon" of one part, so
    one loop handles every type; real polygons have one part per ring.

    Empty parts are dropped. The schema rejects an empty geometry but accepts an
    empty part inside a valid one (``MultiLineString [[a, b], []]``, an empty hole
    ring), and ``ItemIndex.build`` needs every part to own at least one position.
    """

    def part(positions: Sequence[Any]) -> list[tuple[float, float]]:
        return [(float(p[0]), float(p[1])) for p in positions]  # drop any elevation

    coords = item.geometry.coordinates
    match item.geometry.type:
        case "Point":
            polygons = [[part([coords])]]
        case "LineString":
            polygons = [[part(coords)]]
        case "MultiLineString":
            polygons = [[part(line)] for line in coords]
        case "Polygon":
            polygons = [[_closed(part(ring)) for ring in coords]]
        case _:  # MultiPolygon
            polygons = [[_closed(part(ring)) for ring in polygon] for polygon in coords]
    kept = [[ring for ring in polygon if ring] for polygon in polygons]
    return [polygon for polygon in kept if polygon]


def _closed(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return ring if len(ring) < 2 or ring[0] == ring[-1] else [*ring, ring[0]]


# ---------------------------------------------------------------- geometry


def split(
    a: np.ndarray, b: np.ndarray, max_m: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Cut segments ``a[i]``-``b[i]`` into pieces of at most ``max_m``.

    Returns the pieces' starts and ends, the segment each piece came from, and half
    the longest piece: no point on a piece is farther than that from its midpoint.
    """
    if len(a) == 0:
        return a, b, np.empty(0, dtype=np.int64), 0.0
    length = np.linalg.norm(b - a, axis=1)
    pieces = np.maximum(1, np.ceil(length / max_m)).astype(np.int64)
    seg = np.repeat(np.arange(len(a)), pieces)
    first = np.repeat(np.cumsum(pieces) - pieces, pieces)
    j = np.arange(len(seg)) - first
    t0 = (j / pieces[seg])[:, None]
    t1 = ((j + 1) / pieces[seg])[:, None]
    d = b[seg] - a[seg]
    half = float(np.max(length / pieces)) / 2
    return a[seg] + t0 * d, a[seg] + t1 * d, seg, half


def point_to_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row by row, the distance from point ``p[i]`` to segment ``a[i]``-``b[i]``."""
    ab = b - a
    length_sq = np.einsum("ij,ij->i", ab, ab)
    t = np.einsum("ij,ij->i", p - a, ab) / np.where(length_sq > 0, length_sq, 1.0)
    closest = a + np.clip(t, 0.0, 1.0)[:, None] * ab
    return np.linalg.norm(p - closest, axis=1)


def _cross(o: np.ndarray, p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Row by row, the z of (p - o) x (q - o): which side of o-p the point q is on."""
    return (p[:, 0] - o[:, 0]) * (q[:, 1] - o[:, 1]) - (p[:, 1] - o[:, 1]) * (
        q[:, 0] - o[:, 0]
    )


def segment_distance(
    a1: np.ndarray, b1: np.ndarray, a2: np.ndarray, b2: np.ndarray
) -> np.ndarray:
    """Row by row, the shortest distance between segments ``a1-b1`` and ``a2-b2``.

    Two segments that do not cross are closest at an end of one of them, so the
    answer is the smallest of four point-to-segment distances, or 0 when they cross.
    """
    distance = np.minimum.reduce(
        [
            point_to_segment(a1, a2, b2),
            point_to_segment(b1, a2, b2),
            point_to_segment(a2, a1, b1),
            point_to_segment(b2, a1, b1),
        ]
    )
    crossing = (_cross(a1, b1, a2) * _cross(a1, b1, b2) < 0) & (
        _cross(a2, b2, a1) * _cross(a2, b2, b1) < 0
    )
    return np.where(crossing, 0.0, distance)


def inside(points: np.ndarray, rings: Sequence[np.ndarray]) -> np.ndarray:
    """Which points lie inside a polygon (outer ring plus holes), by the even-odd rule."""
    result = np.zeros(len(points), dtype=bool)
    px, py = points[:, :1], points[:, 1:]  # columns, so each point meets every edge
    for ring in rings:
        x0, y0 = ring[:, 0], ring[:, 1]
        x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
        straddles = (y0 > py) != (y1 > py)
        dy = np.where(y1 != y0, y1 - y0, 1.0)
        x_at = x0 + (py - y0) * (x1 - x0) / dy
        result ^= (straddles & (px < x_at)).sum(axis=1) % 2 == 1
    return result


# ---------------------------------------------------------------- the report


@dataclass(frozen=True)
class ReportEntry:
    record: ReportRecord
    status: Status
    distance_m: float | None  # from the route line; None beyond NEAR_M or unknown


@dataclass(frozen=True)
class ReportGroup:
    category: Category
    label: str
    entries: tuple[ReportEntry, ...]


@dataclass(frozen=True)
class RouteReport:
    groups: tuple[ReportGroup, ...]

    def count(self, status: Status) -> int:
        return sum(e.status == status for g in self.groups for e in g.entries)


def build_report(route: Route, indexes: Iterable[ItemIndex] = ()) -> RouteReport:
    """Every known item on, near or avoided by one route, grouped by category.

    ``indexes`` hold the items' records: the matcher's, plus any provider's own.
    """
    indexes = list(indexes)
    distances: dict[str, float] = {}
    for index in indexes:
        for item_id, distance in index.near(route.coordinates).items():
            distances[item_id] = min(distance, distances.get(item_id, distance))

    def find(item_id: str) -> ReportRecord | None:
        return next((r for i in indexes if (r := i.get(item_id)) is not None), None)

    def record(hazard: EdgeHazard) -> ReportRecord:
        return find(hazard.item_id) or ReportRecord.of_hazard(hazard)

    entries: dict[str, ReportEntry] = {}
    for hazard in route.hazards_on_route:
        entries.setdefault(
            hazard.item_id,
            ReportEntry(record(hazard), Status.ON_ROUTE, distances.get(hazard.item_id)),
        )
    for hazard in route.hazards_avoided:
        entries.setdefault(
            hazard.item_id,
            ReportEntry(record(hazard), Status.AVOIDED, distances.get(hazard.item_id)),
        )
    for index in indexes:
        for found in index.everywhere:
            entries.setdefault(
                found.id,
                ReportEntry(found, Status.ON_ROUTE, distances.get(found.id)),
            )
    for item_id, distance in distances.items():
        found = find(item_id)
        if item_id in entries or found is None:
            continue
        on = found.area and distance == 0
        entries[item_id] = ReportEntry(
            found, Status.ON_ROUTE if on else Status.NEARBY, distance
        )
    return group(entries.values())


def group(entries: Iterable[ReportEntry]) -> RouteReport:
    by_category: dict[Category, list[ReportEntry]] = {}
    for entry in entries:
        by_category.setdefault(entry.record.category, []).append(entry)
    groups = [
        ReportGroup(
            category, label, tuple(sorted(by_category.get(category, []), key=_order))
        )
        for category, label in GROUPS
    ]
    groups += [
        ReportGroup(category, label, tuple(sorted(by_category[category], key=_order)))
        for category, label in EXTRA_GROUPS
        if by_category.get(category)
    ]
    return RouteReport(tuple(groups))


def _order(entry: ReportEntry) -> tuple[int, int, float, str]:
    distance = entry.distance_m if entry.distance_m is not None else float("inf")
    return (
        _STATUS_ORDER[entry.status],
        _EFFECT_ORDER[entry.record.effect],
        distance,
        entry.record.id,
    )


# ------------------------------------------------------------- API shapes


class ReportItemOut(BaseModel):
    """One obstruction in a route report."""

    id: str
    status: Status
    distance_m: float | None = Field(
        description="Metres from the route line; null when an avoided item is "
        "more than 50 m away, or the distance is unknown."
    )
    kind: Kind | None = Field(description="Null when only the routing hazard is known.")
    effect: Effect
    confidence: Confidence
    title: str
    detail: str | None
    source: str
    attribution: str | None
    observed_at: datetime | None = Field(
        description="When OpenPath last confirmed this item with its source (show "
        '"checked 2 min ago", not "updated"). Null when only the routing hazard '
        "is known."
    )
    starts_at: datetime | None
    ends_at: datetime | None
    url: str | None = Field(description="Link to the official source.")
    note: str | None = Field(
        description="Why the item cannot change routes, when that is not obvious."
    )


class ReportGroupOut(BaseModel):
    category: Category
    label: str  # "Indoor", "Sidewalks and paths", "Roads and crossings", ...
    items: list[ReportItemOut]


class RouteReportOut(BaseModel):
    """Every known obstruction on the route, within 50 m of it, or avoided by it."""

    on_route: int
    nearby: int
    avoided: int
    groups: list[ReportGroupOut]

    @classmethod
    def of(cls, report: RouteReport) -> RouteReportOut:
        return cls(
            on_route=report.count(Status.ON_ROUTE),
            nearby=report.count(Status.NEARBY),
            avoided=report.count(Status.AVOIDED),
            groups=[
                ReportGroupOut(
                    category=g.category,
                    label=g.label,
                    items=[_item_out(e) for e in g.entries],
                )
                for g in report.groups
            ],
        )


def _item_out(entry: ReportEntry) -> ReportItemOut:
    r = entry.record
    return ReportItemOut(
        id=r.id,
        status=entry.status,
        distance_m=None if entry.distance_m is None else round(entry.distance_m, 1),
        kind=r.kind,
        effect=r.effect,
        confidence=r.confidence,
        title=r.title,
        detail=r.detail,
        source=r.source,
        attribution=r.attribution,
        observed_at=r.observed_at,
        starts_at=r.starts_at,
        ends_at=r.ends_at,
        url=r.url,
        note=r.note,
    )
