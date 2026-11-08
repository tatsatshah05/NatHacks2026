"""Write the API's OpenAPI schema to a file without starting a server (`make types`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud.app.main import app


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: export_openapi.py OUTPUT.json", file=sys.stderr)
        return 2
    Path(sys.argv[1]).write_text(
        json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
