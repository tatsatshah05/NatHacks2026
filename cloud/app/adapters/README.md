# OP-105: LRT elevator and escalator outages

This branch starts Kaleb's Phase 1 work on [issue #33](https://github.com/tatsatshah05/NatHacks2026/issues/33).
It implements and tests the City feed reader and conversion. The repository has
no application skeleton yet, so it is **not integrated into a running API**.

## Try the recorded data

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r cloud/tests/requirements-lrt.txt
.\.venv\Scripts\python.exe -m cloud.app.adapters.lrt_devices --fixtures cloud/tests/fixtures/lrt --source-timezone America/Edmonton
.\.venv\Scripts\python.exe -m pytest cloud/tests/test_lrt_devices.py -q
.\.venv\Scripts\ruff.exe check cloud/app/adapters/lrt_devices.py cloud/tests/test_lrt_devices.py
```

The preview explicitly says `mode: replay`. Fixtures are a recorded snapshot,
not current outages. Tests block network access and mutate copies of recorded
records to exercise elevator outages and failure cases absent in that snapshot.

For a one-shot live preview, pass `--live` instead of `--fixtures ...`. Set
`SOCRATA_APP_TOKEN` in your shell for authenticated Socrata access; never commit
its value. Anonymous public reads can be throttled. The CLI does not start a
background scheduler or write to a database.

## What the code does

- Reads [outages](https://data.edmonton.ca/d/snws-u3zx) at most once every two
  minutes when `LrtDevicesAdapter.fetch()` is called regularly. The caller must
  arrange those calls; this helper does not run autonomously.
- Refreshes the [device master list](https://data.edmonton.ca/d/javh-pjj2) and
  [station coordinates](https://data.edmonton.ca/d/fhxi-cnhe) daily. The master
  list supplies metadata only: its potentially stale status cannot create an
  outage or override the outage feed.
- Returns the obstruction fields documented in PLAN.md section 10, including
  device ID, station/device location, outage start, source observation time,
  attribution and station geometry. Elevator outages are `blocked`; escalator
  outages are `caution`, per the profile rules.
- Chooses a stable point from the City's station table using normalized station
  names, preferring a `Station` over a surface `Stop`. The verified name alias
  `Government Centre` / `Government Station` is explicit. These are station-level
  points, **not elevator entrance or OpenStreetMap matches**.
- Uses a ten-second timeout and three retries with backoff for network errors,
  HTTP 429 and HTTP 5xx. Paginated reads request a deterministic ordering.
- Raises `FeedError` on failed fetches or unsafe records. The previous successful
  snapshot remains available as `last_good`; the caller must not replace its
  store with an empty list after a failure. Only a successful empty outage feed
  clears outages. This in-memory cache is not persisted across restarts.
- Keeps source timestamps when computing expiry; fetching old observations
  again does not make them fresh. The provisional ten-minute lifetime follows
  the example in PLAN.md and must be confirmed during scheduler integration.

## Integration still needed

1. **OP-101 (#29), Tatsat:** the shared `Obstruction`, `SourceAdapter` and store
   have not landed. `normalize_outages()` returns ordinary dictionaries following
   PLAN.md. Adapt these to the agreed types once available; no shared schema,
   API or `config/` file is created or changed by this work.
2. **OP-103 (#31), Matthew:** register the source with the shared scheduler and
   status reporting. Decide whether the helper's cadence/retry/cache logic is
   retained or delegated to that scheduler, to avoid duplicate retry loops.
   Surface `last_error` and observation freshness on `/api/status`.
3. **Timezone confirmation:** City `since_time_stamp` / `polled_time_stamp` are
   floating datetimes and their metadata does not identify a timezone. The
   adapter deliberately requires `source_timezone`; it does not assume UTC.
   `America/Edmonton` in the examples is an **unverified interpretation**, printed
   as `source_timezone_assumption` by the CLI. Confirm it with the source owner
   before live integration. Explicit offsets are honored; ambiguous DST times
   fail rather than inventing an offset. Windows may need the `tzdata` package.
4. **Review and validation:** run the full `make test` and preview deploy once
   the skeleton exists, then get a teammate's review. No root Makefile, web app,
   deployment or shared contract currently exists on this branch.

## Kaleb's next Phase 1 stories

- **OP-107 (#35):** build the construction closure adapter, using active permits
  with `pedestrian_affected = Yes`, with recorded fixtures. It also depends on
  the shared OP-101 types. Use a separate story branch.
- **OP-106 (#34):** map each elevator to the walking network after OP-104 (#32)
  and OP-105. Each real match requires a second teammate's review. Do not infer
  elevator-to-OSM matches from these coarse station coordinates.
