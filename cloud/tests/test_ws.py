"""OP-303: ``WS /ws`` live updates.

Hub rules are tested with fake sockets on a hand-built 3 x 3 grid (100 m apart),
so every ETA is checkable by hand. The end-to-end test at the bottom runs the real
endpoint through ``TestClient.websocket_connect``: it subscribes a route, seeds a
blocking obstruction into the store, triggers a hazard rebuild and reads the
``route_changed`` event with its sentence.

    1 - 2 - 3      Avenue A (bottom row), Avenue B, Avenue C
    |   |   |
    4 - 5 - 6      Street 1, Street 2, Street 3 (columns)
    |   |   |
    7 - 8 - 9
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from cloud.app import obstructions_api, routing_api, ws
from cloud.app.main import create_app
from cloud.app.matching import HazardProvider, Match, MatchResult
from cloud.app.network import RawEdge, RawNode, WalkNetwork, export_network
from cloud.app.profiles import Snap, load_routing_config
from cloud.app.routing import EdgeHazard, Router
from cloud.app.scheduler import Scheduler, Source
from cloud.app.schemas import Confidence, Effect, Obstruction
from cloud.app.store import ObstructionStore
from cloud.app.ws import (
    HeartbeatEvent,
    LiveHub,
    ObstructionBrief,
    RouteKey,
    RouteSnapshot,
    describe_change,
    route_changed,
)

NOW = datetime(2026, 11, 7, 20, 10, tzinfo=UTC)
LAT0, LNG0 = 53.5440, -113.4900
DLAT, DLNG = 0.0009, 0.0015  # about 100 m each at Edmonton's latitude
TITLE = "Construction closure on 102 Avenue"


# ------------------------------------------------------------------ helpers


def grid_network() -> WalkNetwork:
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
    return WalkNetwork.from_data(export_network(nodes, edges, source="test"))


def edge_between(net: WalkNetwork, a: int, b: int) -> int:
    for edge in range(net.edge_count):
        if {int(net.edge_u[edge]) + 1, int(net.edge_v[edge]) + 1} == {a, b}:
            return edge
    raise KeyError((a, b))


def latlng(node: int) -> tuple[float, float]:
    i = node - 1
    return LAT0 + (i // 3) * DLAT, LNG0 + (i % 3) * DLNG


def subscribe_text(a: int, b: int, profile: str = "standard") -> str:
    return json.dumps(
        {
            "type": "subscribe",
            "route": {
                "from": list(latlng(a)),
                "to": list(latlng(b)),
                "profile": profile,
            },
        }
    )


def blocker(
    make_obstruction: Callable[..., Obstruction], **overrides: Any
) -> Obstruction:
    """A construction closure on the middle of edge 1-2 (Avenue A)."""
    data: dict[str, Any] = {
        "source": "city_construction",
        "source_ref": "permit-1",
        "category": "sidewalk",
        "kind": "sidewalk_closed",
        "effect": "blocked",
        "title": TITLE,
        "detail": None,
        "geometry": {"type": "Point", "coordinates": [LNG0 + DLNG / 2, LAT0]},
        "confidence": "high",
        "starts_at": None,
        "observed_at": NOW,
        "expires_at": NOW + timedelta(hours=2),
        "url": None,
        "attribution": "City of Edmonton Open Data",
    }
    data.update(overrides)
    return make_obstruction(**data)


class FakeSocket:
    """What the hub sees of a connection; ``fail`` makes every send raise."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = fail
        self.closed = False

    async def send_json(self, data: Any) -> None:
        if self.fail:
            raise RuntimeError("socket is gone")
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [message for message in self.sent if message["type"] == kind]


def hazard(
    edge: int, item_id: str = "city_construction:permit-1"
) -> dict[int, list[EdgeHazard]]:
    return {edge: [EdgeHazard(item_id, Effect.BLOCKED, Confidence.HIGH, TITLE)]}


