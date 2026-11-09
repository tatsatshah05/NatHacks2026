"""OP-207: routing per profile with alternates, hazards, ETAs, steps and reasons.

Most tests use a small hand-built grid so every expectation is checkable by hand.
The last tests run against the committed Edmonton network for speed and sanity.
"""

from __future__ import annotations

import time
from statistics import quantiles

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scipy.sparse.csgraph import connected_components

from cloud.app import routing_api
from cloud.app.network import (
    DEFAULT_NETWORK_PATH,
    RawEdge,
    RawNode,
    WalkNetwork,
    export_network,
)
from cloud.app.profiles import DEFAULT_PROFILES_PATH, RoutingConfig, load_routing_config
from cloud.app.routing import EdgeHazard, Router, RoutingError, profile_weights
from cloud.app.schemas import Confidence, Effect

# A 3 x 3 grid, 100 m apart, around Churchill Square. Node ids are 1..9:
#   1 - 2 - 3        edge names: row streets "Avenue A/B/C", column streets "Street 1/2/3"
#   |   |   |
#   4 - 5 - 6
#   |   |   |
#   7 - 8 - 9
LAT0, LNG0 = 53.5440, -113.4900
DLAT, DLNG = 0.0009, 0.0015  # about 100 m each at Edmonton's latitude


