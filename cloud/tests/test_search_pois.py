"""OP-416: OpenStreetMap points of interest in the address and place search.

The ranking tests build a small index from in-memory rows: a few downtown parcel
addresses, the recorded LRT stations, ``config/places.json`` and hand-written
Overpass elements that cover every rule of ``parse_poi_elements``. The recorded
downtown Overpass response (``cloud/tests/fixtures/osm_pois``) checks the parser
on real tags. Nothing here touches the network.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import numpy as np
import pytest

from cloud.app import search
from cloud.app.search import (
    FORMAT_VERSION,
    GROUP_CURATED,
    GROUP_POI,
    MAX_RESULTS,
    AddressRow,
    NamedPlace,
    SearchIndex,
    build_index,
    category_label,
    parse_parcel_rows,
    parse_poi_elements,
    parse_station_rows,
)
from cloud.tests.test_search import FIXTURES, PLACES, builder, labels

OSM_POIS = FIXTURES / "osm_pois"

# Four downtown parcel addresses (points from the recorded Parcel Addresses sample).
ADDRESSES = [
    AddressRow(10220, "104 AVENUE NW", 53.547017, -113.497787, parcel=True),
    AddressRow(10150, "JASPER AVENUE NW", 53.541102, -113.495121, parcel=True),
    AddressRow(10205, "101 STREET NW", 53.543650, -113.493583, parcel=True),
    AddressRow(10702, "JASPER AVENUE NW", 53.541800, -113.504600, parcel=True),
]


def node(osm_id: int, name: str, lat: float, lng: float, **tags: str) -> dict[str, Any]:
    return {
        "type": "node",
        "id": osm_id,
        "lat": lat,
        "lon": lng,
        "tags": {"name": name, **tags},
    }


def way(osm_id: int, name: str, lat: float, lng: float, **tags: str) -> dict[str, Any]:
    return {
        "type": "way",
        "id": osm_id,
        "center": {"lat": lat, "lon": lng},
        "nodes": [1, 2, 3],
        "tags": {"name": name, **tags},
    }


def addr(number: str, street: str) -> dict[str, str]:
    return {"addr:housenumber": number, "addr:street": street}


# Hand-written Overpass elements: what is kept, and one of everything that is dropped.
ELEMENTS: list[dict[str, Any]] = [
    # A chain: three cafes. Two carry address tags; the third gets the nearest parcel.
    node(1, "Tim Hortons", 53.541110, -113.495130, amenity="cafe", brand="Tim Hortons", **addr("10150", "Jasper Avenue NW")),
    node(2, "Tim Hortons", 53.543660, -113.493590, amenity="cafe", brand="Tim Hortons"),
    node(3, "Tim Hortons", 53.547100, -113.497900, amenity="cafe", brand="Tim Hortons", **addr("10220", "104 Avenue NW")),
    # The first cafe mapped again as its building, 2 m away: a duplicate.
    way(4, "Tim Hortons", 53.541120, -113.495140, amenity="cafe"),
    # A unique shop.
    node(5, "Audreys Books", 53.541790, -113.504590, shop="books", **addr("10702", "Jasper Avenue NW")),
    # Pharmacies; one carries the category in healthcare only.
    node(6, "Shoppers Drug Mart", 53.544122, -113.493437, amenity="pharmacy", healthcare="pharmacy", brand="Shoppers Drug Mart", **addr("10255", "101 Street NW")),
    node(7, "Family Drugs", 53.544811, -113.486251, healthcare="pharmacy"),
    # A cafe whose brand differs from its name: the brand is an alias.
    node(8, "Reserve Roastery", 53.542000, -113.494700, amenity="cafe", brand="Starbucks"),
    node(9, "Starbucks", 53.542076, -113.494754, amenity="cafe", brand="Starbucks"),
    # A restaurant far from every indexed address: the category alone.
    node(10, "Chipotle Mexican Grill", 53.600000, -113.600000, amenity="fast_food", brand="Chipotle"),
    # Dropped: parking, street furniture, an information board.
    node(11, "City Hall Parkade", 53.545000, -113.489000, amenity="parking"),
    node(12, "Memorial Bench", 53.545100, -113.489100, amenity="bench"),
    node(22, "Downtown Map", 53.545200, -113.489200, tourism="information", information="board"),
    # Dropped: a public transport station (the City's LRT list covers them).
    node(13, "Churchill Station", 53.544074, -113.489008, public_transport="station", amenity="bus_station"),
    # Dropped: no name, no point, a name too long to be typed.
    node(14, "", 53.545300, -113.489300, amenity="cafe"),
    {"type": "way", "id": 15, "tags": {"name": "Lost Way", "amenity": "cafe"}},
    node(16, "x" * 121, 53.545400, -113.489400, amenity="cafe"),
    # Dropped: OpenStreetMap's own copy of a config place, and of a config alias.
    way(17, "Rogers Place", 53.546800, -113.497500, leisure="stadium", amenity="music_venue", **addr("10220", "104 Avenue NW")),
    node(18, "Churchill Square", 53.545500, -113.488800, tourism="attraction"),
    # Kept: a shorter label than "Rogers Place", but a point of interest.
    node(19, "Rogers", 53.543000, -113.490000, shop="mobile_phone"),
    # Category labels: a park, a place of worship, a visitor centre, an unknown shop value.
    node(20, "Beaver Hills House Park", 53.541900, -113.498600, leisure="park"),
    node(21, "All Saints Anglican Cathedral", 53.542700, -113.494900, amenity="place_of_worship"),
    node(23, "Edmonton Visitor Centre", 53.545600, -113.489600, tourism="information", information="office"),
    node(24, "Yogen Fruz", 53.543300, -113.495600, shop="frozen_yogurt", **addr("10200", "102 Avenue NW")),
]  # fmt: skip


@pytest.fixture(scope="module")
def curated() -> list[NamedPlace]:
    stations = parse_station_rows(
        json.loads((FIXTURES / "lrt" / "stations.json").read_text()),
        search.load_station_aliases(PLACES),
    )
    return [*stations, *search.load_places(PLACES)]


@pytest.fixture(scope="module")
def parsed(curated: list[NamedPlace]) -> tuple[list[NamedPlace], dict[str, int]]:
    addresses = build_index(ADDRESSES, [])
    return parse_poi_elements(ELEMENTS, addresses=addresses, curated=curated)


@pytest.fixture(scope="module")
def pois(parsed: tuple[list[NamedPlace], dict[str, int]]) -> list[NamedPlace]:
    return parsed[0]


@pytest.fixture(scope="module")
def index(curated: list[NamedPlace], pois: list[NamedPlace]) -> SearchIndex:
    return build_index(ADDRESSES, [*curated, *pois])


def by_label(pois: list[NamedPlace], label: str) -> list[NamedPlace]:
    return [p for p in pois if p.label == label]


# ------------------------------------------------------------------ parsing


def test_what_is_kept_and_what_is_dropped(
    parsed: tuple[list[NamedPlace], dict[str, int]],
) -> None:
    pois, counts = parsed
    assert counts == {
        "returned": 24,
        "nameless": 1,
        "no_point": 1,
        "too_long": 1,
        "not_destination": 3,
        "station": 1,
        "duplicate": 1,
        "curated_duplicate": 2,
        "with_osm_address": 5,
        "with_nearest_address": 4,  # within 150 m of one of the four addresses
        "without_address": 5,
        "kept": 14,
    }
    assert len(pois) == 14
    assert all(p.kind == "place" and p.group == GROUP_POI for p in pois)
    assert not by_label(pois, "Rogers Place") and not by_label(pois, "Churchill Square")
    assert not by_label(pois, "Churchill Station") and not by_label(
        pois, "Downtown Map"
    )


def test_details_say_what_and_where(pois: list[NamedPlace]) -> None:
    hortons = sorted(p.detail or "" for p in by_label(pois, "Tim Hortons"))
    assert hortons == [
        "Cafe, 10150 Jasper Avenue NW",  # addr:* tags
        "Cafe, 10205 101 Street NW",  # the nearest indexed address, 9 m away
        "Cafe, 10220 104 Avenue NW",
    ]
    assert (
        by_label(pois, "Audreys Books")[0].detail == "Bookstore, 10702 Jasper Avenue NW"
    )
    # healthcare only, and no indexed address within 150 m: the category alone.
    assert by_label(pois, "Family Drugs")[0].detail == "Pharmacy"
    assert by_label(pois, "Chipotle Mexican Grill")[0].detail == "Fast food"
    assert by_label(pois, "Beaver Hills House Park")[0].detail == "Park"
    # The nearest indexed address is 136 m away, so it is used.
    assert (
        by_label(pois, "All Saints Anglican Cathedral")[0].detail
        == "Place of worship, 10205 101 Street NW"
    )
    assert by_label(pois, "Edmonton Visitor Centre")[0].detail == "Visitor centre"
    assert (
        by_label(pois, "Yogen Fruz")[0].detail == "Frozen yogurt, 10200 102 Avenue NW"
    )


def test_aliases_come_from_brand_short_and_alt_names(pois: list[NamedPlace]) -> None:
    assert by_label(pois, "Tim Hortons")[0].aliases == ()  # the brand is the name
    assert by_label(pois, "Reserve Roastery")[0].aliases == ("Starbucks",)
    assert by_label(pois, "Chipotle Mexican Grill")[0].aliases == ("Chipotle",)
    tags = {
        "name": "TD",
        "brand": "TD",
        "alt_name": "TD Canada Trust;TD Bank",
        "short_name": "TD",
    }
    parsed, _ = parse_poi_elements(
        [
            {
                "type": "node",
                "id": 1,
                "lat": 53.5,
                "lon": -113.5,
                "tags": {**tags, "amenity": "bank"},
            }
        ]
    )
    assert parsed[0].aliases == ("TD Canada Trust", "TD Bank")


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("amenity", "place_of_worship", "Place of worship"),
        ("amenity", "cafe;restaurant", "Cafe"),
        ("shop", "supermarket", "Grocery store"),
        ("shop", "frozen_yogurt", "Frozen yogurt"),
        ("office", "yes", "Office"),
        ("office", "advertising_agency", "Advertising agency"),
        ("healthcare", "pharmacy", "Pharmacy"),
    ],
)
def test_category_labels(key: str, value: str, expected: str) -> None:
    assert category_label(key, value) == expected


def test_a_feature_is_dropped_only_when_no_tag_is_a_destination() -> None:
    parking_lot_with_shop = node(
        1, "Corner Store", 53.5, -113.5, amenity="parking", shop="convenience"
    )
    bench = node(2, "A Bench", 53.5, -113.5, amenity="bench", leisure="picnic_table")
    pois, counts = parse_poi_elements([parking_lot_with_shop, bench])
    assert [p.detail for p in pois] == ["Convenience store"]
    assert counts["not_destination"] == 1


def test_same_name_far_apart_is_two_places() -> None:
    pois, counts = parse_poi_elements(
        [
            node(1, "Safeway", 53.50, -113.50, shop="supermarket"),
            node(2, "Safeway", 53.50, -113.5006, shop="supermarket"),  # 40 m: the same
            node(3, "Safeway", 53.52, -113.50, shop="supermarket"),  # 2 km: another one
        ]
    )
    assert counts["duplicate"] == 1 and len(pois) == 2


# ------------------------------------------------------------------ ranking


def test_a_chain_returns_its_locations_with_distinct_details(
    index: SearchIndex,
) -> None:
    results = index.search("tim hortons")
    assert labels(results) == ["Tim Hortons"] * 3
    details = [r.detail for r in results]
    assert len(set(details)) == 3
    assert "Cafe, 10205 101 Street NW" in details
    assert {r.kind for r in results} == {"place"}


def test_a_unique_shop_is_found_by_name(index: SearchIndex) -> None:
    first = index.search("audreys")[0]
    assert (first.label, first.detail, first.kind) == (
        "Audreys Books",
        "Bookstore, 10702 Jasper Avenue NW",
        "place",
    )
    assert index.search("audreys books")[0].label == "Audreys Books"


def test_a_category_word_finds_the_category(index: SearchIndex) -> None:
    results = index.search("pharmacy")
    assert set(labels(results)) == {"Shoppers Drug Mart", "Family Drugs"}
    assert all((r.detail or "").startswith("Pharmacy") for r in results)
    restaurants = index.search("fast food")
    assert labels(restaurants) == ["Chipotle Mexican Grill"]


def test_an_address_query_still_finds_the_address_first(index: SearchIndex) -> None:
    results = index.search("10220 104 ave")
    assert (results[0].label, results[0].kind) == ("10220 104 Avenue NW", "address")
    # Then the places at that address: the config place before the cafe.
    assert labels(results)[1:] == ["Rogers Place", "Tim Hortons"]


def test_station_and_config_aliases_still_work(index: SearchIndex) -> None:
    assert index.search("government centre")[0].label == "Government Station"
    assert index.search("u of a")[0].label == "University of Alberta"
    assert index.search("churchill square")[0].label == "Sir Winston Churchill Square"
    results = index.search("churchill")
    assert {r.label for r in results[:3]} == {
        "Churchill Station",
        "Churchill Stop",
        "Sir Winston Churchill Square",
    }
    assert "Attraction" not in {(r.detail or "").split(",")[0] for r in results}


def test_a_name_prefix_beats_an_alias_match(index: SearchIndex) -> None:
    assert labels(index.search("starbucks")) == ["Starbucks", "Reserve Roastery"]
    assert labels(index.search("chipotle")) == ["Chipotle Mexican Grill"]


def test_stations_and_config_places_come_before_points_of_interest(
    index: SearchIndex,
) -> None:
    # "Rogers" is the shorter label, but Rogers Place is curated.
    assert labels(index.search("rogers")) == ["Rogers Place", "Rogers"]
    assert labels(index.search("central"))[0] == "Central Station"


def test_fuzzy_still_fixes_a_typo(index: SearchIndex) -> None:
    assert index.search("audrys")[0].label == "Audreys Books"
    assert index.search("hortns")[0].label == "Tim Hortons"
    assert index.search("rogres")[0].label == "Rogers Place"


def test_a_typo_is_corrected_against_curated_words_first() -> None:
    """ "rogres" is closer to "progress" (0.86) than to "rogers" (0.83), but the
    curated vocabulary is tried first, so Rogers Place still wins; a typo that
    only a shop word can explain is still corrected."""
    places = search.load_places(PLACES)
    shops = [
        NamedPlace("Progress Academy", "place", 53.5, -113.5, "School", (), GROUP_POI),
        NamedPlace("Chipotle", "place", 53.5, -113.6, "Fast food", (), GROUP_POI),
    ]
    built = build_index(ADDRESSES, [*places, *shops])
    assert built.search("rogres")[0].label == "Rogers Place"
    assert built.search("chipotel")[0].label == "Chipotle"
    curated_words, poi_words = built._vocabularies
    assert "rogers" in curated_words and "progress" in poi_words
    assert not set(curated_words) & set(poi_words)


def test_results_stay_capped(index: SearchIndex) -> None:
    assert len(index.search("station")) == MAX_RESULTS == 8


# ------------------------------------------------------------------ the file


def test_the_npz_round_trips_with_the_group_array(
    index: SearchIndex, tmp_path: Path
) -> None:
    path = tmp_path / "search.npz"
    index.save(path)
    with np.load(path, allow_pickle=False) as data:
        assert int(data["format_version"]) == FORMAT_VERSION == 2
        groups = data["name_group"]
        assert groups.dtype == np.int8
        assert set(groups.tolist()) == {GROUP_CURATED, GROUP_POI}
        assert len(groups) == len(data["name_label"])
        # The prebuilt lookups travel with the file, so loading normalises nothing.
        for key in search.LOOKUP_KEYS:
            assert np.array_equal(data[key], index.lookup[key]), key
        assert (
            data["word_tok"].dtype.kind == "U" and data["word_owner"].dtype == np.int32
        )
    loaded = SearchIndex.load(path)
    for query in ("tim hortons", "pharmacy", "10220 104 ave", "churchill", "rogers"):
        assert loaded.search(query) == index.search(query)
    assert loaded.search("audrys") == index.search("audrys")  # fuzzy: the vocabulary
    rebuilt = SearchIndex(
        **{
            k: v
            for k, v in dict(np.load(path)).items()
            if k != "format_version" and k not in search.LOOKUP_KEYS and k != "manifest"
        },
        manifest=loaded.manifest,
    )
    assert np.array_equal(rebuilt.lookup["word_tok"], loaded.lookup["word_tok"])
    counts = loaded.manifest["counts"]
    assert (counts["stations"], counts["places"], counts["points_of_interest"]) == (
        29,
        54,
        14,
    )
    assert loaded.manifest["osm_attribution"] == "© OpenStreetMap contributors"


def test_a_group_1_file_sorts_curated_entries_first(index: SearchIndex) -> None:
    kinds_and_groups = list(zip(index.name_kind, index.name_group, strict=True))
    places = [g for k, g in kinds_and_groups if k == "place"]
    assert places == sorted(places)  # config places, then points of interest


# ------------------------------------------------------- the recorded response


def test_the_recorded_response_matches_its_manifest() -> None:
    manifest = json.loads((OSM_POIS / "manifest.json").read_text())
    for entry in manifest["files"]:
        data = (OSM_POIS / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert len(json.loads(data)["elements"]) == entry["elements"]
    assert manifest["attribution"] == "© OpenStreetMap contributors"


def test_the_recorded_downtown_features_parse(curated: list[NamedPlace]) -> None:
    rows, _station_rows, _provenance = builder.read_fixtures(FIXTURES)
    addresses = build_index(parse_parcel_rows(rows), [])
    elements, provenance = builder.read_poi_fixture(FIXTURES)
    pois, counts = parse_poi_elements(elements, addresses=addresses, curated=curated)
    assert provenance["extracted_at"] == "2026-11-08T20:49:33Z"
    assert counts["returned"] == 322
    assert counts["kept"] == len(pois) == 285
    assert (
        counts["not_destination"] == 18
    )  # parking, parkade entrances, a clock, an ATM...
    assert counts["station"] == 1
    assert counts["curated_duplicate"] == 15  # Rogers Place, City Hall, the Winspear...
    assert counts["duplicate"] == 3
    assert (
        counts["without_address"] <= 1
    )  # the downtown sample has an address almost everywhere
    kept = {p.label for p in pois}
    assert {"The Westin Edmonton", "Tim Hortons", "Audreys Books"} & kept == {
        "The Westin Edmonton",
        "Tim Hortons",
    }
    assert (
        not {"Rogers Place", "Edmonton City Hall", "Sir Winston Churchill Square"}
        & kept
    )
    assert all(p.detail for p in pois)
    westin = by_label(pois, "The Westin Edmonton")[0]
    assert westin.detail == "Hotel, 10135 100 Street NW" and westin.aliases == (
        "Westin",
    )


def test_the_script_builds_points_of_interest_from_fixtures_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the fixture build must not use the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    out = tmp_path / "search.npz"
    assert builder.main(["--fixtures", str(FIXTURES), "--out", str(out)]) == 0
    built = SearchIndex.load(out)
    counts = built.manifest["counts"]
    assert (counts["places"], counts["points_of_interest"]) == (54, 285)
    assert built.manifest["points_of_interest"]["parsed"]["kept"] == 285
    assert built.search("westin")[0].label == "The Westin Edmonton"
    assert built.search("rogers")[0].label == "Rogers Place"  # the config place, once
    assert labels(built.search("rogers place")) == ["Rogers Place"]
    # And without them.
    plain = tmp_path / "plain.npz"
    assert (
        builder.main(["--fixtures", str(FIXTURES), "--out", str(plain), "--no-pois"])
        == 0
    )
    assert SearchIndex.load(plain).manifest["counts"]["points_of_interest"] == 0


# ------------------------------------------------------------------ Overpass


def test_the_overpass_query_asks_for_named_centres_only() -> None:
    query = builder.overpass_query("53.5,-113.6,53.6,-113.5", timeout_s=42)
    assert query.startswith("[out:json][timeout:42];")
    for tag in search.POI_TAGS:
        assert f'nwr["{tag}"]["name"](53.5,-113.6,53.6,-113.5);' in query
    assert query.rstrip().endswith("out center;")
    assert "out geom" not in query and "out body" not in query
    assert (
        builder.bbox_filter((-113.6, 53.5, -113.5, 53.6))
        == "53.500000,-113.600000,53.600000,-113.500000"
    )


def test_fetch_overpass_waits_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr(builder.time, "sleep", waits.append)
    answers = iter(
        [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(
                200, json={"remark": "runtime error: Query timed out", "elements": []}
            ),
            httpx.Response(
                200, json={"osm3s": {}, "elements": [{"type": "node", "id": 1}]}
            ),
        ]
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: next(answers))
    ) as client:
        body = builder.fetch_overpass(client, "query")
    assert body["elements"] == [{"type": "node", "id": 1}]
    assert waits == [7.0, 60.0]  # Retry-After, then 30 s times the attempt


def test_fetch_overpass_gives_up_after_its_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder.time, "sleep", lambda _s: None)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(504))
        ) as client,
        pytest.raises(builder.OverpassError, match="4 attempts"),
    ):
        builder.fetch_overpass(client, "query")


def test_a_failing_area_is_split_into_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builder.time, "sleep", lambda _s: None)
    monkeypatch.setattr(builder, "OVERPASS_ATTEMPTS", 1)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.content.decode())["data"][0]  # the form field
        south, west, north, east = (
            float(v) for v in query.split("](")[1].split(");")[0].split(",")
        )
        seen.append(f"{south:.2f},{west:.2f},{north:.2f},{east:.2f}")
        if north - south > 0.075:  # the whole 0.1 degree box is "too big" and times out
            return httpx.Response(504)
        return httpx.Response(
            200,
            json={
                "elements": [
                    {"type": "node", "id": len(seen), "lat": south, "lon": west}
                ]
            },
        )

    log: list[str] = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        elements = builder._fetch_tiles(client, (-113.6, 53.5, -113.5, 53.6), 0, log)
    assert len(elements) == 4 and len(log) == 4
    assert seen[0] == "53.50,-113.60,53.60,-113.50" and len(seen) == 5


# ------------------------------------------------------------ committed index


committed = pytest.mark.skipif(
    not search.DEFAULT_INDEX_PATH.exists(), reason="cloud/data/search.npz not built"
)


@committed
def test_the_committed_index_has_points_of_interest() -> None:
    full = SearchIndex.load()
    counts = full.manifest["counts"]
    assert (
        counts["points_of_interest"] > 1_000
    )  # a sanity floor, not the measured count
    assert counts["places"] == 54 and counts["stations"] == 29
    assert full.manifest["points_of_interest"]["extracted_at"]
    assert search.DEFAULT_INDEX_PATH.stat().st_size < 4_500_000


@committed
def test_the_committed_index_finds_shops_and_keeps_old_answers() -> None:
    full = SearchIndex.load()
    chipotle = full.search("chipotle")
    assert chipotle and all(r.label.lower().startswith("chipotle") for r in chipotle)
    assert all(r.kind == "place" and r.detail for r in chipotle)
    pharmacies = full.search("pharmacy")
    assert len(pharmacies) == MAX_RESULTS
    assert all(
        "pharmacy" in (r.detail or "").lower() or "pharmacy" in r.label.lower()
        for r in pharmacies
    )
    churchill = full.search("churchill")
    assert {"Churchill Stop", "Sir Winston Churchill Square"} <= set(
        labels(churchill)[:3]
    )
    assert full.search("10220 104 ave")[0].label == "10220 104 Avenue NW"
    assert full.search("rogers")[0].label == "Rogers Place"
    assert full.search("u of a")[0].label == "University of Alberta"
    assert full.search("government centre")[0].label == "Government Station"
    assert full.search("rogres")[0].label == "Rogers Place"  # a typo, as before
    assert full.search("chipotel")[0].label.lower().startswith("chipotle")


@committed
def test_the_committed_index_loads_quickly_enough() -> None:
    """A loose bound so CI never flakes; the measured time is in docs/DATA_SOURCES.md."""
    started = time.perf_counter()
    SearchIndex.load()
    assert time.perf_counter() - started < 2.0
