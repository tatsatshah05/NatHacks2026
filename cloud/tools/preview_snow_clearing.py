"""Purpose: preview snow-clearing cautions using recordings or live data.

Inactive records produce no warnings. Unusable rows are skipped and logged;
only verified recent service can create a road-line caution for OP-206 matching.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from cloud.app.adapters.snow_clearing import SnowClearingAdapter


def preview(live: bool) -> dict:
    now = datetime.now(UTC)
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
        "integration_needed": "Verified service-time evidence; OP-206 matches road lines to crossings",
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    try:
        result = preview(args.live)
    except (httpx.HTTPError, ValueError, ModuleNotFoundError) as exc:
        print("Snow-clearing preview unavailable: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))
