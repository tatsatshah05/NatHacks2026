"""OP-105: help travelers avoid broken LRT elevators and escalators.

Read City outages, add device details and station locations, and return map
records. Elevator outages block access; escalator outages add a caution.
Keep the last working data when a fetch fails.

Output dictionaries follow PLAN.md section 10. The shared SourceAdapter/Obstruction
integration belongs to OP-101; this module does not define a competing schema.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta, tzinfo
from http.client import HTTPException
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCE = "city_lrt_devices"
OUTAGES = "snws-u3zx"
DEVICES = "javh-pjj2"
STATIONS = "fhxi-cnhe"
POLL_INTERVAL = timedelta(minutes=2)
LOOKUP_INTERVAL = timedelta(days=1)
# PLAN.md's example gives an observation a ten-minute lifetime. Confirm this
# default when integrating with the shared scheduler/store (OP-101/OP-103).
DEFAULT_LIFETIME = timedelta(minutes=10)
Row = dict[str, Any]


class FeedError(ValueError):
    """A feed could not be fetched or safely converted; retain previous data."""


class DatasetReader(Protocol):
    """Local injectable HTTP dependency for offline tests, not SourceAdapter."""

    def read(self, dataset: str) -> list[Row]: ...


class SocrataReader:
    """Read complete datasets with bounded requests, retries and an app token."""

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

    def read(self, dataset: str) -> list[Row]:
        """Download every page so larger City feeds are not cut off."""
        order = {
            OUTAGES: "device_id",
            DEVICES: "device_id",
            STATIONS: "lrt_stop_number",
        }
        if dataset not in order:
            raise ValueError("Unsupported LRT dataset")
        rows: list[Row] = []
        while True:
            query = urlencode(
                {
                    "$limit": self.page_size,
                    "$offset": len(rows),
                    "$order": order[dataset],
                }
            )
            headers = {"Accept": "application/json", "User-Agent": "OpenPath-OP105"}
            if self.app_token:
                headers["X-App-Token"] = self.app_token
            request = Request(
                f"https://data.edmonton.ca/resource/{dataset}.json?{query}",
                headers=headers,
            )
            page = self._request(request)
            rows.extend(page)
            # A short page marks the end of the dataset.
            if len(page) < self.page_size:
                return rows

    def _request(self, request: Request) -> list[Row]:
        """Retry temporary failures; let permanent errors reach the caller."""
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_s) as response:
                    return _rows(json.load(response))
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == self.retries:
                    raise FeedError(f"LRT feed HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                if attempt == self.retries:
                    raise FeedError("LRT feed unavailable after retries") from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise FeedError("LRT feed returned invalid JSON") from exc
            self.sleep(2**attempt)
        raise AssertionError("Retry loop exhausted without a result")


def _rows(value: Any) -> list[Row]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise FeedError("Expected a JSON array of LRT records")
    return value


def _required(row: Row, field: str) -> str:
    value = row.get(field)
    if value is None or not str(value).strip():
        raise FeedError(f"LRT record missing {field}")
    return str(value).strip()


def _instant(value: str, source_timezone: tzinfo) -> datetime:
    """Convert a source timestamp to UTC using the explicitly supplied timezone."""
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            local = parsed.replace(tzinfo=source_timezone)
            # Reject times that need a daylight-saving offset we cannot confirm.
            if local.utcoffset() != local.replace(fold=1).utcoffset():
                raise ValueError("Ambiguous or nonexistent local timestamp")
            parsed = local
        return parsed.astimezone(UTC)
    except ValueError as exc:
        raise FeedError(f"Invalid or ambiguous LRT timestamp: {value}") from exc


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _station_key(name: str) -> str:
    name = re.sub(r"\b(station|stop)\b", "", name, flags=re.IGNORECASE)
    key = re.sub(r"[^a-z0-9]", "", name.casefold())
    # Verified against the recorded City datasets: the device table calls
    # Government Station "Government Centre". Never use fuzzy name matching.
    return {"governmentcentre": "government"}.get(key, key)


def _coordinates(station: Row) -> list[float]:
    """Return the City's coordinates in GeoJSON order: longitude, latitude."""
    try:
        lon = float(station["longitude"])
        lat = float(station["latitude"])
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise ValueError("Nonfinite coordinates")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Coordinates outside WGS84")
        return [lon, lat]
    except (KeyError, TypeError, ValueError) as exc:
        raise FeedError("Invalid LRT station coordinates") from exc


