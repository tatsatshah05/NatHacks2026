"""City of Edmonton traffic disruptions (OP-108).

Source
    "Traffic Disruptions" on the City open data portal, dataset ``k4tx-5k8p``:
    https://data.edmonton.ca/d/k4tx-5k8p. Updated several times a day. Each row is
    one disruption (construction, an incident or a special event) with a point,
    the street and cross streets, an ``impact`` and start/finish dates.

What we take from it
    Only road **closures**: ``Total Closure``, ``Directional Closure``,
    ``Permanent Closure`` and ``Traffic Incident Closure`` whose status is current
    and whose dates cover "now". Delays ("Travel Delays", "Major Delays",
    "Increased Travel Time") do not affect pedestrians and are skipped. A closure
    becomes an ``Obstruction`` in category ``road`` with effect ``caution``: it never
    reroutes anyone, it appears in the route report so people expect a detour at
    the crossing, for example "124 Avenue NW from 66 Street NW to 67 Street NW.
    Sidewalks may be affected."

Freshness
    ``observed_at`` is our fetch time and ``expires_at`` is three cadences later, so
    a disruption disappears if the City feed goes quiet. ``starts_at`` / ``ends_at``
    are the City's dates. Dates in the feed are local Edmonton midnights, so the
    finish date runs to the end of that day.

Replay
    ``FixtureRowReader`` reads the recorded rows in
    ``cloud/tests/fixtures/traffic_disruptions/current.json`` (see its manifest), so
    tests and ``DATA_MODE=replay`` never touch the network.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from cloud.app.schemas import Category, Confidence, Effect, Kind, Obstruction

SOURCE = "city_traffic_disruptions"
DATASET = "k4tx-5k8p"
PORTAL_URL = f"https://data.edmonton.ca/d/{DATASET}"
API_URL = f"https://data.edmonton.ca/resource/{DATASET}.json"
ATTRIBUTION = "City of Edmonton Open Data"
CADENCE_S = 900  # the feed changes a few times a day; 15 minutes is plenty
EDMONTON = ZoneInfo("America/Edmonton")

# Statuses that mean the disruption is in force. "Complete", "Canceled" and "Hold" are not.
ACTIVE_STATUSES = frozenset({"Current", "Revised", "Emergency"})
# Impact values that close the road (fully or in one direction). Everything else is a delay.
CLOSURE_IMPACTS = {
    "Total Closure": "Road closed",
    "Permanent Closure": "Road closed",
    "Traffic Incident Closure": "Road closed",
    "Directional Closure": "Road partly closed",
}
# The contract caps detail at 500 characters.
MAX_DETAIL = 500

LOGGER = logging.getLogger(__name__)

Row = dict[str, Any]


class RowReader(Protocol):
    """Where the rows come from: the City API live, or a recorded file in replay and tests."""

    def read(self) -> list[Row]: ...


class SocrataRowReader:
    """Download every active disruption from the City API, one page at a time."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        app_token: str | None = None,
        page_size: int = 1000,
        max_pages: int = 20,
    ) -> None:
        if page_size < 1 or max_pages < 1:
            raise ValueError("page_size and max_pages must be positive")
        self.client = client
        self.app_token = app_token
        self.page_size = page_size
        self.max_pages = max_pages

    def read(self) -> list[Row]:
        # An app token raises the City's rate limit; without one, anonymous reads can be throttled.
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        statuses = ", ".join(f"'{status}'" for status in sorted(ACTIVE_STATUSES))
        rows: list[Row] = []
        for page in range(self.max_pages):
            response = self.client.get(
                API_URL,
                params={
                    "$where": f"status in ({statuses})",
                    "$order": "disruption_id",
                    "$limit": self.page_size,
                    "$offset": page * self.page_size,
                },
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list) or any(
                not isinstance(row, dict) for row in batch
            ):
                raise ValueError("City API returned an unexpected response shape")
            rows.extend(batch)
            if len(batch) < self.page_size:  # a short page is the last page
                return rows
        raise ValueError("City API page limit reached; refusing a partial refresh")


