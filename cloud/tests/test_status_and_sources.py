"""OP-103: GET /api/status reports every source, and replay mode builds every source offline."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.app import sources as src
from cloud.app.main import create_app
from cloud.app.scheduler import Scheduler, Source

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
NOW = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def quiet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")  # the app must not fetch anything in tests
    for name in ("DATA_MODE", "REPLAY_SNAPSHOT", "REPLAY_AT", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------ /api/status


def test_status_endpoint_with_scheduler_off() -> None:
    with TestClient(create_app()) as client:
        body = client.get("/api/status").json()
    assert body["mode"] == "live" and body["version"] == "dev"
    assert body["scheduler_running"] is False
    assert body["sources"] == []


def test_status_endpoint_lists_sources_after_a_run(store, make_obstruction) -> None:
    async def fetch(now: datetime):
        return [make_obstruction()]

    with TestClient(create_app()) as client:
        scheduler = Scheduler(
            store,
            [
                Source(
                    "city_lrt_devices",
                    120,
                    fetch,
                    url="https://data.edmonton.ca/d/snws-u3zx",
                )
            ],
            clock=lambda: NOW,
        )
        asyncio.run(scheduler.run_once(scheduler.sources[0]))
        client.app.state.scheduler = scheduler  # type: ignore[attr-defined]
        body = client.get("/api/status").json()
    (source,) = body["sources"]
    assert source["source"] == "city_lrt_devices"
    assert (
        source["item_count"] == 1
        and source["stale"] is False
        and source["last_error"] is None
    )
    assert source["url"].endswith("snws-u3zx") and source["cadence_s"] == 120
    assert "age_s" in source


def test_status_is_in_openapi() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/api/status" in paths and "/api/weather" in paths


def test_replay_mode_requires_a_snapshot_when_the_scheduler_runs(monkeypatch) -> None:
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("SCHEDULER", "on")
    with pytest.raises(ValueError, match="REPLAY_SNAPSHOT"), TestClient(create_app()):
        pass


# ------------------------------------------------------------- replay sources


def test_config_lists_every_known_source() -> None:
    configs = src.load_config()
    keys = {c.key for c in configs}
    assert keys == set(src._BUILDERS)
    assert all(c.enabled for c in configs)
    assert all(c.timeout_s >= 10 and c.retries == 3 for c in configs)


def test_replay_builds_every_source_and_fetches_offline() -> None:
    """Every adapter runs against the recorded files with no network at all."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"live request attempted: {request.url}")

    async def scenario() -> dict[str, int | None]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as a:
            with httpx.Client(transport=httpx.MockTransport(refuse)) as s:
                weather = src.build_weather_service("replay", FIXTURES, a)
                built = src.build_sources(
                    mode="replay",
                    snapshot=FIXTURES,
                    app_token=None,
                    async_client=a,
                    sync_client=s,
                    weather_service=weather,
                )
                counts: dict[str, int | None] = {}
                for source in built:
                    result = await source.fetch(NOW)
                    counts[source.name] = None if result is None else len(list(result))
                return counts

    counts = asyncio.run(scenario())
    # Adapters not merged yet are skipped, so only assert on what phase_1 has today.
    assert counts["city_lrt_devices"] >= 1  # recorded outages
    assert counts["city_311_snow"] >= 0 and counts["city_trail_closures"] >= 0
    assert counts["city_traffic_disruptions"] >= 1
    assert counts["weather_watch"] is None  # status-only source
    assert set(counts) >= {
        "city_lrt_devices",
        "city_311_snow",
        "city_trail_closures",
        "city_traffic_disruptions",
        "weather_watch",
    }


def test_replay_transport_serves_first_page_then_empty() -> None:
    transport = src.replay_transport(FIXTURES)
    client = httpx.Client(transport=transport)
    first = client.get(
        "https://data.edmonton.ca/resource/k4tx-5k8p.json", params={"$offset": 0}
    ).json()
    second = client.get(
        "https://data.edmonton.ca/resource/k4tx-5k8p.json", params={"$offset": 1000}
    ).json()
    assert len(first) == 30 and second == []
    assert (
        client.get("https://data.edmonton.ca/resource/unknown.json").status_code == 404
    )


def test_replay_clock_can_be_frozen(monkeypatch) -> None:
    monkeypatch.setenv("REPLAY_AT", "2026-11-10T18:00:00Z")
    assert src.replay_clock(FIXTURES)() == NOW
    monkeypatch.setenv("REPLAY_AT", "2026-11-10T18:00:00")
    with pytest.raises(ValueError, match="time zone"):
        src.replay_clock(FIXTURES)
    monkeypatch.delenv("REPLAY_AT")
    assert src.replay_clock(FIXTURES)().tzinfo is not None


def test_unknown_and_disabled_sources_are_skipped() -> None:
    configs = [
        src.SourceConfig(key="nope"),
        src.SourceConfig(key="lrt", enabled=False),
        src.SourceConfig(key="traffic_disruptions", cadence_s=5),
    ]
    a = httpx.AsyncClient()
    with httpx.Client() as s:
        built = src.build_sources(
            mode="replay",
            snapshot=FIXTURES,
            app_token=None,
            async_client=a,
            sync_client=s,
            weather_service=None,
            configs=configs,
        )
    assert [b.name for b in built] == ["city_traffic_disruptions"]
    assert built[0].cadence_s == 5  # the config override wins over the adapter default
