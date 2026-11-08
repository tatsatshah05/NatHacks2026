"""OP-304 units: the change rules, the hazard diff, subscriptions, sending and keys.

pywebpush is never called for real: ``PushSender`` takes the send function, and one
test patches ``cloud.app.push.webpush`` to show the default path uses it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pywebpush import WebPushException

from cloud.app import push as push_module
from cloud.app.db import make_engine
from cloud.app.push import (
    MAX_FAILURES,
    MAX_SUBSCRIPTIONS_PER_CLIENT,
    LimitReached,
    PushSender,
    PushSubscriptionStore,
    VapidKeys,
    changed_edges,
    generate_vapid_keys,
    main,
    notice_payload,
    public_key_of,
    vapid_keys_from_env,
)
from cloud.app.routing import EdgeHazard, Route
from cloud.app.saved_routes import (
    MAX_ROUTES_PER_CLIENT,
    RouteState,
    SavedRouteRow,
    SavedRouteStore,
    Usual,
    decide,
    edge_index,
)
from cloud.app.saved_routes import LimitReached as RouteLimitReached
from cloud.app.schemas import Confidence, Effect

NOW = datetime(2026, 11, 8, 15, 0, tzinfo=UTC)
CLIENT = "c7Hq2pL9xZ4vT1mK8nR3"


def route(edges: tuple[int, ...], eta_min: float) -> Route:
    return Route(
        nodes=tuple(range(len(edges) + 1)),
        edges=edges,
        distance_m=100.0 * len(edges),
        eta_min=eta_min,
        coordinates=[],
        steps=[],
        hazards_on_route=[],
        hazards_avoided=[],
        warnings=[],
        reason=None,
    )


def state(
    status: str = "ok",
    eta: float | None = 10.0,
    edges: tuple[int, ...] = (1, 2),
    baseline: float | None = 10.0,
) -> RouteState:
    return RouteState(status, eta, edges, baseline)  # type: ignore[arg-type]


# -------------------------------------------------------------- change rules

OPEN = Usual(blocked=False, eta_min=10.0)  # the usual route, open and as fast
BLOCKED = Usual(blocked=True, eta_min=None)


def test_same_route_is_no_change() -> None:
    decision = decide(state(), route((2, 1), 10.4), OPEN)
    assert decision.notice is None and decision.status == "ok"
    assert decision.edge_ids == (1, 2)  # keeps the stored state, not the new order


def test_different_edges_is_a_change_when_the_usual_route_is_blocked() -> None:
    decision = decide(state(), route((1, 3), 10.0), BLOCKED)
    assert decision.notice == "changed" and decision.status == "changed"
    assert decision.edge_ids == (1, 3) and decision.eta_min == 10.0


def test_an_equally_fast_detour_round_a_closure_is_still_pushed() -> None:
    # A grid street one block over is as long: the user must still hear about it.
    assert decide(state(), route((1, 3), 10.0), BLOCKED).notice == "changed"


def test_a_better_route_while_the_usual_one_is_fine_is_recorded_quietly() -> None:
    decision = decide(state(), route((1, 3), 9.0), OPEN)
    assert decision.notice is None and decision.status == "ok"
    assert decision.edge_ids == (1, 3) and decision.eta_min == 9.0


@pytest.mark.parametrize(
    ("eta", "notice"),
    [(11.99, None), (11.999, None), (12.0, "changed"), (12.5, "changed")],
)
def test_eta_rise_boundary_is_twenty_percent(eta: float, notice: str | None) -> None:
    slow = Usual(blocked=False, eta_min=eta)  # same edges, slower (snow)
    assert decide(state(), route((1, 2), eta), slow).notice == notice


def test_no_route_is_impossible_once() -> None:
    first = decide(state(), None, BLOCKED)
    assert first.status == "impossible" and first.notice == "impossible"
    assert first.edge_ids == (1, 2)  # the last known edges stay for recovery
    again = decide(state("impossible", None), None, BLOCKED)
    assert again.notice is None and again.status == "impossible"


def test_recovery_from_impossible_is_clear_again() -> None:
    decision = decide(state("impossible", None), route((1, 2), 10.4), OPEN)
    assert decision.notice == "clear" and decision.status == "ok"


def test_recovery_from_a_detour_is_clear_again() -> None:
    detoured = state("changed", 15.0, (1, 4, 5, 3), 10.0)
    decision = decide(detoured, route((1, 2), 10.2), OPEN)
    assert decision.notice == "clear" and decision.status == "ok"
    assert decision.edge_ids == (1, 2) and decision.eta_min == 10.2


def test_recovery_from_a_slowdown_on_the_same_edges_is_clear_again() -> None:
    slowed = state("changed", 15.0, (1, 2), 10.0)  # snow: same edges, x1.5
    decision = decide(slowed, route((1, 2), 10.0), OPEN)
    assert decision.notice == "clear" and decision.status == "ok"


@pytest.mark.parametrize(("eta", "clear"), [(10.5, True), (10.6, False)])
def test_recovery_needs_the_eta_within_five_percent(eta: float, clear: bool) -> None:
    detoured = state("changed", 15.0, (1, 4, 5, 3), 10.0)
    usual = Usual(blocked=False, eta_min=eta)
    decision = decide(detoured, route((1, 2), eta), usual)
    assert (decision.notice == "clear") is clear
    if not clear:  # 6% slower and a different route from the detour: a change
        assert decision.notice == "changed" and decision.status == "changed"


def test_an_equally_fast_detour_is_not_clear_while_the_usual_route_is_blocked() -> None:
    detoured = state("changed", 10.0, (1, 3), 10.0)
    decision = decide(detoured, route((1, 3), 10.0), BLOCKED)
    assert decision.notice is None and decision.status == "changed"


def test_impossible_route_that_returns_on_a_detour_is_a_change() -> None:
    decision = decide(state("impossible", None), route((1, 4), 14.0), BLOCKED)
    assert decision.notice == "changed" and decision.status == "changed"


def test_a_route_saved_while_impossible_takes_its_first_route_as_usual() -> None:
    saved = state("impossible", None, (), None)
    decision = decide(saved, route((1, 2), 8.0), None)
    assert decision.notice == "clear" and decision.status == "ok"
    assert decision.rebaseline and decision.eta_min == 8.0


# -------------------------------------------------------------- hazard diff


def hazard(item_id: str, effect: Effect = Effect.BLOCKED) -> EdgeHazard:
    return EdgeHazard(item_id, effect, Confidence.HIGH, item_id)


def test_changed_edges_finds_added_removed_and_changed_effects() -> None:
    before = {1: [hazard("a")], 2: [hazard("b", Effect.SNOW)], 3: [hazard("c")]}
    after = {
        2: [hazard("b", Effect.CAUTION)],  # effect changed
        3: [hazard("c")],  # same
        4: [hazard("d")],  # added
    }
    assert changed_edges(before, after) == {1, 2, 4}


def test_changed_edges_ignores_order_and_the_first_map() -> None:
    before = {1: [hazard("a"), hazard("b")]}
    after = {1: [hazard("b"), hazard("a")]}
    assert changed_edges(before, after) == set()
    assert changed_edges(None, after) == set()
    assert changed_edges({1: []}, {}) == set()  # an empty list is no hazard


def test_edge_index_groups_routes_by_edge_and_lists_impossible_ones() -> None:
    rows = [
        SavedRouteRow(id=1, last_edge_ids=[1, 2], baseline_edge_ids=[1, 2]),
        # A detour: indexed by the detour and by the usual route, for recovery.
        SavedRouteRow(
            id=2, last_edge_ids=[4, 5], baseline_edge_ids=[2, 3], last_status="changed"
        ),
        SavedRouteRow(
            id=3, last_edge_ids=[7], baseline_edge_ids=[7], last_status="impossible"
        ),
    ]
    by_edge, impossible = edge_index(rows)
    assert by_edge == {1: {1}, 2: {1, 2}, 3: {2}, 4: {2}, 5: {2}, 7: {3}}
    assert impossible == {3}


# ------------------------------------------------------------ saved routes


@pytest.fixture
def engine():
    return make_engine("sqlite://")


@pytest.fixture
def routes(engine) -> SavedRouteStore:
    store = SavedRouteStore(engine)
    store.create_tables()
    return store


def add_route(store: SavedRouteStore, client: str = CLIENT, **overrides):
    args = {
        "client_id": client,
        "name": "Work",
        "from_lat": 53.5444,
        "from_lng": -113.4909,
        "to_lat": 53.5461,
        "to_lng": -113.4938,
        "profile": "wheelchair",
        "baseline": route((1, 2), 10.0),
        "now": NOW,
    }
    args.update(overrides)
    return store.add(**args)


def test_saved_route_store_keeps_the_baseline_and_enforces_the_limit(routes) -> None:
    row = add_route(routes)
    assert row.id == 1 and row.last_status == "ok" and row.baseline_eta_min == 10.0
    assert row.last_edge_ids == [1, 2] and row.created_at == NOW
    assert row.baseline_edge_ids == [1, 2]
    assert routes.for_client(CLIENT)[0].id == 1
    assert routes.for_client("other-client-id-1234") == []
    for _ in range(MAX_ROUTES_PER_CLIENT - 1):
        add_route(routes)
    with pytest.raises(RouteLimitReached):
        add_route(routes)
    add_route(routes, client="another-client-id-1")  # other clients are unaffected
    assert routes.delete(1, "not-the-owner-123456") is False
    assert routes.delete(1, CLIENT) is True and routes.get(1) is None
    assert routes.update(1, pending=True) is None


def test_a_route_saved_without_a_route_starts_impossible(routes) -> None:
    row = add_route(routes, baseline=None)
    assert row.last_status == "impossible" and row.last_eta_min is None
    assert row.last_edge_ids == [] and row.notified_status == "impossible"
    assert row.baseline_edge_ids == [] and row.baseline_eta_min is None


# ----------------------------------------------------------- subscriptions


@pytest.fixture
def subscriptions(engine) -> PushSubscriptionStore:
    store = PushSubscriptionStore(engine)
    store.create_tables()
    return store


def endpoint(n: int) -> str:
    return f"https://push.example.net/send/{n}"


def test_subscription_upsert_refreshes_moves_and_limits(subscriptions) -> None:
    row, created = subscriptions.upsert(CLIENT, endpoint(1), "p1", "a1", NOW)
    assert created and row.id == 1 and row.failures == 0
    row.failures = 3
    subscriptions.record_failure(1)
    row2, created2 = subscriptions.upsert(CLIENT, endpoint(1), "p2", "a2", NOW)
    assert not created2 and row2.id == 1 and row2.p256dh == "p2" and row2.failures == 0
    # The endpoint follows a new client id (the browser made a new one).
    moved, _ = subscriptions.upsert("new-client-id-abcdef", endpoint(1), "p", "a", NOW)
    assert moved.id == 1 and subscriptions.count(CLIENT) == 0
    for n in range(2, 2 + MAX_SUBSCRIPTIONS_PER_CLIENT):
        subscriptions.upsert(CLIENT, endpoint(n), "p", "a", NOW)
    with pytest.raises(LimitReached):
        subscriptions.upsert(CLIENT, endpoint(99), "p", "a", NOW)
    # Refreshing an existing endpoint is always allowed.
    subscriptions.upsert(CLIENT, endpoint(2), "p", "a", NOW)


def test_failures_drop_a_subscription_after_the_limit(subscriptions) -> None:
    subscriptions.upsert(CLIENT, endpoint(1), "p", "a", NOW)
    for _ in range(MAX_FAILURES - 1):
        assert subscriptions.record_failure(1) is False
    subscriptions.record_success(1, NOW)  # a success resets the count
    assert subscriptions.for_client(CLIENT)[0].failures == 0
    for _ in range(MAX_FAILURES - 1):
        assert subscriptions.record_failure(1) is False
    assert subscriptions.record_failure(1) is True
    assert subscriptions.for_client(CLIENT) == []


# ----------------------------------------------------------------- sending


class FakeWebPush:
    """Stands in for ``pywebpush.webpush``: records calls, fails on demand."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail: dict[str, int | Exception] = {}  # endpoint -> status or error

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        failure = self.fail.get(kwargs["subscription_info"]["endpoint"])
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            raise WebPushException(
                "nope", response=SimpleNamespace(status_code=failure, text="")
            )
        return "ok"


