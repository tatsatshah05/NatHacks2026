"""OP-102: the app wiring: /health, the obstruction store and router, and CORS."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import obstructions_api
from cloud.app.main import cors_settings, create_app, data_mode
from cloud.app.schemas import Obstruction

TEAM_ORIGINS = "https://openpath.vercel.app,https://openpath-*-myteam.vercel.app"


@pytest.fixture(autouse=True)
def local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from a plain local environment and an in-memory database."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")  # OP-103: never fetch real sources in tests
    for name in ("RENDER_GIT_COMMIT", "DATA_MODE", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A client for the real app. ``with`` runs the lifespan, which opens the store."""
    with TestClient(app) as client:
        yield client


def test_health_locally(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "dev", "mode": "live"}


def test_health_reports_render_commit_and_replay_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "4e63531abc")
    monkeypatch.setenv("DATA_MODE", "replay")
    with TestClient(create_app()) as client:
        body = client.get("/health").json()
    assert body == {"status": "ok", "version": "4e63531abc", "mode": "replay"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, "live"), ("", "live"), ("live", "live"), ("replay", "replay")],
)
def test_data_mode(raw: str | None, expected: str) -> None:
    assert data_mode(raw) == expected


@pytest.mark.parametrize("raw", ["Replay", "demo", " live"])
def test_data_mode_rejects_anything_else(raw: str) -> None:
    with pytest.raises(ValueError, match="DATA_MODE"):
        data_mode(raw)


def test_bad_data_mode_stops_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_MODE", "demo")
    with pytest.raises(ValueError, match="DATA_MODE"):
        create_app()


def test_openapi_documents_health_and_obstructions(app: FastAPI) -> None:
    schema = app.openapi()
    assert "/health" in schema["paths"]
    assert "/api/obstructions" in schema["paths"]
    mode = schema["components"]["schemas"]["Health"]["properties"]["mode"]
    assert mode["enum"] == ["live", "replay"]


def test_lifespan_creates_the_store_tables(client: TestClient) -> None:
    """The tables made at startup are visible to the request thread."""
    response = client.get("/api/obstructions")
    assert response.status_code == 200
    assert response.json()["features"] == []


def test_store_items_reach_the_endpoint(
    app: FastAPI,
    client: TestClient,
    make_obstruction: Callable[..., Obstruction],
    now: datetime,
) -> None:
    """A write on this thread is read on the server's thread: one shared database."""
    app.dependency_overrides[obstructions_api.get_now] = lambda: now
    app.state.store.upsert([make_obstruction()], now)
    features = client.get("/api/obstructions").json()["features"]
    assert [feature["id"] for feature in features] == ["city_lrt_devices:1234"]


def test_cors_settings_splits_exact_and_wildcard() -> None:
    origins, regex = cors_settings(
        " https://openpath.vercel.app/ , ,https://openpath-*-myteam.vercel.app"
    )
    assert origins == ["https://openpath.vercel.app"]
    assert regex == r"^(?:https://openpath\-[a-z0-9-]+\-myteam\.vercel\.app)$"


def test_cors_settings_empty() -> None:
    assert cors_settings(None) == ([], None)


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("https://openpath.vercel.app", True),
        ("https://openpath-a1b2c3d4e-myteam.vercel.app", True),  # a commit preview
        ("https://openpath-git-op-102-skeleton-myteam.vercel.app", True),  # a branch
        ("https://openpath-a1b2c3d4e-otherteam.vercel.app", False),
        ("https://openpath-x-myteam.vercel.app.evil.com", False),
        ("https://evil.example.com", False),
    ],
)
def test_cors_allowlist(
    monkeypatch: pytest.MonkeyPatch, origin: str, allowed: bool
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", TEAM_ORIGINS)
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"Origin": origin})
    assert (response.headers.get("access-control-allow-origin") == origin) is allowed


def test_cors_never_allows_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No cookies cross origins, which is why CORS is not a security boundary here."""
    monkeypatch.setenv("CORS_ORIGINS", TEAM_ORIGINS)
    with TestClient(create_app()) as client:
        response = client.get(
            "/health", headers={"Origin": "https://openpath.vercel.app"}
        )
    assert "access-control-allow-credentials" not in response.headers
