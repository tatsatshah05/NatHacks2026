"""OP-105: help travelers avoid broken LRT elevators and escalators.

Read City outages, add device details and station locations, and return map
records. Elevator outages block access; escalator outages add a caution.
Keep the last working data when a fetch fails.

OP-313: each outage's ``detail`` also names the station's other elevators and
escalators that are in service, from the daily City device list grouped by
station. A device is in service unless the outage feed lists it; the device
list's own status is never used. Without a usable device list the outages still
post, with the plain "<station>, <location>" detail.

Output dictionaries follow PLAN.md section 10. The shared SourceAdapter/Obstruction
integration belongs to OP-101; this module does not define a competing schema.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from email.utils import parsedate_to_datetime
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
# Each successful fetch confirms the outage for another ten minutes. The City's
# own poll timestamp can be much older and must not shorten that lifetime.
DEFAULT_LIFETIME = timedelta(minutes=10)
# Obstruction.detail is at most 500 characters (cloud/app/schemas.py). An item
# with a longer detail would be skipped by the scheduler, so every detail fits.
DETAIL_LIMIT = 500
DEVICE_KINDS = ("elevator", "escalator")  # the order the detail lists them in
Row = dict[str, Any]
LOGGER = logging.getLogger(__name__)


class FeedError(ValueError):
    """A feed could not be fetched or safely converted; retain previous data."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        # OP-103 owns retry timing. Carry the server's delay without sleeping here.
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class RowIssue:
    dataset: str
    row_number: int
    device_id: str | None
    message: str


@dataclass
class RowDiagnostics:
    issues: list[RowIssue] = field(default_factory=list)
    cleared_device_ids: set[str] = field(default_factory=set)
    valid_outage_rows: int = 0

    def skip(self, dataset: str, index: int, row: Row, error: ValueError) -> None:
        device_id = str(row["device_id"]).strip() if row.get("device_id") else None
        issue = RowIssue(dataset, index, device_id, str(error))
        self.issues.append(issue)
        LOGGER.warning(
            "Skipping %s row %s (device %s): %s",
            dataset,
            index,
            device_id or "unknown",
            error,
        )


@dataclass(frozen=True)
class StationDevice:
    """One City elevator or escalator, as the daily device list describes it."""

    device_id: str
    kind: str  # "elevator" or "escalator"
    location: str  # the City's location label, e.g. "North Entrance"


# Station key (``_station_key``) -> that station's devices, in device-id order.
StationDevices = Mapping[str, tuple[StationDevice, ...]]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Fetch time must include a timezone")
    return value.astimezone(UTC)


