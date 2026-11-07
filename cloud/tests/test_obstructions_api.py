"""OP-101: GET /api/obstructions returns active items as GeoJSON, with a bbox filter."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import obstructions_api


@pytest.fixture
def client(store, now) -> TestClient:
    app = FastAPI()
    app.state.store = store
    app.include_router(obstructions_api.router)
    app.dependency_overrides[obstructions_api.get_now] = lambda: now
    return TestClient(app)


def test_empty_store_returns_empty_collection(client) -> None:
    body = client.get("/api/obstructions").json()
    assert body["type"] == "FeatureCollection"
    assert body["features"] == []
    assert body["generated_at"] == "2026-11-07T20:10:00Z"


def test_returns_active_items_as_features(client, store, make_obstruction, now) -> None:
    store.upsert(
        [
            make_obstruction(),
            make_obstruction(
                source_ref="old", observed_at=now - timedelta(hours=1), expires_at=now
            ),
        ],
        now,
    )
    features = client.get("/api/obstructions").json()["features"]
    assert [feature["id"] for feature in features] == ["city_lrt_devices:1234"]
    assert features[0]["properties"]["title"] == "Elevator out of service"
    assert features[0]["geometry"]["type"] == "Point"


def test_bbox_filters_items(client, store, make_obstruction, now) -> None:
    far = make_obstruction(
        source_ref="far", geometry={"type": "Point", "coordinates": [-113.3, 53.4]}
    )
    store.upsert([make_obstruction(), far], now)
    features = client.get(
        "/api/obstructions", params={"bbox": "-113.52,53.52,-113.47,53.56"}
    ).json()["features"]
    assert [feature["id"] for feature in features] == ["city_lrt_devices:1234"]


@pytest.mark.parametrize(
    "bbox", ["1,2,3", "a,b,c,d", "-113.4,53.5,-113.6,53.6", "-200,53,-113,54"]
)
def test_invalid_bbox_is_rejected(client, bbox) -> None:
    response = client.get("/api/obstructions", params={"bbox": bbox})
    assert response.status_code == 422
    assert "bbox" in response.json()["detail"]


def test_openapi_documents_the_endpoint(client) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/obstructions" in schema["paths"]
    assert "ObstructionCollection" in schema["components"]["schemas"]
