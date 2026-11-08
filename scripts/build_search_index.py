"""Build the address and place search index, ``cloud/data/search.npz`` (OP-302, OP-416).

    python scripts/build_search_index.py                  # live: City API + Overpass, then commit
    python scripts/build_search_index.py --write-places   # also refresh config/places.json coordinates
    python scripts/build_search_index.py --no-pois        # addresses, stations and places only
    python scripts/build_search_index.py --fixtures       # offline, from cloud/tests/fixtures

This is a build step on a laptop, like ``scripts/build_graph.py``: the API never
downloads addresses and never calls a geocoder at request time (AGENTS.md). It
loads the committed file at startup (``cloud/app/search.py``).

Sources
-------
- City of Edmonton Parcel Addresses, Socrata ``ut27-nrpn``: every ``PARCEL`` row,
  plus the distinct ``SUITE`` points (grouped by the server), read in pages of
  ``--page-size`` rows. ``SOCRATA_APP_TOKEN`` is sent as ``X-App-Token`` when set.
- City of Edmonton LRT Stations and Stops, Socrata ``fhxi-cnhe``.
- ``config/places.json``: named destinations. Each place's coordinate comes from
  the parcel address of its street address (``coord_source: parcel_address``) or,
  where that address is not in the dataset, from the LRT station beside it
  (``coord_source: lrt_station``). ``--write-places`` recomputes both from the
  freshly downloaded data; without it the build only warns about any place whose
  stored coordinate has moved more than 25 m.
- OpenStreetMap points of interest (OP-416, ``--pois``, on by default): every
  named feature with an ``amenity``, ``shop``, ``healthcare``, ``tourism``,
  ``leisure`` or ``office`` tag inside the city boundary, from the Overpass API
  (``out center``: one point per feature, never geometry). The boundary is the
  one ``scripts/build_graph.py`` builds the walking network on: OSMnx's geocode
  of ``--place`` ("Edmonton, Alberta, Canada"). OSMnx is needed only for that
  geocode, so it is installed with ``scripts/requirements-graph.txt`` into the
  build laptop's venv (``make graph`` does the same), never on Render. If the
  whole-city query fails or is rate limited, the script waits and splits the
  boundary's bounding box into tiles, each read on its own. Responses are cached
  in ``--cache-dir`` (``--fresh`` ignores the cache). ``parse_poi_elements`` in
  ``cloud/app/search.py`` decides what is kept. ODbL, © OpenStreetMap contributors.

``--fixtures [DIR]`` reads ``DIR/parcel_addresses/*.json``, ``DIR/lrt/stations.json``
and ``DIR/osm_pois/elements.json`` (the recorded responses) and writes to a
temporary file unless ``--out`` is given, so it can never overwrite the committed
citywide index by accident.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloud.app.search import (
    DEFAULT_INDEX_PATH,
    DEFAULT_PLACES_PATH,
    PARCEL_DATASET,
    POI_TAGS,
    STATIONS_DATASET,
    NamedPlace,
    SearchIndex,
    build_index,
    distance_m,
    load_places,
    load_station_aliases,
    parse_parcel_rows,
    parse_poi_elements,
    parse_station_rows,
    split_house_number,
)

CITY_API = "https://data.edmonton.ca/resource"
DEFAULT_FIXTURES = ROOT / "cloud" / "tests" / "fixtures"
PAGE_SIZE = 50_000
TIMEOUT_S = 120.0
RETRIES = 3
# A stored place coordinate further than this from the fresh data is reported.
PLACE_DRIFT_M = 25.0

# Points of interest (OP-416). The place is the one scripts/build_graph.py uses.
PLACE = "Edmonton, Alberta, Canada"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_S = 600  # the server-side limit in the query; the client waits longer
OVERPASS_ATTEMPTS = 4
OVERPASS_PAUSE_S = 5.0  # between tile requests, so one build never floods the server
MAX_SPLIT_DEPTH = 3  # 2x2 tiles per level: at most 64 tiles
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "openpath" / "overpass"
USER_AGENT = "OpenPath search index builder (github.com/tatsatshah05/NatHacks2026)"
# The polygon sent to Overpass is simplified (about 50 m) and padded (about 100 m);
# the exact boundary is applied to the returned points afterwards.
POLY_SIMPLIFY_DEG = 0.0005
POLY_PAD_DEG = 0.001

PARCEL_QUERY = {
    "$select": "house_number,street_name,latitude,longitude,object_type",
    "$where": "object_type='PARCEL'",
    "$order": ":id",
}
# Many suites share one building point, so the server groups them to distinct points.
SUITE_QUERY = {
    "$select": "house_number,street_name,latitude,longitude,object_type",
    "$where": "object_type='SUITE'",
    "$group": "house_number,street_name,latitude,longitude,object_type",
    "$order": "street_name,house_number,latitude,longitude",
}
STATION_QUERY = {"$limit": "1000"}


# ------------------------------------------------------------------ reading


def fetch_pages(
    client: httpx.Client,
    dataset: str,
    query: Mapping[str, str],
    *,
    page_size: int = PAGE_SIZE,
) -> Iterator[list[dict[str, Any]]]:
    """Every row of one Socrata query, a page at a time, retrying a failed page."""
    offset = 0
    while True:
        params = {**query, "$limit": str(page_size), "$offset": str(offset)}
        for attempt in range(1, RETRIES + 1):
            try:
                response = client.get(f"{CITY_API}/{dataset}.json", params=params)
                response.raise_for_status()
                page = response.json()
                break
            except (httpx.HTTPError, ValueError) as error:
                if attempt == RETRIES:
                    raise
                print(
                    f"  {dataset} offset {offset}: {error}; retrying", file=sys.stderr
                )
                time.sleep(2**attempt)
        if not isinstance(page, list):
            raise TypeError(f"{dataset} returned {type(page).__name__}, not a list")
        yield page
        if len(page) < page_size:
            return
        offset += page_size


def read_live(
    token: str | None, page_size: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parcel address rows, station rows and provenance from the official City API."""
    headers = {"X-App-Token": token} if token else {}
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    started = time.time()
    with httpx.Client(timeout=TIMEOUT_S, headers=headers) as client:
        for name, query in (("parcel", PARCEL_QUERY), ("suite", SUITE_QUERY)):
            before = len(rows)
            for page in fetch_pages(client, PARCEL_DATASET, query, page_size=page_size):
                rows.extend(page)
                print(f"  {PARCEL_DATASET} {name}: {len(rows) - before} rows")
            counts[f"{name}_rows_read"] = len(rows) - before
        stations = next(fetch_pages(client, STATIONS_DATASET, STATION_QUERY))
    provenance = {
        "mode": "live",
        "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "queries": {
            "parcels": f"{CITY_API}/{PARCEL_DATASET}.json {json.dumps(PARCEL_QUERY)}",
            "suites": f"{CITY_API}/{PARCEL_DATASET}.json {json.dumps(SUITE_QUERY)}",
            "stations": f"{CITY_API}/{STATIONS_DATASET}.json",
        },
        **counts,
    }
    return rows, stations, provenance


