"""OP-304 endpoints and the watcher end to end, on a hand-built network.

The app here is a bare FastAPI with the alerts router, the test grid from
``test_routing`` and a hazard map the test controls. The last test runs the real
``create_app()`` on the committed Edmonton network to check the wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pywebpush import WebPushException

from cloud.app import push_api
from cloud.app.db import make_engine
from cloud.app.main import create_app
from cloud.app.network import RawEdge, RawNode, WalkNetwork, export_network
from cloud.app.profiles import load_routing_config
from cloud.app.push import (
    MAX_SUBSCRIPTIONS_PER_CLIENT,
    PushSender,
    PushServices,
    PushSubscriptionStore,
    RouteWatcher,
    generate_vapid_keys,
)
from cloud.app.routing import EdgeHazard, Router
from cloud.app.saved_routes import MAX_ROUTES_PER_CLIENT, SavedRouteStore
from cloud.app.schemas import Confidence, Effect
from cloud.tests.test_routing import grid_network, latlng

CLIENT = "c7Hq2pL9xZ4vT1mK8nR3"
OTHER = "zz9Yx8Ww7Vv6Uu5Tt4Ss"
START = datetime(2026, 11, 8, 15, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Hazards:
    """The hazard provider under test control. Returns a fresh dict per call, as
    the real providers do when their map changes."""

    def __init__(self) -> None:
        self.map: dict[int, list[EdgeHazard]] = {}
        self.calls = 0

    def __call__(self, now: datetime) -> dict[int, list[EdgeHazard]]:
        self.calls += 1
        return {edge: list(items) for edge, items in self.map.items()}

    def set(self, edge: int, item_id: str, effect: Effect) -> None:
        self.map[edge] = [EdgeHazard(item_id, effect, Confidence.HIGH, item_id)]

    def clear(self, edge: int) -> None:
        self.map.pop(edge, None)


class FakeWebPush:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail: dict[str, int] = {}

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        status = self.fail.get(kwargs["subscription_info"]["endpoint"])
        if status is not None:
            raise WebPushException(
                "nope", response=SimpleNamespace(status_code=status, text="")
            )
        return "ok"

    def payloads(self) -> list[dict]:
        import json

        return [json.loads(call["data"]) for call in self.calls]


class Harness:
    def __init__(
        self,
        network: WalkNetwork,
        *,
        keys=True,
        min_push_interval_s: float = 120.0,
        warmup_s: float = 0.0,
    ) -> None:
        self.clock = Clock()
        self.hazards = Hazards()
        self.webpush = FakeWebPush()
        engine = make_engine("sqlite://")
        self.subscriptions = PushSubscriptionStore(engine)
        self.subscriptions.create_tables()
        self.routes = SavedRouteStore(engine)
        self.routes.create_tables()
        self.router = Router(network, load_routing_config())
        self.sender = PushSender(
            self.subscriptions,
            generate_vapid_keys() if keys else None,
            send=self.webpush,
            clock=self.clock,
        )
        self.watcher = RouteWatcher(
            self.routes,
            self.router,
            self.hazards,
            self.sender,
            hazard_clock=self.clock,
            clock=self.clock,
            min_push_interval_s=min_push_interval_s,
            warmup_s=warmup_s,
        )
        app = FastAPI()
        app.include_router(push_api.router)
        app.state.router = self.router
        app.state.hazard_provider = self.hazards
        app.state.clock = self.clock
        app.state.push = PushServices(
            self.subscriptions, self.routes, self.sender, self.watcher
        )
        self.client = TestClient(app)

    # -- requests --------------------------------------------------------

    def subscribe(self, client=CLIENT, endpoint="https://push.example.net/s/1", **kw):
        body = {
            "client_id": client,
            "subscription": {
                "endpoint": endpoint,
                "expirationTime": None,
                "keys": {"p256dh": "BPx", "auth": "k3Z"},
            },
        }
        body.update(kw)
        return self.client.post("/api/push/subscribe", json=body)

    def save(self, start: int, end: int, profile="standard", client=CLIENT, **kw):
        s, e = latlng(start), latlng(end)
        body = {
            "client_id": client,
            "name": f"Node {end}",
            "from": {"lat": s[0], "lng": s[1]},
            "to": {"lat": e[0], "lng": e[1]},
            "profile": profile,
        }
        body.update(kw)
        return self.client.post("/api/saved-routes", json=body)

    def listed(self, client=CLIENT):
        return self.client.get(f"/api/saved-routes?client_id={client}").json()


@pytest.fixture
def grid():
    return grid_network()


@pytest.fixture
def h(grid) -> Harness:
    return Harness(grid[0])


# ------------------------------------------------------------- validation


def test_public_key_reports_the_configured_key(h, grid) -> None:
    body = h.client.get("/api/push/public-key").json()
    assert body == {"public_key": h.sender.public_key, "push_enabled": True}
    without = Harness(grid[0], keys=False)
    assert without.client.get("/api/push/public-key").json() == {
        "public_key": None,
        "push_enabled": False,
    }


@pytest.mark.parametrize(
    "client_id",
    ["short", "x" * 65, "has space in it 1234", "bad/chars/1234567890", ""],
)
def test_client_id_must_be_16_to_64_url_safe_characters(h, client_id) -> None:
    assert h.subscribe(client=client_id).status_code == 422
    assert h.save(1, 3, client=client_id).status_code == 422
    assert h.client.get(f"/api/saved-routes?client_id={client_id}").status_code == 422
    assert (
        h.client.post("/api/push/test", json={"client_id": client_id}).status_code
        == 422
    )


def test_subscription_validation(h) -> None:
    assert h.subscribe(endpoint="http://insecure.example/1").status_code == 422
    bad_keys = {"endpoint": "https://p.example/1", "keys": {"p256dh": "x"}}
    assert h.subscribe(subscription=bad_keys).status_code == 422
    assert h.subscribe(extra="field").status_code == 422
    response = h.subscribe()  # expirationTime from toJSON() is accepted and ignored
    assert response.status_code == 200
    assert response.json() == {
        "client_id": CLIENT,
        "created": True,
        "subscriptions": 1,
        "push_enabled": True,
    }
    assert h.subscribe().json()["created"] is False  # same endpoint: refreshed


def test_subscription_limit_is_429(h) -> None:
    for n in range(MAX_SUBSCRIPTIONS_PER_CLIENT):
        assert h.subscribe(endpoint=f"https://p.example/{n}").status_code == 200
    response = h.subscribe(endpoint="https://p.example/one-too-many")
    assert response.status_code == 429
    assert "at most 5" in response.json()["detail"]
    assert h.subscribe(client=OTHER, endpoint="https://p.example/o").status_code == 200


def test_saved_route_validation(h) -> None:
    assert h.save(1, 3, name="").status_code == 422
    assert h.save(1, 3, name="   ").status_code == 422
    assert h.save(1, 3, name="x" * 81).status_code == 422
    assert h.save(1, 3, profile="hoverboard").status_code == 422
    bad = h.client.post(
        "/api/saved-routes",
        json={
            "client_id": CLIENT,
            "name": "W",
            "from": {"lat": 95, "lng": 0},
            "to": {"lat": 0, "lng": 0},
            "profile": "standard",
        },
    )
    assert bad.status_code == 422
    far = h.client.post(
        "/api/saved-routes",
        json={
            "client_id": CLIENT,
            "name": "Far",
            "from": {"lat": 45.0, "lng": -75.0},
            "to": {"lat": 53.5444, "lng": -113.49},
            "profile": "standard",
        },
    )
    assert far.status_code == 422 and "nearest walkable path" in far.json()["detail"]


def test_save_list_and_delete(h) -> None:
    response = h.save(1, 3, name="  Work   desk ")
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1 and body["name"] == "Work desk" and body["status"] == "ok"
    assert body["from"] == {"lat": latlng(1)[0], "lng": latlng(1)[1]}
    assert body["to"] == {"lat": latlng(3)[0], "lng": latlng(3)[1]}
    assert body["profile"] == "standard"
    assert body["eta_min"] == body["baseline_eta_min"] == 2.6  # 200 m at 1.3 m/s
    assert body["created_at"] == "2026-11-08T15:00:00Z"
    assert body["last_checked_at"] == "2026-11-08T15:00:00Z"
    assert body["last_push_at"] is None
    assert body["url"].startswith("/?from=") and body["url"].endswith("&saved=1")
    assert h.watcher.dirty is True

    assert h.save(7, 9, client=OTHER).status_code == 201
    listed = h.listed()
    assert listed["client_id"] == CLIENT and [r["id"] for r in listed["routes"]] == [1]
    assert [r["id"] for r in h.listed(OTHER)["routes"]] == [2]

    assert h.client.delete(f"/api/saved-routes/1?client_id={OTHER}").status_code == 404
    assert h.client.delete(f"/api/saved-routes/1?client_id={CLIENT}").status_code == 204
    assert h.client.delete(f"/api/saved-routes/1?client_id={CLIENT}").status_code == 404
    assert h.listed()["routes"] == []


def test_saved_route_limit_is_429(h) -> None:
    for _ in range(MAX_ROUTES_PER_CLIENT):
        assert h.save(1, 3).status_code == 201
    response = h.save(1, 3)
    assert response.status_code == 429 and "at most 10" in response.json()["detail"]
    assert h.save(1, 3, client=OTHER).status_code == 201


def test_test_push_needs_keys_and_a_subscription(h, grid) -> None:
    missing = h.client.post("/api/push/test", json={"client_id": CLIENT})
    assert missing.status_code == 404
    h.subscribe()
    response = h.client.post("/api/push/test", json={"client_id": CLIENT})
    assert response.status_code == 200
    assert response.json() == {
        "client_id": CLIENT,
        "sent": 1,
        "subscriptions": 1,
        "push_enabled": True,
    }
    (payload,) = h.webpush.payloads()
    assert payload["title"] == "OpenPath alerts are on" and payload["status"] == "test"
    without = Harness(grid[0], keys=False)
    without.subscribe()
    assert (
        without.client.post("/api/push/test", json={"client_id": CLIENT}).status_code
        == 503
    )


def test_gone_subscription_is_removed_by_a_test_push(h) -> None:
    h.subscribe(endpoint="https://p.example/gone")
    h.subscribe(endpoint="https://p.example/fine")
    h.webpush.fail["https://p.example/gone"] = 410
    body = h.client.post("/api/push/test", json={"client_id": CLIENT}).json()
    assert body["sent"] == 1 and body["subscriptions"] == 1


def test_routes_are_unavailable_without_the_push_services() -> None:
    app = FastAPI()
    app.include_router(push_api.router)
    client = TestClient(app)
    assert client.get(f"/api/saved-routes?client_id={CLIENT}").status_code == 503
    assert client.get("/api/push/public-key").status_code == 503


# ------------------------------------------------------------- end to end


def test_block_change_clear_and_rate_limit(h, grid) -> None:
    """Save 1 -> 3, block its first edge, recover, and hold a second push."""
    _, index = grid
    h.subscribe()
    h.save(1, 3, name="Work")
    assert h.watcher.tick() == [1]  # the first pass checks every route once
    assert h.webpush.calls == []  # ...and nothing changed

    # A closure on 1 -> 2: the only way round is 1 -> 4 -> 5 -> 2 -> 3 (400 m).
    h.hazards.set(index[(1, 2)], "city_construction:1", Effect.BLOCKED)
    h.clock.advance(15)
    assert h.watcher.tick() == [1]
    (payload,) = h.webpush.payloads()
    assert payload["title"] == "Route changed: Work"
    assert payload["body"] == (
        "Your usual route to Work has changed; it now takes about 5 min (+2 min). "
        "Open OpenPath for the new route."
    )
    assert payload["tag"] == "route-1" and payload["status"] == "changed"
    assert payload["url"].endswith("&profile=standard&saved=1")
    (route,) = h.listed()["routes"]
    assert route["status"] == "changed" and route["eta_min"] == 5.1
    assert route["last_push_at"] == "2026-11-08T15:00:15Z"

    # An unrelated edge changing does not recheck this route.
    h.hazards.set(index[(7, 8)], "x:1", Effect.BLOCKED)
    h.clock.advance(15)
    assert h.watcher.tick() == []

    # The closure clears 30 s after the push: the route is back, but the push is
    # held by the rate limit...
    h.hazards.clear(index[(1, 2)])
    h.clock.advance(15)
    assert h.watcher.tick() == [1]
    assert len(h.webpush.calls) == 1
    (route,) = h.listed()["routes"]
    assert route["status"] == "ok" and route["eta_min"] == 2.6

    # ...until two minutes have passed since the last push.
    h.clock.advance(60)
    h.watcher.tick()
    assert len(h.webpush.calls) == 1
    h.clock.advance(30)  # 15:02:15, 120 s after the first push
    assert h.watcher.tick() == []  # nothing to recheck, but the owed push goes out
    assert len(h.webpush.calls) == 2
    assert h.webpush.payloads()[1]["body"] == (
        "Your usual route to Work is clear again (about 3 min)."
    )
    assert h.webpush.payloads()[1]["status"] == "clear"
    (route,) = h.listed()["routes"]
    assert route["last_push_at"] == "2026-11-08T15:02:15Z"


def test_eta_rise_without_a_detour_pushes_at_twenty_percent(h, grid) -> None:
    _, index = grid
    h.subscribe()
    h.save(1, 3, name="Work")
    h.watcher.tick()
    # Snow on 2 -> 3 is x1.5 for the standard profile: the same edges, 25% slower.
    h.hazards.set(index[(2, 3)], "snow:1", Effect.SNOW)
    h.watcher.tick()
    (payload,) = h.webpush.payloads()
    assert payload["status"] == "changed" and payload["body"].startswith(
        "Your usual route to Work has changed; it now takes about 3 min"
    )
    (route,) = h.listed()["routes"]
    assert route["status"] == "changed" and route["eta_min"] == 3.2


def test_caution_on_the_route_changes_nothing(h, grid) -> None:
    _, index = grid
    h.subscribe()
    h.save(1, 3)
    h.watcher.tick()
    h.hazards.set(index[(2, 3)], "advisory:1", Effect.CAUTION)
    assert h.watcher.tick() == [1]  # rechecked, because its edge changed...
    assert h.webpush.calls == []  # ...but the route is the same


def test_a_change_held_by_the_limit_is_dropped_if_it_reverts_first(h, grid) -> None:
    _, index = grid
    h.subscribe()
    h.save(1, 3)
    h.watcher.tick()
    h.hazards.set(index[(1, 2)], "c:1", Effect.BLOCKED)
    h.watcher.tick()
    assert len(h.webpush.calls) == 1  # changed, at 15:00:00
    h.hazards.clear(index[(1, 2)])
    h.clock.advance(15)
    h.watcher.tick()  # recovered, but the clear-again push is held
    assert len(h.webpush.calls) == 1
    h.clock.advance(120)
    h.watcher.tick()
    assert len(h.webpush.calls) == 2  # clear again, once allowed (15:02:15)
    assert h.webpush.payloads()[1]["status"] == "clear"
    # A change held by the limit that reverts before it can be sent: the browser
    # never heard of the problem, so there is nothing to say.
    h.hazards.set(index[(1, 2)], "c:2", Effect.BLOCKED)
    h.watcher.tick()
    assert len(h.webpush.calls) == 2  # held: pushed 0 s ago
    assert h.listed()["routes"][0]["status"] == "changed"
    h.hazards.clear(index[(1, 2)])
    h.watcher.tick()
    h.clock.advance(200)
    h.watcher.tick()
    assert len(h.webpush.calls) == 2
    assert h.listed()["routes"][0]["status"] == "ok"


def test_warm_up_sends_nothing_then_checks_every_route_once(grid) -> None:
    """After a restart, expired items are missing until the scheduler fetches again:
    the watcher must not mistake that for a recovery."""
    _, index = grid
    h = Harness(grid[0], warmup_s=120)
    h.subscribe()
    h.save(1, 3, name="Work")
    h.hazards.set(index[(1, 2)], "c:1", Effect.BLOCKED)
    assert h.watcher.tick() == []  # warming up: the map is read, nothing rechecked
    h.hazards.clear(index[(1, 2)])
    h.clock.advance(60)
    assert h.watcher.tick() == []
    assert h.webpush.calls == []
    h.hazards.set(index[(1, 2)], "c:1", Effect.BLOCKED)  # the scheduler caught up
    h.clock.advance(60)
    assert h.watcher.tick() == [1]  # 120 s: the one full recheck
    assert [p["status"] for p in h.webpush.payloads()] == ["changed"]
    h.clock.advance(15)
    assert h.watcher.tick() == []  # back to rechecking by changed edges only


def test_a_recovery_while_the_api_was_asleep_is_sent_after_the_warm_up(grid) -> None:
    _, index = grid
    h = Harness(grid[0])
    h.subscribe()
    h.save(1, 3, name="Work")
    h.watcher.tick()
    h.hazards.set(index[(1, 2)], "c:1", Effect.BLOCKED)
    h.watcher.tick()
    assert [p["status"] for p in h.webpush.payloads()] == ["changed"]
    # The instance sleeps; the closure ends meanwhile; a new process wakes up.
    h.hazards.clear(index[(1, 2)])
    h.clock.advance(3600)
    woke = RouteWatcher(
        h.routes,
        h.router,
        h.hazards,
        h.sender,
        hazard_clock=h.clock,
        clock=h.clock,
        warmup_s=120,
    )
    assert woke.tick() == []
    h.clock.advance(120)
    assert woke.tick() == [1]
    assert [p["status"] for p in h.webpush.payloads()] == ["changed", "clear"]


def test_a_newly_saved_route_is_rechecked_once(h, grid) -> None:
    _, index = grid
    h.subscribe()
    h.watcher.tick()
    h.save(1, 3)
    # Its edges changed after the baseline but before the watcher looked again.
    h.hazards.set(index[(1, 2)], "c:1", Effect.BLOCKED)
    h.watcher.previous = h.hazards(START)  # the watcher already saw the new map
    assert h.watcher.tick() == [1]
    assert [p["status"] for p in h.webpush.payloads()] == ["changed"]
    assert h.watcher.tick() == []


def test_start_and_end_at_the_same_place_is_422(h) -> None:
    response = h.save(1, 1)
    assert response.status_code == 422
    assert response.json()["detail"] == "The start and end are at the same place."


def test_deleted_route_is_dropped_from_the_index(h, grid) -> None:
    _, index = grid
    h.subscribe()
    h.save(1, 3)
    h.watcher.tick()
    h.client.delete(f"/api/saved-routes/1?client_id={CLIENT}")
    h.hazards.set(index[(1, 2)], "c:1", Effect.BLOCKED)
    assert h.watcher.tick() == []
    assert h.webpush.calls == []


def test_hazard_or_router_failures_never_raise(h, grid, monkeypatch) -> None:
    _, index = grid
    h.subscribe()
    h.save(1, 3)
    h.watcher.tick()

    def broken(now):
        raise RuntimeError("database down")

    h.watcher.hazards = broken
    assert h.watcher.tick() == []  # logged, kept the last map
    h.watcher.hazards = h.hazards
    h.hazards.set(index[(1, 2)], "c:1", Effect.BLOCKED)
    monkeypatch.setattr(
        h.router, "route", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    assert h.watcher.tick() == [1]  # the recheck failed and was logged
    assert h.webpush.calls == [] and h.listed()["routes"][0]["status"] == "ok"


def test_pushes_without_keys_are_counted_as_skipped(grid) -> None:
    _, index = grid
    quiet = Harness(grid[0], keys=False)
    quiet.subscribe()
    quiet.save(1, 3)
    quiet.watcher.tick()
    quiet.hazards.set(index[(1, 2)], "c:1", Effect.BLOCKED)
    quiet.watcher.tick()
    assert quiet.webpush.calls == []
    (route,) = quiet.listed()["routes"]
    assert route["status"] == "changed"  # the state is kept even without push


# -------------------------------------------------------------- impossible


def line_network() -> WalkNetwork:
    """1 -- 2 -- 3 with a 1.2 km last link: when it closes, 3 is cut off beyond
    the 1 km snap limit, so no route exists."""
    lat, lng = 53.5440, -113.4900
    nodes = [
        RawNode(1, lat, lng),
        RawNode(2, lat, lng + 0.0045),  # about 300 m east
        RawNode(3, lat, lng + 0.0225),  # about 1.5 km east
    ]
    edges = [
        RawEdge(1, 2, 300.0, highway="footway", name="A", osmid=101),
        RawEdge(2, 3, 1200.0, highway="footway", name="B", osmid=102),
    ]
    return WalkNetwork.from_data(export_network(nodes, edges, source="test"))


def test_impossible_then_clear_again() -> None:
    net = line_network()
    h = Harness(net)
    h.subscribe()
    body = {
        "client_id": CLIENT,
        "name": "Home",
        "from": {"lat": 53.5440, "lng": -113.4900},
        "to": {"lat": 53.5440, "lng": -113.4900 + 0.0225},
        "profile": "wheelchair",
    }
    saved = h.client.post("/api/saved-routes", json=body).json()
    assert saved["status"] == "ok" and saved["eta_min"] == 25.0  # 1500 m at 1 m/s
    h.watcher.tick()
    (last_edge,) = [e for e in range(net.edge_count) if net.edge_length_m[e] > 1000]
    h.hazards.set(last_edge, "closure:1", Effect.BLOCKED)
    h.clock.advance(15)
    assert h.watcher.tick() == [1]
    (payload,) = h.webpush.payloads()
    assert payload["title"] == "No route to Home" and payload["status"] == "impossible"
    assert payload["body"] == (
        "No Wheelchair route to Home is possible right now. "
        "Open OpenPath to see the barriers."
    )
    (route,) = h.listed()["routes"]
    assert route["status"] == "impossible" and route["eta_min"] is None

    # Impossible routes are rechecked on any change, here on an unrelated edge.
    h.hazards.set(0, "snow:1", Effect.CAUTION)
    h.clock.advance(15)
    assert h.watcher.tick() == [1]
    assert len(h.webpush.calls) == 1

    h.hazards.clear(last_edge)
    h.clock.advance(120)
    assert h.watcher.tick() == [1]
    assert h.webpush.payloads()[1]["body"] == (
        "Your usual route to Home is clear again (about 25 min)."
    )
    (route,) = h.listed()["routes"]
    assert route["status"] == "ok" and route["eta_min"] == 25.0


# --------------------------------------------------------------- real app


@pytest.fixture
def real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    for name in ("DATA_MODE", "RENDER_GIT_COMMIT", "VAPID_PUBLIC_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_real_app_wires_the_alerts_endpoints(real_env) -> None:
    with TestClient(create_app()) as client:
        app = client.app
        assert app.state.push.watcher is not None  # built with the router
        assert app.state.push.watcher._thread is None  # idle with SCHEDULER=off
        assert client.get("/api/push/public-key").json()["push_enabled"] is False
        response = client.post(
            "/api/saved-routes",
            json={
                "client_id": CLIENT,
                "name": "Work",
                "from": {"lat": 53.5444, "lng": -113.4909},
                "to": {"lat": 53.5461, "lng": -113.4938},
                "profile": "wheelchair",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "ok" and body["eta_min"] > 0
        assert app.state.push.watcher.tick() == [1]
        listed = client.get(f"/api/saved-routes?client_id={CLIENT}").json()
        assert [r["id"] for r in listed["routes"]] == [1]
        assert (
            client.delete(f"/api/saved-routes/1?client_id={CLIENT}").status_code == 204
        )
        schema = app.openapi()
        for path in (
            "/api/push/public-key",
            "/api/push/subscribe",
            "/api/push/test",
            "/api/saved-routes",
            "/api/saved-routes/{route_id}",
        ):
            assert path in schema["paths"]
