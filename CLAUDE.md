# PathSense: CLAUDE.md

PathSense plans accessible routes across Edmonton and lists every known barrier between A and B: LRT elevator and escalator outages, sidewalk closures, trail closures, road closures, snow and black-ice risk. It combines City of Edmonton open data, live weather, a vision model that reads road conditions from public cameras, and community reports. It routes by how people get around (wheelchair, walker or cane, low vision, standard). There is no hardware. natHACKS 2026, a 48-hour build by a team of six.

- **Plan (source of truth for scope, architecture and stories):** [PLAN.md](PLAN.md), also as [PDF](docs/PathSense%20Plan.pdf)
- **Live status of every story:** GitHub issues `PS-<id>`, labelled by person, phase, points and priority
- **Repo:** https://github.com/tatsatshah05/NatHacks2026

## Team and phases

Tatsat (lead), Ipsa, Estella, Kaleb, Matthew and Kashish move through four phases together. Each person owns about 8 story points per phase.

| Phase | Hours | Checkpoint |
| --- | --- | --- |
| 1. Data Foundation | 0–10 | Every Must source flows into `/api/obstructions` with freshness on `/api/status`; the walking network loads; CI and deploys work |
| 2. Vision and Routing | 10–22 | The model meets its target on unseen cameras; `/api/route` returns profile routes with alternates and a full report |
| 3. Web App and Alerts | 22–34 | On a phone: plan, report, live update, push alert, Google Maps hand-off |
| 4. Production and Launch | 34–48 | Release checklist done; freeze at hour 40; `v1.0` tagged; submit by hour 46 |

When working a story, stay inside its acceptance criteria. Do not build later-phase stories early.

## Priorities (apply to every task)

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
vision/  Render background worker: camera frames → ONNX road-condition model → POST /api/ingest/camera
         training (PyTorch) runs on a laptop or Colab, never on Render
web/     Next.js on Vercel: planner, route report, alerts (PWA), city dashboard, sources, about, privacy, demo
```

## Repo layout

```
PLAN.md  CLAUDE.md  GUIDELINE.md  README.md  Makefile  render.yaml  .env.example
cloud/     app/{main,schemas,db,store,scheduler,status,matching,routing,report,search,weather,push,ws}.py
           app/adapters/  one file per source   data/ prebuilt walking network   tests/ (fixtures in tests/fixtures/)