class FixtureRowReader:
    """Read recorded rows from a JSON file (replay mode and tests)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> list[Row]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"{self.path} must hold a JSON list of rows")  # noqa: TRY004
        return rows


def _local_midnight(value: Any, zone: tzinfo) -> datetime:
    """Parse a City date such as ``2026-11-09T00:00:00.000`` as local time."""
    if not isinstance(value, str) or not value:
        raise ValueError("missing date")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed


def _text(row: Row, field: str) -> str:
    value = row.get(field)
    return value.strip() if isinstance(value, str) else ""


def _geometry(row: Row) -> dict[str, Any]:
    point = row.get("point")
    if not isinstance(point, dict) or point.get("type") != "Point":
        raise ValueError("missing point")
    coords = point.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        raise ValueError("bad point")
    return {"type": "Point", "coordinates": [float(coords[0]), float(coords[1])]}


def _detail(row: Row) -> str:
    """Plain text for the route report: where, what the City says, and the sidewalk note."""
    on, start, end = (
        _text(row, "on_street"),
        _text(row, "from_street"),
        _text(row, "to_street"),
    )
    where = on
    if start and end:
        where = f"{on} from {start} to {end}"
    elif start or end:
        where = f"{on} at {start or end}"
    parts = [f"{where}." if where else ""]
    details = _text(row, "details")
    if details:
        parts.append(details if details.endswith((".", "!", "?")) else details + ".")
    infrastructure = _text(row, "infrastructure").lower()
    parts.append(
        "Sidewalk affected."
        if "sidewalk" in infrastructure
        else "Sidewalks may be affected."
    )
    text = " ".join(part for part in parts if part)
    return text if len(text) <= MAX_DETAIL else text[: MAX_DETAIL - 3].rstrip() + "..."


def normalize(
    rows: Sequence[Row], now: datetime, *, zone: tzinfo = EDMONTON
) -> tuple[list[Obstruction], list[str]]:
    """Turn City rows into obstructions. Returns the items and one note per skipped row.

    A bad row never fails the whole refresh: it is skipped and reported, so one
    data-entry mistake at the City cannot empty the map.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    items: list[Obstruction] = []
    skipped: list[str] = []
    for row in rows:
        ref = _text(row, "disruption_id")
        try:
            if not ref:
                raise ValueError("missing disruption_id")
            if _text(row, "status") not in ACTIVE_STATUSES:
                continue  # finished, cancelled or on hold: not a skip, just not in force
            title = CLOSURE_IMPACTS.get(_text(row, "impact"))
            if title is None:
                continue  # a delay, not a closure
            starts_at = _local_midnight(row.get("start_date"), zone)
            finish_day = _local_midnight(row.get("finish_date"), zone)
            # The finish date is inclusive: the closure lasts until the end of that day.
            ends_at = datetime.combine(
                finish_day.date(), time(23, 59, 59), tzinfo=finish_day.tzinfo
            )
            if ends_at < starts_at:
                raise ValueError("finish before start")
            if not (starts_at <= now <= ends_at):
                continue  # scheduled for later, or already over
            items.append(
                Obstruction(
                    source=SOURCE,
                    source_ref=ref,
                    category=Category.ROAD,
                    kind=Kind.ROAD_CLOSED,
                    effect=Effect.CAUTION,
                    title=title,
                    detail=_detail(row),
                    geometry=_geometry(row),
                    confidence=Confidence.HIGH,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    observed_at=now,
                    expires_at=now + timedelta(seconds=3 * CADENCE_S),
                    url=PORTAL_URL,
                    attribution=ATTRIBUTION,
                )
            )
        except (ValueError, TypeError) as error:
            note = f"disruption {ref or '?'}: {error}"
            skipped.append(note)
            LOGGER.warning("SKIP %s %s", SOURCE, note)
    items.sort(key=lambda item: item.source_ref)
    return items, skipped


class TrafficDisruptionsAdapter:
    """The ``SourceAdapter`` for road closures. The scheduler owns cadence and retries."""

    source = SOURCE
    cadence_s = CADENCE_S
    url = PORTAL_URL

    def __init__(self, reader: RowReader, *, zone: tzinfo = EDMONTON) -> None:
        self.reader = reader
        self.zone = zone
        self.last_good: list[Obstruction] = []
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self.skipped: list[str] = []

    def fetch(self, now: datetime | None = None) -> list[Obstruction]:
        """Read the whole feed once and return every active closure.

        Raises when the feed cannot be read, so the store keeps its last snapshot.
        """
        now = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            rows = self.reader.read()
        except (httpx.HTTPError, OSError, ValueError) as error:
            self.last_error = f"{type(error).__name__}: {error}"
            LOGGER.warning("REFRESH %s failed: %s", SOURCE, self.last_error)
            raise
        items, skipped = normalize(rows, now, zone=self.zone)
        self.last_good = items
        self.skipped = skipped
        self.last_success = now
        self.last_error = None
        LOGGER.info(
            "REFRESH %s success items=%d skipped=%d", SOURCE, len(items), len(skipped)
        )
        return items


def main() -> None:
    """Preview the adapter's output: ``python -m cloud.app.adapters.traffic_disruptions``."""
    parser = argparse.ArgumentParser(
        description="Preview current road closures as obstructions."
    )
    parser.add_argument(
        "--fixtures", type=Path, help="Recorded rows file (replay); default: live"
    )
    parser.add_argument(
        "--at", help="Fetch time to simulate with --fixtures, e.g. 2026-11-10T18:00:00Z"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    now = datetime.fromisoformat(args.at) if args.at else datetime.now(UTC)
    if args.fixtures:
        adapter = TrafficDisruptionsAdapter(FixtureRowReader(args.fixtures))
    else:
        import os

        adapter = TrafficDisruptionsAdapter(
            SocrataRowReader(
                httpx.Client(), app_token=os.environ.get("SOCRATA_APP_TOKEN")
            )
        )
    items = adapter.fetch(now)
    print(
        json.dumps(
            {
                "mode": "replay" if args.fixtures else "live",
                "count": len(items),
                "skipped": adapter.skipped,
                "items": [item.model_dump(mode="json") for item in items],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
