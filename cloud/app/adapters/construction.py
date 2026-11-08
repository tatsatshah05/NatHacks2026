"""OP-107: help pedestrians avoid sidewalks affected by construction.

Read City permits, keep active pedestrian impacts, and turn their locations
into blocked-sidewalk records. Report missing locations and keep the last
working data when a fetch fails.

Dictionary output follows PLAN.md section 10 until OP-101 supplies the shared
Obstruction/SourceAdapter types. This module does not change shared contracts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from datetime import time as day_time
from http.client import HTTPException
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SOURCE = "city_construction"
DATASET = "7wiq-4rgy"
SOURCE_URL = f"https://data.edmonton.ca/d/{DATASET}"
POLL_INTERVAL = timedelta(hours=1)
MAX_LINE_LENGTH_M = 500
MAX_POINT_LINE_DISTANCE_M = 30
LOGGER = logging.getLogger(__name__)
Row = dict[str, Any]


class FeedError(ValueError):
    """The source cannot safely replace its previous successful snapshot."""


@dataclass
class NormalizedPermits:
    """Keep mapped closures and missing-location warnings together."""

    obstructions: list[Row]
    warnings: list[str]


class PermitReader(Protocol):
    """Injectable HTTP dependency, independent of the pending shared interface."""

    def read(self, today: date) -> list[Any]: ...


class ConstructionReader:
    """Download active permits from the official City API."""

    def __init__(
        self,
        app_token: str | None = None,
        *,
        timeout_s: float = 10,
        page_size: int = 1000,
    ) -> None:
        if timeout_s <= 0 or page_size <= 0:
            raise ValueError("Invalid HTTP timeout or page size")
        self.app_token = app_token
        self.timeout_s = timeout_s
        self.page_size = page_size

    def read(self, today: date) -> list[Any]:
        """Read every page of permits that can affect pedestrians today."""
        # Include the entire finish day, even though City dates use midnight.
        tomorrow = today + timedelta(days=1)
        where = (
            "pedestrian_affected = 'Yes' "
            f"AND start_date < '{tomorrow.isoformat()}T00:00:00' "
            f"AND finish_date >= '{today.isoformat()}T00:00:00'"
        )
        result: list[Any] = []
        while True:
            query = urlencode(
                {
                    "$where": where,
                    "$order": "file_number,street_full_name,:id",
                    "$limit": self.page_size,
                    "$offset": len(result),
                }
            )
            headers = {"Accept": "application/json", "User-Agent": "OpenPath-OP107"}
            if self.app_token:
                headers["X-App-Token"] = self.app_token
            request = Request(
                f"https://data.edmonton.ca/resource/{DATASET}.json?{query}",
                headers=headers,
            )
            page = self._request(request)
            result.extend(page)
            # A short page means the complete matching feed has been read.
            if len(page) < self.page_size:
                return result

    def _request(self, request: Request) -> list[Any]:
        """Make one bounded request; OP-103 owns retries and failure cooldowns."""
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                return _rows(json.load(response))
        except HTTPError as exc:
            raise FeedError(f"Construction feed HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            raise FeedError("Construction feed unavailable") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FeedError("Construction feed returned invalid JSON") from exc


def _rows(value: Any) -> list[Any]:
    # Bad individual rows are handled during normalization, after pagination.
    if not isinstance(value, list):
        raise FeedError("Expected a JSON array of construction permits")
    return value


def _required(row: Row, field: str) -> str:
    value = row.get(field)
    if value is None or not str(value).strip():
        raise FeedError(f"Construction record missing {field}")
    return str(value).strip()


def _permit_date(row: Row, field: str) -> date:
    try:
        # These are proposed/estimated calendar dates, not observation instants.
        return datetime.fromisoformat(_required(row, field)).date()
    except ValueError as exc:
        raise FeedError(f"Invalid construction {field}") from exc


def _position(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise FeedError("Invalid construction coordinate pair")
    if any(isinstance(number, bool) for number in value):
        raise FeedError("Invalid construction coordinate pair")
    try:
        lon, lat = (float(number) for number in value)
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise ValueError("Nonfinite coordinates")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Coordinates outside WGS84")
        return [lon, lat]
    except (TypeError, ValueError, OverflowError) as exc:
        raise FeedError("Invalid construction coordinate pair") from exc


def _point_geometry(row: Row) -> Row | None:
    """Use the City's permit point without inventing a corridor midpoint."""
    point = row.get("location")
    if point is not None:
        if not isinstance(point, dict) or point.get("type") != "Point":
            raise FeedError("Invalid construction location")
        return {"type": "Point", "coordinates": _position(point.get("coordinates"))}
    if row.get("longitude") is not None or row.get("latitude") is not None:
        return {
            "type": "Point",
            "coordinates": _position([row.get("longitude"), row.get("latitude")]),
        }
    return None


