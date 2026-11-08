"""Purpose: warn about curb-ramp snow ridges after verified recent road clearing.

Read the City's road-status feed once per scheduler refresh. Skip inactive NA
records and individual rows missing status or service-time evidence. Emit each
recently plowed road's own line as a caution; OP-206 matches that line to walking
crossings with a 15 m buffer. HTTP retries/backoff belong to OP-103.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING, Any

import httpx

from cloud.app.adapters._quality import RowQuality

if TYPE_CHECKING:
    from cloud.app.schemas import Obstruction

DATASET = "8pdx-hfxi"
API_URL = f"https://data.edmonton.ca/resource/{DATASET}.json"
WARNING = "Plow passed. Curb ramps may be blocked by snow ridges."
CLOCK_SKEW = timedelta(seconds=5)
# The dataset's published columns (cloud/tests/fixtures/snow_clearing/
# source_metadata.json), for OP-407's unexpected-field check.
EXPECTED_FIELDS = frozenset(
    {
        "district",
        "fid",
        "geometry_line",
        "last_updat",
        "linear_km",
        "loc_note",
        "num_passes",
        "pass_km",
        "priority",
        "road_from",
        "road_on",
        "road_to",
        "route",
        "route_id",
        "route_stat",
        "seg_stat",
        "segment",
        "serv_ach",
        "serv_hrs",
    }
)
Row = dict[str, Any]
logger = logging.getLogger(__name__)


def one_line(value: Any) -> str:
    """Keep source values and error messages on a single log line."""
    return " ".join(str(value).splitlines())


def as_utc(value: datetime) -> datetime:
    """Compare elapsed hours in UTC and reject timezone-free times."""
    if not isinstance(value, datetime):
        raise TypeError("Service time must be a datetime")
    if value.tzinfo is None:
        raise ValueError("Use a timezone-aware datetime")
    return value.astimezone(UTC)


def required_text(row: Row, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing or invalid snow-clearing field: " + field)
    return value.strip()


def road_geometry(row: Row) -> dict[str, Any]:
    """Keep the City's LineString/MultiLineString, checking valid positions."""
    geometry = row.get("geometry_line")
    if not isinstance(geometry, dict) or geometry.get("type") not in (
        "LineString",
        "MultiLineString",
    ):
        raise ValueError("Road needs LineString or MultiLineString geometry")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("Road geometry is empty")
    lines = [coordinates] if geometry["type"] == "LineString" else coordinates
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            raise ValueError("Road lines need at least two positions")
        for position in line:
            if not isinstance(position, list) or len(position) not in (2, 3):
                raise ValueError("Invalid road position")
            if not all(
                isinstance(c, int | float) and not isinstance(c, bool) and isfinite(c)
                for c in position
            ):
                raise ValueError("Road coordinates must be finite numbers")
            if not -180 <= position[0] <= 180 or not -90 <= position[1] <= 90:
                raise ValueError("Road coordinates out of range")
    return copy.deepcopy(geometry)


def build_obstruction(**fields: Any) -> Obstruction:
    """Use OP-101's shared model once it is available on this branch."""
    from cloud.app.schemas import Obstruction

    return Obstruction(**fields)


