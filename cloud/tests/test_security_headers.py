"""OP-401: the security headers on every API response (``cloud/app/security.py``),
and that CORS preflights, the docs pages and the WebSocket keep working."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from cloud.app.main import create_app
from cloud.app.security import (
    CSP,
    HSTS,
    SecurityHeadersMiddleware,
    is_https,
    response_headers,
)

ORIGIN = "https://openpath.vercel.app"
EXPECTED = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.setenv("CORS_ORIGINS", ORIGIN)
    for name in ("DATA_MODE", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


# ------------------------------------------------------------------ the helpers


def test_response_headers_by_method_path_and_scheme() -> None:
    read = dict(response_headers("GET", "/health", https=False))
    assert read["Content-Security-Policy"] == CSP
    assert "Cache-Control" not in read
    assert "Strict-Transport-Security" not in read
    write = dict(response_headers("POST", "/api/reports", https=True))
    assert write["Cache-Control"] == "no-store"
    assert write["Strict-Transport-Security"] == HSTS
    assert "Cache-Control" not in dict(
        response_headers("OPTIONS", "/api/reports", False)
    )
    assert "Cache-Control" not in dict(response_headers("HEAD", "/health", False))
    docs = dict(response_headers("GET", "/docs", https=False))
    assert "Content-Security-Policy" not in docs
    assert docs["X-Content-Type-Options"] == "nosniff"


def test_is_https_from_the_socket_or_the_proxy() -> None:
    def scope(scheme: str, proto: str | None = None) -> dict:
        headers = [] if proto is None else [(b"x-forwarded-proto", proto.encode())]
        return {"type": "http", "scheme": scheme, "headers": headers}

    assert is_https(scope("https"))
    assert not is_https(scope("http"))
    assert is_https(scope("http", "https"))
    assert is_https(scope("http", "https, http"))  # the first hop decides
    assert not is_https(scope("http", "http, https"))
    assert not is_https(scope("http", ""))


# ---------------------------------------------------------------------- the app


def test_reads_carry_the_headers_and_only_writes_get_no_store(
    client: TestClient,
) -> None:
    read = client.get("/health")
    for name, value in EXPECTED.items():
        assert read.headers[name] == value, name
    assert "strict-transport-security" not in read.headers
    assert "cache-control" not in read.headers
    write = client.post("/api/push/subscribe", json={})
    assert write.status_code == 422  # a validation error is decorated too
    assert write.headers["cache-control"] == "no-store"
    for name, value in EXPECTED.items():
        assert write.headers[name] == value, name


def test_errors_carry_them_too(client: TestClient) -> None:
    missing = client.get("/nope")
    assert missing.status_code == 404
    assert missing.headers["x-content-type-options"] == "nosniff"
    assert missing.headers["content-security-policy"] == CSP
    wrong_method = client.post("/health")
    assert wrong_method.status_code == 405
    assert wrong_method.headers["cache-control"] == "no-store"


def test_hsts_only_behind_https(client: TestClient) -> None:
    assert "strict-transport-security" not in client.get("/health").headers
    assert "strict-transport-security" not in (
        client.get("/health", headers={"X-Forwarded-Proto": "http"}).headers
    )
    behind_render = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert behind_render.headers["strict-transport-security"] == HSTS
    with TestClient(create_app(), base_url="https://testserver") as secure:
        assert secure.get("/health").headers["strict-transport-security"] == HSTS


def test_cors_preflight_and_simple_requests_still_work(client: TestClient) -> None:
    preflight = client.options(
        "/api/reports",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in preflight.headers["access-control-allow-methods"]
    assert preflight.headers["x-content-type-options"] == "nosniff"
    assert "cache-control" not in preflight.headers  # OPTIONS is a read
    simple = client.get("/health", headers={"Origin": ORIGIN})
    assert simple.headers["access-control-allow-origin"] == ORIGIN
    assert simple.headers["content-security-policy"] == CSP
    other = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_the_docs_pages_get_every_header_but_the_csp(client: TestClient) -> None:
    for path in ("/docs", "/redoc"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert "content-security-policy" not in page.headers, path
        assert page.headers["x-content-type-options"] == "nosniff"
        assert page.headers["x-frame-options"] == "DENY"
    schema = client.get("/openapi.json")
    assert schema.headers["content-security-policy"] == CSP


def test_websocket_connections_are_untouched(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        hello = socket.receive_json()
    assert hello["type"] == "hello"


def test_a_route_may_set_its_own_header() -> None:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.post("/cached")
    def cached() -> Response:
        return Response(
            "x",
            headers={
                "Cache-Control": "public, max-age=60",
                "Content-Security-Policy": "default-src 'self'",
            },
        )

    with TestClient(app) as client:
        response = client.post("/cached")
    assert response.headers["cache-control"] == "public, max-age=60"
    assert response.headers["content-security-policy"] == "default-src 'self'"
    assert response.headers["x-content-type-options"] == "nosniff"
