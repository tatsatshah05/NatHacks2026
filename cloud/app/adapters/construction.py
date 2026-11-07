"""OP-107: official construction permits affecting sidewalks.

Dictionary output follows PLAN.md section 10 until OP-101 supplies the shared
Obstruction/SourceAdapter types. This module does not change shared contracts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from datetime import time as day_time
from http.client import HTTPException
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCE = "city_construction"
DATASET = "7wiq-4rgy"
SOURCE_URL = f"https://data.edmonton.ca/d/{DATASET}"
POLL_INTERVAL = timedelta(hours=1)
Row = dict[str, Any]


class FeedError(ValueError):
    """The source cannot safely replace its previous successful snapshot."""


@dataclass
class NormalizedPermits:
    obstructions: list[Row]
    warnings: list[str]


class PermitReader(Protocol):
    """Injectable HTTP dependency, independent of the pending shared interface."""

    def read(self, today: date) -> list[Row]: ...


class ConstructionReader:
    def __init__(
        self,
        app_token: str | None = None,
        *,
        timeout_s: float = 10,
        retries: int = 3,
        page_size: int = 1000,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_s <= 0 or retries < 0 or page_size <= 0:
            raise ValueError("Invalid HTTP timeout, retry count or page size")
        self.app_token = app_token
        self.timeout_s = timeout_s
        self.retries = retries
        self.page_size = page_size
        self.sleep = sleep

    def read(self, today: date) -> list[Row]:
        tomorrow = today + timedelta(days=1)
        where = (
            "pedestrian_affected = 'Yes' "
            f"AND start_date < '{tomorrow.isoformat()}T00:00:00' "
            f"AND finish_date >= '{today.isoformat()}T00:00:00'"
        )
        result: list[Row] = []
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
            if len(page) < self.page_size:
                return result

    def _request(self, request: Request) -> list[Row]:
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_s) as response:
                    return _rows(json.load(response))
            except HTTPError as exc:
                if (
                    exc.code != 429 and not 500 <= exc.code < 600
                ) or attempt == self.retries:
                    raise FeedError(f"Construction feed HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                if attempt == self.retries:
                    raise FeedError(
                        "Construction feed unavailable after retries"
                    ) from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise FeedError("Construction feed returned invalid JSON") from exc
            self.sleep(2**attempt)
        raise AssertionError("Retry loop exhausted without a result")


def _rows(value: Any) -> list[Row]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
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
    except (TypeError, ValueError) as exc:
        raise FeedError("Invalid construction coordinate pair") from exc


def _geometry(row: Row) -> Row | None:
    multiline = row.get("geometry_multiline")
    if multiline is not None:
        if (
            not isinstance(multiline, dict)
            or multiline.get("type") != "MultiLineString"
        ):
            raise FeedError("Invalid construction geometry_multiline")
        lines = multiline.get("coordinates")
        if not isinstance(lines, list) or not lines:
            raise FeedError("Empty construction geometry_multiline")
        output = []
        for line in lines:
            if not isinstance(line, list) or len(line) < 2:
                raise FeedError("Invalid construction line")
            output.append([_position(point) for point in line])
        return {"type": "MultiLineString", "coordinates": output}
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


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize_permits(
    rows: list[Row],
    *,
    observed_at: datetime,
    local_timezone: tzinfo,
    lifetime: timedelta = 3 * POLL_INTERVAL,
) -> NormalizedPermits:
    """Convert active pedestrian permits; report records with no map location.

    Start and finish dates are inclusive Edmonton calendar dates. Invalid
    supplied geometry fails the snapshot rather than silently using a midpoint.
    """
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    observed_at = observed_at.astimezone(UTC)
    if lifetime <= timedelta(0):
        raise ValueError("Observation lifetime must be positive")
    today = observed_at.astimezone(local_timezone).date()
    obstructions: dict[str, Row] = {}
    warnings: set[str] = set()
    for row in _rows(rows):
        if str(row.get("pedestrian_affected", "")).strip().casefold() != "yes":
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
        geometry = _geometry(row)
        if geometry is None:
            warnings.add(f"{permit}: no City geometry or coordinates; omitted from map")
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
        ends_at = datetime.combine(
            finish + timedelta(days=1), day_time.min, tzinfo=local_timezone
        )
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
            raise FeedError(f"Conflicting construction rows for {permit}")
        obstructions[source_ref] = item
    return NormalizedPermits(
        obstructions=[obstructions[key] for key in sorted(obstructions)],
        warnings=sorted(warnings),
    )


class ConstructionAdapter:
    """Local polling helper pending OP-101/103 integration; failures propagate."""

    def __init__(self, reader: PermitReader, *, local_timezone: tzinfo) -> None:
        self.reader = reader
        self.local_timezone = local_timezone
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self._last_good = NormalizedPermits([], [])

    @property
    def last_good(self) -> NormalizedPermits:
        return deepcopy(self._last_good)

    def fetch(self, *, now: datetime | None = None) -> NormalizedPermits:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("now must include a timezone")
        today = now.astimezone(self.local_timezone).date()
        if self.last_success is not None:
            same_day = self.last_success.astimezone(self.local_timezone).date() == today
            if same_day and now - self.last_success < POLL_INTERVAL:
                return self.last_good
        try:
            rows = _rows(self.reader.read(today))
            if rows and not any("pedestrian_affected" in row for row in rows):
                raise FeedError("Construction feed missing pedestrian_affected")
            result = normalize_permits(
                rows, observed_at=now, local_timezone=self.local_timezone
            )
            if result.warnings and not result.obstructions:
                raise FeedError(
                    "No active pedestrian permits have usable map locations"
                )
        except FeedError as exc:
            self.last_error = str(exc)
            raise
        self._last_good = result
        self.last_success = now.astimezone(UTC)
        self.last_error = None
        return self.last_good


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview OP-107 construction closures")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fixtures", type=Path, help="Directory with recorded permit JSON"
    )
    mode.add_argument(
        "--live", action="store_true", help="Read official City permits once"
    )
    parser.add_argument("--at", help="Timezone-aware time for a recorded-data preview")
    parser.add_argument("--local-timezone", default="America/Edmonton")
    args = parser.parse_args()
    if args.live and args.at:
        parser.error("--at is only allowed with --fixtures")
    now = datetime.fromisoformat(args.at) if args.at else datetime.now(UTC)
    local_timezone = ZoneInfo(args.local_timezone)
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
