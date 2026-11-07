# 00 Kickoff (team lead, hour 0)

Paste this into Claude Code at the repo root after hacking officially starts.

---

Read CLAUDE.md fully. We are at hour 0 of a 48-hour hackathon. Your job is to build the **walking skeleton**: every tier exists, the contracts are real code, and a fake end-to-end demo runs on one laptop with no hardware. Do not build detection, firmware or polish. Other teammates start from what you make.

Plan first. Show me the file list and the order of work, then build it in this order, and run the tests after each step:

1. **Repo plumbing.** Root `Makefile` with every target listed in CLAUDE.md (stub the ones that need later work, but keep them runnable), `.gitignore` (Python, Node, PlatformIO, `.env`, `secrets.h`, `*.db`), `.env.example` with every variable from CLAUDE.md, and `render.yaml` (web service `pathsense-api` with root dir `cloud`, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`, plus a Postgres database wired to `DATABASE_URL`).
2. **Config.** Write `config/profiles.json` exactly as the profile table in CLAUDE.md. Write `config/places.json` with 4 places (Home, Clinic, Transit, Grocery) near the University of Alberta; mark them `"placeholder": true` until the lead replaces them. Write `config/model_map.json` with 14 segments `e1` to `e14`: each has `edge_ids` (empty for now), `name`, and `roi` (polygon in 0 to 1 board coordinates) laid out as a plausible 4-block grid. Write `config/thresholds.json` with vision, fusion and black-ice thresholds.
3. **cloud/.**
   - `schemas.py` holds every contract from CLAUDE.md as Pydantic v2 models.
   - SQLModel tables for hazards, reports, push subscriptions and saved routes; SQLite locally.
   - `graph.py` loads `data/graph.json`. Until the real OSM graph exists, generate a synthetic walkable grid of about 40 nodes around the places, with one edge tagged `highway=steps`, and save it in the same format `build_graph.py` will produce: `{"nodes": {id: [lat, lng]}, "edges": [{"id", "u", "v", "length_m", "geometry": [[lng, lat], ...], "highway"}]}`.
   - `routing.py`: profile costs, impassable removal, best route plus up to 2 distinct alternates, `eta_min`, a human-readable reason for any change, and Google and Apple Maps URL builders.
   - `hazards.py`: source precedence (model > report > weather), expiry, mapping from model segment to edges.
   - `main.py` exposes every endpoint in the contract table. Ingest checks `X-Device-Key`; override and dispatch check `X-Admin-Key`. `ws.py` broadcasts hazard and route events. CORS comes from `CORS_ORIGINS`.
   - `weather.py` and `push.py` are stubs with their real interfaces and TODOs. `priority.py` returns an empty list for now.
   - Tests cover: each profile's impassable rules, stairs, alternates are distinct, ETA math, ingest auth, hazard precedence and expiry.
4. **edge/.** Package skeleton with `fusion.py` fully implemented (EMA plus enter and exit hysteresis per segment, stale-sensor handling, occlusion freeze flag), with tests. `sim.py` posts a scripted scenario to the cloud ingest: snow on `e4` at t=10 s, ice on `e7` at t=25 s, all clear at t=45 s, with keyboard toggles for each segment. Other modules are stubs with clear interfaces and TODOs naming their owner prompt (`prompts/01-edge-vision.md`).
5. **web/.** `npx create-next-app@latest web --ts --tailwind --app --eslint --src-dir --use-npm --yes`. Add `maplibre-gl` with the OpenFreeMap style `https://tiles.openfreemap.org/styles/liberty`. One page: the map centred on the places, the hazard layer from `/api/hazards` (colour plus dash pattern by state), a profile picker, a route from Home to Clinic drawn from `/api/route`, and live updates from `/ws` with auto-reconnect. Add a `make types` target using `openapi-typescript`.
6. **firmware/.** Create the three folders, each with a README stating the serial or MQTT protocol from CLAUDE.md and "owner: prompts/02-firmware.md". `WIRING.md` is a TODO.
7. **Deploy notes.** Write `DEPLOY.md` with the exact click-by-click steps for Render (Blueprint from `render.yaml`) and Vercel (root dir `web`, env vars). Don't run any deploy yourself; I will.

**Done means:**
- `make setup && make test` passes.
- `make sim` opens http://localhost:3000. Within 3 s of the sim putting snow on `e4`, the hazard shows on the map and the wheelchair route changes, without a page refresh.
- `git status` is clean apart from intended files; no secrets are committed.

Finish with a summary: what works, what is stubbed (with the owner prompt for each stub), and the exact commands I run next.
