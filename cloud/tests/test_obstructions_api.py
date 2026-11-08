"""OP-101: GET /api/obstructions returns active items as GeoJSON, with a bbox filter.

The endpoint is mounted on a bare FastAPI app here, with the test store and a
frozen clock injected through FastAPI's dependency overrides. OP-102's real app
wires the same router the same way.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import obstructions_api


@pytest.fixture
def client(store, now) -> TestClient:
    """A test client for an app that has only the obstructions router."""
    app = FastAPI()
    app.state.store = store  # what get_store() reads
    app.include_router(obstructions_api.router)
    # Freeze the clock so "active" is judged against the fixture time, not wall time.
    app.dependency_overrides[obstructions_api.get_now] = lambda: now
    return TestClient(app)


def test_empty_store_returns_empty_collection(client) -> None:
    """With no data, the response is still a valid (empty) FeatureCollection."""
    body = client.get("/api/obstructions").json()
    assert body["type"] == "FeatureCollection"
    assert body["features"] == []
    assert body["generated_at"] == "2026-11-07T20:10:00Z"  # the frozen clock, in UTC


def test_returns_active_items_as_features(client, store, make_obstruction, now) -> None:
    """Active items come back as GeoJSON Features; expired ones are left out."""
    store.upsert(
        [
            make_obstruction(),
            make_obstruction(  # expired exactly now
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
    """?bbox= keeps only items inside the box (downtown in, far east out)."""
    far = make_obstruction(
        source_ref="far", geometry={"type": "Point", "coordinates": [-113.3, 53.4]}
    )
    store.upsert([make_obstruction(), far], now)
    features = client.get(
        "/api/obstructions", params={"bbox": "-113.52,53.52,-113.47,53.56"}
    ).json()["features"]
    assert [feature["id"] for feature in features] == ["city_lrt_devices:1234"]


@pytest.mark.parametrize(
    "bbox",
    [
        "1,2,3",  # only three numbers
        "a,b,c,d",  # not numbers
        "-113.4,53.5,-113.6,53.6",  # corners swapped (min > max)
        "-200,53,-113,54",  # longitude out of range
    ],
)
def test_invalid_bbox_is_rejected(client, bbox) -> None:
    """A malformed bbox is a 422 whose message explains the expected format."""
    response = client.get("/api/obstructions", params={"bbox": bbox})
    assert response.status_code == 422
    assert "bbox" in response.json()["detail"]


def test_openapi_documents_the_endpoint(client) -> None:
    """The endpoint and its response model appear in /openapi.json (used by `make types`)."""
    schema = client.get("/openapi.json").json()
    assert "/api/obstructions" in schema["paths"]
    assert "ObstructionCollection" in schema["components"]["schemas"]
