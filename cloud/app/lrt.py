"""OP-311: walk and LRT trips, using only stations whose elevators work.

Behind the feature flag ``LRT_TRIPS`` (off by default). With the flag off, or with
``mode=walk``, ``GET /api/route`` is exactly the walking planner (OP-207). With the
flag on and ``mode=walk_lrt``, the best walk and LRT trip, when there is one and it
is faster than walking, comes first (rank 1) and the slowest walking route is dropped,
so a response still has at most 3 routes, best first.

The data
    ``cloud/data/lrt.json`` is built from the ETS GTFS schedule by
    ``scripts/build_lrt_graph.py`` (``make lrt``) and committed: stations, line
    order, scheduled ride times between stations, hourly headways per day type, and
    for each station the City elevators and escalators that serve it, by device id.

Which stations a profile may use
    Profiles that cannot use stairs (``stairs: null`` in ``config/profiles.json``:
    wheelchair, walker or cane) enter and leave a station only through a working,
    mapped elevator:

    - ``at_grade`` stations (no City elevator and no GTFS elevator pathway: the
      Valley Line stops and street-level Capital and Metro stations) need none;
    - every ``platform`` elevator must be in service, and at least one ``entrance``
      elevator when the station has any;
    - a station whose elevators OpenPath cannot check (none in the device list, or
      no device status at all) is not used;
    - a transfer station must be usable too.

    Live status is the store's ``elevator_out`` and ``escalator_out`` items from the
    City outage feed (``city_lrt_devices``, OP-105). An item belongs to a station by
    its device id (``source_ref``), or, for a device missing from the list, by the
    station name at the start of its ``detail``. Other profiles may use every
    station; escalator outages are listed in the report for everyone.

    Where the walk meets a station: reviewed OP-106 anchor nodes when there are any,
    otherwise the network nodes nearest the station's GTFS location. None of the 32
    City elevators has a reviewed map match yet, so walks end at the station's
    location and the trip says so.

The trip
    walk (the profile's network, hazards included, at most ``MAX_WALK_M``)
    -> station access -> wait -> ride -> [transfer -> wait -> ride] -> station
    access -> walk. Cost is time: walk time as routing computes it (length / speed x
    multiplier), plus the wait, the scheduled ride and fixed allowances.

    The wait is an estimate: half the scheduled headway for the hour the rider
    boards, on the matching day type (weekday, Saturday or Sunday; holidays are not
    modelled). It is always labelled as an estimate, never as a live arrival.

Assumptions (not measured; see docs/OP-311.md)
    ``ACCESS_S``: time from the street to the platform (2 min with an elevator,
    30 s at grade). ``TRANSFER_PENALTY_S``: changing trains at one station, 3 min.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel, Field
from scipy.sparse.csgraph import dijkstra

from .lrt_gtfs import DAY_TYPES, FORMAT_VERSION, haversine_m, station_key
from .profiles import Profile
from .report import (
    ItemIndex,
    ReportEntry,
    ReportRecord,
    RouteReport,
    Status,
    build_report,
    group,
)
from .routing import (
    EdgeHazard,
    HazardMap,
    Route,
    Router,
    Step,
    _polyline,
    _steps,
    profile_weights,
)
from .schemas import Confidence, Kind

log = logging.getLogger(__name__)

DEFAULT_LRT_PATH = Path(__file__).resolve().parents[1] / "data" / "lrt.json"
DEVICE_SOURCE = "city_lrt_devices"
EDMONTON = ZoneInfo("America/Edmonton")

MAX_WALK_M = 1200.0  # walking cost (metres x multiplier) to or from a station
ENTRY_RADIUS_M = 200.0  # network nodes this close to a station point can be its entry
ENTRY_CANDIDATES = 10
SERVICE_DAY_START_HOUR = 3  # GTFS: trips before 03:00 belong to the previous day
ACCESS_S = {"elevator": 120.0, "at_grade": 30.0}  # assumption: street to platform
TRANSFER_PENALTY_S = 180.0  # assumption: changing trains at one station
FEED_GRACE_DAYS = 0  # warn as soon as the schedule's last day has passed

WAIT_NOTE = (
    "Estimate: half the scheduled time between trains at this hour, from the ETS "
    "schedule. Not a live arrival time."
)
WAIT_WARNING = (
    "Wait times are estimates from the ETS schedule (half the time between trains "
    "at this hour), not live arrivals."
)
UNMAPPED_ENTRY_WARNING = (
    "Station elevators are not yet matched to the map, so the walk ends at the "
    "station's location; follow the station signs to an elevator."
)
MAPS_NOTE = (
    "Google Maps and Apple Maps plan their own transit trip and may not use the same "
    "stations or elevators."
)


def enabled(raw: str | None) -> bool:
    """``LRT_TRIPS``: ``true``, ``1``, ``on`` or ``yes`` turns walk and LRT trips on."""
    return (raw or "").strip().lower() in {"1", "true", "on", "yes"}


# ------------------------------------------------------------------ the schedule


@dataclass(frozen=True)
class Device:
    device_id: str
    kind: Literal["elevator", "escalator"]
    location: str
    role: Literal["platform", "entrance"] | None = None  # elevators only


@dataclass(frozen=True)
class LrtStation:
    id: str
    name: str  # "Churchill"
    title: str  # "Churchill Station"
    lat: float
    lng: float
    access: Literal["elevator", "at_grade"]
    devices: tuple[Device, ...] = ()
    aliases: tuple[str, ...] = ()
    entry_osm_nodes: tuple[int, ...] = ()

    @property
    def elevators(self) -> tuple[Device, ...]:
        return tuple(d for d in self.devices if d.kind == "elevator")

    @property
    def escalators(self) -> tuple[Device, ...]:
        return tuple(d for d in self.devices if d.kind == "escalator")


@dataclass(frozen=True)
class LrtLine:
    id: str
    name: str  # "Capital Line"
    color: str | None
    stations: tuple[str, ...]  # direction 0 order
    ride_s: tuple[int, ...]  # between consecutive stations
    headsigns: Mapping[str, str]  # "0" / "1" -> where the train is going
    headway_s: Mapping[str, tuple[int | None, ...]]  # day type -> 24 hours

    def position(self, station_id: str) -> int | None:
        try:
            return self.stations.index(station_id)
        except ValueError:
            return None

    def headway(self, day_type: str, hour: int) -> int | None:
        hours = self.headway_s.get(day_type)
        return hours[hour % 24] if hours else None


@dataclass(frozen=True)
class LrtTransfer:
    a: str
    b: str
    walk_s: float


@dataclass
class LrtSchedule:
    """``cloud/data/lrt.json`` loaded, with the lookups the planner needs."""

    stations: dict[str, LrtStation]
    lines: tuple[LrtLine, ...]
    transfers: tuple[LrtTransfer, ...]
    source: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.device_index: dict[str, tuple[str, Device]] = {}
        for station in self.stations.values():
            for device in station.devices:
                self.device_index[device.device_id] = (station.id, device)
        # Name key -> station; a station called "Station" wins over a "Stop" of the
        # same name (Churchill Station Underground over the Valley Line's Churchill Stop).
        self.by_key: dict[str, str] = {}
        for station in sorted(
            self.stations.values(),
            key=lambda s: (not s.title.endswith("Station"), not s.devices, s.id),
        ):
            for name in (station.name, station.title, *station.aliases):
                self.by_key.setdefault(station_key(name), station.id)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_LRT_PATH) -> LrtSchedule:
        return cls.from_document(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> LrtSchedule:
        if doc.get("format_version") != FORMAT_VERSION:
            raise ValueError(
                f"lrt.json is format {doc.get('format_version')}, this code reads "
                f"{FORMAT_VERSION}; rebuild it with scripts/build_lrt_graph.py"
            )
        stations: dict[str, LrtStation] = {}
        for s in doc["stations"]:
            devices = [
                Device(
                    str(e["device_id"]), "elevator", e.get("location", ""), e["role"]
                )
                for e in s.get("elevators", [])
            ] + [
                Device(str(e["device_id"]), "escalator", e.get("location", ""))
                for e in s.get("escalators", [])
            ]
            stations[s["id"]] = LrtStation(
                id=s["id"],
                name=s["name"],
                title=s.get("title") or s["name"],
                lat=float(s["lat"]),
                lng=float(s["lng"]),
                access=s["access"],
                devices=tuple(devices),
                aliases=tuple(s.get("aliases", [])),
                entry_osm_nodes=tuple(int(n) for n in s.get("entry_osm_nodes", [])),
            )
        lines = []
        for line in doc["lines"]:
            if len(line["ride_s"]) != len(line["stations"]) - 1:
                raise ValueError(f"{line['name']}: ride_s does not match its stations")
            unknown = set(line["stations"]) - stations.keys()
            if unknown:
                raise ValueError(f"{line['name']}: unknown stations {sorted(unknown)}")
            lines.append(
                LrtLine(
                    id=line["id"],
                    name=line["name"],
                    color=line.get("color"),
                    stations=tuple(line["stations"]),
                    ride_s=tuple(int(r) for r in line["ride_s"]),
                    headsigns=dict(line.get("headsigns", {})),
                    headway_s={
                        t: tuple(h)
                        for t, h in line.get("headway_s", {}).items()
                        if t in DAY_TYPES and len(h) == 24
                    },
                )
            )
        transfers = tuple(
            LrtTransfer(t["from"], t["to"], float(t["walk_s"]))
            for t in doc.get("transfers", [])
            if t["from"] in stations and t["to"] in stations
        )
        return cls(stations, tuple(lines), transfers, dict(doc.get("source", {})))

    def feed_end(self) -> date | None:
        raw = self.source.get("feed_end_date")
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])) if raw else None
        except (TypeError, ValueError):
            return None

    @property
    def attribution(self) -> str:
        return str(
            self.source.get("attribution")
            or "Schedule: Edmonton Transit Service, City of Edmonton Open Data"
        )


# --------------------------------------------------------------- device status


@dataclass(frozen=True)
class DeviceOutage:
    """One live elevator or escalator outage, tied to a station."""

    record: ReportRecord
    station_id: str
    device: Device | None  # None when the device is not in the station's list

    @property
    def is_elevator(self) -> bool:
        return self.record.kind == Kind.ELEVATOR_OUT


def device_records(indexes: Iterable[ItemIndex]) -> list[ReportRecord]:
    """The City elevator and escalator items in the report indexes (the store's items)."""
    found: dict[str, ReportRecord] = {}
    for index in indexes:
        for record in index.records:
            if record.source == DEVICE_SOURCE and record.kind in (
                Kind.ELEVATOR_OUT,
                Kind.ESCALATOR_OUT,
            ):
                found.setdefault(record.id, record)
    return list(found.values())


def outage_place(detail: str | None) -> str:
    """The "<station>, <location>" lead of an outage's ``detail``.

    OP-313 adds sentences about the station's other in-service devices
    ("Central, West Platform. In service at Central: ..."); a short reason needs
    only the first one.
    """
    place = (detail or "").split(". ", 1)[0].strip().rstrip(".")
    return place or "location unknown"


def match_outages(
    schedule: LrtSchedule, records: Iterable[ReportRecord]
) -> list[DeviceOutage]:
    """Tie each outage to its station: by device id, else by the name in ``detail``."""
    outages = []
    for record in records:
        if record.source != DEVICE_SOURCE or record.kind not in (
            Kind.ELEVATOR_OUT,
            Kind.ESCALATOR_OUT,
        ):
            continue
        device_id = record.id.split(":", 1)[-1]
        kind = "elevator" if record.kind == Kind.ELEVATOR_OUT else "escalator"
        known = schedule.device_index.get(device_id)
        if known is not None and known[1].kind == kind:
            outages.append(DeviceOutage(record, known[0], known[1]))
            continue
        # The adapter writes "<City station name>, <device location>".
        name = (record.detail or "").split(",", 1)[0]
        station_id = schedule.by_key.get(station_key(name)) if name else None
        if station_id is None:
            log.warning("LRT %s matches no station; ignored for trips", record.id)
            continue
        outages.append(DeviceOutage(record, station_id, None))
    return outages


@dataclass(frozen=True)
class StationAccess:
    """Whether one profile may use a station right now, and why."""

    station: LrtStation
    usable: bool
    reason: str | None  # why it is not usable
    note: str | None  # how to use it
    outages: tuple[DeviceOutage, ...] = ()


def station_access(
    station: LrtStation,
    outages: Sequence[DeviceOutage],
    *,
    step_free: bool,
    status_known: bool,
) -> StationAccess:
    """Apply the elevator rule in the module docstring to one station."""
    out_ids = {o.device.device_id for o in outages if o.device and o.is_elevator}
    escalators_out = [o for o in outages if not o.is_elevator]
    escalator_note = (
        "Escalator out of service: "
        + ", ".join(
            o.device.location if o.device else "see report" for o in escalators_out
        )
        + "."
        if escalators_out
        else None
    )
    outages = tuple(outages)
    if not step_free:
        return StationAccess(station, True, None, escalator_note, outages)

    def closed(reason: str) -> StationAccess:
        return StationAccess(station, False, reason, None, outages)

    if station.access == "at_grade":
        return StationAccess(
            station,
            True,
            None,
            "No elevator is listed for this station: its platform is treated as "
            "reachable without stairs (from the ETS schedule and the City device list).",
            outages,
        )
    elevators = station.elevators
    if not elevators:
        return closed(
            "it has no mapped elevator, so OpenPath cannot check that one works"
        )
    if not status_known:
        return closed("elevator status is unavailable right now")
    unknown = [o for o in outages if o.is_elevator and o.device is None]
    if unknown:
        return closed(
            f"an elevator is out of service ({outage_place(unknown[0].record.detail)}) "
            "and OpenPath cannot tell which one"
        )
    platform_out = [
        e for e in elevators if e.role == "platform" and e.device_id in out_ids
    ]
    if platform_out:
        return closed(f"the {platform_out[0].location} elevator is out of service")
    entrances = [e for e in elevators if e.role == "entrance"]
    working = [e for e in entrances if e.device_id not in out_ids]
    if entrances and not working:
        return closed("every entrance elevator is out of service")
    if entrances and len(working) < len(entrances):
        note = (
            "Use an entrance with a working elevator: "
            + ", ".join(e.location for e in working)
            + "."
        )
    else:
        note = (
            "Elevators in service at the last check: "
            + ", ".join(e.location for e in elevators)
            + "."
        )
    return StationAccess(station, True, None, note, outages)


# ---------------------------------------------------------------- trip pieces


@dataclass(frozen=True)
class Ride:
    """One ride between two stations, on any of ``lines`` (they share the track)."""

    lines: tuple[LrtLine, ...]
    board: str
    alight: str
    ride_s: float
    headway_s: float  # combined, for every line that serves this ride
    wait_s: float  # the estimate: half the headway
    stops: int
    headsigns: tuple[str, ...]


@dataclass(frozen=True)
class Leg:
    kind: Literal["walk", "ride", "transfer"]
    from_name: str
    to_name: str
    distance_m: float
    duration_s: float
    coordinates: list[list[float]]
    ride: Ride | None = None
    route: Route | None = None  # walk legs: the walking route, for its report


@dataclass(frozen=True)
class LrtTrip:
    legs: list[Leg]
    route: Route  # the whole trip in routing's shape (for steps, ETA, hazards)
    stations: list[StationAccess]  # entry, transfer stations, exit
    total_s: float
    walk_m: float
    warnings: list[str]
    reason: str


@dataclass
class LrtPlan:
    trip: LrtTrip | None
    warnings: list[str] = field(default_factory=list)
    # Stations within walking reach that this profile may not use now, with why.
    skipped: list[StationAccess] = field(default_factory=list)


@dataclass(frozen=True)
class _Option:
    total_s: float
    entry: str
    exit: str
    rides: tuple[Ride, ...]
    transfer: LrtTransfer | None
    walk_in_s: float
    walk_out_s: float


# ------------------------------------------------------------------- planner


class LrtPlanner:
    """Plans walk and LRT trips on the walking network and the LRT schedule.

    Built once at startup (``load_planner``). Station entry nodes are found then;
    a request runs two bounded Dijkstra searches (from the start and from the end)
    on the profile's weights, so it adds tens of milliseconds to a route request.
    """

    def __init__(self, router: Router, schedule: LrtSchedule) -> None:
        self.router = router
        self.network = router.network
        self.schedule = schedule
        net = self.network
        self.entries: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.mapped_entries: set[str] = set()  # joined at reviewed OP-106 nodes
        osm_index: dict[int, int] = {}
        wanted = [n for s in schedule.stations.values() for n in s.entry_osm_nodes]
        if wanted:
            for i in np.flatnonzero(np.isin(net.node_osmid, wanted)):
                osm_index[int(net.node_osmid[i])] = int(i)
        for station in schedule.stations.values():
            mapped = [osm_index[n] for n in station.entry_osm_nodes if n in osm_index]
            if mapped:
                self.mapped_entries.add(station.id)
                nodes = np.asarray(mapped, dtype=np.int64)
                metres = np.array(
                    [
                        haversine_m(
                            station.lat,
                            station.lng,
                            float(net.node_lat[n]),
                            float(net.node_lng[n]),
                        )
                        for n in mapped
                    ]
                )
            else:
                nodes, metres = net.nearest_nodes(
                    station.lat, station.lng, ENTRY_CANDIDATES
                )
                keep = metres <= ENTRY_RADIUS_M
                nodes, metres = nodes[keep], metres[keep]
            if len(nodes):
                self.entries[station.id] = (nodes.astype(np.int64), metres)
            else:
                log.warning("LRT %s has no walking path nearby", station.title)

    # -- public --------------------------------------------------------------

    def plan(
        self,
        *,
        origin_node: int,
        destination_node: int,
        profile: Profile,
        hazards: HazardMap,
        outages: Sequence[DeviceOutage] | None,
        now: datetime,
        walk_eta_min: float | None,
    ) -> LrtPlan:
        """The best walk and LRT trip, or None with the reasons in ``warnings``.

        ``outages`` None means device status could not be read at all.
        """
        step_free = profile.stairs is None
        status_known = outages is not None
        by_station: dict[str, list[DeviceOutage]] = {}
        for outage in outages or ():
            by_station.setdefault(outage.station_id, []).append(outage)
        access = {
            sid: station_access(
                s,
                by_station.get(sid, []),
                step_free=step_free,
                status_known=status_known,
            )
            for sid, s in self.schedule.stations.items()
        }
        plan = LrtPlan(trip=None)
        end = self.schedule.feed_end()
        local = now.astimezone(EDMONTON)
        if end is not None and local.date() > end + timedelta(days=FEED_GRACE_DAYS):
            plan.warnings.append(
                f"The LRT schedule OpenPath holds ended on {end.isoformat()}; times may "
                "be wrong until it is rebuilt."
            )
        if not status_known and step_free:
            plan.warnings.append(
                "LRT elevator status is unavailable, so only stations without "
                "elevators are used."
            )
        if origin_node == destination_node:
            return plan

        weights = profile_weights(self.network, profile, hazards)
        matrix = self.network.length_matrix(weights)
        from_start, pred_start = dijkstra(
            matrix,
            directed=True,
            indices=origin_node,
            limit=MAX_WALK_M,
            return_predecessors=True,
        )
        from_end, pred_end = dijkstra(
            matrix,
            directed=True,
            indices=destination_node,
            limit=MAX_WALK_M,
            return_predecessors=True,
        )
        near_start = self._reach(from_start)
        near_end = self._reach(from_end)
        if not near_start or not near_end:
            plan.warnings.append(
                f"No LRT station is within {MAX_WALK_M / 1000:g} km of the "
                f"{'start' if not near_start else 'end'} on paths this profile can use."
            )
            return plan
        for sid in sorted({*near_start, *near_end}):
            if not access[sid].usable:
                plan.skipped.append(access[sid])
                plan.warnings.append(
                    f"{access[sid].station.title} is not used for the "
                    f"{profile.label} profile: {access[sid].reason}."
                )

        speed = profile.speed_m_s
        best: _Option | None = None
        had_service = False
        for entry, (_, in_cost) in near_start.items():
            if not access[entry].usable:
                continue
            walk_in_s = in_cost / speed
            for exit_, (_, out_cost) in near_end.items():
                if exit_ == entry or not access[exit_].usable:
                    continue
                walk_out_s = out_cost / speed
                board_at = local + timedelta(seconds=walk_in_s + self._access_s(entry))
                for rides, transfer in self._ride_options(
                    entry, exit_, board_at, access
                ):
                    had_service = True
                    total = (
                        walk_in_s
                        + self._access_s(entry)
                        + sum(r.wait_s + r.ride_s for r in rides)
                        + (self._transfer_s(transfer) if len(rides) > 1 else 0.0)
                        + self._access_s(exit_)
                        + walk_out_s
                    )
                    if best is None or total < best.total_s:
                        best = _Option(
                            total, entry, exit_, rides, transfer, walk_in_s, walk_out_s
                        )
        if best is None:
            if not had_service and any(a.usable for a in access.values()):
                plan.warnings.append(
                    "No LRT trip fits: no train serves the usable stations near "
                    "these points at this hour in the ETS schedule."
                )
            return plan
        if walk_eta_min is not None and best.total_s / 60 >= walk_eta_min:
            plan.warnings.append(
                "Walking is faster than any walk and LRT trip between these points."
            )
            return plan
        plan.trip = self._describe(
            best,
            access,
            weights,
            hazards,
            profile,
            (origin_node, pred_start, near_start),
            (destination_node, pred_end, near_end),
            walk_eta_min,
        )
        return plan

    # -- search pieces -------------------------------------------------------

    def _reach(self, cost: np.ndarray) -> dict[str, tuple[int, float]]:
        """Station -> (best entry node, walking cost to the station point)."""
        reach: dict[str, tuple[int, float]] = {}
        for sid, (nodes, metres) in self.entries.items():
            total = cost[nodes] + metres
            i = int(np.argmin(total))
            if math.isfinite(float(total[i])):
                reach[sid] = (int(nodes[i]), float(total[i]))
        return reach

    def _access_s(self, station_id: str) -> float:
        return ACCESS_S[self.schedule.stations[station_id].access]

    @staticmethod
    def _transfer_s(transfer: LrtTransfer | None) -> float:
        return TRANSFER_PENALTY_S if transfer is None else transfer.walk_s

    def _direct(self, board: str, alight: str, at: datetime) -> Ride | None:
        """A ride with no change of train, on any line serving both stations."""
        day_type, hour = _service_hour(at)
        serving = []
        for line in self.schedule.lines:
            i, j = line.position(board), line.position(alight)
            if i is None or j is None or i == j:
                continue
            headway = line.headway(day_type, hour)
            if headway is None:
                continue  # no train on this line at this hour
            direction = "0" if i < j else "1"
            ride = float(sum(line.ride_s[min(i, j) : max(i, j)]))
            serving.append((line, ride, headway, abs(j - i), direction))
        if not serving:
            return None
        # Trains on any of these lines will do, so their frequencies add up.
        headway = 1.0 / sum(1.0 / h for _, _, h, _, _ in serving)
        return Ride(
            lines=tuple(s[0] for s in serving),
            board=board,
            alight=alight,
            ride_s=min(s[1] for s in serving),
            headway_s=headway,
            wait_s=headway / 2,
            stops=min(s[3] for s in serving),
            headsigns=tuple(
                s[0].headsigns.get(s[4], self.schedule.stations[alight].name)
                for s in serving
            ),
        )

    def _ride_options(
        self,
        entry: str,
        exit_: str,
        at: datetime,
        access: Mapping[str, StationAccess],
    ) -> Iterable[tuple[tuple[Ride, ...], LrtTransfer | None]]:
        """A direct ride when one line serves both stations, else one transfer."""
        direct = self._direct(entry, exit_, at)
        if direct is not None:
            yield (direct,), None
            return
        if self._share_a_line(entry, exit_):
            return  # the line exists but has no train at this hour
        points: list[tuple[str, str, LrtTransfer | None]] = [
            (sid, sid, None) for sid in self.schedule.stations
        ]
        for t in self.schedule.transfers:
            points += [(t.a, t.b, t), (t.b, t.a, t)]
        for first_at, then_from, transfer in points:
            if first_at in (entry, exit_) or then_from in (entry, exit_):
                continue
            if not (access[first_at].usable and access[then_from].usable):
                continue  # step-free profiles change only at usable stations
            first = self._direct(entry, first_at, at)
            if first is None:
                continue
            arrive = at + timedelta(
                seconds=first.wait_s + first.ride_s + self._transfer_s(transfer)
            )
            second = self._direct(then_from, exit_, arrive)
            if second is None or {l.id for l in first.lines} & {
                l.id for l in second.lines
            }:
                continue
            yield (first, second), transfer

    def _share_a_line(self, a: str, b: str) -> bool:
        return any(
            line.position(a) is not None and line.position(b) is not None
            for line in self.schedule.lines
        )

    # -- the description -----------------------------------------------------

    def _walk(
        self,
        source: int,
        predecessors: np.ndarray,
        target: int,
        weights: np.ndarray,
        hazards: HazardMap,
        speed: float,
        reverse: bool,
    ) -> Route:
        """The walking path from ``source`` to ``target`` (reversed for the last leg)."""
        path = [target]
        while path[-1] != source:
            previous = int(predecessors[path[-1]])
            if previous < 0:  # cannot happen for a node the search reached
                raise RuntimeError(f"no walking path from node {source} to {target}")
            path.append(previous)
        path.reverse()  # source ... target
        if reverse:
            path.reverse()  # target ... source: from the exit station to the end
        edges = self.router._edges_of(path, weights)
        net = self.network
        coords = _polyline(net, path, edges) or [
            [
                round(float(net.node_lng[path[0]]), 6),
                round(float(net.node_lat[path[0]]), 6),
            ]
        ]
        on_route: list[EdgeHazard] = []
        seen: set[str] = set()
        for edge in edges:
            for hazard in hazards.get(edge, ()):
                if hazard.item_id not in seen:
                    seen.add(hazard.item_id)
                    on_route.append(hazard)
        low = sum(h.confidence == Confidence.LOW for h in on_route)
        warnings = (
            [f"Low-confidence hazard on {low} part{'s' if low != 1 else ''} of a walk."]
            if low
            else []
        )
        distance = float(net.edge_length_m[list(edges)].sum()) if edges else 0.0
        eta = float(weights[list(edges)].sum() / speed / 60.0) if edges else 0.0
        return Route(
            tuple(path),
            tuple(edges),
            round(distance, 1),
            round(eta, 1),
            coords,
            _steps(net, edges),
            on_route,
            [],
            warnings,
            None,
        )

    def _describe(
        self,
        option: _Option,
        access: Mapping[str, StationAccess],
        weights: np.ndarray,
        hazards: HazardMap,
        profile: Profile,
        start: tuple[int, np.ndarray, dict[str, tuple[int, float]]],
        end: tuple[int, np.ndarray, dict[str, tuple[int, float]]],
        walk_eta_min: float | None,
    ) -> LrtTrip:
        stations = self.schedule.stations
        entry, exit_ = stations[option.entry], stations[option.exit]
        speed = profile.speed_m_s
        start_node, pred_start, near_start = start
        end_node, pred_end, near_end = end
        walk_in = self._walk(
            start_node,
            pred_start,
            near_start[entry.id][0],
            weights,
            hazards,
            speed,
            False,
        )
        walk_out = self._walk(
            end_node, pred_end, near_end[exit_.id][0], weights, hazards, speed, True
        )
        entry_pt = [round(entry.lng, 6), round(entry.lat, 6)]
        exit_pt = [round(exit_.lng, 6), round(exit_.lat, 6)]
        in_extra = _metres(walk_in.coordinates[-1], entry_pt)
        out_extra = _metres(exit_pt, walk_out.coordinates[0])

        legs: list[Leg] = [
            Leg(
                "walk",
                "Start",
                entry.title,
                round(walk_in.distance_m + in_extra, 1),
                option.walk_in_s,
                [*walk_in.coordinates, entry_pt],
                route=walk_in,
            )
        ]
        steps: list[Step] = [*walk_in.steps]
        steps.append(
            Step(
                f"Enter {entry.title}. {access[entry.id].note or ''}".strip(),
                in_extra,
                (),
            )
        )
        used = [access[entry.id]]
        for n, ride in enumerate(option.rides):
            if n == 1:
                t = option.transfer
                to = stations[ride.board]
                if t is None:
                    text = f"Change trains at {to.title}."
                    distance, coords = 0.0, [[round(to.lng, 6), round(to.lat, 6)]]
                else:
                    frm = stations[option.rides[0].alight]
                    distance = haversine_m(frm.lat, frm.lng, to.lat, to.lng)
                    coords = [
                        [round(frm.lng, 6), round(frm.lat, 6)],
                        [round(to.lng, 6), round(to.lat, 6)],
                    ]
                    text = (
                        f"Change at {frm.name}: go to {to.title} "
                        f"(about {t.walk_s / 60:.0f} min, the scheduled transfer time). "
                        f"{access[to.id].note or ''}".strip()
                    )
                    used.append(access[frm.id])
                used.append(access[to.id])
                legs.append(
                    Leg(
                        "transfer",
                        stations[option.rides[0].alight].title,
                        to.title,
                        round(distance, 1),
                        self._transfer_s(t),
                        coords,
                    )
                )
                steps.append(Step(text, distance, ()))
            coords, distance = self._ride_geometry(ride)
            legs.append(
                Leg(
                    "ride",
                    stations[ride.board].title,
                    stations[ride.alight].title,
                    round(distance, 1),
                    ride.wait_s + ride.ride_s,
                    coords,
                    ride=ride,
                )
            )
            steps.append(Step(_ride_text(ride, stations), distance, ()))
        used.append(access[exit_.id])
        steps.append(
            Step(
                f"Leave {exit_.title}. {access[exit_.id].note or ''}".strip(),
                out_extra,
                (),
            )
        )
        steps.extend(walk_out.steps)
        legs.append(
            Leg(
                "walk",
                exit_.title,
                "Destination",
                round(walk_out.distance_m + out_extra, 1),
                option.walk_out_s,
                [exit_pt, *walk_out.coordinates],
                route=walk_out,
            )
        )

        coordinates: list[list[float]] = []
        for leg in legs:
            for point in leg.coordinates:
                if not coordinates or coordinates[-1] != point:
                    coordinates.append(point)
        warnings = [WAIT_WARNING]
        if not {entry.id, exit_.id} <= self.mapped_entries:
            warnings.append(UNMAPPED_ENTRY_WARNING)
        warnings += walk_in.warnings + walk_out.warnings
        total_min = option.total_s / 60
        if walk_eta_min is not None:
            reason = (
                f"Walk and LRT: about {walk_eta_min - total_min:.0f} min faster "
                "than walking"
            )
        else:
            reason = "Walk and LRT: no walking route was found"
        walk_m = legs[0].distance_m + legs[-1].distance_m
        distance_m = sum(leg.distance_m for leg in legs)
        hazards_on: list[EdgeHazard] = []
        for hazard in (*walk_in.hazards_on_route, *walk_out.hazards_on_route):
            if all(h.item_id != hazard.item_id for h in hazards_on):
                hazards_on.append(hazard)
        route = Route(
            nodes=(*walk_in.nodes, *walk_out.nodes),
            edges=(*walk_in.edges, *walk_out.edges),
            distance_m=round(distance_m, 1),
            eta_min=round(total_min, 1),
            coordinates=coordinates,
            steps=steps,
            hazards_on_route=hazards_on,
            hazards_avoided=[],
            warnings=warnings,
            reason=reason,
        )
        return LrtTrip(
            legs, route, used, option.total_s, round(walk_m, 1), warnings, reason
        )

    def _ride_geometry(self, ride: Ride) -> tuple[list[list[float]], float]:
        """Straight lines through the stations passed: the map shows the stops."""
        line = ride.lines[0]
        i, j = line.position(ride.board), line.position(ride.alight)
        assert i is not None and j is not None
        ids = line.stations[i : j + 1] if i < j else line.stations[j : i + 1][::-1]
        stations = [self.schedule.stations[s] for s in ids]
        coords = [[round(s.lng, 6), round(s.lat, 6)] for s in stations]
        distance = sum(
            haversine_m(a.lat, a.lng, b.lat, b.lng) for a, b in pairwise(stations)
        )
        return coords, distance


def _service_hour(at: datetime) -> tuple[str, int]:
    """(day type, hour) of the GTFS service day, in Edmonton time."""
    local = at.astimezone(EDMONTON)
    day = local.date()
    if local.hour < SERVICE_DAY_START_HOUR:
        day -= timedelta(days=1)
    return DAY_TYPES[max(0, day.weekday() - 4)], local.hour


def _metres(a: Sequence[float], b: Sequence[float]) -> float:
    """Distance between two [lng, lat] points."""
    return haversine_m(a[1], a[0], b[1], b[0])


def _ride_text(ride: Ride, stations: Mapping[str, LrtStation]) -> str:
    choices = " or ".join(
        f"the {line.name} toward {sign}"
        for line, sign in zip(ride.lines, ride.headsigns, strict=True)
    )
    stops = f"{ride.stops} stop{'s' if ride.stops != 1 else ''}"
    return (
        f"Ride {choices} from {stations[ride.board].name} to "
        f"{stations[ride.alight].name}: {stops}, {ride.ride_s / 60:.0f} min scheduled. "
        f"Wait about {ride.wait_s / 60:.0f} min (estimate from the schedule, not a "
        "live arrival)."
    )


# --------------------------------------------------------------------- report


def trip_report(
    trip: LrtTrip, plan: LrtPlan, indexes: Sequence[ItemIndex]
) -> RouteReport:
    """The report for a walk and LRT trip.

    The walks are reported like any route (on them, within 50 m, avoided). The ride
    itself is not: items near the tracks do not affect a rider. Every elevator and
    escalator outage at a station the trip uses is on the trip (category indoor);
    outages that made a nearby station unusable are listed as avoided, with why.
    """
    order = {Status.ON_ROUTE: 0, Status.NEARBY: 1, Status.AVOIDED: 2}
    entries: dict[str, ReportEntry] = {}
    for leg in trip.legs:
        if leg.route is None:
            continue
        for g in build_report(leg.route, indexes).groups:
            for entry in g.entries:
                old = entries.get(entry.record.id)
                if old is None or order[entry.status] < order[old.status]:
                    entries[entry.record.id] = entry

    def put(outage: DeviceOutage, status: Status, note: str) -> None:
        old = entries.get(outage.record.id)
        record = replace(outage.record, note=note)
        entries[outage.record.id] = ReportEntry(
            record, status, old.distance_m if old else None
        )

    used_ids = set()
    for station in trip.stations:
        used_ids.add(station.station.id)
        for outage in station.outages:
            put(
                outage,
                Status.ON_ROUTE,
                " ".join(
                    part
                    for part in (
                        f"At {station.station.title}, which this trip uses.",
                        station.note,
                    )
                    if part
                ),
            )
    for station in plan.skipped:
        if station.station.id in used_ids:
            continue
        for outage in station.outages:
            put(
                outage,
                Status.AVOIDED,
                f"This trip does not use {station.station.title}: {station.reason}.",
            )
    return group(entries.values())


# ----------------------------------------------------------------- API shapes


class WaitOut(BaseModel):
    """The wait for a train: always an estimate from the schedule.

    Build these (and every model below) with every field passed: ``/api/route``
    leaves unset fields out of the response, defaults included.
    """

    minutes: float
    estimate: Literal[True] = True
    headway_min: float = Field(
        description="Scheduled time between trains at this hour (lines combined)."
    )
    note: str = WAIT_NOTE


class LegOut(BaseModel):
    """One part of a walk and LRT trip."""

    kind: Literal["walk", "ride", "transfer"]
    from_name: str
    to_name: str
    distance_m: float
    duration_min: float = Field(description="For a ride: the wait plus the ride.")
    geometry: dict[str, Any]  # GeoJSON LineString, [lng, lat]
    lines: list[str] | None = (
        None  # ride: "Capital Line" (or several on a shared track)
    )
    line_colors: list[str] | None = None
    headsigns: list[str] | None = None  # ride: where each line's train is going
    stops: int | None = None
    ride_min: float | None = Field(
        default=None, description="Scheduled ride time (median over the timetable)."
    )
    wait: WaitOut | None = None


class DeviceOut(BaseModel):
    device_id: str
    kind: Literal["elevator", "escalator"]
    location: str
    role: Literal["platform", "entrance"] | None
    status: Literal["in_service", "out_of_service"]


class StationOut(BaseModel):
    """A station the trip uses, with its elevator and escalator status."""

    id: str
    name: str
    access: Literal["elevator", "at_grade"]
    usable: bool
    note: str | None
    devices: list[DeviceOut]


class LrtTripOut(BaseModel):
    """What only a walk and LRT trip has."""

    stations: list[StationOut]
    walk_distance_m: float
    wait_is_estimate: Literal[True] = True
    schedule: str = Field(description="The ETS GTFS feed version the times come from.")
    attribution: str
    maps_note: str = MAPS_NOTE


def legs_out(trip: LrtTrip) -> list[LegOut]:
    out = []
    for leg in trip.legs:
        ride = leg.ride
        out.append(
            LegOut(
                kind=leg.kind,
                from_name=leg.from_name,
                to_name=leg.to_name,
                distance_m=leg.distance_m,
                duration_min=round(leg.duration_s / 60, 1),
                geometry={"type": "LineString", "coordinates": leg.coordinates},
                lines=[line.name for line in ride.lines] if ride else None,
                line_colors=[f"#{line.color}" for line in ride.lines if line.color]
                if ride
                else None,
                headsigns=list(ride.headsigns) if ride else None,
                stops=ride.stops if ride else None,
                ride_min=round(ride.ride_s / 60, 1) if ride else None,
                wait=WaitOut(
                    minutes=round(ride.wait_s / 60, 1),
                    estimate=True,
                    headway_min=round(ride.headway_s / 60, 1),
                    note=WAIT_NOTE,
                )
                if ride
                else None,
            )
        )
    return out


def trip_out(trip: LrtTrip, schedule: LrtSchedule) -> LrtTripOut:
    stations = []
    for station in trip.stations:
        out_ids = {o.device.device_id for o in station.outages if o.device}
        stations.append(
            StationOut(
                id=station.station.id,
                name=station.station.title,
                access=station.station.access,
                usable=station.usable,
                note=station.note,
                devices=[
                    DeviceOut(
                        device_id=d.device_id,
                        kind=d.kind,
                        location=d.location,
                        role=d.role,
                        status="out_of_service"
                        if d.device_id in out_ids
                        else "in_service",
                    )
                    for d in station.station.devices
                ],
            )
        )
    # Every field is passed, defaults included: /api/route drops unset fields.
    return LrtTripOut(
        stations=stations,
        walk_distance_m=trip.walk_m,
        wait_is_estimate=True,
        schedule=str(schedule.source.get("feed_version") or "unknown"),
        attribution=schedule.attribution,
        maps_note=MAPS_NOTE,
    )


def _fmt(lat: float, lng: float) -> str:
    return f"{lat:.6f},{lng:.6f}"


def transit_maps_urls(
    origin: tuple[float, float], destination: tuple[float, float]
) -> tuple[str, str]:
    """Google and Apple Maps transit links (they choose their own transit trip)."""
    google = "https://www.google.com/maps/dir/?" + urlencode(
        {
            "api": "1",
            "origin": _fmt(*origin),
            "destination": _fmt(*destination),
            "travelmode": "transit",
        }
    )
    apple = "https://maps.apple.com/?" + urlencode(
        {"saddr": _fmt(*origin), "daddr": _fmt(*destination), "dirflg": "r"}
    )
    return google, apple


# --------------------------------------------------------------------- startup


def load_planner(
    router: Router, path: Path | str = DEFAULT_LRT_PATH
) -> LrtPlanner | None:
    """The planner for ``app.state.lrt_planner``, or None (routes stay walking only)."""
    try:
        schedule = LrtSchedule.load(path)
        planner = LrtPlanner(router, schedule)
    except Exception:
        log.exception("LRT trips unavailable; routes are walking only")
        return None
    log.info(
        "LRT %d stations, %d lines, schedule %s",
        len(schedule.stations),
        len(schedule.lines),
        schedule.source.get("feed_version"),
    )
    return planner