def rebuild_result(hazards: dict[int, list[EdgeHazard]]) -> MatchResult:
    """A matcher result with no report index, as if the index build had failed."""
    matches = {
        h.item_id: Match(h.item_id, "point", (edge,))
        for edge, on_edge in hazards.items()
        for h in on_edge
    }
    return MatchResult(built_at=NOW, hazards=hazards, matches=matches, items=None)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def receive(sock: Any, want: str, timeout_s: float = 15.0) -> dict[str, Any]:
    """The next message of type ``want`` from a test socket, skipping the others.

    ``WebSocketTestSession.receive_json`` blocks with no timeout, so it runs in a
    thread; a missing event fails the test instead of hanging it.
    """
    deadline = time.monotonic() + timeout_s
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no {want} event within {timeout_s} s"
            try:
                message = pool.submit(sock.receive_json).result(timeout=remaining)
            except FutureTimeout:
                pool.shutdown(wait=False)
                pytest.fail(f"no {want} event within {timeout_s} s")
            assert message["type"] != "error", message
            if message["type"] == want:
                return message
    finally:
        pool.shutdown(wait=False)


@pytest.fixture(scope="module")
def network() -> WalkNetwork:
    return grid_network()


@pytest.fixture(scope="module")
def router(network: WalkNetwork) -> Router:
    return Router(network, load_routing_config())


# ------------------------------------------------------------- pure rules


def test_route_changed_rules() -> None:
    before = RouteSnapshot(frozenset({1, 2}), 10.0, 200.0, None)
    same = RouteSnapshot(frozenset({1, 2}), 10.0, 200.0, None)
    assert not route_changed(before, same)
    assert not route_changed(
        before, RouteSnapshot(frozenset({1, 2}), 11.9, 200.0, None)
    )
    assert route_changed(before, RouteSnapshot(frozenset({1, 2}), 12.0, 200.0, None))
    assert route_changed(before, RouteSnapshot(frozenset({1, 3}), 10.0, 200.0, None))
    assert route_changed(before, RouteSnapshot(None, 0.0, 0.0, None))
    impossible = RouteSnapshot(None, 0.0, 0.0, None)
    assert not route_changed(impossible, RouteSnapshot(None, 0.0, 0.0, None))
    assert route_changed(impossible, same)
    # Faster is never announced: the client only hears about worse or different routes.
    assert not route_changed(before, RouteSnapshot(frozenset({1, 2}), 5.0, 200.0, None))


def test_describe_change_sentences() -> None:
    closure = ObstructionBrief(
        id="city_construction:permit-1",
        kind="sidewalk_closed",
        effect="blocked",
        title=TITLE,
    )
    on_route = hazard(7)  # the closure sits on edge 7, which the old route used
    before = RouteSnapshot(frozenset({7, 8}), 2.6, 200.0, None)
    longer = RouteSnapshot(
        frozenset({1, 2, 3, 4}), 5.1, 400.0, f"Avoids {TITLE}; +3 min"
    )
    assert (
        describe_change(
            before, longer, profile_label="Standard", hazards=on_route, added=[closure]
        )
        == f"{TITLE} now blocks your route; the new route is 3 min longer."
    )
    gone = RouteSnapshot(None, 0.0, 0.0, None)
    assert (
        describe_change(
            before, gone, profile_label="Wheelchair", hazards=on_route, added=[closure]
        )
        == f"{TITLE} now blocks your route; no route is possible for the Wheelchair profile."
    )
    assert (
        describe_change(before, gone, profile_label="Wheelchair", hazards={})
        == "A new barrier blocks your route; no route is possible for the Wheelchair profile."
    )
    assert (
        describe_change(gone, before, profile_label="Standard", hazards={})
        == "A route is possible again; it takes about 3 min."
    )
    # A hazard the new route avoids that the old one did not: named from the hazard.
    ice = EdgeHazard(
        "camera_model:cam-1:ice_risk",
        Effect.ICE_RISK,
        Confidence.MEDIUM,
        "Ice risk at 104 Street",
    )
    detour = RouteSnapshot(frozenset({1, 2, 3, 4}), 3.2, 250.0, None, avoided=(ice,))
    assert (
        describe_change(before, detour, profile_label="Standard", hazards={})
        == "Ice risk at 104 Street now affects your route; the new route is 1 min longer."
    )
    # The closure the old route was avoiding is gone: shorter again.
    old_hazard = EdgeHazard(closure.id, Effect.BLOCKED, Confidence.HIGH, TITLE)
    was_detouring = RouteSnapshot(
        frozenset({1, 2, 3, 4}), 5.1, 400.0, None, avoided=(old_hazard,)
    )
    assert (
        describe_change(
            was_detouring,
            before,
            profile_label="Standard",
            hazards={},
            removed=[closure],
        )
        == f"{TITLE} has cleared; the new route is 3 min shorter."
    )
    # Nothing to name, nothing measurable: the generic sentence.
    moved = RouteSnapshot(frozenset({7, 9}), 2.7, 200.0, None)
    assert (
        describe_change(before, moved, profile_label="Standard", hazards={})
        == "Conditions on your route changed; the new route takes about the same time."
    )


