"""Build ``cloud/data/pedestrian_counts.json`` from City dataset ``sw7k-ptx8`` (OP-310).

    python scripts/build_pedestrian_counts.py             # live: one official City API read, then commit
    python scripts/build_pedestrian_counts.py --fixtures  # offline, from cloud/tests/fixtures
    python scripts/build_pedestrian_counts.py --record cloud/tests/fixtures \\
        --record-location "High Level Bridge West" --record-location "Railtown Park SUP"

A build step on a laptop, like ``scripts/build_search_index.py``: the API loads the
committed file at startup (``cloud/app/pedestrian_counts.py``) and never calls the
City at request time.

The read
    ``GET https://data.edmonton.ca/resource/sw7k-ptx8.json`` for the last
    ``--lookback-days`` (default 120) days of rows, in pages of ``--page-size`` rows
    ordered by ``:id``. ``SOCRATA_APP_TOKEN`` is sent as ``X-App-Token`` when set.
    The typical day per counter is the median over the 28 days ending on the
    dataset's latest day (see ``cloud/app/pedestrian_counts.py``).

``--fixtures [DIR]`` reads ``DIR/pedestrian_counts/rows.json`` and writes to a
temporary file unless ``--out`` is given, so it never overwrites the committed file.
``--record DIR`` also writes the last 35 days of rows of the named
``--record-location`` counters to ``DIR/pedestrian_counts/rows.json`` with a
manifest: a small fixture for tests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloud.app.pedestrian_counts import DATASET, DEFAULT_PATH, aggregate

CITY_API = "https://data.edmonton.ca/resource"
DEFAULT_FIXTURES = ROOT / "cloud" / "tests" / "fixtures"
FIXTURE_FOLDER = "pedestrian_counts"
FIELDS = (
    "counter_location_description,latitude,longitude,log_timstamp,"
    "total_pedestrian_count"
)
PAGE_SIZE = 5_000
LOOKBACK_DAYS = 120
RECORD_DAYS = 35  # a fixture keeps the window plus a week before it
TIMEOUT_S = 60.0
RETRIES = 3


def query(since: datetime) -> dict[str, str]:
    return {
        "$select": FIELDS,
        "$where": f"log_timstamp >= '{since:%Y-%m-%d}T00:00:00'",
        "$order": ":id",
    }


def fetch_pages(
    client: httpx.Client, params: Mapping[str, str], page_size: int
) -> Iterator[list[dict[str, Any]]]:
    """Every row of the query, a page at a time, retrying a failed page."""
    offset = 0
    while True:
        page_params = {**params, "$limit": str(page_size), "$offset": str(offset)}
        for attempt in range(1, RETRIES + 1):
            try:
                response = client.get(f"{CITY_API}/{DATASET}.json", params=page_params)
                response.raise_for_status()
                page = response.json()
                break
            except (httpx.HTTPError, ValueError) as error:
                if attempt == RETRIES:
                    raise
                print(f"  offset {offset}: {error}; retrying", file=sys.stderr)
                time.sleep(2**attempt)
        if not isinstance(page, list):
            raise TypeError(f"{DATASET} returned {type(page).__name__}, not a list")
        yield page
        if len(page) < page_size:
            return
        offset += page_size


def read_live(
    token: str | None, page_size: int, lookback_days: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = datetime.now(UTC)
    params = query(started - timedelta(days=lookback_days))
    headers = {"X-App-Token": token} if token else {}
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=TIMEOUT_S, headers=headers) as client:
        for page in fetch_pages(client, params, page_size):
            rows.extend(row for row in page if isinstance(row, dict))
            print(f"  {DATASET}: {len(rows)} rows")
    provenance = {
        "mode": "live",
        "read_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query": f"{CITY_API}/{DATASET}.json {json.dumps(params)}",
        "rows_read": len(rows),
    }
    return rows, provenance


def read_fixtures(snapshot: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    folder = snapshot / FIXTURE_FOLDER
    rows = json.loads((folder / "rows.json").read_text(encoding="utf-8"))
    manifest_path = folder / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    provenance = {
        "mode": "fixtures",
        "read_at": manifest.get("recorded_at"),
        "query": manifest.get("query"),
        "rows_read": len(rows),
    }
    return [row for row in rows if isinstance(row, dict)], provenance


def record(
    rows: Sequence[Mapping[str, Any]],
    provenance: Mapping[str, Any],
    snapshot: Path,
    locations: Sequence[str],
    days: int = RECORD_DAYS,
) -> Path:
    """Write the named counters' rows of the last ``days`` days as a fixture."""
    wanted = {" ".join(name.split()) for name in locations}
    latest = max(str(row.get("log_timstamp", ""))[:10] for row in rows)
    since = (datetime.fromisoformat(latest) - timedelta(days=days - 1)).date()
    kept = [
        dict(row)
        for row in rows
        if " ".join(str(row.get("counter_location_description", "")).split()) in wanted
        and str(row.get("log_timstamp", ""))[:10] >= since.isoformat()
    ]
    missing = wanted - {
        " ".join(str(row["counter_location_description"]).split()) for row in kept
    }
    if missing:
        raise SystemExit(f"--record-location not in the data: {sorted(missing)}")
    kept.sort(
        key=lambda row: (row["counter_location_description"], row["log_timstamp"])
    )
    folder = snapshot / FIXTURE_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    # One row per line keeps the fixture small and its diffs readable.
    lines = ",\n".join(json.dumps(row, ensure_ascii=False) for row in kept)
    (folder / "rows.json").write_text(f"[\n{lines}\n]\n", encoding="utf-8")
    manifest = {
        "source": f"{CITY_API}/{DATASET}.json",
        "recorded_at": provenance.get("read_at"),
        "query": provenance.get("query"),
        "content": "The official rows of the named counters only; no token.",
        "locations": sorted(wanted),
        "days": f"{since.isoformat()} to {latest}",
        "rows": len(kept),
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return folder


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--fixtures",
        nargs="?",
        type=Path,
        const=DEFAULT_FIXTURES,
        help="build offline from recorded rows (default folder: cloud/tests/fixtures)",
    )
    parser.add_argument("--out", type=Path, help="where to write the counts")
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument(
        "--record", type=Path, help="also write a fixture of --record-location rows"
    )
    parser.add_argument("--record-location", action="append", default=[])
    args = parser.parse_args(argv)

    if args.fixtures is not None:
        if args.record:
            parser.error("--record needs a live read, so it cannot use --fixtures")
        out = (
            args.out or Path(tempfile.gettempdir()) / "openpath-pedestrian-counts.json"
        )
        rows, provenance = read_fixtures(args.fixtures)
    else:
        out = args.out or DEFAULT_PATH
        print(f"Reading {DATASET} from the City open data API")
        rows, provenance = read_live(
            os.environ.get("SOCRATA_APP_TOKEN") or None,
            args.page_size,
            args.lookback_days,
        )
        if args.record:
            if not args.record_location:
                parser.error("--record needs at least one --record-location")
            folder = record(rows, provenance, args.record, args.record_location)
            print(f"Recorded {folder}")

    document = aggregate(rows)
    document["read"] = provenance
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    window = document["window"]
    print(
        f"Wrote {out}: {len(document['counters'])} counters with pedestrians, "
        f"{len(document['excluded'])} left out, window {window['start']} to "
        f"{window['end']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
