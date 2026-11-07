# Estella's Phase 1: OP-109 weather watch

The weather feature for [OP-109 (#37)](https://github.com/tatsatshah05/NatHacks2026/issues/37)
is implemented on `op-109-weather-watch`. It runs independently while the shared
OP-101/OP-102 app is being built. Team integration and review remain before the
issue can be closed.

## Try it

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r cloud/requirements-weather.txt
DATA_MODE=replay .venv/bin/python -m uvicorn cloud.app.weather_app:create_app --factory --port 8001
```

Open http://127.0.0.1:8001/docs and try `GET /api/weather`, or open
http://127.0.0.1:8001/api/weather directly. Replay uses the recorded weather
responses in `cloud/tests/fixtures/weather/`; the response clearly says
`"mode": "replay"` and keeps the original observation time. It is a frozen
recording, not a simulation of today's weather. No API keys are required.

For current weather, stop the server with Ctrl+C and run:

```bash
DATA_MODE=live .venv/bin/python -m uvicorn cloud.app.weather_app:create_app --factory --port 8001
```

The first live fetch runs in the background. Until it succeeds, the endpoint
returns HTTP 503 with `Retry-After: 60`; retry after it finishes. Later requests
read the cache and do not trigger new network calls. Refresh runs every 600
seconds. Optional `WEATHER_LAT` and `WEATHER_LNG` control the Open-Meteo grid
location; station observations remain fixed to Blatchford for this Edmonton task.
These settings must be applied before starting the process.

## What you built

| File | Purpose |
| --- | --- |
| `cloud/app/weather.py` | Ice-risk rule from PLAN.md section 7.4 |
| `cloud/app/adapters/weather.py` | Official API requests, retries, units and history validation |
| `cloud/app/weather_service.py` | Last-good cache, refresh loop and reusable API router |
| `cloud/app/weather_app.py` | Small standalone FastAPI app for local testing |
| `cloud/tests/test_weather.py` | Rule branches and freezing/dew-point boundaries |
| `cloud/tests/test_weather_integration.py` | Recorded sources, errors, retries, API and lifecycle tests |

A watch needs freezing air **or** ground, plus at least one of: precipitation in
6 hours, above-zero air in 12 hours, or air within 1 C of the dew point. This is a
city-wide **ice-risk advisory**; it never claims to detect ice or changes routes.
Camera-specific risk is Kaleb's OP-205 in Phase 2.

`GET /api/weather` returns the planned fields: `active`, `reason`, `air_c`,
`ground_c`, `dew_c`, `observed_at`, `mode`. `X-Weather-Stale: true` indicates a
failed refresh or three missed refresh intervals. Failures preserve the entire
last-good response, including its observation time. The cache is in memory;
a restart starts with no reading. HTTP 503 means unavailable, not safe weather.

## Test your work

```bash
.venv/bin/python -m pytest cloud/tests -q
.venv/bin/ruff check --isolated --target-version py39 --select E4,E7,E9,F,I cloud
.venv/bin/ruff format --isolated --target-version py39 --check cloud
```

The project targets Python 3.12; the module also supports the system's Python
3.9. Verified: 24 tests pass on both Python 3.9 and Python 3.12; lint and
formatting checks pass. A live refresh against both official sources also
succeeded on November 7, 2026. All automated tests are offline. `make test` cannot run until OP-102 adds
the root Makefile; run it after integration into the shared app.

## Handoff to Tatsat and Matthew

- Include `weather_service.router` in the shared FastAPI app and assign a
  `WeatherService` instance to `app.state.weather_service` during its lifespan.
- Use `WeatherSources` with the app's async HTTP client. The weather response
  matches the already planned `/api/weather` fields; no shared schemas/config
  have been invented or changed on this branch.
- Either start `service.run()` in the app lifespan (and cancel/await it at shutdown),
  or schedule `service.refresh()` every 600 seconds through OP-103. Use only one
  scheduler. `last_success`, `last_error`, and `stale` are available for its status
  reporting. The standalone server is a local harness, not a replacement for OP-102.
- Merge the isolated dependency file into OP-102's shared requirements and add
  the adapter to shared source registration once that interface is available.
- The web client should show `mode=replay` visibly, and indicate stale data.
- Follow the review workflow: PR title `OP-109: Weather and black-ice watch`,
  body `Closes #37`, one teammate approval and passing CI before merge.

## Next stories

After OP-109 integration, work on OP-110 (#38), the required 311 snow-complaint
and trail-closure adapters. OP-111 (#39), snow-clearing advisories, is a Should
and comes afterward. Keep those stories on separate branches.
