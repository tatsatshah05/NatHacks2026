# PathSense: CLAUDE.md

PathSense detects when accessible winter pathways become inaccessible (snow, black ice, obstructions) and reroutes people live, based on how they get around (wheelchair, walker or cane, low vision, standard). It is our natHACKS 2026 project: a 48-hour build by a team of four.

A tabletop model of a real Edmonton neighbourhood carries the sensors. Its segments map to real streets, so the live web platform shows real routes, walking times, alerts and Google Maps hand-off.

- Repo: https://github.com/tatsatshah05/NatHacks2026
- Technical spec (editable; discuss in comments; it is the source of truth for open decisions): https://claude.ai/artifact/RXvZzsyhUkjnyc4ix92ZjE
- Team plan (roles, timeline, demo script, BOM): https://claude.ai/artifact/D7hBPDBBxcMad9je8PTQ63

## Team and phases

Six people (Tatsat, Ipsa, Estella, Kaleb, Matthew, Kashish) move together through four phases. Each person has one task per phase; `GUIDELINE.md` and the GitHub issues (labels `person: <name>`, one milestone per phase) are the source of truth.

| Phase | Hours | Gate |
| --- | --- | --- |
| 1. Hardware connections and hardware code | 0-10 | The Pi shows camera frames of the whole board, reads both ultrasonics, moves both servos |
| 2. Vision model (from overhead camera frames of the model) | 10-22 | Target 45/50 test placements correct, under 2 s, no false changes in 10 min |
| 3. Web app | 22-34 | Snow on the model reroutes the deployed site on a phone; push and Google Maps hand-off work |
| 4. Finish | 34-48 | Freeze at hour 40; demo rehearsed 5 times; submit by hour 46 |

When working an issue, stay inside that phase's scope. Do not build next-phase features early, except the code skeleton (`prompts/00-kickoff.md`), which runs during Phase 1.

## Priorities (apply to every task)

1. **The live demo must never fail.** Reliability beats features. Prefer simple, deterministic code with explicit fallbacks.
2. **Contracts are shared.** Change `cloud/app/schemas.py` and `config/` only after the team agrees in the Technical Spec comments. If a task seems to need a contract change, stop and say so. Do not work around it.
3. **Must, then Should, then Could** (see Scope). Feature freeze at hour 40; after that, bug fixes only.
4. **Everything runs without hardware.** `make sim` must always run a fake end-to-end demo on a laptop. Never break it.
5. **Small, working steps.** Plan first for any change that touches more than one file. Run `make test` after changes. `main` always passes.
6. **Never invent numbers** for the pitch or the UI. Measure them (`edge/metrics.csv`) or label them as assumptions.

## Scope

| Priority | Features |
| --- | --- |
| Must | Live accessible map of the demo neighbourhood; routes per profile with up to 2 alternates, distance and minutes; black-ice detection (ice station plus weather watch); live updates over WebSocket; Web Push alerts on saved routes; "Open in Google Maps" with waypoints |
| Should | Apple Maps hand-off; community hazard reports; city dashboard with clear-first ranking |
| Could | v2 temperature sensors (ice station, after the camera works); servo plow dispatch; SMS alerts; on-device detection on the ESP32-CAM; ML classifier |
| Won't (this weekend) | User accounts, native apps, writing data into Google or Apple Maps, real city cameras |

## Architecture

```
MODEL (venue, our own 2.4 GHz router)
  Camera ........... overhead; CAM_SOURCE=picamera (Pi Camera Module) or url (phone or any JPEG snapshot URL)
                     (source still open; the OV7670 in our kit is NOT used)
  Arduino Uno ...... HC-SR04 x2, 2 SG90 servos (plow, barrier), optional LEDs  <-> USB serial to the Pi
                     v2: DS18B20 surface temp + water sensor (+ DHT22 if we have one) on the same Arduino
  Feather ESP32 .... spare; optional wireless HC-SR04 node over MQTT later
        |
EDGE HUB (Raspberry Pi; a laptop is the backup)                  edge/
  Mosquitto broker
  vision: grab camera frames -> ArUco warp -> per-segment snow + obstruction scores
  fusion: camera + ultrasonic (+ v2 temperature) -> one state per model segment (EMA + hysteresis)
  uplink: POST /api/ingest/segments on change + every 10 s; listens to cloud WS for route + dispatch
  serial bridge: ultrasonic (+ v2 T/W/H) in; servo commands (+ optional LED frame) out
        |  HTTPS + X-Device-Key
CLOUD API (Render: FastAPI + Postgres)                            cloud/
  hazard store: one state per street edge; sources = model > report > weather
  weather job: Open-Meteo freeze-thaw "black-ice watch" every 10 min
  routing: prebuilt walking graph (networkx), 4 profiles, best + 2 alternates, ETAs, reasons
  live: WebSocket /ws broadcast; Web Push to saved routes when their route changes
  clear-first ranking; dispatch -> WS -> hub -> servo plow
        |  REST + WSS
WEB APP (Vercel: Next.js App Router)                               web/
  /        planner: MapLibre map + hazard layer + route cards + Google/Apple Maps links + report
  /alerts  save a route, enable push (PWA service worker)
  /city    hazards, clear-first list, dispatch crew
  /demo    big QR code to the live site for judges
```

