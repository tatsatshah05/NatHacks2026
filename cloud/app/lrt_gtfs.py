"""OP-311: the ETS GTFS schedule as a compact LRT station graph (and, OP-417, bus lines).

Build time only. ``scripts/build_lrt_graph.py`` downloads the official ETS GTFS zip
once (City of Edmonton open data), calls ``read_lrt()`` and writes
``cloud/data/lrt.json``. ``scripts/build_bus_graph.py`` calls ``read_bus()`` on the
same zip and writes ``cloud/data/bus.json``. The API never reads GTFS:
``cloud/app/lrt.py`` loads the JSON at startup. Tests feed both readers a tiny
synthetic zip or a recorded sample, never the real download.

What is read
    routes.txt        ``route_type`` 0 (light rail): the Capital, Metro and Valley lines
    trips.txt         the trips of those routes, with direction and headsign
    stops.txt         stops grouped into stations by ``parent_station`` (else by stop id)
    stop_times.txt    scheduled times: the ride time between consecutive stations
                      (median over trips) and departures per hour (headways)
    calendar.txt and calendar_dates.txt
                      which trips run on a weekday, a Saturday and a Sunday
    pathways.txt      elevator pathways (mode 5) per station, as evidence that the
                      station needs an elevator for step-free access
    transfers.txt     minimum transfer times between stops of neighbouring stations
    feed_info.txt     the feed version and its validity dates

Stations
    A station is a GTFS parent station (``location_type`` 1) with its child platforms.
    A platform where no trip lets anyone board or alight (a depot platform, a tail
    track) is not a passenger station: it is dropped from the line and the ride time
    spans it.

Line order
    Among the longest stop sequences in direction 0, the most common one is the
    line's order. Every other trip must be a contiguous run of it (a short turn),
    reversed for direction 1; a trip that is not is counted as irregular and ignored
    for ride times and headways.

Headways
    For each day type (weekday, saturday, sunday) the date in the feed with the most
    LRT trips stands for that day type, so a holiday schedule never stands for a
    normal day. The headway for an hour is the median gap between consecutive
    departures from one station in one direction that begin in that hour, over every
    station of the line. GTFS hours 24 and later (after midnight) fold into 0 and 1.
    The planner halves the headway for its wait estimate and labels it as such.

Bus lines (OP-417, ``read_bus``)
    A bus line is one stop pattern: the trips of one route and direction that call
    at the same stops in the same order. ETS routes branch and short-turn, and the
    community routes are loops that start and end at one transit centre (a stop
    repeats), so the LRT line-order rule does not fit them; a pattern keeps every
    trip's stops and times, and the planner combines the patterns of a route where
    they overlap, as it combines LRT lines on a shared track. Ride times are medians
    over the pattern's trips. Headways come from the pattern's departures at its
    first stop, on the busiest date of each day type. Only stops a kept line serves
    are written. GTFS values (route short names, headsigns, stop names) are kept
    verbatim; the planner derives display forms.
"""

from __future__ import annotations

import csv
import io
import itertools
import math
import re
import statistics
import zipfile
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import IO, Any

LRT_ROUTE_TYPE = "0"
BUS_ROUTE_TYPE = "3"
ETS_AGENCY_IDS = frozenset({"1"})  # Edmonton Transit Service in the ETS feed
ELEVATOR_PATHWAY = "5"
NO_SERVICE = "1"  # pickup_type / drop_off_type: nobody boards or alights here
DAY_TYPES = ("weekday", "saturday", "sunday")
HOURS = 24
TRANSFER_M = 250.0  # stations of different lines this close form a transfer pair
TRANSFER_WALK_M_S = 0.9  # the slowest profile's speed
TRANSFER_BUFFER_S = 60
EARTH_RADIUS_M = 6_371_008.8
FORMAT_VERSION = 1
BUS_FORMAT_VERSION = 2  # cloud/data/bus.json
WEEKDAY_FLAGS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