# ---------------------------------------------------------- the hub itself


def test_subscribe_ping_unsubscribe_and_errors(router: Router) -> None:
    async def scenario() -> None:
        hub = LiveHub(router=router, provider=lambda now: {}, clock=lambda: NOW)
        sock = FakeSocket()
        conn = hub.add(sock)
        reply = await hub.handle(conn, subscribe_text(1, 3))
        assert reply.type == "subscribed"
        assert reply.summary.possible and reply.summary.distance_m == 200.0
        assert reply.summary.eta_min == pytest.approx(200 / 1.3 / 60, abs=0.1)
        assert reply.route.model_dump(by_alias=True) == {
            "from": latlng(1),
            "to": latlng(3),
            "profile": "standard",
        }
        assert (
            conn.subscription is not None
            and conn.subscription.key.profile == "standard"
        )
        # A second subscribe replaces the route: one route per connection.
        reply = await hub.handle(conn, subscribe_text(1, 9, "wheelchair"))
        assert reply.type == "subscribed"
        assert conn.subscription.key == RouteKey(*latlng(1), *latlng(9), "wheelchair")
        assert (await hub.handle(conn, '{"type": "ping"}')).type == "pong"
        assert (
            await hub.handle(conn, '{"type": "unsubscribe"}')
        ).type == "unsubscribed"
        assert conn.subscription is None
        # Bad messages answer with an error and never close the connection.
        assert (await hub.handle(conn, "not json")).type == "error"
        assert (await hub.handle(conn, '{"type": "dance"}')).type == "error"
        bad = json.dumps(
            {"type": "subscribe", "route": {"from": [95, 0], "to": [0, 0]}}
        )
        error = await hub.handle(conn, bad)
        assert error.type == "error" and "lat, lng" in error.message
        far = json.dumps({"type": "subscribe", "route": {"from": [0, 0], "to": [0, 1]}})
        error = await hub.handle(conn, far)
        assert error.type == "error" and "nearest walkable path" in error.message
        assert conn.id in hub.connections

    run(scenario())


def test_without_a_network_subscribe_is_an_error_and_status_still_flows() -> None:
    async def scenario() -> None:
        hub = LiveHub(router=None, provider=None, clock=lambda: NOW)
        sock = FakeSocket()
        conn = hub.add(sock)
        reply = await hub.handle(conn, subscribe_text(1, 3))
        assert reply.type == "error" and "not available" in reply.message
        assert await hub.broadcast(HeartbeatEvent(at=NOW)) == 1
        assert hub._tick is None  # nothing to refresh without a provider

    run(scenario())


def test_broadcast_fans_out_and_drops_dead_sockets() -> None:
    async def scenario() -> None:
        hub = LiveHub(clock=lambda: NOW)
        good, dead, other = FakeSocket(), FakeSocket(fail=True), FakeSocket()
        for sock in (good, dead, other):
            hub.add(sock)
        assert len(hub.connections) == 3
        assert await hub.broadcast(HeartbeatEvent(at=NOW)) == 2
        assert len(hub.connections) == 2
        assert [m["type"] for m in good.sent] == ["heartbeat"]
        assert good.sent == other.sent
        assert dead.sent == []
        # A later broadcast skips the dead one without trying it again.
        assert await hub.broadcast(HeartbeatEvent(at=NOW)) == 2

    run(scenario())


