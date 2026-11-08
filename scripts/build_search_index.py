"""Build the address and place search index, ``cloud/data/search.npz`` (OP-302).

    python scripts/build_search_index.py                  # live: official City API, then commit
    python scripts/build_search_index.py --write-places   # also refresh config/places.json coordinates
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

``--fixtures [DIR]`` reads ``DIR/parcel_addresses/*.json`` and ``DIR/lrt/stations.json``
(the recorded responses) and writes to a temporary file unless ``--out`` is given,
so it can never overwrite the committed citywide index by accident.
"""

from __future__ import annotations

import argparse
import json
import math
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
    STATIONS_DATASET,
    NamedPlace,
    SearchIndex,
    build_index,
    load_places,
    load_station_aliases,
    parse_parcel_rows,
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


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular distance; plenty for checks across a city."""
    lat = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(lat)
    dy = math.radians(b[0] - a[0])
    return 6_371_008.8 * math.hypot(dx, dy)


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
    args = parser.parse_args(argv)

    if args.fixtures is not None:
        if args.write_places:
            parser.error(
                "--write-places needs the citywide data, so it cannot use --fixtures"
            )
        out = args.out or Path(tempfile.gettempdir()) / "openpath-search-fixtures.npz"
        rows, station_rows, provenance = read_fixtures(args.fixtures)
    else:
        out = args.out or DEFAULT_INDEX_PATH
        print(
            f"Reading {PARCEL_DATASET} and {STATIONS_DATASET} from the City open data API"
        )
        rows, station_rows, provenance = read_live(
            os.environ.get("SOCRATA_APP_TOKEN") or None, args.page_size
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
    index = build_index(
        addresses, [*stations, *load_places(args.places)], manifest=provenance
    )
    index.save(out)
    elapsed = time.perf_counter() - started
    counts = index.manifest["counts"]
    print(
        f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB) in {elapsed:.1f} s: "
        f"{counts['addresses']} addresses on {counts['streets']} streets, "
        f"{counts['stations']} stations, {counts['places']} places"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
