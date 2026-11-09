"""OP-417: walk and bus trips.

GTFS reading runs on the recorded sample in ``cloud/tests/fixtures/gtfs_bus/``
(three ETS routes, rows copied verbatim) and on test_lrt's synthetic zip. Planner
and API tests use test_lrt's synthetic 6 km street with synthetic bus lines, so
every time is checkable by hand. "Flag off equals today" compares with the response
recorded before this story (``cloud/tests/fixtures/lrt/trip_response.json``). The
last tests use the committed Edmonton network, ``lrt.json`` and ``bus.json``.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import time
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cloud.app import lrt
from cloud.app.lrt import LrtPlanner, LrtSchedule, bus_display
from cloud.app.lrt_gtfs import BUS_FORMAT_VERSION, GtfsError, haversine_m, read_bus
from cloud.app.network import DEFAULT_NETWORK_PATH
from cloud.app.obstructions_api import get_now
from cloud.app.profiles import load_routing_config
from cloud.app.routing import Router
from cloud.app.store import ObstructionStore
from cloud.tests.test_lrt import (
    DLNG,
    LAT0,
    LNG0,
    NORTH,
    NOW,
    at,
    device_item,
    get,
    gtfs_zip,
    hours,
    make_client,
    schedule_doc,
    street,
)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "cloud" / "tests" / "fixtures" / "gtfs_bus"
RECORDED = ROOT / "cloud" / "tests" / "fixtures" / "lrt" / "trip_response.json"
ADDITIVE_LEG_FIELDS = ("vehicle", "route_short_names")

# ------------------------------------------------------------- recorded sample


def sample_zip() -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted(SAMPLE.glob("*.txt")):
            zf.writestr(path.name, path.read_bytes())
    buffer.seek(0)
    return buffer


@pytest.fixture(scope="module")
def sample():
    return read_bus(sample_zip())


def line(graph, line_id: str):
    return next(line for line in graph.lines if line.id == line_id)


def test_sample_reads_one_line_per_stop_pattern(sample) -> None:
    assert sample.feed["feed_version"] == "261023_1059"
    assert sample.agencies == {"1": "Edmonton Transit Service"}
    assert sample.routes == 3 and len(sample.stops) == 94
    assert [line.id for line in sample.lines] == [
        "511-0-1",
        "511-0-2",
        "511-1-1",
        "511-1-2",
        "654-0-1",
        "654-1-1",
        "920X-0-1",
        "920X-1-1",
        "920X-1-2",
    ]
    express = line(sample, "920X-0-1")
    assert express.short_name == "920X" and express.direction == "0"
    assert express.headsign == "920X University Express"  # verbatim
    assert express.color == "F37727" and express.trips == 75
    assert len(express.stops) == 8 and len(express.ride_s) == 7
    assert express.stops[0] == "8603" and express.stops[-1] == "2001"
    assert sum(express.ride_s) == 2100  # 35 min end to end, medians over 75 trips
    # Pattern 1 of a route and direction is the one with the most trips.
    assert line(sample, "920X-1-1").trips == 93 > line(sample, "920X-1-2").trips == 12
    # Stops carry the number on the sign and the GTFS name, verbatim.
    stop = next(s for s in sample.stops if s.id == "1246")
    assert (stop.code, stop.name) == ("1246", "100 Street & 102 Avenue")
    assert stop.lat == pytest.approx(53.5433, abs=0.001)


def test_sample_headways_come_from_the_first_stop_on_the_busiest_day(sample) -> None:
    # 920X toward the University on the busiest weekday (October 23, 2026) leaves
    # its first stop at 06:00, 06:30, 07:00 and then every 12 minutes to 09:24, so
    # the median gap is 30 min in the 06:00 hour and 12 min from 07:00 to 09:00.
    weekday = line(sample, "920X-0-1").headway_s["weekday"]
    assert {h: v for h, v in enumerate(weekday) if v is not None} == {
        6: 1800,
        7: 720,
        8: 720,
        9: 720,
    }
    assert all(v is None for v in line(sample, "920X-0-1").headway_s["saturday"])
    assert all(v is None for v in line(sample, "920X-0-1").headway_s["sunday"])


def test_sample_owl_route_folds_hours_after_midnight(sample) -> None:
    owl = line(sample, "511-0-1")
    assert owl.headsign == "511-Owl Downtown" and owl.trips == 24
    for day_type in ("weekday", "saturday", "sunday"):
        hourly = owl.headway_s[day_type]
        # GTFS writes its trips as 25:xx and 26:xx; they belong to hours 1 and 2.
        assert {h: v for h, v in enumerate(hourly) if v is not None} == {
            1: 1800,
            2: 1800,
        }


def test_sample_loop_route_repeats_its_stops_and_has_no_headway(sample) -> None:
    loop = line(sample, "654-1-1")
    assert loop.stops[0] == loop.stops[7] == "2851"
    assert loop.stops[1] == loop.stops[8] == "2512"
    assert len(loop.ride_s) == len(loop.stops) - 1
    # One trip a day leaves no gap between departures, so no headway at any hour.
    assert loop.trips == 3
    assert all(v is None for hourly in loop.headway_s.values() for v in hourly)


def test_read_bus_filters_agencies(sample) -> None:
    assert len(read_bus(sample_zip(), agency_ids=None).lines) == len(sample.lines)
    with pytest.raises(GtfsError, match="no bus routes"):
        read_bus(sample_zip(), agency_ids={"2"})


def test_read_bus_on_the_synthetic_lrt_feed(tmp_path: Path) -> None:
    graph = read_bus(gtfs_zip(tmp_path / "gtfs.zip"), agency_ids=None)
    assert [line.id for line in graph.lines] == ["BUS9-0-1"]
    bus = graph.lines[0]
    assert bus.stops == ["BUS1", "A1"] and bus.ride_s == [300] and bus.trips == 1
    assert bus.short_name == "9" and bus.name == "9 Line"
    assert bus.headsign == "Alpha Station"  # no trip_headsign: the last stop's name
    assert {s.id for s in graph.stops} == {"BUS1", "A1"}


# -------------------------------------------------------------- build script


def build_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "openpath_build_bus", ROOT / "scripts" / "build_bus_graph.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_writes_format_2_that_loads_beside_lrt_json(sample) -> None:
    script = build_script()
    doc = script.document(sample, source={"feed_version": "t", "attribution": "a"})
    assert doc["format_version"] == BUS_FORMAT_VERSION == 2
    assert {s["id"] for s in doc["stops"]} == {s.id for s in sample.stops}
    text = script.dumps(doc)
    assert json.loads(text) == doc
    # One row per text line: the opening of each list, a row per stop and per line.
    assert text.count("\n") == len(doc["stops"]) + len(doc["lines"]) + 3
    schedule = LrtSchedule.from_document(schedule_doc(), json.loads(text))
    assert schedule.has_bus
    express = next(line for line in schedule.lines if line.id == "920X-0-1")
    assert express.vehicle == "bus" and express.one_way
    assert (express.short_name, express.headsigns["0"]) == (
        "920X",
        "University Express",
    )
    stop = schedule.stations["1246"]
    assert stop.vehicle == "bus" and stop.access == "at_grade" and stop.devices == ()
    assert stop.title == "100 Street & 102 Avenue (stop 1246)"
    assert schedule.bus_source["feed_version"] == "t"
    # Outage matching by station name never lands on a bus stop.
    assert all(schedule.stations[s].vehicle == "lrt" for s in schedule.by_key.values())


def test_bus_doc_with_the_wrong_format_or_a_clashing_id_is_refused() -> None:
    bus = bus_doc()
    bad = {**bus, "format_version": 1}
    with pytest.raises(ValueError, match="bus.json is format 1"):
        LrtSchedule.from_document(schedule_doc(), bad)
    clash = copy.deepcopy(bus)
    clash["stops"][0]["id"] = "S1"
    with pytest.raises(ValueError, match="id of an LRT station"):
        LrtSchedule.from_document(schedule_doc(), clash)


@pytest.mark.parametrize(
    ("short", "sign", "expected"),
    [
        ("002", "2 Stadium", ("2", "Stadium")),
        ("001A", "1A Capilano Via 106 Ave", ("1A", "Capilano Via 106 Ave")),
        ("110X", "110X Downtown Express", ("110X", "Downtown Express")),
        ("002", "2-Owl Clareview", ("2", "Owl Clareview")),
        ("109", "109A Castle Downs", ("109", "109A Castle Downs")),  # kept as is
        ("509B", "Southwood", ("509B", "Southwood")),
        ("000", "", ("000", "")),
    ],
)
def test_bus_display_forms(short: str, sign: str, expected: tuple[str, str]) -> None:
    assert bus_display(short, sign) == expected


# ------------------------------------------------- synthetic street + bus lines


def stop(sid: str, i: int, name: str) -> dict[str, Any]:
    return {
        "id": sid,
        "code": sid,
        "name": name,
        "lat": LAT0 + NORTH,
        "lng": LNG0 + i * DLNG,
    }


def bus_line(
    line_id: str,
    short: str,
    headsign: str,
    stops: list[str],
    ride_s: list[int],
    headway: int | None = 900,
    direction: str = "0",
) -> dict[str, Any]:
    return {
        "id": line_id,
        "route_id": short,
        "short_name": short,
        "name": f"Route {short}",
        "direction": direction,
        "headsign": headsign,
        "color": "005087",
        "stops": stops,
        "ride_s": ride_s,
        "headway_s": {t: hours(headway) for t in ("weekday", "saturday", "sunday")},
        "trips": 10,
    }


def bus_doc(ride_s: int = 300) -> dict[str, Any]:
    """Route 8 along Main Street both ways (B1 - B2 - B3 - B4, ``ride_s`` between
    stops, every 15 min), a second pattern of the 8 that skips B3, and loop route
    650 that calls twice at B2."""
    return {
        "format_version": 2,
        "source": {"feed_version": "synthetic-bus", "attribution": "Test"},
        "stops": [
            stop("1001", 2, "Main Street & 2 Street"),
            stop("1002", 20, "Main Street & 20 Street"),
            stop("1003", 40, "Main Street & 40 Street"),
            stop("1004", 58, "Main Street & 58 Street"),
            stop("1005", 50, "Main Street & 50 Street"),
        ],
        "lines": [
            bus_line(
                "008-0-1",
                "008",
                "8 Abbottsfield",
                ["1001", "1002", "1003", "1004"],
                [ride_s] * 3,
            ),
            bus_line(
                "008-0-2",
                "008",
                "8 Abbottsfield",
                ["1001", "1002", "1004"],
                [ride_s, 2 * ride_s],
            ),
            bus_line(
                "008-1-1",
                "008",
                "8 Millgate",
                ["1004", "1003", "1002", "1001"],
                [ride_s] * 3,
                direction="1",
            ),
            bus_line(
                "650-0-1",
                "650",
                "650 Loop",
                ["1002", "1003", "1005", "1002"],
                [300, 300, 300],
                1800,
            ),
        ],
    }


def far_lrt_doc() -> dict[str, Any]:
    """An LRT schedule whose stations are 5 km north of Main Street: out of reach."""
    doc = schedule_doc()
    for station in doc["stations"]:
        station["lat"] = LAT0 + 0.05
    return doc


@pytest.fixture(scope="module")
def router() -> Router:
    return Router(street(), load_routing_config())


def planner(
    router: Router, lrt_doc: dict[str, Any], bus: dict[str, Any] | None
) -> LrtPlanner:
    return LrtPlanner(router, LrtSchedule.from_document(lrt_doc, bus))


def plan_trip(
    router: Router, planner_: LrtPlanner, profile: str, start: int = 0, end: int = 60
):
    walk = router.route(*at(start), *at(end), profile)
    return planner_.plan(
        origin_node=walk.origin[0],
        destination_node=walk.destination[0],
        profile=router.config.profile(profile),
        hazards={},
        outages=[],
        now=NOW,
        walk_eta_min=walk.routes[0].eta_min,
    ), walk


def test_walk_bus_walk_trip_on_the_synthetic_street(router) -> None:
    result, walk = plan_trip(
        router, planner(router, far_lrt_doc(), bus_doc()), "wheelchair"
    )
    trip = result.trip
    assert trip is not None, result.warnings
    assert [leg.kind for leg in trip.legs] == ["walk", "ride", "walk"]  # no transfer
    ride = trip.legs[1].ride
    assert ride is not None and ride.vehicle == "bus"
    # Both patterns of the 8 toward Abbottsfield serve B1 and B4: one choice, with
    # the frequencies combined (every 15 min each, so 7.5 min between buses).
    assert [line.id for line in ride.lines] == ["008-0-1"]
    assert ride.headsigns == ("Abbottsfield",) and ride.headway_s == 450.0
    # As for LRT lines on a shared track: the shortest scheduled ride and the fewest
    # stops among the lines combined (pattern 2 skips B3), drawn along pattern 1.
    assert ride.wait_s == 225.0 and ride.ride_s == 900.0 and ride.stops == 2
    assert ride.span == (0, 3) and len(trip.legs[1].coordinates) == 4
    assert trip.legs[1].from_name == "Main Street & 2 Street (stop 1001)"
    assert trip.legs[1].to_name == "Main Street & 58 Street (stop 1004)"
    # Time at 1 m/s: the walk from the start straight to the stop point (as OP-311
    # joins a station: 199.4 m, less than two edges and 20 m north), 30 s at the
    # stop, the 3.75 min wait, the 15 min ride, 30 s, and the same walk at the end.
    to_stop_m = haversine_m(LAT0, LNG0, LAT0 + NORTH, LNG0 + 2 * DLNG)
    assert to_stop_m == pytest.approx(199.4, abs=0.1)
    expected_s = to_stop_m + 30 + 225 + 900 + 30 + to_stop_m
    assert trip.total_s == pytest.approx(expected_s, abs=1)
    assert trip.route.eta_min < walk.routes[0].eta_min
    assert trip.reason.startswith("Walk and bus: about")
    texts = [step.text for step in trip.route.steps]
    assert (
        "Go to Main Street & 2 Street (stop 1001). Sidewalk conditions at this stop come from the walking legs."
        in texts
    )
    assert (
        "Take bus 8 toward Abbottsfield from Main Street & 2 Street (stop 1001) to "
        "Main Street & 58 Street (stop 1004): 2 stops, about 15 min scheduled; "
        "low-floor bus. Wait about 4 min (estimate from the schedule, not a live arrival)."
        in texts
    )
    assert any(
        t.startswith("Get off at Main Street & 58 Street (stop 1004).") for t in texts
    )
    assert trip.warnings[0] == lrt.BUS_WAIT_WARNING
    assert lrt.UNMAPPED_ENTRY_WARNING not in trip.warnings  # a stop is where it is
    # Every stop used is open to every profile, with the bus-stop note.
    assert all(s.usable and s.note == lrt.BUS_STOP_NOTE for s in trip.stations)
    assert all(s.station.vehicle == "bus" for s in trip.stations)


def test_a_bus_line_is_ridden_forward_only(router) -> None:
    # From the east end to the west end only the 8 toward Millgate (direction 1) fits.
    result, _ = plan_trip(
        router, planner(router, far_lrt_doc(), bus_doc()), "standard", 60, 0
    )
    ride = result.trip.legs[1].ride
    assert [line.id for line in ride.lines] == ["008-1-1"]
    assert ride.headsigns == ("Millgate",) and ride.headway_s == 900.0
    assert result.trip.legs[1].from_name.endswith("(stop 1004)")
    assert result.trip.legs[1].to_name.endswith("(stop 1001)")
    # The ride is drawn through the stops it passes, in travel order.
    coords = result.trip.legs[1].coordinates
    assert [round(c[0], 4) for c in coords] == [
        round(LNG0 + i * DLNG, 4) for i in (58, 40, 20, 2)
    ]


def test_a_loop_route_takes_the_shortest_ride_forward(router) -> None:
    doc = bus_doc()
    doc["lines"] = [doc["lines"][3]]  # only the 650 loop: B2 - B3 - B5 - B2
    # From B5 (node 50) back to B2 (node 20): one stop forward around the loop,
    # not three stops backwards.
    result, _ = plan_trip(
        router, planner(router, far_lrt_doc(), doc), "standard", 50, 20
    )
    ride = result.trip.legs[1].ride
    assert ride.stops == 1 and ride.ride_s == 300.0 and ride.span == (2, 3)
    assert [round(c[0], 4) for c in result.trip.legs[1].coordinates] == [
        round(LNG0 + i * DLNG, 4) for i in (50, 20)
    ]
    # From B2 to B5: two stops forward from the loop's first call at B2.
    result, _ = plan_trip(
        router, planner(router, far_lrt_doc(), doc), "standard", 20, 50
    )
    ride = result.trip.legs[1].ride
    assert ride.stops == 2 and ride.span == (0, 2)


def test_no_bus_at_this_hour_and_no_stop_in_reach_explain_themselves(router) -> None:
    quiet = bus_doc()
    for line in quiet["lines"]:
        line["headway_s"] = {t: hours(None) for t in ("weekday", "saturday", "sunday")}
    result, _ = plan_trip(router, planner(router, far_lrt_doc(), quiet), "standard")
    assert result.trip is None
    assert result.warnings == [
        (
            "No LRT or bus trip fits: nothing serves the usable stations and stops "
            "near these points at this hour in the ETS schedule."
        )
    ]
    # Stops 5 km away cannot be walked to within the 1.2 km limit.
    far = bus_doc()
    for s in far["stops"]:
        s["lat"] = LAT0 + 0.05
    result, _ = plan_trip(router, planner(router, far_lrt_doc(), far), "standard")
    assert result.warnings == [
        (
            "No LRT station or bus stop is within 1.2 km of the start on paths this "
            "profile can use."
        )
    ]


def test_walking_is_kept_when_it_beats_the_bus(router) -> None:
    # Node 0 to node 18 on foot: 1,787 m at 1.3 m/s, 22.9 min. By a bus that takes
    # 15 min between stops: two nodes' walk each end (168 s each), 30 s at each
    # stop, a 225 s wait and the 900 s ride, 25.4 min. Walking stays.
    result, walk = plan_trip(
        router, planner(router, far_lrt_doc(), bus_doc(ride_s=900)), "standard", 0, 18
    )
    assert result.trip is None and walk.routes[0].eta_min == pytest.approx(22.9, 0.01)
    assert result.warnings == [
        "Walking is faster than any walk and LRT or bus trip between these points."
    ]


def test_lrt_rides_are_unchanged_when_buses_are_on(router) -> None:
    # The LRT trip of test_lrt (S1 - X, change, X - S3: 27.4 min) beats a slow bus.
    with_bus = planner(router, schedule_doc(), bus_doc(ride_s=900))
    without = planner(router, schedule_doc(), None)
    a, _ = plan_trip(router, with_bus, "wheelchair")
    b, _ = plan_trip(router, without, "wheelchair")
    assert a.trip is not None and b.trip is not None
    assert [leg.kind for leg in a.trip.legs] == [
        "walk",
        "ride",
        "transfer",
        "ride",
        "walk",
    ]
    assert lrt.legs_out(a.trip) == lrt.legs_out(b.trip)
    assert a.trip.route == b.trip.route and a.warnings == b.warnings
    # A faster bus wins instead, and the LRT options are still evaluated first.
    fast, _ = plan_trip(
        router, planner(router, schedule_doc(), bus_doc(ride_s=120)), "wheelchair"
    )
    assert fast.trip is not None and fast.trip.legs[1].ride.vehicle == "bus"
    assert fast.trip.total_s < a.trip.total_s


# ---------------------------------------------------------------------- API


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


def strip_additive(body: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(body)
    for route in body["routes"]:
        for leg in route.get("legs") or []:
            for name in ADDITIVE_LEG_FIELDS:
                leg.pop(name, None)
    return body


def test_flag_off_equals_the_response_recorded_before_this_story(
    router, store_with_outages
) -> None:
    """TRANSIT_BUS off: the walk and LRT response is today's, plus the two additive
    leg fields. The recording was made on the code before OP-417 with the same
    synthetic street, schedule and outages (test_lrt's fixtures)."""
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    client = make_client(
        router, store_with_outages, planner(router, schedule_doc(), None)
    )
    for profile, expected in recorded.items():
        body = get(client, profile=profile, mode="walk_lrt")
        rides = [
            leg
            for route in body["routes"]
            for leg in route.get("legs") or []
            if leg["kind"] == "ride"
        ]
        assert rides and all(leg["vehicle"] == "lrt" for leg in rides)
        assert all(
            leg["route_short_names"] == leg["lines"] for leg in rides
        )  # synthetic lines have no short name
        walks = [
            leg
            for route in body["routes"]
            for leg in route.get("legs") or []
            if leg["kind"] != "ride"
        ]
        assert all(
            leg["vehicle"] is None and leg["route_short_names"] is None for leg in walks
        )
        assert strip_additive(body) == expected, profile
        assert json.dumps(strip_additive(body)) == json.dumps(
            expected
        )  # same key order


def test_bus_on_leaves_lrt_responses_identical_and_walking_unchanged(
    router, store_with_outages
) -> None:
    off = make_client(router, store_with_outages, planner(router, schedule_doc(), None))
    on = make_client(
        router, store_with_outages, planner(router, schedule_doc(), bus_doc(ride_s=900))
    )
    for profile in ("wheelchair", "walker_cane", "low_vision", "standard"):
        assert get(on, profile=profile, mode="walk_lrt") == get(
            off, profile=profile, mode="walk_lrt"
        )
        assert get(on, profile=profile) == get(off, profile=profile)
        assert get(on, profile=profile, mode="walk") == get(
            off, profile=profile, mode="walk"
        )


def test_walk_lrt_response_carries_a_bus_ride(router, store_with_outages) -> None:
    client = make_client(
        router, store_with_outages, planner(router, far_lrt_doc(), bus_doc())
    )
    body = get(client, profile="wheelchair", mode="walk_lrt")
    trip = body["routes"][0]
    assert trip["mode"] == "walk_lrt" and trip["rank"] == 1
    assert trip["reason"].startswith("Walk and bus: about")
    kinds = [leg["kind"] for leg in trip["legs"]]
    assert kinds == ["walk", "ride", "walk"]
    ride = trip["legs"][1]
    assert ride["vehicle"] == "bus" and ride["route_short_names"] == ["8"]
    assert ride["lines"] == ["Route 008"] and ride["headsigns"] == ["Abbottsfield"]
    assert (
        ride["line_colors"] == ["#005087"] and ride["stops"] == 2
    )  # pattern 2 skips B3
    assert ride["wait"]["estimate"] is True and ride["wait"]["minutes"] == 3.8
    assert ride["wait"]["note"] == lrt.BUS_WAIT_NOTE  # "between buses", not trains
    assert ride["ride_min"] == 15.0
    walk_legs = [leg for leg in trip["legs"] if leg["kind"] == "walk"]
    assert all(
        leg["vehicle"] is None and leg["route_short_names"] is None for leg in walk_legs
    )
    stops = trip["lrt"]["stations"]
    assert [s["name"] for s in stops] == [
        "Main Street & 2 Street (stop 1001)",
        "Main Street & 58 Street (stop 1004)",
    ]
    assert all(
        s["access"] == "at_grade" and s["devices"] == [] and s["usable"] for s in stops
    )
    assert all(s["note"] == lrt.BUS_STOP_NOTE for s in stops)
    texts = [step["text"] for step in trip["steps"]]
    assert any("low-floor bus" in t for t in texts)
    assert any(t.startswith("Take bus 8 toward Abbottsfield") for t in texts)
    assert lrt.BUS_WAIT_WARNING in trip["warnings"]
    assert trip["google_maps_url"].endswith("travelmode=transit")
    # The report groups are the fixed four, and the walks' escalator outage is
    # not on a bus trip (it belongs to LRT station S3, which this trip never uses).
    assert [g["category"] for g in trip["report"]["groups"]] == [
        "indoor",
        "sidewalk",
        "road",
        "weather",
    ]


def test_openapi_schema_has_the_additive_ride_fields() -> None:
    from cloud.app.main import app

    leg = app.openapi()["components"]["schemas"]["LegOut"]["properties"]
    assert set(ADDITIVE_LEG_FIELDS) <= set(leg)
    assert {"lrt", "bus"} <= set(leg["vehicle"]["anyOf"][0]["enum"])
    mode = next(
        p
        for p in app.openapi()["paths"]["/api/route"]["get"]["parameters"]
        if p["name"] == "mode"
    )
    assert mode["schema"]["enum"] == ["walk", "walk_lrt"]  # unchanged


# --------------------------------------------------------- committed data and app


def test_committed_bus_json_is_complete_small_and_quick_to_load() -> None:
    path = ROOT / "cloud" / "data" / "bus.json"
    assert path.stat().st_size < 3_000_000  # the story's target; CI refuses 5 MB
    started = time.perf_counter()
    schedule = LrtSchedule.load(lrt.DEFAULT_LRT_PATH, path)
    load_ms = (time.perf_counter() - started) * 1000
    assert load_ms < 1500, f"bus.json took {load_ms:.0f} ms to load"  # measured: 40 ms
    bus_lines = [line for line in schedule.lines if line.vehicle == "bus"]
    bus_stops = [s for s in schedule.stations.values() if s.vehicle == "bus"]
    assert len({line.id.split("-")[0] for line in bus_lines}) >= 150  # 182 ETS routes
    assert len(bus_lines) >= 400 and len(bus_stops) >= 4000  # 527 lines, 5,273 stops
    assert all(len(line.ride_s) == len(line.stations) - 1 for line in bus_lines)
    assert all(line.one_way and line.headsigns["0"] for line in bus_lines)
    # The same feed as lrt.json, so the two timetables agree on dates.
    assert schedule.bus_source["feed_version"] == schedule.source["feed_version"]
    assert schedule.bus_source["agencies"] == {"1": "Edmonton Transit Service"}
    assert schedule.bus_source["attribution"] == schedule.attribution
    assert schedule.bus_source["downloaded_at"]
    # Display forms: no zero padding, no route number repeated in the headsign.
    two = next(line for line in bus_lines if line.id.startswith("002-0-"))
    assert two.short_name == "2" and not two.headsigns["0"].startswith("2 ")
    assert len(schedule.by_key) == len(LrtSchedule.load().by_key)  # no bus stop in it


def test_load_planner_falls_back_to_lrt_when_bus_json_is_unusable(
    router, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lrt_path = tmp_path / "lrt.json"
    lrt_path.write_text(json.dumps(schedule_doc()), encoding="utf-8")
    bad = tmp_path / "bus.json"
    bad.write_text("{not json", encoding="utf-8")
    planner_ = lrt.load_planner(router, lrt_path, bus_path=bad)
    assert planner_ is not None and not planner_.schedule.has_bus
    assert any(
        "TRANSIT" in r.message and "unusable" in r.message for r in caplog.records
    )
    good = tmp_path / "good.json"
    good.write_text(json.dumps(bus_doc()), encoding="utf-8")
    planner_ = lrt.load_planner(router, lrt_path, bus_path=good)
    assert planner_ is not None and planner_.schedule.has_bus
    assert any(
        r.message.startswith("TRANSIT loaded 2 LRT lines") for r in caplog.records
    )


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    for name in (
        "DATA_MODE",
        "REPLAY_SNAPSHOT",
        "REPLAY_AT",
        "LRT_TRIPS",
        "TRANSIT_BUS",
    ):
        monkeypatch.delenv(name, raising=False)
    yield monkeypatch


needs_network = pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)

WESTMOUNT = "53.5527,-113.5445"
KINGSWAY = "53.5610,-113.5000"
# A Tuesday at 11:00 in Edmonton, the replay clock the demo uses.
TUESDAY = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


@needs_network
def test_the_app_loads_buses_only_with_both_flags(app_env) -> None:
    from cloud.app.main import create_app

    app_env.setenv("TRANSIT_BUS", "true")
    with TestClient(create_app()) as client:
        assert client.app.state.lrt_planner is None  # LRT_TRIPS off: nothing
    app_env.setenv("LRT_TRIPS", "true")
    app_env.setenv("TRANSIT_BUS", "false")
    with TestClient(create_app()) as client:
        assert not client.app.state.lrt_planner.schedule.has_bus
    app_env.setenv("TRANSIT_BUS", "true")
    with TestClient(create_app()) as client:
        planner_ = client.app.state.lrt_planner
        assert planner_.schedule.has_bus
        assert (
            sum(s.vehicle == "bus" for s in planner_.schedule.stations.values()) >= 4000
        )


@needs_network
def test_westmount_to_kingsway_by_wheelchair_rides_a_bus(app_env) -> None:
    from cloud.app.main import create_app

    app_env.setenv("LRT_TRIPS", "true")
    app_env.setenv("TRANSIT_BUS", "true")
    with TestClient(create_app()) as client:
        client.app.dependency_overrides[get_now] = lambda: TUESDAY
        params = {"from": WESTMOUNT, "to": KINGSWAY, "profile": "wheelchair"}
        started = time.perf_counter()
        body = client.get("/api/route", params={**params, "mode": "walk_lrt"})
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert body.status_code == 200, body.text
        assert elapsed_ms < 4000, f"walk_lrt took {elapsed_ms:.0f} ms"  # target < 4 s
        routes = body.json()["routes"]
        trip = routes[0]
        assert trip["mode"] == "walk_lrt", body.json()["warnings"]
        assert len(routes) <= 3 and [r["rank"] for r in routes] == list(
            range(1, len(routes) + 1)
        )
        rides = [leg for leg in trip["legs"] if leg["kind"] == "ride"]
        assert len(rides) == 1 and rides[0]["vehicle"] == "bus"
        assert rides[0]["route_short_names"] and rides[0]["headsigns"]
        assert rides[0]["from_name"].endswith(")") and "(stop " in rides[0]["from_name"]
        texts = [s["text"] for s in trip["steps"]]
        assert any(t.startswith("Take bus ") and "low-floor bus" in t for t in texts)
        walking = client.get("/api/route", params={**params, "mode": "walk"}).json()
        assert trip["eta_min"] < walking["routes"][0]["eta_min"]
        assert "legs" not in walking["routes"][0]  # walking responses are unchanged
