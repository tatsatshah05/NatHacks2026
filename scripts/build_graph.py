#!/usr/bin/env python
"""Build the prebuilt walking network (OP-104). Run on a laptop, never on Render.

What it does
------------
1. Downloads the OpenStreetMap walking network for a place with OSMnx
   (sidewalks, paths, trails, stairs, crossings, elevators).
2. Simplifies the graph while keeping elevator nodes, then merges the two
   directions of each path into one undirected edge.
3. Converts the graph into the compact arrays in ``cloud/app/network.py`` and
   saves ``cloud/data/network.npz``.
4. Loads the saved file in a fresh process to measure load time and peak memory,
   so the numbers in docs/DATA_SOURCES.md are measured, not guessed.

Usage
-----
    pip install -r scripts/requirements-graph.txt -r cloud/requirements-network.txt
    python scripts/build_graph.py                       # Edmonton, default output
    python scripts/build_graph.py --place "Strathcona, Edmonton, Alberta, Canada"
    python scripts/build_graph.py --cache-dir ~/.cache/openpath/osmnx

Downloads are cached in ``--cache-dir``, so a rebuild after the first run takes
about a minute instead of several. Commit the output file after rebuilding.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(
    0, str(REPO)
)  # so ``cloud.app.network`` imports from any working directory

from cloud.app.network import (
    DEFAULT_NETWORK_PATH,
    RawEdge,
    RawNode,
    export_network,
    save_network,
)

# Extra OSM way tags to keep beyond OSMnx's defaults. ``footway`` tells sidewalks
# and crossings apart; ``crossing`` describes the crossing type.
EXTRA_WAY_TAGS = ["footway", "crossing"]


def download(place: str, cache_dir: Path):
    """Download and simplify the walking network, returning an undirected OSMnx graph."""
    import osmnx as ox  # imported here so the API never needs OSMnx installed

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir)
    ox.settings.log_console = False
    ox.settings.useful_tags_way = list(
        dict.fromkeys([*ox.settings.useful_tags_way, *EXTRA_WAY_TAGS])
    )
    print(
        f"Downloading the walking network for {place!r} (cache: {cache_dir})",
        flush=True,
    )
    # Download unsimplified, then simplify ourselves so elevator nodes survive.
    # Simplification normally removes every node that just sits in the middle of
    # a path, and an elevator node is exactly that. Marking those nodes first and
    # naming the marker in node_attrs_include keeps them as endpoints.
    graph = ox.graph_from_place(place, network_type="walk", simplify=False)
    for _node, data in graph.nodes(data=True):
        if data.get("highway") == "elevator":
            data["elevator"] = True
    graph = ox.simplification.simplify_graph(graph, node_attrs_include=["elevator"])
    print(
        f"Downloaded {graph.number_of_nodes()} nodes and {graph.number_of_edges()} "
        "directed edges",
        flush=True,
    )
    # Walking is two-way, so OSMnx gives every path twice (once per direction).
    # Merge the pairs so each physical path is stored once.
    return ox.convert.to_undirected(graph)


def to_raw(graph) -> tuple[list[RawNode], list[RawEdge]]:
    """Turn the OSMnx graph into plain records that cloud/app/network.py understands."""
    nodes = [
        RawNode(
            osmid=int(osmid),
            lat=float(data["y"]),
            lng=float(data["x"]),
            highway=data.get("highway"),
        )
        for osmid, data in graph.nodes(data=True)
    ]
    edges = []
    for u, v, data in graph.edges(data=True):
        geometry = data.get("geometry")  # a shapely LineString when the edge has bends
        edges.append(
            RawEdge(
                u=int(u),
                v=int(v),
                length_m=float(data["length"]),
                highway=data.get("highway"),
                name=data.get("name"),
                footway=data.get("footway"),
                osmid=data.get("osmid"),
                geometry=[(float(x), float(y)) for x, y in geometry.coords]
                if geometry is not None
                else None,
            )
        )
    return nodes, edges


def measure_in_fresh_process(path: Path) -> dict[str, float]:
    """Load the file in a new Python process and report load time and peak memory.

    A fresh process gives the number Render will see: this process already holds
    the whole OSMnx graph, so measuring here would be meaningless.
    """
    code = (
        "import json, resource, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "from cloud.app.network import measure_load\n"
        f"result = measure_load({str(path)!r})\n"
        "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        # macOS reports bytes, Linux kilobytes.
        "result['peak_rss_mb'] = round(peak / (1e6 if sys.platform == 'darwin' else 1e3), 1)\n"
        "print(json.dumps(result))\n"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    )
    return json.loads(output.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--place", default="Edmonton, Alberta, Canada")
    parser.add_argument("--out", type=Path, default=DEFAULT_NETWORK_PATH)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path.home() / ".cache" / "openpath" / "osmnx"
    )
    args = parser.parse_args()

    started = time.perf_counter()
    graph = download(args.place, args.cache_dir)
    nodes, edges = to_raw(graph)
    data = export_network(
        nodes,
        edges,
        source=f"OpenStreetMap via OSMnx, place={args.place!r}",
        built_at=datetime.now(UTC),
    )
    save_network(data, args.out)
    build_seconds = time.perf_counter() - started
    build_peak_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1e9 if sys.platform == "darwin" else 1e6
    )

    measured = measure_in_fresh_process(args.out)
    manifest = data.manifest
    print()
    print(f"Saved {args.out} ({measured['file_mb']} MB)")
    print(json.dumps(manifest, indent=2))
    print()
    print("Paste into docs/DATA_SOURCES.md:")
    print("| Measurement | Value |")
    print("| --- | --- |")
    print(f"| Nodes | {manifest['node_count']:,} |")
    print(f"| Edges (undirected) | {manifest['edge_count']:,} |")
    print(
        f"| Stair edges / elevator edges / elevator nodes | {manifest['steps_edges']:,} / {manifest['elevator_edges']:,} / {manifest['elevator_nodes']:,} |"
    )
    print(
        f"| Crossing edges / sidewalk edges | {manifest['crossing_edges']:,} / {manifest['sidewalk_edges']:,} |"
    )
    print(f"| File size | {measured['file_mb']} MB |")
    print(f"| Load time (fresh process) | {measured['load_seconds']} s |")
    print(
        f"| Memory: arrays / process peak after load | {measured['arrays_mb']} MB / {measured['peak_rss_mb']} MB |"
    )
    print(
        f"| Build time / build peak memory (laptop) | {build_seconds:.0f} s / {build_peak_gb:.1f} GB |"
    )


if __name__ == "__main__":
    main()
