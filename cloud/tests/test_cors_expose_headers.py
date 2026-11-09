"""The browser may read ``Retry-After`` across origins (OP-419, PIP waits the right time)."""

from fastapi.testclient import TestClient

from cloud.app.main import create_app


def test_retry_after_is_exposed_to_the_web_origin(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://openpath-lyart.vercel.app")
    monkeypatch.setenv("SCHEDULER", "off")
    with TestClient(create_app()) as client:
        response = client.get(
            "/health", headers={"Origin": "https://openpath-lyart.vercel.app"}
        )
    assert response.status_code == 200
    exposed = response.headers.get("access-control-expose-headers", "")
    assert "Retry-After" in [part.strip() for part in exposed.split(",")]
