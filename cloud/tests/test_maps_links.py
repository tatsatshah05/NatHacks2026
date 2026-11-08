"""OP-209: Google Maps and Apple Maps links carry our route's detours."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import maps_links, routing_api
from cloud.app.profiles import load_routing_config
from cloud.app.routing import EdgeHazard, Router
from cloud.app.schemas import Confidence, Effect
from cloud.tests.test_routing import grid_network, latlng

A, B = (53.5444, -113.4909), (53.5468, -113.4973)


def test_google_url_format_and_waypoint_cap() -> None:
    url = maps_links.google_maps_url(
        A,
        B,
        [
            (53.545, -113.492),
            (53.546, -113.494),
            (53.5465, -113.496),
            (53.547, -113.497),
        ],
    )
    parsed = urlparse(url)
    assert (
        parsed.scheme == "https"
        and parsed.netloc == "www.google.com"
        and parsed.path == "/maps/dir/"
    )
    q = parse_qs(parsed.query)
    assert q["api"] == ["1"] and q["travelmode"] == ["walking"]
    assert q["origin"] == ["53.544400,-113.490900"] and q["destination"] == [
        "53.546800,-113.497300"
    ]
    assert q["waypoints"][0].count("|") == 2  # three waypoints at most
    assert url.count("%7C") == 2  # the separator is URL-encoded


def test_google_url_without_waypoints_has_no_waypoints_parameter() -> None:
    q = parse_qs(urlparse(maps_links.google_maps_url(A, B)).query)
    assert "waypoints" not in q


def test_apple_url_format() -> None:
    url = maps_links.apple_maps_url(A, B)
    parsed = urlparse(url)
    assert parsed.netloc == "maps.apple.com"
    q = parse_qs(parsed.query)
    assert q == {
        "saddr": ["53.544400,-113.490900"],
        "daddr": ["53.546800,-113.497300"],
        "dirflg": ["w"],
    }
    assert "may not keep" in maps_links.APPLE_MAPS_NOTE


# ---------------------------------------------------------- waypoint selection


@pytest.fixture(scope="module")
def grid():
    return grid_network()


@pytest.fixture
def router(grid):
    return Router(grid[0], load_routing_config())


def test_no_waypoints_when_the_route_is_the_plain_route(grid) -> None:
    net, _ = grid
    assert maps_links.detour_waypoints(net, (0, 1, 2), (0, 1, 2)) == []
    assert maps_links.detour_waypoints(net, (0, 1, 2), None) == []


def test_waypoint_sits_in_the_middle_of_the_detour(grid) -> None:
    net, _ = grid
    # Plain route goes straight down the middle column (2 -> 5 -> 8 as indices 1, 4, 7);
    # our route goes around via the left column: 1, 0, 3, 6, 7.
    points = maps_links.detour_waypoints(net, (1, 0, 3, 6, 7), (1, 4, 7))
    assert len(points) == 1
    mid_node = 3  # index 3 is the middle of the off-plain stretch (0, 3, 6)
    assert points[0] == (float(net.node_lat[mid_node]), float(net.node_lng[mid_node]))


def test_longest_detours_win_and_stay_in_travel_order(grid) -> None:
    net, _ = grid
    # Route 0..8 with the plain route sharing only the ends: everything in between is one stretch.
    points = maps_links.detour_waypoints(
        net, (0, 1, 2, 5, 8), (0, 3, 6, 7, 8), max_waypoints=3
    )
    assert len(points) == 1  # a single contiguous stretch gives a single waypoint
    # Two separate stretches, both kept, in order.
    points = maps_links.detour_waypoints(
        net, (0, 1, 4, 3, 6, 7, 8), (0, 3, 6, 8), max_waypoints=3
    )
    assert len(points) == 2
    first, second = points
    assert (first[0], first[1]) == (
        float(net.node_lat[1]),
        float(net.node_lng[1]),
    )  # stretch (1, 4) -> node 1
    assert (second[0], second[1]) == (
        float(net.node_lat[7]),
        float(net.node_lng[7]),
    )  # stretch (7,) -> node 7


# ------------------------------------------------------------------------ API


def test_route_endpoint_fills_the_maps_links(router, grid) -> None:
    _, index = grid
    app = FastAPI()
    app.state.router = router
    app.state.hazard_provider = lambda now: {
        index[(5, 8)]: [
            EdgeHazard("b:1", Effect.BLOCKED, Confidence.HIGH, "Sidewalk closed")
        ]
    }
    app.include_router(routing_api.router)
    client = TestClient(app)
    frm, to = latlng(2), latlng(8)
    body = client.get(
        "/api/route",
        params={
            "from": f"{frm[0]},{frm[1]}",
            "to": f"{to[0]},{to[1]}",
            "profile": "standard",
        },
    ).json()
    best = body["routes"][0]
    g = parse_qs(urlparse(best["google_maps_url"]).query)
    assert g["origin"] == [f"{frm[0]:.6f},{frm[1]:.6f}"] and g["destination"] == [
        f"{to[0]:.6f},{to[1]:.6f}"
    ]
    assert "waypoints" in g  # the detour around the closed sidewalk is pinned
    assert best["apple_maps_url"].startswith("https://maps.apple.com/?saddr=")
    assert body["apple_maps_note"] == maps_links.APPLE_MAPS_NOTE
