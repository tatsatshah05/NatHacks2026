# PathSense team guideline

We move as **one team through four phases**, in order. Everyone works on the current phase; nobody starts the next phase until its gate passes. The one exception is the code skeleton: Claude Code builds it in the background during Phase 1, so Phase 3 starts from working code.

Each person has **one task per phase**. When yours is done, help whoever is behind in the same phase. Every task is a GitHub issue. Filter by your `person:` label and the current milestone.

## The phases

| Phase | Hours | Goal | Gate to move on |
| --- | --- | --- | --- |
| 1. Hardware | 0–10 | Wire everything and write the hardware code | The Pi shows camera frames of the whole board, reads both ultrasonic sensors and moves both servos |
| 2. Vision model | 10–22 | Turn camera frames of the model into reliable segment states | Target: correct on 45 of 50 test placements, changes in under 2 s, no false changes in 10 min |
| 3. Web app | 22–34 | Get states to the cloud and onto a live map | Snow on the model reroutes the deployed site on a phone; a push alert arrives; the Google Maps hand-off works |
| 4. Finish | 34–48 | Harden, freeze at hour 40, pitch, submit by hour 46 | Demo rehearsed 5 times |

## At a glance

| Person | Phase 1: Hardware | Phase 2: Vision model | Phase 3: Web app | Phase 4: Finish |
| --- | --- | --- | --- | --- |
| Tatsat | Raspberry Pi hub setup + code skeleton | Fusion, stability, reliability tests | Street graph, routing, Pi uplink | Demo checks, reset, offline mode |
| Ipsa | Camera source and mount | Calibration and segment regions | Black-ice weather watch and replay | Measure the pitch numbers |
| Estella | Build the street model | Collect the training data | Saved routes and push alerts | Slides, Devpost, rehearsals |
| Kaleb | Ultrasonic sensors: wiring + code | Baseline detector (no ML) | City dashboard and dispatch | Hardware hardening |
| Matthew | Servos: wiring + code | Train the classifier, run it on the Pi | Cloud API on Render | Deploy stability + backup video |
| Kashish | Serial console + wiring diagram | Data collection tool | Live map and route planner | Accessibility pass |

## Phase 1: Hardware connections and hardware code (hours 0–10)

| Person | Task | Done when |
| --- | --- | --- |
| Tatsat | Set up the Raspberry Pi as the hub: OS, SSH, network, Python venv with `--system-site-packages`, repo cloned, Arduino seen over USB. Run `prompts/00-kickoff.md` in the background. | The team can SSH into the Pi, and it sees the Arduino |
| Kaleb | Wire 2 HC-SR04 (D2/D3, D4/D5) and write their code: `U<i>,<mm>` at 5 Hz | The serial monitor shows both distances change when a toy bin is placed |
| Matthew | Wire 2 servos (plow D9, barrier D10) on 4xAA power with shared ground, and write their code: `S<i>,<deg>`, `P`/`OK`. Merge it with Kaleb's code into `firmware/arduino_street/` | `S0,20` and `S1,90` move the right servo without jitter |
| Ipsa | Pick the camera (Pi Camera Module or a phone; not the OV7670), mount it about 70 cm above the board, fix and mark the lamp angle | The Pi saves a sharp, evenly lit frame with all 4 ArUco markers |
| Kashish | `edge/tools/serial_console.py` (send `S`/`P`, print `U`), plus `firmware/WIRING.md` with a pin table and a photo | Anyone can move a servo and watch both distances from the Pi |
| Estella | Build the model: matte dark-gray board, about 14 numbered segments, a curb cut, a stairs shortcut, Home/Clinic/Transit/Grocery, ArUco markers, the clear-film patch, props | The model matches `config/model_map.json` |

## Phase 2: Vision model (hours 10–22)

Our data is **overhead camera frames of the model**. We cut each frame into one image per segment (a "crop"), label the crops, and train on them.