def test_source_status_after_each_scheduler_run(store: ObstructionStore) -> None:
    rows = [
        {
            "source": "city_lrt_devices",
            "source_ref": "1",
            "category": "indoor",
            "kind": "elevator_out",
            "effect": "blocked",
            "title": "Elevator out of service",
            "geometry": {"type": "Point", "coordinates": [-113.49, 53.544]},
            "confidence": "high",
            "observed_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
            "attribution": "City of Edmonton Open Data",
        }
    ]
    outcomes: list[Any] = [rows, RuntimeError("boom")]

    async def fetch(now: datetime) -> Any:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def scenario() -> None:
        source = Source(name="city_lrt_devices", cadence_s=60, fetch=fetch, retries=0)
        ticks = [NOW]
        scheduler = Scheduler(store, [source], clock=lambda: ticks[0], backoff_s=(0,))
        hub = LiveHub(clock=lambda: ticks[0])
        scheduler.add_listener(hub.on_source_run)
        sock = FakeSocket()
        hub.add(sock)

        assert await scheduler.run_once(source) is True
        await hub.drain()
        [event] = sock.of_type("source_status")
        assert event == {
            "type": "source_status",
            "source": "city_lrt_devices",
            "ok": True,
            "stale": False,
            "item_count": 1,
            "age_s": 0.0,
            "last_error": None,
            "at": "2026-11-07T20:10:00Z",
        }

        ticks[0] = NOW + timedelta(minutes=2)
        assert await scheduler.run_once(source) is False
        await hub.drain()
        failed = sock.of_type("source_status")[1]
        assert failed["ok"] is False and failed["stale"] is False
        assert failed["last_error"] == "RuntimeError: boom"
        assert failed["item_count"] == 1  # the last good data is kept
        assert failed["age_s"] == 120.0

    run(scenario())


def test_a_failing_listener_never_breaks_the_scheduler(
    store: ObstructionStore, caplog: pytest.LogCaptureFixture
) -> None:
    async def fetch(now: datetime) -> list[Any]:
        return []

    def explode(status: Any, ok: bool) -> None:
        raise ValueError("listener bug")

    async def scenario() -> None:
        source = Source(name="city_lrt_devices", cadence_s=60, fetch=fetch)
        scheduler = Scheduler(store, [source], clock=lambda: NOW)
        scheduler.add_listener(explode)
        assert await scheduler.run_once(source) is True
        assert scheduler.status("city_lrt_devices").stale is False

    run(scenario())
    assert "LISTENER failed after SOURCE city_lrt_devices" in caplog.text


def test_obstruction_changed_is_the_diff_between_rebuilds(
    network: WalkNetwork,
    store: ObstructionStore,
    make_obstruction: Callable[..., Obstruction],
) -> None:
    async def scenario() -> None:
        provider = HazardProvider(store, network, refresh_s=0, background=False)
        hub = LiveHub(provider=provider, clock=lambda: NOW)
        provider.on_rebuild(hub.on_rebuild)
        sock = FakeSocket()
        hub.add(sock)

        provider(NOW)  # the first rebuild is the baseline: nothing to announce
        await hub.drain()
        assert sock.of_type("obstruction_changed") == []

        store.upsert([blocker(make_obstruction)], NOW)
        provider(NOW)
        await hub.drain()
        [added] = sock.of_type("obstruction_changed")
        assert added["added"] == [
            {
                "id": "city_construction:permit-1",
                "kind": "sidewalk_closed",
                "effect": "blocked",
                "title": TITLE,
            }
        ]
        assert added["removed"] == [] and added["at"] == "2026-11-07T20:10:00Z"

        provider(NOW)  # the same set again: nothing new
        await hub.drain()
        assert len(sock.of_type("obstruction_changed")) == 1

        store.replace_source("city_construction", [], NOW)
        provider(NOW)
        await hub.drain()
        removed = sock.of_type("obstruction_changed")[1]
        assert removed["added"] == []
        assert [brief["id"] for brief in removed["removed"]] == [
            "city_construction:permit-1"
        ]
        assert hub.rebuilds == 4

    run(scenario())


