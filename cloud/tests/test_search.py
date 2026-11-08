"""OP-302: address and place search, from the recorded Parcel Addresses sample.

The fixture index is built from ``cloud/tests/fixtures/parcel_addresses`` (a
downtown box), the recorded LRT stations and ``config/places.json``. Nothing here
touches the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from cloud.app import search
from cloud.app.main import create_app
from cloud.app.search import (
    MAX_RESULTS,
    AddressRow,
    NamedPlace,
    SearchIndex,
    build_index,
    load_index,
    normalise,
    parse_parcel_rows,
    parse_station_rows,
    street_label,
    words,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "cloud" / "tests" / "fixtures"
PARCELS = FIXTURES / "parcel_addresses"
PLACES = ROOT / "config" / "places.json"
# The City's whole service area, from the walking network's bbox (lng, lat).
EDMONTON_BBOX = (-113.72, 53.33, -113.27, 53.72)


def load_script() -> ModuleType:
    """Import scripts/build_search_index.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "openpath_build_search_index", ROOT / "scripts" / "build_search_index.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_script()


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(lat)
    return 6_371_008.8 * math.hypot(dx, math.radians(b[0] - a[0]))


@pytest.fixture(scope="module")
def index() -> SearchIndex:
    """The fixture index, built exactly as the script builds it."""
    rows, station_rows, provenance = builder.read_fixtures(FIXTURES)
    stations = parse_station_rows(station_rows, search.load_station_aliases(PLACES))
    return build_index(
        parse_parcel_rows(rows),
        [*stations, *search.load_places(PLACES)],
        manifest=provenance,
    )


def labels(results: list[search.SearchResult]) -> list[str]:
    return [r.label for r in results]


# ------------------------------------------------------------------ normalising


@pytest.mark.parametrize(
    "text",
    [
        "10220 104 Ave NW",
        "10220 104 Avenue Northwest",
        "10220-104 Avenue NW",
        "10220 104th Ave. N.W.",
        "10220 104 AV NW",
        "  10220   104 avenue   nw ",
    ],
)
def test_address_spellings_normalise_alike(text: str) -> None:
    assert normalise(text) == ["10220", "104", "avenue", "nw"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sir Winston Churchill Sq.", ["sir", "winston", "churchill", "square"]),
        ("Stollery Children's Hospital", ["stollery", "childrens", "hospital"]),
        ("MASKÊKOSIHK TRAIL NW", ["maskekosihk", "trail", "nw"]),
        ("1100 Youville Dr W", ["1100", "youville", "drive", "w"]),
        ("South Campus Ft Edmonton", ["south", "campus", "fort", "edmonton"]),
        ("!!", []),
    ],
)
def test_normalise(text: str, expected: list[str]) -> None:
    assert normalise(text) == expected


def test_words_keep_what_was_typed() -> None:
    """Matching tries the word as typed too, so "st" can still start "Stony"."""
    assert words("10220-104th St. N.W.") == ["10220", "104", "st", "nw"]


def test_street_label() -> None:
    assert street_label("104 AVENUE NW") == "104 Avenue NW"
    assert street_label("103A AVENUE NW") == "103A Avenue NW"
    assert street_label("SIR WINSTON CHURCHILL SQUARE NW") == (
        "Sir Winston Churchill Square NW"
    )


# ------------------------------------------------------------------ the index


def test_fixture_index_counts(index: SearchIndex) -> None:
    counts = index.manifest["counts"]
    assert counts["parcel_rows"] == 310
    assert counts["suite_rows"] == 515
    assert counts["stations"] == 29  # every name in the recorded fhxi-cnhe rows
    assert counts["places"] == len(json.loads(PLACES.read_text())["places"])
    # Distinct (number, street) pairs, fewer than the rows they came from.
    assert 0 < counts["addresses"] < counts["parcel_rows"] + counts["suite_rows"]
    assert index.manifest["mode"] == "fixtures"


