"""OP-208: the route report, on synthetic geometry laid out in metres.

    D(0,100) ------------------------- E(200,100)
      |                                    |
      |                                    |
    A(0,0) ---------- B(100,0) ---------- C(200,0)

Every edge is straight. From A to C the plain shortest route is A-B-C (200 m); the
only other way is A-D-E-C (400 m).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from statistics import quantiles
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import routing_api
from cloud.app.matching import EdgeIndex, HazardProvider, match_items
from cloud.app.network import (
    DEFAULT_NETWORK_PATH,
    RawEdge,
    RawNode,
    WalkNetwork,
    export_network,
)
from cloud.app.profiles import load_routing_config
from cloud.app.report import (
    NEAR_M,
    NOTES,
    ItemIndex,
    RouteReport,
    Status,
    build_report,
    inside,
    point_to_segment,
    segment_distance,
)
from cloud.app.routing import EdgeHazard, HazardMap, Route, Router
from cloud.app.schemas import Confidence, Effect, Obstruction
from cloud.app.store import ObstructionStore

LAT0, LNG0 = 53.54, -113.49
M_PER_DEG = 6_371_008.8 * math.pi / 180  # the network's own projection

NODES = {"A": (0, 0), "B": (100, 0), "C": (200, 0), "D": (0, 100), "E": (200, 100)}
EDGES = ["AB", "BC", "AD", "DE", "EC"]
GROUP_LABELS = [
    "Indoor",
    "Sidewalks and paths",
    "Roads and crossings",
    "Weather and ice",
]


def pos(x: float, y: float) -> list[float]:
    """Metres east and north of the origin, as a GeoJSON [lng, lat] position."""
    return [
        LNG0 + x / (M_PER_DEG * math.cos(math.radians(LAT0))),
        LAT0 + y / M_PER_DEG,
    ]


def point(x: float, y: float) -> dict[str, Any]:
    return {"type": "Point", "coordinates": pos(x, y)}


def line(*xys: tuple[float, float]) -> dict[str, Any]:
    return {"type": "LineString", "coordinates": [pos(*xy) for xy in xys]}


def square(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [pos(x0, y0), pos(x1, y0), pos(x1, y1), pos(x0, y1), pos(x0, y0)]


def build_network() -> WalkNetwork:
    names = list(NODES)
    nodes = [
        RawNode(osmid=i + 1, lat=pos(*NODES[n])[1], lng=pos(*NODES[n])[0])
        for i, n in enumerate(names)
    ]
    edges = [
        RawEdge(
            u=names.index(u) + 1,
            v=names.index(v) + 1,
            length_m=math.dist(NODES[u], NODES[v]),
            highway="footway",
            name=u + v,
            osmid=100 + k,
        )
        for k, (u, v) in enumerate(EDGES)
    ]
    return WalkNetwork.from_data(export_network(nodes, edges, source="test"))


@pytest.fixture(scope="module")
def network() -> WalkNetwork:
    return build_network()


@pytest.fixture(scope="module")
def index(network: WalkNetwork) -> EdgeIndex:
    return EdgeIndex(network)


@pytest.fixture(scope="module")
def router(network: WalkNetwork) -> Router:
    return Router(network, load_routing_config())


@pytest.fixture
def item(make_obstruction: Callable[..., Obstruction]) -> Callable[..., Obstruction]:
    """A construction-style item ("sidewalk closed", blocked) with any overrides."""

    def build(ref: str, geometry: dict[str, Any], **overrides: Any) -> Obstruction:
        data: dict[str, Any] = {
            "source": "city_construction",
            "source_ref": ref,
            "category": "sidewalk",
            "kind": "sidewalk_closed",
            "effect": "blocked",
            "title": "Sidewalk closed",
            "detail": None,
            "confidence": "high",
            "geometry": geometry,
        }
        data.update(overrides)
        return make_obstruction(**data)

    return build


def trip(
    router: Router, start: str, end: str, hazards: HazardMap, profile: str = "standard"
) -> list[Route]:
    (lng1, lat1), (lng2, lat2) = pos(*NODES[start]), pos(*NODES[end])
    return router.route(lat1, lng1, lat2, lng2, profile, hazards).routes


def report_for(
    router: Router, index: EdgeIndex, now: datetime, *items: Obstruction
) -> tuple[Route, RouteReport]:
    """Match the items, route from A to C, and report on the best route."""
    matched = match_items(items, index, now)
    best = trip(router, "A", "C", matched.hazards)[0]
    return best, build_report(best, [matched.items])


def listed(report: RouteReport) -> dict[str, tuple[str, int | None]]:
    """Item ID -> (status, distance in whole metres).

    Whole metres, because route polylines are float32 rounded to 6 decimal places
    (about 0.1 m), so a distance of 20 m comes back as 19.9 or 20.1.
    """
    return {
        e.record.id: (
            e.status.value,
            None if e.distance_m is None else round(e.distance_m),
        )
        for g in report.groups
        for e in g.entries
    }


def ids_by_group(report: RouteReport) -> dict[str, list[str]]:
    return {g.label: [e.record.id for e in g.entries] for g in report.groups}


# ---------------------------------------------------------------- the report


def test_items_on_and_near_the_route_are_grouped_by_category(
    router, index, item, now
) -> None:
    road = item(
        "7",
        line((110, -5), (190, -5)),  # along B-C, 5 m south: matched to B-C
        source="city_traffic_disruptions",
        category="road",
        kind="road_closed",
        effect="caution",
        confidence="medium",
        title="Road closed",
        detail="102 Avenue between 100 Street and 101 Street",
        url="https://data.edmonton.ca/d/k4tx-5k8p",
    )
    trail = item(
        "trail-1",
        point(50, 40),  # 40 m north of A-B: too far to snap, but within 50 m
        source="city_trails",
        kind="trail_caution",
        effect="caution",
    )
    elevator = item(
        "101",
        point(150, 20),  # a station 20 m from the route
        source="city_lrt_devices",
        category="indoor",
        kind="elevator_out",
        title="Elevator out of service",
    )
    advisory = item(
        "nbhd-5",
        point(100, -10),
        source="city_311_snow",
        kind="snow_complaints",
        effect="caution",
        confidence="low",
        title="Snow on sidewalks reported",
    )
    watch = item(
        "city",
        {"type": "Polygon", "coordinates": [square(-50, -50, 250, 150)]},
        source="weather_watch",
        category="weather",
        kind="ice_risk",
        effect="caution",
        confidence="low",
        title="Ice risk",
    )
    zone = item(
        "zone",
        {"type": "Polygon", "coordinates": [square(80, -20, 120, 20)]},  # around B
        category="road",
        kind="road_closed",
        effect="caution",
        title="Road work zone",
    )
    far = item("far", point(100, -70), category="road", kind="road_closed")

    best, report = report_for(
        router, index, now, road, trail, elevator, advisory, watch, zone, far
    )
    assert best.distance_m == 200  # nothing blocks A-B-C

    assert [g.label for g in report.groups] == GROUP_LABELS
    assert ids_by_group(report) == {
        "Indoor": ["city_lrt_devices:101"],
        "Sidewalks and paths": ["city_311_snow:nbhd-5", "city_trails:trail-1"],
        "Roads and crossings": [
            "city_construction:zone",
            "city_traffic_disruptions:7",
        ],
        "Weather and ice": ["weather_watch:city"],
    }
    assert listed(report) == {
        "city_construction:zone": ("on_route", 0),  # the route passes through it
        "city_traffic_disruptions:7": ("on_route", 5),  # matched to B-C
        "weather_watch:city": ("on_route", 0),  # city-wide
        "city_lrt_devices:101": ("nearby", 20),
        "city_311_snow:nbhd-5": ("nearby", 10),
        "city_trails:trail-1": ("nearby", 40),
    }
    assert (report.count(Status.ON_ROUTE), report.count(Status.NEARBY)) == (3, 3)

    # Each item keeps what a person needs to judge it.
    records = {e.record.id: e.record for g in report.groups for e in g.entries}
    entry = records["city_traffic_disruptions:7"]
    assert (entry.source, entry.title, entry.detail) == (
        "city_traffic_disruptions",
        "Road closed",
        "102 Avenue between 100 Street and 101 Street",
    )
    assert entry.confidence == Confidence.MEDIUM and entry.observed_at == now
    assert entry.url == "https://data.edmonton.ca/d/k4tx-5k8p"
    assert entry.attribution == "City of Edmonton Open Data"
    # Items that cannot change routes say why.
    notes = {item_id: record.note for item_id, record in records.items()}
    assert notes["city_lrt_devices:101"] == NOTES["station"]
    assert notes["city_311_snow:nbhd-5"] == NOTES["area"]
    assert notes["city_construction:zone"] == NOTES["area"]
    assert notes["weather_watch:city"] == NOTES["citywide"]
    assert notes["city_trails:trail-1"] == NOTES["unmatched"]
    assert notes["city_traffic_disruptions:7"] is None


def test_avoided_items_are_listed_as_avoided(router, index, item, now) -> None:
    near = item("1", point(20, -10))  # on A-B, 22 m from the detour at A
    far = item("2", point(150, -10))  # on B-C, 51 m from the detour at C
    best, report = report_for(router, index, now, near, far)
    assert best.distance_m == 400  # A-D-E-C, around both closures
    # Avoided wins over nearby, and keeps its distance when it has one.
    assert listed(report) == {
        "city_construction:1": ("avoided", 22),  # sqrt(20^2 + 10^2) = 22.4 m
        "city_construction:2": ("avoided", None),
    }
    assert ids_by_group(report)["Sidewalks and paths"] == [
        "city_construction:1",
        "city_construction:2",
    ]


@pytest.mark.parametrize(
    ("south_m", "listed_"), [(NEAR_M - 1, True), (NEAR_M + 1, False)]
)
def test_only_items_within_50_m_are_listed(
    router, index, item, now, south_m, listed_
) -> None:
    lane = item("x", point(100, -south_m), category="road", kind="road_closed")
    _, report = report_for(router, index, now, lane)
    found = listed(report)
    assert ("city_construction:x" in found) is listed_
    if listed_:
        assert found["city_construction:x"] == ("nearby", NEAR_M - 1)


def test_order_inside_a_group(router, index, item, now) -> None:
    """On the route, then nearby, then avoided; blocked before caution; then nearest."""
    avoided = item("avoided", point(20, -10))  # blocks A-B, so the route is A-D-E-C
    on_route = item("on", point(100, 105), effect="caution", kind="trail_caution")
    blocked_40 = item("blocked-40", point(100, 60))  # 40 m from D-E, not snapped
    caution_35 = item(
        "caution-35", point(100, 135), effect="caution", kind="trail_caution"
    )
    _, report = report_for(
        router, index, now, caution_35, avoided, blocked_40, on_route
    )
    assert ids_by_group(report)["Sidewalks and paths"] == [
        "city_construction:on",
        "city_construction:blocked-40",
        "city_construction:caution-35",
        "city_construction:avoided",
    ]


def test_hazards_without_a_record_are_still_listed(router, network) -> None:
    """A provider can add hazards the matcher never saw (OP-205 camera ice risks)."""
    ab, bc = (edge_of(network, name) for name in ("AB", "BC"))
    hazards = {
        ab: [EdgeHazard("x:1", Effect.BLOCKED, Confidence.HIGH)],
        bc: [
            EdgeHazard(
                "camera_model:cam-7", Effect.ICE_RISK, Confidence.MEDIUM, "Ice risk"
            )
        ],
    }
    best = trip(router, "A", "C", hazards)[0]
    report = build_report(best)  # no index has these items
    groups = {g.label: g.entries for g in report.groups}
    (blocked,) = groups["Sidewalks and paths"]
    assert (blocked.status, blocked.record.title, blocked.record.kind) == (
        Status.AVOIDED,
        "Blocked",
        None,
    )
    (ice,) = groups["Weather and ice"]
    assert ice.record.source == "camera_model" and ice.record.kind == "ice_risk"
    assert ice.status == Status.AVOIDED and ice.distance_m is None


def watch_item(
    item: Callable[..., Obstruction],
    ref: str,
    geometry: dict[str, Any],
    **overrides: Any,
) -> Obstruction:
    """The black-ice watch as the weather_watch source will publish it."""
    return item(
        ref,
        geometry,
        source="weather_watch",
        category="weather",
        kind="ice_risk",
        effect="caution",
        confidence="low",
        title="Ice risk watch",
        **overrides,
    )


def test_weather_areas_are_on_every_route(router, network, index, item, now) -> None:
    """The city-wide watch is listed on every route, wherever its geometry sits."""
    lng0, lat0, lng1, lat1 = network.bbox
    bbox = [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]
    watch = watch_item(item, "bbox", {"type": "Polygon", "coordinates": [bbox]})
    far_point = watch_item(item, "point", point(100, 5000))  # 5 km away
    advisory = item(  # an area too, but not weather: only within 50 m
        "nbhd-9", point(100, -70), source="city_311_snow", kind="snow_complaints"
    )
    matched = match_items([watch, far_point, advisory], index, now)
    assert matched.hazards == {}  # areas never change routes
    routes = trip(router, "A", "C", matched.hazards)
    assert len(routes) == 2  # A-B-C and A-D-E-C
    for route in routes:
        report = build_report(route, [matched.items])
        assert ids_by_group(report)["Weather and ice"] == [
            "weather_watch:bbox",
            "weather_watch:point",
        ]
        assert listed(report) == {
            "weather_watch:bbox": ("on_route", 0),  # inside the network's box
            "weather_watch:point": ("on_route", None),
        }


def test_records_can_come_from_another_index(router, network, index, item, now) -> None:
    """OP-205's camera ice risks are full Obstructions that never pass the store."""
    camera = item(
        "cam-7:ice_risk",
        point(100, 100),  # on D-E
        source="camera_model",
        category="weather",
        kind="ice_risk",
        effect="ice_risk",
        confidence="medium",
        title="Ice risk at the crossing",
        detail="Camera reads ice; air -4 °C",
        url="https://511.alberta.ca/",
        attribution="511 Alberta",
    )
    other = item(
        "cam-8:ice_risk",
        point(100, 60),  # 40 m from D-E; its crossings are not on this route
        source="camera_model",
        category="weather",
        kind="ice_risk",
        effect="ice_risk",
        confidence="medium",
        title="Ice risk at the crossing",
    )
    cameras = ItemIndex.build([camera, other], index.to_xy)  # no matcher methods
    hazards = {
        edge_of(network, "DE"): [
            EdgeHazard(camera.id, camera.effect, camera.confidence, camera.title)
        ]
    }
    matched = match_items([], index, now)
    route = next(
        r for r in trip(router, "A", "C", hazards) if edge_of(network, "DE") in r.edges
    )
    report = build_report(route, [matched.items, cameras])
    (on, near) = {g.label: g.entries for g in report.groups}["Weather and ice"]
    assert (on.record.id, on.status) == (camera.id, Status.ON_ROUTE)
    assert (on.record.observed_at, on.record.attribution, on.record.detail) == (
        now,
        "511 Alberta",
        "Camera reads ice; air -4 °C",
    )
    assert on.record.url == "https://511.alberta.ca/" and on.record.note is None
    # Local camera items are not city-wide: listed only near the route.
    assert (near.record.id, near.status) == (other.id, Status.NEARBY)
    assert near.distance_m is not None and round(near.distance_m) == 40