def test_a_rebuild_reroutes_each_subscription_once(
    network: WalkNetwork, router: Router
) -> None:
    """Changed paths announce once; the same map again announces nothing."""
    current: list[dict[int, list[EdgeHazard]]] = [{}]
    avenue_a = edge_between(network, 1, 2)

    async def scenario() -> None:
        hub = LiveHub(router=router, provider=lambda now: current[0], clock=lambda: NOW)
        watching, idle = FakeSocket(), FakeSocket()
        conn = hub.add(watching)
        hub.add(idle)
        subscribed = await hub.handle(conn, subscribe_text(1, 3))
        assert subscribed.type == "subscribed"
        baseline_eta = subscribed.summary.eta_min

        hub.on_rebuild(rebuild_result({}))  # baseline for the obstruction diff
        await hub.drain()
        current[0] = hazard(avenue_a)
        hub.on_rebuild(rebuild_result(current[0]))
        await hub.drain()

        [changed] = watching.of_type("route_changed")
        assert changed["route"] == {
            "from": list(latlng(1)),
            "to": list(latlng(3)),
            "profile": "standard",
        }
        assert changed["previous"]["eta_min"] == pytest.approx(baseline_eta)
        assert (
            changed["summary"]["possible"] and changed["summary"]["distance_m"] == 400.0
        )
        delta = changed["summary"]["eta_min"] - changed["previous"]["eta_min"]
        assert int(round(delta, 1) + 0.5) == 3  # halves round up
        assert (
            changed["change"]
            == f"{TITLE} now blocks your route; the new route is 3 min longer."
        )
        assert changed["summary"]["reason"] == f"Avoids {TITLE}; +3 min"
        # Both connections hear about the obstruction; only the subscriber is re-routed.
        assert len(watching.of_type("obstruction_changed")) == 1
        assert len(idle.of_type("obstruction_changed")) == 1
        assert idle.of_type("route_changed") == []

        hub.on_rebuild(rebuild_result(current[0]))  # unchanged: nothing
        await hub.drain()
        assert len(watching.of_type("route_changed")) == 1

    run(scenario())


def test_a_route_that_becomes_impossible_and_possible_again(
    network: WalkNetwork, router: Router
) -> None:
    """Node 1's two paths both blocked: no route; cleared: a route again (once each).

    Routing moves a blocked start to the nearest open path within ``snap.max_m``
    (1000 m), so on this small grid a route would always exist. A 60 m limit keeps
    the start at node 1, whose only paths are closed.
    """
    current: list[dict[int, list[EdgeHazard]]] = [{}]
    a, b = edge_between(network, 1, 2), edge_between(network, 1, 4)
    tight = Router(
        network,
        router.config.model_copy(update={"snap": Snap(warn_m=50, max_m=60)}),
    )

    async def scenario() -> None:
        hub = LiveHub(router=tight, provider=lambda now: current[0], clock=lambda: NOW)
        sock = FakeSocket()
        conn = hub.add(sock)
        assert (
            await hub.handle(conn, subscribe_text(1, 3, "wheelchair"))
        ).type == "subscribed"
        hub.on_rebuild(rebuild_result({}))
        await hub.drain()

        current[0] = {
            a: [
                EdgeHazard(
                    "city_construction:permit-1", Effect.BLOCKED, Confidence.HIGH, TITLE
                )
            ],
            b: [
                EdgeHazard(
                    "city_construction:permit-2",
                    Effect.BLOCKED,
                    Confidence.HIGH,
                    "Street 1 closed",
                )
            ],
        }
        hub.on_rebuild(rebuild_result(current[0]))
        await hub.drain()
        [gone] = sock.of_type("route_changed")
        assert gone["summary"] == {
            "possible": False,
            "eta_min": 0.0,
            "distance_m": 0.0,
            "reason": None,
        }
        assert (
            gone["change"]
            == f"{TITLE} now blocks your route; no route is possible for the Wheelchair profile."
        )

        hub.on_rebuild(rebuild_result(current[0]))  # still impossible: no repeat
        await hub.drain()
        assert len(sock.of_type("route_changed")) == 1

        current[0] = {}
        hub.on_rebuild(rebuild_result({}))
        await hub.drain()
        back = sock.of_type("route_changed")[1]
        assert back["summary"]["possible"] and back["summary"]["distance_m"] == 200.0
        assert back["change"] == "A route is possible again; it takes about 3 min."

    run(scenario())


