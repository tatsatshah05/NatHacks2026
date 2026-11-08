"""OP-206: matching obstructions to network edges, on synthetic geometry only.

The test network is laid out in metres around Edmonton, so the 30 m point radius
and the 15 m line buffer can be tested at their exact limits:

    D(0,50) ----- E(100,50) ----- F(200,50)
      |              |               |
      |              |               |
    A(0,-50) ---- B(100,-50) ---- C(200,-50) ------------------------ K(600,-50)

Every edge is straight; A-B, B-C, D-E, E-F and the verticals are 100 m long,
and C-K is 400 m.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import matching, routing_api
from cloud.app.main import create_app
from cloud.app.matching import (
    LINE_BUFFER_M,
    PIECE_M,
    POINT_RADIUS_M,
    EdgeIndex,
    HazardProvider,
    MatchResult,
    match_items,
)
from cloud.app.network import RawEdge, RawNode, WalkNetwork, export_network
from cloud.app.profiles import load_routing_config
from cloud.app.routing import Router, profile_weights
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionStore

LAT0, LNG0 = 53.54, -113.49
M_PER_DEG = 6_371_008.8 * math.pi / 180  # the network's own projection

NODES = {
    "A": (0, -50),
    "B": (100, -50),
    "C": (200, -50),
    "D": (0, 50),
    "E": (100, 50),
    "F": (200, 50),
    "K": (600, -50),
}
EDGES = ["AB", "BC", "DE", "EF", "AD", "BE", "CF", "CK"]


def pos(x: float, y: float) -> list[float]:
    """Metres east and north of the origin, as a GeoJSON [lng, lat] position."""
    return [
        LNG0 + x / (M_PER_DEG * math.cos(math.radians(LAT0))),
        LAT0 + y / M_PER_DEG,
    ]


def build_network() -> WalkNetwork:
    names = list(NODES)
    nodes = []
    for i, name in enumerate(names):
        lng, lat = pos(*NODES[name])
        nodes.append(RawNode(osmid=i + 1, lat=lat, lng=lng))
    edges = []
    for k, (u, v) in enumerate(EDGES):
        (x1, y1), (x2, y2) = NODES[u], NODES[v]
        edges.append(
            RawEdge(
                u=names.index(u) + 1,
                v=names.index(v) + 1,
                length_m=math.dist((x1, y1), (x2, y2)),
                highway="footway",
                osmid=100 + k,
            )
        )
    return WalkNetwork.from_data(export_network(nodes, edges, source="test"))


@pytest.fixture(scope="module")
def network() -> WalkNetwork:
    return build_network()


@pytest.fixture(scope="module")
def index(network: WalkNetwork) -> EdgeIndex:
    return EdgeIndex(network)


def edge_of(network: WalkNetwork, name: str) -> int:
    """The edge index for a name such as "AB", whichever way the export stored it."""
    names = list(NODES)
    ends = {names.index(name[0]), names.index(name[1])}
    for edge in range(network.edge_count):
        if {int(network.edge_u[edge]), int(network.edge_v[edge])} == ends:
            return edge
    raise KeyError(name)


def named(network: WalkNetwork, edges: Any) -> set[str]:
    return {name for name in EDGES if edge_of(network, name) in set(edges)}


@pytest.fixture
def item(make_obstruction: Callable[..., Obstruction]) -> Callable[..., Obstruction]:
    """A construction-style item ("sidewalk closed", blocked) with any overrides."""

    def build(geometry: dict[str, Any], **overrides: Any) -> Obstruction:
        data: dict[str, Any] = {
            "source": "city_construction",
            "source_ref": "permit-1",
            "category": "sidewalk",
            "kind": "sidewalk_closed",
            "effect": "blocked",
            "title": "Sidewalk closed",
            "confidence": "high",
            "geometry": geometry,
        }
        data.update(overrides)
        return make_obstruction(**data)

    return build


def run(index: EdgeIndex, now: datetime, *items: Obstruction, **kw: Any) -> MatchResult:
    return match_items(items, index, now, **kw)


def point(x: float, y: float) -> dict[str, Any]:
    return {"type": "Point", "coordinates": pos(x, y)}


def line(*xys: tuple[float, float]) -> dict[str, Any]:
    return {"type": "LineString", "coordinates": [pos(*xy) for xy in xys]}


# ------------------------------------------------------------------------- points


def test_point_snaps_to_the_nearest_edge(network, index, item, now) -> None:
    result = run(index, now, item(point(50, -40)))  # 10 m north of A-B
    match = result.matches["city_construction:permit-1"]
    assert match.method == "point"
    assert named(network, match.edges) == {"AB"}
    assert match.distance_m == pytest.approx(10, abs=0.2)


def test_the_closest_of_several_edges_wins(network, index, item, now) -> None:
    # 10 m from A-B but only 5 m from B-E.
    match = run(index, now, item(point(95, -40))).matches["city_construction:permit-1"]
    assert named(network, match.edges) == {"BE"}


@pytest.mark.parametrize(
    ("north_m", "matched"),
    [(POINT_RADIUS_M - 1, True), (POINT_RADIUS_M + 1, False)],
)
def test_points_snap_only_within_30_m(index, item, now, north_m, matched) -> None:
    # In the middle of the A-B-E-D square, so A-B is the nearest edge.
    match = run(index, now, item(point(50, -50 + north_m))).matches[
        "city_construction:permit-1"
    ]
    assert (match.method == "point") is matched
    if not matched:
        assert match.method == "unmatched"
        assert match.edges == ()
        assert "30 m" in (match.note or "")


def test_index_pieces_cover_every_edge_exactly(network, index) -> None:
    """Pieces are at most PIECE_M long and add up to each edge's polyline length.

    (Compared with the polyline, not ``edge_length_m``: the network stores the
    coordinates as float32, which rounds them to about half a metre.)
    """
    piece_length = 2 * np.hypot(index.half[:, 0], index.half[:, 1])
    assert piece_length.max() <= PIECE_M + 1e-3
    total = np.bincount(index.edge, weights=piece_length, minlength=network.edge_count)
    polyline = [
        np.linalg.norm(
            np.diff(index.to_xy(network.edge_coords(e)), axis=0), axis=1
        ).sum()
        for e in range(network.edge_count)
    ]
    assert np.allclose(total, polyline, atol=1e-3)
    # Every test edge is one straight segment, split into ceil(length / PIECE_M)
    # pieces: 3 for the 100 m edges stored a little over 100 m, 9 for C-K.
    expected = np.ceil(np.asarray(polyline) / PIECE_M)
    assert np.array_equal(
        np.bincount(index.edge, minlength=network.edge_count), expected
    )
    assert np.count_nonzero(index.edge == edge_of(network, "CK")) >= 8


@pytest.mark.parametrize("x", [205, 400, 595])
def test_points_snap_along_the_whole_of_a_long_edge(
    network, index, item, now, x
) -> None:
    match = run(index, now, item(point(x, -45))).matches["city_construction:permit-1"]
    assert named(network, match.edges) == {"CK"}
    assert match.distance_m == pytest.approx(5, abs=0.2)


# -------------------------------------------------------------------------- lines


def test_a_line_matches_the_edges_inside_its_15_m_buffer(
    network, index, item, now
) -> None:
    # 5 m north of A-B-C. The verticals have only 15 m of their 100 m inside.
    match = run(index, now, item(line((0, -45), (200, -45)))).matches[
        "city_construction:permit-1"
    ]
    assert match.method == "line"
    assert named(network, match.edges) == {"AB", "BC"}


def test_a_line_outside_the_buffer_matches_nothing(index, item, now) -> None:
    offset = LINE_BUFFER_M + 5  # 20 m away, parallel to A-B
    match = run(index, now, item(line((10, -50 + offset), (90, -50 + offset)))).matches[
        "city_construction:permit-1"
    ]
    assert match.method == "unmatched"


def test_a_closure_in_the_middle_of_a_long_edge_still_matches(
    network, index, item, now
) -> None:
    # 100 m of the 400 m C-K edge: under half, but well over 25 m.
    match = run(index, now, item(line((350, -45), (450, -45)))).matches[
        "city_construction:permit-1"
    ]
    assert named(network, match.edges) == {"CK"}


def test_multilinestring_parts_each_match(network, index, item, now) -> None:
    geometry = {
        "type": "MultiLineString",
        "coordinates": [
            [pos(20, -48), pos(80, -48)],  # along the middle of A-B
            [pos(120, 52), pos(180, 52)],  # along the middle of E-F
        ],
    }
    match = run(index, now, item(geometry)).matches["city_construction:permit-1"]
    assert named(network, match.edges) == {"AB", "EF"}


# ----------------------------------------------------------- areas and stations


def test_polygons_attach_as_areas_and_change_no_edges(index, item, now) -> None:
    square = [pos(-10, -60), pos(210, -60), pos(210, 60), pos(-10, 60), pos(-10, -60)]
    result = run(index, now, item({"type": "Polygon", "coordinates": [square]}))
    assert result.matches["city_construction:permit-1"].method == "area"
    assert result.hazards == {}


def test_311_advisories_and_weather_are_areas_even_as_points(index, item, now) -> None:
    on_edge = point(50, -50)
    snow = item(
        on_edge,
        source="city_311_snow",
        kind="snow_complaints",
        effect="caution",
        confidence="low",
    )
    ice = item(
        on_edge,
        source="weather_watch",
        category="weather",
        kind="ice_risk",
        effect="ice_risk",
        confidence="low",
    )
    result = run(index, now, snow, ice)
    assert {m.method for m in result.matches.values()} == {"area"}
    assert result.hazards == {}


def test_station_points_are_never_snapped_to_sidewalks(index, item, now) -> None:
    escalator = item(
        point(50, -50),  # exactly on A-B
        source="city_lrt_devices",
        source_ref="101",
        category="indoor",
        kind="escalator_out",
        effect="caution",
    )
    elevator = item(
        point(50, -50),
        source="city_lrt_devices",
        source_ref="102",
        category="indoor",
        kind="elevator_out",
        effect="blocked",
    )
    result = run(index, now, escalator, elevator)
    assert {m.method for m in result.matches.values()} == {"station"}
    assert result.hazards == {}


def test_an_elevator_blocks_only_its_reviewed_edges(network, index, item, now) -> None:
    be = edge_of(network, "BE")
    elevator = item(
        point(50, -50),
        source="city_lrt_devices",
        source_ref="102",
        category="indoor",
        kind="elevator_out",
        effect="blocked",
    )
    result = run(index, now, elevator, elevator_edges={"102": [be]})
    assert result.matches["city_lrt_devices:102"].method == "elevator"
    assert list(result.hazards) == [be]


# ------------------------------------------------------- hazards and effects


def test_hazards_keep_the_item_id_and_effect(network, index, item, now) -> None:
    closed = item(point(50, -48))
    caution = item(
        point(60, -48),
        source="city_traffic_disruptions",
        source_ref="7",
        category="road",
        kind="road_closed",
        effect="caution",
        confidence="medium",
        title="Road closed",
    )
    result = run(index, now, closed, caution)
    hazards = result.hazards[edge_of(network, "AB")]
    assert [(h.item_id, h.effect, h.confidence, h.title) for h in hazards] == [
        ("city_construction:permit-1", "blocked", "high", "Sidewalk closed"),
        ("city_traffic_disruptions:7", "caution", "medium", "Road closed"),
    ]


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        # PLAN.md section 8: multiplier per profile, None = the path is removed.
        (
            "blocked",
            {
                "wheelchair": None,
                "walker_cane": None,
                "low_vision": None,
                "standard": None,
            },
        ),
        (
            "snow",
            {
                "wheelchair": None,
                "walker_cane": 3.0,
                "low_vision": 2.0,
                "standard": 1.5,
            },
        ),
        (
            "ice_risk",
            {
                "wheelchair": 3.0,
                "walker_cane": None,
                "low_vision": 3.0,
                "standard": 1.5,
            },
        ),
        (
            "caution",
            {"wheelchair": 1.0, "walker_cane": 1.0, "low_vision": 1.0, "standard": 1.0},
        ),
    ],
)
def test_effects_follow_the_plan_table(
    network, index, item, now, effect, expected
) -> None:
    """A matched item changes routing exactly as the PLAN.md section 8 table says."""
    kind = {"blocked": "sidewalk_closed", "caution": "trail_caution"}.get(
        effect, effect
    )
    hazards = run(index, now, item(point(50, -48), kind=kind, effect=effect)).hazards
    ab = edge_of(network, "AB")
    config = load_routing_config()
    for profile, multiplier in expected.items():
        weights = profile_weights(network, config.profile(profile), hazards)
        length = float(network.edge_length_m[ab])
        assert weights[ab] == pytest.approx(
            0 if multiplier is None else length * multiplier
        )
        others = np.delete(weights, ab)
        assert np.allclose(others, np.delete(network.edge_length_m, ab))


# ------------------------------------------------------------------- provider


class FlakyStore:
    """A real store whose reads the test can make fail, or hold until it says go."""

    def __init__(self, store: ObstructionStore) -> None:
        self.store = store
        self.fail = False
        self.calls = 0
        self.reading = threading.Event()  # set when a read starts
        self.gate = threading.Event()  # a read waits here until the test opens it
        self.gate.set()

    def active(self, now: datetime, **kwargs: Any) -> list[Obstruction]:
        self.calls += 1
        self.reading.set()
        assert self.gate.wait(10), "the test never opened the gate"
        if self.fail:
            raise ConnectionError("database is down")
        return self.store.active(now, **kwargs)


def test_provider_caches_and_refreshes(
    network, store: ObstructionStore, item, now
) -> None:
    ticks = [0.0]
    provider = HazardProvider(
        store, network, refresh_s=30, clock=lambda: ticks[0], background=False
    )
    assert provider(now) == {}
    store.upsert([item(point(50, -48))], now)
    ticks[0] = 10.0
    assert provider(now) == {}  # still the cached map
    ticks[0] = 30.0
    assert list(provider(now)) == [edge_of(network, "AB")]
    assert provider.latest is not None
    assert provider.latest.matches["city_construction:permit-1"].method == "point"


def test_a_stale_map_is_rebuilt_in_the_background(
    network, store: ObstructionStore, item, now
) -> None:
    """A request never waits for matching once a map exists."""
    flaky = FlakyStore(store)
    ticks = [0.0]
    provider = HazardProvider(flaky, network, refresh_s=30, clock=lambda: ticks[0])
    assert provider(now) == {}  # the first map is built in the call
    store.upsert([item(point(50, -48))], now)
    flaky.gate.clear()
    flaky.reading.clear()
    ticks[0] = 30.0
    assert provider(now) == {}  # answered at once with the old map...
    assert flaky.reading.wait(10)  # ...while the rebuild is held at the gate
    assert provider(now) == {}  # a second call does not start another rebuild
    assert flaky.calls == 2
    flaky.gate.set()
    assert provider.worker is not None
    provider.worker.join(10)
    assert list(provider(now)) == [edge_of(network, "AB")]
    assert flaky.calls == 2  # not due again until another refresh_s


def test_a_failed_rebuild_keeps_the_last_good_map(
    network, store: ObstructionStore, item, now, caplog: pytest.LogCaptureFixture
) -> None:
    store.upsert([item(point(50, -48))], now)
    flaky = FlakyStore(store)
    ticks = [0.0]
    provider = HazardProvider(flaky, network, refresh_s=30, clock=lambda: ticks[0])
    good = provider(now)
    assert list(good) == [edge_of(network, "AB")]

    flaky.fail = True
    ticks[0] = 30.0
    assert provider(now) == good
    assert provider.worker is not None
    provider.worker.join(10)
    assert "keeping the last good hazard map" in caplog.text
    assert provider(now) == good  # still the last good map...
    ticks[0] = 59.0
    assert provider(now) == good
    assert flaky.calls == 2  # ...and no retry before refresh_s has passed

    ticks[0] = 60.0
    provider(now)
    provider.worker.join(10)
    assert flaky.calls == 3  # retried once refresh_s passed

    flaky.fail = False
    ticks[0] = 90.0
    provider(now)
    provider.worker.join(10)
    assert provider(now) == good  # recovered: rebuilt from the store again
    assert flaky.calls == 4


def test_a_failing_store_before_any_map_gives_no_hazards(
    network, store: ObstructionStore, item, now
) -> None:
    flaky = FlakyStore(store)
    flaky.fail = True
    ticks = [0.0]
    provider = HazardProvider(flaky, network, refresh_s=30, clock=lambda: ticks[0])
    assert provider(now) == {}  # no exception reaches routing
    assert provider(now) == {}
    assert flaky.calls == 1  # not retried before refresh_s
    flaky.fail = False
    store.upsert([item(point(50, -48))], now)
    ticks[0] = 30.0
    assert list(provider(now)) == [edge_of(network, "AB")]  # built in the call


def test_routes_keep_working_when_the_store_fails(
    network, store: ObstructionStore
) -> None:
    """With every store read failing, /api/route still answers 200."""
    flaky = FlakyStore(store)
    flaky.fail = True
    app = FastAPI()
    app.state.router = Router(network, load_routing_config())
    app.state.hazard_provider = HazardProvider(flaky, network, refresh_s=0)
    app.include_router(routing_api.router)
    (a_lng, a_lat), (c_lng, c_lat) = pos(*NODES["A"]), pos(*NODES["C"])
    params = {
        "from": f"{a_lat},{a_lng}",
        "to": f"{c_lat},{c_lng}",
        "profile": "standard",
    }
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/api/route", params=params).status_code == 200
    assert flaky.calls == 3  # each call tried again (refresh_s=0) and failed safely


def test_one_odd_item_does_not_stop_the_others(
    network, index, item, now, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    real = matching.match_item

    def match_item(target: Obstruction, *args: Any) -> matching.Match:
        if target.source_ref == "odd":
            raise ValueError("unexpected geometry")
        return real(target, *args)

    monkeypatch.setattr(matching, "match_item", match_item)
    odd = item(point(50, -48), source_ref="odd")
    fine = item(point(50, -48), source_ref="fine")
    result = run(index, now, odd, fine)
    assert result.matches[odd.id].method == "unmatched"
    assert result.matches[odd.id].note == "matching failed"
    assert [h.item_id for h in result.hazards[edge_of(network, "AB")]] == [fine.id]
    assert f"MATCH {odd.id} failed" in caplog.text


def test_the_app_routes_with_the_matcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the committed network, startup sets ``hazard_provider`` for routing."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.delenv("DATA_MODE", raising=False)
    app = create_app()
    with TestClient(app):
        assert isinstance(app.state.hazard_provider, HazardProvider)