def read_fixtures(
    snapshot: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """The recorded responses: ``parcel_addresses/*.json`` and ``lrt/stations.json``."""
    rows: list[dict[str, Any]] = []
    folder = snapshot / "parcel_addresses"
    for path in sorted(folder.glob("*.json")):
        if path.name == "manifest.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(row for row in data if isinstance(row, dict))
    stations = json.loads(
        (snapshot / "lrt" / "stations.json").read_text(encoding="utf-8")
    )
    manifest_path = folder / "manifest.json"
    recorded = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    provenance = {
        "mode": "fixtures",
        "fixtures": str(
            folder.relative_to(ROOT) if folder.is_relative_to(ROOT) else folder
        ),
        "area": recorded.get("area"),
    }
    return rows, stations, provenance


# ------------------------------------------------------------------- places


def resolve_place(
    entry: Mapping[str, Any], index: SearchIndex, stations: Sequence[NamedPlace]
) -> tuple[float, float] | None:
    """The coordinate a place's ``coord_source`` gives, from the fresh data."""
    source = entry.get("coord_source")
    if source == "parcel_address":
        number, street = split_house_number(entry["address"].split())
        if number is None:
            return None
        return index.find_address(number, " ".join(street))
    if source == "lrt_station":
        for station in stations:
            if station.label == entry.get("station"):
                return station.lat, station.lng
    return None


def check_places(
    places_path: Path,
    index: SearchIndex,
    stations: Sequence[NamedPlace],
    *,
    write: bool,
) -> list[str]:
    """Resolve every place again; rewrite the coordinates with ``write``. Returns problems."""
    raw = json.loads(places_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for entry in raw["places"]:
        point = resolve_place(entry, index, stations)
        if point is None:
            problems.append(f"{entry['name']}: {entry.get('coord_source')} not found")
            continue
        lat, lng = round(point[0], 6), round(point[1], 6)
        stored = (float(entry.get("lat", 0)), float(entry.get("lng", 0)))
        moved = distance_m(stored, (lat, lng))
        if write:
            entry["lat"], entry["lng"] = lat, lng
        elif moved > PLACE_DRIFT_M:
            problems.append(
                f"{entry['name']}: stored point is {moved:.0f} m from the data"
            )
    if write:
        places_path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return problems


# ------------------------------------------------------- points of interest


class OverpassError(RuntimeError):
    """The Overpass API did not answer a query after every attempt."""


def overpass_query(area: str, timeout_s: int = OVERPASS_TIMEOUT_S) -> str:
    """Named features of every POI tag in ``area`` (a bbox or ``poly:`` filter), centres only."""
    statements = "\n".join(f'  nwr["{tag}"]["name"]({area});' for tag in POI_TAGS)
    return f"[out:json][timeout:{timeout_s}];\n(\n{statements}\n);\nout center;\n"


def bbox_filter(bounds: tuple[float, float, float, float]) -> str:
    """Overpass bbox filter from shapely bounds (west, south, east, north)."""
    west, south, east, north = bounds
    return f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"


def poly_filter(polygon: Any) -> str:
    """Overpass ``poly:`` filter of a shapely polygon's exterior, simplified and padded."""
    outline = polygon.buffer(POLY_PAD_DEG).simplify(POLY_SIMPLIFY_DEG)
    if outline.geom_type == "MultiPolygon":
        outline = max(outline.geoms, key=lambda part: part.area)
    points = " ".join(f"{lat:.5f} {lng:.5f}" for lng, lat in outline.exterior.coords)
    return f'poly:"{points}"'


def city_boundary(place: str, cache_dir: Path) -> Any:
    """The boundary ``scripts/build_graph.py`` uses: OSMnx's geocode of ``place`` (shapely)."""
    import osmnx as ox  # build time only; never installed on Render

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir / "osmnx")
    ox.settings.log_console = False
    boundary = ox.geocode_to_gdf(place).geometry.union_all()
    if boundary.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"{place!r} geocoded to a {boundary.geom_type}, not an area")
    return boundary