def test_transit_appears_only_with_items(router, index, item, now) -> None:
    _, empty = report_for(router, index, now)
    assert [g.label for g in empty.groups] == GROUP_LABELS
    assert all(not g.entries for g in empty.groups)
    trains = item(
        "t", point(100, 10), category="transit", kind="blocked", effect="caution"
    )
    _, report = report_for(router, index, now, trains)
    assert [g.label for g in report.groups] == [*GROUP_LABELS, "Transit"]


def edge_of(network: WalkNetwork, name: str) -> int:
    names = list(NODES)
    ends = {names.index(name[0]), names.index(name[1])}
    for edge in range(network.edge_count):
        if {int(network.edge_u[edge]), int(network.edge_v[edge])} == ends:
            return edge
    raise KeyError(name)


# ---------------------------------------------------------------- geometry


def xy(*points: tuple[float, float]) -> np.ndarray:
    return np.asarray(points, dtype=float)


def test_segment_distance() -> None:
    a1, b1 = xy((0, 0), (0, 0), (0, 0), (0, 0)), xy((10, 0), (10, 0), (10, 0), (10, 0))
    a2 = xy((5, -5), (0, 3), (13, 0), (12, 4))
    b2 = xy((5, 5), (10, 3), (20, 0), (12, 4))
    # Crossing, parallel 3 m apart, collinear with a 3 m gap, and a point 2 m past an end.
    assert segment_distance(a1, b1, a2, b2) == pytest.approx(
        [0, 3, 3, math.hypot(2, 4)]
    )
    # A zero-length segment is a point.
    assert point_to_segment(xy((3, 4)), xy((0, 0)), xy((0, 0))) == pytest.approx([5])


