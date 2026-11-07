# PathSense Team Guideline

## Overview

PathSense detects snow, ice and obstructions on real city sidewalks. When a sidewalk becomes unsafe, it shows people a safe, accessible route in real time.

The team works through four phases, in order. Everyone works on the same phase at the same time. We move to the next phase only after the current phase passes its checkpoint.

## Phases

| Phase | Hours | Goal | Checkpoint |
| --- | --- | --- | --- |
| 1. Hardware | 0–10 | Connect and program all hardware | The Raspberry Pi shows a clear view of the real sidewalk by day and night, records footage and reads the curb-cut sensor |
| 2. Vision Model | 10–22 | Detect sidewalk conditions from camera images | At least 45 of 50 test cases are correct, updates appear within 2 seconds, and there are no false alerts over 10 minutes |
| 3. Web App | 22–34 | Show live conditions and routes online | Blocking the real sidewalk updates the route on a phone, sends a notification and opens the route in Google Maps |
| 4. Finish | 34–48 | Stabilize, present and submit | Features are frozen at hour 40, the demo is rehearsed five times, and the project is submitted by hour 46 |

## Assignments

| Team member | Phase 1: Hardware | Phase 2: Vision Model | Phase 3: Web App | Phase 4: Finish |
| --- | --- | --- | --- | --- |
| Tatsat | Raspberry Pi setup and code foundation | Result stability and reliability testing | Routing and Pi-to-server connection | Demo checks and offline backup |
| Ipsa | Camera selection and window mount | Camera calibration | Black ice detection from weather data | Performance measurements |
| Estella | Site selection and sidewalk survey | Training data collection | Saved routes and notifications | Presentation, Devpost and rehearsals |
| Kaleb | Outdoor curb-cut sensor | Baseline detection | City dashboard | Cold-weather reliability |
| Matthew | Footage recording for training | Model training and deployment on the Pi | Server and database on Render | Deployment stability and backup video |
| Kashish | Live preview and sensor tools | Data labeling tool | Live map and route planner on Vercel | Accessibility review |

Each assignment is a GitHub issue with a goal, a task checklist and a definition of done.

## Working on a Task

1. Open **Issues** and filter by your name label (`person: <your name>`) and the current phase milestone.
2. Comment on the issue to show that you are working on it.
3. Create a branch from `main`, named after the phase and issue number.
4. Use Claude Code with the issue description and the reference listed in the issue.
5. Commit your work and push the branch.
6. Open a pull request that includes `Closes #<issue number>`, and ask a teammate to review it.
7. Merge after the review, then confirm the result works on the real hardware.

```bash
git checkout main && git pull
git checkout -b phase1/4-footage
# ...work with Claude Code...
git add -A && git commit -m "Add footage recording"
git push -u origin phase1/4-footage
gh pr create --title "Add footage recording" --body "Closes #4"
```

## Team Rules

- **Stay in the current phase.** When your task is done, help a teammate in the same phase.
- **Check in at every checkpoint.** Take five minutes: what is done, what is blocked, and are we ready to move on?
- **Agree before changing shared files.** Discuss changes to `config/` or the server data format as a team first.
- **Document wiring changes.** Record every change in `firmware/WIRING.md`.
- **Protect privacy.** Never commit passwords, keys or the training dataset. Delete any frame that shows a person before it is saved or shared.
- **Ask for help early.** If you are blocked for more than 20 minutes, tell the team.

## Version 2

After the Phase 3 checkpoint, and only if time allows, Kaleb will add outdoor temperature and moisture sensors to the Arduino to improve black ice detection.
