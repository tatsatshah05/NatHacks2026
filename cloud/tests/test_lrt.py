"""OP-311: walk and LRT trips.

GTFS parsing runs on a tiny synthetic zip built here (never the real download).
Planner and API tests use a synthetic 6 km street with a synthetic schedule, so
every time and rule is checkable by hand. The last tests use the committed
Edmonton network and ``cloud/data/lrt.json``.
"""

from __future__ import annotations

import copy
import csv
import importlib.util
import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import lrt, routing_api
from cloud.app.lrt import LrtPlanner, LrtSchedule, match_outages, station_access
from cloud.app.lrt_gtfs import GtfsError, read_lrt, station_key
from cloud.app.matching import HazardProvider
from cloud.app.network import (
    DEFAULT_NETWORK_PATH,
    RawEdge,
    RawNode,
    WalkNetwork,
    export_network,
)
from cloud.app.obstructions_api import get_now
from cloud.app.profiles import load_routing_config
from cloud.app.report import ReportRecord
from cloud.app.routing import Router
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionStore

ROOT = Path(__file__).resolve().parents[2]
# conftest's NOW: Saturday November 7, 2026, early afternoon in Edmonton. The local
# hour depends on the installed time zone data (tzdata 2026f keeps Alberta on UTC-6
# all year), so tests read it from the zone instead of assuming an offset.
NOW = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)
LOCAL_HOUR = NOW.astimezone(lrt.EDMONTON).hour

# ---------------------------------------------------------------- GTFS fixture

STOPS = {
    # stop_id: (name, lat, lng, location_type, parent)
    "PA": ("Alpha Station", 53.500, -113.500, "1", ""),
    "A1": ("Alpha Station", 53.5001, -113.5000, "0", "PA"),
    "A2": ("Alpha Station", 53.4999, -113.5000, "0", "PA"),
    "PB": ("Bravo Station", 53.510, -113.500, "1", ""),
    "B1": ("Bravo Station", 53.5101, -113.5000, "0", "PB"),
    "B2": ("Bravo Station", 53.5099, -113.5000, "0", "PB"),
    "BE1": ("Bravo Street Entrance", 53.5102, -113.5003, "2", "PB"),
    "BN1": ("Bravo Concourse", 53.5101, -113.5002, "3", "PB"),
    "PC": ("Charlie Station", 53.520, -113.500, "1", ""),
    "C1": ("Charlie Station", 53.5201, -113.5000, "0", "PC"),
    "C2": ("Charlie Station", 53.5199, -113.5000, "0", "PC"),
    "PD": ("Delta Station", 53.530, -113.500, "1", ""),
    "D1": ("Delta Station", 53.5301, -113.5000, "0", "PD"),
    "TAIL": ("Alpha Tail Track", 53.495, -113.500, "0", ""),
    "E1": ("Echo Stop", 53.520, -113.480, "0", ""),  # Blue Line, no parent station
    "G1": ("Golf Stop", 53.5301, -113.4985, "0", ""),  # Green Line, 100 m from Delta
    "H1": ("Hotel Stop", 53.540, -113.4985, "0", ""),
    "BUS1": ("Bus Stop", 53.500, -113.520, "0", ""),
}


