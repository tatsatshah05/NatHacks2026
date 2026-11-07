"""Purpose: preview the verified portion of OP-111 using recordings or live data.

The actual recorded feed is inactive (NA). A completed active segment raises a
clear error until verified service-time evidence and crossing mappings are wired.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from cloud.app.adapters.snow_clearing import SnowClearingAdapter


def preview(live: bool) -> dict:
    now = datetime.now(timezone.utc)
    with httpx.Client() as client:
        adapter = SnowClearingAdapter(client, app_token=os.getenv("SOCRATA_APP_TOKEN"))
        if live:
            items = adapter.fetch(now)
        else:
            path = Path(__file__).parents[1] / "tests/fixtures/snow_clearing/na.json"
            items = adapter.normalize(json.loads(path.read_text()), now)
    return {
        "mode": "live" if live else "replay",
        "count": len(items),
        "items": [item.model_dump(mode="json") for item in items],
        "integration_needed": "Verified service times and walking-crossing mappings",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    try:
        result = preview(args.live)
    except (httpx.HTTPError, ValueError, ModuleNotFoundError) as exc:
        print("Snow-clearing preview unavailable: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))
