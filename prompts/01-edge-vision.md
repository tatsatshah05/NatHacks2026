# 01 Edge and vision (vision owner)

Paste into Claude Code on branch `edge/vision` after the kickoff skeleton is merged.

---

Read CLAUDE.md, then `edge/`, `config/model_map.json` and `config/thresholds.json`. You own the edge service that runs on the hub laptop at the venue. It turns the ESP32-CAM's view of the tabletop model, plus the ultrasonic and ice-station readings, into one reliable state per model segment, then sends changes to the cloud. Reliability on stage matters more than cleverness: no flicker, no false alarms when a hand passes over the board.

Plan first, then build in this order, with tests at each step:

1. **`camera.py`.** ESP32-CAM client. Poll `CAM_URL/capture` at 5 Hz using `httpx` with a 1 s timeout; reconnect forever with backoff. Use `/control` for setup: SVGA resolution, then lock exposure, gain and white balance after a 2 s warm-up (check the variable names in the CameraWebServer example). If no frame arrives for 5 s, report the camera stale so fusion marks segments `unknown`. Keep a `--stream` mode that reads `:81/stream` via OpenCV as an alternative.
2. **`calibrate.py`.** Detect ArUco `DICT_4X4_50` IDs 0 to 3 with `cv2.aruco.ArucoDetector` and compute the homography to a 900 x 600 top-down board image. Save it to `edge/calibration.json` (git-ignored). Then capture the median of 30 frames as the clean baseline, saved per region of interest. Keyboard: `c` recalibrate, `b` re-baseline, `q` quit.
3. **`detect.py`.** Per segment, inside its region of interest:
   - **snow score:** share of pixels much brighter than the baseline (LAB L channel up by more than the threshold) and low in saturation.
   - **obstruction score:** share of changed pixels that are not snow-like, after a morphological open.
   - **occlusion guard:** any marker missing, or more than 40% of the board changed, means occluded, so freeze.
   - Pure functions that take numpy arrays, tested on synthetic images (a white blob for snow, a coloured block for an obstruction, a big dark blob for a hand).
   - **gloss score (Path B, `ICE_MODE=weather_camera`):** share of near-saturated specular pixels (V > 240 in HSV, and not present in the baseline) in each region of interest. Test it on a synthetic image with a bright streak. With the real board, a clear plastic film under the angled lamp must cross the threshold while bare board does not. Poll `GET /api/weather` every 30 s, and set `ice_risk` only when gloss is high **and** the watch is active.
4. **Fusion inputs.** In Path A, `mqtt.py` subscribes to `pathsense/sensor/ice-1` (paho-mqtt 2.x, `CallbackAPIVersion.VERSION2`) and applies the black-ice rule from CLAUDE.md to the mapped segment. `serial_bridge.py` parses `U<i>,<mm>` against a per-sensor baseline distance; a 15 mm drop or more means obstruction or snowbank on that segment. Feed all of it into the existing `fusion.py`.
5. **`uplink.py`.** POST `/api/ingest/segments` on every state change, plus a heartbeat every 10 s. Keep a bounded retry queue so the network can drop without crashing anything. Listen to cloud `WS /ws`: for `route_changed` on the demo route, send the LED frame (`L...`); for `dispatch`, sweep the servo (`S20`, wait 600 ms, `S160`, wait 600 ms, `S90`).
6. **Debug view.** `run.py --show` opens an OpenCV window with the warped board, each region outlined in its state colour, and the snow and obstruction scores printed. Also serve it as MJPEG at `http://localhost:8001/video` for the city dashboard on the hub.
7. **Metrics.** Append every state change to `edge/metrics.csv` (timestamp, segment, from, to, scores, latency from first threshold crossing to uplink acknowledgement). Add `python -m pathsense_edge.bench` for a guided 50-placement test that prints hits, misses, false alarms and median detection time. These are the numbers for the pitch.

**Done (Gate 2, hour 18):**
- Fake snow on a model segment reaches state `snow` locally in under 2 s, and shows in the cloud API in under 3 s.
- With no changes for 10 minutes while people walk around the table, there are zero false state changes.
- A hand held over the board for 3 s causes no state change.
- `make test` passes.

Do not change `config/` or cloud schemas; ask the lead. Do not add an ML model until Gate 3 is passed. If you do add one, it is a small classifier on region crops (`tools/label.py` to collect data), and it may only veto or confirm the classical scores.
