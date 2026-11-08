"""OP-310: pedestrian counts (``sw7k-ptx8``) and the clear-first ranking.

The counts tests read the recorded official rows in ``fixtures/pedestrian_counts``.
The ranking tests use the 3 x 3 test grid from ``test_routing`` (100 m blocks around
Churchill Square), a hazard map the test controls and real saved-route rows. The
last test runs ``create_app()`` on the committed Edmonton network for the wiring.
No test calls the network.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.app import pedestrian_counts as pc
from cloud.app import priority
from cloud.app.db import make_engine
from cloud.app.main import create_app
from cloud.app.profiles import load_routing_config
from cloud.app.push import PushSender, PushServices, PushSubscriptionStore
from cloud.app.routing import EdgeHazard, Router
from cloud.app.saved_routes import SavedRouteStore
from cloud.app.schemas import Confidence, Effect, Geometry
from cloud.app.store import ObstructionStore
from cloud.tests.conftest import obstruction
from cloud.tests.test_routing import DLAT, DLNG, LAT0, LNG0, grid_network, latlng

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROWS = json.loads((FIXTURES / "pedestrian_counts" / "rows.json").read_text("utf-8"))
NOW = datetime(2026, 11, 8, 16, 0, tzinfo=UTC)
CLIENT = "c7Hq2pL9xZ4vT1mK8nR3"


def point(lat: float, lng: float) -> Geometry:
    return Geometry(type="Point", coordinates=[lng, lat])


def north_of(lat: float, metres: float) -> float:
    return lat + metres / 111_195.0


# ------------------------------------------------------------ the counts file


def test_aggregate_takes_the_median_day_of_the_last_28_days() -> None:
    document = pc.aggregate(ROWS)
    assert document["window"] == {
        "start": "2026-10-11",
        "end": "2026-11-07",
        "days": 28,
        "min_days": 7,
    }
    counters = {c["name"]: c for c in document["counters"]}
    # Medians of the recorded official rows, checked by hand.
    assert counters["Railtown Park SUP"]["daily_pedestrians"] == 808
    assert counters["Funicular @ 100 St"]["daily_pedestrians"] == 488
    assert counters["High Level Bridge West"]["daily_pedestrians"] == 390
    assert counters["High Level Bridge West"]["days"] == 28
    # Hermitage South has gaps: 24 of the 28 days.
    assert counters["Hermitage South"]["days"] == 24
    assert document["rows"] == {"used": len(ROWS), "skipped": 0}
    assert document["attribution"] == "City of Edmonton Open Data"


def test_bicycle_only_counters_are_left_out_not_counted_as_empty() -> None:
    document = pc.aggregate(ROWS)
    names = {c["name"] for c in document["counters"]}
    assert "103 Street N of 102 Avenue" not in names
    assert {"name": "103 Street N of 102 Avenue", "reason": "counts bicycles only"} in (
        document["excluded"]
    )


def test_a_broken_day_barely_moves_the_median() -> None:
    railtown = [
        r for r in ROWS if r["counter_location_description"] == "Railtown Park SUP"
    ]
    # The same day twice: the larger counts, so a zero duplicate changes nothing.
    duplicate = [*railtown, {**railtown[-1], "total_pedestrian_count": "0"}]
    (counter,) = pc.aggregate(duplicate)["counters"]
    assert counter["daily_pedestrians"] == 808 and counter["days"] == 28
    # One broken day (the size of a real one in the dataset) shifts the median by
    # one place; a mean would jump to about 17,800.
    broken = [*railtown, {**railtown[-1], "total_pedestrian_count": "477654"}]
    (counter,) = pc.aggregate(broken)["counters"]
    assert counter["daily_pedestrians"] == 828


def test_counters_with_too_few_days_are_left_out() -> None:
    rows = [
        r for r in ROWS if r["counter_location_description"] == "Funicular @ 100 St"
    ]
    document = pc.aggregate(rows[-5:])
    assert document["counters"] == []
    assert document["excluded"] == [
        {"name": "Funicular @ 100 St", "reason": "only 5 days in the window"}
    ]


def test_bad_rows_are_skipped_and_no_rows_is_an_error() -> None:
    bad = [{"latitude": "53.5"}, {**ROWS[0], "log_timstamp": "not a date"}]
    document = pc.aggregate([*ROWS, *bad])
    assert document["rows"]["skipped"] == 2
    with pytest.raises(ValueError, match="no usable"):
        pc.aggregate(bad)


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "openpath_build_pedestrian_counts",
        ROOT / "scripts" / "build_pedestrian_counts.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_script_from_fixtures_writes_a_loadable_file(tmp_path: Path) -> None:
    out = tmp_path / "counts.json"
    assert load_script().main(["--fixtures", str(FIXTURES), "--out", str(out)]) == 0
    counts = pc.load(out)
    assert counts is not None
    assert {c.name for c in counts.counters} == {
        "Funicular @ 100 St",
        "Hermitage South",
        "High Level Bridge West",
        "Railtown Park SUP",
    }
    assert counts.window_end == date(2026, 11, 7)
    written = json.loads(out.read_text("utf-8"))
    assert written["read"]["mode"] == "fixtures"
    assert "X-App-Token" not in out.read_text("utf-8")


def test_committed_file_loads_with_edmonton_counters() -> None:
    counts = pc.load()
    assert counts is not None and len(counts.counters) >= 10
    for counter in counts.counters:
        assert 53.3 < counter.lat < 53.8 and -113.8 < counter.lng < -113.2
        assert counter.daily_pedestrians > 0 and counter.days >= pc.MIN_DAYS
    assert counts.window_start is not None and counts.window_end is not None
    assert (counts.window_end - counts.window_start).days == pc.WINDOW_DAYS - 1


def test_missing_or_broken_file_gives_no_counts(tmp_path: Path) -> None:
    assert pc.load(tmp_path / "missing.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text('{"counters": [{"name": "x"}]}', encoding="utf-8")
    assert pc.load(broken) is None


# ------------------------------------------------------------ the weight


COUNTER = pc.Counter("Test counter", LAT0, LNG0, daily_pedestrians=1000, days=28)
COUNTS = pc.PedestrianCounts(counters=(COUNTER,))


def test_weight_falls_off_linearly_to_zero_at_400_m() -> None:
    at, nearest = COUNTS.weight(point(LAT0, LNG0))
    assert at == pytest.approx(1000)
    assert nearest is not None and nearest.distance_m == pytest.approx(0, abs=0.01)
    half, _ = COUNTS.weight(point(north_of(LAT0, 200), LNG0))
    assert half == pytest.approx(500, rel=0.01)
    far, nearest = COUNTS.weight(point(north_of(LAT0, 400), LNG0))
    assert far == 0 and nearest is None


def test_distance_to_lines_and_polygons() -> None:
    lat = north_of(LAT0, 100)
    line = Geometry(
        type="LineString", coordinates=[[LNG0 - 0.01, lat], [LNG0 + 0.01, lat]]
    )
    assert pc.distance_to_geometry_m(line, LAT0, LNG0) == pytest.approx(100, rel=0.01)
    box = [
        [LNG0 - 0.01, LAT0 - 0.01],
        [LNG0 + 0.01, LAT0 - 0.01],
        [LNG0 + 0.01, LAT0 + 0.01],
        [LNG0 - 0.01, LAT0 + 0.01],
        [LNG0 - 0.01, LAT0 - 0.01],
    ]
    polygon = Geometry(type="Polygon", coordinates=[box])
    assert pc.distance_to_geometry_m(polygon, LAT0, LNG0) == 0
    hole = [
        [LNG0 + dx, LAT0 + dy]
        for dx, dy in (
            (-1e-3, -1e-3),
            (1e-3, -1e-3),
            (1e-3, 1e-3),
            (-1e-3, 1e-3),
            (-1e-3, -1e-3),
        )
    ]
    holed = Geometry(type="Polygon", coordinates=[box, hole])
    assert pc.distance_to_geometry_m(holed, LAT0, LNG0) > 50


# ------------------------------------------------------------ the ranking


def item(
    ref: str,
    *,
    effect: str = "blocked",
    lat: float = LAT0,
    lng: float = LNG0,
    **kw: Any,
):
    defaults: dict[str, Any] = {
        "source": "city_construction",
        "source_ref": ref,
        "category": "sidewalk",
        "kind": "sidewalk_closed" if effect == "blocked" else "trail_caution",
        "effect": effect,
        "title": f"Item {ref}",
        "detail": None,
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
        "observed_at": NOW - timedelta(minutes=6),
        "expires_at": NOW + timedelta(hours=1),
        "starts_at": None,
    }
    defaults.update(kw)
    return obstruction(**defaults)


def hazards_for(
    edges: dict[str, list[int]], effects: dict[str, str]
) -> dict[int, list[EdgeHazard]]:
    result: dict[int, list[EdgeHazard]] = {}
    for item_id, on in edges.items():
        for edge in on:
            result.setdefault(edge, []).append(
                EdgeHazard(item_id, Effect(effects[item_id]), Confidence.HIGH, item_id)
            )
    return result


CONFIG = load_routing_config()


def test_severity_comes_from_the_profiles() -> None:
    assert priority.severity(Effect.BLOCKED, CONFIG) == 1.0
    assert priority.severity(Effect.SNOW, CONFIG) == pytest.approx(0.4)
    assert priority.severity(Effect.ICE_RISK, CONFIG) == pytest.approx(0.4)
    assert priority.severity(Effect.CAUTION, CONFIG) == pytest.approx(0.2)


def test_rank_orders_by_trips_people_and_severity() -> None:
    closure = item("1")  # blocked, at the counter, on two saved routes
    lonely = item("2", lat=north_of(LAT0, 1000))  # blocked, nobody nearby, no routes
    caution = item("3", effect="caution")  # take care, at the counter
    weather = item(
        "4",
        source="weather_watch",
        category="weather",
        kind="ice_risk",
        effect="ice_risk",
        confidence="low",
    )
    hazards = hazards_for(
        {closure.id: [10], lonely.id: [20], caution.id: [30]},
        {closure.id: "blocked", lonely.id: "blocked", caution.id: "caution"},
    )
    routes = [
        priority.RouteEdges(1, frozenset({10, 11})),
        priority.RouteEdges(2, frozenset({10, 30})),
        priority.RouteEdges(3, frozenset({99})),
    ]
    ranked = priority.rank(
        [weather, lonely, caution, closure], hazards, routes, COUNTS, CONFIG, NOW
    )
    # A closure nobody walks still outranks a busy "take care": it stops everyone.
    assert [row.id for row in ranked] == [closure.id, lonely.id, caution.id, weather.id]
    assert [row.rank for row in ranked] == [1, 2, 3, 4]
    top = ranked[0]
    assert top.saved_routes_affected == 2
    assert top.pedestrian_weight == pytest.approx(1000)
    assert top.score == pytest.approx(1.0 * 3 * 2)
    assert (
        top.why
        == "Blocks 2 saved routes; City counters nearby see about 1,000 people walking a day."
    )
    assert top.age_s == 360
    assert top.on_network and top.nearest_counter is not None
    assert (
        ranked[1].why == "On no saved route; no City pedestrian counter within 400 m."
    )
    assert ranked[2].score == pytest.approx(0.2 * 2 * 2)
    assert ranked[2].why.startswith("On 1 saved route;")
    assert ranked[3].score == 0 and ranked[3].saved_routes_affected == 0
    assert ranked[3].why.startswith("Covers a whole area")


def test_why_names_the_effect_and_unmatched_items() -> None:
    snow = item("5", effect="snow", kind="snow")
    station = item(
        "6", source="city_lrt_devices", category="indoor", kind="elevator_out"
    )
    off_path = item("7")
    hazards = hazards_for({snow.id: [1]}, {snow.id: "snow"})
    routes = [priority.RouteEdges(1, frozenset({1}))]
    by_id = {
        row.id: row
        for row in priority.rank(
            [snow, station, off_path], hazards, routes, None, CONFIG, NOW
        )
    }
    assert (
        by_id[snow.id].why == "Snow on 1 saved route; pedestrian counts are not loaded."
    )
    assert by_id[station.id].why.startswith("Station warning")
    assert by_id[off_path.id].why.startswith("Not on a mapped walking path")
    assert by_id[off_path.id].on_network is False


def test_people_text_keeps_two_significant_figures() -> None:
    assert priority.people_text(3.6) == "about 4"
    assert priority.people_text(87.5) == "about 88"
    assert priority.people_text(1234) == "about 1,200"
    assert priority.people_text(98765) == "about 99,000"


# ------------------------------------------------------------ the endpoint


class Hazards:
    def __init__(self) -> None:
        self.map: dict[int, list[EdgeHazard]] = {}

    def __call__(self, now: datetime) -> dict[int, list[EdgeHazard]]:
        return self.map


@pytest.fixture
def api():
    network, edge = grid_network()
    engine = make_engine("sqlite://")
    store = ObstructionStore(engine)
    store.create_tables()
    routes = SavedRouteStore(engine)
    routes.create_tables()
    subscriptions = PushSubscriptionStore(engine)
    subscriptions.create_tables()
    router_ = Router(network, CONFIG)
    hazards = Hazards()
    app = FastAPI()
    app.include_router(priority.router)
    app.state.store = store
    app.state.router = router_
    app.state.hazard_provider = hazards
    app.state.clock = lambda: NOW
    app.state.mode = "replay"
    app.state.pedestrian_counts = pc.PedestrianCounts(
        counters=(pc.Counter("Grid counter", LAT0, LNG0 + DLNG / 2, 600, 28),)
    )
    app.state.push = PushServices(
        subscriptions, routes, PushSender(subscriptions, None), None
    )
    return TestClient(app), store, routes, router_, hazards, edge


def save(
    routes: SavedRouteStore,
    router_: Router,
    hazards: Hazards,
    a: int,
    b: int,
    name: str,
) -> None:
    (lat1, lng1), (lat2, lng2) = latlng(a), latlng(b)
    result = router_.route(lat1, lng1, lat2, lng2, "wheelchair", hazards.map)
    routes.add(
        client_id=CLIENT,
        name=name,
        from_lat=lat1,
        from_lng=lng1,
        to_lat=lat2,
        to_lng=lng2,
        profile="wheelchair",
        baseline=result.routes[0] if result.routes else None,
        now=NOW,
    )


def test_endpoint_counts_routes_that_detour_around_a_closure(api) -> None:
    client, store, routes, router_, hazards, edge = api
    # A closure on Avenue A between nodes 1 and 2, already there when both routes
    # were saved, so their usual routes go around it; their plain routes do not.
    closure = item("1", lat=LAT0, lng=LNG0 + DLNG / 2)
    store.upsert([closure], NOW)
    hazards.map = hazards_for({closure.id: [edge[(1, 2)]]}, {closure.id: "blocked"})
    save(routes, router_, hazards, 1, 3, "Work")
    save(routes, router_, hazards, 4, 2, "Library")
    save(routes, router_, hazards, 7, 9, "Elsewhere")  # Avenue C, never near it
    (row,) = (r for r in routes.all() if r.name == "Work")
    assert edge[(1, 2)] not in row.baseline_edge_ids  # the usual route detours

    response = client.get("/api/priority")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "replay"
    assert body["saved_routes"] == 3 and body["saved_routes_pending"] == 0
    assert body["pedestrian_counts"]["counters"] == 1
    (top,) = body["items"]
    assert top["id"] == closure.id and top["rank"] == 1
    assert top["saved_routes_affected"] == 2
    assert top["pedestrian_weight"] == pytest.approx(600)
    assert top["nearest_counter"]["name"] == "Grid counter"
    assert (
        top["why"]
        == "Blocks 2 saved routes; City counters nearby see about 600 people walking a day."
    )
    assert top["age_s"] == 360 and top["lat"] == pytest.approx(LAT0)
    # Plain routes are cached: a second request computes none.
    plain = client.app.state.priority_plain_routes
    assert len(plain._edges) == 3
    assert client.get("/api/priority").json()["items"][0]["saved_routes_affected"] == 2


def test_endpoint_budget_leaves_plain_routes_for_later(api, monkeypatch) -> None:
    client, _store, routes, router_, hazards, _ = api
    save(routes, router_, hazards, 1, 3, "Work")
    save(routes, router_, hazards, 7, 9, "Home")
    monkeypatch.setattr(priority, "PLAIN_BUDGET_S", 0.0)
    body = client.get("/api/priority").json()
    assert body["saved_routes"] == 2 and body["saved_routes_pending"] == 2
    monkeypatch.setattr(priority, "PLAIN_BUDGET_S", 5.0)
    assert client.get("/api/priority").json()["saved_routes_pending"] == 0


def test_endpoint_without_router_saved_routes_or_counts(api) -> None:
    client, store, *_ = api
    state = client.app.state
    state.router = None
    del state.push
    state.pedestrian_counts = None
    store.upsert([item("1")], NOW)
    body = client.get("/api/priority").json()
    assert body["saved_routes"] == 0 and body["pedestrian_counts"] is None
    (row,) = body["items"]
    assert row["why"].endswith("pedestrian counts are not loaded.")


def test_real_app_serves_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.delenv("DATA_MODE", raising=False)
    with TestClient(create_app()) as client:
        body = client.get("/api/priority").json()
        assert body["items"] == [] and body["mode"] == "live"
        assert body["pedestrian_counts"]["dataset"] == "sw7k-ptx8"
        assert "/api/priority" in client.app.openapi()["paths"]


def test_grid_constants_are_about_100_m() -> None:
    # The fixtures above assume the test grid's spacing; fail loudly if it changes.
    assert DLAT == pytest.approx(0.0009) and DLNG == pytest.approx(0.0015)
