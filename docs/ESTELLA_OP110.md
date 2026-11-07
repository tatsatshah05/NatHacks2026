# OP-110: 311 snow complaints and trail closures

Estella's second Phase 1 story is [OP-110 (#38)](https://github.com/tatsatshah05/NatHacks2026/issues/38).
The implementation is on `op-110-snow-and-trails`, branched from `phase_1`.
It is independent of the OP-109 weather PR.

## Run it

From the repository root, install the isolated dependencies into a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r cloud/requirements-city-data.txt
.venv/bin/python -m cloud.tools.preview_advisories
```

This prints JSON from recorded City responses, explicitly labelled `mode=replay`.
The snow fixture is a small sample, so its output is not a city-wide count.
For a current, complete paginated fetch:

```bash
.venv/bin/python -m cloud.tools.preview_advisories --live
```

The CLI reads optional `SOCRATA_APP_TOKEN` from the process environment and sends
it as `X-App-Token`. Use the team's token for regular polling to reduce throttling;
do not commit it. Public unauthenticated access works for a local smoke check.
The command performs one refresh and exits; it does not start a server or scheduler.

## Files

| File | Purpose |
| --- | --- |
| `cloud/app/adapters/city_advisories.py` | Shared City API transport and the two OP-110 adapters |
| `cloud/tools/preview_advisories.py` | Standalone replay/live preview |
| `cloud/tests/test_city_advisories.py` | Offline tests with recorded responses and explicit mutations |
| `cloud/tests/fixtures/city_advisories/` | Original source samples and recording metadata |

## Behaviour

**Snow complaints:** only open requests created in the last 72 elapsed hours,
excluding future requests. Deduplicate by `row_id`, then count by
`neighbourhood_id`. Publish one low-confidence `snow_complaints` obstruction per
neighbourhood with effect `caution`. Detail includes "area, not exact location".
The marker is the City's neighbourhood centroid, not the complaint address or
the boundary of the affected area. These advisories must not reroute users.

**Trail disruptions:** use the structured `type_of_closure`: `Permanent` and
`Temporary` map to `trail_closed`/`blocked`; `Caution` maps to
`trail_caution`/`caution`. Exclude future starts and definite expired end dates.
Definite end dates include the whole final day; named months include the whole
month. Vague ends such as "Ongoing", "1 Year" or "Spring 2027" stay unknown and
are retained in the text. Never guess a reopening date from prose.

**Refresh reliability:** 10-second timeout and three retries (1, 2, 4 seconds).
Fetch every page in stable source-ID order before replacing any cached data.
Malformed responses, invalid active records and pagination limits fail the refresh
and preserve `last_good`. A successful empty response is distinct from failure.
Freshness is an in-memory cache, not persistence across process restarts.

## Shared-app handoff

OP-101 and OP-103 are not present on the current `phase_1` branch. The adapters
return dictionaries with exactly the existing PLAN.md obstruction fields; no
replacement `schemas.py`, config, store, endpoint or scheduler has been created.

With a shared `httpx.AsyncClient`, construct:

```python
snow = SnowComplaintsAdapter(client, app_token=token)
trails = TrailClosuresAdapter(client, app_token=token)
```

Register `snow.fetch(now_utc)` hourly (`poll_interval_seconds=3600`) and
`trails.fetch(now_utc)` every 15 minutes (`poll_interval_seconds=900`) with OP-103.
Use the OP-101 agreed interface once merged; these classes are not a substitute
for that shared interface. Successful results are complete per-source snapshots.
The store must reconcile removed IDs on successful refresh; on failure it must
keep existing data and mark the source stale. `last_success`, `last_error` and
`last_good` are available for handoff. Existing items also carry expiry times.

The shared map/matcher must treat `snow_complaints` as a neighbourhood advisory,
never as an exact point barrier. Trail locations come from official point markers;
mapping them to affected walking edges belongs to OP-206.

## Timestamp assumption to verify with the City/lead

The 311 feed uses Socrata floating timestamps without a timezone declaration.
For complaints, the adapter explicitly assumes the publisher's `America/Edmonton`
time, following Socrata's published guidance. This remains an assumption to
confirm, configurable through `source_timezone`; it never follows the machine's
timezone. The trail dataset links to an official ArcGIS layer whose metadata
declares date fields in UTC, so trail start timestamps default to UTC.
Human-readable trail end dates use Edmonton calendar days.

Packaged `tzdata>=2026.6` supplies current Alberta rules, even on older hosts.
Alberta adopted permanent UTC-6 in November 2026. Tests cover winter/summer,
the new rules and historical ambiguous/nonexistent DST times.

## Checks

```bash
.venv/bin/python -m pytest cloud/tests -q
.venv/bin/ruff check --isolated --target-version py39 --select E4,E7,E9,F,I cloud
.venv/bin/ruff format --isolated --target-version py39 --check cloud
```

The project targets Python 3.12; the feature also supports the available system
Python 3.9. Tests never use the network. The shared `make test`, preview deploy
and teammate review remain integration checks once the app skeleton is merged.