def grid_network() -> tuple[WalkNetwork, dict[tuple[int, int], int]]:
    nodes = [
        RawNode(osmid=i + 1, lat=LAT0 + (i // 3) * DLAT, lng=LNG0 + (i % 3) * DLNG)
        for i in range(9)
    ]
    edges: list[RawEdge] = []
    for r in range(3):
        for c in range(3):
            n = r * 3 + c + 1
            if c < 2:
                edges.append(
                    RawEdge(
                        u=n,
                        v=n + 1,
                        length_m=100.0,
                        highway="footway",
                        name=f"Avenue {'ABC'[r]}",
                        osmid=100 + n,
                    )
                )
            if r < 2:
                edges.append(
                    RawEdge(
                        u=n,
                        v=n + 3,
                        length_m=100.0,
                        highway="footway",
                        name=f"Street {c + 1}",
                        osmid=200 + n,
                    )
                )
    # The middle column between rows 1 and 2 (node 2 -> 5) is a staircase.
    edges = [
        RawEdge(**{**e.__dict__, "highway": "steps"}) if (e.u, e.v) == (2, 5) else e
        for e in edges
    ]
    data = export_network(nodes, edges, source="test")
    net = WalkNetwork.from_data(data)
    index = {
        (int(net.edge_u[i]) + 1, int(net.edge_v[i]) + 1): i
        for i in range(net.edge_count)
    }
    index.update({(v, u): i for (u, v), i in list(index.items())})
    return net, index


@pytest.fixture(scope="module")
def grid():
    return grid_network()


@pytest.fixture(scope="module")
def config() -> RoutingConfig:
    return load_routing_config(DEFAULT_PROFILES_PATH)


@pytest.fixture
def router(grid, config) -> Router:
    return Router(grid[0], config)


def latlng(node: int) -> tuple[float, float]:
    i = node - 1
    return LAT0 + (i // 3) * DLAT, LNG0 + (i % 3) * DLNG


# ------------------------------------------------------------------- config


def test_profiles_match_the_plan(config) -> None:
    assert set(config.profiles) == {
        "wheelchair",
        "walker_cane",
        "low_vision",
        "standard",
    }
    w, k, v, s = (
        config.profiles[p]
        for p in ("wheelchair", "walker_cane", "low_vision", "standard")
    )
    assert (
        w.multiplier(Effect.SNOW) is None
        and w.multiplier(Effect.ICE_RISK) == 3
        and w.stairs is None
    )
    assert (
        k.multiplier(Effect.SNOW) == 3
        and k.multiplier(Effect.ICE_RISK) is None
        and k.stairs is None
    )
    assert (
        v.multiplier(Effect.SNOW) == 2
        and v.multiplier(Effect.ICE_RISK) == 3
        and v.stairs == 1
    )
    assert s.multiplier(Effect.SNOW) == 1.5 and s.speed_m_s == 1.3
    assert all(p.multiplier(Effect.BLOCKED) is None for p in config.profiles.values())
    assert all(p.multiplier(Effect.CAUTION) == 1 for p in config.profiles.values())


def test_unknown_profile_is_an_error(router) -> None:
    with pytest.raises(RoutingError, match="unknown profile"):
        router.route(*latlng(1), *latlng(9), "hoverboard")


# ------------------------------------------------------------------ weights


def test_weights_follow_profile_rules(grid, config) -> None:
    net, index = grid
    stairs = index[(2, 5)]
    weights = profile_weights(net, config.profile("wheelchair"), {})
    assert weights[stairs] == 0  # removed for wheelchairs
    assert (
        profile_weights(net, config.profile("standard"), {})[stairs] == 100
    )  # usable, x1

    hazards = {
        index[(4, 5)]: [EdgeHazard("x:1", Effect.SNOW, Confidence.MEDIUM, "Snow")],
        index[(5, 6)]: [
            EdgeHazard("x:2", Effect.ICE_RISK, Confidence.MEDIUM, "Ice"),
            EdgeHazard("x:3", Effect.CAUTION, Confidence.LOW),
        ],
        index[(7, 8)]: [
            EdgeHazard("x:4", Effect.ICE_RISK, Confidence.MEDIUM),
            EdgeHazard("x:5", Effect.BLOCKED, Confidence.HIGH),
        ],
    }
    w = profile_weights(net, config.profile("standard"), hazards)
    assert w[index[(4, 5)]] == 150  # snow x1.5
    assert (
        w[index[(5, 6)]] == 150
    )  # ice x1.5 beats caution x1 (largest multiplier wins)
    assert w[index[(7, 8)]] == 0  # blocked wins over everything
    assert (
        profile_weights(net, config.profile("wheelchair"), hazards)[index[(4, 5)]] == 0
    )  # snow impassable
    assert (
        profile_weights(net, config.profile("walker_cane"), hazards)[index[(5, 6)]] == 0
    )  # ice impassable


# ------------------------------------------------------------------- routes


def test_shortest_route_and_eta(router) -> None:
    result = router.route(*latlng(1), *latlng(9), "standard")
    best = result.routes[0]
    assert best.distance_m == 400  # four 100 m edges
    assert best.eta_min == pytest.approx(400 / 1.3 / 60, abs=0.05)
    assert best.nodes[0] == 0 and best.nodes[-1] == 8
    assert best.coordinates[0] == pytest.approx(
        [LNG0, LAT0], abs=1e-5
    )  # float32 polyline
    assert best.hazards_on_route == [] and best.reason is None
    # Plain words: the first step starts the trip, every later one turns onto a
    # named way, and the last says where the destination is.
    assert best.steps[0].text.startswith("Start on ")
    assert all(
        s.text.startswith(("Continue onto ", "Bear ", "Turn ")) for s in best.steps[1:]
    )
    assert best.steps[-1].text.endswith("Your destination is ahead.")
    assert not any("footway" in s.text for s in best.steps)
    assert sum(s.distance_m for s in best.steps) == pytest.approx(400)


def test_wheelchair_avoids_stairs_and_walker_speed(router) -> None:
    # 2 -> 8 straight down uses the staircase between 2 and 5.
    standard = router.route(*latlng(2), *latlng(8), "standard").routes[0]
    assert standard.distance_m == 200
    wheelchair = router.route(*latlng(2), *latlng(8), "wheelchair").routes[0]
    assert wheelchair.distance_m == 400  # around the stairs
    walker = router.route(*latlng(2), *latlng(8), "walker_cane").routes[0]
    assert walker.eta_min == pytest.approx(400 / 0.9 / 60, abs=0.05)


def test_hazard_reroutes_and_explains(router, grid) -> None:
    _, index = grid
    hazards = {
        index[(5, 8)]: [
            EdgeHazard(
                "city_lrt_devices:9",
                Effect.BLOCKED,
                Confidence.HIGH,
                "Elevator out of service",
            )
        ],
        index[(2, 5)]: [
            EdgeHazard("x:ice", Effect.ICE_RISK, Confidence.LOW, "Ice risk")
        ],
    }
    result = router.route(*latlng(2), *latlng(8), "standard", hazards)
    best = result.routes[0]
    assert index[(5, 8)] not in best.edges  # the blocked edge is never used
    avoided = [h.item_id for h in best.hazards_avoided]
    assert avoided[0] == "city_lrt_devices:9"  # the blocked elevator leads the list
    assert set(avoided) <= {
        "city_lrt_devices:9",
        "x:ice",
    }  # the ice edge sat on the plain route too
    assert best.reason is not None and best.reason.startswith(
        "Avoids Elevator out of service"
    )
    # Some routes may still cross the low-confidence ice edge; when they do, they warn.
    for route in result.routes:
        if index[(2, 5)] in route.edges:
            assert any("Low-confidence" in w for w in route.warnings)
            assert any(h.item_id == "x:ice" for h in route.hazards_on_route)


def test_alternates_are_distinct_and_capped(router) -> None:
    result = router.route(*latlng(1), *latlng(9), "standard")
    assert 1 <= len(result.routes) <= 3
    assert result.routes[0].distance_m <= min(r.distance_m for r in result.routes)
    for i, a in enumerate(result.routes):
        for b in result.routes[i + 1 :]:
            shared = len(set(a.edges) & set(b.edges)) * 100 / b.distance_m
            assert shared < 0.7


def test_unreachable_destination_gives_no_route(router, grid, config) -> None:
    """Cut the top row off for wheelchairs: 1-4 and 3-6 blocked, 2-5 is stairs.

    The top row is a pocket: its paths work but lead nowhere else. Within a 50 m
    snap limit the start cannot leave it, so there is no route.
    """
    from cloud.app.profiles import Snap

    _, index = grid
    hazards = {
        index[(1, 4)]: [EdgeHazard("b:1", Effect.BLOCKED, Confidence.HIGH)],
        index[(3, 6)]: [EdgeHazard("b:2", Effect.BLOCKED, Confidence.HIGH)],
    }
    tight = Router(
        grid[0], config.model_copy(update={"snap": Snap(warn_m=40, max_m=50)})
    )
    result = tight.route(*latlng(1), *latlng(9), "wheelchair", hazards)
    assert result.routes == []
    assert any("No route is possible" in w for w in result.warnings)
    # A standard walker can take the stairs, so the same hazards still leave a route.
    assert tight.route(*latlng(1), *latlng(9), "standard", hazards).routes
    # With the normal limit the start leaves the pocket for node 4, 100 m away.
    joined = router.route(*latlng(1), *latlng(9), "wheelchair", hazards)
    assert joined.routes and joined.origin == (3, pytest.approx(100, abs=1))
    assert any("cut off" in w and "begins" in w for w in joined.warnings)


def test_start_inside_a_blocked_stretch_snaps_to_the_nearest_open_path(
    router, grid
) -> None:
    """Every path out of node 1 is blocked; the trip must still route from the next open node."""
    _, index = grid
    hazards = {
        index[(1, 2)]: [
            EdgeHazard("c:1", Effect.BLOCKED, Confidence.HIGH, "Sidewalk closed")
        ],
        index[(1, 4)]: [
            EdgeHazard("c:2", Effect.BLOCKED, Confidence.HIGH, "Sidewalk closed")
        ],
    }
    result = router.route(*latlng(1), *latlng(9), "wheelchair", hazards)
    assert result.routes, result.warnings
    assert result.origin[0] != 0  # moved off the boxed-in node
    assert any(
        "inside a blocked stretch" in w and "begins" in w for w in result.warnings
    )
    # The destination end gets the same treatment.
    hazards_end = {
        index[(6, 9)]: [EdgeHazard("c:3", Effect.BLOCKED, Confidence.HIGH)],
        index[(8, 9)]: [EdgeHazard("c:4", Effect.BLOCKED, Confidence.HIGH)],
    }
    result = router.route(*latlng(1), *latlng(9), "wheelchair", hazards_end)
    assert result.routes and result.destination[0] != 8
    assert any("ends" in w for w in result.warnings)
    # Without hazards the nearest node is used as before, with no warning.
    plain = router.route(*latlng(1), *latlng(9), "wheelchair")
    assert plain.origin == (0, pytest.approx(0, abs=1)) and plain.warnings == []


def test_same_place_and_far_points(router) -> None:
    same = router.route(*latlng(5), *latlng(5), "standard")
    assert same.routes == [] and any("same place" in w for w in same.warnings)
    with pytest.raises(RoutingError, match="from the nearest walkable path"):
        router.route(53.9, -113.0, *latlng(9), "standard")  # ~45 km away


def test_steps_merge_consecutive_edges(router) -> None:
    best = router.route(*latlng(1), *latlng(3), "standard").routes[0]
    # Two 100 m edges of Avenue A make one step; 200 m at 1.3 m/s is about 3 minutes.
    assert [s.text for s in best.steps] == [
        (
            "Start on Avenue A heading east for about 3 minutes (200 m). "
            "Your destination is ahead."
        )
    ]
    assert best.steps[0].distance_m == 200 and len(best.steps[0].edges) == 2


# -------------------------------------------------- review regressions (Estella)


def small_network(edges: list[RawEdge], nodes: list[RawNode]) -> WalkNetwork:
    return WalkNetwork.from_data(export_network(nodes, edges, source="test"))


def test_parallel_edges_do_not_produce_duplicate_alternates(config) -> None:
    """Two parallel edges A-B plus a detour A-C-B: the alternates are the detour, never A-B twice."""
    nodes = [
        RawNode(1, LAT0, LNG0),
        RawNode(2, LAT0, LNG0 + DLNG),
        RawNode(3, LAT0 + DLAT, LNG0),
    ]
    edges = [
        RawEdge(1, 2, 100.0, highway="footway", name="Main Street north side", osmid=1),
        RawEdge(1, 2, 100.0, highway="footway", name="Main Street south side", osmid=2),
        RawEdge(1, 3, 100.0, highway="footway", name="Side Street", osmid=3),
        RawEdge(3, 2, 100.0, highway="footway", name="Back Lane", osmid=4),
    ]
    router = Router(small_network(edges, nodes), config)
    result = router.route(LAT0, LNG0, LAT0, LNG0 + DLNG, "standard")
    node_paths = [r.nodes for r in result.routes]
    assert len(node_paths) == len(set(node_paths)), node_paths  # no repeated route
    assert len(result.routes) == 2
    assert result.routes[0].distance_m == 100 and result.routes[1].distance_m == 200


def test_snapping_never_exceeds_the_configured_limit(router, grid, config) -> None:
    """With every path out of node 1 blocked and a 50 m limit, no farther node is chosen."""
    from cloud.app.profiles import Snap

    _, index = grid
    tight = Router(
        grid[0], config.model_copy(update={"snap": Snap(warn_m=40, max_m=50)})
    )
    hazards = {
        index[(1, 2)]: [EdgeHazard("c:1", Effect.BLOCKED, Confidence.HIGH)],
        index[(1, 4)]: [EdgeHazard("c:2", Effect.BLOCKED, Confidence.HIGH)],
    }
    result = tight.route(*latlng(1), *latlng(9), "wheelchair", hazards)
    assert (
        result.origin[0] == 0 and result.origin[1] <= 50
    )  # stayed on the nearest node
    assert result.routes == [] and any(
        "No route is possible" in w for w in result.warnings
    )
    # The normal limit (1,000 m) still allows the 100 m move, as the earlier test shows.
    assert router.route(*latlng(1), *latlng(9), "wheelchair", hazards).routes


def courtyard() -> tuple[WalkNetwork, int]:
    """Main Street M1-M2-M3, and a courtyard P1-P2 reached by stairs (P2-M1) or a gate (P1-M2).

    P2 ------- P1        (60 m north of Main Street)
    |  stairs     |  gate
    M1 ------- M2 ------- M3
    """

    def at(x: float, y: float) -> tuple[float, float]:  # grid units of about 100 m
        return LAT0 + y * DLAT, LNG0 + x * DLNG

    places = {1: at(0, 0), 2: at(1, 0), 3: at(2, 0), 4: at(1, 0.6), 5: at(0.4, 0.6)}
    nodes = [RawNode(osmid, lat, lng) for osmid, (lat, lng) in places.items()]
    edges = [
        RawEdge(1, 2, 100.0, highway="footway", name="Main Street", osmid=1),
        RawEdge(2, 3, 100.0, highway="footway", name="Main Street", osmid=2),
        RawEdge(4, 5, 60.0, highway="footway", name="Courtyard path", osmid=3),
        RawEdge(5, 1, 72.0, highway="steps", name="Courtyard stairs", osmid=4),
        RawEdge(4, 2, 60.0, highway="footway", name="Courtyard gate", osmid=5),
    ]
    net = small_network(edges, nodes)
    gate = next(
        e
        for e in range(net.edge_count)
        if {int(net.edge_u[e]), int(net.edge_v[e])} == {3, 1}  # P1 and M2
    )
    return net, gate


def test_a_start_boxed_in_by_stairs_and_a_closure_joins_the_main_network(
    config,
) -> None:
    """The start's nearest node has a usable path, but it only leads around the pocket."""
    net, gate = courtyard()
    router = Router(net, config)
    hazards = {gate: [EdgeHazard("c:gate", Effect.BLOCKED, Confidence.HIGH)]}
    start = (LAT0 + 0.6 * DLAT, LNG0 + 1.1 * DLNG)  # 10 m from P1, 61 m from M2
    m3 = (LAT0, LNG0 + 2 * DLNG)

    result = router.route(*start, *m3, "wheelchair", hazards)
    assert result.routes, result.warnings  # used to be "No route is possible"
    assert result.origin == (1, pytest.approx(61, abs=1))  # M2, the nearest joined node
    assert result.routes[0].distance_m == 100  # M2 to M3
    assert any("cut off" in w and "begins" in w for w in result.warnings)

    # A standard walker can take the stairs, so the start stays in the courtyard.
    standard = router.route(*start, *m3, "standard", hazards)
    assert standard.origin[0] == 3 and standard.routes
    assert not any("cut off" in w for w in standard.warnings)

    # A trip inside the courtyard is not moved out of it.
    p2 = (LAT0 + 0.6 * DLAT, LNG0 + 0.4 * DLNG)
    inside = router.route(*start, *p2, "wheelchair", hazards)
    assert inside.routes[0].distance_m == 60 and inside.warnings == []

    # The end is joined the same way.
    back = router.route(*m3, *start, "wheelchair", hazards)
    assert back.destination[0] == 1 and any("ends" in w for w in back.warnings)


@pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)
def test_the_city_pocket_from_the_op206_review(config) -> None:
    """#116 review: closing edge 4562 cuts a wheelchair start off between stairs and it."""
    net = WalkNetwork.load()
    hazards = {4562: [EdgeHazard("test:closure", Effect.BLOCKED, Confidence.HIGH)]}
    weights = profile_weights(net, config.profile("wheelchair"), hazards)
    _, labels = connected_components(net.length_matrix(weights), directed=False)
    main = np.bincount(labels).argmax()
    ends = (int(net.edge_u[4562]), int(net.edge_v[4562]))
    pocket = [node for node in ends if labels[node] != main]
    if not pocket:
        pytest.skip("edge 4562 no longer closes off a pocket in this network build")
    lat, lng = float(net.node_lat[pocket[0]]), float(net.node_lng[pocket[0]])
    churchill = (53.5444, -113.4909)
    result = Router(net, config).route(lat, lng, *churchill, "wheelchair", hazards)
    assert result.routes, result.warnings
    assert labels[result.origin[0]] == main
    assert any("cut off" in w for w in result.warnings)


def test_a_hazard_still_crossed_is_not_reported_as_avoided(config) -> None:
    """One snow item spans two paths; the route leaves one but crosses the other, so it is not avoided."""
    nodes = [
        RawNode(1, LAT0, LNG0),
        RawNode(2, LAT0, LNG0 + DLNG),
        RawNode(3, LAT0 + DLAT, LNG0),
        RawNode(4, LAT0 + DLAT, LNG0 + DLNG),
    ]
    edges = [
        RawEdge(1, 2, 100.0, highway="footway", name="A-B", osmid=1),
        RawEdge(2, 4, 100.0, highway="footway", name="B-D", osmid=2),
        RawEdge(1, 3, 120.0, highway="footway", name="A-C", osmid=3),
        RawEdge(3, 4, 100.0, highway="footway", name="C-D", osmid=4),
    ]
    net = small_network(edges, nodes)
    idx = {
        (int(net.edge_u[i]) + 1, int(net.edge_v[i]) + 1): i
        for i in range(net.edge_count)
    }
    snow = EdgeHazard(
        "city_311_snow:7", Effect.SNOW, Confidence.LOW, "Snow-covered corridor"
    )
    closed = EdgeHazard(
        "city_construction:9", Effect.BLOCKED, Confidence.HIGH, "Sidewalk closed"
    )
    hazards = {idx[(1, 2)]: [snow, closed], idx[(3, 4)]: [snow]}
    result = Router(net, config).route(
        LAT0, LNG0, LAT0 + DLAT, LNG0 + DLNG, "standard", hazards
    )
    best = result.routes[0]
    assert best.nodes == (0, 2, 3)  # A-C-D, around the closed sidewalk
    assert [h.item_id for h in best.hazards_avoided] == [
        "city_construction:9"
    ]  # snow is still crossed
    assert [h.item_id for h in best.hazards_on_route] == ["city_311_snow:7"]
    assert best.reason.startswith("Avoids Sidewalk closed")


# ------------------------------------------------------------------------ API


@pytest.fixture
def client(router) -> TestClient:
    app = FastAPI()
    app.state.router = router
    app.include_router(routing_api.router)
    return TestClient(app)


def test_route_endpoint(client) -> None:
    body = client.get(
        "/api/route",
        params={
            "from": f"{latlng(1)[0]},{latlng(1)[1]}",
            "to": f"{latlng(9)[0]},{latlng(9)[1]}",
            "profile": "wheelchair",
        },
    ).json()
    assert body["profile"] == "wheelchair" and body["routes"]
    best = body["routes"][0]
    assert best["rank"] == 1 and best["geometry"]["type"] == "LineString"
    assert best["distance_m"] == 400 and best["eta_min"] > 0
    assert best["steps"] and best["google_maps_url"].startswith(
        "https://www.google.com/maps/dir/"
    )


def test_route_endpoint_uses_the_hazard_provider(client, grid) -> None:
    _, index = grid
    client.app.state.hazard_provider = lambda now: {
        index[(5, 8)]: [
            EdgeHazard(
                "city_lrt_devices:9",
                Effect.BLOCKED,
                Confidence.HIGH,
                "Elevator out of service",
            )
        ]
    }
    body = client.get(
        "/api/route",
        params={
            "from": f"{latlng(2)[0]},{latlng(2)[1]}",
            "to": f"{latlng(8)[0]},{latlng(8)[1]}",
            "profile": "standard",
        },
    ).json()
    assert body["routes"][0]["hazards_avoided"][0]["item_id"] == "city_lrt_devices:9"
    assert body["routes"][0]["reason"].startswith("Avoids")


@pytest.mark.parametrize(
    "params",
    [
        {"from": "53.5,-113.5"},
        {"from": "a,b", "to": "53.5,-113.5"},
        {"from": "53.5,-113.5", "to": "95,0"},
        {"from": "53.5,-113.5", "to": "53.6,-113.5", "profile": "jetpack"},
    ],
)
def test_route_endpoint_validates_input(client, params) -> None:
    assert client.get("/api/route", params=params).status_code == 422


def test_route_endpoint_without_network_is_503() -> None:
    app = FastAPI()
    app.include_router(routing_api.router)
    assert (
        TestClient(app)
        .get("/api/route", params={"from": "53.5,-113.5", "to": "53.6,-113.5"})
        .status_code
        == 503
    )


# ------------------------------------------------------------- the real city


@pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)
def test_city_routes_for_every_profile_under_a_second(config) -> None:
    """Churchill Square to Rogers Place for every profile: real routes, p95 under 1 s (target)."""
    router = Router(WalkNetwork.load(), config)
    churchill, rogers = (53.5444, -113.4909), (53.5468, -113.4973)
    timings = []
    for profile in config.profiles:
        for _ in range(5):
            start = time.perf_counter()
            result = router.route(*churchill, *rogers, profile)
            timings.append(time.perf_counter() - start)
        best = result.routes[0]
        assert 400 < best.distance_m < 2000, (profile, best.distance_m)
        assert 2 < best.eta_min < 40
        assert len(best.coordinates) > 10 and best.steps
    p95 = quantiles(timings, n=20)[-1]
    print(
        f"\nroute p50={sorted(timings)[len(timings) // 2] * 1000:.0f} ms p95={p95 * 1000:.0f} ms over {len(timings)} routes"
    )
    assert (
        p95 < 2.0
    )  # generous for CI runners; the measured p95 is printed for docs/PERFORMANCE.md