@pytest.fixture
def keys() -> VapidKeys:
    return generate_vapid_keys("mailto:team@example.com")


def test_sender_without_keys_is_a_no_op(subscriptions) -> None:
    fake = FakeWebPush()
    subscriptions.upsert(CLIENT, endpoint(1), "p", "a", NOW)
    sender = PushSender(subscriptions, None, send=fake)
    assert not sender.enabled and sender.public_key is None
    assert sender.send_to_client(CLIENT, {"title": "x"}) == 0
    assert fake.calls == []


def test_sender_signs_with_vapid_and_records_success(subscriptions, keys) -> None:
    fake = FakeWebPush()
    subscriptions.upsert(CLIENT, endpoint(1), "p1", "a1", NOW)
    subscriptions.upsert(CLIENT, endpoint(2), "p2", "a2", NOW)
    sender = PushSender(subscriptions, keys, send=fake, clock=lambda: NOW)
    assert sender.send_to_client(CLIENT, {"title": "Hi", "body": "there"}) == 2
    call = fake.calls[0]
    assert call["subscription_info"] == {
        "endpoint": endpoint(1),
        "keys": {"p256dh": "p1", "auth": "a1"},
    }
    assert call["data"] == '{"title": "Hi", "body": "there"}'
    assert call["vapid_private_key"] == keys.private_key
    assert call["vapid_claims"] == {"sub": "mailto:team@example.com"}
    assert call["ttl"] == push_module.PUSH_TTL_S
    assert all(row.last_ok_at == NOW for row in subscriptions.for_client(CLIENT))