def test_fixture_files_match_their_manifest() -> None:
    """The recorded responses are never edited; tests build modified copies instead."""
    manifest = json.loads((PARCELS / "manifest.json").read_text())
    for entry in manifest["files"]:
        data = (PARCELS / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert len(json.loads(data)) == entry["rows"]


def test_parcel_point_wins_over_suites_and_suite_only_counts() -> None:
    rows = [
        AddressRow(1, "MAIN STREET NW", 53.50, -113.50, parcel=True),
        AddressRow(1, "MAIN STREET NW", 53.60, -113.60, parcel=False),
        AddressRow(2, "MAIN STREET NW", 53.40, -113.40, parcel=False),
        AddressRow(2, "MAIN STREET NW", 53.42, -113.42, parcel=False),
    ]
    built = build_index(rows, [])
    assert built.find_address(1, "Main St NW") == pytest.approx((53.50, -113.50))
    assert built.find_address(2, "main street northwest") == pytest.approx(
        (53.41, -113.41)
    )
    assert built.find_address(3, "Main Street NW") is None


def test_malformed_rows_are_skipped() -> None:
    good = {
        "house_number": "10",
        "street_name": "MAIN STREET NW",
        "latitude": "53.5",
        "longitude": "-113.5",
        "object_type": "PARCEL",
    }
    rows = [
        good,
        {**good, "house_number": None},
        {**good, "latitude": "north"},
        {**good, "longitude": "-999"},
        {key: value for key, value in good.items() if key != "street_name"},
        {**good, "street_name": "  "},
    ]
    parsed = parse_parcel_rows(rows)
    assert parsed == [AddressRow(10, "MAIN STREET NW", 53.5, -113.5, parcel=True)]


def test_station_rows_merge_platforms() -> None:
    rows = [
        {"lrt_stop_description": "Central Station", "latitude": "1", "longitude": "3"},
        {"lrt_stop_description": "Central Station", "latitude": "2", "longitude": "4"},
        {"lrt_stop_description": "Quarters Stop", "latitude": "5", "longitude": "6"},
        {"lrt_stop_description": "Broken"},
    ]
    stations = parse_station_rows(rows, {"Central Station": ["Central"]})
    assert stations[0] == NamedPlace(
        "Central Station", "station", 1.5, 3.5, "LRT station", ("Central",)
    )
    assert stations[1].detail == "LRT stop"


def test_save_and_load_round_trip(index: SearchIndex, tmp_path: Path) -> None:
    path = tmp_path / "search.npz"
    index.save(path)
    loaded = SearchIndex.load(path)
    for query in ("rogers", "10220 104", "churchill", "10060 jaspr ave"):
        assert loaded.search(query) == index.search(query)
    assert loaded.manifest == index.manifest


def test_an_old_format_is_refused(index: SearchIndex, tmp_path: Path) -> None:
    path = tmp_path / "search.npz"
    index.save(path)
    with np.load(path) as data:
        arrays = dict(data)
    arrays["format_version"] = np.array(99, dtype=np.int32)
    np.savez(path, **arrays)
    with pytest.raises(ValueError, match="format 99"):
        SearchIndex.load(path)
    assert load_index(path) is None  # the app starts; /api/search answers 503


def test_a_missing_index_loads_as_none(tmp_path: Path) -> None:
    assert load_index(tmp_path / "missing.npz") is None


# ------------------------------------------------------------------ ranking


@pytest.mark.parametrize(
    "query",
    [
        "10220 104 Ave NW",
        "10220 104 Avenue Northwest",
        "10220-104 ave",
        "10220 104th avenue n.w.",
        "10220 104",
        "10220 104 aven",  # half typed
    ],
)
def test_address_spellings_find_rogers_place(index: SearchIndex, query: str) -> None:
    first = index.search(query)[0]
    assert (first.label, first.kind) == ("10220 104 Avenue NW", "address")
    assert distance_m((first.lat, first.lng), (53.547017, -113.497787)) < 5


def test_a_name_prefix_comes_first(index: SearchIndex) -> None:
    assert index.search("rogers")[0].label == "Rogers Place"
    assert index.search("Rogers Pl")[0].label == "Rogers Place"
    central = index.search("central station")
    assert (central[0].label, central[0].kind) == ("Central Station", "station")


def test_churchill_finds_stations_and_the_square(index: SearchIndex) -> None:
    results = index.search("churchill")
    assert {r.label for r in results[:3]} == {
        "Churchill Station",
        "Churchill Stop",
        "Sir Winston Churchill Square",
    }
    # Then the places on the square, found through their addresses.
    assert "Edmonton City Hall" in labels(results)


def test_addresses_come_before_word_matches(index: SearchIndex) -> None:
    """ "10220 104": the addresses first, then Rogers Place (whose address it is)."""
    found = labels(index.search("10220 104"))
    assert found.index("10220 104 Avenue NW") < found.index("Rogers Place")
    assert "10220 104 Street NW" in found


def test_a_suite_only_address_is_found(index: SearchIndex) -> None:
    """Edmonton City Centre exists in the dataset only as suites."""
    first = index.search("10200 102 avenue")[0]
    assert (first.label, first.kind) == ("10200 102 Avenue NW", "address")


def test_aliases_and_station_aliases(index: SearchIndex) -> None:
    assert index.search("government centre")[0].label == "Government Station"
    assert index.search("u of a")[0].label == "University of Alberta"
    assert index.search("royal alex")[0].label == "Royal Alexandra Hospital"
    assert index.search("fort edm")[0].label == "Fort Edmonton Park"


def test_a_direction_word_does_not_match_every_w_word(index: SearchIndex) -> None:
    assert labels(index.search("west ed")) == ["West Edmonton Mall"]
    assert index.search("wes")[0].label == "West Edmonton Mall"


def test_fuzzy_matches_fix_a_typo(index: SearchIndex) -> None:
    assert index.search("rogres")[0].label == "Rogers Place"
    assert "10060 Jasper Avenue NW" in labels(index.search("10060 jaspr ave"))


def test_results_are_capped_at_eight(index: SearchIndex) -> None:
    results = index.search("station")
    assert len(results) == MAX_RESULTS == 8
    assert {r.kind for r in results} == {"station"}


@pytest.mark.parametrize("query", ["", " ", "a", "1", "!!", "zzzzzz"])
def test_short_or_unknown_queries_give_nothing(index: SearchIndex, query: str) -> None:
    assert index.search(query) == []


def test_search_is_fast_on_the_fixture_index(index: SearchIndex) -> None:
    """A generous bound so CI never flakes; the measured times are in DATA_SOURCES.md."""
    queries = search._benchmark_queries(index)
    assert len(queries) == 200
    slowest = 0.0
    for query in queries:
        started = time.perf_counter()
        index.search(query)
        slowest = max(slowest, time.perf_counter() - started)
    assert slowest < 1.0


# ------------------------------------------------------------------ places.json


def test_every_place_records_where_its_point_came_from() -> None:
    raw = json.loads(PLACES.read_text())
    assert set(raw["coord_sources"]) == {"parcel_address", "lrt_station"}
    names = [entry["name"] for entry in raw["places"]]
    assert len(names) == len(set(names))
    for entry in raw["places"]:
        assert entry["coord_source"] in raw["coord_sources"], entry["name"]
        assert entry["address"], entry["name"]
        west, south, east, north = EDMONTON_BBOX
        assert west < entry["lng"] < east and south < entry["lat"] < north
        if entry["coord_source"] == "lrt_station":
            assert entry["note"], entry["name"]  # says how far the station is


def test_station_points_match_the_recorded_stations(index: SearchIndex) -> None:
    raw = json.loads(PLACES.read_text())
    stations = parse_station_rows(
        json.loads((FIXTURES / "lrt/stations.json").read_text())
    )
    by_name = {s.label: (s.lat, s.lng) for s in stations}
    assert set(raw["station_aliases"]) <= set(by_name)
    for entry in raw["places"]:
        if entry["coord_source"] == "lrt_station":
            point = by_name[entry["station"]]
            assert distance_m(point, (entry["lat"], entry["lng"])) < 1, entry["name"]


def test_downtown_place_points_match_the_recorded_addresses(index: SearchIndex) -> None:
    """Places inside the fixture box resolve to the same point from the recording."""
    raw = json.loads(PLACES.read_text())
    stations = parse_station_rows(
        json.loads((FIXTURES / "lrt/stations.json").read_text())
    )
    checked = 0
    for entry in raw["places"]:
        point = builder.resolve_place(entry, index, stations)
        if point is None:
            continue  # outside the downtown box
        assert distance_m(point, (entry["lat"], entry["lng"])) < 1, entry["name"]
        checked += 1
    assert (
        checked >= 15
    )  # Rogers Place, Churchill Square, City Centre, Stantec Tower...


# ------------------------------------------------------------------ the script


def test_the_script_builds_from_fixtures_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the fixture build must not use the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    out = tmp_path / "search.npz"
    assert builder.main(["--fixtures", str(FIXTURES), "--out", str(out)]) == 0
    built = SearchIndex.load(out)
    assert built.manifest["counts"]["parcel_rows"] == 310
    assert built.search("rogers")[0].label == "Rogers Place"


def test_the_script_refuses_to_rewrite_places_from_fixtures() -> None:
    with pytest.raises(SystemExit):
        builder.main(["--fixtures", "--write-places"])


def test_live_paging_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live reader pages with $limit/$offset and stops at a short page."""
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        offset = int(params["$offset"])
        rows = [{"n": offset + i} for i in range(2 if offset < 4 else 1)]
        return httpx.Response(200, json=rows)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        pages = list(builder.fetch_pages(client, "ut27-nrpn", {}, page_size=2))
    assert [len(page) for page in pages] == [2, 2, 1]
    assert [p["$offset"] for p in seen] == ["0", "2", "4"]


# ------------------------------------------------------------------ the API


@pytest.fixture
def client(index: SearchIndex, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.delenv("DATA_MODE", raising=False)
    app = create_app()
    app.state.search = index
    with TestClient(app) as client:
        yield client


def test_endpoint_returns_typed_results(client: TestClient) -> None:
    response = client.get("/api/search", params={"q": "rogers"})
    assert response.status_code == 200
    body = response.json()
    assert body[0] == {
        "label": "Rogers Place",
        "detail": "10220 104 Avenue NW",
        "lat": pytest.approx(53.547016, abs=1e-5),
        "lng": pytest.approx(-113.497787, abs=1e-5),
        "kind": "place",
    }


def test_endpoint_empty_and_too_long_queries(client: TestClient) -> None:
    assert client.get("/api/search").json() == []
    assert client.get("/api/search", params={"q": "r"}).json() == []
    assert client.get("/api/search", params={"q": "x" * 201}).status_code == 422


def test_endpoint_needs_no_network(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No geocoder, no API: a request never opens an outbound connection."""

    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("search must not call the network")

    # The real transports; TestClient uses its own, so the request still runs.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    assert client.get("/api/search", params={"q": "central station"}).json()[0][
        "kind"
    ] == ("station")


