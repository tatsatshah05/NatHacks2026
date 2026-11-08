"""REPLAY_AT freezes one clock for the scheduler, the map and route hazards.

Recorded closures are only active around the time they were recorded. With
``REPLAY_AT`` set, the scheduler stores them at that time, so the map
(``GET /api/obstructions``) and route hazards (``GET /api/route``) must read the
store at that time too; on the real clock every recorded item looks expired.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cloud.app.main import create_app
from cloud.app.network import DEFAULT_NETWORK_PATH
from cloud.app.schemas import Obstruction

REPLAY_AT = "2026-11-07T20:10:00Z"  # conftest's NOW


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")  # the test stores its own items
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", "cloud/tests/fixtures")
    monkeypatch.setenv("REPLAY_AT", REPLAY_AT)


def test_the_map_reads_the_store_at_replay_at(
    replay, monkeypatch, make_obstruction: Callable[..., Obstruction], now
) -> None:
    item = make_obstruction()  # active at REPLAY_AT, expired 10 minutes later
    assert item.expires_at < datetime.now(UTC)  # long expired on the real clock

    app = create_app()
    with TestClient(app) as client:
        app.state.store.upsert([item], now)
        body = client.get("/api/obstructions").json()
    assert [feature["id"] for feature in body["features"]] == [item.id]
    assert body["generated_at"] == REPLAY_AT

    # Without REPLAY_AT replay uses the real clock, and the recorded item is gone.
    monkeypatch.delenv("REPLAY_AT")
    app = create_app()
    with TestClient(app) as client:
        app.state.store.upsert([item], now)
        assert client.get("/api/obstructions").json()["features"] == []


@pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)
def test_route_hazards_use_replay_at(
    replay, make_obstruction: Callable[..., Obstruction], now
) -> None:
    churchill, rogers = "53.5444,-113.4909", "53.5468,-113.4973"
    app = create_app()
    with TestClient(app) as client:
        # Close the middle of the plain wheelchair route, at a point inside one edge.
        router = app.state.router
        plain = router.route(53.5444, -113.4909, 53.5468, -113.4973, "wheelchair")
        edge = plain.routes[0].edges[len(plain.routes[0].edges) // 2]
        (lng0, lat0), (lng1, lat1) = router.network.edge_coords(edge)[:2].tolist()
        closure = make_obstruction(
            source="city_construction",
            source_ref="replay-1",
            category="sidewalk",
            kind="sidewalk_closed",
            effect="blocked",
            title="Sidewalk closed",
            geometry={
                "type": "Point",
                "coordinates": [(lng0 + lng1) / 2, (lat0 + lat1) / 2],
            },
            expires_at=now + timedelta(minutes=10),  # expired on the real clock
        )
        app.state.store.upsert([closure], now)
        response = client.get(
            "/api/route",
            params={"from": churchill, "to": rogers, "profile": "wheelchair"},
        )
    assert response.status_code == 200, response.text
    assert app.state.hazard_provider.latest.built_at == now  # matched at REPLAY_AT
    best = response.json()["routes"][0]
    report = {
        i["id"]: i["status"] for g in best["report"]["groups"] for i in g["items"]
    }
    assert report.get("city_construction:replay-1") == "avoided"