def fetch_overpass(client: httpx.Client, query: str) -> dict[str, Any]:
    """One Overpass response, retried with a pause after a timeout, 429 or 5xx."""
    for attempt in range(1, OVERPASS_ATTEMPTS + 1):
        try:
            response = client.post(OVERPASS_URL, data={"data": query})
            if response.status_code in {429, 502, 503, 504}:
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("elements"), list):
                raise TypeError("response is not an Overpass JSON document")
            if str(body.get("remark", "")).startswith("runtime error"):
                # A timeout or memory limit inside Overpass: the answer is partial.
                raise ValueError(body["remark"])
            return body
        except (httpx.HTTPError, TypeError, ValueError) as error:
            if attempt == OVERPASS_ATTEMPTS:
                raise OverpassError(
                    f"Overpass failed after {attempt} attempts: {error}"
                )
            retry_after = None
            if isinstance(error, httpx.HTTPStatusError):
                retry_after = error.response.headers.get("Retry-After")
            wait_s = (
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else 30.0 * attempt
            )
            print(f"  overpass: {error}; waiting {wait_s:.0f} s", file=sys.stderr)
            time.sleep(wait_s)
    raise OverpassError("unreachable")  # pragma: no cover


def _fetch_tiles(
    client: httpx.Client,
    bounds: tuple[float, float, float, float],
    depth: int,
    log: list[str],
) -> list[dict[str, Any]]:
    """The elements of a bbox, splitting it 2x2 (down to ``MAX_SPLIT_DEPTH``) when it fails."""
    try:
        time.sleep(OVERPASS_PAUSE_S)
        body = fetch_overpass(client, overpass_query(bbox_filter(bounds)))
        log.append(f"tile {bbox_filter(bounds)}: {len(body['elements'])} elements")
        return body["elements"]
    except OverpassError:
        if depth >= MAX_SPLIT_DEPTH:
            raise
        print(f"  splitting {bbox_filter(bounds)} into 4 tiles", file=sys.stderr)
    west, south, east, north = bounds
    mid_x, mid_y = (west + east) / 2, (south + north) / 2
    elements: list[dict[str, Any]] = []
    for tile in (
        (west, south, mid_x, mid_y),
        (mid_x, south, east, mid_y),
        (west, mid_y, mid_x, north),
        (mid_x, mid_y, east, north),
    ):
        elements.extend(_fetch_tiles(client, tile, depth + 1, log))
    return elements


