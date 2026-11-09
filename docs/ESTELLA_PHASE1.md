# Estella's Phase 1: OP-109 weather watch

**Purpose:** combine Blatchford observations and modelled hourly weather to
provide a city-wide ice-risk advisory. The feature is on `op-109-weather-watch`,
rebased onto `phase_1`, for [OP-109 (#37)](https://github.com/tatsatshah05/NatHacks2026/issues/37).
The standalone app lets us check it while the shared application is being built.

## Run locally (Python 3.12)

From the repository root:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r cloud/requirements-weather.txt
DATA_MODE=live .venv/bin/python -m uvicorn cloud.app.weather_app:create_app --factory --port 8001
```

Open http://127.0.0.1:8001/api/weather or the interactive docs at
http://127.0.0.1:8001/docs. Live access requires no API keys. Optionally set
`WEATHER_LAT` and `WEATHER_LNG` for the Open-Meteo grid request. The observed air
and dew point always come from Blatchford for this Edmonton feature.

The service starts a background refresh immediately, then waits 600 seconds
before refreshing again. HTTP requests read its cache and do not fetch providers.
The cache is in memory, so a restart starts with unknown weather.

## Weather response and freshness

The endpoint always returns HTTP 200 and a validated Pydantic `WeatherResponse`:

- `active`: true or false after a successful fetch; null before the first success.
- `reason`: a readable explanation, including the initial unavailable state.
- `air_c`, `ground_c`, `dew_c`: Celsius readings, initially null.
- `observed_at`: the original UTC observation time, initially null.
- `mode`: `live` or `replay`.

A failed refresh retains the full last-good result and its observation time.
There is no 503, `Retry-After`, or `X-Weather-Stale` weather contract. The service's
`last_success`, `last_error`, and `stale` properties are for OP-103 to expose on
`/api/status`. These status properties are not extra weather response fields.

The rule requires freezing air or ground plus precipitation in six hours,
above-zero air in twelve hours, or air within 1 C of the dew point. The watch is
a city-wide advisory and never changes routing costs. Local camera-based risk
is Kaleb's OP-205.

## Replay using REPLAY_SNAPSHOT

Production code reads only the configured snapshot path. Deployments can omit
`cloud/tests` entirely. The OP-305 recorder can add a `weather` section to its
shared snapshot with this shape:

```json
{
  "weather": {
    "eccc": {"features": ["raw Environment Canada features"]},
    "open_meteo": {"hourly": "raw Open-Meteo response, including metadata"}
  }
}
```

The structure above illustrates where the complete original API responses go;
it is not a runnable data fixture. Other source sections may coexist in the file.
The loader validates both responses and freezes its clock to the latest recorded
observation, keeping the original times and returning `mode=replay`.

To create a local demo snapshot from the actual recorded test responses:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
fixtures = Path('cloud/tests/fixtures/weather')
snapshot = {'weather': {
    'eccc': json.loads((fixtures / 'eccc.json').read_text()),
    'open_meteo': json.loads((fixtures / 'open_meteo.json').read_text()),
}}
Path('/tmp/openpath-weather-snapshot.json').write_text(json.dumps(snapshot))
PY
DATA_MODE=replay REPLAY_SNAPSHOT=/tmp/openpath-weather-snapshot.json .venv/bin/python -m uvicorn cloud.app.weather_app:create_app --factory --port 8001
```

Only this optional development preparation step reads test fixtures. At runtime,
the app uses `REPLAY_SNAPSHOT`. Starting replay without a valid path is a startup
configuration error. A web client must visibly label replayed data.

## Files and flow

| File | Purpose |
| --- | --- |
| `cloud/app/adapters/weather.py` | Fetch and validate two provider responses concurrently |
| `cloud/app/weather.py` | Calculate the ice-risk rule |
| `cloud/app/weather_service.py` | Pydantic response, cache, state-change logging, refresh loop and route |
| `cloud/app/weather_app.py` | Standalone startup/shutdown and configured snapshot loading |
| `cloud/tests/test_weather.py` | Rule branches and boundaries |
| `cloud/tests/test_weather_integration.py` | Parsing, failures, retries, endpoint, replay deployment and lifecycle |

Timeouts and 5xx responses are retried three times after 1, 2 and 4 seconds.
Other failures are reported immediately. Each HTTP request has a 10-second
limit. If Environment Canada fails, unfinished Open-Meteo work is cancelled and
awaited and the refresh fails. Open-Meteo is optional since the fix of
November 8, 2026 (`cloud/tests/test_weather_open_meteo_429.py`): its failure
never fails the refresh; a 429 starts a backoff (`Retry-After`, else 10 minutes
doubling to 60) and the watch runs on the station alone, with the history from
the last good Open-Meteo data under 24 hours old or from the station's own
12-hour log, and the ground temperature unknown. The `reason` then says what
the result rests on, and the service's `warning` carries the operator's message.
ECCC observations tolerate five seconds of clock skew. Both query bounds and
validation use that tolerance; old observations are still rejected after two hours.

The service logs only watch-state changes at INFO level, including the initial
unknown-to-known state, for example `CHANGE weather watch inactive->active ...`.
Repeated identical states do not produce duplicate CHANGE lines. Fetch failures
have separate warning logs and do not fabricate a state transition.

## Test and lint (same Ruff version/commands as CI)

Runtime requirements contain only runtime packages. Install test tools separately:

```bash
.venv/bin/python -m pip install pytest ruff==0.17.4
.venv/bin/python -m pytest cloud/tests -q
.venv/bin/ruff check --fix .
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Use the root commands without selecting or ignoring rules. Automated source
checks use recorded responses or fake HTTP, never real provider calls. The
replay deployment test copies only production modules and a snapshot into an
isolated directory and verifies the endpoint works without a tests directory.

The rebased branch also includes OP-104's network tests. Install its runtime
requirements (`cloud/requirements-network.txt`) before running the whole suite.
Run the shared `make test` after OP-102 adds the Makefile.

## Integration and review

Include `weather_service.router` in the shared FastAPI app and set
`app.state.weather_service` during its lifespan. Use either `service.run()` or
OP-103's scheduler for `service.refresh()`, with only one scheduler active. Cancel
and await background work before closing the shared HTTP client. Consolidate
runtime dependencies and connect freshness to `/api/status` when those stories
are merged. Shared schemas and configuration remain owned by their stories.

`CLAUDE.md` stays as the team's short import of `AGENTS.md`; all OP-109 notes live
here, with a command pointer in `AGENTS.md`. Source attribution and recorded data
provenance are in `docs/DATA_SOURCES.md`. PR #85 remains targeted to `phase_1` and
closes issue #37 on merge after integration and approval.
