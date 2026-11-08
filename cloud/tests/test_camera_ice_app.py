"""OP-205 in the app: a stored camera reading reaches /api/route and its report.

Readings go in through the real ``POST /api/ingest/camera`` and the real camera
store, so these tests exercise the same path as the vision worker. The ingest API
checks times against the real clock, so readings are anchored to the real "now".
No network requests, images or trained model are involved.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import camera_ice_routing, ingest_api, main, routing_api
from cloud.app.camera_ice import CrossingIndex, match_camera_ice, weather_is_stale
from cloud.app.camera_ice_routing import CameraIceHazardProvider
from cloud.app.camera_readings import CameraReadingStore
from cloud.app.db import make_engine
from cloud.app.main import create_app
from cloud.app.matching import EdgeIndex, HazardProvider, match_items
from cloud.app.network import (
    DEFAULT_NETWORK_PATH,
    EDGE_CROSSING,
    RawEdge,
    RawNode,
    WalkNetwork,
    export_network,
)
from cloud.app.profiles import load_routing_config
from cloud.app.report import ItemIndex
from cloud.app.routing import Router, profile_weights
from cloud.app.schemas import Obstruction
from cloud.app.store import ObstructionStore
from cloud.app.weather_service import REFRESH_SECONDS, WeatherService
from cloud.tests.test_camera_ice import coordinate, watch

KEY = "op-205-test-key"
REF = "511:9001:1"
ITEM_ID = f"camera_model:{REF}:ice_risk"
CREDIT = "Road camera data: 511 Alberta, Government of Alberta"


def real_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def reading(latitude: float, longitude: float, **overrides: Any) -> dict[str, Any]:
    """An agreed OP-213 payload, observed a minute ago and trusted for 15 minutes."""
    now = real_now()
    body: dict[str, Any] = {
        "source": "camera_model",
        "source_ref": REF,
        "camera_id": 9001,
        "view_id": 1,
        "latitude": latitude,
        "longitude": longitude,
        "label": "ice",
        "score": 0.93,
        "reason": None,
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=14)).isoformat(),
        "model_version": "a" * 64,
        "mode": "live",
    }
    body.update(overrides)
    return body


def post(client: TestClient, body: dict[str, Any]) -> None:
    response = client.post(
        "/api/ingest/camera", json=body, headers={"X-Worker-Key": KEY}
    )
    assert response.status_code == 200, response.text


# A short crossing (B-C, 20 m) with a 200 m detour around it (B-E-C):
#
#                 E
#               /   \
#   A ------- B --x-- C ------- D        x = the camera, on the crossing
#
# Walker or cane cannot use an ice-risk crossing, so it detours: 340 m. A wheelchair
# pays x3 on the crossing (60 m of cost) and keeps it: 160 m, ETA 200 m / 1.0 m/s.
POSITIONS = {
    "A": (-80, 0),
    "B": (-10, 0),
    "C": (10, 0),
    "D": (80, 0),
    "E": (0, 100),
}
EDGES = [
    ("A", "B", 70, "sidewalk"),
    ("B", "C", 20, "crossing"),
    ("C", "D", 70, "sidewalk"),
    ("B", "E", 100, "sidewalk"),
    ("E", "C", 100, "sidewalk"),
]


def short_crossing_network() -> WalkNetwork:
    ids = {name: i + 1 for i, name in enumerate(POSITIONS)}
    nodes = []
    for name, (x, y) in POSITIONS.items():
        lng, lat = coordinate(x, y)
        nodes.append(RawNode(ids[name], lat, lng))
    edges = [
        RawEdge(ids[u], ids[v], length, highway="footway", footway=kind, osmid=200 + i)
        for i, (u, v, length, kind) in enumerate(EDGES)
    ]
    return WalkNetwork.from_data(
        export_network(nodes, edges, source="OP-205 app test", built_at=real_now())
    )


def latlng(name: str) -> str:
    lng, lat = coordinate(*POSITIONS[name])
    return f"{lat},{lng}"


CAMERA_LNG, CAMERA_LAT = coordinate(0, 0)


@pytest.fixture(autouse=True)
def worker_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_KEY", KEY)


@pytest.fixture
def network() -> WalkNetwork:
    return short_crossing_network()


@pytest.fixture
def app(network: WalkNetwork) -> Iterator[FastAPI]:
    """The routing and ingest APIs with the real stores, matcher and camera provider."""
    engine = make_engine("sqlite://")
    store = ObstructionStore(engine)
    store.create_tables()
    cameras = CameraReadingStore(engine)
    cameras.create_tables()
    app = FastAPI()
    app.state.mode = "live"
    app.state.store = store
    app.state.camera_store = cameras
    app.state.router = Router(network, load_routing_config())
    app.state.hazard_provider = CameraIceHazardProvider(
        network,
        cameras.latest,
        weather=None,  # SCHEDULER=off: ice readings count, wet ones need the watch
        base=HazardProvider(store, network, background=False),
    )
    app.include_router(routing_api.router)
    app.include_router(ingest_api.router)
    yield app
    engine.dispose()


def route(client: TestClient, profile: str) -> dict[str, Any]:
    response = client.get(
        "/api/route",
        params={"from": latlng("A"), "to": latlng("D"), "profile": profile},
    )
    assert response.status_code == 200, response.text
    return response.json()["routes"][0]


def report_items(best: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: {**item, "group": group["label"]}
        for group in best["report"]["groups"]
        for item in group["items"]
    }


def crossing(network: WalkNetwork) -> int:
    (edge,) = np.flatnonzero(network.edge_flags & EDGE_CROSSING)
    return int(edge)


# ----------------------------------------------------------- the whole path


def test_a_stored_ice_reading_reroutes_walker_and_costs_wheelchair_x3(
    app: FastAPI, network: WalkNetwork
) -> None:
    with TestClient(app) as client:
        before = {p: route(client, p) for p in ("walker_cane", "wheelchair")}
        assert before["walker_cane"]["distance_m"] == 160
        assert before["wheelchair"]["eta_min"] == round(160 / 60, 1)

        body = reading(CAMERA_LAT, CAMERA_LNG)
        post(client, body)
        stored = app.state.camera_store.latest(real_now())
        assert [row.source_ref for row in stored] == [REF]  # a real CameraReadingRow

        walker = route(client, "walker_cane")
        wheelchair = route(client, "wheelchair")

    # Walker or cane: the ice-risk crossing is removed, so the route goes around it.
    assert walker["distance_m"] == 340
    assert [h["item_id"] for h in walker["hazards_avoided"]] == [ITEM_ID]
    # Wheelchair: the crossing costs x3 (20 m -> 60 m), still cheaper than 200 m around.
    assert wheelchair["distance_m"] == 160
    assert wheelchair["eta_min"] == round((70 + 3 * 20 + 70) / 60, 1)  # 2.7 -> 3.3
    assert [h["item_id"] for h in wheelchair["hazards_on_route"]] == [ITEM_ID]

    # The report lists the full camera record under Weather and ice.
    item = report_items(walker)[ITEM_ID]
    assert item.pop("distance_m") == pytest.approx(10, abs=0.5)  # camera to route B
    assert item == {
        "id": ITEM_ID,
        "group": "Weather and ice",
        "status": "avoided",
        "kind": "ice_risk",
        "effect": "ice_risk",
        "confidence": "medium",
        "title": "Ice risk at nearby crossings",
        "detail": "Camera indicates an icy surface; ice risk at nearby crossings.",
        "source": "camera_model",
        "attribution": CREDIT,
        "observed_at": body["observed_at"].replace("+00:00", "Z"),
        "starts_at": None,
        "ends_at": None,
        "url": None,
        "note": None,
    }
    on_route = report_items(wheelchair)[ITEM_ID]
    assert on_route["status"] == "on_route"
    assert on_route["distance_m"] == pytest.approx(0, abs=0.5)  # 6-decimal geometry
    assert on_route["observed_at"] == item["observed_at"]  # a record, not a bare hazard

    # The same map through OP-207's weights: removed for walker or cane, x3 wheelchair.
    hazards = app.state.hazard_provider(real_now())
    edge = crossing(network)
    assert set(hazards) == {edge}
    profiles = load_routing_config().profiles
    assert profile_weights(network, profiles["walker_cane"], hazards)[edge] == 0
    assert profile_weights(network, profiles["wheelchair"], hazards)[edge] == 60


def test_a_dry_reading_from_the_same_camera_clears_the_route(app: FastAPI) -> None:
    with TestClient(app) as client:
        post(client, reading(CAMERA_LAT, CAMERA_LNG))
        assert route(client, "walker_cane")["distance_m"] == 340
        later = real_now()
        post(
            client,
            reading(
                CAMERA_LAT,
                CAMERA_LNG,
                label="dry",
                observed_at=later.isoformat(),
                expires_at=(later + timedelta(minutes=15)).isoformat(),
            ),
        )
        best = route(client, "walker_cane")
    assert best["distance_m"] == 160
    assert ITEM_ID not in report_items(best)


def test_camera_risks_keep_the_matchers_items(app: FastAPI, network: WalkNetwork):
    """Both providers' records reach the report, and their hazards are combined."""
    now = real_now()
    lng, lat = coordinate(45, 0)  # on C-D, the only way to D
    closure = Obstruction.model_validate(
        {
            "source": "city_construction",
            "source_ref": "permit-1",
            "category": "sidewalk",
            "kind": "trail_caution",
            "effect": "caution",
            "title": "Sidewalk narrowed",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "confidence": "high",
            "observed_at": now,
            "expires_at": now + timedelta(hours=1),
            "attribution": "City of Edmonton Open Data",
        }
    )
    app.state.store.upsert([closure], now)
    with TestClient(app) as client:
        post(client, reading(CAMERA_LAT, CAMERA_LNG))
        items = report_items(route(client, "walker_cane"))
    assert items[ITEM_ID]["status"] == "avoided"
    assert items["city_construction:permit-1"]["status"] == "on_route"
    assert items["city_construction:permit-1"]["attribution"] == closure.attribution


@pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)
def test_the_app_wires_camera_ice_into_city_routes(monkeypatch) -> None:
    """create_app(): ingest, store, provider, router and report on the city network."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.delenv("DATA_MODE", raising=False)
    churchill, rogers = (53.5444, -113.4909), (53.5468, -113.4973)
    params = {
        "from": "{},{}".format(*churchill),
        "to": "{},{}".format(*rogers),
        "profile": "walker_cane",
    }
    app = create_app()
    with TestClient(app) as client:
        provider = app.state.hazard_provider
        assert isinstance(provider, CameraIceHazardProvider)
        assert isinstance(provider.base, HazardProvider)
        router: Router = app.state.router
        city = router.network
        plain = router.route(*churchill, *rogers, "walker_cane").routes[0]
        first = next(e for e in plain.edges if city.edge_flags[e] & EDGE_CROSSING)
        lng, lat = city.edge_coords(first).astype(float).mean(axis=0)
        post(client, reading(float(lat), float(lng)))
        response = client.get("/api/route", params=params)
        assert response.status_code == 200, response.text
        best = response.json()["routes"][0]
        hazards = provider(real_now())

    iced = {edge for edge, items in hazards.items() if items[0].item_id == ITEM_ID}
    assert first in iced
    detour = router.route(*churchill, *rogers, "walker_cane", hazards).routes[0]
    assert not iced & set(detour.edges)  # walker or cane uses none of them
    assert best["distance_m"] == detour.distance_m > plain.distance_m
    assert ITEM_ID in [h["item_id"] for h in best["hazards_avoided"]]
    item = report_items(best)[ITEM_ID]
    assert (item["group"], item["status"], item["attribution"]) == (
        "Weather and ice",
        "avoided",
        CREDIT,
    )
    wheelchair = load_routing_config().profiles["wheelchair"]
    weights = profile_weights(city, wheelchair, hazards)
    for edge in iced:
        assert weights[edge] == pytest.approx(3 * float(city.edge_length_m[edge]))


# ------------------------------------------------------- failures and safety


def test_a_failing_camera_store_keeps_routing_and_the_last_risks(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    provider: CameraIceHazardProvider = app.state.hazard_provider
    with TestClient(app) as client:
        post(client, reading(CAMERA_LAT, CAMERA_LNG))
        assert route(client, "walker_cane")["distance_m"] == 340

        def broken(now: datetime) -> list[object]:
            raise RuntimeError("postgres://user:secret@db/openpath is down")

        provider.readings = broken
        best = route(client, "walker_cane")  # 200, and the unexpired risk is kept
    assert best["distance_m"] == 340
    assert report_items(best)[ITEM_ID]["status"] == "avoided"
    assert "CAMERA readings unavailable (RuntimeError)" in caplog.text
    assert "secret" not in caplog.text
    assert "secret" not in " ".join(provider.latest.warnings)


def test_a_bug_in_the_camera_provider_returns_the_matchers_map(
    app: FastAPI, network: WalkNetwork, monkeypatch, caplog
) -> None:
    now = real_now()
    lng, lat = coordinate(0, 0)
    closure = Obstruction.model_validate(
        {
            "source": "city_construction",
            "source_ref": "permit-2",
            "category": "road",
            "kind": "road_closed",
            "effect": "blocked",
            "title": "Crossing closed",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "confidence": "high",
            "observed_at": now,
            "expires_at": now + timedelta(hours=1),
            "attribution": "City of Edmonton Open Data",
        }
    )
    app.state.store.upsert([closure], now)

    def broken(*args: object, **kwargs: object) -> None:
        raise ValueError("synthetic camera bug")

    monkeypatch.setattr(camera_ice_routing, "match_camera_ice", broken)
    with TestClient(app) as client:
        best = route(client, "standard")
    assert best["distance_m"] == 340  # the matcher's closure still reroutes
    assert "CAMERA ice risks failed" in caplog.text


def test_startup_keeps_the_matcher_when_the_camera_index_fails(
    network: WalkNetwork, monkeypatch, caplog
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise ValueError("Crossing geometry needs at least two positions")

    engine = make_engine("sqlite://")
    store = ObstructionStore(engine)
    base = HazardProvider(store, network)
    monkeypatch.setattr(main, "CameraIceHazardProvider", broken)
    with caplog.at_level(logging.ERROR):
        chosen = main.camera_ice_provider(
            network, CameraReadingStore(engine), None, base, "live"
        )
    assert chosen is base
    assert "CAMERA ice risks unavailable" in caplog.text
    engine.dispose()


def test_camera_items_never_become_city_wide_in_the_matcher(network) -> None:
    """If a camera risk ever reaches the store, the area rule must not take it."""
    now = real_now()
    lng, lat = coordinate(0, 0)
    item = Obstruction.model_validate(
        {
            "source": "camera_model",
            "source_ref": f"{REF}:ice_risk",
            "category": "weather",
            "kind": "ice_risk",
            "effect": "ice_risk",
            "title": "Ice risk at nearby crossings",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "confidence": "medium",
            "observed_at": now,
            "expires_at": now + timedelta(minutes=10),
            "attribution": CREDIT,
        }
    )
    result = match_items([item], EdgeIndex(network), now)
    assert result.matches[item.id].method == "unmatched"
    assert result.hazards == {}  # the camera provider owns its exact crossings
    assert isinstance(result.items, ItemIndex)
    assert result.items.everywhere == []  # never on every route


# ------------------------------------------------------- speed and freshness


def test_unchanged_inputs_reuse_the_evaluation_and_the_merged_map(
    network: WalkNetwork, monkeypatch
) -> None:
    now = real_now()
    rows = [
        {
            **reading(CAMERA_LAT, CAMERA_LNG),
            "observed_at": now - timedelta(minutes=1),
            "expires_at": now + timedelta(minutes=14),
        }
    ]
    calls: list[datetime] = []

    def counted(*args: Any, **kwargs: Any):
        calls.append(kwargs["now"])
        return match_camera_ice(*args, **kwargs)

    monkeypatch.setattr(camera_ice_routing, "match_camera_ice", counted)
    base: dict[int, list] = {}
    provider = CameraIceHazardProvider(
        network, lambda at: rows, weather=None, base=lambda at: base, recheck_s=10
    )
    first = provider(now)
    assert provider(now + timedelta(seconds=9)) is first  # nothing changed
    assert len(calls) == 1
    assert provider(now + timedelta(seconds=10)) is first  # rechecked, same result
    assert len(calls) == 2
    rows[0] = {**rows[0], "label": "dry"}  # a changed reading: evaluated at once
    assert provider(now + timedelta(seconds=11)) == {}
    assert len(calls) == 3
    # A risk is dropped exactly at its expiry, even within the recheck interval.
    rows[0] = {**rows[0], "label": "ice", "observed_at": now + timedelta(seconds=11)}
    assert provider(now + timedelta(seconds=12))
    expiry = provider.latest.risks[0].obstruction.expires_at
    provider.recheck = timedelta(hours=1)
    assert provider(expiry) == {}


def test_weather_age_uses_the_services_clock_and_never_its_last_error() -> None:
    recorded = datetime(2026, 11, 7, 21, tzinfo=UTC)

    async def unused(at: datetime) -> dict[str, Any]:
        raise AssertionError("not fetched")

    # Replay: the service's clock is frozen at the recording, whatever the app's time.
    replay = WeatherService(unused, mode="replay", clock=lambda: recorded)
    replay.last_good = watch(observed_at=recorded, mode="replay")
    replay.last_success = recorded
    replay.last_error = "a later refresh failed"
    assert not weather_is_stale(replay)
    wet = {
        **reading(CAMERA_LAT, CAMERA_LNG, label="wet", mode="replay"),
        "observed_at": recorded,
        "expires_at": recorded + timedelta(hours=2),
    }
    index = CrossingIndex(short_crossing_network())
    hour_later = recorded + timedelta(hours=1)
    assert match_camera_ice(
        [wet], replay.last_good, index, now=hour_later, mode="replay"
    ).risks
    # The watch's own two-hour observation limit still bounds old weather.
    two_hours = recorded + timedelta(hours=2)
    assert not match_camera_ice(
        [{**wet, "expires_at": two_hours + timedelta(minutes=5)}],
        replay.last_good,
        index,
        now=two_hours,
        mode="replay",
    ).risks

    # Live: stale only after three refresh intervals without a success.
    clock = [recorded]
    live = WeatherService(unused, clock=lambda: clock[0])
    live.last_success = recorded
    live.last_error = "one failed refresh"
    clock[0] = recorded + timedelta(seconds=3 * REFRESH_SECONDS)
    assert not weather_is_stale(live)
    clock[0] += timedelta(seconds=1)
    assert weather_is_stale(live)
    assert weather_is_stale(None)


def test_stale_weather_keeps_a_wet_risk_until_its_own_expiry(network) -> None:
    """Stale weather is uncertain: the risk is neither cleared nor renewed."""
    start = real_now()
    clock = [start]

    async def unused(at: datetime) -> dict[str, Any]:
        raise AssertionError("not fetched")

    service = WeatherService(unused, clock=lambda: clock[0])
    service.last_good = watch(observed_at=start - timedelta(minutes=5))
    service.last_success = start
    rows = [
        {
            **reading(CAMERA_LAT, CAMERA_LNG, label="wet"),
            "observed_at": start - timedelta(minutes=1),
            "expires_at": start + timedelta(minutes=40),
        }
    ]
    provider = CameraIceHazardProvider(network, lambda at: rows, weather=service)
    assert provider(start)
    saved = provider.latest.risks[0]

    service.last_error = "refresh failed"  # one failure: still fresh by age
    clock[0] = start + timedelta(minutes=5)
    assert provider(clock[0]) and provider.latest.risks[0] == saved

    clock[0] = start + timedelta(minutes=31)  # stale: wet readings are uncertain
    rows[0] = {**rows[0], "observed_at": start + timedelta(minutes=30)}
    assert provider(clock[0]) and provider.latest.risks[0] == saved  # not renewed
    assert any("stale" in warning for warning in provider.latest.warnings)
    assert provider(saved.obstruction.expires_at) == {}