def normalize_outages(
    outages: list[Row],
    devices: list[Row],
    stations: list[Row],
    *,
    source_timezone: tzinfo,
    lifetime: timedelta = DEFAULT_LIFETIME,
) -> list[Row]:
    """Translate an entire successful snapshot; fail rather than silently lose rows.

    Only the outage feed can create an obstruction. Station coordinates are a
    representative station point, not a verified elevator/entrance position.
    """
    if lifetime <= timedelta(0):
        raise ValueError("Observation lifetime must be positive")
    _rows(outages)
    _rows(devices)
    _rows(stations)
    if not devices or not stations:
        raise FeedError("LRT lookup datasets must not be empty")
    device_by_id = {_required(row, "device_id"): row for row in devices}
    station_by_name: dict[str, Row] = {}
    # Multiple stops can represent a station. Prefer a Station over a Stop
    # (Churchill has both), then use the lowest stop ID for a stable City point.
    ordered_stations = sorted(
        stations,
        key=lambda row: (
            not _required(row, "lrt_stop_description").casefold().endswith("station"),
            _required(row, "lrt_stop_number"),
        ),
    )
    for station in ordered_stations:
        key = _station_key(_required(station, "lrt_stop_description"))
        station_by_name.setdefault(key, station)

    result: list[Row] = []
    seen: set[str] = set()
    for outage in outages:
        device_id = _required(outage, "device_id")
        code = _required(outage, "device_op_status_code")
        # City status 0 means in service; only status 1 represents an outage.
        if code == "0":
            continue
        if code != "1":
            raise FeedError(f"Unknown LRT status code for device {device_id}")
        if device_id in seen:
            raise FeedError(f"Duplicate LRT outage for device {device_id}")
        seen.add(device_id)
        # Master fields fill missing metadata only; never use its live status.
        row = {**device_by_id.get(device_id, {}), **outage}
        device_type = _required(row, "device_type").casefold()
        if device_type not in {"elevator", "escalator"}:
            raise FeedError(f"Unknown LRT device type for {device_id}")
        station_name = _required(row, "lrt_station_name")
        station = station_by_name.get(_station_key(station_name))
        if station is None:
            raise FeedError(f"No City station coordinates for {station_name}")
        since = _instant(_required(outage, "since_time_stamp"), source_timezone)
        observed = _instant(_required(outage, "polled_time_stamp"), source_timezone)
        if since > observed:
            raise FeedError(f"Outage starts after observation for device {device_id}")
        location = _required(row, "lrt_device_location")
        # Keep the City's observation time so another download cannot refresh old data.
        result.append(
            {
                "id": f"{SOURCE}:{device_id}",
                "source": SOURCE,
                "source_ref": device_id,
                "category": "indoor",
                "kind": f"{device_type}_out",
                "effect": "blocked" if device_type == "elevator" else "caution",
                "title": f"{device_type.capitalize()} out of service",
                "detail": f"{station_name}, {location}; since {_iso(since)}",
                "geometry": {"type": "Point", "coordinates": _coordinates(station)},
                "confidence": "high",
                "starts_at": _iso(since),
                "ends_at": None,
                "observed_at": _iso(observed),
                "expires_at": _iso(observed + lifetime),
                "url": f"https://data.edmonton.ca/d/{OUTAGES}",
                "attribution": "City of Edmonton Open Data",
            }
        )
    return sorted(result, key=lambda row: row["source_ref"])


class LrtDevicesAdapter:
    """Provisional polling helper until OP-101/103 supply the shared interface.

    Fetch errors propagate, so the store can retain its last snapshot. Successful
    empty outage lists are valid. Lookup/cache updates are atomic; failed polls
    neither clear last_good nor advance last_success.
    """

    def __init__(self, reader: DatasetReader, *, source_timezone: tzinfo) -> None:
        self.reader = reader
        self.source_timezone = source_timezone
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self._lookup_success: datetime | None = None
        self._devices: list[Row] = []
        self._stations: list[Row] = []
        self._last_good: list[Row] = []

    @property
    def last_good(self) -> list[Row]:
        """Return a copy so callers cannot change the saved snapshot."""
        return deepcopy(self._last_good)

    def fetch(self, *, now: datetime | None = None) -> list[Row]:
        """Refresh outages every two minutes and device/station lookups daily."""
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("now must include a timezone")
        now = now.astimezone(UTC)
        if self.last_success is not None and now - self.last_success < POLL_INTERVAL:
            return self.last_good
        try:
            devices, stations = self._devices, self._stations
            refresh = (
                self._lookup_success is None
                or now - self._lookup_success >= LOOKUP_INTERVAL
            )
            if refresh:
                devices = self.reader.read(DEVICES)
                stations = self.reader.read(STATIONS)
            result = normalize_outages(
                self.reader.read(OUTAGES),
                devices,
                stations,
                source_timezone=self.source_timezone,
            )
        except FeedError as exc:
            self.last_error = str(exc)
            raise
        # Save the new data only after every required read and conversion succeeds.
        self._devices, self._stations = deepcopy(devices), deepcopy(stations)
        if refresh:
            self._lookup_success = now
        self._last_good = result
        self.last_success = now
        self.last_error = None
        return self.last_good


def main() -> None:
    """Preview recorded or live outages without writing to the application."""
    parser = argparse.ArgumentParser(description="Preview OP-105 LRT obstructions")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fixtures", type=Path, help="Directory with three recorded JSON files"
    )
    mode.add_argument(
        "--live", action="store_true", help="Read the official City API once"
    )
    parser.add_argument(
        "--source-timezone",
        required=True,
        help="Timezone for unlabeled feed times; confirm with the source owner",
    )
    args = parser.parse_args()
    source_timezone = ZoneInfo(args.source_timezone)
    if args.fixtures:
        payloads = [
            _rows(json.loads((args.fixtures / name).read_text(encoding="utf-8")))
            for name in ("outages.json", "devices.json", "stations.json")
        ]
        obstructions = normalize_outages(*payloads, source_timezone=source_timezone)
    else:
        reader = SocrataReader(os.environ.get("SOCRATA_APP_TOKEN"))
        obstructions = LrtDevicesAdapter(
            reader, source_timezone=source_timezone
        ).fetch()
    print(
        json.dumps(
            {
                "mode": "replay" if args.fixtures else "live",
                "source_timezone_assumption": args.source_timezone,
                "obstructions": obstructions,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