def test_inside_respects_holes() -> None:
    outer = xy((0, 0), (100, 0), (100, 100), (0, 100), (0, 0))
    hole = xy((40, 40), (60, 40), (60, 60), (40, 60), (40, 40))
    points = xy((10, 10), (50, 50), (150, 50))
    assert inside(points, [outer, hole]).tolist() == [True, False, False]


# --------------------------------------------------------------------- API


def live_item(item: Callable[..., Obstruction], *args: Any, **kw: Any) -> Obstruction:
    """An item active at the real clock, which ``/api/route`` uses."""
    real_now = datetime.now(UTC)
    return item(
        *args,
        starts_at=None,
        observed_at=real_now - timedelta(minutes=5),
        expires_at=real_now + timedelta(hours=1),
        **kw,
    )


@pytest.fixture
def app(router: Router) -> FastAPI:
    app = FastAPI()
    app.state.router = router
    app.include_router(routing_api.router)
    return app


def get_route(app: FastAPI) -> dict[str, Any]:
    (lng1, lat1), (lng2, lat2) = pos(*NODES["A"]), pos(*NODES["C"])
    response = TestClient(app).get(
        "/api/route",
        params={
            "from": f"{lat1},{lng1}",
            "to": f"{lat2},{lng2}",
            "profile": "wheelchair",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_route_endpoint_includes_the_report(
    app, network, store: ObstructionStore, item
) -> None:
    closed = live_item(item, "1", point(20, -10), detail="North side of 102 Avenue")
    lng0, lat0, lng1, lat1 = network.bbox
    bbox = [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]
    watch = live_item(
        watch_item, item, "active", {"type": "Polygon", "coordinates": [bbox]}
    )
    store.upsert([closed, watch], datetime.now(UTC))
    app.state.hazard_provider = HazardProvider(store, network)

    body = get_route(app)
    best = body["routes"][0]
    report = best["report"]
    assert [g["label"] for g in report["groups"]] == GROUP_LABELS
    assert [g["category"] for g in report["groups"]] == [
        "indoor",
        "sidewalk",
        "road",
        "weather",
    ]
    assert (report["on_route"], report["nearby"], report["avoided"]) == (1, 0, 1)
    (avoided,) = report["groups"][1]["items"]
    assert avoided.pop("distance_m") == pytest.approx(22.4, abs=0.2)
    assert avoided == {
        "id": "city_construction:1",
        "status": "avoided",
        "kind": "sidewalk_closed",
        "effect": "blocked",
        "confidence": "high",
        "title": "Sidewalk closed",
        "detail": "North side of 102 Avenue",
        "source": "city_construction",
        "attribution": "City of Edmonton Open Data",
        "observed_at": closed.observed_at.isoformat().replace("+00:00", "Z"),
        "starts_at": None,
        "ends_at": None,
        "url": "https://data.edmonton.ca/d/snws-u3zx",
        "note": None,
    }
    # The black-ice watch is city-wide: on every route, changing none.
    (ice,) = report["groups"][3]["items"]
    assert (ice["id"], ice["status"], ice["title"]) == (
        "weather_watch:active",
        "on_route",
        "Ice risk watch",
    )
    assert ice["note"] == NOTES["citywide"]
    assert all(r["report"]["on_route"] >= 1 for r in body["routes"])


def test_route_endpoint_without_a_matcher_reports_routing_hazards(app, network) -> None:
    app.state.hazard_provider = lambda now: {
        edge_of(network, "AB"): [
            EdgeHazard(
                "city_lrt_devices:9", Effect.BLOCKED, Confidence.HIGH, "Elevator out"
            )
        ]
    }
    report = get_route(app)["routes"][0]["report"]
    assert report["avoided"] == 1
    assert report["groups"][1]["items"][0]["title"] == "Elevator out"


# ------------------------------------------------------------- the real city


@pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)
def test_city_reports_are_fast(make_obstruction, now) -> None:
    """3,000 items around downtown: the index builds once per match, reports stay quick."""
    city = WalkNetwork.load()
    rng = np.random.default_rng(208)
    items = []
    for i in range(3000):
        lat, lng = 53.535 + rng.random() * 0.02, -113.52 + rng.random() * 0.04
        if i % 2:
            geometry = {"type": "Point", "coordinates": [lng, lat]}
        else:
            geometry = {
                "type": "LineString",
                "coordinates": [[lng, lat], [lng + 0.002, lat + 0.0005 * rng.random()]],
            }
        items.append(
            make_obstruction(
                source="city_construction",
                source_ref=str(i),
                category="sidewalk",
                kind="sidewalk_closed",
                effect="caution",
                title="Sidewalk work",
                geometry=geometry,
            )
        )
    edges = EdgeIndex(city)
    started = time.perf_counter()
    matched = match_items(items, edges, now)  # builds the report's ItemIndex too
    match_s = time.perf_counter() - started
    assert matched.items is not None and len(matched.items.records) == 3000

    router = Router(city, load_routing_config())
    churchill, rogers = (53.5444, -113.4909), (53.5468, -113.4973)
    routes = router.route(*churchill, *rogers, "wheelchair", matched.hazards).routes
    timings = []
    for _ in range(10):
        started = time.perf_counter()
        reports = [build_report(r, [matched.items]) for r in routes]
        timings.append(time.perf_counter() - started)
    assert all(r.count(Status.ON_ROUTE) + r.count(Status.NEARBY) for r in reports)
    p95 = quantiles(timings, n=20)[-1]
    print(
        f"\nmatch_items {match_s * 1000:.0f} ms; report for {len(routes)} routes "
        f"p50={sorted(timings)[len(timings) // 2] * 1000:.1f} ms p95={p95 * 1000:.1f} ms"
    )
    assert p95 < 1.0  # generous for CI runners; the measured numbers are printed