def test_a_slower_route_on_the_same_paths_counts_from_20_percent(
    network: WalkNetwork, router: Router
) -> None:
    """Snow on Avenue A for a low-vision user doubles the cost of a path (x 2)."""
    current: list[dict[int, list[EdgeHazard]]] = [{}]
    avenue_a = edge_between(network, 1, 2)

    async def scenario() -> None:
        hub = LiveHub(router=router, provider=lambda now: current[0], clock=lambda: NOW)
        sock = FakeSocket()
        conn = hub.add(sock)
        reply = await hub.handle(conn, subscribe_text(1, 2, "low_vision"))
        assert reply.type == "subscribed" and reply.summary.eta_min == pytest.approx(
            100 / 60, abs=0.05
        )
        hub.on_rebuild(rebuild_result({}))
        await hub.drain()
        # Only one path joins 1 and 2 directly; the detour (300 m) costs more than 200 m of snow.
        current[0] = {
            avenue_a: [
                EdgeHazard(
                    "city_snow:1", Effect.SNOW, Confidence.MEDIUM, "Snow on the path"
                )
            ]
        }
        hub.on_rebuild(rebuild_result(current[0]))
        await hub.drain()
        [slower] = sock.of_type("route_changed")
        assert slower["summary"]["distance_m"] == 100.0  # the same path...
        assert slower["summary"]["eta_min"] == pytest.approx(
            200 / 60, abs=0.05
        )  # ...twice as slow
        assert (
            slower["change"]
            == "Snow on the path now affects your route; the new route is 2 min longer."
        )

    run(scenario())


def test_a_failing_rebuild_callback_never_breaks_matching(
    network: WalkNetwork, store: ObstructionStore, caplog: pytest.LogCaptureFixture
) -> None:
    provider = HazardProvider(store, network, refresh_s=0, background=False)

    def explode(result: MatchResult) -> None:
        raise ValueError("callback bug")

    provider.on_rebuild(explode)
    assert provider(NOW) == {}
    assert provider.latest is not None
    assert "MATCH rebuild callback failed" in caplog.text


# ---------------------------------------------------------------- the app


def test_attach_wires_the_scheduler_and_the_matcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_app`` puts a hub on ``app.state.live`` hooked into the matcher."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.delenv("DATA_MODE", raising=False)
    app = create_app()
    assert any(getattr(route, "path", None) == "/ws" for route in app.routes)
    with TestClient(app):
        hub = app.state.live
        assert isinstance(hub, LiveHub)
        assert hub.router is app.state.router
        matcher = ws.matcher_of(app.state.hazard_provider)
        assert matcher is not None and hub.on_rebuild in matcher._rebuild_callbacks
        assert hub.clock is app.state.clock


def test_the_endpoint_without_a_hub_closes_the_socket() -> None:
    app = FastAPI()
    app.include_router(ws.router)
    with TestClient(app) as client, client.websocket_connect("/ws") as sock:
        with pytest.raises(WebSocketDisconnect) as closed:
            sock.receive_text()
        assert closed.value.code == 1013


def live_app(
    network: WalkNetwork,
    router: Router,
    store: ObstructionStore,
    ticks: list[float] | None = None,
    **hub_options: Any,
) -> FastAPI:
    """The pieces ``main.py`` wires, on the grid: router, matcher, clock and hub.

    The matcher keeps its production 30 s refresh; ``ticks[0]`` is its monotonic
    clock, so a test decides when a rebuild is due.
    """
    ticks = ticks if ticks is not None else [0.0]
    app = FastAPI()
    app.state.store = store
    app.state.router = router
    app.state.hazard_provider = HazardProvider(
        store, network, refresh_s=30, clock=lambda: ticks[0]
    )
    app.state.clock = lambda: NOW
    app.state.live = ws.attach(app, **hub_options)
    app.include_router(ws.router)
    app.include_router(routing_api.router)
    app.dependency_overrides[obstructions_api.get_now] = lambda: NOW
    return app