def _retry_after(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        if value.strip().isdigit():
            return float(value)
        retry_at = parsedate_to_datetime(value)
        return max(0.0, (_utc(retry_at) - _utc(now)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


class DatasetReader(Protocol):
    """Local injectable HTTP dependency for offline tests, not SourceAdapter."""

    def read(self, dataset: str) -> list[Row]: ...


class SocrataReader:
    """Read complete datasets once per call; the scheduler owns retries."""

    def __init__(
        self,
        app_token: str | None = None,
        *,
        timeout_s: float = 10,
        page_size: int = 1000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if timeout_s <= 0 or page_size <= 0:
            raise ValueError("Invalid HTTP timeout or page size")
        self.app_token = app_token
        self.timeout_s = timeout_s
        self.page_size = page_size
        self.clock = clock

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
        """Make one bounded request and pass retry guidance to OP-103."""
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                return _rows(json.load(response))
        except HTTPError as exc:
            delay = (
                _retry_after(exc.headers.get("Retry-After"), self.clock())
                if exc.code == 429 and exc.headers
                else None
            )
            raise FeedError(f"LRT feed HTTP {exc.code}", retry_after_s=delay) from exc
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            raise FeedError("LRT feed unavailable") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FeedError("LRT feed returned invalid JSON") from exc


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


def _valid_lookup_rows(
    dataset: str, rows: list[Row], diagnostics: RowDiagnostics
) -> list[Row]:
    valid = []
    for index, row in enumerate(_rows(rows), start=1):
        try:
            if dataset == DEVICES:
                _required(row, "device_id")
            else:
                _required(row, "lrt_stop_description")
                int(_required(row, "lrt_stop_number"))
                _coordinates(row)
            valid.append(row)
        except ValueError as exc:
            diagnostics.skip(dataset, index, row, exc)
    if not valid:
        raise FeedError(f"LRT lookup {dataset} has no valid rows")
    return valid


def _device_order(device: StationDevice) -> tuple[int, int, str]:
    """Numeric device ids in number order (9 before 10), then any others."""
    digits = device.device_id.isdigit()
    return (
        0 if digits else 1,
        int(device.device_id) if digits else 0,
        device.device_id,
    )


def _label_key(label: str) -> str:
    return " ".join(label.split()).casefold()


def group_station_devices(devices: list[Row]) -> dict[str, tuple[StationDevice, ...]]:
    """Group the City device list by station for the OP-313 detail text.

    The list's own status field is ignored: only the outage feed says what is out.
    A station with a device row that lacks its type or location label is left out,
    so its outages keep the plain detail instead of an incomplete answer.
    """
    groups: dict[str, list[StationDevice]] = {}
    incomplete: set[str] = set()
    seen: set[str] = set()
    for row in devices:
        device_id = str(row.get("device_id") or "").strip()
        station = str(row.get("lrt_station_name") or "").strip()
        if not device_id or not station:
            LOGGER.warning("LRT device %s has no station; not listed", device_id or "?")
            continue
        key = _station_key(station)
        kind = str(row.get("device_type") or "").strip().casefold()
        location = " ".join(str(row.get("lrt_device_location") or "").split())
        if kind not in DEVICE_KINDS or not location:
            incomplete.add(key)
            LOGGER.warning(
                "LRT device %s at %s lacks a type or location; the station's "
                "other devices are not listed",
                device_id,
                station,
            )
            continue
        if device_id not in seen:
            seen.add(device_id)
            groups.setdefault(key, []).append(StationDevice(device_id, kind, location))
    return {
        key: tuple(sorted(items, key=_device_order))
        for key, items in groups.items()
        if key not in incomplete
    }


def _usable_devices(devices: Any, diagnostics: RowDiagnostics) -> list[Row]:
    """The valid device rows, or none when the list is missing or invalid.

    The device list only fills missing outage fields and names the station's other
    devices, so a bad list never stops the outages (OP-313).
    """
    if not devices:
        return []
    try:
        return _valid_lookup_rows(DEVICES, devices, diagnostics)
    except FeedError as exc:
        LOGGER.warning("LRT device list unusable; plain outage details: %s", exc)
        return []


def _out_of_service_ids(outages: list[Row]) -> set[str] | None:
    """Device ids the outage feed lists as not in service.

    Any status other than "0" counts, so a row that is invalid for another reason
    still keeps its device from being called in service. ``None`` when a listed
    row has no device id: then nobody can tell which devices are in service.
    """
    out: set[str] = set()
    for row in outages:
        if str(row.get("device_op_status_code") or "").strip() == "0":
            continue
        device_id = str(row.get("device_id") or "").strip()
        if not device_id:
            return None
        out.add(device_id)
    return out


def _join(words: Sequence[str]) -> str:
    """Join words as "A", "A and B" or "A, B and C"."""
    if len(words) == 1:
        return words[0]
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _noun(kind: str, count: int) -> str:
    return kind if count == 1 else f"{kind}s"


def _device_list(devices: Sequence[StationDevice], shown: int) -> str:
    """List devices as "elevators at A and B; escalators at C".

    At most ``shown`` location labels are named. Labels hidden to fit the detail
    are counted ("and 3 more") from the end, so elevators keep their names longest.
    """
    parts = []
    for kind in DEVICE_KINDS:
        of_kind = [device for device in devices if device.kind == kind]
        if not of_kind:
            continue
        labels = list({_label_key(d.location): d.location for d in of_kind}.values())
        visible = labels[: max(shown, 0)]
        shown -= len(visible)
        hidden = len(labels) - len(visible)
        noun = _noun(kind, len(of_kind))
        if not visible:
            parts.append(f"{len(of_kind)} {noun}")
        else:
            more = [f"{hidden} more"] if hidden else []
            parts.append(f"{noun} at {_join([*visible, *more])}")
    return "; ".join(parts)


def _fit(text: str, limit: int = DETAIL_LIMIT) -> str:
    """Cut an overlong text at a word boundary, never inside a word."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if text[limit - 1] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"


def station_detail(
    station_name: str,
    location: str,
    device_type: str,
    device_id: str,
    devices: Sequence[StationDevice] | None,
    out_ids: set[str] | None,
    limit: int = DETAIL_LIMIT,
) -> str:
    """The outage's ``detail``: where it is, then the station's in-service devices.

    "Use the <label> <kind> instead" names only an in-service device with the same
    location label; an escalator is never suggested in place of an elevator. With
    no station devices or unknown statuses it is the plain "<station>, <location>".
    """
    base = f"{station_name}, {location}"
    if not devices or out_ids is None:
        return _fit(base, limit)
    in_service = [
        device
        for device in devices
        if device.device_id != device_id and device.device_id not in out_ids
    ]
    if not in_service:
        return _fit(
            f"{base}. No elevators or escalators at {station_name} are in service.",
            limit,
        )
    usable = ("elevator",) if device_type == "elevator" else DEVICE_KINDS
    same = [
        device
        for device in in_service
        if device.kind in usable and _label_key(device.location) == _label_key(location)
    ]
    text = f"{base}."
    if same:
        kinds = [kind for kind in DEVICE_KINDS if any(d.kind == kind for d in same)]
        text += f" Use the {same[0].location} {' or '.join(kinds)} instead."
    elif device_type == "elevator":
        text += f" No other elevator at {location} is in service."
    rest = [device for device in in_service if device not in same]
    if rest:
        prefix = "Also in service at" if same else "In service at"
        # Name as many labels as fit; drop the list only if not even counts fit.
        for shown in range(len(rest), -1, -1):
            listed = f"{text} {prefix} {station_name}: {_device_list(rest, shown)}."
            if len(listed) <= limit:
                return listed
    return _fit(text, limit)


def normalize_outages(
    outages: list[Row],
    devices: list[Row],
    stations: list[Row],
    *,
    observed_at: datetime,
    source_timezone: tzinfo,
    lifetime: timedelta = DEFAULT_LIFETIME,
    diagnostics: RowDiagnostics | None = None,
    station_devices: StationDevices | None = None,
) -> list[Row]:
    """Convert valid rows and report bad rows without discarding other outages.

    Only the outage feed can create an obstruction. Station coordinates are a
    representative station point, not a verified elevator/entrance position.
    ``station_devices`` is the device list grouped by station (the adapter keeps
    it between daily refreshes); without it, ``devices`` is grouped here. An
    empty or invalid device list still converts outages, with plain details.
    """
    if lifetime <= timedelta(0):
        raise ValueError("Observation lifetime must be positive")
    observed_at = _utc(observed_at)
    diagnostics = diagnostics if diagnostics is not None else RowDiagnostics()
    _rows(outages)
    _rows(stations)
    if not stations:
        raise FeedError("LRT station lookup must not be empty")
    devices = _usable_devices(devices, diagnostics)
    stations = _valid_lookup_rows(STATIONS, stations, diagnostics)
    if station_devices is None:
        station_devices = group_station_devices(devices)
    out_ids = _out_of_service_ids(outages)
    device_by_id = {_required(row, "device_id"): row for row in devices}
    station_by_name: dict[str, Row] = {}
    # Multiple stops can represent a station. Prefer a Station over a Stop
    # (Churchill has both), then use the lowest stop ID for a stable City point.
    ordered_stations = sorted(
        stations,
        key=lambda row: (
            not _required(row, "lrt_stop_description").casefold().endswith("station"),
            int(_required(row, "lrt_stop_number")),
        ),
    )
    for station in ordered_stations:
        key = _station_key(_required(station, "lrt_stop_description"))
        station_by_name.setdefault(key, station)

    result: list[Row] = []
    seen: set[str] = set()
    for index, outage in enumerate(outages, start=1):
        try:
            item = _normalize_outage(
                outage,
                device_by_id,
                station_by_name,
                source_timezone,
                observed_at,
                lifetime,
                station_devices,
                out_ids,
            )
            device_id = _required(outage, "device_id")
            if device_id in seen:
                raise FeedError(f"Duplicate LRT outage for device {device_id}")
            seen.add(device_id)
            diagnostics.valid_outage_rows += 1
            if item is None:
                diagnostics.cleared_device_ids.add(device_id)
            else:
                result.append(item)
        except FeedError as exc:
            diagnostics.skip(OUTAGES, index, outage, exc)
    if outages and diagnostics.valid_outage_rows == 0:
        raise FeedError(
            f"All {len(outages)} outage rows were invalid; keep last-good data"
        )
    return sorted(result, key=lambda row: row["source_ref"])


def _normalize_outage(
    outage: Row,
    device_by_id: dict[str, Row],
    station_by_name: dict[str, Row],
    source_timezone: tzinfo,
    observed_at: datetime,
    lifetime: timedelta,
    station_devices: StationDevices,
    out_ids: set[str] | None,
) -> Row | None:
    """Convert one outage; callers isolate and log any row validation error."""
    device_id = _required(outage, "device_id")
    code = _required(outage, "device_op_status_code")
    # City status 0 means in service; only status 1 represents an outage.
    if code == "0":
        return None
    if code != "1":
        raise FeedError(f"Unknown LRT status code for device {device_id}")
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
    if since > observed_at:
        raise FeedError(f"Outage starts after observation for device {device_id}")
    location = _required(row, "lrt_device_location")
    detail = station_detail(
        station_name,
        location,
        device_type,
        device_id,
        station_devices.get(_station_key(station_name)),
        out_ids,
    )
    # A successful fetch confirms that this outage is still listed. Keep the
    # source's start time, but measure freshness from our own fetch time.
    return {
        "id": f"{SOURCE}:{device_id}",
        "source": SOURCE,
        "source_ref": device_id,
        "category": "indoor",
        "kind": f"{device_type}_out",
        "effect": "blocked" if device_type == "elevator" else "caution",
        "title": f"{device_type.capitalize()} out of service",
        "detail": detail,
        "geometry": {"type": "Point", "coordinates": _coordinates(station)},
        "confidence": "high",
        "starts_at": _iso(since),
        "ends_at": None,
        "observed_at": _iso(observed_at),
        "expires_at": _iso(observed_at + lifetime),
        "url": f"https://data.edmonton.ca/d/{OUTAGES}",
        "attribution": "City of Edmonton Open Data",
    }


class LrtDevicesAdapter:
    """Provisional polling helper until OP-101/103 supply the shared interface.

    Fetch errors propagate, so the store can retain its last snapshot. Successful
    empty outage lists are valid. Invalid rows keep their previous records with
    unchanged timestamps, while valid rows continue flowing. OP-103 owns cadence,
    retries and backoff; every call polls the outage feed once.
    """

    source = SOURCE
    cadence_s = int(POLL_INTERVAL.total_seconds())

    def __init__(self, reader: DatasetReader, *, source_timezone: tzinfo) -> None:
        self.reader = reader
        self.source_timezone = source_timezone
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self._lookup_success: dict[str, datetime] = {}
        self._devices: list[Row] = []
        # OP-313: the device list grouped by station, kept with the last good list.
        self._station_devices: dict[str, tuple[StationDevice, ...]] = {}
        self._stations: list[Row] = []
        self._last_good: list[Row] = []

    @property
    def last_good(self) -> list[Row]:
        """Return a copy so callers cannot change the saved snapshot."""
        return deepcopy(self._last_good)

    def _lookup(
        self, dataset: str, now: datetime, diagnostics: RowDiagnostics, notes: list[str]
    ) -> list[Row]:
        cached = self._devices if dataset == DEVICES else self._stations
        refreshed_at = self._lookup_success.get(dataset)
        if refreshed_at is not None and now - refreshed_at < LOOKUP_INTERVAL:
            return cached
        # A missing or unreadable device list (a replay snapshot without the
        # file, say) must never stop the outages (OP-313); stations are required.
        errors = (OSError, ValueError) if dataset == DEVICES else (FeedError,)
        try:
            rows = _valid_lookup_rows(dataset, self.reader.read(dataset), diagnostics)
        except errors as exc:
            if not cached and dataset != DEVICES:
                raise
            note = (
                f"Using cached {dataset} lookup after refresh failed: {exc}"
                if cached
                else f"No {dataset} device list, so outage details name no other "
                f"devices: {exc}"
            )
            notes.append(note)
            LOGGER.warning(note)
            return cached
        # Cache the validated rows by reference; conversion only reads them.
        if dataset == DEVICES:
            self._devices = rows
            self._station_devices = group_station_devices(rows)
        else:
            self._stations = rows
        self._lookup_success[dataset] = now
        return rows

    def fetch(self, now: datetime | None = None) -> list[Row]:
        """Poll once when the scheduler calls, with daily cached lookup refresh."""
        now = _utc(now or datetime.now(UTC))
        diagnostics = RowDiagnostics()
        notes: list[str] = []
        try:
            devices = self._lookup(DEVICES, now, diagnostics, notes)
            stations = self._lookup(STATIONS, now, diagnostics, notes)
            result = normalize_outages(
                self.reader.read(OUTAGES),
                devices,
                stations,
                observed_at=now,
                source_timezone=self.source_timezone,
                diagnostics=diagnostics,
                station_devices=self._station_devices,
            )
        except FeedError as exc:
            self.last_error = _diagnostic_summary(diagnostics, [*notes, str(exc)])
            raise
        # A bad row must not delete a previously known outage during the store's
        # source reconciliation. Preserve it without making its old data fresh.
        invalid_ids = {
            issue.device_id for issue in diagnostics.issues if issue.dataset == OUTAGES
        }
        current_ids = {item["source_ref"] for item in result}
        for previous in self._last_good:
            device_id = previous["source_ref"]
            if (
                device_id not in current_ids
                and device_id not in diagnostics.cleared_device_ids
                and (device_id in invalid_ids or None in invalid_ids)
            ):
                result.append(previous)
        result.sort(key=lambda row: row["source_ref"])
        self._last_good = result
        self.last_success = now
        self.last_error = _diagnostic_summary(diagnostics, notes)
        return self.last_good


def _diagnostic_summary(diagnostics: RowDiagnostics, notes: list[str]) -> str | None:
    summary = []
    if diagnostics.issues:
        count = len(diagnostics.issues)
        summary.append(f"Skipped {count} invalid {'row' if count == 1 else 'rows'}")
    summary.extend(notes)
    return "; ".join(summary) or None


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
    parser.add_argument("--at", help="Timezone-aware confirmation time for a replay")
    args = parser.parse_args()
    if args.live and args.at:
        parser.error("--at is only allowed with --fixtures")
    observed_at = (
        _utc(datetime.fromisoformat(args.at)) if args.at else datetime.now(UTC)
    )
    source_timezone = ZoneInfo(args.source_timezone)
    diagnostics = RowDiagnostics()
    last_error = None
    if args.fixtures:
        payloads = [
            _rows(json.loads((args.fixtures / name).read_text(encoding="utf-8")))
            for name in ("outages.json", "devices.json", "stations.json")
        ]
        obstructions = normalize_outages(
            *payloads,
            observed_at=observed_at,
            source_timezone=source_timezone,
            diagnostics=diagnostics,
        )
        last_error = _diagnostic_summary(diagnostics, [])
    else:
        reader = SocrataReader(os.environ.get("SOCRATA_APP_TOKEN"))
        adapter = LrtDevicesAdapter(reader, source_timezone=source_timezone)
        obstructions = adapter.fetch(observed_at)
        last_error = adapter.last_error
    print(
        json.dumps(
            {
                "mode": "replay" if args.fixtures else "live",
                "source_timezone_assumption": args.source_timezone,
                "row_warnings": [issue.message for issue in diagnostics.issues],
                "last_error": last_error,
                "obstructions": obstructions,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