def fetch_pois(
    place: str, *, cache_dir: Path, fresh: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every named POI element inside the city boundary, with provenance for the manifest.

    One Overpass request for the whole boundary; if it fails after its retries, the
    boundary's bounding box is read as tiles instead. Points outside the exact
    boundary are dropped. The merged response is cached in ``cache_dir``.
    """
    import shapely

    boundary = city_boundary(place, cache_dir)
    whole_query = overpass_query(poly_filter(boundary))
    key = hashlib.sha256(whole_query.encode()).hexdigest()[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"pois-{key}.json"
    if cache_file.exists() and not fresh:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        print(
            f"  overpass: using {cache_file} ({age_h:.1f} h old; --fresh re-reads)",
            flush=True,
        )
    else:
        started = time.time()
        headers = {"User-Agent": USER_AGENT}
        requests_log: list[str] = []
        with httpx.Client(timeout=OVERPASS_TIMEOUT_S + 120, headers=headers) as client:
            try:
                print(f"  overpass: reading {place!r} in one request", flush=True)
                body = fetch_overpass(client, whole_query)
                elements = body["elements"]
                osm_base = body.get("osm3s", {}).get("timestamp_osm_base")
                requests_log.append(f"whole boundary: {len(elements)} elements")
            except OverpassError as error:
                print(
                    f"  overpass: {error}; reading the boundary as tiles",
                    file=sys.stderr,
                )
                elements = _fetch_tiles(client, tuple(boundary.bounds), 0, requests_log)
                osm_base = None
        cached = {
            "place": place,
            "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "osm_base": osm_base,
            "requests": requests_log,
            "elements": elements,
        }
        cache_file.write_text(json.dumps(cached), encoding="utf-8")
        print(
            f"  overpass: cached {len(elements)} elements in {cache_file}", flush=True
        )

    # Tiles overlap at their edges and a poly filter is padded: keep one copy of each
    # feature, then only those inside the exact boundary.
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for element in cached["elements"]:
        unique.setdefault(
            (str(element.get("type")), int(element.get("id", 0))), element
        )
    shapely.prepare(boundary)
    inside: list[dict[str, Any]] = []
    outside = 0
    for element in unique.values():
        source = (
            element if element.get("type") == "node" else element.get("center") or {}
        )
        try:
            keep = bool(
                shapely.contains_xy(
                    boundary, float(source["lon"]), float(source["lat"])
                )
            )
        except (KeyError, TypeError, ValueError):
            keep = True  # parse_poi_elements counts and drops it as no_point
        if keep:
            inside.append(element)
        else:
            outside += 1
    provenance = {
        "source": OVERPASS_URL,
        "place": place,
        "extracted_at": cached["extracted_at"],
        "osm_base": cached.get("osm_base"),
        "query": overpass_query("<boundary>"),
        "requests": cached["requests"],
        "elements_returned": len(cached["elements"]),
        "repeated_across_tiles": len(cached["elements"]) - len(unique),
        "outside_boundary": outside,
    }
    return inside, provenance


def read_poi_fixture(snapshot: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The recorded Overpass response ``osm_pois/elements.json``, or nothing if absent."""
    folder = snapshot / "osm_pois"
    path = folder / "elements.json"
    if not path.exists():
        return [], {"mode": "fixtures", "fixture": None}
    body = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = folder / "manifest.json"
    recorded = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    files = recorded.get("files") or [{}]
    return body.get("elements", []), {
        "mode": "fixtures",
        "fixture": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
        "extracted_at": files[0].get("captured_at"),
        "osm_base": files[0].get("osm_base"),
        "area": recorded.get("area"),
    }


# --------------------------------------------------------------------- main


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--fixtures",
        nargs="?",
        type=Path,
        const=DEFAULT_FIXTURES,
        help="build offline from recorded responses (default folder: cloud/tests/fixtures)",
    )
    parser.add_argument("--out", type=Path, help="where to write the index")
    parser.add_argument("--places", type=Path, default=DEFAULT_PLACES_PATH)
    parser.add_argument(
        "--write-places",
        action="store_true",
        help="recompute the coordinates in config/places.json from the fresh data (live only)",
    )
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    parser.add_argument(
        "--pois",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="add OpenStreetMap points of interest (Overpass API, or the recorded fixture)",
    )
    parser.add_argument(
        "--place",
        default=PLACE,
        help="the boundary to read points of interest in (as scripts/build_graph.py)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="where Overpass responses and the geocoded boundary are cached",
    )
    parser.add_argument(
        "--fresh", action="store_true", help="ignore cached Overpass responses"
    )
    args = parser.parse_args(argv)

    poi_elements: list[dict[str, Any]] = []
    poi_provenance: dict[str, Any] = {}
    if args.fixtures is not None:
        if args.write_places:
            parser.error(
                "--write-places needs the citywide data, so it cannot use --fixtures"
            )
        out = args.out or Path(tempfile.gettempdir()) / "openpath-search-fixtures.npz"
        rows, station_rows, provenance = read_fixtures(args.fixtures)
        if args.pois:
            poi_elements, poi_provenance = read_poi_fixture(args.fixtures)
    else:
        out = args.out or DEFAULT_INDEX_PATH
        print(
            f"Reading {PARCEL_DATASET} and {STATIONS_DATASET} from the City open data API"
        )
        rows, station_rows, provenance = read_live(
            os.environ.get("SOCRATA_APP_TOKEN") or None, args.page_size
        )
        if args.pois:
            print("Reading points of interest from the Overpass API")
            poi_elements, poi_provenance = fetch_pois(
                args.place, cache_dir=args.cache_dir, fresh=args.fresh
            )

    started = time.perf_counter()
    stations = parse_station_rows(station_rows, load_station_aliases(args.places))
    addresses = parse_parcel_rows(rows)
    # Build once without places to resolve their coordinates, then with them.
    index = build_index(addresses, stations, manifest=provenance)
    if args.fixtures is None:
        problems = check_places(args.places, index, stations, write=args.write_places)
        for problem in problems:
            print(f"  PLACE {problem}", file=sys.stderr)
        if problems and args.write_places:
            return 1
    places = load_places(args.places)
    pois: list[NamedPlace] = []
    if args.pois:
        pois, dropped = parse_poi_elements(
            poi_elements, addresses=index, curated=[*stations, *places]
        )
        provenance = {
            **provenance,
            "points_of_interest": {**poi_provenance, "parsed": dropped},
        }
        print(
            "  points of interest: "
            + ", ".join(f"{key} {value}" for key, value in sorted(dropped.items()))
        )
    index = build_index(addresses, [*stations, *places, *pois], manifest=provenance)
    index.save(out)
    elapsed = time.perf_counter() - started
    counts = index.manifest["counts"]
    print(
        f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB) in {elapsed:.1f} s: "
        f"{counts['addresses']} addresses on {counts['streets']} streets, "
        f"{counts['stations']} stations, {counts['places']} places, "
        f"{counts['points_of_interest']} points of interest"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