vision/    train.py  worker.py  tools/label.py  LABELS.md  MODEL_CARD.md  tests/
web/       src/app/{page.tsx, alerts/, city/, sources/, about/, privacy/, demo/}  src/lib/api-types.ts (generated)  public/sw.js
config/    profiles.json  sources.json  elevators.json  matching.json  places.json
scripts/   build_graph.py  record_snapshot.py  demo_check.py
docs/      DATA_SOURCES.md  ARCHITECTURE.md  RUNBOOK.md  PERFORMANCE.md  adr/  design/
```

## Contracts (summary; PLAN.md section 10 and the code are the source of truth)

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
- **Read:** `GET /health`, `/api/status`, `/api/obstructions?bbox=`, `/api/route?from=&to=&profile=&mode=walk|walk_lrt`, `/api/search?q=`, `/api/weather`, `/api/cameras`, `/api/priority`.
- **Write:** `POST /api/reports`, `/api/push/subscribe`, `/api/saved-routes`, `/api/ingest/camera` (`X-Worker-Key`), `/api/admin/override` (`X-Admin-Key`, testing only).
- **Live:** `WS /ws` with events `obstruction_changed`, `route_changed` and `source_status`.

## Data source rules

- **Access:** official access only. Use the City open data API (`data.edmonton.ca/resource/<id>.json`, with `SOCRATA_APP_TOKEN`), the 511 Alberta API with our key (10 calls per 60 s), Environment Canada MSC GeoMet, and Open-Meteo. Never scrape websites or call private endpoints. Use City camera streams only after the City gives written permission.
- **Inventory:** dataset IDs, cadences and licences live in PLAN.md section 6 and `docs/DATA_SOURCES.md`. Live LRT status comes from the outage feed (`snws-u3zx`), never the master list, which lags.
- **Fixtures:** every adapter has recorded fixtures, and tests never call the network.
- **Attribution:** attribute every source on `/sources` and on every item, as each licence requires.
- **Request time:** never run OSMnx on Render, and never call an external geocoder at request time. The network and the address index are prebuilt.

## Vision rules

- **No stored images.** Process frames in memory and never store them. Keep only labelled training images where the camera's terms allow it, and never an image in which a person can be identified. Classify community photos, then discard them.
- **Classes:** `dry`, `wet`, `snow_partial`, `snow_covered`, `ice` and `unusable`. Unusable frames give `unknown`, never `dry`.
- **Stability:** a reading changes only after 2 agreeing frames in a row.
- **Testing:** test cameras never appear in training. Publish measured results only, in `vision/MODEL_CARD.md`.
- **Fallback:** if cameras or the model fail, fall back to the weather watch and say so in the UI.

## Commands (root Makefile)

| Command | What it does |
| --- | --- |
| `make setup` | Creates `.venv` (cloud + vision), runs `npm ci` in `web/` |
| `make dev` | API on :8000 (SQLite, recorded fixtures, no keys) + web on :3000 |
| `make test` | `pytest` for cloud and vision, plus `npm run lint` and `npm run typecheck` in `web/` |
| `make graph` | Rebuilds the walking network with OSMnx (local only, then commit) |
| `make types` | Regenerates `web/src/lib/api-types.ts` from `/openapi.json` |
| `make record` | Records a replay snapshot of every source (no images) |
| `make worker` | Runs the vision worker locally |

## Environment variables (never commit values; keep `.env.example` current)

- **cloud:** `DATABASE_URL`, `ADMIN_KEY`, `WORKER_KEY`, `CORS_ORIGINS`, `SOCRATA_APP_TOKEN`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `DATA_MODE` (`live` or `replay`), `REPLAY_SNAPSHOT`, `WEATHER_LAT`, `WEATHER_LNG`, `LRT_TRIPS`
- **vision:** `CLOUD_URL`, `WORKER_KEY`, `ALBERTA_511_KEY`, `MODEL_URL`
- **web:** `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_VAPID_PUBLIC_KEY`

## Conventions

- **Python 3.12:**
  - Type hints everywhere; Pydantic v2 and SQLModel; `ruff format` and `ruff check`; `pytest`.
  - Put units in names (`length_m`, `eta_min`, `air_c`).
  - Use UTC with ISO 8601 everywhere.
- **Logging:** one line per state change, for example `CHANGE city_lrt_devices:1234 new elevator_out Central/West Platform`.
- **TypeScript:**
  - Strict mode, no `any`. API types come only from the generated `api-types.ts`.
  - Small components, Tailwind, no extra state library.
- **Git:**
  - Branch `ps-<id>-<short-name>` from `main`.
  - PR title `PS-<id>: <story title>` with `Closes #<issue>`.
  - One review before merge. Commit messages say what changed and why.
- **Replay:** `DATA_MODE=replay` shows a visible Replay badge on every page. Never present replayed data as live.

## Accessibility requirements (web; judges will check)

- WCAG 2.2 AA contrast. Never colour alone: every obstruction kind has an icon, a pattern and a text label.
- Every route is also a text list of steps and a grouped text report, so the map is never the only source of information.
- Route changes are announced in an `aria-live="assertive"` region, spoken with `speechSynthesis` (user toggle) and vibrated where supported.
- Fully keyboard operable with visible focus, 44 px touch targets, `prefers-reduced-motion` respected, text resizable to 200%.

## Testing

- **cloud:**
  - Each adapter with recorded fixtures, including a failing source and a changed schema.
  - Matching with synthetic geometry.
  - Routing per profile: impassable edges, stairs, ETA maths, distinct alternates.
  - Black-ice rule branches, push triggers (pywebpush mocked), Maps URL builders.
- **vision:** label tool, worker hysteresis, unusable frames give `unknown`, and the ice rule.
- **web:** typecheck and lint on every PR; Playwright end-to-end on the critical path (PS-406).

## Do not

- Add accounts, auth providers, new services or state libraries without the lead's sign-off. Version 1 has no user accounts (decided November 7).
- Commit secrets, `.env`, API keys, model weights or images.
- Scrape any website, use private endpoints, or use City camera streams without written permission.
- Fake data in the demo path. Override endpoints are for testing and are labelled.
- Change contracts silently, or "fix" a failing test by weakening it.