| Person | Task | Done when |
| --- | --- | --- |
| Ipsa | Calibration: ArUco to a top-down view, one region per segment from `model_map.json`, a baseline from 30 clean frames | A debug window outlines every segment in the right place |
| Kashish | `edge/tools/label.py`: one keypress saves every segment's crop with a label (clear, snow, obstruction, gloss) to a shared folder outside git | One keypress saves 14 labelled crops |
| Estella | Collect data: at least 50 placements per class under 3 lighting setups; keep 10 per class aside as the test set | The dataset and its label CSV are shared |
| Kaleb | Baseline detector without ML: snow, obstruction and gloss scores, plus the ultrasonic drop that marks a segment blocked | Baseline accuracy on the test set is posted in the issue |
| Matthew | Train a small classifier (e.g. MobileNetV3-small), export it to ONNX, and time it on the Pi | Both accuracies are in the issue; the better model runs on the Pi at 5 frames/s or faster |
| Tatsat | Fusion: smoothing, enter/exit thresholds, hand-over-board freeze, stale data; run the 50-placement and 10-minute tests | Gate 2 passes, with results in `edge/metrics.csv` |

## Phase 3: Web app (hours 22–34)

| Person | Task | Done when |
| --- | --- | --- |
| Matthew | Cloud API on Render: ingest with device key, hazard store in Postgres, live `/ws`, `/health` | A posted hazard shows up live for every connected client |
| Tatsat | Street graph of the demo neighbourhood, routing per profile with alternates, walking times and Maps links; the Pi uplink and dispatch listener | A hazard on the model changes the route the API returns |
| Kashish | Next.js on Vercel: live map with hazard layer, profile picker, route cards, spoken and vibrating alerts | On a phone, the deployed site reroutes live |
| Ipsa | Open-Meteo black-ice watch, `GET /api/weather`, replay mode with a visible badge; gloss plus watch means black-ice risk | The clear film shows as black-ice risk on the map |
| Estella | Saved routes and Web Push (with Kashish); push when a route changes, is blocked, or clears; iPhone instructions | A push reaches Android within 5 s of snow on the saved route |
| Kaleb | `/city` dashboard: live hazards, clear-first list, Dispatch button that moves the plow; blocked segments drop the barrier | Dispatch sweeps the plow and the route restores |

## Phase 4: Finish (hours 34–48)

Feature freeze is at **hour 40**: after it, bug fixes only. Submit by **hour 46**.

| Person | Task |
| --- | --- |
| Tatsat | `demo_check`, `demo_reset`, offline mode (`make local`) |
| Ipsa | Measure the pitch numbers: detection time, end-to-end latency, accuracy, false alarms |
| Estella | Slides with sourced statistics, the Devpost write-up with honest limits, 5 timed rehearsals |
| Kaleb | Hardware hardening: tidy and label wiring, strain relief, spare batteries and cables |
| Matthew | Keep Render warm, check env vars, a `/demo` QR page; record the backup video at hour 40 |
| Kashish | Accessibility pass: keyboard, screen-reader labels, contrast, 200% zoom, reduced motion |

**Later, v2 (only after Gate 3 and only if there is time):** Kaleb adds the DS18B20, the 4.7 kΩ resistor and the water sensor (plus the DHT22 if we have one) to the Arduino.

## How to work a task

```bash
# 1. Start from the latest main
git checkout main && git pull

# 2. Make a branch named after the phase and issue number
git checkout -b p1/4-servos

# 3. Open Claude Code in the repo; paste the issue text and the prompt it references

# 4. Commit small, then push
git add -A && git commit -m "Servo control over serial" && git push -u origin p1/4-servos

# 5. Open a pull request that closes the issue; ask one teammate to review
gh pr create --title "Servo control over serial" --body "Closes #4"
```

Merge once it works and someone has checked it, then delete the branch.

## Rules

- **Stay in the phase.** Finish your task, then help someone in the same phase.
- **Gate check.** At each gate, run a 5-minute stand-up: what's done, what's blocked, and do we move on?
- **Shared contracts.** Change `config/` and `cloud/app/schemas.py` only after the team agrees in the Technical Spec comments.
- **Hardware changes** go into `firmware/WIRING.md` the same hour.
- **Never commit** secrets, `.env`, `secrets.h`, the dataset, or photos with people in them.
- **Ask early.** Stuck for 20 minutes? Say so in the team chat.