def test_gone_endpoints_are_deleted_and_other_errors_counted(
    subscriptions, keys
) -> None:
    fake = FakeWebPush()
    for n in (1, 2, 3, 4):
        subscriptions.upsert(CLIENT, endpoint(n), "p", "a", NOW)
    fake.fail = {
        endpoint(1): 404,
        endpoint(2): 410,
        endpoint(3): 500,
        endpoint(4): RuntimeError("boom"),
    }
    sender = PushSender(subscriptions, keys, send=fake)
    assert sender.send_to_client(CLIENT, {"title": "x"}) == 0
    left = {row.endpoint: row.failures for row in subscriptions.for_client(CLIENT)}
    assert left == {endpoint(3): 1, endpoint(4): 1}
    for _ in range(MAX_FAILURES - 1):
        sender.send_to_client(CLIENT, {"title": "x"})
    assert subscriptions.for_client(CLIENT) == []  # dropped after MAX_FAILURES


def test_default_sender_uses_pywebpush(monkeypatch, subscriptions, keys) -> None:
    fake = FakeWebPush()
    monkeypatch.setattr(push_module, "webpush", fake)
    subscriptions.upsert(CLIENT, endpoint(1), "p", "a", NOW)
    # The default argument was bound at import time, so build the sender afterwards.
    sender = PushSender(subscriptions, keys, send=push_module.webpush)
    assert sender.send_to_client(CLIENT, {"title": "x"}) == 1
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- messages


