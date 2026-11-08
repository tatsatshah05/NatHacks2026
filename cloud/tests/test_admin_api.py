"""OP-406: ``POST /api/admin/override``, the seeded change the end-to-end tests use.

The key rules mirror ``/api/ingest/camera``. The live test runs the real endpoint on
``test_ws``'s 3 x 3 grid: a page subscribes a route over ``WS /ws``, the override
blocks its path, and ``route_changed`` arrives in the same request's wake because
the override rebuilds the hazard map at once instead of waiting for the refresh.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cloud.app import admin_api
from cloud.app.admin_api import ATTRIBUTION, SOURCE, TITLE_PREFIX
from cloud.app.main import create_app
from cloud.app.matching import HazardProvider
from cloud.app.network import WalkNetwork
from cloud.app.profiles import load_routing_config
from cloud.app.routing import Router
from cloud.app.store import ObstructionStore
from cloud.tests.test_ws import (
    DLNG,
    LAT0,
    LNG0,
    NOW,
    grid_network,
    latlng,
    live_app,
    receive,
    subscribe_text,
)

KEY = "e2e"
POINT = {"type": "Point", "coordinates": [LNG0 + DLNG / 2, LAT0]}  # mid edge 1-2


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_ref": "e2e-1",
        "title": "Sidewalk closed for a test",
        "geometry": POINT,
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.setenv("ADMIN_KEY", KEY)
    for name in ("DATA_MODE", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def post(client: TestClient, data: dict[str, Any], key: str | None = KEY) -> Any:
    headers = {"X-Admin-Key": key} if key is not None else {}
    return client.post("/api/admin/override", json=data, headers=headers)


def active_ids(client: TestClient) -> list[str]:
    features = client.get("/api/obstructions").json()["features"]
    return sorted(f["id"] for f in features if f["properties"]["source"] == SOURCE)


# --------------------------------------------------------------------- keys


def test_missing_or_wrong_key_is_rejected(client: TestClient) -> None:
    assert post(client, body(), key=None).status_code == 401
    assert post(client, body(), key="nope").status_code == 401
    assert client.delete("/api/admin/override").status_code == 401
    assert active_ids(client) == []  # nothing was stored


def test_non_ascii_key_is_a_401_not_a_500(client: TestClient) -> None:
    response = client.post(
        "/api/admin/override",
        json=body(),
        headers={"X-Admin-Key": "clé".encode("latin-1")},
    )
    assert response.status_code == 401


def test_server_without_an_admin_key_answers_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADMIN_KEY")
    with TestClient(create_app()) as c:
        assert post(c, body()).status_code == 503


# ------------------------------------------------------------------ writes


def test_post_stores_a_labelled_item_that_the_map_lists(client: TestClient) -> None:
    response = post(client, body(detail="Only for a test.", ttl_s=120))
    assert response.status_code == 201, response.text
    out = response.json()
    assert out["id"] == f"{SOURCE}:e2e-1"
    assert out["source"] == SOURCE
    assert out["title"] == f"{TITLE_PREFIX}Sidewalk closed for a test"
    assert out["effect"] == "blocked"
    observed = out["observed_at"]
    expires = out["expires_at"]
    assert expires > observed
    # The real app loads the walking network, so the map was rebuilt in the request.
    assert out["rebuilt"] is True

    features = client.get("/api/obstructions").json()["features"]
    [feature] = [f for f in features if f["properties"]["source"] == SOURCE]
    props = feature["properties"]
    assert props["title"].startswith(TITLE_PREFIX)
    assert props["attribution"] == ATTRIBUTION
    assert props["kind"] == "blocked" and props["category"] == "sidewalk"
    assert props["detail"] == "Only for a test."
    assert feature["geometry"] == POINT


def test_defaults_and_overrides_of_the_optional_fields(client: TestClient) -> None:
    response = post(
        client,
        body(
            source_ref="icy",
            kind="ice_risk",
            effect="ice_risk",
            category="road",
            confidence="medium",
        ),
    )
    assert response.status_code == 201
    assert response.json()["effect"] == "ice_risk"
    features = client.get("/api/obstructions").json()["features"]
    [feature] = [f for f in features if f["id"] == f"{SOURCE}:icy"]
    assert feature["properties"]["category"] == "road"
    assert feature["properties"]["confidence"] == "medium"


def test_the_same_ref_updates_instead_of_duplicating(client: TestClient) -> None:
    assert post(client, body(title="First")).status_code == 201
    assert post(client, body(title="Second")).status_code == 201
    assert active_ids(client) == [f"{SOURCE}:e2e-1"]
    features = client.get("/api/obstructions").json()["features"]
    [feature] = [f for f in features if f["properties"]["source"] == SOURCE]
    assert feature["properties"]["title"] == f"{TITLE_PREFIX}Second"


@pytest.mark.parametrize(
    "bad",
    [
        body(source_ref="has space"),
        body(source_ref=""),
        body(title=""),
        body(ttl_s=5),
        body(ttl_s=86400),
        body(geometry={"type": "Point", "coordinates": [53.5, -113.5, 1, 2]}),
        body(effect="closed"),
        {**body(), "source": "city_construction"},  # the source is never a choice
    ],
)
def test_invalid_bodies_are_422(client: TestClient, bad: dict[str, Any]) -> None:
    assert post(client, bad).status_code == 422
    assert active_ids(client) == []


def test_delete_one_and_delete_all(client: TestClient) -> None:
    assert post(client, body(source_ref="a")).status_code == 201
    assert post(client, body(source_ref="b")).status_code == 201
    assert post(client, body(source_ref="c")).status_code == 201
    headers = {"X-Admin-Key": KEY}
    assert client.delete("/api/admin/override/b", headers=headers).status_code == 204
    assert active_ids(client) == [f"{SOURCE}:a", f"{SOURCE}:c"]
    assert client.delete("/api/admin/override/b", headers=headers).status_code == 404
    assert client.delete("/api/admin/override", headers=headers).status_code == 204
    assert active_ids(client) == []
    # Clearing an empty set is fine too.
    assert client.delete("/api/admin/override", headers=headers).status_code == 204


def test_an_override_expires_by_itself() -> None:
    item = admin_api.to_obstruction(
        admin_api.OverrideIn.model_validate(body(ttl_s=60)), NOW
    )
    assert item.expires_at == NOW + timedelta(seconds=60)
    assert item.is_active(NOW + timedelta(seconds=59))
    assert not item.is_active(NOW + timedelta(seconds=60))
    assert item.attribution == ATTRIBUTION


# -------------------------------------------------------------- live change


@pytest.fixture(scope="module")
def network() -> WalkNetwork:
    return grid_network()


@pytest.fixture(scope="module")
def router(network: WalkNetwork) -> Router:
    return Router(network, load_routing_config())


def test_rebuild_now_runs_the_callbacks_and_respects_stop(
    network: WalkNetwork, store: ObstructionStore
) -> None:
    ticks = [0.0]
    provider = HazardProvider(store, network, refresh_s=30, clock=lambda: ticks[0])
    seen: list[int] = []
    provider.on_rebuild(lambda result: seen.append(len(result.hazards)))
    provider(NOW)  # the first map
    assert seen == [0]
    # Not due (the clock has not moved), yet rebuild_now rebuilds anyway.
    assert provider.rebuild_now(NOW) is True
    assert seen == [0, 0]
    assert provider.stop() is True
    assert provider.rebuild_now(NOW) is False  # stopped: no more store reads
    assert seen == [0, 0]


def test_the_override_reaches_a_subscribed_page_at_once(
    network: WalkNetwork, router: Router, store: ObstructionStore
) -> None:
    """The end-to-end story behind OP-406's live-change test, without a browser.

    The matcher's clock never moves, so nothing is "due": only the override's own
    rebuild can produce the events.
    """
    app = live_app(network, router, store, heartbeat_s=60)
    app.include_router(admin_api.router)
    headers = {"X-Admin-Key": KEY}
    with TestClient(app) as client, client.websocket_connect("/ws") as sock:
        receive(sock, "hello")
        sock.send_json(json.loads(subscribe_text(1, 3, "wheelchair")))
        before = receive(sock, "subscribed")["summary"]
        assert before["distance_m"] == 200.0

        response = client.post("/api/admin/override", json=body(), headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()["rebuilt"] is True

        changed = receive(sock, "obstruction_changed")
        assert [brief["id"] for brief in changed["added"]] == [f"{SOURCE}:e2e-1"]
        assert changed["added"][0]["title"].startswith(TITLE_PREFIX)

        rerouted = receive(sock, "route_changed")
        assert rerouted["previous"] == before
        assert rerouted["summary"]["distance_m"] == 400.0
        assert rerouted["change"].startswith(
            f"{TITLE_PREFIX}Sidewalk closed for a test now blocks your route"
        )

        # The route request agrees, and the report labels the item as a test.
        a, c = latlng(1), latlng(3)
        best = client.get(
            "/api/route",
            params={
                "from": f"{a[0]},{a[1]}",
                "to": f"{c[0]},{c[1]}",
                "profile": "wheelchair",
            },
        ).json()["routes"][0]
        assert best["distance_m"] == 400.0
        items = [i for g in best["report"]["groups"] for i in g["items"]]
        assert [i["attribution"] for i in items] == [ATTRIBUTION]

        # Removing it is a change too: the route is short again.
        assert (
            client.delete("/api/admin/override/e2e-1", headers=headers).status_code
            == 204
        )
        cleared = receive(sock, "route_changed")
        assert cleared["summary"]["distance_m"] == 200.0
