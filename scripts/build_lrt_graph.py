"""Build ``cloud/data/lrt.json``, the LRT station graph for walk and LRT trips (OP-311).

    python scripts/build_lrt_graph.py                  # one official download, then write
    python scripts/build_lrt_graph.py --gtfs gtfs.zip  # from a zip already downloaded

The ETS GTFS schedule is City of Edmonton open data (``GTFS_URL``). This script
downloads it once into a temporary folder, reads the light-rail routes with
``cloud.app.lrt_gtfs``, adds what routing needs from recorded City data, and writes
one small JSON file, which is committed. The zip itself is never committed.

What is added to the GTFS graph
    City LRT device master list (``--devices``, dataset javh-pjj2, recorded)
        the elevators and escalators of each station, by device id, so the planner
        can tell which live outage (``city_lrt_devices:<device_id>``) closes which
        station. An elevator whose location names a platform is ``platform`` (it
        must work); any other is ``entrance`` (one of them must work).
    City station names (``--stations``, dataset fhxi-cnhe, recorded)
        display names and aliases, matched to GTFS by a normalised key.
    OP-106 elevator mapping (``--elevators``, docs/elevators.proposed.json)
        reviewed OSM anchor nodes become the station's entry nodes on the walking
        network. Candidates are not used; the station point stands in.
    access
        ``elevator`` when the station has City elevators or GTFS elevator pathways;
        otherwise ``at_grade`` (a street-level stop needs no elevator).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so ``cloud.app.lrt_gtfs`` imports from anywhere

from cloud.app.lrt_gtfs import (
    FORMAT_VERSION,
    LrtGraph,
    read_lrt,
    short_name,
    station_key,
)

# The City's GTFS server, linked from the ETS data for developers page and listed
# as the producer URL in the feed's own attributions.txt.
GTFS_URL = "https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/GTFS.zip"
LICENCE_URL = "https://data.edmonton.ca/stories/s/City-of-Edmonton-Open-Data-Terms-of-Use/msh8-if28/"
ATTRIBUTION = "Schedule: Edmonton Transit Service, City of Edmonton Open Data"
DEFAULT_OUT = ROOT / "cloud" / "data" / "lrt.json"
DEFAULT_DEVICES = ROOT / "cloud" / "tests" / "fixtures" / "lrt" / "devices.json"
DEFAULT_STATIONS = ROOT / "cloud" / "tests" / "fixtures" / "lrt" / "stations.json"
DEFAULT_ELEVATORS = ROOT / "docs" / "elevators.proposed.json"
DOWNLOAD_TIMEOUT_S = 120


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def download(url: str, folder: Path) -> Path:
    target = folder / "gtfs.zip"
    print(f"downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "OpenPath-OP311"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
        target.write_bytes(response.read())
    print(f"  {target.stat().st_size / 1e6:.1f} MB")
    return target


def enrich(
    graph: LrtGraph,
    devices: list[dict[str, Any]],
    city_stations: list[dict[str, Any]],
    mapping: dict[str, Any] | None,
    *,
    source: dict[str, Any],
) -> dict[str, Any]:
    """The JSON document: the GTFS graph plus City devices, names and OP-106 anchors."""
    # When two GTFS stations share a key (Churchill Station Underground and the Valley
    # Line's Churchill Stop), the City's devices belong to the one called a Station.
    by_key: dict[str, list[Any]] = {}
    for station in graph.stations:
        by_key.setdefault(station_key(station.gtfs_name), []).append(station)
    preferred = {
        key: max(group, key=lambda s: ("station" in s.gtfs_name.casefold(), s.id))
        for key, group in by_key.items()
    }

    city_names: dict[str, str] = {}
    for row in city_stations:
        name = str(row.get("lrt_stop_description", "")).strip()
        if name:
            city_names.setdefault(station_key(name), short_name(name))

    elevators: dict[str, list[dict[str, str]]] = {}
    escalators: dict[str, list[dict[str, str]]] = {}
    device_names: dict[str, str] = {}
    for row in devices:
        kind = str(row.get("device_type", "")).strip().casefold()
        station_name = str(row.get("lrt_station_name", "")).strip()
        device_id = str(row.get("device_id", "")).strip()
        if kind not in {"elevator", "escalator"} or not station_name or not device_id:
            continue
        key = station_key(station_name)
        device_names.setdefault(key, station_name)
        location = str(row.get("lrt_device_location", "")).strip()
        entry = {"device_id": device_id, "location": location}
        if kind == "elevator":
            entry["role"] = (
                "platform" if "platform" in location.casefold() else "entrance"
            )
            elevators.setdefault(key, []).append(entry)
        else:
            escalators.setdefault(key, []).append(entry)

    anchors: dict[str, list[int]] = {}
    for entry in (mapping or {}).get("elevators", []):
        if entry.get("status") == "reviewed" and entry.get("anchor_node_id"):
            anchors.setdefault(station_key(entry["station"]), []).append(
                int(entry["anchor_node_id"])
            )

    stations_out = []
    for station in sorted(graph.stations, key=lambda s: s.id):
        key = station_key(station.gtfs_name)
        owns_devices = preferred[key] is station
        name = (
            device_names.get(key)
            or city_names.get(key)
            or short_name(station.gtfs_name)
        )
        is_stop = station.gtfs_name.casefold().endswith("stop")
        lifts = (
            sorted(elevators.get(key, []), key=lambda e: int(e["device_id"]))
            if owns_devices
            else []
        )
        stairs = (
            sorted(escalators.get(key, []), key=lambda e: int(e["device_id"]))
            if owns_devices
            else []
        )
        aliases = sorted(
            {
                a
                for a in (
                    device_names.get(key),
                    city_names.get(key),
                    short_name(station.gtfs_name),
                )
                if a and a != name
            }
        )
        stations_out.append(
            {
                "id": station.id,
                "name": name,
                "title": f"{name} {'Stop' if is_stop else 'Station'}",
                "gtfs_name": station.gtfs_name,
                "aliases": aliases,
                "lat": round(station.lat, 6),
                "lng": round(station.lng, 6),
                "access": "elevator"
                if lifts or station.elevator_pathways
                else "at_grade",
                "elevators": lifts,
                "escalators": stairs,
                "gtfs_elevator_pathways": station.elevator_pathways,
                "entry_osm_nodes": sorted(anchors.get(key, [])) if owns_devices else [],
            }
        )

    return {
        "format_version": FORMAT_VERSION,
        "source": source,
        "timezone": "America/Edmonton",
        "stations": stations_out,
        "lines": [
            {
                "id": line.id,
                "name": line.name,
                "short_name": line.short_name,
                "color": line.color,
                "stations": line.stations,
                "ride_s": line.ride_s,
                "headsigns": line.headsigns,
                "headway_s": line.headway_s,
                "trips": line.trips,
                "irregular_trips": line.irregular_trips,
            }
            for line in graph.lines
        ],
        "transfers": [
            {"from": t.from_station, "to": t.to_station, "walk_s": t.walk_s}
            for t in graph.transfers
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gtfs", type=Path, help="a GTFS zip already downloaded")
    parser.add_argument("--url", default=GTFS_URL, help="GTFS download URL")
    parser.add_argument("--devices", type=Path, default=DEFAULT_DEVICES)
    parser.add_argument("--stations", type=Path, default=DEFAULT_STATIONS)
    parser.add_argument("--elevators", type=Path, default=DEFAULT_ELEVATORS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="openpath-gtfs-") as tmp:
        zip_path = args.gtfs or download(args.url, Path(tmp))
        graph = read_lrt(zip_path)
    devices = load_json(args.devices) if args.devices.exists() else []
    stations = load_json(args.stations) if args.stations.exists() else []
    mapping = load_json(args.elevators) if args.elevators.exists() else None
    feed = graph.feed
    source = {
        "name": "ETS GTFS schedule",
        "url": args.url,
        "feed_publisher": feed.get("feed_publisher_name"),
        "feed_version": feed.get("feed_version"),
        "feed_start_date": feed.get("feed_start_date"),
        "feed_end_date": feed.get("feed_end_date"),
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "licence_url": LICENCE_URL,
        "attribution": ATTRIBUTION,
        "devices": str(args.devices.relative_to(ROOT)) if devices else None,
    }
    document = enrich(graph, devices, stations, mapping, source=source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")

    print(f"wrote {args.out} ({args.out.stat().st_size / 1e3:.1f} kB)")
    print(
        f"feed {source['feed_version']} valid {source['feed_start_date']} to {source['feed_end_date']}"
    )
    print(
        f"{len(document['stations'])} stations, {len(document['lines'])} lines, {len(document['transfers'])} transfers"
    )
    names = {s["id"]: s["title"] for s in document["stations"]}
    for line in document["lines"]:
        print(
            f"  {line['name']}: {len(line['stations'])} stations, "
            f"{sum(line['ride_s']) // 60} min end to end, {line['trips']} trips "
            f"({line['irregular_trips']} irregular), weekday headway at 08:00 "
            f"{line['headway_s']['weekday'][8]} s"
        )
        print(f"    {names[line['stations'][0]]} to {names[line['stations'][-1]]}")
    lifts = sum(len(s["elevators"]) for s in document["stations"])
    at_grade = sum(s["access"] == "at_grade" for s in document["stations"])
    print(f"{lifts} City elevators mapped, {at_grade} at-grade stations")


if __name__ == "__main__":
    main()
