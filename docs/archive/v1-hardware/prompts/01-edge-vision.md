# 01 Edge and vision (reference for Phase 2 tasks and the Phase 3 Pi uplink)

Paste into Claude Code on branch `edge/vision` after the kickoff skeleton is merged.

---

Read CLAUDE.md, then `edge/`, `config/segments.json` and `config/thresholds.json`. You own the edge service that runs on the Raspberry Pi hub. It turns the camera's view of a real snowy sidewalk, plus the curb-cut distance sensor, into one reliable state per segment. Reliability on stage matters more than cleverness: no flicker, no false alarms when a hand passes over the board.

Plan first, then build in this order, with tests at each step:

1. **`camera.py`.** One interface with two sources, chosen by `CAM_SOURCE`: `picamera` uses picamera2 on the Pi (install it with apt and create the venv with `--system-site-packages`); `url` polls any JPEG snapshot URL (a phone IP-camera app, or an ESP32-CAM if we get one). Lock exposure, gain and white balance in both. The rest of this step describes the `url` source with an ESP32-CAM. Poll `CAM_URL/capture` at 5 Hz using `httpx` with a 1 s timeout; reconnect forever with backoff. Use `/control` for setup: SVGA resolution, then lock exposure, gain and white balance after a 2 s warm-up (check the variable names in the CameraWebServer example). If no frame arrives for 5 s, report the camera stale so fusion marks segments `unknown`. Keep a `--stream` mode that reads `:81/stream` via OpenCV as an alternative.
2. **`calibrate.py`.** Mark each sidewalk segment as a polygon by clicking on a live camera frame, and save it to `config/segments.json` in 0 to 1 image coordinates. Capture a clean baseline per segment (median of 30 frames), and re-baseline when the light changes (dusk, night). Keys: `m` mark, `b` re-baseline, `q` quit.
3. **`detect.py`.** Per segment, inside its region of interest:
   - **snow score:** share of pixels much brighter than the baseline (LAB L channel up by more than the threshold) and low in saturation.
   - **obstruction score:** share of changed pixels that are not snow-like, after a morphological open.
   - **occlusion guard:** if more than 40% of a segment changes at once (a person or vehicle in view), freeze that segment until the view clears. Never save frames that show people.
   - Pure functions that take numpy arrays, tested on synthetic images (a white blob for snow, a coloured block for an obstruction, a big dark blob for a hand).
   - **gloss score (Path B, `ICE_MODE=weather_camera`):** share of near-saturated specular pixels (V > 240 in HSV, and not present in the baseline) in each region of interest. Test it on a synthetic image with a bright streak. With the real board, a clear plastic film under the angled lamp must cross the threshold while bare board does not. Poll `GET /api/weather` every 30 s, and set `ice_risk` only when gloss is high **and** the watch is active.
4. **Fusion inputs.** In Path A, `mqtt.py` subscribes to `pathsense/sensor/ice-1` (paho-mqtt 2.x, `CallbackAPIVersion.VERSION2`) and applies the black-ice rule from CLAUDE.md to the mapped segment. `serial_bridge.py` parses `U<i>,<mm>` against a per-sensor baseline distance; a 15 mm drop or more means obstruction or snowbank on that segment. Feed all of it into the existing `fusion.py`.
5. **`uplink.py`.** POST `/api/ingest/segments` on every state change, plus a heartbeat every 10 s. Keep a bounded retry queue so the network can drop without crashing anything. Listen to cloud `WS /ws`: for `route_changed` on the demo route, send the LED frame (`L...`); for `dispatch`, sweep the plow servo (`S0,20`, wait 600 ms, `S0,160`, wait 600 ms, `S0,90`). Drop the barrier servo (`S1,0`) while any mapped segment is `blocked` and raise it (`S1,90`) when clear.
6. **Debug view.** `run.py --show` opens an OpenCV window with the warped board, each region outlined in its state colour, and the snow and obstruction scores printed. Also serve it as MJPEG at `http://localhost:8001/video` for the city dashboard on the hub.
7. **Metrics.** Append every state change to `edge/metrics.csv` (timestamp, segment, from, to, scores, latency from first threshold crossing to uplink acknowledgement). Add `python -m pathsense_edge.bench` for a guided 50-placement test that prints hits, misses, false alarms and median detection time. These are the numbers for the pitch.

**Done (by the end of its phase):**
- A cone or a pile of snow on a real segment reaches state `snow` or `blocked` locally in under 2 s, and shows in the cloud API in under 3 s.
- With no changes for 10 minutes while people walk around the table, there are zero false state changes.
- A person walking through the view causes no false state change.
- `make test` passes.

Do not change `config/` or cloud schemas; ask the lead. Phase 2 includes ML: after the classical baseline works, train a small classifier on segment crops (collected with `edge/tools/label.py`). Keep whichever of baseline or classifier scores higher on the held-out test set.
