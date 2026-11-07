"""Purpose: warn at road crossings after a verified recent plow pass.

Read the City's route-status feed every 15 minutes. Outside snow events, NA
produces no items. A completed segment needs a verified service time and mapped
crossings before it can create a caution. The public feed currently supplies
neither a usable service timestamp nor walking-network crossing IDs, so these
are explicit inputs from the source/matching integration, never guessed here.
"""

from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

import httpx

if TYPE_CHECKING:
    from ..schemas import Obstruction

DATASET = "8pdx-hfxi"
API_URL = f"https://data.edmonton.ca/resource/{DATASET}.json"
WARNING = "Plow passed. Curb ramps may be blocked by snow ridges."
Row = dict[str, Any]
# An internal resolver result: stable crossing ID and its GeoJSON geometry.
Crossing = tuple[str, dict[str, Any]]


class MissingServiceTime(ValueError):
    """A completed status alone cannot establish a pass within the last 12 hours."""


class MissingCrossings(ValueError):
    """The serviced road still needs to be linked to actual walking crossings."""


def as_utc(value: datetime) -> datetime:
    """Compare elapsed hours in UTC and reject ambiguous timezone-free times."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Use a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def required_text(row: Row, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing or invalid snow-clearing field: " + field)
    return value.strip()


def build_obstruction(**fields: Any) -> Obstruction:
    """Use OP-101's actual shared model once it is available on this branch."""
    from ..schemas import Obstruction

    return Obstruction(**fields)


class SnowClearingAdapter:
    """Read one complete source snapshot, retaining previous data on failure.

    The sync fetch(now) method and cadence_s match the pending OP-101 protocol.
    verified_service_time must come from evidence of actual service, and
    resolve_crossings must return walking crossings of the serviced road.
    """

    source = "city_snow_clearing"
    cadence_s = 900

    def __init__(
        self,
        client: httpx.Client,
        *,
        app_token: Optional[str] = None,
        verified_service_time: Optional[Callable[[Row], Optional[datetime]]] = None,
        resolve_crossings: Optional[Callable[[Row], Sequence[Crossing]]] = None,
        item_factory: Callable[..., Any] = build_obstruction,
        page_size: int = 1000,
        max_pages: int = 100,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if page_size < 1 or max_pages < 1 or retries < 0:
            raise ValueError("Invalid pagination or retry limits")
        self.client = client
        self.app_token = app_token
        self.verified_service_time = verified_service_time
        self.resolve_crossings = resolve_crossings
        self.item_factory = item_factory
        self.page_size = page_size
        self.max_pages = max_pages
        self.retries = retries
        self.sleep = sleep
        self.last_good: Optional[list[Obstruction]] = None
        self.last_success: Optional[datetime] = None
        self.last_error: Optional[str] = None

    def _page(self, offset: int) -> list[Row]:
        """Read one stable-ordered page, retrying network/JSON failures."""
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        for attempt in range(self.retries + 1):
            try:
                response = self.client.get(
                    API_URL,
                    params={
                        "$limit": self.page_size,
                        "$offset": offset,
                        "$order": "route_id,fid",
                        # Avoid downloading every inactive road geometry. Nulls
                        # remain visible so malformed status records fail validation.
                        "$where": "(upper(route_stat) != 'NA' OR route_stat IS NULL) AND (upper(seg_stat) != 'NA' OR seg_stat IS NULL)",
                    },
                    headers=headers,
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or any(
                    not isinstance(row, dict) for row in rows
                ):
                    raise ValueError("Expected a list of snow-clearing records")
                return rows
            except (httpx.HTTPError, ValueError):
                if attempt == self.retries:
                    raise
                self.sleep(2**attempt)
        raise AssertionError("Unreachable")

    def normalize(self, rows: list[Row], now: datetime) -> list[Obstruction]:
        """Keep only verified passes less than 12 hours old, at mapped crossings."""
        now = as_utc(now)
        by_route: dict[str, Row] = {}
        for row in rows:
            route_id = required_text(row, "route_id")
            # fid is explicitly nonstable in the metadata, so it is not item identity.
            comparison = {key: value for key, value in row.items() if key != "fid"}
            if route_id in by_route and by_route[route_id] != comparison:
                raise ValueError("Conflicting duplicate road segment: " + route_id)
            by_route[route_id] = comparison

        items = []
        for route_id, row in sorted(by_route.items()):
            route_status = required_text(row, "route_stat").casefold()
            segment_status = required_text(row, "seg_stat").casefold()
            # NA means the event map is inactive; a zero serv_hrs is not a pass time.
            if "na" in (route_status, segment_status):
                continue
            if segment_status in ("not started", "scheduled", "in progress"):
                continue
            if segment_status != "completed":
                raise ValueError("Unrecognized segment status: " + segment_status)

            serviced = (
                self.verified_service_time(row) if self.verified_service_time else None
            )
            if serviced is None:
                raise MissingServiceTime(
                    "Completed segment needs verified service time; last_updat is deprecated"
                )
            serviced = as_utc(serviced)
            if serviced > now:
                raise ValueError("Service time cannot be in the future")
            deadline = serviced + timedelta(hours=12)
            if deadline <= now:
                continue

            crossings = self.resolve_crossings(row) if self.resolve_crossings else None
            if not crossings:
                raise MissingCrossings(
                    "No mapped crossings for serviced route: " + route_id
                )
            seen_crossings = set()
            for crossing_id, geometry in crossings:
                if not isinstance(crossing_id, str) or not crossing_id.strip():
                    raise ValueError("Crossing needs a stable ID")
                if crossing_id in seen_crossings:
                    raise ValueError("Duplicate crossing ID")
                seen_crossings.add(crossing_id)
                if (
                    not isinstance(geometry, dict)
                    or geometry.get("type") not in ("Point", "LineString")
                    or not geometry.get("coordinates")
                ):
                    raise ValueError("Crossing needs a point or line geometry")
                # The shared model performs detailed GeoJSON validation.
                source_ref = route_id + ":" + crossing_id
                items.append(
                    self.item_factory(
                        id=self.source + ":" + source_ref,
                        source=self.source,
                        source_ref=source_ref,
                        category="road",
                        kind="plow_passed",
                        effect="caution",
                        title=WARNING,
                        detail=None,
                        geometry=copy.deepcopy(geometry),
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
        """Return a complete refresh or raise so the store retains previous items."""
        now = as_utc(now)
        try:
            rows = []
            for page in range(self.max_pages):
                batch = self._page(page * self.page_size)
                rows.extend(batch)
                if len(batch) < self.page_size:
                    break
            else:
                raise ValueError("Page limit reached; refusing a partial refresh")
            items = self.normalize(rows, now)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self.last_error = type(exc).__name__
            raise
        # A genuine all-NA/empty success can clear the source; a failure never does.
        self.last_good = copy.deepcopy(items)
        self.last_success = now
        self.last_error = None
        return items
