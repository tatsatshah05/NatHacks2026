# 03 Cloud API and routing (team lead)

Paste into Claude Code on branch `cloud/core` after the kickoff skeleton is merged.

---

Read CLAUDE.md and all of `cloud/`. You own the FastAPI service deployed on Render: the hazard store, routing, black ice, live updates, push alerts and the clear-first ranking. The skeleton's endpoints exist; make them real, correct and fast. Every response a phone waits on should take under 300 ms locally.

Plan first, then build in this order, with tests at each step:

1. **Real street graph.** In `scripts/build_graph.py`, use OSMnx (`graph_from_point(center, dist=1200, network_type="walk")`) around `WEATHER_LAT/WEATHER_LNG`. Export to the same `data/graph.json` format the skeleton uses, keeping the `highway` tag and LineString geometry. Run it locally (`make graph`) and commit the output; Render never runs OSMnx. Then help me fill `edge_ids` in `config/model_map.json`: print candidate edges near each segment's named street so I can pick them.
2. **Hazard store.** Persist the current state per edge with source, severity, confidence, `updated_at` and `expires_at`. Precedence is model > report > weather. Model states expire 30 s after the last heartbeat; reports after 2 h; weather is refreshed every 10 min. Expiry runs in a lifespan background task every 5 s. Every change is logged and broadcast on `/ws`.
3. **Black-ice weather watch** (`weather.py`). It is used in both pathways, and in Path B it is the only temperature source.
   - Call Open-Meteo `/v1/forecast` with hourly `temperature_2m,dew_point_2m,relative_humidity_2m,precipitation,soil_temperature_0cm`, `past_days=1`, `timezone=UTC`, and apply the rule in CLAUDE.md.
   - Expose `GET /api/weather`. Cache the last good response so a failed call never clears hazards.
   - `WEATHER_MODE=replay` reads Open-Meteo's historical archive API for `REPLAY_DATE`. Help me find a real Edmonton freeze-thaw night in the 2025-26 winter by scanning the archive for a day where the temperature crossed 0 C with precipitation; print candidates. Every response in replay mode carries `"mode": "replay"` so the web app shows a Replay badge.
4. **Routing.** Implement the profile-weighted graph, best route plus up to 2 distinct alternates, `distance_m`, `eta_min`, `hazards_avoided`, and `warnings` (e.g. "Low-confidence ice watch on 2 segments"). Give each route a one-line `reason` when it differs from the hazard-free route ("Avoids snow on 104 St curb cut; +3 min"). Snap `from`/`to` to the nearest graph nodes with a KD-tree. Cache the weighted graph per profile and invalidate it on any hazard change.
5. **Maps hand-off.** Build the Google Maps URL with at most 3 waypoints, chosen at the nodes where our route departs from the hazard-free shortest route, plus the Apple Maps URL. Unit-test both builders.
6. **Push alerts** (`push.py`). Store pywebpush subscriptions with a `client_id`. A saved route holds `from`, `to`, `profile` and `client_id`. On every hazard change, recompute only the saved routes whose current path touches a changed edge. Push when the route changes, becomes impossible, or its ETA rises by 20% or more. Throttle to one push per saved route every 2 min. The payload is `{title, body, url}`, with a deep link to the planner showing that route. Push "Your usual route is clear again" when it recovers.
7. **Clear-first ranking** (`priority.py`). For each hazardous edge, count how many saved routes plus place-to-place pairs (all profiles) would get shorter or become possible if that edge were clear. Return them ranked, with a plain-language line ("Clearing 104 St curb cut restores wheelchair access to Clinic and Transit").
8. **Dispatch.** `POST /api/dispatch/{edge_id}` (admin key) broadcasts `{"type": "dispatch", "model_segment_id"}` so the hub sweeps the servo plow.
9. **Render.** Confirm `render.yaml` works: Postgres via `DATABASE_URL` (convert `postgres://` to `postgresql+psycopg://` if needed), the `/health` check, and `CORS_ORIGINS` set to the Vercel production and preview domains. Write how to keep the instance awake during judging.

**Done (Gate 2, hour 18):** posting a hazard to ingest changes the routes for each profile correctly, and every connected web client sees it within 1 s.

**Done (Gate 3, hour 30):**
- Push arrives on an Android phone within 5 s of a hazard on a saved route.
- The weather watch shows up when forced.
- Clear-first lists the right edge first.
- `make test` passes.
