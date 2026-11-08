# Performance (OP-404)

Measured numbers only. Every figure below says where, when (UTC), on which commit and from which client it was measured. Nothing here is a target unless it says so.

## Summary

- On the production instance (Render free plan) a route request took **1158 ms at p50 and 2607 ms at p95** when requests came one at a time, and **4721 ms at p50** with four at once (November 8, 2026, 18:26 to 18:30 UTC, 160 requests, no errors). The network and framework floor from Edmonton was 115 ms (`/health`), so about a second of that is routing work on the instance's CPU.
- A route request is Dijkstra bound: four or five single-source Dijkstra runs on the 134,948-node network, each about 12 ms on a laptop, make up over 90% of the request. Everything else (weights, sparse matrix, report, JSON) is a few milliseconds.
- The per-profile weight and matrix cache this story adds (`routing.WeightCache`) can therefore save at most 3 to 4 ms of a 55 ms route on a laptop, which is below the run-to-run noise of the HTTP measurement (pooled p50 58 ms before, 59 ms after; p95 78 and 81 ms). The routes it returns are identical (tests). The lever that would change the production number is the instance (decision D-2) or fewer Dijkstra runs, which is a contract change for the lead (see the last section).
- Every database query already used an index. One index was added (`saved_route(client_id, created_at)`), and `db.ensure_indexes` now creates at startup any declared index an older database lacks.
- Cold starts: Render spins a free instance down after 15 idle minutes; the `Keep warm` workflow, the camera worker and a warm-up visit keep it awake for judging.

## How it is measured

`scripts/route_bench.py` (`make bench`; standard library only, no keys):

- **Trips:** 20 fixed pairs of real places from `config/places.json`, across downtown and the Ice District (7), the university and Whyte Avenue (5), the LRT line to the north (5) and the river crossings (3), from about 300 m to about 3 km. They are fixed by name; a renamed place stops the script (`cloud/tests/test_route_bench.py` checks them).
- **Profiles:** all four (`wheelchair`, `walker_cane`, `low_vision`, `standard`), so one pass is 80 requests.
- **Levels:** every trip and profile once per pass, first on one worker (sequential), then on four workers at once. Each worker keeps one HTTP connection open, as a browser does.
- **Rate:** at most 4 requests per second in total by default, because the live API is shared with the demo. Local runs use `--rate 0`.
- **A sample** is the wall time from sending a request to receiving its last byte. Percentiles use the nearest-rank method on the successful samples (p95 of 20 is the 19th in sorted order). A non-200 response counts as an error and is left out of the percentiles; the script exits 1 when there was any.
- **Warm-up:** one `GET /health` first (its time is reported, and a warm-up at least 10 times slower than the five `/health` calls that follow, and over 3 s, is flagged as a suspected cold start; a heuristic, not a verdict), then five more `/health` calls as the network and framework floor.

Reproduce: `make bench BENCH_FLAGS="--base-url https://openpath-api-w6ql.onrender.com --client 'where you are' --json bench.json"`. Keep the live run at the default rate.

## Production: Render free instance

