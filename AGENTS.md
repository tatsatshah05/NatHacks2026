# OpenPath: Agent Instructions

This file gives every coding agent (Claude Code, Codex, Cursor, GitHub Copilot, Gemini and others) the context and rules for this repository. Read all of it before changing anything. People should start with [README.md](README.md).

## Project in Brief

OpenPath plans accessible routes across Edmonton and lists every known barrier between A and B:

- **Indoor:** LRT elevator and escalator outages
- **Sidewalks and paths:** construction closures, trail closures, snow complaints and community reports
- **Roads and crossings:** road closures and snow ridges left by plows
- **Weather and ice:** a black-ice watch, plus road conditions that a vision model reads from public cameras

Routes depend on how a person gets around: wheelchair, walker or cane, low vision, or standard. Data comes from City of Edmonton open data, live weather, public cameras and community reports. **There is no hardware.**

The project is being built at natHACKS 2026 (Edmonton) in 48 hours by Team ClaudesPlan. Repo: https://github.com/tatsatshah05/NatHacks2026

## Current Status

Update this section whenever the status changes.

- **OP-404 (performance):** `make bench` (`scripts/route_bench.py`) measures `GET /api/route` for 20 fixed trips and four profiles against any instance; the measured numbers, the method and the judging plan are in [docs/PERFORMANCE.md](docs/PERFORMANCE.md). A request is Dijkstra bound (four or five runs on 135k nodes), so `routing.WeightCache` caches only the per-profile weights and matrix structure per hazard map object (a provider's new map is the invalidation) and routes are unchanged; `db.ensure_indexes` adds at startup any declared index an older database lacks (new: `saved_route(client_id, created_at)`). `.github/workflows/keep-warm.yml` calls production `/health` and `/api/status` every 10 minutes and fails loudly; the worker's posts and a warm-up visit 10 minutes before judging complete the plan (docs/DEPLOY.md, section 4).
- **Data quality checks (OP-407):** each adapter owns a `RowQuality` (`cloud/app/adapters/_quality.py`) and records the rows its own validation rejected and the field names it does not know (Socrata `:`-prefixed system fields excepted); the scheduler copies the report onto `GET /api/status` as additive, optional fields per source: `rejected_rows`, `unexpected_fields` (sorted, at most `UNEXPECTED_FIELDS_MAX` = 20), `last_validation_error` (the first rejection message of the last fetch, else null) and `count_change` (`{today, yesterday, drop}`). `cloud/app/quality.py` writes one item count per source and UTC day to `source_count_daily` after every successful fetch and compares it with the previous day's row: `drop` is true when today is below `DROP_FRACTION` (0.5) of yesterday and yesterday was at least `DROP_MIN_YESTERDAY` (10), logged once per day as `WARN quality <source>: items fell from m to n`; new field names log once as `WARN quality <source>: new fields a, b`. Nothing here clears data, fails a fetch or changes routing; in replay the day comes from `app.state.clock`. `/city` says it in one sentence per source (`source-status.tsx`). Changed-schema fixtures: `cloud/tests/fixtures/schema_change/`.
- **OP-411 (judge page and launch assets):** `/demo` shows a large QR code of the production address, the pitch, a suggested trip that opens the planner pre-filled (Edmonton Convention Centre to the Alberta Legislature for a wheelchair, verified in replay mode) and three things to try; `?url=` (http or https) overrides the QR target for local testing. The QR encoder is `web/src/lib/qr.ts` (no dependency; checked against the Python `qrcode` package and decoded with OpenCV). `web/src/app/opengraph-image.tsx` is the social preview (Twitter cards reuse it), `web/src/lib/site.ts` holds the name, pitch and production URL, and `python scripts/make_icons.py` writes the favicon (`web/src/app/icon.svg`, light and dark variants), `icon1.png` and `apple-icon.png` after running `build_icons.py`. See docs/design/DESIGN.md section 10.

- **Replay mode (OP-305):** `make record` runs `scripts/record_snapshot.py`, which records every source (JSON only, never images, no token written) into `snapshots/<UTC time>/` in the layout `REPLAY_SNAPSHOT` reads, with a `manifest.json`; snapshots are about 9 MB and stay out of git. `REPLAY_CLOCK=running` advances replay time from `REPLAY_AT` or the manifest's `replay_at`; `WEATHER_REPLAY=freeze_thaw` serves a real freeze-thaw night (Edmonton, November 5 to 6, 2025, from Open-Meteo's archive; the watch fires 12:00 to 18:00 UTC). `/health`, `/api/status`, `/api/weather` and `/api/cameras` say `replay`; the web badge reads `/health`. See [docs/OP-305.md](docs/OP-305.md).
- **OP-311 (walk and LRT trips):** behind `LRT_TRIPS` (off by default). With it on, `GET /api/route?mode=walk_lrt` puts the best walk and LRT trip first (rank 1; `mode`, `legs`, `lrt` fields) when it is faster than walking, and drops the slowest walking route so there are still at most 3 routes. `cloud/app/lrt.py` plans it from `cloud/data/lrt.json` (built from the ETS GTFS schedule by `make lrt`); wheelchair and walker profiles use a station only when its platform elevators and one entrance elevator are in service in the City outage feed; waits are labelled estimates (half the scheduled headway). With the flag off, or `mode=walk`, responses are byte-for-byte unchanged; `/api/route` uses `response_model_exclude_unset=True`, so a new field with a default must be set explicitly. Its Maps links use `travelmode=transit` / `dirflg=r`. See [docs/OP-311.md](docs/OP-311.md).

- **OP-410 (web polish):** every page has loading, error (with Try again), empty and offline states; the header carries one offline line ("You are offline. Showing what was loaded before.") that reserves its space; `web/src/app/not-found.tsx` and `error.tsx` are in the design system; `web/public/sw.js` is registered on every page after load and serves `web/public/offline.html` only when a navigation fails (it caches nothing else). The maps follow `mapStyleFor(settings, dark)`, and the element around each canvas carries the map's own palette (`.map-light` / `.map-dark`, DESIGN.md section 11), which fixes the dark-theme marker contrast (#118); zoom controls are 44 x 44 CSS px (#120); markers are rebuilt only when data changes (#119). The MapLibre chunk mounts when the map nears the viewport (`LazyMount`). Lighthouse before and after is in [web/PERFORMANCE.md](web/PERFORMANCE.md).
- **Accessibility audit (OP-408):** [web/ACCESSIBILITY.md](web/ACCESSIBILITY.md) holds the measured audit of every page (axe-core 4.14.0 on 44 page-contexts, the Tab order and visible focus on each stop, the skip link, Enter, Space and Escape, the focus ring's contrast, the announced route change with the speech and vibration calls, 200% zoom and 320 px reflow, reduced motion, hazard marks, touch targets), the four fixes it led to, and the VoiceOver and TalkBack scripts for Estella, which are written but not yet run. `cd web && npm run a11y` (`web/scripts/a11y-audit.mjs`) regenerates the measurements into `web/test-results/a11y/` against the replay API and production build named by `E2E_WEB_URL`, `E2E_API_URL` and `E2E_ADMIN_KEY`; `A11Y_ONLY=axe,keyboard,...` reruns a subset and `A11Y_PAGES=planner-trip` limits axe to named pages. The OP-212 checklist for UI pull requests stays in the same file.

- **OP-406 (end-to-end tests):** `make e2e` runs `scripts/e2e.py`, which starts the API in replay mode (temporary SQLite, `ADMIN_KEY=e2e`, `WORKER_KEY=e2e`, scheduler on) and the production web build, runs the Playwright flows in `web/e2e/` (plan a route, open the report, a seeded live change over `WS /ws`, save a route, axe on each page) on a phone and a laptop viewport, and stops both. CI runs it in the `End-to-end` job when the app changed; the deploy job runs the read-only `@smoke` subset against production. The seeded change uses `POST /api/admin/override` (`cloud/app/admin_api.py`, `X-Admin-Key`): a labelled test item (`Test override: ...`, source `admin_override`, short `ttl_s`) that rebuilds the hazard map at once (`HazardProvider.rebuild_now`); `DELETE /api/admin/override/{source_ref}` and `DELETE /api/admin/override` remove it.
- **OP-205:** camera `ice` (or `wet` while the watch is active) becomes `ice_risk` on the crossings within 50 m of the camera. `CameraIceHazardProvider` (`cloud/app/camera_ice_routing.py`) is `app.state.hazard_provider`: it wraps the matcher and adds those exact crossings, so camera risks never meet the matcher's area rule. See [docs/OP-205.md](docs/OP-205.md).
- **Model monitoring and fallback (OP-412):** `GET /api/status` carries a `cameras` block (`cloud/app/camera_status.py`): `state` is `live` when a usable camera reading (label other than `unknown`) is newer than `LIVE_WITHIN` (15 minutes, one worker lifetime at the default 5-minute interval), `stale` when usable readings exist but are all older, `none` otherwise; `ice_source` is `cameras` only while live, else `weather_watch`. It also lists reading counts, ages, distinct `model_versions` (opaque strings), score min and median, counts per label and the cached watch's `watch_active`. `GET /api/cameras` repeats it as `summary`. The web helper `web/src/lib/camera-status.ts` turns a state other than live into one sentence ("Road cameras are not reporting right now; ice risk comes from the weather watch alone.", or the readings' age when stale, or "the ice-risk weather watch is off" when it is), shown above the planner's routes (`city-wide-conditions.tsx`) and in the city page's camera list (`road-cameras.tsx`). The worker logs one `WARN camera_model no usable reading ...` line when every view it holds is `unknown`, and one `CHANGE ... resumed` line after. Everything is measured at `app.state.clock`.