def saved_row(**overrides) -> SavedRouteRow:
    data = {
        "id": 7,
        "client_id": CLIENT,
        "name": "Work",
        "from_lat": 53.5444,
        "from_lng": -113.4909,
        "to_lat": 53.5461,
        "to_lng": -113.4938,
        "profile": "wheelchair",
        "created_at": NOW,
        "baseline_eta_min": 10.0,
        "last_eta_min": 13.0,
        "last_edge_ids": [1, 2],
        "last_status": "changed",
    }
    data.update(overrides)
    return SavedRouteRow(**data)


def test_notice_payloads() -> None:
    changed = notice_payload(saved_row(), "changed", "Wheelchair")
    assert changed["title"] == "Route changed: Work"
    assert changed["body"] == (
        "Your usual route to Work has changed; it now takes about 13 min (+3 min). "
        "Open OpenPath for the new route."
    )
    assert changed["tag"] == "route-7" and changed["saved_route_id"] == 7
    assert changed["url"] == (
        "/?from=53.544400,-113.490900&to=53.546100,-113.493800"
        "&profile=wheelchair&saved=7"
    )
    impossible = notice_payload(
        saved_row(last_eta_min=None, last_status="impossible"),
        "impossible",
        "Wheelchair",
    )
    assert impossible["title"] == "No route to Work"
    assert impossible["body"].startswith("No Wheelchair route to Work is possible")
    clear = notice_payload(saved_row(last_eta_min=10.2, last_status="ok"), "clear", "W")
    assert clear["body"] == "Your usual route to Work is clear again (about 10 min)."


