"""Daily pedestrian counts near a barrier (OP-310), for the clear-first ranking.

Source
    City of Edmonton, Daily Pedestrian and Bike Counts (Socrata ``sw7k-ptx8``): one
    row per counter location and day, with the counter's coordinates and the day's
    pedestrians in both directions (``total_pedestrian_count``). The data is raw,
    straight from the equipment, with no quality control (the dataset says so), so a
    single day can be wildly off.

What we keep
    ``scripts/build_pedestrian_counts.py`` reads the dataset once on a laptop and
    writes ``cloud/data/pedestrian_counts.json`` with one typical day per counter:

    - **Window:** the ``WINDOW_DAYS`` (28) days ending on the latest day in the
      dataset, so the number reflects the current season.
    - **Statistic:** the median of the counter's daily pedestrian counts over the
      days in that window that have a row. The median ignores the occasional
      broken day (one recorded day has 477,654 pedestrians at a park entrance).
    - **Kept:** counters with at least ``MIN_DAYS`` (7) days in the window and at
      least one day with a pedestrian counted. Many counters on bike lanes count
      bicycles only and report 0 pedestrians every day; they are left out, not
      treated as empty streets.

    The API loads the file at startup (``load``) and never calls the City at
    request time. A missing or unreadable file gives no counts, never an error.

Weight near a barrier
    ``PedestrianCounts.weight`` adds up every counter within ``RADIUS_M`` (400 m) of
    the barrier's geometry, each scaled down linearly with distance:

        weight = sum(daily_pedestrians * (1 - distance_m / 400))  for distance_m < 400

    A counter on the barrier counts fully; one 200 m away counts half; one 400 m
    or more away does not count. The radius and the linear fall-off are our
    choice (about a five-minute walk), not a measured value.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from .schemas import Geometry

log = logging.getLogger("openpath.pedestrian_counts")

DATASET = "sw7k-ptx8"
DATASET_TITLE = "Daily Pedestrian and Bike Counts"
DATASET_URL = "https://data.edmonton.ca/d/sw7k-ptx8"
ATTRIBUTION = "City of Edmonton Open Data"
LICENCE = "City of Edmonton Open Data Terms of Use"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "pedestrian_counts.json"

WINDOW_DAYS = 28  # the typical day is the median over this many days
MIN_DAYS = 7  # a counter needs at least this many days in the window
RADIUS_M = 400.0  # counters farther from a barrier than this do not count
STATISTIC = (
    "median of the daily total_pedestrian_count (both directions) over the "
    "window's days that have a row"
)

EARTH_RADIUS_M = 6_371_008.8


# ------------------------------------------------------------------ counters


@dataclass(frozen=True)
class Counter:
    """One counter location and its typical day."""

    name: str
    lat: float
    lng: float
    daily_pedestrians: int  # the median day in the window
    days: int  # days in the window with a row


@dataclass(frozen=True)
class Nearby:
    """The counter nearest a barrier, within the radius."""

    counter: Counter
    distance_m: float


@dataclass(frozen=True)
class PedestrianCounts:
    counters: tuple[Counter, ...]
    window_start: date | None = None
    window_end: date | None = None
    read_at: str | None = None
    radius_m: float = RADIUS_M

    def weight(self, geometry: Geometry) -> tuple[float, Nearby | None]:
        """Distance-weighted pedestrians near ``geometry`` and the nearest counter.

        Both are for counters within ``radius_m``; ``(0.0, None)`` when none is.
        """
        total = 0.0
        nearest: Nearby | None = None
        for counter in self.counters:
            distance = distance_to_geometry_m(geometry, counter.lat, counter.lng)
            if distance >= self.radius_m:
                continue
            total += counter.daily_pedestrians * (1.0 - distance / self.radius_m)
            if nearest is None or distance < nearest.distance_m:
                nearest = Nearby(counter, distance)
        return total, nearest


def load(path: Path | str = DEFAULT_PATH) -> PedestrianCounts | None:
    """Read ``pedestrian_counts.json``. None when it is missing or unreadable."""
    path = Path(path)
    if not path.is_file():
        log.warning("PEDESTRIAN counts missing (%s); clear-first uses none", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        counters = tuple(
            Counter(
                name=str(entry["name"]),
                lat=float(entry["lat"]),
                lng=float(entry["lng"]),
                daily_pedestrians=int(entry["daily_pedestrians"]),
                days=int(entry["days"]),
            )
            for entry in data["counters"]
        )
        window = data.get("window") or {}
        start = window.get("start")
        end = window.get("end")
        read = data.get("read") or {}
        counts = PedestrianCounts(
            counters=counters,
            window_start=date.fromisoformat(start) if start else None,
            window_end=date.fromisoformat(end) if end else None,
            read_at=read.get("read_at"),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        # The ranking must still work without counts; it just weighs nothing.
        log.warning("PEDESTRIAN counts unreadable (%s): %s", path, error)
        return None
    log.info("PEDESTRIAN counts loaded: %d counters from %s", len(counters), path)
    return counts


# ----------------------------------------------------------------- building


@dataclass(frozen=True)
class _Row:
    name: str
    lat: float
    lng: float
    day: date
    pedestrians: float


def _parse_row(row: Mapping[str, Any]) -> _Row | None:
    """One dataset row, or None when a field is missing or not a number."""
    try:
        name = " ".join(str(row["counter_location_description"]).split())
        lat = float(row["latitude"])
        lng = float(row["longitude"])
        day = datetime.fromisoformat(str(row["log_timstamp"])).date()
        pedestrians = float(row.get("total_pedestrian_count", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if not name or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    if not math.isfinite(pedestrians) or pedestrians < 0:
        return None
    return _Row(name, lat, lng, day, pedestrians)


def aggregate(
    rows: Iterable[Mapping[str, Any]],
    *,
    window_days: int = WINDOW_DAYS,
    min_days: int = MIN_DAYS,
) -> dict[str, Any]:
    """The JSON document ``build_pedestrian_counts.py`` writes, from raw rows.

    Rows of one counter are grouped by name and position (rounded to 5 decimals,
    about 1 m), because the dataset repeats some counters with differently rounded
    coordinates. If a counter has two rows for one day, the larger counts.
    """
    parsed: list[_Row] = []
    skipped = 0
    for row in rows:
        item = _parse_row(row)
        if item is None:
            skipped += 1
        else:
            parsed.append(item)
    if not parsed:
        raise ValueError("no usable pedestrian count rows")

    end = max(item.day for item in parsed)
    start = end - timedelta(days=window_days - 1)
    days: dict[tuple[str, float, float], dict[date, float]] = {}
    positions: dict[tuple[str, float, float], tuple[float, float]] = {}
    for item in parsed:
        key = (item.name, round(item.lat, 5), round(item.lng, 5))
        positions.setdefault(key, (item.lat, item.lng))
        if start <= item.day <= end:
            by_day = days.setdefault(key, {})
            by_day[item.day] = max(by_day.get(item.day, 0.0), item.pedestrians)

    counters: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for key in sorted(positions):
        name, lat, lng = key
        values = list(days.get(key, {}).values())
        if not values:
            excluded.append({"name": name, "reason": "no rows in the window"})
        elif max(values) <= 0:
            excluded.append({"name": name, "reason": "counts bicycles only"})
        elif len(values) < min_days:
            excluded.append(
                {"name": name, "reason": f"only {len(values)} days in the window"}
            )
        else:
            counters.append(
                {
                    "name": name,
                    "lat": lat,
                    "lng": lng,
                    "daily_pedestrians": round(statistics.median(values)),
                    "days": len(values),
                }
            )
    return {
        "dataset": DATASET,
        "title": DATASET_TITLE,
        "url": DATASET_URL,
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": window_days,
            "min_days": min_days,
        },
        "statistic": STATISTIC,
        "rows": {"used": len(parsed), "skipped": skipped},
        "counters": counters,
        "excluded": excluded,
    }


# ----------------------------------------------------------------- geometry


def _xy(lat: float, lng: float, lat0: float, lng0: float) -> tuple[float, float]:
    """Metres east and north of (lat0, lng0): equirectangular, fine across a city."""
    x = math.radians(lng - lng0) * math.cos(math.radians(lat0)) * EARTH_RADIUS_M
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def _segment_distance(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _inside(point: tuple[float, float], ring: Sequence[tuple[float, float]]) -> bool:
    """Even-odd rule for one ring, in projected metres."""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, [*ring[1:], ring[0]], strict=True):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _lines(geometry: Geometry) -> list[list[tuple[float, float]]]:
    """The geometry as lists of (lng, lat): one per line or ring."""
    coords = geometry.coordinates
    if geometry.type == "Point":
        return [[(float(coords[0]), float(coords[1]))]]
    if geometry.type == "LineString":
        return [[(float(p[0]), float(p[1])) for p in coords]]
    if geometry.type in ("MultiLineString", "Polygon"):
        return [[(float(p[0]), float(p[1])) for p in part] for part in coords]
    return [  # MultiPolygon
        [(float(p[0]), float(p[1])) for p in ring] for poly in coords for ring in poly
    ]


def _polygons(geometry: Geometry) -> list[list[list[tuple[float, float]]]]:
    """Each polygon as its rings of (lng, lat), outer ring first."""
    if geometry.type == "Polygon":
        polys = [geometry.coordinates]
    elif geometry.type == "MultiPolygon":
        polys = geometry.coordinates
    else:
        return []
    return [
        [[(float(p[0]), float(p[1])) for p in ring] for ring in poly] for poly in polys
    ]


def distance_to_geometry_m(geometry: Geometry, lat: float, lng: float) -> float:
    """Metres from (lat, lng) to the geometry; 0 inside a polygon (outside its holes)."""
    origin = (0.0, 0.0)  # the point itself, in its own frame

    def project(part: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
        return [_xy(p[1], p[0], lat, lng) for p in part]

    for rings in _polygons(geometry):
        outer, *holes = (project(ring) for ring in rings)
        if (
            len(outer) >= 3
            and _inside(origin, outer)
            and not any(len(hole) >= 3 and _inside(origin, hole) for hole in holes)
        ):
            return 0.0
    best = math.inf
    for part in (project(part) for part in _lines(geometry)):
        if len(part) == 1:
            best = min(best, math.hypot(*part[0]))
        for a, b in pairwise(part):
            best = min(best, _segment_distance(origin, a, b))
    return best