def test_endpoint_without_an_index_answers_503(client: TestClient) -> None:
    client.app.state.search = None  # type: ignore[attr-defined]
    assert client.get("/api/search", params={"q": "rogers"}).status_code == 503


def test_openapi_declares_the_result_shape(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    response = schema["paths"]["/api/search"]["get"]["responses"]["200"]
    items = response["content"]["application/json"]["schema"]["items"]
    assert items["$ref"].endswith("/SearchResult")
    result = schema["components"]["schemas"]["SearchResult"]
    assert set(result["required"]) == {"label", "detail", "lat", "lng", "kind"}
    assert result["properties"]["kind"]["enum"] == ["address", "station", "place"]


# ------------------------------------------------------------------ committed index


committed = pytest.mark.skipif(
    not search.DEFAULT_INDEX_PATH.exists(), reason="cloud/data/search.npz not built"
)


@committed
def test_the_committed_index_covers_the_city() -> None:
    full = SearchIndex.load()
    counts = full.manifest["counts"]
    assert counts["addresses"] > 300_000
    assert counts["stations"] == 29
    assert search.DEFAULT_INDEX_PATH.stat().st_size < 15_000_000
    west, south, east, north = full.manifest["bbox"]
    assert west < -113.65 and east > -113.30 and south < 53.40 and north > 53.65
    assert full.search("10220 104 ave")[0].label == "10220 104 Avenue NW"
    assert full.search("1100 youville dr w")[0].label == "1100 Youville Drive West NW"


@committed
def test_the_committed_index_is_fast() -> None:
    full = SearchIndex.load()
    slowest = 0.0
    for query in search._benchmark_queries(full):
        started = time.perf_counter()
        full.search(query)
        slowest = max(slowest, time.perf_counter() - started)
    assert slowest < 1.0  # the target is 200 ms; measured p95 is under 1 ms


@committed
def test_the_app_loads_the_committed_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCHEDULER", "off")
    app = create_app()
    assert isinstance(app.state.search, SearchIndex)
