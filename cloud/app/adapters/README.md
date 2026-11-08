# OP-105: LRT elevator and escalator outages

This branch implements Kaleb's Phase 1 work on [issue #33](https://github.com/tatsatshah05/NatHacks2026/issues/33)
and addresses [Tatsat's review of PR #83](https://github.com/tatsatshah05/NatHacks2026/pull/83#pullrequestreview-5815445627).
It implements and tests the City feed reader and conversion. The repository has
no application skeleton yet, so it is **not integrated into a running API**.

## Try the recorded data

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r cloud/tests/requirements-lrt.txt
.\.venv\Scripts\python.exe -m cloud.app.adapters.lrt_devices --fixtures cloud/tests/fixtures/lrt --source-timezone America/Edmonton
.\.venv\Scripts\python.exe -m pytest cloud/tests/test_lrt_devices.py -q
.\.venv\Scripts\python.exe -m pytest cloud/tests/test_lrt_review_regressions.py -q
.\.venv\Scripts\ruff.exe check cloud/app/adapters/lrt_devices.py cloud/tests/test_lrt_devices.py
```

The preview explicitly says `mode: replay`. Fixtures are a recorded snapshot,
not current outages. Tests block network access and mutate copies of recorded
records to exercise elevator outages and failure cases absent in that snapshot.
Use `--at 2026-11-07T23:00:00Z` with recorded data to make its simulated fetch
time reproducible. `--at` is rejected in live mode. The JSON preview exposes
row warnings and `last_error` alongside the obstruction records.

For a one-shot live preview, pass `--live` instead of `--fixtures ...`. Set
`SOCRATA_APP_TOKEN` in your shell for authenticated Socrata access; never commit
its value. Anonymous public reads can be throttled. The CLI does not start a
background scheduler or write to a database.

## What the code does

- Reads [outages](https://data.edmonton.ca/d/snws-u3zx) on every scheduled call.
  `source` is `city_lrt_devices` and `cadence_s` is 120. OP-103 owns the two-minute
  cadence, retries and backoff; the adapter has no polling gate or sleeps.
- Refreshes the [device master list](https://data.edmonton.ca/d/javh-pjj2) and
  [station coordinates](https://data.edmonton.ca/d/fhxi-cnhe) daily. The master
  list supplies metadata only: its potentially stale status cannot create an
  outage or override the outage feed. If either daily refresh fails, its cached
  table is reused and the failure is recorded in `last_error`. If no usable
  station table exists, the fetch raises. Without a usable device list (OP-313)
  the outages still post with the plain detail below, the failure is recorded in
  `last_error`, and the next poll reads the list again. Each table refreshes
  independently; lookup records are not deep-copied on every outage poll.
- **OP-313:** the adapter keeps the device list grouped by station, and each
  outage's `detail` adds the station's other devices that are in service, by type
  and the City's location label, elevators first. A device is in service unless
  the outage feed lists it (any status other than `0`); the list's own status is
  never used. "Use the ... instead" appears only for an in-service device with the
  same location label, and an escalator is never suggested in place of an
  elevator. From the recorded snapshot:
  - `Central, West Platform. In service at Central: elevators at Platform and North Entrance; escalators at East Platform and North Entrance.`
  - `Health Sciences/Jubilee, Platform. Use the Platform elevator instead.`
  - An elevator with no other in-service elevator at its label adds
    `No other elevator at Platform is in service.`; a station with every device
    out says `No elevators or escalators at <station> are in service.`
  - The detail stays within 500 characters: a long list names as many labels as
    fit and counts the rest (`and 3 more`), never cutting a word. An outage row
    without a device ID, or a station whose device rows lack a type or label,
    keeps the plain detail, because the answer could be wrong.
- Returns the obstruction fields documented in PLAN.md section 10, including
  device ID, station/device location, outage start, our fetch confirmation time,
  attribution and station geometry. Elevator outages are `blocked`; escalator
  outages are `caution`, per the profile rules.
- Chooses a stable point from the City's station table using normalized station
  names, preferring a `Station` over a surface `Stop`. The verified name alias
  `Government Centre` / `Government Station` is explicit. Stop IDs sort
  numerically, so stop 9 precedes stop 10. These are station-level
  points, **not elevator entrance or OpenStreetMap matches**.
- Uses a ten-second timeout with one attempt per page. Network/HTTP errors
  propagate to the scheduler. On HTTP 429, `FeedError.retry_after_s` carries
  `Retry-After` seconds (or the remaining delay from an HTTP date); OP-103 must
  respect that hint when scheduling the next attempt. No delay is performed here.
- Skips and logs invalid rows, counts them in `last_error`, and continues with
  valid outages. Previously known records affected by a bad row are retained
  with their old timestamps, so partial reconciliation cannot silently clear
  them or falsely refresh them. If the bad row has no device ID, absent old
  records are retained conservatively, except explicit in-service devices.
- Raises `FeedError` for transport/envelope failures, unusable initial lookup
  data or an entirely invalid nonempty outage feed. `last_good` stays intact.
  A successful empty outage feed clears outages. The cache is in memory only.
- Uses **our successful fetch time** for `observed_at`, and sets `expires_at`
  ten minutes later. A City outage still present in a new fetch is freshly
  confirmed even if `polled_time_stamp` is old. This prevents recurring gaps
  between City updates. `since_time_stamp` remains `starts_at`; human-readable
  `detail` starts with `<station>, <device location>` (OP-311 reads the station
  name before the first comma), never an ISO timestamp.

## Integration still needed

1. **OP-101 (#29), Tatsat:** the shared `Obstruction`, `SourceAdapter` and store
   have not landed. `normalize_outages()` returns ordinary dictionaries following
   PLAN.md. Adapt these to the agreed types once available; no shared schema,
   API or `config/` file is created or changed by this work.
2. **OP-103 (#31), Matthew:** register the source with the shared scheduler and
   status reporting. The scheduler owns retries, backoff and the 120-second
   cadence; respect `FeedError.retry_after_s` when supplied. Surface `last_error`
   even after a successful partial poll, plus observation freshness, on `/api/status`.
3. **Timezone confirmation:** City `since_time_stamp` values are
   floating datetimes and their metadata does not identify a timezone. The
   adapter deliberately requires `source_timezone`; it does not assume UTC.
   `America/Edmonton` in the examples is an **unverified interpretation**, printed
   as `source_timezone_assumption` by the CLI. Confirm it with the source owner
   before live integration. Explicit offsets are honored; ambiguous DST times
   are reported as bad rows rather than inventing an offset. Windows may need
   the `tzdata` package. Fetch confirmation times always use our UTC clock.
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