# GTFS, the City device list and the City station list spell some stations
# differently; these keys make them meet. Verified against the GTFS feed of
# November 8, 2026 and the recorded City datasets. Never use fuzzy matching.
KEY_ALIASES = {
    "governmentcentre": "government",
    "bayenterprise": "bayenterprisesquare",
    "southcamputsftedmonton": "southcampusftedmonton",
}


def station_key(name: str) -> str:
    """Normalise a station name so GTFS, City devices and City stations match.

    "Churchill Station Underground", "Churchill Stop" and "Churchill" share one key;
    callers that need to tell them apart prefer the one named a Station.
    """
    name = re.sub(r"\b(station|stop|underground)\b", "", name, flags=re.IGNORECASE)
    key = re.sub(r"[^a-z0-9]", "", name.casefold())
    return KEY_ALIASES.get(key, key)


def short_name(gtfs_name: str) -> str:
    """ "Southgate Station" -> "Southgate"; "Churchill Station Underground" -> "Churchill"."""
    return re.sub(
        r"\s+(station underground|station|stop)$", "", gtfs_name, flags=re.IGNORECASE
    ).strip()


class GtfsError(ValueError):
    """The feed cannot be turned into a usable LRT graph."""


# ------------------------------------------------------------------ the graph


@dataclass
class Station:
    id: str  # the GTFS parent station id, e.g. "Q7004"
    gtfs_name: str
    lat: float
    lng: float
    elevator_pathways: int = 0
    stop_ids: list[str] = field(default_factory=list)


@dataclass
class Line:
    id: str  # the GTFS route id, e.g. "021R"
    name: str  # "Capital Line"
    short_name: str  # "Capital"
    color: str | None
    stations: list[str]  # station ids in direction 0 order
    ride_s: list[int]  # scheduled seconds between consecutive stations (len n - 1)
    headsigns: dict[str, str]  # direction "0" / "1" -> where the train is going
    headway_s: dict[str, list[int | None]]  # day type -> 24 hourly headways
    trips: int
    irregular_trips: int


@dataclass
class Transfer:
    from_station: str
    to_station: str
    walk_s: int


@dataclass
class LrtGraph:
    feed: dict[str, str]
    stations: list[Station]
    lines: list[Line]
    transfers: list[Transfer]

    def station(self, station_id: str) -> Station:
        return next(s for s in self.stations if s.id == station_id)


# -------------------------------------------------------------------- reading


@dataclass(frozen=True)
class _StopTime:
    sequence: int
    stop_id: str
    arrival_s: int
    departure_s: int
    boards: bool  # someone may get on or off here


def _rows(
    zf: zipfile.ZipFile, name: str, required: bool = True
) -> Iterator[dict[str, str]]:
    """The rows of one GTFS table, with keys and values stripped of stray spaces."""
    try:
        member = zf.getinfo(name)
    except KeyError:
        if required:
            raise GtfsError(f"the GTFS zip has no {name}") from None
        return
    with zf.open(member) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text):
            yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}


def _seconds(value: str) -> int:
    """``HH:MM:SS`` to seconds; GTFS hours may pass 24 for service after midnight."""
    parts = value.split(":")
    if len(parts) != 3:
        raise GtfsError(f"bad GTFS time {value!r}")
    try:
        h, m, s = (int(p) for p in parts)
    except ValueError:
        raise GtfsError(f"bad GTFS time {value!r}") from None
    return h * 3600 + m * 60 + s


def _date(value: str) -> date:
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        raise GtfsError(f"bad GTFS date {value!r}") from None