| | |
| --- | --- |
| Instance | `https://openpath-api-w6ql.onrender.com`, Render web service `openpath-api`, free plan, Oregon |
| `/health` | version `25e9856baa9f6deee884565e26948a3abc6d1852` (the Phase 2 release, before this story's cache), mode `live` |
| When | November 8, 2026, 18:26:36 to 18:30:12 UTC |
| Client | Edmonton, Alberta; the lead's MacBook Air (macOS 26.3, arm64, Python 3.12.11) on Wi-Fi (`en0`); one keep-alive HTTPS connection per worker; at most 4 requests/s |
| Warm-up | `/health` 383 ms; the next five 140, 87, 115, 69 and 115 ms (median 115 ms). Not a cold start by the heuristic above |
| Load on the instance | the camera worker was posting (at 18:37 UTC `/api/cameras` listed 56 readings aged 235 to 810 s), and the store held 1,342 active items (1,218 construction, 73 traffic disruptions, 44 trail closures, 7 LRT devices; `/api/status` at 18:37 UTC), which the matcher turns into its hazard map at most every 30 s |

Sequential (one request at a time, 80 requests, 0 errors):

| Profile | Requests | Errors | p50 ms | p95 ms | Max ms | Min ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| wheelchair | 20 | 0 | 1227 | 2798 | 6780 | 887 |
| walker_cane | 20 | 0 | 1058 | 2845 | 3083 | 868 |
| low_vision | 20 | 0 | 1112 | 1906 | 2607 | 897 |
| standard | 20 | 0 | 1118 | 1921 | 2516 | 868 |
| all profiles | 80 | 0 | 1158 | 2607 | 6780 | 868 |

Four workers at once (80 requests, 0 errors):

| Profile | Requests | Errors | p50 ms | p95 ms | Max ms | Min ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| wheelchair | 20 | 0 | 4397 | 6692 | 7488 | 1894 |
| walker_cane | 20 | 0 | 4906 | 6327 | 6686 | 3299 |
| low_vision | 20 | 0 | 4580 | 5937 | 7019 | 3791 |
| standard | 20 | 0 | 4700 | 5918 | 6891 | 2787 |
| all profiles | 80 | 0 | 4721 | 6686 | 7488 | 1894 |

Notes:

- The 6780 ms maximum was the very first route request of the run (wheelchair, Rogers Place to Churchill Square). The first route request after the matcher's refresh interval builds the hazard map before answering, and with 1,342 items to match on this instance's CPU that is the most likely reason; the server logs were not read. Without that sample the sequential p50 is still 1158 ms (79 samples). The production map is much larger than the 61 recorded items the local measurements below use.
- Four requests at once took about four times as long each, at the same overall throughput: the instance serves requests one after another (one process, and SciPy's Dijkstra holds the Python lock). Four judges planning routes at the same moment would each wait 4 to 7 s on this instance.
- Response bodies were 8 to 84 kB (median 26 kB); at these times the transfer is not where the time goes.

Against the user story ("routes in about a second"): the sequential p50 is about a second; p95 is 2.6 s; with four users at once it is 4.7 s.

## Where a request spends its time

Measured in process on the lead's laptop (Apple silicon MacBook Air, macOS 26.3, Python 3.12.11, SciPy 1.18.1, NumPy 2.5.4), commit `3967064`, replay mode with the recorded fixtures (61 obstructions in the store, 10 requests through FastAPI's test client, `cProfile`):

| Step | Time | Share |
| --- | ---: | ---: |
| `Router._shortest` (SciPy Dijkstra), 41 calls | 546 ms | 90% |
| `WalkNetwork.length_matrix`, 42 calls | 42 ms | 7% |
| everything else (snapping, report, Maps links, JSON) | about 20 ms | 3% |
| whole request, p50 | 55 ms | |

A route runs Dijkstra once for the best route, up to three more times for alternates (`attempts: 4` in `config/profiles.json`) and once for the plain route that hazards are compared against; each run is a full single-source tree on 134,948 nodes and 194,643 edges (389,286 matrix entries), about 12 ms. Building the weights takes 0.2 to 0.3 ms and the sparse matrix 0.9 to 1.2 ms per Dijkstra run. The network loads in 58 ms and its arrays take 20.5 MB.

## Local: before and after the weight cache

Same laptop, same trips, the API in replay mode on `localhost` with the recorded fixtures (61 obstructions), `--rate 0 --repeat 2` (160 requests per level per run). "Before" is commit `3967064` (the base of this branch) running on port 8731; "after" is this branch on port 8732. The two servers ran at the same time and the runs were interleaved (before, after, before, after) because other work was running on the laptop during the measurement; the spread between the two rounds of the same server shows that noise.

November 8, 2026, 18:34:29 to 18:35:47 UTC, all profiles together (40 requests per profile per level per run), 0 errors in every run:

| Run | Level | Requests | p50 ms | p95 ms | Max ms | Min ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| before, round 1 | sequential | 160 | 58 | 73 | 94 | 53 |
| after, round 1 | sequential | 160 | 62 | 88 | 111 | 52 |
| before, round 2 | sequential | 160 | 59 | 78 | 238 | 53 |
| after, round 2 | sequential | 160 | 55 | 75 | 87 | 52 |
| before, round 1 | 4 workers | 160 | 226 | 277 | 334 | 193 |
| after, round 1 | 4 workers | 160 | 229 | 297 | 360 | 115 |
| before, round 2 | 4 workers | 160 | 227 | 281 | 310 | 186 |
| after, round 2 | 4 workers | 160 | 225 | 284 | 377 | 160 |

Both rounds pooled (320 requests per cell):

| | Level | p50 ms | p95 ms | Max ms | Min ms |
| --- | --- | ---: | ---: | ---: | ---: |
| before (`3967064`) | sequential | 58 | 78 | 238 | 53 |
| after (this branch) | sequential | 59 | 81 | 111 | 52 |
| before (`3967064`) | 4 workers | 227 | 280 | 334 | 186 |
| after (this branch) | 4 workers | 227 | 289 | 377 | 115 |

Per-profile rows are in the run files' tables; every profile sits within 2 ms of its run's overall p50 at the sequential level.

What this shows:

- **The cache makes no measurable difference at the HTTP level on this laptop.** The in-process saving it can make is bounded by what it replaces: about 0.3 ms of weights plus about 0.6 ms per sparse-matrix build, four or five builds per route, so 3 to 4 ms of a 55 ms request. The round-to-round spread of the same server (p50 58 to 59 ms before, 55 to 62 ms after) is larger than that. The fastest run of the four was the cached server's second round; the slowest was its first.
- **Four requests at once take four times as long each,** before and after: the API serves one request at a time (one process; Dijkstra holds the Python lock), the same shape as production.
- **Memory.** Resident set size read right after each run: before 217 MB and 204 MB, after 221 MB and 230 MB. The cache's theoretical maximum here is 8 entries of about 5 MB (four plain, four for the current hazard map).

Earlier single runs on the same servers (18:19 and 18:25 UTC, 240 requests per level, not interleaved) gave before 56/68 ms and after 70/102 ms (p50/p95, sequential); the after run overlapped with a test run on the same laptop, which is why the interleaved rounds above were made.

## The weight cache

`cloud/app/routing.py`, `WeightCache` and `ProfileWeights`:

- **What is cached.** For each profile and hazard map: the per-edge weights (`profile_weights`), the sparse matrix's CSR structure after zero entries are removed (`indices`, `indptr`), the edge behind each stored entry (`kept`) and the removed-edge mask. The matrix for any weights with the same zero pattern, including the penalised copies the alternates search makes, is then one gather instead of a full `length_matrix` build (about 0.4 ms instead of 0.9 to 1.2 ms). The cached arrays are read-only; `_search` copies them before penalising.
- **Invalidation.** Entries are keyed by the hazard map object. OP-206's matcher and OP-205's camera provider build a new map whenever anything changes and never edit a map they have handed out, so a new map simply misses the cache; the cache keeps a reference to the maps it holds so an id cannot be reused. Empty maps share the plain entries. Only the newest hazard map is kept (`max_maps=1`), so the cache holds at most one entry per profile for the plain weights and one per profile for the current map: at most 8 entries of about 5 MB each on the city network.
- **Thread safety.** Entries are immutable; the tables change only under a lock; a miss builds outside the lock, so two threads can build the same entry once and the second is dropped. The push watcher (`push.py`), the LRT planner (`lrt.py`) and the live hub's best-route router (`ws.py`) share the request router's cache.
- **One shortcut.** When no hazard changes a weight for the profile (an empty map, or caution-only items), the plain route is the first route found: the same Dijkstra on the same matrix would return the same path, so it is not run again. With city data there is always a blocked path somewhere, so this does not help the production number.
- **Routes do not change.** `cloud/tests/test_weight_cache.py` routes grid and city trips for every profile with and without the cache and requires identical results, checks the cached matrix against `length_matrix` entry for entry, and runs 16 threads against the cache at once.

## Database indexes

`EXPLAIN QUERY PLAN` on SQLite for every query the five stores run (`cloud/app/store.py`, `camera_readings.py`, `saved_routes.py`, `push.py`, `reports.py`), on commit `3967064`:

| Query | Plan before this story |
| --- | --- |
| `obstruction` active, by source, by bbox, expired, by id | index lookup (`ix_obstruction_expires_at`, `ix_obstruction_source`, `ix_obstruction_bbox`, the primary key); active queries then sort with a temporary B-tree for `ORDER BY category, id` |
| `camera_reading` latest, expired | index lookup (`ix_camera_reading_expires_at`); latest then sorts for `ORDER BY camera_id, view_id` |
| `saved_route` by client (`add`, `for_client`) | index lookup (`ix_saved_route_client_id`); `for_client` then sorts with a temporary B-tree for `ORDER BY created_at, id` |
| `saved_route` all | a full scan, by design (the watcher indexes every route) |
| `push_subscription` by endpoint, by client | index lookup (the unique endpoint, `ix_push_subscription_client_id`) |
| `community_report_log` counts, oldest, prune | covering index lookups (`ix_community_report_log_client`, `ix_community_report_log_created`) |

Every filter already hit an index. The only sort an index can remove is `for_client`'s: `saved_route(client_id, created_at)` serves the equality and the order together, so the alerts page's read (`GET /api/saved-routes?client_id=`) no longer sorts (`cloud/tests/test_indexes.py` checks the plan: the new index, no temporary B-tree). The sorts after a range scan (`active`, `latest`) cannot be served by an index, and those result sets are small (hundreds of rows for `active`, under a hundred for `latest`). Nothing was dropped or renamed.

`db.ensure_indexes(engine)` runs at startup after every table exists: it compares each model's declared indexes with the database's and creates only the missing ones. `create_all` never alters an existing table, so a Postgres database created by an earlier release would otherwise never get an index declared later. A failure there is logged and the API starts anyway. The tests create tables without their indexes, as an older release would have, and check that one call adds them and a second call adds nothing.

## Cold starts and the plan for judging

Render's documentation ([render.com/docs/free](https://render.com/docs/free), read on November 8, 2026): "Render spins down a Free web service that goes 15 minutes without receiving any inbound traffic." Waking it up "takes about one minute." Also: "Render grants 750 Free instance hours to each workspace per calendar month", and when they run out "Render suspends all of your Free web services" until the next month. One instance kept awake around the clock uses about 720 hours in a 30-day month, so only one free service may be kept warm; a second one would exhaust the workspace's hours.

Three things keep the API awake; any one is enough:

1. **The camera worker** (OP-204) posts to `/api/ingest/camera` every 5 minutes while it runs on the lead's laptop (docs/DEPLOY.md, section 4).
2. **The `Keep warm` workflow** (`.github/workflows/keep-warm.yml`) calls `/health` and `/api/status` on production every 10 minutes from the repository's default branch and fails when either is not HTTP 200 or `/health` does not report a 40-character commit, so GitHub emails the person who last changed the workflow file. GitHub may start a scheduled run a few minutes late and disables schedules in a repository with no activity for 60 days; neither matters during the hackathon. OP-402's uptime workflow may absorb these two calls.
3. **A warm-up visit** 10 minutes before the judging slot: open the web app, plan one route, open the alerts page. The first route request after an idle spell is slower (see the 6780 ms sample above), so the warm-up also takes that hit instead of the judges.

Check the `Keep warm` run is green in the Actions tab before the slot. Decision D-2 (PLAN.md section 16) still covers moving to an always-on instance.

## What would change the production number

Options for the lead, not done in this story because each changes a contract or needs a decision:

- **A paid instance** (decision D-2). The laptop in-process cost is 55 ms per route; the free instance took about 1000 ms for the same work, and it serves one request at a time. More CPU, or a second worker process (which needs about twice the memory: the network, the matcher's index and the caches are per process), is the only change that moves p50 without touching routing.
- **Fewer Dijkstra runs.** `alternates.attempts` is 4 in `config/profiles.json`; the plain route is a fifth run. Lowering `attempts`, searching for alternates only on request, or stopping Dijkstra early at the destination (SciPy's `limit` would need a proof that tie-breaking stays identical) each changes what routes come back, so they are contract changes.
- **Reusing a Dijkstra tree** between requests with the same origin and weights (a user switching profiles, the live planner re-routing the same trip) would not change results but helps only repeat traffic.
