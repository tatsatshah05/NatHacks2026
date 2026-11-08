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

- **OP-205:** camera `ice` (or `wet` while the watch is active) becomes `ice_risk` on the crossings within 50 m of the camera. `CameraIceHazardProvider` (`cloud/app/camera_ice_routing.py`) is `app.state.hazard_provider`: it wraps the matcher and adds those exact crossings, so camera risks never meet the matcher's area rule. See [docs/OP-205.md](docs/OP-205.md).

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
- **Deploys:** the one-time Render, Vercel and GitHub setup is in [docs/DEPLOY.md](docs/DEPLOY.md).
- **CI/CD is live, and every branch below is protected.** Every pull request needs the `CI passed` check and an approval from Tatsat, Ipsa or Kashish. See [docs/CI_CD.md](docs/CI_CD.md).
- **Phase branches:** Phase 1 work goes into the `phase_1` branch, which is the default branch. When the phase passes its checkpoint, `phase_1` is released into `main`, and `phase_2` starts from `main`.
- **Vision training (OP-202):** `vision/train.py` trains and exports the road-condition model on a laptop or Colab (never Render or CI); no real model exists yet, pending OP-201 labels and decision D-3. `vision/model_contract.py` defines `labels.json`, the hand-off to the worker (OP-204).
- **Old material:** the earlier hardware design (version 1, named PathSense) is archived in `docs/archive/v1-hardware/`. Ignore it for new work.

- **Scheduler and status (OP-103):** `cloud/app/scheduler.py` runs every source in `config/sources.json` on its cadence (10 s timeout, 3 retries, stale after 3 failures, a failed source never clears data) and `GET /api/status` reports freshness. `SCHEDULER=off` disables fetching (tests). In replay mode the adapters read the `REPLAY_SNAPSHOT` folder through a fake HTTP transport, so nothing touches the network.

- **Routing (OP-207):** `cloud/app/routing.py` runs Dijkstra on the prebuilt network with per-profile weights from `config/profiles.json` (a shared contract), returns up to 3 distinct routes with ETAs, text steps, hazards avoided and a reason, and serves `GET /api/route`. Hazards enter as a `HazardMap` (edge index -> `EdgeHazard`) that the matcher (OP-206) provides through `app.state.hazard_provider`. Each end snaps to the nearest node with a usable path; if no route is found because an end sits in a pocket cut off for that profile (between stairs and a closure, say), it moves to the nearest node of the main connected piece within `snap.max_m` and the search runs again, with a warning. `cloud/app/maps_links.py` (OP-209) builds the Google Maps link with up to 3 waypoints where a route leaves the plain shortest route, and the Apple Maps link (start to end only, with a note).
- **Camera worker hosting (November 8):** the OP-204 worker runs on one team laptop during the demo, not on Render, because Render's free plan has no background workers. It runs on Tatsat's laptop, including on demo day; the steps are in [docs/DEPLOY.md](docs/DEPLOY.md#4-camera-worker-on-demo-day). Its posts every 5 minutes also keep the free API awake.
- **Matching (OP-206):** `cloud/app/matching.py` builds that `HazardMap` from the store's active items. The first route request builds it; after that it is rebuilt at most every 30 s in a background thread, so requests never wait, and a failed rebuild (for example a database error) keeps the last good map. A point snaps to the nearest edge within 30 m; a line matches each edge with at least half its length, or at least 25 m of it, inside a 15 m buffer; polygons, 311 neighbourhood advisories (`snow_complaints`) and `weather` items attach as areas and change no edges. Indoor items (LRT elevators and escalators) are never snapped to sidewalks, because the City gives station points; an elevator outage blocks only edges from a reviewed mapping (OP-106), otherwise it is kept as a station warning. Each hazard keeps the item's `id` and `effect`; routing applies the effect per profile.
- **Route report (OP-208):** every route from `GET /api/route` carries `report`: each known item `on_route` (matched to a path the route uses, or an area the route passes through), `nearby` (within 50 m of the route line) or `avoided` (on the plain shortest route but not this one, at any distance). Groups come in a fixed order and are always present: Indoor, Sidewalks and paths, Roads and crossings, Weather and ice (Transit only when it has items). Each item has its source and attribution, title and detail, confidence, `observed_at` (when OpenPath last confirmed it with its source, not when the source changed it), the source's dates, a source link, its distance from the route and, for station-level or area items, a `note` on why it cannot change routes. A `weather` item the matcher keeps as an area (the black-ice watch, which the `weather_watch` source will publish as one item over the network's bounding box) is `on_route` for every route. 311 neighbourhood advisories are still points, so they follow the 50 m rule until they get neighbourhood polygons (Phase 3). `cloud/app/report.py` builds the report from one or more `ItemIndex`: the matcher builds one with every match. A hazard with no record is still listed from what the hazard carries. The report can never break matching or routing: if the index build fails, the match keeps its hazards with no index, and if a route's report fails, it falls back to the hazards that route met (both logged). `routing_api.get_report_items` adds the index of OP-205's camera ice risks (`CameraIceHazardProvider.items`) and takes the matcher from its `base`.
- **Live updates (OP-303):** `WS /ws` (`cloud/app/ws.py`, hub on `app.state.live`) sends `source_status` after every scheduler run, `obstruction_changed` (items added and removed between hazard rebuilds) and, for the one route a connection subscribes with `{"type": "subscribe", "route": {"from": [lat, lng], "to": [lat, lng], "profile": "wheelchair"}}`, `route_changed` when a rebuild gives it different paths, no route, or an ETA at least 20% longer, with one sentence to announce; a heartbeat goes out every 25 s. It attaches through `HazardProvider.on_rebuild` and `Scheduler.add_listener`, which OP-304 can use too; re-routing runs in a worker thread and never touches `/api/route`. The web hook `useLiveUpdates` (`web/src/lib/live.ts`) and `LiveStatus` (`web/src/components/live-status.tsx`) are ready for OP-301 to mount; the speech toggle is stored under `openpath.speak`, and the WebSocket message types in `web/src/lib/live-types.ts` are hand-written because OpenAPI does not cover WebSockets.

