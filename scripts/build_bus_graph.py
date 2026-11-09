"""Build ``cloud/data/bus.json``, the ETS bus lines for walk and bus trips (OP-417).

    python scripts/build_bus_graph.py                  # one official download, then write
    python scripts/build_bus_graph.py --gtfs gtfs.zip  # from a zip already downloaded

The same ETS GTFS zip as ``build_lrt_graph.py`` (City of Edmonton open data, its
``GTFS_URL``), read by ``cloud.app.lrt_gtfs.read_bus``: one line per stop pattern
(the trips of a route and direction that call at the same stops in the same order),
with the median scheduled ride time between consecutive stops, the median headway
per hour for a weekday, a Saturday and a Sunday from the departures at the first
stop, and only the stops a line serves. Runs on a laptop, never on Render; the JSON
is committed, the zip never is.

By default only Edmonton Transit Service routes (``agency_id`` 1) are kept: the
feed also carries the regional agencies, whose fleets and terms are their own.
GTFS values (route short names, headsigns, stop names) are written verbatim; the
planner derives display forms.

The file is compact JSON, one stop and one line per text line, so it stays small
and diffs by row. Every field with a unit says so in its name (``ride_s``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so ``cloud.app.lrt_gtfs`` imports from anywhere

from cloud.app.lrt_gtfs import BUS_FORMAT_VERSION, ETS_AGENCY_IDS, BusGraph, read_bus

DEFAULT_OUT = ROOT / "cloud" / "data" / "bus.json"
SIZE_TARGET_BYTES = 3_000_000  # a target, from the story; the build warns above it


def lrt_build() -> ModuleType:
    """``scripts/build_lrt_graph.py`` (scripts/ is not a package): the shared URL,
    licence, attribution and download."""
    spec = importlib.util.spec_from_file_location(
        "openpath_build_lrt", Path(__file__).with_name("build_lrt_graph.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def document(graph: BusGraph, *, source: dict[str, Any]) -> dict[str, Any]:
    """The JSON document ``cloud/app/lrt.py`` loads next to ``lrt.json``."""
    return {
        "format_version": BUS_FORMAT_VERSION,
        "source": source,
        "timezone": "America/Edmonton",
        "stops": [
            {
                "id": stop.id,
                "code": stop.code,
                "name": stop.name,
                "lat": round(stop.lat, 6),
                "lng": round(stop.lng, 6),
            }
            for stop in graph.stops
        ],
        "lines": [
            {
                "id": line.id,
                "route_id": line.route_id,
                "short_name": line.short_name,
                "name": line.name,
                "direction": line.direction,
                "headsign": line.headsign,
                "color": line.color,
                "stops": line.stops,
                "ride_s": line.ride_s,
                "headway_s": line.headway_s,
                "trips": line.trips,
            }
            for line in graph.lines
        ],
    }


def dumps(doc: dict[str, Any]) -> str:
    """Compact JSON with one stop and one line per text line."""

    def row(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"))

    head = {k: v for k, v in doc.items() if k not in ("stops", "lines")}
    parts = [row(head)[:-1]]  # the opening brace and the scalar fields
    parts.append(',"stops":[\n' + ",\n".join(row(s) for s in doc["stops"]) + "\n]")
    parts.append(',"lines":[\n' + ",\n".join(row(s) for s in doc["lines"]) + "\n]}\n")
    text = "".join(parts)
    assert json.loads(text) == doc
    return text


def main() -> None:
    shared = lrt_build()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gtfs", type=Path, help="a GTFS zip already downloaded")
    parser.add_argument("--url", default=shared.GTFS_URL, help="GTFS download URL")
    parser.add_argument(
        "--agency",
        action="append",
        help="GTFS agency_id to keep (default: 1, Edmonton Transit Service); "
        "'all' keeps every agency",
    )
    parser.add_argument(
        "--downloaded-at",
        help="when --gtfs was downloaded (ISO 8601, UTC); default: its file time",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    agencies = (
        None
        if args.agency and "all" in args.agency
        else frozenset(args.agency)
        if args.agency
        else ETS_AGENCY_IDS
    )
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="openpath-gtfs-") as tmp:
        if args.gtfs:
            zip_path = args.gtfs
            downloaded_at = args.downloaded_at or (
                datetime.fromtimestamp(zip_path.stat().st_mtime, UTC).isoformat(
                    timespec="seconds"
                )
            )
        else:
            zip_path = shared.download(args.url, Path(tmp))
            downloaded_at = datetime.now(UTC).isoformat(timespec="seconds")
        graph = read_bus(zip_path, agencies)
    feed = graph.feed
    source = {
        "name": "ETS GTFS schedule",
        "url": args.url,
        "feed_publisher": feed.get("feed_publisher_name"),
        "feed_version": feed.get("feed_version"),
        "feed_start_date": feed.get("feed_start_date"),
        "feed_end_date": feed.get("feed_end_date"),
        "agencies": graph.agencies,
        "downloaded_at": downloaded_at,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "licence_url": shared.LICENCE_URL,
        "attribution": shared.ATTRIBUTION,
    }
    doc = document(graph, source=source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(doc), encoding="utf-8")
    elapsed = time.perf_counter() - started

    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size / 1e6:.2f} MB) in {elapsed:.1f} s")
    print(
        f"feed {source['feed_version']} valid {source['feed_start_date']} to "
        f"{source['feed_end_date']}, agencies {graph.agencies}"
    )
    routes = {line.route_id for line in graph.lines}
    loops = sum(len(set(line.stops)) != len(line.stops) for line in graph.lines)
    single = sum(line.trips == 1 for line in graph.lines)
    print(
        f"{graph.routes} bus routes kept, {len(routes)} with trips, "
        f"{len(graph.lines)} lines (stop patterns), {len(graph.stops)} stops, "
        f"{sum(line.trips for line in graph.lines)} trips"
    )
    print(
        f"  lines per route: median {statistics.median(sum(1 for l in graph.lines if l.route_id == r) for r in routes):g}, "
        f"max {max(sum(1 for l in graph.lines if l.route_id == r) for r in routes)}; "
        f"stops per line: median {statistics.median(len(l.stops) for l in graph.lines):g}, "
        f"max {max(len(l.stops) for l in graph.lines)}; "
        f"{loops} loop lines repeat a stop; {single} lines have one trip"
    )
    if size > SIZE_TARGET_BYTES:
        print(
            f"WARN {size / 1e6:.2f} MB is over the {SIZE_TARGET_BYTES / 1e6:g} MB target",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
