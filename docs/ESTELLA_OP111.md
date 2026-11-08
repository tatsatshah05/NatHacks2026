# OP-111: snow-clearing status adapter

**Purpose:** create a road caution after a verified recent plow pass, warning that
snow ridges may obstruct curb ramps. [OP-111 (#39)](https://github.com/tatsatshah05/NatHacks2026/issues/39)
is Estella's last Phase 1 story, marked Should.

## What the adapter does

- Reads the official route-status feed once per scheduler refresh; `cadence_s=900`.
- Skips and logs area/road rows missing either `route_stat` or `seg_stat`. NA rows
  are inactive and produce no item. A normal inactive refresh succeeds with zero
  items even when statusless area records coexist in the feed.
- Skips and logs completed rows without a verified service time. Invalid times,
  including timezone-free timestamps, affect only that row.
- Allows up to five seconds of positive clock skew. A slightly future time is
  capped to the refresh time so the caution is immediately active and does not
  receive an extra future lifetime. Greater skew skips that row with a warning.
- Creates a `plow_passed` / `caution` for each verified road serviced less than
  12 hours ago. Its title is exactly: "Plow passed. Curb ramps may be blocked by snow ridges."
- Emits the road's own `geometry_line`, supporting LineString and MultiLineString.
  It never needs a crossing resolver. OP-206 matches road lines to walking
  crossings with the agreed 15 m buffer.
- Uses stable `route_id` identity; `fid` is only a pagination tiebreaker because
  the City documents it as nonstable.
- Expires each caution at the earlier of its 12-hour deadline or 30 minutes after
  the current successful observation. Cautions do not change routing costs.

## Source timing evidence

Dataset `8pdx-hfxi` documents `last_updat` as deprecated and always empty.
Neither `serv_hrs`, `serv_ach`, nor fetch time establishes the actual service time.
The adapter therefore accepts an optional `verified_service_time(row)` function
that supplies a timezone-aware datetime from genuine service evidence. Without
that evidence, the affected completed row is skipped and logged; the rest of the
feed still refreshes. No plow timestamps are invented.

The metadata recording remains documentation, not a unit test that asserts its
wording. Behavioral regression tests show that populating `last_updat`,
`serv_hrs`, and `serv_ach` cannot produce a fake recent-plow caution.

## Reliability and scheduler integration

`fetch(now)` is synchronous and returns a complete set of usable current items.
It performs one HTTP attempt per page with httpx's 10-second timeout. It does
not retry or sleep: OP-103 owns retry/backoff policy and calls it every 900 seconds.
An HTTP failure, malformed source response, conflicting duplicate route, or truly
exceeded page limit raises, preserving `last_good` and `last_success`.
`last_error` stores the full `str(exc)` message. Refresh outcomes and skipped rows
are logged on single lines; the stored error itself preserves its original text.

A full final page is not automatically an error. At the configured data-page
limit the adapter probes one extra row: an empty probe confirms a complete exact
multiple; a nonempty probe fails rather than publishing a truncated snapshot.
A failed probe also fails the entire refresh, preserving previous data.

The in-memory cache is not persistence across restarts. A successful empty or
all-inactive refresh is distinct from failure and can clear this source's old
items in the shared store. Individual skipped rows are excluded from that
successful snapshot; operators can inspect their logged IDs and reasons.

## Run locally (Python 3.12)

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r cloud/requirements-snow-clearing.txt
.venv/bin/python -m cloud.tools.preview_snow_clearing
.venv/bin/python -m cloud.tools.preview_snow_clearing --live
```

The preview is one-shot, not a server or scheduler. It defaults to the recorded
NA sample (`mode=replay`); `--live` reads the public City API. Set optional
`SOCRATA_APP_TOKEN` in the process environment for normal team access. The token
is sent as `X-App-Token`; it is never committed or logged. JSON goes to stdout,
operational logs to stderr. The CLI notes the remaining verified-time integration
without requiring any crossing-map input.

Files:

- `cloud/app/adapters/snow_clearing.py`: parsing, pagination, cache, logging and
  road caution construction.
- `cloud/tests/test_snow_clearing.py`: recorded and controlled regression tests.
- `cloud/tests/fixtures/snow_clearing/`: real NA/missing-status samples and metadata.
- `cloud/tools/preview_snow_clearing.py`: standalone recorded/live check.

## Tests and lint

Runtime requirements contain no test tools. Install them separately, including
OP-104's runtime packages for the rebased walking-network suite:

```bash
.venv/bin/python -m pip install pytest ruff==0.17.4
.venv/bin/python -m pip install -r cloud/requirements-network.txt
.venv/bin/python -m pytest cloud/tests -q
.venv/bin/ruff check --fix .
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Run Ruff at the repository root without rule-selection overrides, as CI does.
Tests never call live APIs. Completed-status rows and verified service times are
explicitly controlled test inputs, not recorded observations of plow activity.
An integration test runs automatically when OP-101's shared schema is available;
until then it skips. The current proposed OP-101 protocol/model was also checked
independently against a City MultiLineString caution with controlled evidence.

## Shared-app handoff

Use OP-101's shared Obstruction model and source interface, OP-103's scheduler
and freshness reporting, and OP-206's line-to-crossing matching. The adapter's
default factory imports the shared model only when it constructs an eligible
caution. Tests inject a dictionary factory for isolated checks. No shared schema,
configuration or API has been changed. Consolidate runtime dependencies, run
shared `make test` once OP-102 adds the Makefile, and verify the preview deploy.
The task-specific notes remain here; AGENTS.md and README have no branch-only
OP-111 instructions.

## Official source evidence

- Dataset: https://data.edmonton.ca/d/8pdx-hfxi
- API: https://data.edmonton.ca/resource/8pdx-hfxi.json
- Metadata: https://data.edmonton.ca/api/views/8pdx-hfxi.json
- Winter travel: https://www.edmonton.ca/transportation/on_your_streets/snow-ice

The City states that this map updates only during declared snow events; NA does
not prove no minor clearing occurred. Every emitted item attributes City of
Edmonton Open Data. Recorded source samples were retrieved November 7, 2026.