def test_heartbeats_flow_and_a_gone_client_is_dropped(
    network: WalkNetwork, router: Router, store: ObstructionStore
) -> None:
    app = live_app(network, router, store, heartbeat_s=0.05)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as sock:
            hello = receive(sock, "hello")
            assert hello["heartbeat_s"] == 0.05
            receive(sock, "heartbeat")
            receive(sock, "heartbeat")
            sock.send_json({"type": "ping"})
            receive(sock, "pong")
            assert len(app.state.live.connections) == 1
        deadline = time.monotonic() + 5
        while app.state.live.connections and time.monotonic() < deadline:
            time.sleep(0.02)
        assert app.state.live.connections == {}


def test_end_to_end_a_seeded_closure_changes_the_subscribed_route(
    network: WalkNetwork,
    router: Router,
    store: ObstructionStore,
    make_obstruction: Callable[..., Obstruction],
) -> None:
    """The story's end-to-end check over the real endpoint.

    Subscribe a route on the open grid, seed a blocking closure on its path into the
    store, trigger the hazard rebuild (as the hub's tick or a route request would)
    and read ``obstruction_changed`` and ``route_changed`` with the new summary and
    the announcement sentence.
    """
    ticks = [0.0]
    app = live_app(network, router, store, ticks, heartbeat_s=60)
    with TestClient(app) as client, client.websocket_connect("/ws") as sock:
        receive(sock, "hello")
        sock.send_json(json.loads(subscribe_text(1, 3, "wheelchair")))
        subscribed = receive(sock, "subscribed")
        assert subscribed["summary"] == {
            "possible": True,
            "eta_min": pytest.approx(200 / 60, abs=0.05),
            "distance_m": 200.0,
            "reason": None,
        }

        store.upsert([blocker(make_obstruction)], NOW)  # the seeded change
        ticks[0] = 30.0  # the matcher's map is now due for a rebuild...
        app.state.hazard_provider(NOW)  # ...which starts in the background thread

        changed = receive(sock, "obstruction_changed")
        assert [brief["id"] for brief in changed["added"]] == [
            "city_construction:permit-1"
        ]
        assert changed["added"][0]["title"] == TITLE

        rerouted = receive(sock, "route_changed")
        assert rerouted["route"] == {
            "from": list(latlng(1)),
            "to": list(latlng(3)),
            "profile": "wheelchair",
        }
        assert rerouted["previous"] == subscribed["summary"]
        assert (
            rerouted["summary"]["possible"]
            and rerouted["summary"]["distance_m"] == 400.0
        )
        delta = rerouted["summary"]["eta_min"] - rerouted["previous"]["eta_min"]
        assert int(round(delta, 1) + 0.5) == 3  # halves round up
        assert (
            rerouted["change"]
            == f"{TITLE} now blocks your route; the new route is 3 min longer."
        )
        assert rerouted["at"] == "2026-11-07T20:10:00Z"

        # GET /api/route agrees, and answers while the socket is open.
        a, c = latlng(1), latlng(3)
        response = client.get(
            "/api/route",
            params={
                "from": f"{a[0]},{a[1]}",
                "to": f"{c[0]},{c[1]}",
                "profile": "wheelchair",
            },
        )
        assert response.status_code == 200
        best = response.json()["routes"][0]
        assert best["distance_m"] == rerouted["summary"]["distance_m"]
        assert best["eta_min"] == rerouted["summary"]["eta_min"]
        assert best["reason"] == rerouted["summary"]["reason"]

        # The hub's own timer keeps the map fresh while a client is connected.
        assert app.state.live._tick is not None and not app.state.live._tick.done()
    # The first map (at subscribe) and the seeded one; re-routing started no others.
    assert app.state.live.rebuilds == 2