The model's segments `e1` to `e14` map to real OpenStreetMap edges through `config/model_map.json`. A model segment's state applies to every real edge it maps to. If the hub goes silent for more than 30 s, model-sourced states expire and the cloud falls back to reports and weather.

## Repo layout

```
CLAUDE.md  Makefile  render.yaml  .env.example
config/      profiles.json  model_map.json  places.json  thresholds.json
cloud/       app/{main,schemas,db,models,graph,routing,hazards,weather,push,priority,ws}.py
             data/graph.json (committed)   scripts/build_graph.py (OSMnx, run locally once)   tests/
edge/        pathsense_edge/{camera,calibrate,detect,fusion,serial_bridge,mqtt,uplink,run,sim}.py   tests/
web/         src/app/{page.tsx,alerts/,city/,demo/}   src/lib/api-types.ts (generated)   public/sw.js
firmware/    esp32cam/  esp32_ice_station/  arduino_street/  WIRING.md
prompts/     00-kickoff  01-edge-vision  02-firmware  03-cloud  04-web  05-integration
```

## Contracts (summary; the code is the source of truth)

**Segment states:** `clear | snow | ice_risk | blocked | unknown`.

**MQTT** (Mosquitto on the hub, port 1883). Only needed for the v2 ice station or the optional spare-ESP32 ultrasonic node; v1 runs without it.
- `pathsense/sensor/ice-1`: `{"surface_c": 0.4, "air_c": 2.1, "rh": 93, "wet": true, "ts": 1794012345}`
- `pathsense/sensor/ice-1/status`: `online` / `offline` (last will)

**Arduino serial** (115200 baud, newline-terminated ASCII):
- Arduino to hub: `U<i>,<mm>` ultrasonic reading at 5 Hz (e.g. `U0,182`); `OK` replies to `P`
- v2, Arduino to hub at 1 Hz: `T<surface_c>` (DS18B20, e.g. `T-0.8`), `W<0-1023>` (water sensor, higher = wetter), `H<air_c>,<rh>` (DHT22, only if fitted)
- Hub to Arduino: `S<i>,<deg>` servo `i` (0 = plow arm, 1 = sidewalk-closed barrier); `P` ping; optional `L<chars>` LED frame with one char per segment in `model_map` order (`G` clear, `W` snow, `C` ice risk, `R` blocked, `B` on route, `.` off)

**Hub to cloud:** `POST /api/ingest/segments`, header `X-Device-Key`
```json
{"device_id": "edge-1", "segments": [{"model_segment_id": "e4", "state": "snow", "severity": 0.72,
  "sources": ["camera", "ultrasonic"], "observed_at": "2026-11-14T15:02:11Z"}]}
```

**Cloud API:**
| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness for Render and the demo check |
| `GET /api/hazards` | GeoJSON FeatureCollection of edges with `state, severity, source, confidence, updated_at` |
| `GET /api/route?from=lat,lng&to=lat,lng&profile=wheelchair` | Up to 3 routes: GeoJSON geometry, `distance_m`, `eta_min`, `hazards_avoided`, `warnings`, `google_maps_url`, `apple_maps_url` |
| `GET /api/places` | Named demo places |
| `GET /api/weather` | Current weather watch: `{active, mode, reason, air_c, ground_c, dew_c, updated_at}` (`mode` is `live` or `replay`) |
| `POST /api/reports` | `{lat, lng, type: snow or ice or blocked}`; snaps to the nearest edge; expires in 2 h |
| `POST /api/push/subscribe`, `POST /api/saved-routes` | Anonymous push subscription and saved route (`client_id` from localStorage) |
| `GET /api/priority` | Clear-first ranking |
| `POST /api/override/{edge_id}`, `POST /api/dispatch/{edge_id}` | Testing and demo only; require `X-Admin-Key` |
| `WS /ws` | `{"type": "hazard"}`, `{"type": "route_changed"}`, `{"type": "dispatch"}` events |

