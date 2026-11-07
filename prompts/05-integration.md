# 05 Integration and demo hardening (Tatsat, from Gate 2)

Paste into Claude Code on `main` once Gate 2 is reached.

---

Read CLAUDE.md and the demo script in the team plan (linked at the top of CLAUDE.md). From now on the goal is a demo that works 10 times out of 10, on venue power and venue Wi-Fi, with a tired team. No new features unless they are on the Must list and still missing.

Do the following, one at a time. Plan each first.

1. **`scripts/demo_check.py`.** One command that prints a green or red line for each item, with the fix for every red:
   - ESP32-CAM `/capture` returns a JPEG under 1 s old
   - an ice-station message arrived in the last 5 s
   - the Arduino answers `P` with `OK`
   - calibration markers are visible
   - cloud `/health` responds
   - the WebSocket connects
   - a test push can be sent
   - the Vercel URL loads
   - the Render instance is warm
2. **`scripts/demo_reset.py`.** Clears all hazards and reports, re-baselines vision, sets the LEDs to the clear route, centres the servo, and loads the saved route "Maya: Home to Clinic, wheelchair" for the demo phone.
3. **`make local` offline mode.** The cloud API runs on the hub with SQLite and the same graph. The web app runs locally pointed at it. The edge points `CLOUD_URL` at localhost. Test it with Wi-Fi off. Write the switch-over steps in `DEMO.md` (target: under 60 s).
4. **Timing.** Add an end-to-end latency measurement: the time from a camera threshold crossing to the web client receiving the event, logged to `edge/metrics.csv`. Report the median and the 95th percentile over 20 placements, for the pitch.
5. **Rehearsal runbook (`DEMO.md`).**
   - The 8 demo beats with exact actions and expected on-screen results.
   - Who does what: one person narrates, one handles props, one drives the phone.
   - What to do if each beat fails.
   - A pre-judging checklist (charge phones, lamp on, run `demo_check`, warm Render, `demo_reset`).
6. **Freeze (hour 40).**
   - Tag `v1.0-demo`.
   - Record the backup video of the full demo.
   - Generate the Devpost draft from README plus metrics, and list the honest limitations (miniature model, simulated snow, placeholder speeds, weather-based ice watch is a heuristic).

**Rules from here:** bug fixes only, each on its own commit with a one-line reason. Run `demo_check` after every merge. If a fix risks the demo, don't ship it.