## Where to Find Context

| You need | Read |
| --- | --- |
| Product, scope, data sources, vision model, routing rules, architecture, contracts, production checklist, risks, open decisions | [PLAN.md](PLAN.md) (sections 1 to 17) |
| Every user story with its owner and story points | [docs/USER_STORIES.md](docs/USER_STORIES.md) |
| Acceptance criteria and live status of a story | GitHub issues `OP-101` to `OP-414` (#29 to #81) |
| Team workflow and Definition of Done | [GUIDELINE.md](GUIDELINE.md) |
| CI checks, merge rules and deployment | [docs/CI_CD.md](docs/CI_CD.md) and [docs/DEPLOY.md](docs/DEPLOY.md) |
| Data source details and licences (created in Phase 1) | `docs/DATA_SOURCES.md` |

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
config/    profiles.json  sources.json  elevators.json  matching.json  places.json
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
- **Read:** `GET /health`, `/api/status`, `/api/obstructions?bbox=`, `/api/route?from=&to=&profile=&mode=walk|walk_lrt`, `/api/search?q=`, `/api/weather`, `/api/cameras`, `/api/priority`.
- **Write:** `POST /api/reports`, `/api/push/subscribe`, `/api/saved-routes`, `/api/ingest/camera` (`X-Worker-Key`), `/api/admin/override` (`X-Admin-Key`, testing only).
- **Camera readings (OP-213, agreed November 8):** `POST /api/ingest/camera` takes one reading per camera view in the shape agreed on issue #47: `source` `camera_model`, `source_ref` `511:<camera_id>:<view_id>`, `camera_id`, `view_id`, `latitude`, `longitude`, `label` (`dry|wet|snow_partial|snow_covered|ice|unknown`), `score`, `reason`, `observed_at`, `expires_at`, `model_version`, `mode`, `attribution`. One row per view in `camera_reading` (`cloud/app/camera_readings.py`); an older reading never replaces a newer one; `source_ref` must match the ids; `expires_at` is at most 2 h after `observed_at`; `mode` must equal the API's `DATA_MODE` (else 409). `GET /api/cameras` lists the latest unexpired readings as typed GeoJSON. Readings are not obstructions; OP-205 turns them into `ice_risk` where the rule applies.
- **Live:** `WS /ws` with events `obstruction_changed`, `route_changed` and `source_status`.

## Data Source Rules

- **Access:** official access only. Use the City open data API (`data.edmonton.ca/resource/<id>.json`, with `SOCRATA_APP_TOKEN`), the 511 Alberta API with our key (10 calls per 60 s), Environment Canada MSC GeoMet, and Open-Meteo. Never scrape websites or call private endpoints. Use City camera streams only after the City gives written permission.
- **Inventory:** dataset IDs, cadences and licences live in PLAN.md section 6 and `docs/DATA_SOURCES.md`. Live LRT status comes from the outage feed (`snws-u3zx`), never the master list, which lags.
- **Fixtures:** every adapter has recorded fixtures, and tests never call the network.
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

OP-109's standalone weather app: `.venv/bin/python -m uvicorn cloud.app.weather_app:create_app --factory --port 8001`; replay requires `DATA_MODE=replay` and `REPLAY_SNAPSHOT` pointing to the snapshot described in [Estella's weather guide](docs/ESTELLA_PHASE1.md).