def _csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _hms(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def gtfs_zip(path: Path) -> Path:
    """A small but complete feed: three light-rail lines, one bus route.

    Red Line (direction 0): [Alpha Tail Track, no boarding] Alpha - Bravo - Charlie - Delta.
    Weekdays it leaves Alpha every 10 min from 07:00 to 07:50, then every 20 min to
    08:40; Saturdays at 09:00 and 09:30. Rides: Alpha-Bravo 120 s (one slow trip
    takes 180 s), Bravo-Charlie 60 s, Charlie-Delta 180 s. One short turn
    (Alpha-Charlie) and one irregular trip (Alpha-Charlie skipping Bravo).
    Blue Line: Charlie - Echo, 240 s. Green Line: Golf - Hotel, 300 s.
    """
    trips: list[dict[str, Any]] = []
    times: list[dict[str, Any]] = []

    def trip(
        trip_id: str,
        route: str,
        service: str,
        direction: str,
        start_s: int,
        stops: list[tuple[str, int]],
        no_service: frozenset[str] = frozenset(),
    ) -> None:
        trips.append(
            {
                "route_id": route,
                "service_id": service,
                "trip_id": trip_id,
                "trip_headsign": {"0": "Delta", "1": "Alpha"}[direction]
                if route == "RED"
                else "",
                "direction_id": direction,
            }
        )
        at = start_s
        for seq, (stop, gap) in enumerate(stops, start=1):
            at += gap
            flag = "1" if stop in no_service else "0"
            times.append(
                {
                    "trip_id": trip_id,
                    "arrival_time": _hms(at),
                    "departure_time": _hms(at),
                    "stop_id": stop,
                    "stop_sequence": seq,
                    "pickup_type": flag,
                    "drop_off_type": flag,
                }
            )

    red = [("TAIL", 0), ("A1", 60), ("B1", 120), ("C1", 60), ("D1", 180)]
    weekday_starts = [7 * 3600 + m * 60 for m in (0, 10, 20, 30, 40, 50)] + [
        8 * 3600 + m * 60 for m in (0, 20, 40)
    ]
    for n, start in enumerate(weekday_starts):
        stops = list(red)
        if n == 3:
            stops[2] = ("B1", 180)  # one slow ride: the median stays 120 s
        trip(f"R0-{n}", "RED", "WK", "0", start - 60, stops, frozenset({"TAIL"}))
        back = [("D1", 0), ("C2", 180), ("B2", 60), ("A2", 120)]
        trip(f"R1-{n}", "RED", "WK", "1", start + 300, back)
    for n, start in enumerate((9 * 3600, 9 * 3600 + 1800)):
        trip(f"RS-{n}", "RED", "SA", "0", start - 60, red, frozenset({"TAIL"}))
    trip("R-short", "RED", "WK", "0", 9 * 3600, [("A1", 0), ("B1", 120), ("C1", 60)])
    trip("R-odd", "RED", "WK", "0", 10 * 3600, [("A1", 0), ("C1", 200)])
    for n, start in enumerate(weekday_starts):
        trip(f"B-{n}", "BLUE", "WK", "0", start, [("C2", 0), ("E1", 240)])
        trip(f"G-{n}", "GREEN", "WK", "0", start, [("G1", 0), ("H1", 300)])
    trip("BUS-1", "BUS9", "WK", "0", 7 * 3600, [("BUS1", 0), ("A1", 300)])

    files = {
        "feed_info.txt": _csv(
            [
                {
                    "feed_publisher_name": "Test Transit",
                    "feed_version": "test_1",
                    "feed_start_date": "20261101",
                    "feed_end_date": "20261130",
                }
            ]
        ),
        "routes.txt": _csv(
            [
                {
                    "route_id": rid,
                    "route_short_name": short,
                    "route_long_name": f"{short} Line",
                    "route_type": rtype,
                    "route_color": color,
                }
                for rid, short, rtype, color in (
                    ("RED", "Red", "0", "FF0000"),
                    ("BLUE", "Blue", "0", "0000FF"),
                    ("GREEN", "Green", "0", "00FF00"),
                    ("BUS9", "9", "3", ""),
                )
            ]
        ),
        "trips.txt": _csv(trips),
        "stop_times.txt": _csv(times),
        "stops.txt": _csv(
            [
                {
                    "stop_id": sid,
                    "stop_name": name,
                    "stop_lat": f"  {lat}",  # the real feed pads latitudes
                    "stop_lon": lng,
                    "location_type": ltype,
                    "parent_station": parent,
                }
                for sid, (name, lat, lng, ltype, parent) in STOPS.items()
            ]
        ),
        "calendar.txt": _csv(
            [
                {
                    "service_id": "WK",
                    **{d: "1" for d in ("monday", "tuesday", "wednesday", "thursday")},
                    "friday": "1",
                    "saturday": "0",
                    "sunday": "0",
                    "start_date": "20261102",
                    "end_date": "20261127",
                }
            ]
        ),
        "calendar_dates.txt": _csv(
            [
                {"service_id": "SA", "date": "20261107", "exception_type": "1"},
                {"service_id": "SA", "date": "20261114", "exception_type": "1"},
                {"service_id": "WK", "date": "20261111", "exception_type": "2"},
            ]
        ),
        "pathways.txt": _csv(
            [
                {
                    "pathway_id": "Bravo-lift",
                    "from_stop_id": "BE1",
                    "to_stop_id": "BN1",
                    "pathway_mode": "5",
                    "is_bidirectional": "1",
                },
                {
                    "pathway_id": "Bravo-stairs",
                    "from_stop_id": "BE1",
                    "to_stop_id": "BN1",
                    "pathway_mode": "2",
                    "is_bidirectional": "1",
                },
            ]
        ),
        "transfers.txt": _csv(
            [
                {
                    "from_stop_id": "D1",
                    "to_stop_id": "G1",
                    "transfer_type": "2",
                    "min_transfer_time": "300",
                }
            ]
        ),
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, "﻿" + text)  # a byte-order mark, as some feeds have
    return path


@pytest.fixture(scope="module")
def feed(tmp_path_factory: pytest.TempPathFactory):
    return read_lrt(gtfs_zip(tmp_path_factory.mktemp("gtfs") / "gtfs.zip"))


def test_gtfs_reads_light_rail_only_and_groups_stations(feed) -> None:
    assert [line.name for line in feed.lines] == [
        "Blue Line",
        "Green Line",
        "Red Line",
    ]
    ids = {s.id for s in feed.stations}
    assert ids == {"PA", "PB", "PC", "PD", "E1", "G1", "H1"}  # no bus stop
    assert "TAIL" not in ids  # nobody boards or alights at the tail track
    alpha = feed.station("PA")
    assert alpha.gtfs_name == "Alpha Station" and set(alpha.stop_ids) == {"A1", "A2"}
    assert alpha.lat == pytest.approx(53.5)  # the parent station's location
    assert feed.station("PB").elevator_pathways == 1  # stairs do not count
    assert feed.feed["feed_version"] == "test_1"


def test_gtfs_line_order_and_median_ride_times(feed) -> None:
    red = next(line for line in feed.lines if line.id == "RED")
    assert red.stations == ["PA", "PB", "PC", "PD"]  # direction 0 order, tail dropped
    assert red.ride_s == [120, 60, 180]  # median over trips: one 180 s ride ignored
    assert red.headsigns == {"0": "Delta", "1": "Alpha"}
    assert red.irregular_trips == 1  # Alpha-Charlie skipping Bravo
    assert red.trips == 9 * 2 + 2 + 2  # weekday both ways, Saturday, short and odd


def test_gtfs_headway_per_hour_and_day_type(feed) -> None:
    red = next(line for line in feed.lines if line.id == "RED")
    weekday = red.headway_s["weekday"]
    assert weekday[7] == 600  # every 10 minutes from 07:00
    assert weekday[8] == 1200  # every 20 minutes from 08:00
    assert weekday[12] is None  # no trains
    assert red.headway_s["saturday"][9] == 1800
    assert all(h is None for h in red.headway_s["sunday"])  # no Sunday service


def test_gtfs_transfer_pairs(feed) -> None:
    pairs = {(t.from_station, t.to_station): t.walk_s for t in feed.transfers}
    # Delta (Red) and Golf (Green) are 100 m apart; the feed's 300 s minimum wins.
    assert pairs == {("G1", "PD"): 300}


def test_gtfs_without_light_rail_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "bus.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("routes.txt", _csv([{"route_id": "9", "route_type": "3"}]))
        zf.writestr("trips.txt", "route_id,service_id,trip_id\n")
        zf.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n")
        zf.writestr("stop_times.txt", "trip_id,stop_id,stop_sequence\n")
    with pytest.raises(GtfsError, match="no light-rail routes"):
        read_lrt(path)


def test_station_keys_join_gtfs_and_city_names() -> None:
    assert station_key("Churchill Station Underground") == station_key("Churchill")
    assert station_key("Government Station") == station_key("Government Centre")
    assert station_key("Bay Enterprise Station") == station_key("Bay/Enterprise Square")
    assert station_key("Health Sciences Jubilee Station") == station_key(
        "Health Sciences/Jubilee"
    )


# ------------------------------------------------------------- build script


def build_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "openpath_build_lrt", ROOT / "scripts" / "build_lrt_graph.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_adds_city_devices_and_access(feed) -> None:
    devices = [
        {
            "device_id": "7",
            "device_type": "Elevator",
            "lrt_station_name": "Bravo",
            "lrt_device_location": "Platform",
        },
        {
            "device_id": "8",
            "device_type": "Elevator",
            "lrt_station_name": "Bravo",
            "lrt_device_location": "North Entrance",
        },
        {
            "device_id": "9",
            "device_type": "Escalator",
            "lrt_station_name": "Bravo",
            "lrt_device_location": "North Entrance",
        },
    ]
    doc = build_script().enrich(feed, devices, [], None, source={"feed_version": "t"})
    assert doc["format_version"] == 1
    bravo = next(s for s in doc["stations"] if s["id"] == "PB")
    assert bravo["access"] == "elevator"
    assert [(e["device_id"], e["role"]) for e in bravo["elevators"]] == [
        ("7", "platform"),
        ("8", "entrance"),
    ]
    assert [e["device_id"] for e in bravo["escalators"]] == ["9"]
    echo = next(s for s in doc["stations"] if s["id"] == "E1")
    assert echo["access"] == "at_grade" and echo["title"] == "Echo Stop"
    # The document loads back into the planner's schedule.
    schedule = LrtSchedule.from_document(doc)
    assert schedule.device_index["7"][0] == "PB"


def test_committed_lrt_json_is_small_and_complete() -> None:
    path = ROOT / "cloud" / "data" / "lrt.json"
    assert path.stat().st_size < 100_000
    schedule = LrtSchedule.load(path)
    names = {line.name for line in schedule.lines}
    assert names == {"Capital Line", "Metro Line", "Valley Line"}
    capital = next(line for line in schedule.lines if line.name == "Capital Line")
    titles = [schedule.stations[s].title for s in capital.stations]
    assert titles[0] == "Century Park Station" and titles[-1] == "Clareview Station"
    assert "Churchill Station" in titles and "Southgate Station" in titles
    # Every City elevator in the recorded device list belongs to a station.
    devices = json.loads(
        (ROOT / "cloud/tests/fixtures/lrt/devices.json").read_text(encoding="utf-8")
    )
    elevators = {d["device_id"] for d in devices if d["device_type"] == "Elevator"}
    mapped = {
        d for d, (_, dev) in schedule.device_index.items() if dev.kind == "elevator"
    }
    assert elevators == mapped
    assert schedule.source["attribution"]


# ---------------------------------------------------- synthetic street + schedule

LAT0, LNG0 = 53.50, -113.50
DLNG = 0.0015  # about 99 m at this latitude
NORTH = 0.00018  # about 20 m


def street() -> WalkNetwork:
    """Main Street: 61 nodes, about 99 m apart, about 6 km end to end."""
    nodes = [RawNode(i + 1, LAT0, LNG0 + i * DLNG) for i in range(61)]
    edges = [
        RawEdge(i + 1, i + 2, 99.3, highway="footway", name="Main Street", osmid=i + 1)
        for i in range(60)
    ]
    return WalkNetwork.from_data(export_network(nodes, edges, source="test"))


def at(i: int) -> tuple[float, float]:
    """The street node i (0 to 60) as (lat, lng)."""
    return LAT0, LNG0 + i * DLNG


def station(
    sid: str, i: int, access: str, elevators=(), escalators=()
) -> dict[str, Any]:
    return {
        "id": sid,
        "name": sid,
        "title": f"{sid} Station",
        "aliases": [],
        "lat": LAT0 + NORTH,
        "lng": LNG0 + i * DLNG,
        "access": access,
        "elevators": [
            {"device_id": d, "location": loc, "role": role}
            for d, loc, role in elevators
        ],
        "escalators": [{"device_id": d, "location": loc} for d, loc in escalators],
        "entry_osm_nodes": [],
    }


def hours(value: int | None) -> list[int | None]:
    return [value] * 24


def schedule_doc() -> dict[str, Any]:
    """Red: S1 - S2 - X (2 min each); Blue: X - S3 (3 min). Trains every 10 min."""
    every_10 = {t: hours(600) for t in ("weekday", "saturday", "sunday")}
    return {
        "format_version": 1,
        "source": {"feed_version": "synthetic", "feed_end_date": "20261231"},
        "stations": [
            station(
                "S1",
                1,
                "elevator",
                [
                    ("10", "Platform", "platform"),
                    ("11", "North", "entrance"),
                    ("12", "South", "entrance"),
                ],
                [("13", "North")],
            ),
            station("S2", 15, "at_grade"),
            station("X", 30, "elevator", [("40", "Platform", "platform")]),
            station(
                "S3",
                59,
                "elevator",
                [("30", "Platform", "platform")],
                [("31", "Platform")],
            ),
        ],
        "lines": [
            {
                "id": "R",
                "name": "Red Line",
                "color": "FF0000",
                "stations": ["S1", "S2", "X"],
                "ride_s": [120, 120],
                "headsigns": {"0": "X", "1": "S1"},
                "headway_s": copy.deepcopy(every_10),
            },
            {
                "id": "B",
                "name": "Blue Line",
                "color": "0000FF",
                "stations": ["X", "S3"],
                "ride_s": [180],
                "headsigns": {"0": "S3", "1": "X"},
                "headway_s": copy.deepcopy(every_10),
            },
        ],
        "transfers": [],
    }


@pytest.fixture(scope="module")
def net() -> WalkNetwork:
    return street()


@pytest.fixture(scope="module")
def config():
    return load_routing_config()


@pytest.fixture(scope="module")
def router(net, config) -> Router:
    return Router(net, config)


def planner_for(router: Router, doc: dict[str, Any] | None = None) -> LrtPlanner:
    return LrtPlanner(router, LrtSchedule.from_document(doc or schedule_doc()))


def outage(device_id: str, detail: str, kind: str = "elevator_out") -> ReportRecord:
    item = device_item(device_id, detail, kind)
    return ReportRecord.of_item(item, "station")


def device_item(
    device_id: str, detail: str, kind: str = "elevator_out", lng: float = LNG0
) -> Obstruction:
    elevator = kind == "elevator_out"
    return Obstruction.model_validate(
        {
            "source": "city_lrt_devices",
            "source_ref": device_id,
            "category": "indoor",
            "kind": kind,
            "effect": "blocked" if elevator else "caution",
            "title": f"{'Elevator' if elevator else 'Escalator'} out of service",
            "detail": detail,
            "geometry": {"type": "Point", "coordinates": [lng, LAT0 + NORTH]},
            "confidence": "high",
            "starts_at": NOW - timedelta(hours=2),
            "ends_at": None,
            "observed_at": NOW,
            "expires_at": NOW + timedelta(minutes=10),
            "url": "https://data.edmonton.ca/d/snws-u3zx",
            "attribution": "City of Edmonton Open Data",
        }
    )


def plan(
    router: Router,
    planner: LrtPlanner,
    profile: str,
    records: list[ReportRecord] | None = (),  # type: ignore[assignment]
    start: int = 0,
    end: int = 60,
    now: datetime = NOW,
):
    walk = router.route(*at(start), *at(end), profile)
    outages = None if records is None else match_outages(planner.schedule, records)
    return planner.plan(
        origin_node=walk.origin[0],
        destination_node=walk.destination[0],
        profile=router.config.profile(profile),
        hazards={},
        outages=outages,
        now=now,
        walk_eta_min=walk.routes[0].eta_min if walk.routes else None,
    )


# ----------------------------------------------------------------- the planner


def test_trip_with_one_transfer_and_estimated_waits(router) -> None:
    result = plan(router, planner_for(router), "wheelchair")
    trip = result.trip
    assert trip is not None, result.warnings
    assert [leg.kind for leg in trip.legs] == [
        "walk",
        "ride",
        "transfer",
        "ride",
        "walk",
    ]
    first, second = trip.legs[1].ride, trip.legs[3].ride
    assert (first.board, first.alight) == ("S1", "X") and first.ride_s == 240
    assert (second.board, second.alight) == ("X", "S3") and second.ride_s == 180
    assert first.wait_s == second.wait_s == 300  # half of the 10 min headway
    assert [s.station.id for s in trip.stations] == ["S1", "X", "S3"]
    # Walk 99 m to and from the street ends at 1.0 m/s, the station allowances
    # (2 min each with elevators), the waits, the rides and the 3 min transfer.
    walks = trip.legs[0].duration_s + trip.legs[-1].duration_s
    assert trip.total_s == pytest.approx(
        walks + 120 + 300 + 240 + 180 + 300 + 180 + 120, abs=1
    )
    assert trip.route.eta_min == pytest.approx(trip.total_s / 60, abs=0.05)
    assert trip.route.eta_min < 99  # walking the 6 km takes about 99 min
    assert any("estimate" in w for w in trip.warnings)
    texts = [s.text for s in trip.route.steps]
    assert any(t.startswith("Ride the Red Line toward X from S1 to X") for t in texts)
    assert any("Change trains at X Station" in t for t in texts)
    assert all("estimate from the schedule" in t for t in texts if t.startswith("Ride"))


def test_wheelchair_cannot_use_a_station_whose_only_elevator_is_out(router) -> None:
    planner = planner_for(router)
    records = [outage("30", "S3, Platform")]
    blocked = plan(router, planner, "wheelchair", records)
    assert blocked.trip is None
    assert any(
        "S3 Station is not used for the Wheelchair profile" in w
        and "Platform elevator is out of service" in w
        for w in blocked.warnings
    )
    assert [s.station.id for s in blocked.skipped] == ["S3"]
    walker = plan(router, planner, "walker_cane", records)
    assert walker.trip is None  # walkers need the elevator too
    standard = plan(router, planner, "standard", records)
    assert standard.trip is not None  # standard is unaffected
    assert standard.trip.stations[-1].station.id == "S3"
    low_vision = plan(router, planner, "low_vision", records)
    assert low_vision.trip is not None


def test_a_transfer_needs_a_working_elevator_for_step_free_profiles(router) -> None:
    planner = planner_for(router)
    records = [outage("40", "X, Platform")]
    assert plan(router, planner, "wheelchair", records).trip is None
    assert plan(router, planner, "standard", records).trip is not None


def test_entrance_elevators_one_working_is_enough(router) -> None:
    planner = planner_for(router)
    one_out = plan(router, planner, "wheelchair", [outage("11", "S1, North")])
    assert one_out.trip is not None
    s1 = one_out.trip.stations[0]
    assert s1.usable and s1.note == "Use an entrance with a working elevator: South."
    both = [outage("11", "S1, North"), outage("12", "S1, South")]
    assert plan(router, planner, "wheelchair", both).trip is None


def test_an_unlisted_elevator_outage_closes_its_station_by_name(router) -> None:
    planner = planner_for(router)
    records = [outage("999", "S3, Somewhere new")]
    outages = match_outages(planner.schedule, records)
    assert [(o.station_id, o.device) for o in outages] == [("S3", None)]
    result = plan(router, planner, "wheelchair", records)
    assert result.trip is None
    assert any("cannot tell which one" in w for w in result.warnings)


def test_at_grade_stations_need_no_elevator(router) -> None:
    doc = schedule_doc()
    # Ride S1 to S2 (at grade) and walk from there: the trip ends 1.4 km from S3.
    result = plan(router, planner_for(router, doc), "wheelchair", end=20)
    assert result.trip is not None, result.warnings
    s2 = result.trip.stations[-1]
    assert s2.station.id == "S2" and s2.usable and "No elevator is listed" in s2.note


def test_a_station_without_a_mapped_elevator_is_not_used(router) -> None:
    doc = schedule_doc()
    doc["stations"][3]["elevators"] = []  # S3: elevator access, but none listed
    result = plan(router, planner_for(router, doc), "wheelchair")
    assert result.trip is None
    assert any("no mapped elevator" in w for w in result.warnings)


def test_unknown_device_status_keeps_step_free_profiles_off_elevator_stations(
    router,
) -> None:
    result = plan(router, planner_for(router), "wheelchair", records=None)
    assert result.trip is None
    assert any("elevator status is unavailable" in w for w in result.warnings)
    assert plan(router, planner_for(router), "standard", records=None).trip


def test_lines_sharing_a_track_combine_their_headways(router) -> None:
    doc = schedule_doc()
    twin = copy.deepcopy(doc["lines"][0])
    twin.update(id="R2", name="Pink Line", headsigns={"0": "X2", "1": "S1"})
    doc["lines"].append(twin)
    result = plan(router, planner_for(router, doc), "standard", end=30)
    ride = result.trip.legs[1].ride
    assert {line.id for line in ride.lines} == {"R", "R2"}
    assert ride.headway_s == 300 and ride.wait_s == 150  # two trains per 10 min


def test_no_train_at_this_hour(router) -> None:
    doc = schedule_doc()
    for line in doc["lines"]:
        line["headway_s"]["saturday"][LOCAL_HOUR] = None  # NOW is a Saturday
    result = plan(router, planner_for(router, doc), "standard")
    assert result.trip is None
    assert any("no train serves" in w for w in result.warnings)


def test_after_midnight_uses_the_previous_service_day() -> None:
    saturday_early = datetime(2026, 11, 7, 0, 30, tzinfo=lrt.EDMONTON)
    assert lrt._service_hour(saturday_early) == ("weekday", 0)  # Friday's late trains
    assert lrt._service_hour(NOW) == ("saturday", LOCAL_HOUR)
    sunday_noon = datetime(2026, 11, 8, 12, 0, tzinfo=lrt.EDMONTON)
    assert lrt._service_hour(sunday_noon) == ("sunday", 12)


def test_walking_is_kept_when_it_is_faster(router) -> None:
    result = plan(router, planner_for(router), "standard", start=0, end=3)
    assert result.trip is None
    assert any("Walking is faster" in w for w in result.warnings)


def test_an_old_schedule_is_flagged(router) -> None:
    doc = schedule_doc()
    doc["source"]["feed_end_date"] = "20261101"
    result = plan(router, planner_for(router, doc), "standard")
    assert result.trip is not None
    assert any(
        "schedule OpenPath holds ended on 2026-11-01" in w for w in result.warnings
    )


def test_station_access_rules_directly() -> None:
    schedule = LrtSchedule.from_document(schedule_doc())
    s1 = schedule.stations["S1"]
    fine = station_access(s1, [], step_free=True, status_known=True)
    assert fine.usable and fine.note.startswith("Elevators in service")
    escalator = match_outages(schedule, [outage("13", "S1, North", "escalator_out")])
    standard = station_access(s1, escalator, step_free=False, status_known=True)
    assert standard.usable and standard.note == "Escalator out of service: North."
    assert station_access(s1, escalator, step_free=True, status_known=True).usable


# ------------------------------------------------------------------------ API

RESPONSE_KEYS = {
    "profile",
    "generated_at",
    "origin_snap_m",
    "destination_snap_m",
    "routes",
    "warnings",
    "apple_maps_note",
}
ROUTE_KEYS = {
    "rank",
    "geometry",
    "distance_m",
    "eta_min",
    "steps",
    "hazards_on_route",
    "hazards_avoided",
    "warnings",
    "reason",
    "report",
    "google_maps_url",
    "apple_maps_url",
}


@pytest.fixture
def store_with_outages(store: ObstructionStore) -> ObstructionStore:
    store.upsert(
        [
            device_item("31", "S3, Platform", "escalator_out", LNG0 + 59 * DLNG),
            device_item("11", "S1, North", "elevator_out", LNG0 + DLNG),
        ],
        NOW,
    )
    return store


def make_client(
    router: Router,
    store: ObstructionStore,
    planner: LrtPlanner | None,
    *,
    provider: bool = True,
) -> TestClient:
    app = FastAPI()
    app.state.router = router
    app.state.store = store
    app.state.lrt_planner = planner
    if provider:
        app.state.hazard_provider = HazardProvider(
            store, router.network, background=False
        )
    app.include_router(routing_api.router)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def get(client: TestClient, profile: str = "wheelchair", **params: str) -> Any:
    response = client.get(
        "/api/route",
        params={
            "from": "{},{}".format(*at(0)),
            "to": "{},{}".format(*at(60)),
            "profile": profile,
            **params,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    body.pop("generated_at")
    return body


def test_flag_off_responses_are_unchanged(router, store_with_outages) -> None:
    off = make_client(router, store_with_outages, None)
    on = make_client(router, store_with_outages, planner_for(router))
    plain = get(off)
    assert set(plain) | {"generated_at"} == RESPONSE_KEYS
    assert all(set(r) == ROUTE_KEYS for r in plain["routes"])
    assert get(off, mode="walk") == plain
    assert get(off, mode="walk_lrt") == plain  # LRT_TRIPS off: walking only
    assert get(on) == plain  # flag on, but walking asked for
    assert get(on, mode="walk") == plain
    raw = [
        c.get(
            "/api/route",
            params={"from": "{},{}".format(*at(0)), "to": "{},{}".format(*at(60))},
        ).content
        for c in (off, on)
    ]
    body = [json.loads(r) for r in raw]
    for b in body:
        b.pop("generated_at")
    assert json.dumps(body[0]) == json.dumps(body[1])  # same keys in the same order


def test_walk_lrt_adds_the_trip_with_legs_and_station_status(
    router, store_with_outages
) -> None:
    client = make_client(router, store_with_outages, planner_for(router))
    walking = get(client, mode="walk")
    body = get(client, mode="walk_lrt")
    assert body["mode"] == "walk_lrt"
    walks, trip = body["routes"][:-1], body["routes"][-1]
    assert [r["mode"] for r in walks] == ["walk"] * len(walking["routes"])
    for before, after in zip(walking["routes"], walks, strict=True):
        assert {k: v for k, v in after.items() if k != "mode"} == before
    assert trip["mode"] == "walk_lrt" and trip["rank"] == len(walks) + 1
    assert trip["eta_min"] < walks[0]["eta_min"]
    assert trip["reason"].startswith("Walk and LRT: about")
    assert [leg["kind"] for leg in trip["legs"]] == [
        "walk",
        "ride",
        "transfer",
        "ride",
        "walk",
    ]
    ride = trip["legs"][1]
    assert ride["lines"] == ["Red Line"] and ride["line_colors"] == ["#FF0000"]
    assert ride["wait"]["estimate"] is True and ride["wait"]["minutes"] == 5.0
    assert ride["wait"]["note"].startswith("Estimate:")
    assert trip["lrt"]["wait_is_estimate"] is True
    assert trip["lrt"]["maps_note"] and trip["lrt"]["attribution"]
    assert "travelmode=transit" in trip["google_maps_url"]
    assert "dirflg=r" in trip["apple_maps_url"]
    s1 = trip["lrt"]["stations"][0]
    assert s1["name"] == "S1 Station" and s1["usable"] is True
    status = {d["device_id"]: d["status"] for d in s1["devices"]}
    assert status == {
        "10": "in_service",
        "11": "out_of_service",
        "12": "in_service",
        "13": "in_service",
    }
    # The report lists the station devices on the trip, under Indoor.
    indoor = next(g for g in trip["report"]["groups"] if g["category"] == "indoor")
    items = {item["id"]: item for item in indoor["items"]}
    assert set(items) == {"city_lrt_devices:11", "city_lrt_devices:31"}
    assert all(item["status"] == "on_route" for item in items.values())
    assert items["city_lrt_devices:11"]["note"] == (
        "At S1 Station, which this trip uses. "
        "Use an entrance with a working elevator: South."
    )


def test_walk_lrt_report_explains_an_avoided_station(router, store) -> None:
    store.upsert([device_item("30", "S3, Platform", lng=LNG0 + 59 * DLNG)], NOW)
    client = make_client(router, store, planner_for(router))
    wheelchair = get(client, mode="walk_lrt")
    assert all(r["mode"] == "walk" for r in wheelchair["routes"])  # no trip
    assert any("S3 Station is not used" in w for w in wheelchair["warnings"])
    standard = get(client, "standard", mode="walk_lrt")
    trip = standard["routes"][-1]
    assert trip["mode"] == "walk_lrt"
    indoor = next(g for g in trip["report"]["groups"] if g["category"] == "indoor")
    assert [i["id"] for i in indoor["items"]] == ["city_lrt_devices:30"]


def test_walk_lrt_reads_the_store_without_a_hazard_provider(
    router, store_with_outages
) -> None:
    client = make_client(
        router, store_with_outages, planner_for(router), provider=False
    )
    trip = get(client, mode="walk_lrt")["routes"][-1]
    s1 = trip["lrt"]["stations"][0]
    assert {d["device_id"]: d["status"] for d in s1["devices"]}["11"] == (
        "out_of_service"
    )


def test_unknown_mode_is_rejected(router, store) -> None:
    client = make_client(router, store, None)
    response = client.get(
        "/api/route",
        params={
            "from": "{},{}".format(*at(0)),
            "to": "{},{}".format(*at(60)),
            "mode": "bike",
        },
    )
    assert response.status_code == 422


def test_a_planner_failure_keeps_the_walking_routes(router, store) -> None:
    class Broken(LrtPlanner):
        def plan(self, **_: Any):  # type: ignore[override]
            raise RuntimeError("boom")

    broken = Broken(router, LrtSchedule.from_document(schedule_doc()))
    body = get(make_client(router, store, broken), mode="walk_lrt")
    assert body["routes"] and all(r["mode"] == "walk" for r in body["routes"])
    assert "Walk and LRT trips are unavailable right now." in body["warnings"]


# ------------------------------------------------------------ app and flag


def test_flag_parsing() -> None:
    assert lrt.enabled("true") and lrt.enabled("1") and lrt.enabled(" ON ")
    assert not lrt.enabled(None) and not lrt.enabled("") and not lrt.enabled("false")


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    for name in ("DATA_MODE", "REPLAY_SNAPSHOT", "REPLAY_AT", "LRT_TRIPS"):
        monkeypatch.delenv(name, raising=False)
    yield monkeypatch


needs_network = pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)


@needs_network
def test_the_app_loads_the_planner_only_with_the_flag(app_env) -> None:
    from cloud.app.main import create_app

    with TestClient(create_app()) as client:
        assert client.app.state.lrt_planner is None
    app_env.setenv("LRT_TRIPS", "true")
    with TestClient(create_app()) as client:
        planner = client.app.state.lrt_planner
        assert planner is not None and len(planner.schedule.stations) >= 25
        # Station entry nodes are found at startup, near every station.
        assert len(planner.entries) == len(planner.schedule.stations)


CHURCHILL = "53.5444,-113.4909"
SOUTHGATE = "53.4853,-113.5169"


def city_item(device_id: str, detail: str, kind: str, lat: float, lng: float):
    item = device_item(device_id, detail, kind)
    return item.model_copy(
        update={
            "geometry": item.geometry.model_copy(update={"coordinates": [lng, lat]})
        }
    )


@needs_network
def test_churchill_to_southgate_by_wheelchair_on_the_real_network(app_env) -> None:
    from cloud.app.main import create_app

    app_env.setenv("LRT_TRIPS", "true")
    with TestClient(create_app()) as client:
        client.app.dependency_overrides[get_now] = lambda: NOW
        client.app.state.store.upsert(
            [
                city_item(
                    "119",
                    "Southgate, East Entrance",
                    "escalator_out",
                    53.485344,
                    -113.516892,
                ),
                city_item(
                    "1", "Churchill, Chancery", "elevator_out", 53.54442, -113.489045
                ),
            ],
            NOW,
        )
        params = {"from": CHURCHILL, "to": SOUTHGATE, "mode": "walk_lrt"}
        body = client.get("/api/route", params={**params, "profile": "wheelchair"})
        assert body.status_code == 200, body.text
        routes = body.json()["routes"]
        trip = routes[-1]
        assert trip["mode"] == "walk_lrt", body.json()["warnings"]
        assert trip["eta_min"] < routes[0]["eta_min"]
        rides = [leg for leg in trip["legs"] if leg["kind"] == "ride"]
        assert len(rides) == 1 and rides[0]["lines"] == ["Capital Line"]
        assert rides[0]["from_name"] == "Churchill Station"
        assert rides[0]["to_name"] == "Southgate Station"
        assert rides[0]["wait"]["estimate"] is True
        indoor = next(g for g in trip["report"]["groups"] if g["category"] == "indoor")
        found = {i["id"]: i for i in indoor["items"]}
        assert {"city_lrt_devices:1", "city_lrt_devices:119"} <= set(found)
        assert "working elevator" in found["city_lrt_devices:1"]["note"]

        # Southgate's platform elevator out: no step-free trip ends there, and
        # no other station is within 1.2 km of the destination.
        client.app.state.store.upsert(
            [
                city_item(
                    "114", "Southgate, Platform", "elevator_out", 53.485344, -113.516892
                )
            ],
            NOW,
        )
        client.app.state.hazard_provider.base.refresh(NOW)
        wheelchair = client.get(
            "/api/route", params={**params, "profile": "wheelchair"}
        ).json()
        assert all(r["mode"] == "walk" for r in wheelchair["routes"])
        assert any("Southgate Station is not used" in w for w in wheelchair["warnings"])
        standard = client.get(
            "/api/route", params={**params, "profile": "standard"}
        ).json()
        assert standard["routes"][-1]["mode"] == "walk_lrt"