- **OP-204 on this branch:** `vision/worker.py` and `vision/inference.py` run
  scheduled CPU inference with shared OP-112 rate limiting, in-memory frames,
  two-observation stability and a metadata-only client for OP-213's
  `POST /api/ingest/camera`. `inference.py` reads OP-202's `labels.json` with the
  wording it imports from `vision/model_contract.py`. Only `image/jpeg` responses
  are classified; 511's HTTP-200 PNG placeholder becomes `unknown`
  (`camera_unavailable`). Readings expire after three intervals, capped at the
  API's 2-hour limit. `vision/tests/test_worker_ingest.py` posts real worker
  readings through `HttpPublisher` into the API and checks them on
  `GET /api/cameras`. Official metadata is recorded (392 cameras; approximate
  Edmonton bbox: 19 cameras/47 enabled views); one JPEG view was verified in
  memory. Live use needs the published `model.onnx` and `labels.json`. See
  [docs/OP-204.md](docs/OP-204.md). Shared schemas, API routes and
  configuration are unchanged; no worker deploy is activated.

- **November 7, 2026:** Phase 1 is in progress. OP-104's walking network is merged through PR #97. OP-101 (contracts), OP-102 (skeleton) and OP-103 (scheduler) remain integration dependencies on this branch.
- **OP-107 on this branch:** the construction adapter and recorded preview isolate malformed/amended rows and use only a short line near the official permit point. Broad corridors fall back to the point. Shared API/store/scheduler integration remains pending; see [docs/OP-107.md](docs/OP-107.md). OP-103 owns hourly cadence, retries and cooldowns; every adapter call performs one fetch, with no internal sleeps or poll gate.
- **CI/CD is live and `main` is protected.** Every pull request needs the `CI passed` check and an approval from the scrum master. See [docs/CI_CD.md](docs/CI_CD.md).
- **November 7, 2026:** planning is complete and Phase 1 is under way. OP-102 adds the skeleton: the FastAPI app (`GET /health`, plus OP-101's `GET /api/obstructions` and store), the Makefile, `render.yaml` and `.env.example`. The web app in `web/` comes from OP-113.
- **Deploys:** the one-time Render, Vercel and GitHub setup is in [docs/DEPLOY.md](docs/DEPLOY.md). Vercel deploys only `main` (`git.deploymentEnabled` in `web/vercel.json`, November 8): the Hobby plan allows 100 deployments per day and previews for every push exhausted it. Test web changes locally with `make dev`.
- **CI/CD is live, and every branch below is protected.** Every pull request needs the `CI passed` check and an approval from Tatsat, Ipsa or Kashish. See [docs/CI_CD.md](docs/CI_CD.md).
- **Phase branches:** Phase 1 work goes into the `phase_1` branch, which is the default branch. When the phase passes its checkpoint, `phase_1` is released into `main`, and `phase_2` starts from `main`.
- **Vision training (OP-202):** `vision/train.py` trains and exports the road-condition model on a laptop or Colab (never Render or CI). Two real models exist, trained on November 8 on Tatsat's laptop from privately kept 511 frames: the night-only `v1-night` and the day-and-night model that replaced it in the live worker. Their measured results and limits are in `vision/MODEL_CARD.md`; the weights are never committed. Decision D-3 (PLAN.md section 16) allows licensed outside datasets for training only. `vision/model_contract.py` defines `labels.json`, the hand-off to the worker (OP-204).
- **Old material:** the earlier hardware design (version 1, named PathSense) is archived in `docs/archive/v1-hardware/`. Ignore it for new work.

- **Scheduler and status (OP-103):** `cloud/app/scheduler.py` runs every source in `config/sources.json` on its cadence (10 s timeout, 3 retries, stale after 3 failures, a failed source never clears data) and `GET /api/status` reports freshness. `SCHEDULER=off` disables fetching (tests). In replay mode the adapters read the `REPLAY_SNAPSHOT` folder through a fake HTTP transport, so nothing touches the network.

- **Live planner (OP-301):** `/` and `/route` call the API through `web/src/lib/api.ts` (typed from the regenerated `api-types.ts`): place search (`/api/search`, 150 ms debounce, stale requests cancelled), `/api/route` (with `mode=walk_lrt` when "Include LRT trips" is ticked), `/api/status` and `/api/obstructions?bbox=` for map markers. The URL carries the trip (`/?from=lat,lng&from_name=…&to=lat,lng&to_name=…&profile=…[&mode=walk_lrt][&saved=<id>]`, `lib/route-link.ts`), so a link reopens the same routes; `history.replaceState` keeps it current. The map is MapLibre (`planner-map.tsx`); `web/src/lib/maplibre.ts` sets the worker URL, because maplibre-gl 6 otherwise looks for its worker at the build machine's file path and draws nothing (the `/city` map, `city/city-map.tsx`, uses the same module). The mock routes in `lib/mock-routes.ts` appear only behind "Show demo routes instead" when the route service cannot be reached, labelled as samples. Each walking route's actions show OP-306's `SaveRouteButton` right after "View route report" (`saveAction` in `planner-page.tsx`); a walk and LRT trip has none, because a saved route (OP-304) has no mode and the API watches the trip's walking route.

- **Routing (OP-207):** `cloud/app/routing.py` runs Dijkstra on the prebuilt network with per-profile weights from `config/profiles.json` (a shared contract), returns up to 3 distinct routes with ETAs, text steps, hazards avoided and a reason, and serves `GET /api/route`. Hazards enter as a `HazardMap` (edge index -> `EdgeHazard`) that the matcher (OP-206) provides through `app.state.hazard_provider`. Each end snaps to the nearest node with a usable path; if no route is found because an end sits in a pocket cut off for that profile (between stairs and a closure, say), it moves to the nearest node of the main connected piece within `snap.max_m` and the search runs again, with a warning. `cloud/app/maps_links.py` (OP-209) builds the Google Maps link with up to 3 waypoints where a route leaves the plain shortest route, and the Apple Maps link (start to end only, with a note).

- **Search (OP-302):** `cloud/app/search.py` serves `GET /api/search?q=` (up to 8 `{label, detail, lat, lng, kind}`, kind `address | station | place`) from the prebuilt `cloud/data/search.npz`: 346,922 City parcel addresses (`ut27-nrpn`, whole city), 29 LRT stations and 54 named places from `config/places.json`. No geocoder at request time. Rebuild with `python scripts/build_search_index.py --write-places` (one official City API read) and commit; `--fixtures` builds offline. See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).
- **Community reports (OP-308):** `POST /api/reports` (`cloud/app/reports_api.py`, `cloud/app/reports.py`) takes `client_id`, `type` (`snow`, `ice` or `blocked`), `lat`, `lng` inside Edmonton, an optional `note` (120 characters) and an optional JPEG or PNG `photo` (5 MB), as multipart or JSON. Each report is a `community_report` obstruction that expires per `config/reports.json` (ice 2 h, snow 12 h, blocked 6 h) and is limited to 5 per client and 200 in total per hour, counted in the database. The photo is parsed and classified in memory and never stored (it is sent to the Anthropic API for the Claude check). **Photo check (lead decision, November 8):** the ONNX model and Claude check each photo with the camera worker's agree rule. The model runs first, and Claude is asked only when the model confirms the type; `medium` needs both to agree (for `blocked`, Claude answers yes to "Is the walking path in this photo blocked?" and the model's class is not `unusable`, because no model class can confirm a blocked path), and anything else is `low` with the reported type kept. `app.state.photo_checker` is a `reports.PhotoChecks`: the model when `MODEL_FILE` or `MODEL_URL` is set with `MODEL_LABELS` (a path or an HTTPS URL; its `sha256` must equal `MODEL_SHA256`) and `MODEL_SHA256` (optional `MODEL_MIN_SCORE`), and Claude when `ANTHROPIC_API_KEY` is set (optional `REPORTS_AI_MODEL`, default `claude-haiku-5-5`, with the report-photo prompt `ClaudeCheck(..., subject="report_photo")`). With only one, it checks alone and logs a warning; every response lists the checks that answered in `photo.checked_by` (`model`, `claude`). Render installs `cloud/requirements-photo-check.txt` (onnxruntime, Pillow, anthropic only); the lead's dashboard steps are in [docs/DEPLOY.md](docs/DEPLOY.md) section 6. The model's accuracy on phone photos is not measured (it was trained and tested on road-camera frames). `web/src/components/report-button.tsx` is the floating Report button (mounted on the planner by OP-301).
- **Camera worker hosting (November 8):** the OP-204 worker runs on one team laptop during the demo, not on Render, because Render's free plan has no background workers. It runs on Tatsat's laptop, including on demo day; the steps are in [docs/DEPLOY.md](docs/DEPLOY.md#4-camera-worker-on-demo-day). Its posts every 5 minutes also keep the free API awake.
- **Saved routes and push alerts (OP-304):** `cloud/app/saved_routes.py` (table `saved_route` and the change rules), `cloud/app/push.py` (table `push_subscription`, the pywebpush sender and `RouteWatcher`) and `cloud/app/push_api.py` (the endpoints below). A saved route belongs to an anonymous `client_id` (16 to 64 URL-safe characters the browser generates and keeps); at most 10 saved routes and 5 push subscriptions per client (429 beyond). Saving computes the usual route (edges and ETA) at once. Every 15 s the watcher reads the hazard map, diffs it with the last one and rechecks only routes whose current or usual edges changed, plus impossible routes and newly saved ones. It pushes when the best route's edges change, no route is possible, or the ETA rises by 20% or more over the last notified ETA, and sends "Your usual route to <name> is clear again" once the usual route is open and within 5% of its saved ETA. A better route while the usual one is still fine is recorded without a push. At most one push per saved route every 120 s; a held push is sent when the window passes, unless the route recovered before the browser heard of it. After startup (or a free-plan wake-up) it waits 120 s for the scheduler, then rechecks every route once. A 404 or 410 from the push service deletes the subscription; other failures count, and it is dropped after 5. Without `VAPID_*` keys it logs once and every send is a no-op. The watcher runs in its own thread and never blocks or breaks `/api/route`; `SCHEDULER=off` keeps it idle (tests call `tick()`).
- **Alerts page and push client (OP-306):** `web/public/sw.js` shows a push and opens its `url` on click (it has no fetch handler and caches nothing, so an alert never shows an old route as current). `web/src/lib/push.ts` asks for notification permission only from a button press, subscribes with the key from `GET /api/push/public-key`, posts the subscription with the shared `client-id.ts` id, and re-posts it when `/alerts` opens. iPhone and iPad in Safari report `needs_install` ("Add to Home Screen to get alerts"); a home-screen app keeps its own storage, so it has its own id and saved routes. "Turn off alerts" unsubscribes in the browser only: the API has no unsubscribe endpoint and drops the row on the next 404 or 410 from the push service. `web/public/manifest.webmanifest` and the icons make the app installable.
- **Matching (OP-206):** `cloud/app/matching.py` builds that `HazardMap` from the store's active items. The first route request builds it; after that it is rebuilt at most every 30 s in a background thread, so requests never wait, and a failed rebuild (for example a database error) keeps the last good map. A point snaps to the nearest edge within 30 m; a line matches each edge with at least half its length, or at least 25 m of it, inside a 15 m buffer; polygons, 311 neighbourhood advisories (`snow_complaints`) and `weather` items attach as areas and change no edges. Indoor items (LRT elevators and escalators) are never snapped to sidewalks, because the City gives station points; an elevator outage blocks only edges from a reviewed mapping (OP-106), otherwise it is kept as a station warning. Each hazard keeps the item's `id` and `effect`; routing applies the effect per profile.
- **Route report (OP-208):** every route from `GET /api/route` carries `report`: each known item `on_route` (matched to a path the route uses, or an area the route passes through), `nearby` (within 50 m of the route line) or `avoided` (on the plain shortest route but not this one, at any distance). Groups come in a fixed order and are always present: Indoor, Sidewalks and paths, Roads and crossings, Weather and ice (Transit only when it has items). Each item has its source and attribution, title and detail, confidence, `observed_at` (when OpenPath last confirmed it with its source, not when the source changed it), the source's dates, a source link, its distance from the route and, for station-level or area items, a `note` on why it cannot change routes. A `weather` item the matcher keeps as an area (the black-ice watch, which the `weather_watch` source will publish as one item over the network's bounding box) is `on_route` for every route. 311 neighbourhood advisories are still points, so they follow the 50 m rule until they get neighbourhood polygons (Phase 3). `cloud/app/report.py` builds the report from one or more `ItemIndex`: the matcher builds one with every match. A hazard with no record is still listed from what the hazard carries. The report can never break matching or routing: if the index build fails, the match keeps its hazards with no index, and if a route's report fails, it falls back to the hazards that route met (both logged). `routing_api.get_report_items` adds the index of OP-205's camera ice risks (`CameraIceHazardProvider.items`) and takes the matcher from its `base`.
- **Station devices (OP-313):** each LRT outage's `detail` (`cloud/app/adapters/lrt_devices.py`) adds the station's other elevators and escalators that are in service, by type and the City's location label, from the daily device list (`javh-pjj2`) the adapter keeps grouped by station (last good list kept). In service means not listed in the outage feed (`snws-u3zx`); the list's own status is ignored. It says "Use the <label> <type> instead" only for an in-service device with the same label (never an escalator in place of an elevator), "No other elevator at <label> is in service." when none shares the label, and stays within 500 characters. Without a usable device list the outages still post with the plain `<station>, <location>` detail. No schema or config change.
- **Live updates (OP-303):** `WS /ws` (`cloud/app/ws.py`, hub on `app.state.live`) sends `source_status` after every scheduler run, `obstruction_changed` (items added and removed between hazard rebuilds) and, for the one route a connection subscribes with `{"type": "subscribe", "route": {"from": [lat, lng], "to": [lat, lng], "profile": "wheelchair"}}`, `route_changed` when a rebuild gives it different paths, no route, or an ETA at least 20% longer, with one sentence to announce; a heartbeat goes out every 25 s. It attaches through `HazardProvider.on_rebuild` and `Scheduler.add_listener`, which OP-304 can use too; re-routing runs in a worker thread and never touches `/api/route`. The web hook `useLiveUpdates` (`web/src/lib/live.ts`) and `LiveStatus` (`web/src/components/live-status.tsx`) are mounted on the planner by OP-301; the speech toggle is stored under `openpath.speak`, and the WebSocket message types in `web/src/lib/live-types.ts` are hand-written because OpenAPI does not cover WebSockets.
- **Shutdown order:** the lifespan (`stop_store_users` in `cloud/app/main.py`) stops the scheduler, the push watcher, the live hub and the hazard providers, in that order, and disposes the engine only when all of them report idle. Each `stop()` waits, with a timeout, for the worker thread it started, because closing in-memory SQLite's one shared connection under a running call crashed CI with a segmentation fault. Run store calls from a new background thread through `cloud/app/threads.py` (`WorkerThreads`), not `asyncio.to_thread`, and add its `stop()` to that list.
- **City dashboard and camera readings (OP-310, OP-309):** `GET /api/priority` (`cloud/app/priority.py`) ranks every active obstruction clear-first: `score = severity x (1 + saved routes affected) x (1 + pedestrian weight / 1000)`. A saved route (OP-304) is affected when the obstruction's matched edges (from `app.state.hazard_provider`) are on its current, usual or plain route (the shortest way for its profile with no hazards, cached per route; at most 2 s of new ones per request). `severity = (1 + profiles the effect stops) / (1 + profiles)` from `config/profiles.json`; area-wide items score 0. The pedestrian weight adds City counters within 400 m, scaled down linearly with distance, from `cloud/data/pedestrian_counts.json` (the median day of the last 28 days of `sw7k-ptx8`, bicycle-only counters left out), which `python scripts/build_pedestrian_counts.py` rebuilds with one official City API read (`--fixtures` builds offline; commit the result). Each item carries `why`, a plain sentence. `/city` (`web/src/components/city/`) shows the ranked rows (25 per page on a laptop, 10 on a phone), filters, CSV export of the filtered rows, `/api/status` per source, the map with barrier markers and camera markers from `GET /api/cameras`, and a "Road cameras" list. 511 Alberta has no page for a single camera, so each camera links to its camera list (`https://511.alberta.ca/cctv`) and is named from the recorded official metadata (`web/src/lib/camera-places.ts`); camera images are never shown. `city-map.tsx` takes MapLibre from `web/src/lib/maplibre.ts`, which sets MapLibre 6's worker URL from the bundle; without it the base map does not draw.
- **In-memory SQLite (tests):** `make_engine("sqlite://")` is one pysqlite connection shared by every thread, and pysqlite breaks when two threads use it at once, so `SharedMemoryPool` (`cloud/app/db.py`) lends it to one thread at a time. A Session opened inside another Session on the same thread just goes one level deeper. A thread that waits more than 30 s raises `SharedConnectionTimeout`, which names the thread holding the connection. Close every Session and connection (use `with`), and never keep one open while waiting for another thread that uses the database: that deadlock now fails after 30 s. File SQLite (`make dev`) and Postgres (Render) are unchanged.
- **Monitoring and runbook (OP-402):** [docs/RUNBOOK.md](docs/RUNBOOK.md) gives the symptom, check and fix for each known failure. `make demo-check` (`scripts/demo_check.py`) prints `ok`, `warn` or `FAIL` per dependency with read-only requests only (`--mode replay`, `--api`, `--web`, `--only`). `.github/workflows/uptime.yml` runs its health, status and web checks against production every 10 minutes (decision D-1: no new account); a red run emails the lead. `create_app` installs `cloud/app/logging_setup.py`: one line per record, `<UTC time> <LEVEL> <logger> <message>`, or JSON with `LOG_FORMAT=json`; the API's own loggers log at `LOG_LEVEL` (default `INFO`, so `CHANGE` lines now reach Render's log).
- **Backups (OP-405):** `scripts/db_backup.py --yes` dumps `DATABASE_URL` (`pg_dump --format=custom`, or the SQLite backup API) with a manifest of its SHA-256 and row counts; `scripts/db_restore.py --yes <file>` checks and restores it (`pg_restore --clean --if-exists --single-transaction`). `.github/workflows/backup.yml` zips the replay snapshot nightly (its second place) and, with the repository secrets `DATABASE_URL` and `BACKUP_PASSPHRASE`, uploads the encrypted dump and runs a restore drill. Render's free Postgres has no backups of its own. Only SQLite restores have been run; see RUNBOOK section 4.3.
- **Security and privacy review (OP-401):** every public write (`POST /api/reports`, `/api/push/subscribe`, `/api/push/test`, `/api/saved-routes` and `DELETE /api/saved-routes/{id}`) is limited per connection and minute by `cloud/app/rate_limit.py` (`RATE_LIMIT_PER_MIN`, default 30, `0` off; keyed by the first `X-Forwarded-For` hop, else the socket; 429 with `Retry-After`), and the keyed `POST /api/ingest/camera` gets ten times that after its key check. `cloud/app/security.py` adds the security headers to every API response (`Cache-Control: no-store` on writes, HSTS only behind HTTPS); the web app's headers and Content-Security-Policy are in `web/next.config.mjs`. `cloud/tests/test_privacy_inventory.py` pins the database inventory to the privacy page and checks that no image is written anywhere. The testing-only `POST /api/admin/override` (OP-406) takes `X-Admin-Key` with the same constant-time check, then the keyed limit. Audits, what is protected and the follow-ups: [docs/SECURITY.md](docs/SECURITY.md).
- **Technical write-up and changelog (OP-413, OP-403):** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the code as it is (the route, ingest, camera and live paths, retention, hosting, failure behaviour) and ends with a list of places where the code differs from this file and PLAN.md. Each decision already made has an ADR in `docs/adr/` (`0001` to `0011`). [docs/DEVPOST_TECH.md](docs/DEVPOST_TECH.md) is the Devpost's technical section, with every number traced to a file or a measurement. [CHANGELOG.md](CHANGELOG.md) is built from the merged pull requests; `v1.0` is tagged at the feature freeze (OP-403).