def _multiline(value: Any) -> list[list[list[float]]]:
    if not isinstance(value, dict) or value.get("type") != "MultiLineString":
        raise FeedError("Invalid construction geometry_multiline")
    lines = value.get("coordinates")
    if not isinstance(lines, list) or not lines:
        raise FeedError("Empty construction geometry_multiline")
    output = []
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            raise FeedError("Invalid construction line")
        output.append([_position(point) for point in line])
    return output


def _distance_m(first: list[float], second: list[float]) -> float:
    """Measure segment length on a sphere, including long source corridors."""
    lon1, lat1, lon2, lat2 = map(math.radians, [*first, *second])
    hav = math.sin((lat2 - lat1) / 2) ** 2 + (
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(min(1, hav)))


def _point_segment_distance_m(
    point: list[float], first: list[float], second: list[float]
) -> float:
    # A local tangent plane is sufficient for the already-limited 500 m lines.
    lon, lat = point
    scale_x = 6_371_000 * math.cos(math.radians(lat)) * math.pi / 180
    scale_y = 6_371_000 * math.pi / 180
    ax, ay = (first[0] - lon) * scale_x, (first[1] - lat) * scale_y
    bx, by = (second[0] - lon) * scale_x, (second[1] - lat) * scale_y
    dx, dy = bx - ax, by - ay
    fraction = (
        min(1, max(0, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0
    )
    return math.hypot(ax + fraction * dx, ay + fraction * dy)


def _geometry(row: Row) -> tuple[Row | None, str | None]:
    """Keep only a short, nearby line; otherwise use the official permit point."""
    point = _point_geometry(row)
    if row.get("geometry_multiline") is None:
        return point, None
    try:
        lines = _multiline(row["geometry_multiline"])
        length_m = sum(
            _distance_m(first, second)
            for line in lines
            for first, second in pairwise(line)
        )
        if length_m >= MAX_LINE_LENGTH_M:
            reason = (
                f"corridor length {length_m:.0f} m is not under {MAX_LINE_LENGTH_M} m"
            )
        elif point is None:
            reason = "line cannot be checked without a City permit point"
        elif any(
            min(
                _point_segment_distance_m(point["coordinates"], first, second)
                for first, second in pairwise(line)
            )
            > MAX_POINT_LINE_DISTANCE_M
            for line in lines
        ):
            reason = (
                f"line is not within {MAX_POINT_LINE_DISTANCE_M} m of the permit point"
            )
        else:
            return {"type": "MultiLineString", "coordinates": lines}, None
    except FeedError as exc:
        reason = str(exc)
    action = "used City point" if point else "no City point; omitted from map"
    return point, f"{reason}; {action}"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize_permits(
    rows: list[Any],
    *,
    observed_at: datetime,
    local_timezone: tzinfo,
    lifetime: timedelta = 3 * POLL_INTERVAL,
    previous: list[Row] | None = None,
) -> NormalizedPermits:
    """Convert active pedestrian permits; report records with no map location.

    Start and finish dates are inclusive Edmonton calendar dates. Bad rows are
    isolated; affected previous records keep their original freshness/expiry.
    """
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    observed_at = observed_at.astimezone(UTC)
    if lifetime <= timedelta(0):
        raise ValueError("Observation lifetime must be positive")
    today = observed_at.astimezone(local_timezone).date()
    obstructions: dict[str, Row] = {}
    warnings: set[str] = set()
    failed_permits: set[str] = set()
    unidentified_error = False
    for row in _rows(rows):
        permit = (
            str(row.get("file_number") or "").strip() if isinstance(row, dict) else ""
        )
        try:
            if not isinstance(row, dict):
                raise FeedError("Expected a construction record object")
            if _required(row, "pedestrian_affected").casefold() != "yes":
                continue
            start, finish = (
                _permit_date(row, "start_date"),
                _permit_date(row, "finish_date"),
            )
            if finish < start:
                raise FeedError("Construction finish_date precedes start_date")
            if not start <= today <= finish:
                continue
            permit = _required(row, "file_number")
            geometry, diagnostic = _geometry(row)
            if diagnostic:
                warnings.add(f"{permit}: {diagnostic}")
            if geometry is None:
                raise FeedError(
                    "No usable City geometry or coordinates; omitted from map"
                )
            # The finish day is inclusive, so the closure ends next midnight.
            ends_at = datetime.combine(
                finish + timedelta(days=1), day_time.min, tzinfo=local_timezone
            )
        except (FeedError, OverflowError) as exc:
            warnings.add(f"{permit or 'Unknown permit'}: {exc}; row skipped")
            if permit:
                failed_permits.add(permit)
            else:
                unidentified_error = True
            continue
        street = str(row.get("street_full_name") or "Street not specified").strip()
        intersections = str(row.get("road_segment_intersections") or "").strip()
        identity = json.dumps(
            [street, intersections, geometry], sort_keys=True, separators=(",", ":")
        )
        # A permit can cover many distinct streets/segments. Do not collapse
        # them by file_number; keep IDs stable across row ordering/date edits.
        digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
        source_ref = f"{permit}:{digest}"
        detail = "; ".join(
            part
            for part in (
                permit,
                str(row.get("work_reason") or "").strip(),
                intersections,
                f"Active {start.isoformat()} through {finish.isoformat()} (inclusive)",
            )
            if part
        )
        item = {
            "id": f"{SOURCE}:{source_ref}",
            "source": SOURCE,
            "source_ref": source_ref,
            "category": "sidewalk",
            "kind": "sidewalk_closed",
            "effect": "blocked",
            "title": f"Construction affects pedestrians: {street}",
            "detail": detail,
            "geometry": geometry,
            "confidence": "high",
            "starts_at": _iso(datetime.combine(start, day_time.min, local_timezone)),
            "ends_at": _iso(ends_at),
            "observed_at": _iso(observed_at),
            "expires_at": _iso(min(observed_at + lifetime, ends_at)),
            "url": SOURCE_URL,
            "attribution": "City of Edmonton Open Data",
        }
        if source_ref in obstructions and obstructions[source_ref] != item:
            warnings.add(
                f"{permit}: conflicting duplicate segment; kept latest finish/start "
                "date, then fullest detail"
            )
            # Deterministic across page/row order; amendments cannot fail the feed.
            item = max(
                (obstructions[source_ref], item),
                key=lambda record: (
                    record["ends_at"],
                    record["starts_at"],
                    len(record["detail"]),
                    record["detail"],
                ),
            )
        # Identical copies share an ID and therefore appear only once on the map.
        obstructions[source_ref] = item
    for item in previous or []:
        permit = item["source_ref"].split(":", 1)[0]
        if unidentified_error or permit in failed_permits:
            # Do not renew stale data, or hide a valid replacement from this fetch.
            obstructions.setdefault(item["source_ref"], deepcopy(item))
    for warning in sorted(warnings):
        LOGGER.warning("%s", warning)
    return NormalizedPermits(
        obstructions=[obstructions[key] for key in sorted(obstructions)],
        warnings=sorted(warnings),
    )


class ConstructionAdapter:
    """Fetch helper pending OP-101/103 integration; source failures propagate."""

    def __init__(self, reader: PermitReader, *, local_timezone: tzinfo) -> None:
        self.reader = reader
        self.local_timezone = local_timezone
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self._last_good = NormalizedPermits([], [])

    @property
    def last_good(self) -> NormalizedPermits:
        """Return a copy so callers cannot change the saved snapshot."""
        return deepcopy(self._last_good)

    def fetch(self, *, now: datetime | None = None) -> NormalizedPermits:
        """Fetch once when called; OP-103 owns cadence, retries and cooldowns."""
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("now must include a timezone")
        today = now.astimezone(self.local_timezone).date()
        try:
            rows = _rows(self.reader.read(today))
            result = normalize_permits(
                rows,
                observed_at=now,
                local_timezone=self.local_timezone,
                previous=self._last_good.obstructions,
            )
        except FeedError as exc:
            self.last_error = str(exc)
            raise
        # Replace the snapshot only after the full fetch and conversion succeed.
        self._last_good = result
        self.last_success = now.astimezone(UTC)
        self.last_error = "; ".join(result.warnings) or None
        return self.last_good


def _preview_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--at must be an ISO 8601 time with a timezone (for example 2026-11-07T19:00:00Z)"
        ) from exc


def main() -> None:
    """Preview recorded or live permits without writing to the application."""
    parser = argparse.ArgumentParser(description="Preview OP-107 construction closures")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fixtures", type=Path, help="Directory with recorded permit JSON"
    )
    mode.add_argument(
        "--live", action="store_true", help="Read official City permits once"
    )
    parser.add_argument(
        "--at", type=_preview_time, help="Timezone-aware recorded preview time"
    )
    parser.add_argument("--local-timezone", default="America/Edmonton")
    args = parser.parse_args()
    if args.live and args.at:
        parser.error("--at is only allowed with --fixtures")
    now = args.at or datetime.now(UTC)
    try:
        local_timezone = ZoneInfo(args.local_timezone)
    except ZoneInfoNotFoundError:
        parser.error("--local-timezone must name a known IANA timezone")
    if args.fixtures:
        rows = []
        manifest = json.loads((args.fixtures / "manifest.json").read_text("utf-8"))
        for entry in manifest:
            rows.extend(
                _rows(json.loads((args.fixtures / entry["file"]).read_text("utf-8")))
            )
        result = normalize_permits(rows, observed_at=now, local_timezone=local_timezone)
    else:
        result = ConstructionAdapter(
            ConstructionReader(os.environ.get("SOCRATA_APP_TOKEN")),
            local_timezone=local_timezone,
        ).fetch(now=now)
    print(
        json.dumps(
            {
                "mode": "replay" if args.fixtures else "live",
                "as_of": _iso(now),
                "calendar_timezone": args.local_timezone,
                "obstructions": result.obstructions,
                "warnings": result.warnings,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
