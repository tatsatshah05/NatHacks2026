"""Purpose: turn City snow complaints and trail notices into map advisories.

Snow complaints are counted by neighbourhood and shown as area cautions.
Trail notices become either blocked paths or cautions, based on the City's type.
Both adapters fetch every page, validate the data, and save a successful snapshot.
Results use PLAN.md's obstruction fields for later OP-101/OP-103 integration.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from cloud.app.adapters._text import clip_detail

# The IANA zone name; a named constant so secret scanners do not mistake the ZoneInfo key= argument for a credential.
EDMONTON_ZONE_NAME = "America/Edmonton"

CITY_BASE = "https://data.edmonton.ca"
ATTRIBUTION = "City of Edmonton Open Data"
# Use packaged current rules even on hosts whose system tzdb predates ABT.
with (
    files("tzdata.zoneinfo").joinpath("America").joinpath("Edmonton").open("rb") as _tz
):
    EDMONTON = ZoneInfo.from_file(_tz, key=EDMONTON_ZONE_NAME)
Obstruction = dict[str, Any]
logger = logging.getLogger(__name__)


def one_line(value: Any) -> str:
    """Keep source values and exception messages on one log line."""
    return " ".join(str(value).splitlines())


def utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("Refresh time must be timezone-aware")
    return now.astimezone(UTC)


def iso(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def city_time(value: str, source_timezone: ZoneInfo = EDMONTON) -> datetime:
    """Treat floating City timestamps as publisher-local, not machine-local.

    Publisher-local time is an explicit assumption: these feeds do not declare
    a timezone in their metadata. Callers can supply a confirmed timezone later.
    Ambiguous/nonexistent DST times are rejected rather than guessed.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        first = parsed.replace(tzinfo=source_timezone, fold=0)
        second = parsed.replace(tzinfo=source_timezone, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError("Ambiguous or nonexistent City timestamp")
        parsed = first
    return utc(parsed)


def end_time(value: Any, source_timezone: ZoneInfo = EDMONTON) -> datetime | None:
    """Parse definite dates/months as inclusive; leave vague estimates unknown."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        logger.warning(
            "UNPARSED end_date value=%s reason=expected text", one_line(repr(value))
        )
        return None
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"^end of\s+", "", cleaned, flags=re.IGNORECASE).rstrip(".")
    # City's text dates include dotted month abbreviations and US-style slashes.
    cleaned = re.sub(r"\b([A-Za-z]{3})\.", r"\1", cleaned)
    for pattern in (
        "%Y-%m-%d",
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%m/%d/%Y",
    ):
        try:
            date = datetime.strptime(cleaned, pattern).replace(tzinfo=source_timezone)
            return utc(date + timedelta(days=1))
        except ValueError:
            pass
    for pattern in ("%B %Y", "%b %Y"):
        try:
            date = datetime.strptime(cleaned, pattern).replace(tzinfo=source_timezone)
            next_month = (
                date.replace(year=date.year + 1, month=1)
                if date.month == 12
                else date.replace(month=date.month + 1)
            )
            return utc(next_month)
        except ValueError:
            pass
    logger.warning("UNPARSED end_date value=%s", one_line(repr(value)))
    return None


def text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing or invalid City field: " + field)
    return value.strip()


def point(row: dict[str, Any], field: str) -> dict[str, Any]:
    """Validate a City map marker before it reaches the shared map/store."""
    value = row.get(field)
    if not isinstance(value, dict) or value.get("type") != "Point":
        raise ValueError("Missing or invalid City point geometry")
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise ValueError("Invalid point coordinates")
    if any(
        isinstance(c, bool) or not isinstance(c, (float, int)) or not isfinite(c)
        for c in coordinates
    ):
        raise ValueError("Invalid point coordinate values")
    if not -180 <= coordinates[0] <= 180 or not -90 <= coordinates[1] <= 90:
        raise ValueError("Point coordinates out of range")
    return copy.deepcopy(value)


def unique_rows(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Count identical IDs once; reject conflicting copies rather than guessing."""
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = text(row, field)
        if key in seen and seen[key] != row:
            raise ValueError("Conflicting duplicate City row: " + key)
        seen[key] = row
    return list(seen.values())


class CityAdapter:
    """Share HTTP requests, pagination, and cache handling between both sources."""

    dataset = ""
    source = ""
    poll_interval_seconds = 0
    order_by = ""
    timestamp_timezone = EDMONTON

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        app_token: str | None = None,
        page_size: int = 1000,
        max_pages: int = 100,
        retries: int = 3,
        source_timezone: ZoneInfo | None = None,
    ) -> None:
        if page_size < 1 or max_pages < 1 or retries < 0:
            raise ValueError("Invalid City adapter request limits")
        self.client = client
        self.app_token = app_token
        self.page_size = page_size
        self.max_pages = max_pages
        self.retries = retries
        self.source_timezone = source_timezone or self.timestamp_timezone
        self.last_good: list[Obstruction] | None = None
        self.last_success: datetime | None = None
        self.last_error: str | None = None

    def query(self, now: datetime) -> dict[str, str]:
        return {}

    def normalize(self, rows: list[dict[str, Any]], now: datetime) -> list[Obstruction]:
        raise NotImplementedError

    async def _page(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Request one page; retry only server errors and HTTP timeouts."""
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.get(
                    f"{CITY_BASE}/resource/{self.dataset}.json",
                    params=params,
                    headers=headers,
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or any(
                    not isinstance(row, dict) for row in rows
                ):
                    raise ValueError("City API returned an unexpected response schema")
                return rows
            except httpx.HTTPStatusError as exc:
                if not 500 <= exc.response.status_code < 600 or attempt == self.retries:
                    raise
            except httpx.TimeoutException:
                if attempt == self.retries:
                    raise
            await asyncio.sleep(2**attempt)
        raise AssertionError("Unreachable")

    async def fetch(self, now: datetime) -> list[Obstruction]:
        """Build a complete refresh; leave last_good untouched if any step fails."""
        try:
            now = utc(now)
            rows = []
            # Fetch the full source before replacing the previous snapshot.
            for page in range(self.max_pages):
                batch = await self._page(
                    {
                        **self.query(now),
                        "$order": self.order_by,
                        "$limit": self.page_size,
                        "$offset": page * self.page_size,
                    }
                )
                rows.extend(batch)
                if len(batch) < self.page_size:
                    break
            else:
                raise ValueError(
                    "City API page limit reached; refusing a partial refresh"
                )
            items = self.normalize(rows, now)
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Retain the complete previous snapshot; let the scheduler mark failure.
            self.last_error = str(exc)
            logger.warning("REFRESH %s failed: %s", self.source, one_line(exc))
            raise
        # Keep a separate copy so callers cannot accidentally change the cache.
        self._log_changes(items)
        self.last_good = copy.deepcopy(items)
        self.last_success = now
        self.last_error = None
        logger.info("REFRESH %s success items=%d", self.source, len(items))
        return items

    def _log_changes(self, items: list[Obstruction]) -> None:
        """Log meaningful additions, updates and removals, ignoring freshness ticks."""
        old = {item["id"]: item for item in self.last_good or []}
        new = {item["id"]: item for item in items}

        def content(item: Obstruction) -> dict[str, Any]:
            return {
                key: value
                for key, value in item.items()
                if key not in ("observed_at", "expires_at")
            }

        for key in sorted(new):
            if key not in old:
                logger.info("CHANGE %s new %s", one_line(key), new[key]["kind"])
            elif content(old[key]) != content(new[key]):
                logger.info("CHANGE %s updated %s", one_line(key), new[key]["kind"])
        for key in sorted(old.keys() - new.keys()):
            logger.info("CHANGE %s removed %s", one_line(key), old[key]["kind"])

    def item(
        self,
        source_ref: str,
        now: datetime,
        *,
        kind: str,
        effect: str,
        title: str,
        detail: str,
        geometry: dict[str, Any],
        confidence: str,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
    ) -> Obstruction:
        """Build an item in the planned shared format, with a freshness deadline."""
        expires = now + timedelta(seconds=self.poll_interval_seconds * 2)
        if ends_at is not None:
            expires = min(expires, ends_at)
        return {
            "id": f"{self.source}:{source_ref}",
            "source": self.source,
            "source_ref": source_ref,
            "category": "sidewalk",
            "kind": kind,
            "effect": effect,
            "title": title,
            "detail": detail,
            "geometry": geometry,
            "confidence": confidence,
            "starts_at": iso(starts_at) if starts_at is not None else None,
            "ends_at": iso(ends_at) if ends_at is not None else None,
            "observed_at": iso(now),
            "expires_at": iso(expires),
            "url": f"{CITY_BASE}/d/{self.dataset}",
            "attribution": ATTRIBUTION,
        }


class SnowComplaintsAdapter(CityAdapter):
    """Count recent open complaints per neighbourhood; refresh hourly."""

    dataset = "9i6v-cqzn"
    source = "city_311_snow"
    poll_interval_seconds = 3600
    order_by = "row_id"

    def query(self, now: datetime) -> dict[str, str]:
        """Ask the City for open complaints inside the 72-hour local-time window."""

        def local(value: datetime) -> str:
            return (
                value.astimezone(self.source_timezone).replace(tzinfo=None).isoformat()
            )

        return {
            "$where": (
                "upper(request_status) = 'OPEN' AND "
                f"date_created >= '{local(now - timedelta(hours=72))}' AND "
                f"date_created <= '{local(now)}'"
            )
        }

    def normalize(self, rows: list[dict[str, Any]], now: datetime) -> list[Obstruction]:
        """Filter requests, group by neighbourhood ID, and create area cautions."""
        now = utc(now)
        groups: dict[str, list[tuple[dict[str, Any], datetime]]] = defaultdict(list)
        # Check dates/status again so recorded data follows the same rules as live data.
        for row in unique_rows(rows, "row_id"):
            status = text(row, "request_status").casefold()
            if status == "closed":
                continue
            if status != "open":
                raise ValueError("Unknown snow complaint status")
            created = city_time(text(row, "date_created"), self.source_timezone)
            if not now - timedelta(hours=72) <= created <= now:
                continue
            # A request without an area/marker cannot locate an advisory. Skip
            # that request while still publishing other valid neighbourhoods.
            try:
                neighbourhood_id = text(row, "neighbourhood_id")
                text(row, "neighbourhood")
                point(row, "nbhd_location")
            except ValueError as exc:
                logger.warning(
                    "SKIP %s row=%s reason=%s",
                    self.source,
                    one_line(row["row_id"]),
                    one_line(exc),
                )
                continue
            groups[neighbourhood_id].append((row, created))
        items = []
        for neighbourhood_id, entries in sorted(groups.items()):
            first = entries[0][0]
            name = text(first, "neighbourhood")
            # This marker locates the neighbourhood, not an individual complaint.
            geometry = point(first, "nbhd_location")
            if any(point(row, "nbhd_location") != geometry for row, _ in entries):
                raise ValueError("Inconsistent neighbourhood centroids")
            # The count becomes outdated when its oldest complaint leaves the window.
            first_expiry = min(created + timedelta(hours=72) for _, created in entries)
            item = self.item(
                neighbourhood_id,
                now,
                kind="snow_complaints",
                effect="caution",
                title=f"Snow complaints in {name}",
                detail=f"{len(entries)} open snow-on-walk complaint(s) in the last 72 hours; area, not exact location. Marker is the neighbourhood centroid.",
                geometry=geometry,
                confidence="low",
                starts_at=min(created for _, created in entries),
            )
            item["expires_at"] = iso(min(first_expiry, now + timedelta(hours=2)))
            items.append(item)
        return items


class TrailClosuresAdapter(CityAdapter):
    """Convert current trail notices to blocked/caution items; refresh every 15 min."""

    dataset = "k4mi-dkvi"
    source = "city_trail_closures"
    poll_interval_seconds = 900
    order_by = "id_number"
    # The official ArcGIS layer linked by the dataset declares date fields UTC.
    timestamp_timezone = ZoneInfo("UTC")

    def normalize(self, rows: list[dict[str, Any]], now: datetime) -> list[Obstruction]:
        """Keep active notices and use the City's structured closure type."""
        now = utc(now)
        items = []
        for row in unique_rows(rows, "id_number"):
            # Parse every supplied end date so unknown values are visible in logs.
            raw_end = row.get("end_date")
            ends = end_time(raw_end, EDMONTON)
            if not isinstance(row.get("details"), str) or not row["details"].strip():
                logger.warning(
                    "SKIP %s row=%s reason=missing or invalid details",
                    self.source,
                    one_line(row["id_number"]),
                )
                continue
            starts = (
                city_time(text(row, "start_date"), self.source_timezone)
                if row.get("start_date")
                else None
            )
            # Exclude future and finished notices; unclear finish dates stay unknown.
            if (starts is not None and starts > now) or (
                ends is not None and ends <= now
            ):
                continue
            # Use the official type instead of guessing severity from the description.
            closure = text(row, "type_of_closure").casefold()
            if closure not in ("permanent", "temporary", "caution"):
                raise ValueError("Unknown trail closure type")
            caution = closure == "caution"
            location = row.get("location_name") or "River Valley trail"
            reported_end = (
                f" Reported end: {raw_end}."
                if raw_end is not None and raw_end != ""
                else ""
            )
            # A long City notice is shortened, never dropped: the closure still
            # reaches the map and routing, and the item's url links the full text.
            detail = clip_detail(text(row, "details"), keep=reported_end)
            items.append(
                self.item(
                    text(row, "id_number"),
                    now,
                    kind="trail_caution" if caution else "trail_closed",
                    effect="caution" if caution else "blocked",
                    title=f"{'Trail caution' if caution else 'Trail closed'}: {location}",
                    detail=detail,
                    geometry=point(row, "geometry_point"),
                    confidence="high",
                    starts_at=starts,
                    ends_at=ends,
                )
            )
        return items