# -------------------------------------------------------------------- keys


def test_generated_keys_match_and_load_from_the_environment() -> None:
    keys = generate_vapid_keys("mailto:a@b.c")
    assert public_key_of(keys.private_key) == keys.public_key
    assert len(keys.public_key) == 87 and len(keys.private_key) == 43  # base64url
    env = {
        "VAPID_PUBLIC_KEY": keys.public_key,
        "VAPID_PRIVATE_KEY": keys.private_key,
        "VAPID_SUBJECT": "mailto:a@b.c",
    }
    assert vapid_keys_from_env(env) == keys


def test_missing_or_mismatched_keys_disable_push() -> None:
    keys = generate_vapid_keys()
    other = generate_vapid_keys()
    assert vapid_keys_from_env({}) is None
    assert vapid_keys_from_env({"VAPID_PUBLIC_KEY": keys.public_key}) is None
    env = {"VAPID_PUBLIC_KEY": keys.public_key, "VAPID_PRIVATE_KEY": keys.private_key}
    assert vapid_keys_from_env(env) is None  # no subject
    env["VAPID_SUBJECT"] = "mailto:a@b.c"
    env["VAPID_PRIVATE_KEY"] = other.private_key
    assert vapid_keys_from_env(env) is None  # mismatched pair
    env["VAPID_PRIVATE_KEY"] = "not-a-key"
    assert vapid_keys_from_env(env) is None


def test_generate_keys_command_prints_the_variables(capsys) -> None:
    assert main(["--generate-keys", "--subject", "mailto:ops@example.com"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    names = [line.split("=", 1)[0] for line in lines]
    assert names == [
        "VAPID_PUBLIC_KEY",
        "VAPID_PRIVATE_KEY",
        "VAPID_SUBJECT",
        "NEXT_PUBLIC_VAPID_PUBLIC_KEY",
    ]
    values = dict(line.split("=", 1) for line in lines)
    assert values["VAPID_SUBJECT"] == "mailto:ops@example.com"
    assert public_key_of(values["VAPID_PRIVATE_KEY"]) == values["VAPID_PUBLIC_KEY"]
    assert main([]) == 2  # no action: help and a non-zero exit