def _float(value: str, what: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise GtfsError(f"bad {what} {value!r}") from None
    if not math.isfinite(result):
        raise GtfsError(f"bad {what} {value!r}")
    return result


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _service_dates(zf: zipfile.ZipFile) -> dict[str, set[date]]:
    """service_id -> the dates it runs, from calendar.txt and calendar_dates.txt."""
    dates: dict[str, set[date]] = defaultdict(set)
    for row in _rows(zf, "calendar.txt", required=False):
        start, end = _date(row["start_date"]), _date(row["end_date"])
        flags = [row.get(flag, "0") == "1" for flag in WEEKDAY_FLAGS]
        day = start
        while day <= end:
            if flags[day.weekday()]:
                dates[row["service_id"]].add(day)
            day += timedelta(days=1)
    for row in _rows(zf, "calendar_dates.txt", required=False):
        day = _date(row["date"])
        if row.get("exception_type") == "2":
            dates[row["service_id"]].discard(day)
        else:
            dates[row["service_id"]].add(day)
    return dates


def read_lrt(source: Path | str | IO[bytes]) -> LrtGraph:
    """Read a GTFS zip (a path or an open binary file) into an ``LrtGraph``."""
    with zipfile.ZipFile(source) as zf:
        feed = next(_rows(zf, "feed_info.txt", required=False), {})
        routes = {
            r["route_id"]: r
            for r in _rows(zf, "routes.txt")
            if r.get("route_type") == LRT_ROUTE_TYPE
        }
        if not routes:
            raise GtfsError("no light-rail routes (route_type 0) in routes.txt")
        trips = {
            t["trip_id"]: t for t in _rows(zf, "trips.txt") if t["route_id"] in routes
        }
        if not trips:
            raise GtfsError("no trips for the light-rail routes in trips.txt")
        stops = {s["stop_id"]: s for s in _rows(zf, "stops.txt")}
        times = _stop_times(zf, trips)
        service_dates = _service_dates(zf)
        pathways = list(_rows(zf, "pathways.txt", required=False))
        transfer_rows = list(_rows(zf, "transfers.txt", required=False))

    def station_of(stop_id: str) -> str:
        stop = stops.get(stop_id)
        if stop is None:
            raise GtfsError(f"stop_times refers to an unknown stop {stop_id}")
        return stop.get("parent_station") or stop_id

    stations = _stations(times, stops, station_of, pathways)
    lines = [
        _line(route, trips, times, stations, station_of, service_dates)
        for _, route in sorted(routes.items())
    ]
    transfers = _transfers(stations, lines, transfer_rows, station_of)
    return LrtGraph(
        feed=feed, stations=list(stations.values()), lines=lines, transfers=transfers
    )


def _stop_times(
    zf: zipfile.ZipFile, trips: dict[str, dict[str, str]]
) -> dict[str, list[_StopTime]]:
    """The stop times of ``trips``, in stop sequence order, from stop_times.txt."""
    times: dict[str, list[_StopTime]] = defaultdict(list)
    for row in _rows(zf, "stop_times.txt"):
        if row["trip_id"] not in trips:
            continue
        arrival = row.get("arrival_time") or row.get("departure_time") or ""
        departure = row.get("departure_time") or arrival
        times[row["trip_id"]].append(
            _StopTime(
                int(row["stop_sequence"]),
                row["stop_id"],
                _seconds(arrival),
                _seconds(departure),
                boards=(row.get("pickup_type") or "0") != NO_SERVICE
                or (row.get("drop_off_type") or "0") != NO_SERVICE,
            )
        )
    for trip_times in times.values():
        trip_times.sort(key=lambda s: s.sequence)
    return times


def _stations(
    times: dict[str, list[_StopTime]],
    stops: dict[str, dict[str, str]],
    station_of: Any,
    pathways: list[dict[str, str]],
) -> dict[str, Station]:
    """Passenger stations: at least one trip lets someone board or alight there."""
    children: dict[str, set[str]] = defaultdict(set)
    passenger: set[str] = set()
    for trip_times in times.values():
        for st in trip_times:
            station_id = station_of(st.stop_id)
            children[station_id].add(st.stop_id)
            if st.boards:
                passenger.add(station_id)
    if not passenger:
        raise GtfsError("no light-rail stop lets anyone board or alight")

    result: dict[str, Station] = {}
    for station_id in sorted(passenger):
        parent = stops.get(station_id)
        kids = sorted(children[station_id])
        if parent is not None and parent.get("location_type") == "1":
            name = parent["stop_name"]
            lat = _float(parent["stop_lat"], "stop_lat")
            lng = _float(parent["stop_lon"], "stop_lon")
        else:  # no parent record: the first platform names it, their mean places it
            name = stops[kids[0]]["stop_name"]
            lat = statistics.fmean(
                _float(stops[k]["stop_lat"], "stop_lat") for k in kids
            )
            lng = statistics.fmean(
                _float(stops[k]["stop_lon"], "stop_lon") for k in kids
            )
        result[station_id] = Station(station_id, name, lat, lng, 0, kids)

    for pathway in pathways:
        if pathway.get("pathway_mode") != ELEVATOR_PATHWAY:
            continue
        for key in ("from_stop_id", "to_stop_id"):
            stop = stops.get(pathway.get(key, ""))
            if stop is None:
                continue
            station = result.get(stop.get("parent_station") or stop["stop_id"])
            if station is not None:
                station.elevator_pathways += 1
                break  # count each elevator once, at the station it belongs to
    return result


def _line(
    route: dict[str, str],
    trips: dict[str, dict[str, str]],
    times: dict[str, list[_StopTime]],
    stations: dict[str, Station],
    station_of: Any,
    service_dates: dict[str, set[date]],
) -> Line:
    route_id = route["route_id"]
    name = route.get("route_long_name") or route.get("route_short_name") or route_id

    # Each trip as its passenger stations in travel order, with the times there.
    visits: dict[str, list[tuple[str, int, int]]] = {}
    for trip_id, trip in trips.items():
        if trip["route_id"] != route_id or trip_id not in times:
            continue
        seq: list[tuple[str, int, int]] = []
        for st in times[trip_id]:
            station_id = station_of(st.stop_id)
            if station_id in stations and (not seq or seq[-1][0] != station_id):
                seq.append((station_id, st.arrival_s, st.departure_s))
        if len(seq) >= 2:
            visits[trip_id] = seq
    if not visits:
        raise GtfsError(f"{name}: no trip serves two or more passenger stations")

    def oriented(trip_id: str) -> tuple[str, ...]:
        ids = tuple(s for s, _, _ in visits[trip_id])
        return ids if trips[trip_id].get("direction_id", "0") != "1" else ids[::-1]

    # The order: the most common among the longest direction-0 sequences.
    sequences = Counter(oriented(t) for t in visits)
    longest = max(len(s) for s in sequences)
    order = list(
        max(
            (s for s in sequences if len(s) == longest),
            key=lambda s: (sequences[s], s),
        )
    )
    index = {station_id: i for i, station_id in enumerate(order)}

    ride_samples: list[list[int]] = [[] for _ in range(len(order) - 1)]
    regular: set[str] = set()
    for trip_id, seq in visits.items():
        run = oriented(trip_id)
        start = index.get(run[0])
        if start is None or tuple(order[start : start + len(run)]) != run:
            continue  # irregular: not a contiguous run of the line
        regular.add(trip_id)
        for (a, _, dep_a), (b, arr_b, _) in itertools.pairwise(seq):
            link = min(index[a], index[b])
            if arr_b >= dep_a:
                ride_samples[link].append(arr_b - dep_a)
    ride_s = []
    for i, samples in enumerate(ride_samples):
        if not samples:
            raise GtfsError(f"{name}: no trip rides {order[i]} to {order[i + 1]}")
        ride_s.append(round(statistics.median(samples)))

    headsigns: dict[str, str] = {}
    for direction, fallback in (("0", order[-1]), ("1", order[0])):
        signs = Counter(
            trips[t].get("trip_headsign", "")
            for t in regular
            if trips[t].get("direction_id", "0") == direction
        )
        signs.pop("", None)
        headsigns[direction] = (
            signs.most_common(1)[0][0] if signs else stations[fallback].gtfs_name
        )

    return Line(
        id=route_id,
        name=name,
        short_name=route.get("route_short_name") or name,
        color=route.get("route_color") or None,
        stations=order,
        ride_s=ride_s,
        headsigns=headsigns,
        headway_s=_headways(regular, trips, visits, service_dates),
        trips=len(visits),
        irregular_trips=len(visits) - len(regular),
    )


def day_type(day: date) -> str:
    """``weekday``, ``saturday`` or ``sunday``."""
    return DAY_TYPES[max(0, day.weekday() - 4)]


def _busiest_dates(
    trip_ids: Iterable[str],
    trips: dict[str, dict[str, str]],
    service_dates: dict[str, set[date]],
) -> dict[str, date]:
    """For each day type, the date in the feed on which most of these trips run.

    So a holiday schedule never stands for a normal day. Ties go to the later date.
    """
    trips_by_service: Counter[str] = Counter(trips[t]["service_id"] for t in trip_ids)
    trips_by_date: Counter[date] = Counter()
    for service_id, count in trips_by_service.items():
        for day in service_dates.get(service_id, ()):
            trips_by_date[day] += count
    chosen: dict[str, date] = {}
    for type_ in DAY_TYPES:
        candidates = [d for d in trips_by_date if day_type(d) == type_]
        if candidates:
            chosen[type_] = max(
                candidates, key=lambda d: (trips_by_date[d], -d.toordinal())
            )
    return chosen


def _median_gaps(departure_groups: Iterable[list[int]]) -> list[int | None]:
    """Per hour, the median gap between consecutive departures within each group.

    A gap belongs to the hour of its earlier departure; GTFS hours past 24 fold.
    """
    gaps: dict[int, list[int]] = defaultdict(list)
    for deps in departure_groups:
        deps.sort()
        for earlier, later in itertools.pairwise(deps):
            if later > earlier:
                gaps[(earlier // 3600) % HOURS].append(later - earlier)
    hours: list[int | None] = [None] * HOURS
    for hour, values in gaps.items():
        hours[hour] = round(statistics.median(values))
    return hours


def _headways(
    regular: Iterable[str],
    trips: dict[str, dict[str, str]],
    visits: dict[str, list[tuple[str, int, int]]],
    service_dates: dict[str, set[date]],
) -> dict[str, list[int | None]]:
    """Median gap between departures per hour, for the busiest date of each day type."""
    regular = list(regular)
    result: dict[str, list[int | None]] = {t: [None] * HOURS for t in DAY_TYPES}
    for type_, chosen in _busiest_dates(regular, trips, service_dates).items():
        departures: dict[tuple[str, str], list[int]] = defaultdict(list)
        for trip_id in regular:
            if chosen not in service_dates.get(trips[trip_id]["service_id"], ()):
                continue
            direction = trips[trip_id].get("direction_id", "0")
            for station_id, _, dep in visits[trip_id][:-1]:
                departures[(station_id, direction)].append(dep)
        result[type_] = _median_gaps(departures.values())
    return result


def _transfers(
    stations: dict[str, Station],
    lines: list[Line],
    transfer_rows: list[dict[str, str]],
    station_of: Any,
) -> list[Transfer]:
    """Pairs of nearby stations on different lines, with a walking time between them."""
    lines_of: dict[str, set[str]] = defaultdict(set)
    for line in lines:
        for station_id in line.stations:
            lines_of[station_id].add(line.id)
    min_transfer: dict[tuple[str, str], int] = {}
    for row in transfer_rows:
        try:
            a, b = station_of(row["from_stop_id"]), station_of(row["to_stop_id"])
        except GtfsError:
            continue
        if a != b and row.get("min_transfer_time"):
            key = (a, b)
            min_transfer[key] = max(
                min_transfer.get(key, 0), int(row["min_transfer_time"])
            )
    result: list[Transfer] = []
    ids = sorted(stations)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if not lines_of[a] or lines_of[a] == lines_of[b]:
                continue
            sa, sb = stations[a], stations[b]
            distance = haversine_m(sa.lat, sa.lng, sb.lat, sb.lng)
            if distance > TRANSFER_M:
                continue
            walk_s = round(distance / TRANSFER_WALK_M_S) + TRANSFER_BUFFER_S
            walk_s = max(
                walk_s, min_transfer.get((a, b), 0), min_transfer.get((b, a), 0)
            )
            result.append(Transfer(a, b, walk_s))
    return result


# ------------------------------------------------------------------ bus lines


@dataclass
class BusStop:
    id: str  # the GTFS stop id, e.g. "1620"
    code: str  # the number on the stop sign (stop_code, else the id)
    name: str  # "101 Street & Jasper Avenue", verbatim
    lat: float
    lng: float


@dataclass
class BusLine:
    id: str  # "<route id>-<direction>-<n>": one stop pattern of a route
    route_id: str
    short_name: str  # route_short_name, verbatim ("002")
    name: str  # route_long_name ("West Edmonton Mall - Stadium - Clareview")
    direction: str  # direction_id, "0" or "1"
    headsign: str  # the pattern's most common trip_headsign, verbatim
    color: str | None
    stops: list[str]  # stop ids in travel order; a loop repeats its first stop
    ride_s: list[int]  # median scheduled seconds between consecutive stops
    headway_s: dict[str, list[int | None]]  # day type -> 24 hourly headways
    trips: int


@dataclass
class BusGraph:
    feed: dict[str, str]
    agencies: dict[str, str]  # agency id -> name, for the routes kept
    routes: int  # bus routes kept
    stops: list[BusStop]
    lines: list[BusLine]


def read_bus(
    source: Path | str | IO[bytes],
    agency_ids: Collection[str] | None = ETS_AGENCY_IDS,
) -> BusGraph:
    """Read a GTFS zip into bus lines, one per stop pattern (module docstring).

    ``agency_ids`` keeps only those agencies' routes (the ETS feed also carries the
    regional agencies, whose fleets and licences are their own); None keeps every
    bus route.
    """
    with zipfile.ZipFile(source) as zf:
        feed = next(_rows(zf, "feed_info.txt", required=False), {})
        agencies = {
            a.get("agency_id", ""): a.get("agency_name", "")
            for a in _rows(zf, "agency.txt", required=False)
        }
        routes = {
            r["route_id"]: r
            for r in _rows(zf, "routes.txt")
            if r.get("route_type") == BUS_ROUTE_TYPE
            and (agency_ids is None or r.get("agency_id", "") in agency_ids)
        }
        if not routes:
            raise GtfsError("no bus routes (route_type 3) in routes.txt")
        trips = {
            t["trip_id"]: t for t in _rows(zf, "trips.txt") if t["route_id"] in routes
        }
        if not trips:
            raise GtfsError("no trips for the bus routes in trips.txt")
        stops = {s["stop_id"]: s for s in _rows(zf, "stops.txt")}
        times = _stop_times(zf, trips)
        service_dates = _service_dates(zf)

    # Each trip as its stops in travel order with the times there, then grouped
    # into patterns: the trips of one route and direction with the same stops.
    visits: dict[str, list[tuple[str, int, int]]] = {}
    patterns: dict[tuple[str, str, tuple[str, ...]], list[str]] = defaultdict(list)
    for trip_id, trip_times in times.items():
        seq: list[tuple[str, int, int]] = []
        for st in trip_times:
            if not seq or seq[-1][0] != st.stop_id:
                seq.append((st.stop_id, st.arrival_s, st.departure_s))
        if len(seq) < 2:
            continue
        visits[trip_id] = seq
        trip = trips[trip_id]
        ids = tuple(stop_id for stop_id, _, _ in seq)
        patterns[(trip["route_id"], trip.get("direction_id", "0"), ids)].append(trip_id)
    if not patterns:
        raise GtfsError("no bus trip calls at two or more stops")

    grouped: dict[tuple[str, str], list[tuple[tuple[str, ...], list[str]]]] = (
        defaultdict(list)
    )
    for (route_id, direction, ids), trip_ids in patterns.items():
        grouped[(route_id, direction)].append((ids, trip_ids))
    lines: list[BusLine] = []
    for (route_id, direction), group in sorted(grouped.items()):
        # Pattern 1 has the most trips; ties go to the longer one, then the stop ids.
        group.sort(key=lambda item: (-len(item[1]), -len(item[0]), item[0]))
        for n, (ids, trip_ids) in enumerate(group, start=1):
            lines.append(
                _bus_line(
                    f"{route_id}-{direction}-{n}",
                    routes[route_id],
                    direction,
                    ids,
                    trip_ids,
                    trips,
                    visits,
                    stops,
                    service_dates,
                )
            )

    bus_stops: list[BusStop] = []
    for stop_id in sorted({stop_id for line in lines for stop_id in line.stops}):
        stop = stops.get(stop_id)
        if stop is None:
            raise GtfsError(f"stop_times refers to an unknown stop {stop_id}")
        bus_stops.append(
            BusStop(
                stop_id,
                stop.get("stop_code") or stop_id,
                stop["stop_name"],
                _float(stop["stop_lat"], "stop_lat"),
                _float(stop["stop_lon"], "stop_lon"),
            )
        )
    kept = sorted({r.get("agency_id", "") for r in routes.values()})
    return BusGraph(
        feed=feed,
        agencies={a: agencies.get(a, "") for a in kept},
        routes=len(routes),
        stops=bus_stops,
        lines=lines,
    )


def _bus_line(
    line_id: str,
    route: dict[str, str],
    direction: str,
    ids: tuple[str, ...],
    trip_ids: list[str],
    trips: dict[str, dict[str, str]],
    visits: dict[str, list[tuple[str, int, int]]],
    stops: dict[str, dict[str, str]],
    service_dates: dict[str, set[date]],
) -> BusLine:
    short = route.get("route_short_name") or route["route_id"]
    ride_samples: list[list[int]] = [[] for _ in range(len(ids) - 1)]
    for trip_id in trip_ids:
        for k, ((_, _, dep_a), (_, arr_b, _)) in enumerate(
            itertools.pairwise(visits[trip_id])
        ):
            if arr_b >= dep_a:
                ride_samples[k].append(arr_b - dep_a)
    ride_s: list[int] = []
    for k, samples in enumerate(ride_samples):
        if not samples:
            raise GtfsError(
                f"route {short}: no scheduled time from {ids[k]} to {ids[k + 1]}"
            )
        ride_s.append(round(statistics.median(samples)))

    signs = Counter(trips[t].get("trip_headsign", "") for t in trip_ids)
    signs.pop("", None)
    last = stops.get(ids[-1], {})
    headsign = signs.most_common(1)[0][0] if signs else last.get("stop_name", ids[-1])

    # Headways from the departures at the first stop, on the busiest date of each
    # day type (the same rule as the LRT lines).
    headway: dict[str, list[int | None]] = {t: [None] * HOURS for t in DAY_TYPES}
    for type_, chosen in _busiest_dates(trip_ids, trips, service_dates).items():
        departures = [
            visits[t][0][2]
            for t in trip_ids
            if chosen in service_dates.get(trips[t]["service_id"], ())
        ]
        headway[type_] = _median_gaps([departures])

    return BusLine(
        id=line_id,
        route_id=route["route_id"],
        short_name=short,
        name=route.get("route_long_name") or short,
        direction=direction,
        headsign=headsign,
        color=route.get("route_color") or None,
        stops=list(ids),
        ride_s=ride_s,
        headway_s=headway,
        trips=len(trip_ids),
    )
