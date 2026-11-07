import pytest
from fastapi.testclient import TestClient

from cloud.app.main import app, cors_settings, create_app


def test_health_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "dev"}


def test_health_reports_render_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "4e63531abc")
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.json() == {"status": "ok", "version": "4e63531abc"}


def test_openapi_lists_health() -> None:
    assert "/health" in app.openapi()["paths"]


def test_cors_settings_splits_exact_and_wildcard() -> None:
    origins, regex = cors_settings(
        " https://openpath.vercel.app/ , ,https://openpath-*.vercel.app"
    )
    assert origins == ["https://openpath.vercel.app"]
    assert regex == r"^(?:https://openpath\-[a-z0-9-]+\.vercel\.app)$"


def test_cors_settings_empty() -> None:
    assert cors_settings(None) == ([], None)


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("https://openpath.vercel.app", True),
        ("https://openpath-git-op-102-team.vercel.app", True),
        ("https://evil.example.com", False),
        ("https://openpath-x.vercel.app.evil.com", False),
    ],
)
def test_cors_allowlist(
    monkeypatch: pytest.MonkeyPatch, origin: str, allowed: bool
) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS", "https://openpath.vercel.app,https://openpath-*.vercel.app"
    )
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"Origin": origin})
    assert (response.headers.get("access-control-allow-origin") == origin) is allowed