**Profiles** (`config/profiles.json`): cost = length x multiplier; time = length / speed x multiplier. `null` means impassable (the edge is removed). Edges tagged `highway=steps` are impassable for `wheelchair` and `walker_cane`.

| Profile | Speed m/s (assumed) | snow | ice_risk | blocked |
| --- | --- | --- | --- | --- |
| `wheelchair` | 1.0 | null | 3 | null |
| `walker_cane` | 0.9 | 3 | null | null |
| `low_vision` | 1.0 | 2 | 3 | null |
| `standard` | 1.3 | 1.5 | 1.5 | null |

**Black ice: v1 is camera plus weather; v2 adds sensors** (decided Nov 6). `ICE_MODE` defaults to `weather_camera`. Both modes emit the same `ice_risk` state, so cloud and web code never branch on the mode. Build v2 only after the camera pipeline passes Gate 3.
- **v2, Path A, `ICE_MODE=sensor`** (DS18B20 + water sensor, plus DHT22 if fitted, on the Arduino over serial; added after the camera works end to end): `ice_risk` when `surface_c <= 1.0` and (`wet` or `surface_c <= dew point`). Sources: `["ice_station"]`.
- **v1, Path B, `ICE_MODE=weather_camera`** (default; no temperature or water sensors): the edge computes a per-segment **gloss score**: the share of near-saturated specular pixels compared with the baseline, which shows up when a clear plastic film sits under the angled lamp. The segment is `ice_risk` when gloss is over its threshold **and** the cloud's weather watch is active. Gloss without freezing weather is ignored (wet, not icy). The edge gets the watch from `GET /api/weather`. Sources: `["camera_sheen", "weather"]`.
- **Weather watch (cloud, both pathways; Open-Meteo, no API key):** hourly `temperature_2m, dew_point_2m, relative_humidity_2m, precipitation, soil_temperature_0cm`, `past_days=1`. Active when (air <= 0 C or ground <= 0 C) and one of: precipitation > 0 in the last 6 h; air > 0 C in the last 12 h (melt and refreeze); or air minus dew point <= 1 C (frost). It is applied at low confidence to every edge without fresher data.
- **`WEATHER_MODE=live|replay`:** replay feeds a real past freeze-thaw night from Open-Meteo's historical API (`REPLAY_DATE`). The UI must show a visible "Replay" badge whenever replay is on. Never present replayed weather as live.
- **Precedence:** model > report > weather. Reports expire after 2 h, and model states after 30 s without a heartbeat.

**Alternates:** use `networkx.shortest_simple_paths` on the profile-weighted graph. Keep at most 3 routes, and only keep a route if it shares under 70% of its length with the ones already kept.

**Maps hand-off:**
- Google: `https://www.google.com/maps/dir/?api=1&origin=LAT,LNG&destination=LAT,LNG&travelmode=walking&waypoints=LAT,LNG|LAT,LNG`. Use at most 3 waypoints, placed where our route departs from the plain shortest route.
- Apple: `https://maps.apple.com/?saddr=LAT,LNG&daddr=LAT,LNG&dirflg=w`. Waypoints are not guaranteed, so the UI says so.

## Commands (root Makefile)

| Command | What it does |
| --- | --- |
| `make setup` | Creates `.venv` (edge + cloud requirements), runs `npm ci` in `web/` |
| `make test` | Runs `pytest` for cloud and edge, plus `npm run lint` and `npm run typecheck` in `web/` |
| `make sim` | Cloud API on :8000 with SQLite + `edge/sim.py` scripted hazards + web on :3000; no hardware |
| `make local` | Offline demo: the cloud API runs on the hub, and the web app points at it |
| `make edge` | Real edge service (camera, MQTT, serial, uplink) |
| `make cloud` / `make web` | Run one tier in dev mode |
| `make graph` | Rebuilds `cloud/data/graph.json` with OSMnx (run locally, then commit) |
| `make types` | Regenerates `web/src/lib/api-types.ts` from `/openapi.json` |

## Environment variables (never commit values; keep `.env.example` current)

- **cloud:** `DATABASE_URL` (Postgres on Render, `sqlite:///./pathsense.db` locally), `DEVICE_KEY`, `ADMIN_KEY`, `CORS_ORIGINS`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (`mailto:`), `WEATHER_LAT`, `WEATHER_LNG`, `WEATHER_MODE` (`live` or `replay`), `REPLAY_DATE`
- **edge:** `CLOUD_URL`, `DEVICE_KEY`, `CAM_SOURCE` (`picamera` or `url`), `CAM_URL`, `MQTT_HOST` (optional), `SERIAL_PORT`, `ICE_MODE` (`sensor` or `weather_camera`)
- **web:** `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_VAPID_PUBLIC_KEY`
- **firmware:** `secrets.h` (git-ignored), copied from `secrets.example.h`

