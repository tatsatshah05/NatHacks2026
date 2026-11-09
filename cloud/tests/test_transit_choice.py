"""OP-420: choose walking, walking and transit, or transit only (issue #199).

``GET /api/route?mode=walk_lrt`` takes ``transit=faster|always|only``. The tests
run on test_lrt's synthetic street and ladder with test_bus's synthetic bus lines,
so every time is checkable by hand: 99.3 m between nodes, stops and stations 20 m
north of the street (199.4 m from the node two steps away), 1.3 m/s for the
standard profile and 1.0 m/s for a wheelchair, 30 s at a bus stop and 120 s at an
elevator station each end, and a wait of half the headway (buses every 15 min:
225 s; the two patterns of the 8 combined; trains every 10 min: 300 s).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cloud.app import lrt, routing_api
from cloud.app.lrt import LrtPlan, LrtPlanner, LrtSchedule, trip_reason
from cloud.app.profiles import load_routing_config
from cloud.app.routing import Router, RouteResult
from cloud.app.store import ObstructionStore
from cloud.tests.test_bus import bus_doc, far_lrt_doc
from cloud.tests.test_lrt import (
    DLNG,
    LAT0,
    LNG0,
    NORTH,
    NOW,
    at,
    hours,
    ladder,
    make_client,
    schedule_doc,
    street,
)

ASKED = "shown because you asked for transit"
NO_TRANSIT = routing_api.NO_TRANSIT_WARNING
WALKING_FASTER = (
    "Walking is faster than any walk and LRT or bus trip between these points."
)
TransitValue = lrt.TransitChoice


@pytest.fixture(scope="module")
def router() -> Router:
    return Router(street(), load_routing_config())


def planner(
    router: Router, lrt_doc: dict[str, Any], bus: dict[str, Any] | None
) -> LrtPlanner:
    return LrtPlanner(router, LrtSchedule.from_document(lrt_doc, bus))


def slow_bus(router: Router) -> LrtPlanner:
    """Route 8 along Main Street, 15 min between stops, no LRT in reach: from node
    0 to node 18 it loses to walking (22.9 min on foot at 1.3 m/s)."""
    return planner(router, far_lrt_doc(), bus_doc(ride_s=900))


def plan(
    router: Router,
    planner_: LrtPlanner,
    profile: str,
    start: int,
    end: int,
    transit: TransitValue,
) -> tuple[LrtPlan, RouteResult]:
    walk = router.route(*at(start), *at(end), profile)
    result = planner_.plan(
        origin_node=walk.origin[0],
        destination_node=walk.destination[0],
        profile=router.config.profile(profile),
        hazards={},
        outages=[],
        now=NOW,
        walk_eta_min=walk.routes[0].eta_min,
        transit=transit,
    )
    return result, walk


def params(start: int, end: int, profile: str, **extra: str) -> dict[str, str]:
    return {
        "from": "{},{}".format(*at(start)),
        "to": "{},{}".format(*at(end)),
        "profile": profile,
        **extra,
    }


def get(
    client: TestClient, start: int, end: int, profile: str = "standard", **extra: str
) -> dict[str, Any]:
    response = client.get("/api/route", params=params(start, end, profile, **extra))
    assert response.status_code == 200, response.text
    body = response.json()
    body.pop("generated_at")
    return body


def raw(
    client: TestClient, start: int, end: int, profile: str = "standard", **extra: str
) -> str:
    """The body as sent, minus the timestamp: key order counts."""
    response = client.get("/api/route", params=params(start, end, profile, **extra))
    assert response.status_code == 200, response.text
    body = json.loads(response.content)
    body.pop("generated_at")
    return json.dumps(body)


def without(route: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in route.items() if k not in keys}


# ---------------------------------------------------------------- the planner


def test_the_reason_against_walking() -> None:
    faster = "Walk and bus: about 6 min faster than walking"
    assert trip_reason("Walk and bus", 20.0, 26.4) == faster
    # Strictly faster keeps the wording from before OP-420, even by under a minute.
    assert trip_reason("Walk and bus", 20.0, 20.3) == (
        "Walk and bus: about 0 min faster than walking"
    )
    assert trip_reason("Walk and bus", 26.4, 20.0) == (
        f"Walk and bus: about 6 min slower than walking; {ASKED}"
    )
    same = f"Walk and LRT: about the same time as walking; {ASKED}"
    assert trip_reason("Walk and LRT", 20.0, 20.0) == same
    assert trip_reason("Walk and LRT", 20.4, 20.0) == same
    assert trip_reason("Walk and LRT", 20.5, 20.0) == same  # rounds half to even
    assert trip_reason("Walk and LRT", 20.6, 20.0) == (
        f"Walk and LRT: about 1 min slower than walking; {ASKED}"
    )
    assert trip_reason("Walk and LRT", 20.0, None) == (
        "Walk and LRT: no walking route was found"
    )


def test_one_search_three_policies_on_a_slower_bus(router: Router) -> None:
    """Node 0 to node 18 on foot: 1,787 m, 22.9 min. By the bus: 153.4 s to stop
    1001 (199.4 m), 30 s, a 225 s wait, the 900 s ride, 30 s and 153.4 s from stop
    1002: 1,491.8 s, 24.9 min, so about 2 min slower."""
    p = slow_bus(router)
    faster, walk = plan(router, p, "standard", 0, 18, "faster")
    assert walk.routes[0].eta_min == 22.9
    assert faster.trips == [] and faster.trip is None
    assert faster.warnings == [WALKING_FASTER]

    always, _ = plan(router, p, "standard", 0, 18, "always")
    trip = always.trip
    assert trip is not None and always.trips == [trip]
    assert trip.total_s == pytest.approx(1491.8, abs=0.1)
    assert trip.route.eta_min == 24.9
    assert trip.reason == f"Walk and bus: about 2 min slower than walking; {ASKED}"
    assert [leg.kind for leg in trip.legs] == ["walk", "ride", "walk"]
    assert always.warnings == []  # the reason says it; no "walking is faster"

    only, _ = plan(router, p, "standard", 0, 18, "only")
    assert len(only.trips) == 1 and only.trip is only.trips[0]
    # The same trip, found and described the same way under every policy.
    assert only.trips[0].route == trip.route
    assert lrt.legs_out(only.trips[0]) == lrt.legs_out(trip)
    assert only.warnings == []


def test_a_slower_lrt_trip_says_lrt(router: Router) -> None:
    """LRT only (no buses), the Blue line slowed to 100 min between X and S3: the
    trip is 124.4 min against 99.3 min on foot by wheelchair."""
    doc = schedule_doc()
    doc["lines"][1]["ride_s"] = [6000]
    always, walk = plan(
        router, planner(router, doc, None), "wheelchair", 0, 60, "always"
    )
    assert walk.routes[0].eta_min == 99.3
    assert always.trip is not None and always.trip.route.eta_min == 124.4
    assert always.trip.reason == (
        f"Walk and LRT: about 25 min slower than walking; {ASKED}"
    )
    faster, _ = plan(router, planner(router, doc, None), "wheelchair", 0, 60, "faster")
    assert faster.trip is None
    assert faster.warnings == [
        "Walking is faster than any walk and LRT trip between these points."
    ]


def test_only_lists_distinct_trips_fastest_first(router: Router) -> None:
    """Node 0 to node 60 by the standard profile (76.4 min on foot): bus 8 from
    stop 1001 to stop 1004 (1,491.8 s) and the Red then Blue line from S1 to S3
    with a change at X (77.9 s walk, 120 s, 300 s wait, 240 s ride, 180 s change,
    300 s wait, 180 s ride, 120 s, 77.9 s walk: 1,595.7 s)."""
    p = planner(router, schedule_doc(), bus_doc())
    only, walk = plan(router, p, "standard", 0, 60, "only")
    assert walk.routes[0].eta_min == 76.4
    rides = [trip.legs[1].ride for trip in only.trips]
    assert [ride.vehicle for ride in rides if ride] == ["bus", "lrt"]
    assert [round(trip.total_s, 1) for trip in only.trips] == [1491.8, 1595.7]
    assert [trip.reason for trip in only.trips] == [
        "Walk and bus: about 52 min faster than walking",
        "Walk and LRT: about 50 min faster than walking",
    ]
    assert only.trip is only.trips[0]
    # faster and always keep the best of the same search.
    for transit in ("faster", "always"):
        one, _ = plan(router, p, "standard", 0, 60, transit)
        assert [trip.route for trip in one.trips] == [only.trips[0].route]


def test_only_counts_the_same_line_from_another_stop_as_one_trip(
    router: Router,
) -> None:
    """A stop of the 8 added at node 5: boarding there (497 m of walking, a 750 s
    ride) is the same trip as boarding at stop 1001 (199 m, 900 s) at a worse
    time, not an alternative, so transit=only lists the 8 once, from stop 1001."""
    doc = bus_doc()
    doc["stops"].append(
        {
            "id": "1000",
            "code": "1000",
            "name": "Main Street & 5 Street",
            "lat": LAT0 + NORTH,
            "lng": LNG0 + 5 * DLNG,
        }
    )
    doc["lines"][0]["stops"] = ["1001", "1000", "1002", "1003", "1004"]
    doc["lines"][0]["ride_s"] = [150, 150, 300, 300]
    only, _ = plan(
        router, planner(router, far_lrt_doc(), doc), "standard", 0, 60, "only"
    )
    assert len(only.trips) == 1
    assert only.trips[0].legs[0].to_name == "Main Street & 2 Street (stop 1001)"
    assert only.trips[0].total_s == pytest.approx(1491.8, abs=0.1)


# ---------------------------------------------------------------------- API


def test_absent_and_faster_are_the_same_bytes(
    router: Router, store: ObstructionStore
) -> None:
    client = make_client(router, store, slow_bus(router))
    plain = raw(client, 0, 18, mode="walk_lrt")
    assert raw(client, 0, 18, mode="walk_lrt", transit="faster") == plain
    body = json.loads(plain)
    assert body["mode"] == "walk_lrt"
    assert [r["mode"] for r in body["routes"]] == ["walk"]
    assert WALKING_FASTER in body["warnings"]


def test_always_adds_the_slower_trip_after_the_walking_route(
    router: Router, store: ObstructionStore
) -> None:
    client = make_client(router, store, slow_bus(router))
    walking = get(client, 0, 18, mode="walk_lrt")
    body = get(client, 0, 18, mode="walk_lrt", transit="always")
    assert body["mode"] == "walk_lrt"
    assert [(r["rank"], r["mode"]) for r in body["routes"]] == [
        (1, "walk"),
        (2, "walk_lrt"),
    ]
    assert body["routes"][0] == walking["routes"][0]  # the walking route as it was
    trip = body["routes"][1]
    assert (walking["routes"][0]["eta_min"], trip["eta_min"]) == (22.9, 24.9)
    assert trip["reason"] == f"Walk and bus: about 2 min slower than walking; {ASKED}"
    assert [leg["kind"] for leg in trip["legs"]] == ["walk", "ride", "walk"]
    assert trip["legs"][1]["vehicle"] == "bus" and trip["legs"][1]["wait"]["estimate"]
    assert [s["name"] for s in trip["lrt"]["stations"]] == [
        "Main Street & 2 Street (stop 1001)",
        "Main Street & 20 Street (stop 1002)",
    ]
    assert "travelmode=transit" in trip["google_maps_url"]
    assert body["warnings"] == [w for w in walking["warnings"] if w != WALKING_FASTER]


def test_only_returns_the_trip_alone(router: Router, store: ObstructionStore) -> None:
    client = make_client(router, store, slow_bus(router))
    always = get(client, 0, 18, mode="walk_lrt", transit="always")
    body = get(client, 0, 18, mode="walk_lrt", transit="only")
    assert body["mode"] == "walk_lrt"
    assert body["routes"] == [{**always["routes"][1], "rank": 1}]
    assert body["warnings"] == always["warnings"]
    assert NO_TRANSIT not in body["warnings"]


def test_only_lists_up_to_three_distinct_trips(
    router: Router, store: ObstructionStore
) -> None:
    client = make_client(router, store, planner(router, schedule_doc(), bus_doc()))
    body = get(client, 0, 60, mode="walk_lrt", transit="only")
    assert [(r["rank"], r["mode"]) for r in body["routes"]] == [
        (1, "walk_lrt"),
        (2, "walk_lrt"),
    ]
    assert [r["legs"][1]["vehicle"] for r in body["routes"]] == ["bus", "lrt"]
    assert body["routes"][0]["eta_min"] < body["routes"][1]["eta_min"]
    assert all(r["reason"].endswith("min faster than walking") for r in body["routes"])
    # The default shows the best of them first and the walking route after it.
    faster = get(client, 0, 60, mode="walk_lrt")
    assert [r["mode"] for r in faster["routes"]] == ["walk_lrt", "walk"]
    assert faster["routes"][0] == body["routes"][0]


def test_always_ranks_a_slower_trip_among_three_walking_routes(
    store: ObstructionStore,
) -> None:
    """The ladder gives three walking routes by wheelchair: Main Street 99.3 min,
    North and South Street 109.3 min each. A bus with 1,852 s between stops makes
    a 104.0 min trip (2 x 199.4 s of walking, 2 x 30 s, a 225 s wait, 5,556 s of
    riding): rank 2, and South Street (the later of the tie) makes room. With
    2,400 s between stops (131.4 min) the trip is rank 3."""
    router = Router(ladder(), load_routing_config())
    walking = get(make_client(router, store, None), 0, 60, "wheelchair")["routes"]
    assert [r["eta_min"] for r in walking] == [99.3, 109.3, 109.3]

    middle = make_client(
        router, store, planner(router, far_lrt_doc(), bus_doc(ride_s=1852))
    )
    routes = get(middle, 0, 60, "wheelchair", mode="walk_lrt", transit="always")[
        "routes"
    ]
    assert [(r["rank"], r["mode"], r["eta_min"]) for r in routes] == [
        (1, "walk", 99.3),
        (2, "walk_lrt", 104.0),
        (3, "walk", 109.3),
    ]
    assert routes[1]["reason"] == (
        f"Walk and bus: about 5 min slower than walking; {ASKED}"
    )
    for kept, original in zip((routes[0], routes[2]), walking[:2], strict=True):
        assert without(kept, "mode", "rank") == without(original, "rank")

    last = make_client(
        router, store, planner(router, far_lrt_doc(), bus_doc(ride_s=2400))
    )
    routes = get(last, 0, 60, "wheelchair", mode="walk_lrt", transit="always")["routes"]
    assert [(r["rank"], r["mode"], r["eta_min"]) for r in routes] == [
        (1, "walk", 99.3),
        (2, "walk", 109.3),
        (3, "walk_lrt", 131.4),
    ]
    assert routes[2]["reason"] == (
        f"Walk and bus: about 32 min slower than walking; {ASKED}"
    )
    # The default never shows a slower trip, and the walking routes stay as they are.
    faster = get(last, 0, 60, "wheelchair", mode="walk_lrt")["routes"]
    assert [without(r, "mode") for r in faster] == walking


def test_only_falls_back_to_walking_with_a_warning_when_nothing_serves(
    router: Router, store: ObstructionStore
) -> None:
    # Stops and stations 5 km north: nothing within the 1.2 km walking limit.
    far = bus_doc()
    for s in far["stops"]:
        s["lat"] = LAT0 + 0.05
    client = make_client(router, store, planner(router, far_lrt_doc(), far))
    walking = get(client, 0, 60, mode="walk")
    body = get(client, 0, 60, mode="walk_lrt", transit="only")
    assert body["mode"] == "walk_lrt"
    assert [without(r, "mode") for r in body["routes"]] == walking["routes"]
    assert [r["mode"] for r in body["routes"]] == ["walk"]
    assert body["warnings"] == [
        *walking["warnings"],
        NO_TRANSIT,
        (
            "No LRT station or bus stop is within 1.2 km of the start on paths this "
            "profile can use."
        ),
    ]
    # Stops in reach but nothing running at this hour: the same fallback, with
    # the schedule's explanation after the warning.
    quiet = bus_doc()
    for line in quiet["lines"]:
        line["headway_s"] = {t: hours(None) for t in ("weekday", "saturday", "sunday")}
    client = make_client(router, store, planner(router, far_lrt_doc(), quiet))
    body = get(client, 0, 60, mode="walk_lrt", transit="only")
    assert [r["mode"] for r in body["routes"]] == ["walk"]
    assert body["warnings"][-2:] == [
        NO_TRANSIT,
        (
            "No LRT or bus trip fits: nothing serves the usable stations and stops near "
            "these points at this hour in the ETS schedule."
        ),
    ]


def test_transit_is_ignored_without_walk_lrt(
    router: Router, store: ObstructionStore
) -> None:
    client = make_client(router, store, slow_bus(router))
    plain = raw(client, 0, 18)
    assert "mode" not in json.loads(plain)
    for transit in ("faster", "always", "only"):
        assert raw(client, 0, 18, transit=transit) == plain
        assert raw(client, 0, 18, mode="walk", transit=transit) == plain
    # LRT_TRIPS off (no planner): walk_lrt is the walking response, whatever transit says.
    off = make_client(router, store, None)
    plain = raw(off, 0, 18)
    for transit in ("faster", "always", "only"):
        assert raw(off, 0, 18, mode="walk_lrt", transit=transit) == plain


def test_an_unknown_transit_value_is_rejected(
    router: Router, store: ObstructionStore
) -> None:
    client = make_client(router, store, slow_bus(router))
    response = client.get(
        "/api/route", params=params(0, 18, "standard", mode="walk_lrt", transit="never")
    )
    assert response.status_code == 422


def test_a_planner_failure_with_only_keeps_the_walking_routes(
    router: Router, store: ObstructionStore
) -> None:
    class Broken(LrtPlanner):
        def plan(self, **_: Any) -> LrtPlan:  # type: ignore[override]
            raise RuntimeError("boom")

    client = make_client(
        router, store, Broken(router, LrtSchedule.from_document(schedule_doc()))
    )
    body = get(client, 0, 60, mode="walk_lrt", transit="only")
    assert body["routes"] and all(r["mode"] == "walk" for r in body["routes"])
    assert body["warnings"][-2:] == [
        routing_api.TRANSIT_UNAVAILABLE_WARNING,
        NO_TRANSIT,
    ]
    always = get(client, 0, 60, mode="walk_lrt", transit="always")
    assert always["warnings"][-1] == routing_api.TRANSIT_UNAVAILABLE_WARNING
    assert NO_TRANSIT not in always["warnings"]


def test_openapi_lists_the_transit_values() -> None:
    from cloud.app.main import app

    spec = app.openapi()
    parameters = spec["paths"]["/api/route"]["get"]["parameters"]
    transit = next(p for p in parameters if p["name"] == "transit")
    assert transit["in"] == "query" and transit["required"] is False
    assert transit["schema"]["enum"] == ["faster", "always", "only"]
    assert transit["schema"]["default"] == "faster"
    assert "mode=walk_lrt" in transit["description"]
    mode = next(p for p in parameters if p["name"] == "mode")
    assert mode["schema"]["enum"] == ["walk", "walk_lrt"]  # unchanged
    # Nothing was added to the response.
    assert set(spec["components"]["schemas"]["RouteResponse"]["properties"]) == {
        "profile",
        "generated_at",
        "origin_snap_m",
        "destination_snap_m",
        "routes",
        "warnings",
        "apple_maps_note",
        "mode",
    }
