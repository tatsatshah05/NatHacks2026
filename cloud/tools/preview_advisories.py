"""Purpose: preview OP-110 advisories from the terminal.

Read recorded City responses by default, or fetch current data with --live.
Print the mode, counts, and advisory items as JSON for an easy manual check.
This runs once while the shared API/scheduler is being built.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from cloud.app.adapters.city_advisories import (
    SnowComplaintsAdapter,
    TrailClosuresAdapter,
    city_time,
)


async def preview(live: bool) -> dict:
    """Choose the data source, run both adapters, and label the preview output."""
    fixture_dir = Path(__file__).parents[1] / "tests/fixtures/city_advisories"
    if live:
        now = datetime.now(timezone.utc)
    else:
        recording = json.loads((fixture_dir / "recording.json").read_text())
        # Use the recording's time so replay results remain repeatable.
        now = city_time(recording["replay_at"])
    async with httpx.AsyncClient() as client:
        snow = SnowComplaintsAdapter(client, app_token=os.getenv("SOCRATA_APP_TOKEN"))
        trails = TrailClosuresAdapter(client, app_token=os.getenv("SOCRATA_APP_TOKEN"))
        if live:
            snow_items = await snow.fetch(now)
            trail_items = await trails.fetch(now)
        else:
            snow_items = snow.normalize(
                json.loads((fixture_dir / "snow.json").read_text()), now
            )
            trail_items = trails.normalize(
                json.loads((fixture_dir / "trails.json").read_text()), now
            )
    return {
        "mode": "live" if live else "replay",
        "as_of": now.isoformat(),
        "counts": {snow.source: len(snow_items), trails.source: len(trail_items)},
        "items": snow_items + trail_items,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch official APIs instead of recorded fixtures",
    )
    arguments = parser.parse_args()
    print(json.dumps(asyncio.run(preview(arguments.live)), indent=2))