class SnowClearingAdapter:
    """Fetch a complete snapshot once; the scheduler controls retries and cadence.

    verified_service_time supplies actual clearing evidence. No timestamp is
    inferred from deprecated last_updat, serv_hrs, or the time of this fetch.
    """

    source = "city_snow_clearing"
    cadence_s = 900

    def __init__(
        self,
        client: httpx.Client,
        *,
        app_token: str | None = None,
        verified_service_time: Callable[[Row], datetime | None] | None = None,
        item_factory: Callable[..., Any] = build_obstruction,
        page_size: int = 1000,
        max_pages: int = 100,
    ) -> None:
        if page_size < 1 or max_pages < 1:
            raise ValueError("Invalid pagination limits")
        self.client = client
        self.app_token = app_token
        self.verified_service_time = verified_service_time
        self.item_factory = item_factory
        self.page_size = page_size
        self.max_pages = max_pages
        self.last_good: list[Obstruction] | None = None
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self.quality = RowQuality(EXPECTED_FIELDS)  # OP-407, read by the scheduler

    def _page(self, offset: int, *, limit: int | None = None) -> list[Row]:
        """Read a page once. Errors propagate to OP-103's retry policy."""
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        response = self.client.get(
            API_URL,
            params={
                "$limit": self.page_size if limit is None else limit,
                "$offset": offset,
                "$order": "route_id,fid",
                # Avoid inactive road geometry while still accepting/observing
                # nullable area records, which normalization skips and logs.
                "$where": "(upper(route_stat) != 'NA' OR route_stat IS NULL) AND (upper(seg_stat) != 'NA' OR seg_stat IS NULL)",
            },
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Expected a list of snow-clearing records")
        return rows

    def _skip(self, row: Row, reason: Any) -> None:
        identifier = row.get("route_id") or row.get("fid") or "unknown"
        self.quality.reject(reason)
        logger.warning(
            "SKIP %s row=%s reason=%s",
            self.source,
            one_line(identifier),
            one_line(reason),
        )

    def normalize(self, rows: list[Row], now: datetime) -> list[Obstruction]:
        """Build cautions from usable rows, allowing five seconds of clock skew."""
        now = as_utc(now)
        by_route: dict[str, Row] = {}
        for row in rows:
            self.quality.see(row)
            # Area records often have no status. One such record must not stale
            # the entire feed or stop an inactive snow event returning zero items.
            try:
                required_text(row, "route_stat")
                required_text(row, "seg_stat")
                route_id = required_text(row, "route_id")
            except ValueError as exc:
                self._skip(row, exc)
                continue
            # fid changes between exports, so road identity uses route_id alone.
            comparison = {key: value for key, value in row.items() if key != "fid"}
            if route_id in by_route and by_route[route_id] != comparison:
                raise ValueError("Conflicting duplicate road segment: " + route_id)
            by_route[route_id] = comparison

        items = []
        for route_id, row in sorted(by_route.items()):
            route_status = required_text(row, "route_stat").casefold()
            segment_status = required_text(row, "seg_stat").casefold()
            if "na" in (route_status, segment_status):
                continue
            if segment_status in ("not started", "scheduled", "in progress"):
                continue
            if segment_status != "completed":
                raise ValueError("Unrecognized segment status: " + segment_status)

            try:
                serviced = (
                    self.verified_service_time(row)
                    if self.verified_service_time
                    else None
                )
                if serviced is None:
                    raise ValueError(
                        "No verified service time; last_updat is deprecated"
                    )
                serviced = as_utc(serviced)
                if serviced > now + CLOCK_SKEW:
                    raise ValueError(
                        "Service time exceeds five-second clock-skew tolerance"
                    )
                # A slightly fast provider clock should not defer the caution.
                serviced = min(serviced, now)
                geometry = road_geometry(row)
            except (ValueError, TypeError) as exc:
                self._skip(row, exc)
                continue
            deadline = serviced + timedelta(hours=12)
            if deadline <= now:
                continue
            items.append(
                self.item_factory(
                    id=self.source + ":" + route_id,
                    source=self.source,
                    source_ref=route_id,
                    category="road",
                    kind="plow_passed",
                    effect="caution",
                    title=WARNING,
                    detail=None,
                    geometry=geometry,
                    confidence="high",
                    starts_at=serviced,
                    ends_at=deadline,
                    observed_at=now,
                    expires_at=min(
                        deadline, now + timedelta(seconds=self.cadence_s * 2)
                    ),
                    url=f"https://data.edmonton.ca/d/{DATASET}",
                    attribution="City of Edmonton Open Data",
                )
            )
        return items

    def fetch(self, now: datetime) -> list[Obstruction]:
        """Return a complete source refresh; preserve last-good data on failure."""
        self.quality.start()
        try:
            now = as_utc(now)
            rows = []
            for page in range(self.max_pages):
                batch = self._page(page * self.page_size)
                rows.extend(batch)
                if len(batch) < self.page_size:
                    break
            else:
                # A full last page might be the exact end of the feed. Probe
                # just one extra row before declaring the snapshot too large.
                if self._page(self.max_pages * self.page_size, limit=1):
                    raise ValueError("Page limit reached; refusing a partial refresh")
            items = self.normalize(rows, now)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self.last_error = str(exc)
            logger.warning("REFRESH %s failed: %s", self.source, one_line(exc))
            raise
        self.last_good = copy.deepcopy(items)
        self.last_success = now
        self.last_error = None
        logger.info("REFRESH %s success items=%d", self.source, len(items))
        return items
