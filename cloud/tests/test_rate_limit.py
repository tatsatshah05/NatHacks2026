"""OP-401: per-connection limits on the public write routes (``cloud/app/rate_limit.py``)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from cloud.app.admin_api import require_admin_key
from cloud.app.ingest_api import require_worker_key
from cloud.app.main import create_app
from cloud.app.rate_limit import (
    DEFAULT_PER_MIN,
    KEYED_FACTOR,
    RateLimiter,
    RateLimits,
    client_key,
    keyed_write_limit,
    limit_per_min,
    public_write_limit,
)
from cloud.tests.test_camera_ingest import KEY as WORKER_KEY
from cloud.tests.test_camera_ingest import reading

CLIENT = "c7Hq2pL9xZ4vT1mK8nR3"
HOP_A = "203.0.113.9"
HOP_B = "198.51.100.4"


def subscription(n: int = 1, client_id: str = CLIENT) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "subscription": {
            "endpoint": f"https://push.example/{n}",
            "keys": {"p256dh": "p", "auth": "a"},
        },
    }


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ------------------------------------------------------------------ RateLimiter


def test_allows_the_limit_then_refuses_until_the_window_ends() -> None:
    clock = Clock()
    limiter = RateLimiter(3, window_s=60, clock=clock)
    decisions = [limiter.check("a") for _ in range(3)]
    assert [d.allowed for d in decisions] == [True, True, True]
    assert [d.remaining for d in decisions] == [2, 1, 0]
    refused = limiter.check("a")
    assert not refused.allowed
    assert refused.retry_after_s == 60
    assert refused.first_refusal
    clock.advance(20)
    again = limiter.check("a")
    assert not again.allowed
    assert again.retry_after_s == 40  # the window ends 40 s from now
    assert not again.first_refusal  # one log line per window
    clock.advance(40)
    assert limiter.check("a").allowed  # a new window


def test_clients_are_counted_apart() -> None:
    limiter = RateLimiter(1)
    assert limiter.check("a").allowed
    assert limiter.check("b").allowed
    assert not limiter.check("a").allowed
    assert not limiter.check("b").allowed
    assert limiter.tracked() == 2


def test_zero_turns_the_limiter_off() -> None:
    limiter = RateLimiter(0)
    assert not limiter.enabled
    assert all(limiter.check("a").allowed for _ in range(100))
    assert limiter.tracked() == 0


def test_a_negative_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="limit"):
        RateLimiter(-1)


def test_memory_is_bounded() -> None:
    clock = Clock()
    limiter = RateLimiter(5, window_s=60, clock=clock, max_keys=10)
    for n in range(10):
        limiter.check(f"k{n}")
    assert limiter.tracked() == 10
    clock.advance(61)
    assert limiter.check("new").allowed  # the ended windows are pruned first
    assert limiter.tracked() == 1
    for n in range(9):
        limiter.check(f"m{n}")
    assert limiter.tracked() == 10
    clock.advance(1)
    assert limiter.check("one-more").allowed  # full: the oldest window goes
    assert limiter.tracked() == 10
    assert not any(key == "new" for key in limiter._windows)


# -------------------------------------------------------------------- settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 30), ("", 30), ("  ", 30), ("0", 0), ("12", 12), (" 7 ", 7)],
)
def test_limit_per_min(raw: str | None, expected: int) -> None:
    assert limit_per_min(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "-5", "1.5"])
def test_a_bad_value_keeps_the_default_and_warns(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="openpath.rate_limit")
    assert limit_per_min(raw) == DEFAULT_PER_MIN
    assert "RATE_LIMIT_PER_MIN" in caplog.text


def test_from_env_builds_both_limiters() -> None:
    limits = RateLimits.from_env("4")
    assert limits.public.limit == 4
    assert limits.keyed.limit == 4 * KEYED_FACTOR
    assert not RateLimits.from_env("0").public.enabled
    assert not RateLimits.from_env("0").keyed.enabled


# ------------------------------------------------------------------ client key


def request_with(headers: dict[str, str], client: tuple[str, int] | None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


def test_client_key_prefers_the_first_forwarded_hop() -> None:
    socket = ("10.0.0.2", 1234)
    assert (
        client_key(request_with({"X-Forwarded-For": f" {HOP_A} , 10.0.0.1"}, socket))
        == HOP_A
    )
    assert client_key(request_with({}, socket)) == "10.0.0.2"
    assert client_key(request_with({"X-Forwarded-For": "  "}, socket)) == "10.0.0.2"
    assert client_key(request_with({}, None)) == "unknown"


# --------------------------------------------------------------------- the app


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.setenv("WORKER_KEY", WORKER_KEY)
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    for name in ("DATA_MODE", "CORS_ORIGINS", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client(env: None) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


def test_the_third_public_write_in_a_minute_is_429(client: TestClient) -> None:
    assert client.post("/api/push/subscribe", json=subscription(1)).status_code == 200
    assert client.post("/api/push/subscribe", json=subscription(2)).status_code == 200
    refused = client.post("/api/push/subscribe", json=subscription(3))
    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert refused.json()["detail"].startswith(
        "Too many requests from your connection in the last minute. Please wait "
    )
    assert refused.json()["detail"].endswith(" and try again.")
    assert refused.headers["cache-control"] == "no-store"  # security.py, a write
    # Nothing was stored for the refused request.
    assert client.post("/api/push/subscribe", json=subscription(3)).status_code == 429


def test_every_public_write_shares_the_budget_but_reads_never_do(
    client: TestClient,
) -> None:
    assert client.post("/api/push/subscribe", json=subscription(1)).status_code == 200
    # Counted before the route runs: this one answers 503 (no VAPID keys) and counts.
    assert client.post("/api/push/test", json={"client_id": CLIENT}).status_code == 503
    assert client.delete(f"/api/saved-routes/1?client_id={CLIENT}").status_code == 429
    assert client.post("/api/reports", json={}).status_code == 429
    assert client.post("/api/saved-routes", json={}).status_code == 429
    # GET routes are never limited.
    assert client.get("/health").status_code == 200
    assert client.get("/api/obstructions").status_code == 200
    assert client.get(f"/api/saved-routes?client_id={CLIENT}").status_code == 200
    assert client.get("/api/push/public-key").status_code == 200


def test_invalid_bodies_count_too(client: TestClient) -> None:
    """A flood of garbage is still a flood: the limit runs before validation."""
    assert client.post("/api/push/subscribe", json={"nope": 1}).status_code == 422
    assert client.post("/api/push/subscribe", json={"nope": 1}).status_code == 422
    assert client.post("/api/push/subscribe", json={"nope": 1}).status_code == 429


def test_each_connection_has_its_own_budget(client: TestClient) -> None:
    a = {"X-Forwarded-For": f"{HOP_A}, 10.0.0.1"}
    b = {"X-Forwarded-For": HOP_B}
    for n in (1, 2):
        assert (
            client.post(
                "/api/push/subscribe", json=subscription(n), headers=a
            ).status_code
            == 200
        )
    assert (
        client.post("/api/push/subscribe", json=subscription(3), headers=a).status_code
        == 429
    )
    assert (
        client.post("/api/push/subscribe", json=subscription(4), headers=b).status_code
        == 200
    )
    # No header: the socket address, which is its own client.
    assert client.post("/api/push/subscribe", json=subscription(5)).status_code == 200


def test_keyed_routes_check_the_key_first_and_get_a_looser_limit(
    client: TestClient,
) -> None:
    keyed_limit = 2 * KEYED_FACTOR
    # Requests without the key are refused and never counted.
    for _ in range(keyed_limit + 5):
        assert client.post("/api/ingest/camera", json=reading()).status_code == 401
    for n in range(keyed_limit):
        response = client.post(
            "/api/ingest/camera",
            json=reading(source_ref=f"511:1234:{n}", view_id=n),
            headers={"X-Worker-Key": WORKER_KEY},
        )
        assert response.status_code == 200, (n, response.text)
    refused = client.post(
        "/api/ingest/camera", json=reading(), headers={"X-Worker-Key": WORKER_KEY}
    )
    assert refused.status_code == 429
    assert "Retry-After" in refused.headers
    # Still 401 without the key, even over the limit.
    assert client.post("/api/ingest/camera", json=reading()).status_code == 401


def test_zero_turns_every_limit_off(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")
    with TestClient(create_app()) as client:
        statuses = {
            client.post(
                "/api/push/subscribe", json=subscription(n, f"client{n:016d}")
            ).status_code
            for n in range(DEFAULT_PER_MIN + 10)
        }
    assert statuses == {200}


def test_the_first_refusal_in_a_window_is_logged_once(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="openpath.rate_limit")
    for n in range(5):
        client.post("/api/push/subscribe", json=subscription(n))
    lines = [r.message for r in caplog.records if r.message.startswith("RATE_LIMIT")]
    assert lines == ["RATE_LIMIT public POST /api/push/subscribe over 2 per minute"]


def test_every_write_route_is_keyed_or_limited(env: None) -> None:
    """A new POST or DELETE route must take a key or the public limit (or both)."""
    app = create_app()
    unguarded: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.methods - {"GET", "HEAD", "OPTIONS"}:
            continue
        calls = {dependency.call for dependency in route.dependant.dependencies}
        has_key = require_worker_key in calls or require_admin_key in calls
        keyed = has_key and keyed_write_limit in calls
        if not (public_write_limit in calls or keyed):
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    assert unguarded == []


def test_an_app_without_limits_never_blocks() -> None:
    """The small test apps have no ``app.state.rate_limits``: the dependency is a no-op."""
    app = FastAPI()

    @app.post("/w", dependencies=[Depends(public_write_limit)])
    def write() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        assert all(client.post("/w").status_code == 200 for _ in range(50))