## Where to Find Context

| You need | Read |
| --- | --- |
| Product, scope, data sources, vision model, routing rules, architecture, contracts, production checklist, risks, open decisions | [PLAN.md](PLAN.md) (sections 1 to 17) |
| Every user story with its owner and story points | [docs/USER_STORIES.md](docs/USER_STORIES.md) |
| Acceptance criteria and live status of a story | GitHub issues `OP-101` to `OP-414` (#29 to #81) |
| Team workflow and Definition of Done | [GUIDELINE.md](GUIDELINE.md) |
| CI checks, merge rules and deployment | [docs/CI_CD.md](docs/CI_CD.md) and [docs/DEPLOY.md](docs/DEPLOY.md) |
| What to do when something breaks, uptime checks, backups and restores | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| Data source details and licences (created in Phase 1) | `docs/DATA_SOURCES.md` |
| What is protected and how (keys, CORS, rate limits, headers, no images), the dependency audits and the security follow-ups | [docs/SECURITY.md](docs/SECURITY.md) |
| How the parts fit together: system diagram, data flows, retention, hosting, failure behaviour | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Why each decision was made (one ADR per decision, `0001` onwards) | [docs/adr/](docs/adr/) |
| What changed in each phase, by pull request | [CHANGELOG.md](CHANGELOG.md) |

`PLAN.md` section 14 and `docs/USER_STORIES.md` are generated from the backlog. To change a story, edit its GitHub issue and tell the lead; do not edit those sections by hand.

## Team

| Person | GitHub | Label |
| --- | --- | --- |
| Tatsat (lead and scrum master) | `tatsatshah05` | `person: Tatsat` |
| Ipsa (reviewer) | `ipsamanhas` | `person: Ipsa` |
| Estella | `estella1221` | `person: Estella` |
| Kaleb | `kalebpham` | `person: Kaleb` |
| Kashish (reviewer) | `kashishgupta2911` | `person: Kashish` |
| Matthew | `Maosro` | `person: Matthew` |

Issues also carry `phase N: ...`, `points: N`, `priority: must|should|could` and `epic: ...` labels, and one of four phase milestones.

## Phases

The team moves through four phases together. Each person owns about 8 story points per phase.

| Phase | Hours | Checkpoint |
| --- | --- | --- |
| 1. Data Foundation | 0–10 | Every Must source flows into `/api/obstructions` with freshness on `/api/status`; the walking network loads; CI and deploys work |
| 2. Vision and Routing | 10–22 | The model meets its target on unseen cameras; `/api/route` returns profile routes with alternates and a full report |
| 3. Web App and Alerts | 22–34 | On a phone: plan, report, live update, push alert, Google Maps hand-off |
| 4. Production and Launch | 34–48 | Release checklist done; freeze at hour 40; `v1.0` tagged; submit by hour 46 |

## Decisions Already Made

Do not reopen these without the lead.

- **No hardware.** Everything runs on public data, public cameras and community reports.
- **No user accounts in version 1.** Settings and saved routes stay on the device, and push alerts use an anonymous ID.
- **Stack:**
  - Web app: Next.js on Vercel
  - API: FastAPI and Postgres on Render
  - Vision: an ONNX worker that runs on Tatsat's laptop during the demo, posting to the API on Render. Render's free plan has no background workers (decided November 8).
  - Routing: SciPy on a prebuilt OpenStreetMap network
- **Official data access only.** No scraping. City camera streams are used only with written permission.
- **No stored images,** ever.
- **Naming:** the product is OpenPath, and story IDs are `OP-<id>`.

Open decisions D-1 to D-5 are in PLAN.md section 16.

## How to Work a Story

1. Open the story's issue (`OP-<id>`). Read the user story, the acceptance criteria and the dependencies. Check that the dependencies are merged or can be mocked.
2. Branch from the current phase branch (`phase_1` during Phase 1) as `op-<id>-<short-name>`. Never branch from `main` or open story pull requests into it.
3. For any change that touches more than one file, plan first. Stay inside the acceptance criteria, and do not build later-phase stories.
4. Add tests and run `make test`.
5. Open a pull request into the story's phase branch (`OP-1xx` into `phase_1`, `OP-2xx` into `phase_2`, and so on), titled `OP-<id>: <story title>`, with `Closes #<issue number>`. Work outside a story uses a `docs:`, `chore:`, `ci:` or `fix:` title. CI checks the title, the link, the files, the Python code and the web app.
6. The pull request merges only when `CI passed` is green and one reviewer approves: Tatsat (`@tatsatshah05`), Ipsa (`@ipsamanhas`) or Kashish (`@kashishgupta2911`). Merges are squash-only. Merges into a phase branch deploy to staging, and phase releases into `main` deploy to production.
7. If the story needs a change to a shared contract, stop and ask the lead.
8. If you add a command, an endpoint, an environment variable or a decision, update this file in the same pull request.

## Priorities for Every Task

1. **The live demo must never fail.** Reliability beats features. Every source has a timeout, retries and a last-good cache. A failed source never clears data and never breaks routing.
2. **Contracts are shared.** Change `cloud/app/schemas.py`, `config/` or the API only after the team agrees, and record the decision in PLAN.md. If a story seems to need a contract change, stop and say so.
3. **Must, then Should, then Could** (PLAN.md section 5). Feature freeze at hour 40; bug fixes only after that.
4. **Everything runs without keys.** `make dev` runs the full app locally from recorded fixtures. Never break it.
5. **Small, working steps.** Plan first for any change that touches more than one file. Run `make test` after changes. `main` always passes.
6. **Never invent numbers.** Measure them (model card, `docs/PERFORMANCE.md`) or label them as targets or assumptions.

## Architecture

```
Sources ── City of Edmonton open data (Socrata), Environment Canada + Open-Meteo,
           511 Alberta cameras (API key), City cameras (only with written permission),
           community reports, OpenStreetMap (prebuilt)
   │
cloud/   FastAPI on Render + Postgres
         ingest scheduler: one adapter per source → Obstruction store (upsert, expiry, freshness)
         matching: obstructions → walking-network edges (points 30 m, lines 15 m buffer, elevators via config)
         routing: compact arrays + SciPy Dijkstra, 4 profiles, best + 2 alternates, report, Maps URLs
         live: WS /ws events; Web Push for saved routes
vision/  worker on a team laptop during the demo: camera frames → ONNX road-condition model → POST /api/ingest/camera
         training (PyTorch) runs on a laptop or Colab, never on Render
web/     Next.js on Vercel: planner, route report, alerts (PWA), city dashboard, sources, about, privacy, demo
```

## Repo Layout (target; created in Phase 1)

```
PLAN.md  AGENTS.md  CLAUDE.md  GUIDELINE.md  README.md  Makefile  render.yaml  .env.example
.github/   workflows/ci.yml  CODEOWNERS  pull_request_template.md  ISSUE_TEMPLATE/
cloud/     app/{main,schemas,db,store,network,obstructions_api,scheduler,status,matching,routing,report,search,weather,push,ws}.py
           app/adapters/  one file per source   data/network.npz (prebuilt walking network, committed)   tests/ (fixtures in tests/fixtures/)
vision/    train.py  worker.py  tools/label.py  LABELS.md  MODEL_CARD.md  tests/
web/       src/app/{page.tsx, alerts/, city/, sources/, about/, privacy/, demo/}  src/lib/api-types.ts (generated)  public/sw.js
config/    profiles.json  sources.json  elevators.json  matching.json  places.json  reports.json
scripts/   build_graph.py  record_snapshot.py  demo_check.py
docs/      DATA_SOURCES.md  ARCHITECTURE.md  RUNBOOK.md  PERFORMANCE.md  adr/  design/
```

## Contracts

This is a summary; PLAN.md section 10 and the code are the source of truth.

**Obstruction:** `id, source, source_ref, category, kind, effect, title, detail, geometry (GeoJSON), confidence, starts_at, ends_at, observed_at, expires_at, url, attribution`.
- `category`: `indoor | sidewalk | road | weather | transit`
- `kind`: `elevator_out | escalator_out | sidewalk_closed | road_closed | trail_closed | trail_caution | plow_passed | snow_complaints | snow | ice_risk | blocked`
- `effect`: `blocked | snow | ice_risk | caution`; `confidence`: `high | medium | low`

**Effects by profile** (`config/profiles.json`): cost = length × multiplier; time = length ÷ speed × multiplier; "avoid" removes the edge.

| Effect | Wheelchair | Walker or cane | Low vision | Standard |
| --- | --- | --- | --- | --- |
| `blocked` | avoid | avoid | avoid | avoid |
| `snow` | avoid | × 3 | × 2 | × 1.5 |
| `ice_risk` (local only) | × 3 | avoid | × 3 | × 1.5 |
| `caution` | report only | report only | report only | report only |
| stairs (`highway=steps`) | avoid | avoid | × 1 | × 1 |
| speed m/s (assumed) | 1.0 | 0.9 | 1.0 | 1.3 |

- `blocked` wins when effects overlap; otherwise the largest multiplier applies.
- The city-wide weather watch and 311 neighbourhood advisories are `caution` only: they cover every path equally, so they never reroute.

**Black-ice rule:**
- **Watch:** active when (air ≤ 0 °C or ground ≤ 0 °C) and at least one of: precipitation in the last 6 h, air above 0 °C in the last 12 h, or air within 1 °C of the dew point.
- **Camera:** an `ice` reading, or a `wet` reading while the watch is active, becomes `ice_risk` at medium confidence on crossings within 50 m of the camera.
- **Wording:** wet without freezing is ignored. Always say "ice risk", never "black ice detected".

**Routing:**
- Alternates use the penalty method; keep a route only if it shares under 70% of its length with routes already kept. Return at most 3.
- **Google Maps:** `https://www.google.com/maps/dir/?api=1&origin=LAT,LNG&destination=LAT,LNG&travelmode=walking&waypoints=LAT,LNG|LAT,LNG`, with at most 3 waypoints placed where our route departs from the plain shortest route.
- **Apple Maps:** `https://maps.apple.com/?saddr=LAT,LNG&daddr=LAT,LNG&dirflg=w`, and the UI notes that it may not keep the detour.

**API:**
- **Health:** `GET /health` returns `{"status": "ok", "version": "<git commit>", "mode": "live"}`. `version` comes from `RENDER_GIT_COMMIT` (`dev` locally), and the deploy job waits for it. `mode` is `DATA_MODE` (`live` or `replay`); the web app shows the Replay badge when it is `replay`.
- **Read:** `GET /health`, `/api/status`, `/api/obstructions?bbox=`, `/api/route?from=&to=&profile=&mode=walk|walk_lrt`, `/api/search?q=`, `/api/weather`, `/api/cameras`, `/api/priority`, `/api/push/public-key`, `/api/saved-routes?client_id=`.
- **Write:** `POST /api/reports`, `/api/push/subscribe`, `/api/push/test`, `/api/saved-routes`, `DELETE /api/saved-routes/{id}?client_id=`, `POST /api/ingest/camera` (`X-Worker-Key`), `POST /api/admin/override` and `DELETE /api/admin/override[/{source_ref}]` (`X-Admin-Key`, testing only: a labelled `Test override: ...` item under source `admin_override` with a short `ttl_s`; it rebuilds the hazard map at once).
- **Alerts (OP-304; the extra endpoints are a Phase 3 decision, PLAN.md section 16):** `POST /api/push/subscribe` takes `{client_id, subscription: {endpoint, keys: {p256dh, auth}}}` (the browser's `PushSubscription.toJSON()`; `expirationTime` is ignored, `endpoint` must be https) and answers `{client_id, created, subscriptions, push_enabled}`. `POST /api/saved-routes` takes `{client_id, name, from: {lat, lng}, to: {lat, lng}, profile}` and answers 201 with a saved route: `{id, client_id, name, from, to, profile, created_at, status: ok|changed|impossible, eta_min, baseline_eta_min, last_checked_at, last_push_at, url}`. `GET /api/saved-routes?client_id=` answers `{client_id, routes: [...]}`; `DELETE /api/saved-routes/{id}?client_id=` answers 204, or 404 when the route is not that client's. `POST /api/push/test` takes `{client_id}` and answers `{client_id, sent, subscriptions, push_enabled}` (404 without a subscription, 503 without VAPID keys). `GET /api/push/public-key` answers `{public_key, push_enabled}`, so the web app needs no build-time key. A push payload is `{title, body, url: "/?from=lat,lng&to=lat,lng&profile=..&saved=<id>", tag: "route-<id>", saved_route_id, status: changed|impossible|clear, eta_min}`; the test push has `tag` `openpath-test`, `status` `test` and `url` `/alerts`.
- **Camera readings (OP-213, agreed November 8):** `POST /api/ingest/camera` takes one reading per camera view in the shape agreed on issue #47: `source` `camera_model`, `source_ref` `511:<camera_id>:<view_id>`, `camera_id`, `view_id`, `latitude`, `longitude`, `label` (`dry|wet|snow_partial|snow_covered|ice|unknown`), `score`, `reason`, `observed_at`, `expires_at`, `model_version`, `mode`, `attribution`. One row per view in `camera_reading` (`cloud/app/camera_readings.py`); an older reading never replaces a newer one; `source_ref` must match the ids; `expires_at` is at most 2 h after `observed_at`; `mode` must equal the API's `DATA_MODE` (else 409). `GET /api/cameras` lists the latest unexpired readings as typed GeoJSON. Readings are not obstructions; OP-205 turns them into `ice_risk` where the rule applies.
- **Camera status (OP-412):** `GET /api/status` has `cameras` (`CamerasStatus`, null only when the camera table cannot be read): `state` `live | stale | none`, `ice_source` `cameras | weather_watch`, `readings`, `usable`, `views_total`, `newest_observed_at`, `newest_age_s`, `oldest_age_s`, `model_versions`, `score_min`, `score_median`, `labels`, `watch_active`, `live_within_s`. `LIVE_WITHIN` = 15 minutes and `UNKNOWN_LABEL` = `unknown` in `cloud/app/camera_status.py`; the web app re-reads the block every `CAMERA_STATUS_REFRESH_MS` = 60 s. `GET /api/cameras` carries the same block as `summary`. Both are additive.
- **Live:** `WS /ws` with events `obstruction_changed`, `route_changed` and `source_status`.

## Data Source Rules

- **Access:** official access only. Use the City open data API (`data.edmonton.ca/resource/<id>.json`, with `SOCRATA_APP_TOKEN`), the 511 Alberta API with our key (10 calls per 60 s), Environment Canada MSC GeoMet, and Open-Meteo. Never scrape websites or call private endpoints. Use City camera streams only after the City gives written permission.
- **Inventory:** dataset IDs, cadences and licences live in PLAN.md section 6 and `docs/DATA_SOURCES.md`. Live LRT status comes from the outage feed (`snws-u3zx`), never the master list, which lags.
- **Fixtures:** every adapter has recorded fixtures, and tests never call the network.
- **Long text:** `detail` is at most 500 characters, and the scheduler skips an item that breaks the contract. An adapter that copies free City text into `detail` shortens it with `clip_detail` (`cloud/app/adapters/_text.py`: cut at a word, marked with "...", keeping a short ending such as an end date whole) and never drops the item for a long detail; the item's `url` links the full text. Items are skipped only for invalid data, such as a bad geometry or a missing id.
- **Attribution:** attribute every source on `/sources` and on every item, as each licence requires.
- **Request time:** never run OSMnx on Render, and never call an external geocoder at request time. The network and the address index are prebuilt.
- **Walking network:** `cloud/app/network.py` loads `cloud/data/network.npz` (compact NumPy arrays: nodes, undirected edges with flags for stairs, elevators, crossings and sidewalks, and edge polylines). Routing works on node and edge indices, never on OSM ids. Weight 0 in `length_matrix()` means the edge is removed.

## Vision Rules

- **No stored images.** Process frames in memory and never store them. Keep only labelled training images where the camera's terms allow it, and never an image in which a person can be identified. Classify community photos, then discard them.
- **Classes:** `dry`, `wet`, `snow_partial`, `snow_covered`, `ice` and `unusable`. Unusable frames give `unknown`, never `dry`.
- **Stability:** a reading changes only after 2 agreeing frames in a row.
- **Testing:** test cameras never appear in training. Publish measured results only, in `vision/MODEL_CARD.md`.
- **Fallback:** if cameras or the model fail, fall back to the weather watch and say so in the UI.

## Commands

OP-201 dataset audit: `python -m vision.tools.dataset --images vision/data/raw --labels vision/data/labels_primary.csv --out vision/data/dataset`; see the [dataset workflow](docs/ESTELLA_OP201.md) for blind second labelling and source permissions. Preserve AI-assisted labeling provenance; non-blind review is not independent agreement.

OP-201 → OP-202: pass only `--labels vision/data/labels_primary.csv` and the frozen IDs via `--test-cameras $(cat vision/data/dataset/test_cameras.txt)` to `vision.train` (when OP-202 is available); its default split differs and does not read the registry. Do not glob `labels_*.csv` or train on blind second-label CSVs. The audit reports `split_class_counts` and exports `test_cameras.txt`.

OP-201 collection (keeping frames follows the lead's November 8 decision in #132): `python -m vision.tools.collect --limit 6 --reviewer Estella` previews each image in memory; only `k` retains a privacy-reviewed frame in ignored `vision/data/raw/`. `--views all` also fetches each camera's other enabled views, through the same rate limiter and host check, into `vision/data/raw/view_<view_id>/`. `--plan` lists cached camera IDs offline. The UTC filename timestamp is retrieval time, not verified capture time.

Issue #44 permits additional provincial snow/ice training examples: `python -m vision.tools.collect --scope alberta --limit 20 --reviewer Estella` (optional `--camera-ids ...`); `--plan` remains offline. The actual reviewer is required for live collection; wait a minute between collector runs because each starts a new local rate limiter. This training-data selection does not change Edmonton routing coverage.

OP-306 app icons: `python scripts/build_icons.py` redraws `web/public/icons/*` and `web/public/apple-touch-icon.png` with Pillow (from `vision/requirements.txt`); commit the result. `python scripts/make_icons.py` (OP-411) runs it and also writes the favicon and Apple icon that Next links from every page (`web/src/app/icon.svg`, `icon1.png`, `apple-icon.png`); use it for any icon change.

OP-304 Web Push keys: `python -m cloud.app.push --generate-keys --subject mailto:<team address>` prints `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` and `NEXT_PUBLIC_VAPID_PUBLIC_KEY` lines for the Render and Vercel dashboards ([docs/DEPLOY.md](docs/DEPLOY.md#5-web-push-keys-op-304)). Never commit them.

OP-109's standalone weather app: `.venv/bin/python -m uvicorn cloud.app.weather_app:create_app --factory --port 8001`; replay requires `DATA_MODE=replay` and `REPLAY_SNAPSHOT` pointing to the snapshot described in [Estella's weather guide](docs/ESTELLA_PHASE1.md).

These live in the root Makefile and are available after OP-102.

| Command | What it does |
| --- | --- |
| `make setup` | Creates `.venv` with every `cloud/` and `vision/` requirements file (as CI installs them), runs `npm ci` in `web/` |
| `make dev` | API on :8000 (SQLite, recorded fixtures, no keys) + web on :3000 |
| `make web` | Only the web app on :3000, pointed at the API on :8000 |
| `make test` | `pytest` for cloud and vision, plus `npm run lint` and `npm run typecheck` in `web/` |
| `make graph` | Installs `scripts/requirements-graph.txt`, then rebuilds `cloud/data/network.npz` with OSMnx (local only, then commit). macOS or Linux for now: `scripts/build_graph.py` uses the Unix-only `resource` module |
| `make lrt` | Downloads the ETS GTFS zip once (City of Edmonton open data) and rebuilds `cloud/data/lrt.json`, the LRT station graph for walk and LRT trips (then commit it; the zip is never committed). `python scripts/build_lrt_graph.py --gtfs <zip>` rebuilds from a zip already downloaded |
| `make types` | Regenerates `web/src/lib/api-types.ts` from `/openapi.json` (with `npx openapi-typescript`) |
| `make record` | Records a replay snapshot of every source into `snapshots/<UTC time>/` (JSON only, no images; `RECORD_FLAGS="--cameras-from <API URL>"` adds camera readings). See [docs/OP-305.md](docs/OP-305.md) |
| `make worker` | Runs the vision worker locally |
| `make e2e` | Playwright end-to-end tests (OP-406): starts the API in replay mode and the production web build, runs `web/e2e/`, stops both. `E2E_FLAGS="--skip-build -- --grep @smoke"` passes options (`--skip-build` reuses `web/.next`; arguments after `--` go to `playwright test`). In `web/`, `npm run e2e` and `npm run e2e:smoke` run the suite against `E2E_WEB_URL` and `E2E_API_URL` that are already up |
| `npm run a11y` (in `web/`) | The OP-408 accessibility audit: axe, keyboard, focus, the announced route change, zoom, reduced motion, hazard marks and touch targets on every page, against the API and web app at `E2E_API_URL` and `E2E_WEB_URL` (replay mode, `E2E_ADMIN_KEY` set), into `web/test-results/a11y/`. `A11Y_ONLY` and `A11Y_PAGES` narrow a run. See [web/ACCESSIBILITY.md](web/ACCESSIBILITY.md) |
| `make bench` | Measures `GET /api/route` latency (p50, p95, max and errors per profile; sequential, then 4 workers) for 20 fixed trips against the API on :8000, with no keys; `BENCH_FLAGS="--base-url https://... --json out.json"` for another instance. Polite by default (4 requests/s). See [docs/PERFORMANCE.md](docs/PERFORMANCE.md) (OP-404) |
| `make demo-check` | Checks every demo dependency against production and prints `ok`, `warn` or `FAIL` per line (OP-402); `DEMO_CHECK_FLAGS="--mode replay --api http://localhost:8000 --web http://localhost:3000"` checks `make dev` |
| `make bench` | Measures `GET /api/route` latency (p50, p95, max and errors per profile; sequential, then 4 workers) for 20 fixed trips against the API on :8000, with no keys; `BENCH_FLAGS="--base-url https://... --json out.json"` for another instance. Polite by default (4 requests/s). See [docs/PERFORMANCE.md](docs/PERFORMANCE.md) (OP-404) |
| `make e2e` | Playwright end-to-end tests (OP-406): starts the API in replay mode and the production web build, runs `web/e2e/`, stops both. `E2E_FLAGS="--skip-build -- --grep @smoke"` passes options (`--skip-build` reuses `web/.next`; arguments after `--` go to `playwright test`). In `web/`, `npm run e2e` and `npm run e2e:smoke` run the suite against `E2E_WEB_URL` and `E2E_API_URL` that are already up |
| `make lint` | `ruff check`, `ruff format --check` and `npm run lint` (the same lint CI runs) |
| `make format` | `ruff check --fix`, then `ruff format` |
| `make build` | Production build of the web app (`npm run build`) |

`make record` exits with 1 if any source failed (the others are still written; `manifest.json` lists each source's status). `python scripts/record_snapshot.py --weather-archive YYYY-MM-DD` records a past night from Open-Meteo's archive instead of today's weather. `make worker` now runs a network-free, explicitly synthetic OP-204 replay; see below for live prerequisites. While `web/` is missing, the web steps of `make setup`, `make test` and `make lint` are skipped, `make dev` starts only the API, and `make web`, `make build` and `make types` stop with an error. The Makefile works on Windows (GNU make, for example `winget install ezwinports.make`), macOS and Linux.

`make dev` and `make web` run `scripts/dev.py`. It removes API keys from the environment and sets `DATA_MODE=replay` with `REPLAY_SNAPSHOT=cloud/tests/fixtures`, so adapters must read their fixtures in replay mode. If one server stops (for example because its port is in use), the other is stopped too and `make dev` fails with that server's exit code.

OP-202 trains the road-condition model on a laptop or Colab, never on Render or in CI: `pip install -r vision/requirements.txt -r scripts/requirements-train.txt`, then `python -m vision.train --images vision/data/raw --labels vision/data/labels_*.csv --out vision/data/runs/v1` (see [vision/README.md](vision/README.md#training)). It writes `model.onnx`, `labels.json`, `split.json` and `metrics.json`; publish `model.onnx` and `labels.json` as a GitHub release asset and set `MODEL_URL`, never commit them. Its end-to-end tests skip where PyTorch is not installed.

OP-203 evaluates a run on its unseen test cameras and regenerates `vision/MODEL_CARD.md`: `python -m vision.evaluate --run vision/data/runs/v1 --images vision/data/raw --labels vision/data/labels_*.csv` (needs onnxruntime, not PyTorch). It refuses a `model.onnx` that does not match the `sha256` in the run's `labels.json`, and records that hash on the card. Never edit the card's numbers by hand.

OP-107 offers a recorded preview until the shared app is integrated:

```bash
python -m cloud.app.adapters.construction --fixtures cloud/tests/fixtures/construction --at 2026-11-07T19:00:00Z
python -m pytest cloud/tests/test_construction.py cloud/tests/test_construction_review.py -q
```

Use `--live` instead of `--fixtures` for one official City API read, with optional
`SOCRATA_APP_TOKEN` from the shell (`.env.example` contains its blank template).
`--at` is replay-only and requires a timezone; `--local-timezone` defaults to
`America/Edmonton`. This preview prints output and does not write to the store.

OP-204 commands (defaults perform no network calls):

```bash
python -m vision.worker
python -m pytest vision/tests/test_worker.py vision/tests/test_worker_inference.py vision/tests/test_worker_review.py vision/tests/test_worker_ingest.py -q
```

The direct 511 JPEG response is verified. With OP-202's published `model.onnx`
(at `MODEL_URL`) and its `labels.json`, use
`python -m vision.worker --live --model-labels /path/to/labels.json --model-sha256 EXPECTED_SHA256 --min-score VALIDATED_SCORE`.
It uses existing `ALBERTA_511_KEY` and `MODEL_URL`, with `--interval-s` defaulting
to 300 seconds and an optional `--boundary` GeoJSON. `--once` runs one pass.
`DATA_MODE=replay` prohibits live fetching. Posting additionally requires
`--publish`, `CLOUD_URL` and `WORKER_KEY`; the API must run with `DATA_MODE=live`,
because it refuses a live reading in replay mode (409). `--once --publish` reports
delivery counts and fails only when posts were attempted but all failed; a single
pass cannot confirm a reading, so it normally makes zero attempts. A shared
one-minute cooldown follows a 511 HTTP 429; live startup uses OP-112's on-disk
metadata cache. Replay data is never posted. See [docs/OP-204.md](docs/OP-204.md)
for the labels.json format, expiry and remaining work.

A second model can add snow and ice readings beside the main model (lead decision,
November 8; `vision/snow_assist.py`): `--snow-model-file`, `--snow-model-labels` and
`--snow-model-sha256` go together, share `--min-score`, and the snow model's answer is
used only when it is `snow_partial`, `snow_covered` or `ice`. With `--ai-check`, Claude
must agree with the final label. `vision/MODEL_CARD.md` measures both models.

OP-205 checks: `python -m pytest cloud/tests/test_camera_ice.py cloud/tests/test_camera_ice_routing.py cloud/tests/test_camera_ice_review.py cloud/tests/test_camera_ice_app.py -q`.

## Environment Variables

Never commit values. Keep `.env.example` current. Nothing loads `.env.example` or a `.env` file: Render and Vercel take values from their dashboards, and `make dev` sets its own.

- **cloud:** `DATABASE_URL`, `ADMIN_KEY`, `WORKER_KEY`, `CORS_ORIGINS`, `SOCRATA_APP_TOKEN`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `DATA_MODE` (`live` or `replay`), `REPLAY_SNAPSHOT`, `REPLAY_AT`, `REPLAY_CLOCK` (`frozen` or `running`), `WEATHER_REPLAY`, `WEATHER_LAT`, `WEATHER_LNG`, `LRT_TRIPS`; for the report photo check (OP-308): `MODEL_URL` or `MODEL_FILE`, `MODEL_LABELS` (path or HTTPS URL), `MODEL_SHA256`, `MODEL_MIN_SCORE`, `ANTHROPIC_API_KEY`, `REPORTS_AI_MODEL`; logging (OP-402): `LOG_FORMAT` (`text` or `json`), `LOG_LEVEL` (default `INFO`)
- **GitHub repository secrets (OP-405):** `DATABASE_URL` (Render's External Database URL) and `BACKUP_PASSPHRASE` for `backup.yml`; the lead adds them, never in Git. The repository variable `DEMO_CHECK_MODE=replay` is set only while the API runs the replay fallback
- **cloud:** `DATABASE_URL`, `ADMIN_KEY`, `WORKER_KEY`, `CORS_ORIGINS`, `RATE_LIMIT_PER_MIN`, `SOCRATA_APP_TOKEN`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `DATA_MODE` (`live` or `replay`), `REPLAY_SNAPSHOT`, `REPLAY_AT`, `REPLAY_CLOCK` (`frozen` or `running`), `WEATHER_REPLAY`, `WEATHER_LAT`, `WEATHER_LNG`, `LRT_TRIPS`; for the report photo check (OP-308): `MODEL_URL` or `MODEL_FILE`, `MODEL_LABELS` (path or HTTPS URL), `MODEL_SHA256`, `MODEL_MIN_SCORE`, `ANTHROPIC_API_KEY`, `REPORTS_AI_MODEL`
- **vision:** `CLOUD_URL`, `WORKER_KEY`, `ALBERTA_511_KEY`, `MODEL_URL`, `ANTHROPIC_API_KEY` (for `--ai-check` on the worker laptop, and on Render for report photos; never in CI)
- **web:** `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_VAPID_PUBLIC_KEY`. The web app reads `NEXT_PUBLIC_API_BASE_URL` first and falls back to `NEXT_PUBLIC_API_URL` (`web/src/lib/api.ts`); `make dev` and docs/DEPLOY.md set both until the two agree.
- **end-to-end tests (OP-406):** `E2E_WEB_URL`, `E2E_API_URL`, `E2E_ADMIN_KEY` (read by `web/playwright.config.ts` and `web/e2e/`; `scripts/e2e.py` sets them, and the deploy job points the first two at production), `E2E_API_PORT` and `E2E_WEB_PORT` (the ports `scripts/e2e.py` starts the servers on; 8740 and 3140).

`REPLAY_SNAPSHOT` is a folder, not a file: one subfolder of recorded responses per source, as in `cloud/tests/fixtures/`. `REPLAY_AT` (replay only, ISO 8601 with a time zone, for example `2026-11-10T18:00:00Z`) freezes the app's clock (`app.state.clock`) so recorded closures stay active: the scheduler, the map (`GET /api/obstructions`), route hazards and `GET /api/cameras` all use it. `REPLAY_CLOCK=running` starts that clock at `REPLAY_AT` (or the snapshot manifest's `replay_at`) and lets it advance in real time; `REPLAY_CLOCK=frozen` freezes it at the manifest's time. `WEATHER_REPLAY=<name>` serves the weather recording in `<snapshot>/weather/<name>/`; `freeze_thaw` is the committed archive night. `CORS_ORIGINS` is comma-separated, and a `*` matches part of one host-name label (`https://openpath-*-<team-slug>.vercel.app`). It is not a security boundary; see [docs/DEPLOY.md](docs/DEPLOY.md).

## Conventions

- **Python 3.12:**
  - Type hints everywhere; Pydantic v2 and SQLModel; `ruff format` and `ruff check`; `pytest`.
  - Imports are rooted at the repository: `from cloud.app import db`, run as `uvicorn cloud.app.main:app` from the root. Tool settings live in the root `pyproject.toml`; dependencies in `cloud/requirements*.txt` and `vision/requirements.txt`.
  - Put units in names (`length_m`, `eta_min`, `air_c`).
  - Use UTC with ISO 8601 everywhere.
- **Logging:** one line per state change, for example `CHANGE city_lrt_devices:1234 new elevator_out Central/West Platform`.
- **TypeScript:**
  - Strict mode, no `any`. API types come only from the generated `api-types.ts`.
  - Small components, Tailwind, no extra state library.
- **Git:**
  - Branch `op-<id>-<short-name>` from `main`.
  - PR title `OP-<id>: <story title>` with `Closes #<issue>`.
  - One review before merge. Commit messages say what changed and why.
- **Replay:** `DATA_MODE=replay` shows a visible Replay badge on every page. Never present replayed data as live.

## Accessibility Requirements

These apply to the web app, and judges will check them.

- WCAG 2.2 AA contrast. Never colour alone: every obstruction kind has an icon, a pattern and a text label.
- Every route is also a text list of steps and a grouped text report, so the map is never the only source of information.
- Route changes are announced in an `aria-live="assertive"` region, spoken with `speechSynthesis` (user toggle) and vibrated where supported.
- Fully keyboard operable with visible focus, 44 px touch targets, `prefers-reduced-motion` respected, text resizable to 200%.

## Testing

Run `make test` from the repository root. Tests import code as `cloud.app.<module>`; the root `pyproject.toml` puts the repository on the import path.

- **cloud:**
  - Each adapter with recorded fixtures, including a failing source and a changed schema.
  - Matching with synthetic geometry.
  - Routing per profile: impassable edges, stairs, ETA maths, distinct alternates.
  - Black-ice rule branches, push triggers (pywebpush mocked), Maps URL builders.
- **vision:** label tool, worker hysteresis, unusable frames give `unknown`, and the ice rule.
- **web:** typecheck and lint on every PR; Playwright end-to-end on the critical path (OP-406, `web/e2e/`, `make e2e`): plan a route through the place search, open the route report (four groups, every item with a source), receive a seeded live change (`POST /api/admin/override`, announced in the `aria-live="assertive"` region), save a route and find it on `/alerts`; axe (WCAG 2.0/2.1 A and AA, 2.2 AA) on every page of the flows, failing on serious or critical violations; a read-only `@smoke` subset runs against production after each deploy.

## Do Not

- Add accounts, auth providers, new services or state libraries without the lead's sign-off. Version 1 has no user accounts (decided November 7).
- Commit secrets, `.env`, API keys, model weights or images.
- Scrape any website, use private endpoints, or use City camera streams without written permission.
- Fake data in the demo path. Override endpoints are for testing and are labelled.
- Change contracts silently, or "fix" a failing test by weakening it.
