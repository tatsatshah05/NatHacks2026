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
from typing import Any

import numpy as np


def distinct_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Show each OSM object once, keeping its closest network segment."""
    unique = {}
    for candidate in candidates:
        key = (candidate["type"], candidate["id"])
        if key not in unique or candidate["distance_m"] < unique[key]["distance_m"]:
            unique[key] = candidate
    return sorted(unique.values(), key=lambda candidate: candidate["distance_m"])


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
    from cloud.app.network import EDGE_ELEVATOR, NODE_ELEVATOR, WalkNetwork

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
    # Let the network owner validate its format and define the flag bits.
    network = WalkNetwork.load(args.network)
    manifest = network.manifest
    lat, lng = network.node_lat, network.node_lng
    node_ids = network.node_osmid
    elevator_nodes = np.flatnonzero(network.node_flags & NODE_ELEVATOR)
    elevator_ways = np.flatnonzero(network.edge_flags & EDGE_ELEVATOR)
    way_ids = network.edge_osmid[elevator_ways]
    u, v = network.edge_u[elevator_ways], network.edge_v[elevator_ways]
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
                            "anchor_node_id": osm_id,
                            "anchor_note": (
                                "Review the vertical edge pair; "
                                "this node alone must not close its junction."
                            ),
                        }
                    )
            way_distances = distances(way_lat, way_lng, points)
            for ordinal, (osm_id, distance) in enumerate(
                zip(way_ids, way_distances, strict=True)
            ):
                if int(osm_id) > 0 and distance <= args.max_distance_m:
                    endpoints = np.array([u[ordinal], v[ordinal]])
                    endpoint_distances = distances(
                        lat[endpoints],
                        lng[endpoints],
                        [(float(way_lat[ordinal]), float(way_lng[ordinal]))],
                    )
                    anchor = int(endpoints[int(np.argmin(endpoint_distances))])
                    candidates.append(
                        {
                            "type": "way",
                            "id": int(osm_id),
                            "distance_m": round(float(distance), 1),
                            "url": f"https://www.openstreetmap.org/way/{int(osm_id)}",
                            "anchor_node_id": int(node_ids[anchor]),
                            "anchor_note": (
                                "Nearest endpoint of the flagged vertical link; "
                                "independent review required."
                            ),
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
                "elevator_candidates": distinct_candidates(candidates),
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
