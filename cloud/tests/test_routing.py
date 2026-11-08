"""OP-207: routing per profile with alternates, hazards, ETAs, steps and reasons.

Most tests use a small hand-built grid so every expectation is checkable by hand.
The last tests run against the committed Edmonton network for speed and sanity.
"""

from __future__ import annotations

import time
from statistics import quantiles

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    assert all(s.text.startswith("Follow ") for s in best.steps)
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


def test_unreachable_destination_gives_no_route(router, grid) -> None:
    """Cut the top row off for wheelchairs: 1-4 and 3-6 blocked, 2-5 is stairs. No route."""
    _, index = grid
    hazards = {
        index[(1, 4)]: [EdgeHazard("b:1", Effect.BLOCKED, Confidence.HIGH)],
        index[(3, 6)]: [EdgeHazard("b:2", Effect.BLOCKED, Confidence.HIGH)],
    }
    result = router.route(*latlng(1), *latlng(9), "wheelchair", hazards)
    assert result.routes == []
    assert any("No route is possible" in w for w in result.warnings)
    # A standard walker can take the stairs, so the same hazards still leave a route.
    assert router.route(*latlng(1), *latlng(9), "standard", hazards).routes


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
    assert [s.text for s in best.steps] == ["Follow Avenue A for 200 m"]


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
    assert best["steps"] and best["google_maps_url"] is None  # OP-209 fills the links


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