These live in the root Makefile and are available after OP-102.

| Command | What it does |
| --- | --- |
| `make setup` | Creates `.venv` with every `cloud/` and `vision/` requirements file (as CI installs them), runs `npm ci` in `web/` |
| `make dev` | API on :8000 (SQLite, recorded fixtures, no keys) + web on :3000 |
| `make web` | Only the web app on :3000, pointed at the API on :8000 |
| `make test` | `pytest` for cloud and vision, plus `npm run lint` and `npm run typecheck` in `web/` |
| `make graph` | Installs `scripts/requirements-graph.txt`, then rebuilds `cloud/data/network.npz` with OSMnx (local only, then commit). macOS or Linux for now: `scripts/build_graph.py` uses the Unix-only `resource` module |
| `make types` | Regenerates `web/src/lib/api-types.ts` from `/openapi.json` (with `npx openapi-typescript`) |
| `make record` | Records a replay snapshot of every source (no images) |
| `make worker` | Runs the vision worker locally |
| `make lint` | `ruff check`, `ruff format --check` and `npm run lint` (the same lint CI runs) |
| `make format` | `ruff check --fix`, then `ruff format` |
| `make build` | Production build of the web app (`npm run build`) |

`make record` prints "not built yet" and exits with an error until OP-305 adds its script. `make worker` now runs a network-free, explicitly synthetic OP-204 replay; see below for live prerequisites. While `web/` is missing, the web steps of `make setup`, `make test` and `make lint` are skipped, `make dev` starts only the API, and `make web`, `make build` and `make types` stop with an error. The Makefile works on Windows (GNU make, for example `winget install ezwinports.make`), macOS and Linux.

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

OP-205 checks: `python -m pytest cloud/tests/test_camera_ice.py cloud/tests/test_camera_ice_routing.py cloud/tests/test_camera_ice_review.py cloud/tests/test_camera_ice_app.py -q`.

## Environment Variables

Never commit values. Keep `.env.example` current. Nothing loads `.env.example` or a `.env` file: Render and Vercel take values from their dashboards, and `make dev` sets its own.

- **cloud:** `DATABASE_URL`, `ADMIN_KEY`, `WORKER_KEY`, `CORS_ORIGINS`, `SOCRATA_APP_TOKEN`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `DATA_MODE` (`live` or `replay`), `REPLAY_SNAPSHOT`, `REPLAY_AT`, `WEATHER_LAT`, `WEATHER_LNG`, `LRT_TRIPS`
- **vision:** `CLOUD_URL`, `WORKER_KEY`, `ALBERTA_511_KEY`, `MODEL_URL`, `ANTHROPIC_API_KEY` (only for `--ai-check`, on the worker laptop; never on Render, never in CI)
- **web:** `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_VAPID_PUBLIC_KEY`. The OP-113 web app currently reads `NEXT_PUBLIC_API_BASE_URL` instead of `NEXT_PUBLIC_API_URL`; `make dev` and docs/DEPLOY.md set both until the two agree.

`REPLAY_SNAPSHOT` is a folder, not a file: one subfolder of recorded responses per source, as in `cloud/tests/fixtures/`. `REPLAY_AT` (replay only, ISO 8601 with a time zone, for example `2026-11-10T18:00:00Z`) freezes the app's clock (`app.state.clock`) so recorded closures stay active: the scheduler, the map (`GET /api/obstructions`) and route hazards all use it. `CORS_ORIGINS` is comma-separated, and a `*` matches part of one host-name label (`https://openpath-*-<team-slug>.vercel.app`). It is not a security boundary; see [docs/DEPLOY.md](docs/DEPLOY.md).

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
- **web:** typecheck and lint on every PR; Playwright end-to-end on the critical path (OP-406).

## Do Not

- Add accounts, auth providers, new services or state libraries without the lead's sign-off. Version 1 has no user accounts (decided November 7).
- Commit secrets, `.env`, API keys, model weights or images.
- Scrape any website, use private endpoints, or use City camera streams without written permission.
- Fake data in the demo path. Override endpoints are for testing and are labelled.
- Change contracts silently, or "fix" a failing test by weakening it.
