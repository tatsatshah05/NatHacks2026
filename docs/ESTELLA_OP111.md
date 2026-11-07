# OP-111: snow-clearing status adapter

**Purpose:** warn about possible snow ridges at curb ramps after a verified
plow pass on the road being crossed. This is Estella's last Phase 1 story and
is marked **Should**. Issue: [OP-111 (#39)](https://github.com/tatsatshah05/NatHacks2026/issues/39).

## Status: implementation started; required timing data is missing

The official dataset `8pdx-hfxi` does not currently provide a documented service
timestamp. Its metadata explicitly describes `last_updat` as deprecated and
always empty. `fid` is explicitly nonstable. Neither `serv_hrs` nor `serv_ach`
is documented as the actual time a road was cleared.

Therefore a Completed label alone cannot prove a pass happened in the last
12 hours. This branch does **not** invent a pass time using fetch time, dataset
update time, or `serv_hrs`. OP-111 remains incomplete until the lead confirms a
reliable timing source or approves a change to the acceptance criteria.

The upstream ArcGIS URL linked in the City's dataset description now returns
"Token Required" to unauthenticated requests. This adapter uses only the public
City open-data API; it does not bypass access checks or scrape a map.

## What is implemented

- `cloud/app/adapters/snow_clearing.py`: paginated, synchronous `fetch(now)` with
  `source` and `cadence_s=900`, matching OP-101's proposed protocol.
- Inactive `NA` produces no items. Completed roads require a verified service
  time and mapped walking crossings. Missing evidence raises an explicit error
  and preserves the previous successful cache.
- Verified passes younger than 12 hours produce `plow_passed` / `caution` items
  at crossings, using exactly: "Plow passed. Curb ramps may be blocked by snow ridges."
- Items expire at the earlier of their 12-hour deadline or two refresh intervals.
  They do not change routing costs or claim a ramp is definitely blocked.
- `route_id`, combined with a stable crossing ID, identifies each item; `fid`
  is used only to order pages. Failed or partial refreshes never clear data.
- Recorded `NA` fixture, source metadata, and tests for time boundaries, missing
  evidence, duplicates, retries, pagination, and cache preservation.

The test completed-status rows, service timestamps and crossing locations are
explicitly synthetic inputs. They are not recorded evidence of real plow passes.

## Run it

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r cloud/requirements-snow-clearing.txt
.venv/bin/python -m pytest cloud/tests -q
.venv/bin/python -m cloud.tools.preview_snow_clearing
```

The preview defaults to the recorded NA sample and labels itself `mode=replay`.
For one current fetch:

```bash
.venv/bin/python -m cloud.tools.preview_snow_clearing --live
```

The live check on November 7 also found area records that omit `route_stat` and
`seg_stat` (for example, `4ne Area 407`). The adapter currently rejects those
records explicitly rather than interpreting missing status as `NA` and clearing
previous alerts. `missing_status.json` records a small actual sample for a
regression test. The preview reports this as unavailable and exits with code 1.
The lead/source owner must confirm how these area records should be interpreted
before live integration can be considered complete.

This is a one-shot check, not a running scheduler. Set `SOCRATA_APP_TOKEN` in the
process environment for the team's normal access; it is sent as `X-App-Token`.
The HTTP client uses a 10-second timeout and three retries with 1/2/4-second waits.

## Integration inputs still needed

1. **Actual service time:** give Tatsat the metadata evidence and ask which source
   establishes when each road was cleared. A provider can then implement
   `verified_service_time(row) -> timezone-aware datetime | None`.
2. **Walking crossings:** wire a resolver returning `(crossing_id, GeoJSON geometry)`
   pairs for crossings of each serviced road. Road geometry itself is not a
   crossing location. Coordinate with the graph/matching owners.
3. **Shared model:** OP-101 remains unmerged on `phase_1`. The adapter imports its
   real `Obstruction` model only when building an eligible item. Tests inject a
   dictionary factory to check isolated logic. An additional integration test
   runs automatically once `cloud.app.schemas` is present.
4. **Scheduler/store:** register `.fetch(now)` every 900 seconds through OP-103.
   Reconcile removals after a successful complete refresh; preserve data and mark
   stale after a failure. Consolidate isolated dependencies into shared requirements.
5. Run the shared `make test`, verify the preview deployment, and request review
   before closing issue #39. There is still no root Makefile on this branch.

Verified locally: 30 offline tests pass on Python 3.9 and 3.12. One shared-schema
integration test skips until OP-101 is merged. An independent compatibility check
against Tatsat's current OP-101 branch also passed with controlled test evidence.

## Official source evidence

- Dataset: https://data.edmonton.ca/d/8pdx-hfxi
- API: https://data.edmonton.ca/resource/8pdx-hfxi.json
- Metadata: https://data.edmonton.ca/api/views/8pdx-hfxi.json
- Winter travel: https://www.edmonton.ca/transportation/on_your_streets/snow-ice

The City states that the map updates only when a snow event is declared, so an
inactive feed is not proof that no minor clearing has happened. Attribution for
every generated item is **City of Edmonton Open Data**. The small NA recording
and metadata excerpt were retrieved November 7, 2026.
