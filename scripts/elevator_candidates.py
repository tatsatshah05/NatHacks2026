"""OP-106: prepare evidence for reviewing City elevators against OP-104.

Report nearby elevator objects and a station-point proxy from the committed
network. These are review candidates only; this tool never activates a match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np


def station_key(name: str) -> str:
    name = re.sub(r"\b(station|stop)\b", "", name, flags=re.IGNORECASE)
    key = re.sub(r"[^a-z0-9]", "", name.casefold())
    return {"governmentcentre": "government"}.get(key, key)


def distances(
    lat: np.ndarray, lng: np.ndarray, points: list[tuple[float, float]]
) -> np.ndarray:
    """Minimum great-circle distance to any official point for the station."""
    result = np.full(len(lat), np.inf)
    for station_lat, station_lng in points:
        delta_lat = np.radians(lat - station_lat)
        delta_lng = np.radians(lng - station_lng)
        haversine = (
            np.sin(delta_lat / 2) ** 2
            + math.cos(math.radians(station_lat))
            * np.cos(np.radians(lat))
            * np.sin(delta_lng / 2) ** 2
        )
        distance = 2 * 6_371_008.8 * np.arcsin(np.sqrt(np.clip(haversine, 0, 1)))
        result = np.minimum(result, distance)
    return result


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cloud.app.elevator_mapping import prepare_inventory

    parser = argparse.ArgumentParser(
        description="OP-106 review candidates, never automatic matches"
    )
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--devices", type=Path, required=True)
    parser.add_argument("--stations", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-distance-m", type=float, default=200)
    args = parser.parse_args()
    if not math.isfinite(args.max_distance_m) or args.max_distance_m <= 0:
        parser.error("--max-distance-m must be positive and finite")
    # Read only OP-104's published layout. No pickle loading or online queries.
    with np.load(args.network, allow_pickle=False) as data:
        manifest = json.loads(str(data["manifest"]))
        if manifest.get("format_version") != 1:
            raise ValueError("Unsupported OP-104 format_version")
        lat, lng = data["node_lat"], data["node_lng"]
        node_ids = data["node_osmid"]
        elevator_nodes = np.flatnonzero(data["node_flags"] & 1)
        elevator_ways = np.flatnonzero(data["edge_flags"] & 2)
        way_ids = data["edge_osmid"][elevator_ways]
        u, v = data["edge_u"][elevator_ways], data["edge_v"][elevator_ways]
        way_lat, way_lng = (lat[u] + lat[v]) / 2, (lng[u] + lng[v]) / 2
    stations = json.loads(args.stations.read_text("utf-8-sig"))
    devices = json.loads(args.devices.read_text("utf-8-sig"))
    inventory = prepare_inventory(devices)
    entries = []
    for elevator in inventory["elevators"]:
        matching = [
            row
            for row in stations
            if station_key(row["lrt_stop_description"])
            == station_key(elevator["station"])
        ]
        station_rows = [
            row for row in matching if row["lrt_stop_description"].endswith("Station")
        ] or matching
        points = [
            (float(row["latitude"]), float(row["longitude"])) for row in station_rows
        ]
        candidates = []
        proxy = None
        if points:
            node_distances = distances(lat[elevator_nodes], lng[elevator_nodes], points)
            for index, distance in zip(elevator_nodes, node_distances, strict=True):
                if distance <= args.max_distance_m:
                    osm_id = int(node_ids[index])
                    candidates.append(
                        {
                            "type": "node",
                            "id": osm_id,
                            "distance_m": round(float(distance), 1),
                            "url": f"https://www.openstreetmap.org/node/{osm_id}",
                        }
                    )
            way_distances = distances(way_lat, way_lng, points)
            for osm_id, distance in zip(way_ids, way_distances, strict=True):
                if int(osm_id) > 0 and distance <= args.max_distance_m:
                    candidates.append(
                        {
                            "type": "way",
                            "id": int(osm_id),
                            "distance_m": round(float(distance), 1),
                            "url": f"https://www.openstreetmap.org/way/{int(osm_id)}",
                        }
                    )
            all_distances = distances(lat, lng, points)
            nearest = int(np.argmin(all_distances))
            proxy = {
                "node_id": int(node_ids[nearest]),
                "distance_m": round(float(all_distances[nearest]), 1),
                "note": (
                    "Nearest node to a station point; "
                    "entrance/platform identity is not verified."
                ),
            }
        entries.append(
            {
                "device_id": elevator["device_id"],
                "station": elevator["station"],
                "location": elevator["location"],
                "station_stop_ids": [row["lrt_stop_number"] for row in station_rows],
                "elevator_candidates": sorted(
                    candidates, key=lambda row: row["distance_m"]
                ),
                "station_point_proxy": proxy,
                "review_status": "unreviewed; no active match",
            }
        )
    output = {
        "story": "OP-106",
        "generated_at": datetime.now(UTC).isoformat(),
        "network_sha256": hashlib.sha256(args.network.read_bytes()).hexdigest(),
        "network_manifest": manifest,
        "search_radius_m": args.max_distance_m,
        "search_radius_note": (
            "Review aid only; proximity is not evidence of a device-level match."
        ),
        "way_distance_note": (
            "Way distances use endpoint midpoints and are approximate review aids."
        ),
        "attribution": [
            "City of Edmonton Open Data",
            "© OpenStreetMap contributors, ODbL",
        ],
        "entries": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(entries)} unverified elevator review records in {args.out}")


if __name__ == "__main__":
    main()
