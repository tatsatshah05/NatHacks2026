"""511 Alberta camera list adapter (OP-112).

Lists public road cameras from the 511 Alberta API once a day and counts how
many are inside Edmonton. Only camera metadata is fetched here; no images are
downloaded or stored.

Reliability rules (CLAUDE.md, priority 1):
- every request has a timeout;
- a failed fetch never clears data: the last good list is kept and marked stale;
- the API key never appears in logs or error messages.

Usage:
    ALBERTA_511_KEY=... python -m vision.cameras
    ALBERTA_511_KEY=... python -m vision.cameras --boundary config/edmonton_boundary.geojson
    ALBERTA_511_KEY=... python -m vision.cameras --record vision/tests/fixtures/alberta511_cameras_recorded.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

log = logging.getLogger("vision.cameras")

SOURCE = "alberta511_cameras"
API_URL = "https://511.alberta.ca/api/v2/get/cameras"
KEY_ENV = "ALBERTA_511_KEY"
REQUEST_TIMEOUT_S = 20.0

# 511 Alberta throttles to 10 calls every 60 seconds.
RATE_LIMIT_CALLS = 10
RATE_LIMIT_PERIOD_S = 60.0

# Approximate bounding box around the City of Edmonton (lat/lng, WGS84).
# Used only when no boundary file is given. A box over-counts: it also covers
# parts of St. Albert, Strathcona County and other neighbours. Report counts
# from it as approximate.
EDMONTON_BBOX_APPROX = (
    53.3957,
    -113.7136,
    53.7160,
    -113.2714,
)  # min_lat, min_lng, max_lat, max_lng


# ---------------------------------------------------------------------------
# Models (field names follow the 511 Alberta "GET Cameras" documentation)
# ---------------------------------------------------------------------------


class CameraView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="Id")
    url: str = Field(alias="Url")
    status: str = Field(alias="Status")
    description: str | None = Field(default=None, alias="Description")


class Camera(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="Id")
    source: str | None = Field(default=None, alias="Source")
    source_id: str | None = Field(default=None, alias="SourceId")
    roadway: str | None = Field(default=None, alias="Roadway")
    direction: str | None = Field(default=None, alias="Direction")
    latitude: float = Field(alias="Latitude", ge=-90, le=90)
    longitude: float = Field(alias="Longitude", ge=-180, le=180)
    location: str | None = Field(default=None, alias="Location")
    sort_order: int | None = Field(default=None, alias="SortOrder")
    views: list[CameraView] = Field(default_factory=list, alias="Views")


_camera_list = TypeAdapter(list[Camera])


def parse_cameras(payload: object) -> list[Camera]:
    """Validate a raw API payload. Raises pydantic.ValidationError if the schema changed."""
    return _camera_list.validate_python(payload)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` per `period_s` seconds.

    Shared by every request to 511 Alberta (camera list and, later, images).
    `clock` and `sleep` are injectable so tests run instantly.
    """

    def __init__(
        self,
        max_calls: int = RATE_LIMIT_CALLS,
        period_s: float = RATE_LIMIT_PERIOD_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_calls = max_calls
        self.period_s = period_s
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()

    def acquire(self) -> float:
        """Block until a call is allowed. Returns the seconds waited."""
        waited_s = 0.0
        while True:
            now = self._clock()
            while self._calls and now - self._calls[0] >= self.period_s:
                self._calls.popleft()
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return waited_s
            wait_s = self.period_s - (now - self._calls[0])
            self._sleep(wait_s)
            waited_s += wait_s


# ---------------------------------------------------------------------------
# Edmonton boundary and counting
# ---------------------------------------------------------------------------

Ring = Sequence[Sequence[float]]  # [[lng, lat], ...] as in GeoJSON
Polygon = Sequence[Ring]  # outer ring first, then holes


def _point_in_ring(lng: float, lat: float, ring: Ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygons(lat: float, lng: float, polygons: Sequence[Polygon]) -> bool:
    """True if the point is inside any polygon (and not inside one of its holes)."""
    for polygon in polygons:
        if not polygon:
            continue
        if _point_in_ring(lng, lat, polygon[0]) and not any(
            _point_in_ring(lng, lat, hole) for hole in polygon[1:]
        ):
            return True
    return False


def load_boundary(path: Path) -> list[Polygon]:
    """Load Polygon/MultiPolygon geometries from a GeoJSON file (lng/lat order)."""
    data = json.loads(path.read_text())
    if data.get("type") == "FeatureCollection":
        geometries = [f["geometry"] for f in data["features"] if f.get("geometry")]
    elif data.get("type") == "Feature":
        geometries = [data["geometry"]]
    else:
        geometries = [data]

    polygons: list[Polygon] = []
    for geometry in geometries:
        if geometry["type"] == "Polygon":
            polygons.append(geometry["coordinates"])
        elif geometry["type"] == "MultiPolygon":
            polygons.extend(geometry["coordinates"])
        else:
            raise ValueError(f"Unsupported boundary geometry: {geometry['type']}")
    if not polygons:
        raise ValueError(f"No polygons found in {path}")
    return polygons


def in_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lat, min_lng, max_lat, max_lng = bbox
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


@dataclass(frozen=True)
class CameraCount:
    total: int
    in_edmonton: int
    method: Literal["boundary", "bbox_approximate"]
    enabled_views_in_edmonton: int


def count_in_edmonton(
    cameras: Sequence[Camera], boundary: Sequence[Polygon] | None = None
) -> CameraCount:
    """Count cameras inside Edmonton: exact with a boundary, approximate without."""
    if boundary is not None:
        inside = [
            c for c in cameras if point_in_polygons(c.latitude, c.longitude, boundary)
        ]
        method: Literal["boundary", "bbox_approximate"] = "boundary"
    else:
        inside = [
            c for c in cameras if in_bbox(c.latitude, c.longitude, EDMONTON_BBOX_APPROX)
        ]
        method = "bbox_approximate"
    enabled_views = sum(
        1 for c in inside for v in c.views if v.status.strip().lower() == "enabled"
    )
    return CameraCount(
        total=len(cameras),
        in_edmonton=len(inside),
        method=method,
        enabled_views_in_edmonton=enabled_views,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class CameraFetchError(Exception):
    """A fetch failed. The message never contains the API key."""


@dataclass(frozen=True)
class CameraSnapshot:
    cameras: list[Camera]
    fetched_at: datetime | None  # when this list was last fetched successfully (UTC)
    stale: bool  # True when the latest refresh failed and this is the last good list
    error: str | None = None


class CameraAdapter:
    """Fetches the 511 Alberta camera list and keeps the last good copy."""

    def __init__(
        self,
        key: str,
        client: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        cache_path: Path | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not key:
            raise ValueError(f"{KEY_ENV} is not set")
        self._key = key
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT_S)
        self._limiter = limiter or RateLimiter()
        self._cache_path = cache_path
        self._now = now
        self._last_good: list[Camera] = []
        self._last_good_at: datetime | None = None
        self._load_cache()

    # -- public -------------------------------------------------------------

    def fetch_raw(self) -> object:
        """One rate-limited request to the camera endpoint. Returns parsed JSON."""
        self._limiter.acquire()
        try:
            response = self._client.get(
                API_URL, params={"key": self._key, "format": "json", "lang": "en"}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise CameraFetchError(
                f"HTTP {exc.response.status_code} from 511 Alberta"
            ) from None
        except httpx.HTTPError as exc:
            raise CameraFetchError(
                f"{type(exc).__name__} calling 511 Alberta"
            ) from None
        except ValueError:
            raise CameraFetchError(
                "511 Alberta returned a response that is not JSON"
            ) from None

    def refresh(self) -> CameraSnapshot:
        """Fetch the list. On any failure, keep the last good list and mark it stale."""
        try:
            cameras = parse_cameras(self.fetch_raw())
        except CameraFetchError as exc:
            return self._keep_last_good(str(exc))
        except ValidationError as exc:
            return self._keep_last_good(
                f"511 Alberta camera schema changed ({exc.error_count()} validation errors)"
            )

        previous = len(self._last_good)
        self._last_good = cameras
        self._last_good_at = self._now()
        self._save_cache()
        if previous != len(cameras):
            log.info("CHANGE %s count %d -> %d", SOURCE, previous, len(cameras))
        return CameraSnapshot(
            cameras=cameras, fetched_at=self._last_good_at, stale=False
        )

    # -- internals ----------------------------------------------------------

    def _keep_last_good(self, reason: str) -> CameraSnapshot:
        log.warning(
            "CHANGE %s stale: %s (keeping %d cameras)",
            SOURCE,
            reason,
            len(self._last_good),
        )
        return CameraSnapshot(
            cameras=list(self._last_good),
            fetched_at=self._last_good_at,
            stale=True,
            error=reason,
        )

    def _load_cache(self) -> None:
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text())
            self._last_good = parse_cameras(data["cameras"])
            self._last_good_at = datetime.fromisoformat(data["fetched_at"])
        except (OSError, ValueError, KeyError, ValidationError):
            log.warning("Ignoring unreadable camera cache at %s", self._cache_path)

    def _save_cache(self) -> None:
        if not self._cache_path or self._last_good_at is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": self._last_good_at.isoformat(),
            "cameras": [c.model_dump(by_alias=True) for c in self._last_good],
        }
        self._cache_path.write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count 511 Alberta cameras inside Edmonton."
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        help="GeoJSON of the City of Edmonton boundary. Without it the count is approximate.",
    )
    parser.add_argument(
        "--record",
        type=Path,
        help="Save the raw camera list (metadata only, no images) as a test fixture.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    key = os.environ.get(KEY_ENV, "")
    if not key:
        print(f"Set {KEY_ENV} first (see docs/DATA_SOURCES.md).", file=sys.stderr)
        return 2

    adapter = CameraAdapter(key=key)
    try:
        raw = adapter.fetch_raw()
        cameras = parse_cameras(raw)
    except (CameraFetchError, ValidationError) as exc:
        print(f"Could not fetch cameras: {exc}", file=sys.stderr)
        return 1

    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(raw, indent=2))
        print(f"Recorded {len(cameras)} cameras to {args.record}")

    boundary = load_boundary(args.boundary) if args.boundary else None
    count = count_in_edmonton(cameras, boundary)
    label = (
        "inside Edmonton"
        if count.method == "boundary"
        else "inside Edmonton (approximate, bounding box)"
    )
    print(f"511 Alberta cameras: {count.total} total, {count.in_edmonton} {label}")
    print(f"Enabled views on those cameras: {count.enabled_views_in_edmonton}")
    print(f"Checked at {datetime.now(UTC).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