Generate VAPID keys with `npx web-push generate-vapid-keys`.

## Conventions

- **Python 3.12:** type hints everywhere, Pydantic v2, SQLModel, `ruff format` + `ruff check`, `pytest`. Put units in names (`length_m`, `eta_min`, `surface_c`, `distance_mm`). Use UTC everywhere: ISO 8601 in HTTP, epoch seconds on MQTT.
- **Logging:** one line per state change, e.g. `STATE e4 clear->snow camera=0.72 ultrasonic=0.10`. These logs are our demo debugging tool.
- **TypeScript:** strict mode, no `any`. API types come only from the generated `api-types.ts`. Small components, Tailwind, no extra state library.
- **Firmware:** Arduino framework, non-blocking `loop()` built on `millis()`, no `delay()` over 20 ms, a serial debug line on boot, Wi-Fi and MQTT auto-reconnect.
- **Git:** one branch per workstream (`edge/...`, `hw/...`, `cloud/...`, `web/...`). Merge to `main` at gates. Write commit messages that say what changed and why.

## Hardware gotchas (check these before debugging code)

- **ESP32-CAM:** needs a solid 5 V / 2 A supply or it browns out and resets. Hold IO0 to GND to flash. Wi-Fi is 2.4 GHz only. Lock exposure and white balance through `/control` after a 2 s warm-up; check the variable names in the example's `app_httpd.cpp`. Turn the flash LED (GPIO 4) off.
- **ESP32 analog:** ADC2 pins stop working while Wi-Fi is on, so read the water sensor on an ADC1 pin (GPIO 32 to 39).
- **DS18B20:** needs a 4.7 kOhm pull-up to 3.3 V. Use 10-bit resolution with non-blocking conversion.
- **Servos (SG90 x2):** power them from a 4xAA pack or a 5 V 2 A supply with ground tied to the Arduino, never from a 9 V battery or the Arduino's 5 V pin.
- **v2 sensors:** the DS18B20 needs a 4.7 kOhm pull-up (data to 5 V); power the water sensor from a digital pin only while reading, to slow corrosion.
- **HC-SR04:** a 5 V part, so it stays on the Arduino. Fire the sensors one at a time to avoid crosstalk, and use `pulseIn` with a 25 ms timeout.
- **WS2812B with the servo on one Arduino:** `show()` blocks interrupts, which makes the servo jitter. Only call `show()` when the frame changes, and `detach()` the servo after each move.
- **LED power:** give the LEDs their own 5 V supply with a common ground, a 330 Ohm resistor on data, and a 1000 uF capacitor across power.
- **Vision:** paint the board matte dark gray, put ArUco `DICT_4X4_50` IDs 0 to 3 at the corners, and light it with our own lamp. When markers are hidden or more than 40% of the board changes at once (a hand in frame), freeze states.

## Accessibility requirements (web; judges will check)

- WCAG 2.2 AA contrast. Never colour alone: hazards also use patterns or icons plus text labels.
- Every route is also a text list of steps, so the map is never the only source of information.
- Route changes are announced in an `aria-live="assertive"` region, spoken with `speechSynthesis` (with a user toggle) and vibrated with `navigator.vibrate` where supported.
- Fully keyboard operable with visible focus, 44 px minimum touch targets, `prefers-reduced-motion` respected, text resizable to 200%.

## Testing

- **cloud:** routing per profile (impassable edges, stairs, ETA math, alternates are distinct), hazard precedence and expiry, ingest auth, push trigger (mock pywebpush), Maps URL builder.
- **edge:** fusion state machine (enter and exit thresholds, hysteresis, occlusion freeze, stale sensors), serial parser, `detect` on synthetic images (a white blob on a gray region of interest).
- **web:** typecheck and lint; a Playwright smoke test is optional.
- **Hardware:** every firmware change comes with a written manual test procedure in its README.

## Do not

- Add accounts, auth providers, new services, ORMs or state libraries without the lead's sign-off.
- Commit secrets, `.env`, `secrets.h`, or images with people in them.
- Run OSMnx on Render, or call Nominatim or Overpass at request time. The graph is prebuilt.
- Fake detections in the demo path. Override and dispatch endpoints are for testing and are labeled.
- Change contracts silently, or "fix" a failing test by weakening it.
