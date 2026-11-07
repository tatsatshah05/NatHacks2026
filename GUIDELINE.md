# OpenPath Team Guideline

## Overview

OpenPath plans accessible routes across Edmonton and lists every known barrier between A and B, using City of Edmonton open data, live weather, a vision model on public road cameras and community reports. There is no hardware. The full plan is in [PLAN.md](PLAN.md) ([PDF](docs/OpenPath%20Plan.pdf)).

The team works through four phases, in order. Everyone works on the same phase at the same time. We move to the next phase only after the current phase passes its checkpoint.

Each phase has its own branch: `phase_1`, `phase_2`, `phase_3` and `phase_4`. During a phase, every pull request goes into that phase's branch. When the checkpoint passes, the scrum master releases the phase branch into `main` and starts the next phase branch from `main`.

## Phases

| Phase | Hours | Goal | Checkpoint |
| --- | --- | --- | --- |
| 1. Data Foundation | 0–10 | Bring every city data source into one live obstruction feed, build the walking network, and deploy the skeleton. | Live items from every Must source appear in `GET /api/obstructions`; `GET /api/status` shows each source's freshness; the city walking network loads within its memory budget; the API and web app deploy from `main` with CI passing; the 511 Alberta camera list is counted. |
| 2. Vision and Routing | 10–22 | Train the road-condition model on public camera images, and turn obstructions into profile-aware routes with a full report. | The model meets its target on cameras it never saw in training; camera readings and the black-ice watch reach the obstruction feed; `GET /api/route` returns routes with alternates and a full report; a test elevator outage reroutes a wheelchair route. |
| 3. Web App and Alerts | 22–34 | Deliver the complete experience on a phone: search, plan, report, live updates and push alerts. | On a phone, the deployed site plans a route anywhere in Edmonton, shows the full report, updates live when an obstruction changes, sends a push alert for a saved route, and opens the route in Google Maps. |
| 4. Production and Launch | 34–48 | Harden, measure, document and launch. Feature freeze at hour 40; submit by hour 46. | The release checklist in PLAN.md is complete, `v1.0` is tagged, the demo is rehearsed five times, and the project is submitted by hour 46. |

## Assignments

Each line is one user story: its ID, its title and its story points. Every story is a GitHub issue with the user story, acceptance criteria, points, priority and dependencies.

| Team member | Phase 1: Data Foundation | Phase 2: Vision and Routing | Phase 3: Web App and Alerts | Phase 4: Production and Launch |
| --- | --- | --- | --- | --- |
| Tatsat | OP-101 Obstruction contract and store (3)<br>OP-104 City walking network (5) | OP-207 Profile routing with alternates (5)<br>OP-209 Google Maps and Apple Maps hand-off (2) | OP-304 Saved routes and push alerts (server) (5)<br>OP-305 Replay mode (3) | OP-401 Security and privacy review (3)<br>OP-402 Monitoring and runbook (3)<br>OP-403 Release v1.0 (2) |
| Ipsa | OP-108 Traffic disruptions adapter (2)<br>OP-112 Public camera access (3)<br>OP-115 Label guide and labelling tool (3) | OP-202 Train the road-condition model (5)<br>OP-203 Evaluate the model and write the model card (3) | OP-308 Community reports with photo check (5)<br>OP-309 Camera readings on the map (3) | OP-412 Model monitoring and fallback (3)<br>OP-413 Technical write-up (3)<br>OP-414 Backup demo video (2) |
| Estella | OP-109 Weather and black-ice watch (3)<br>OP-110 311 snow complaints and trail closures (3)<br>OP-111 Snow-clearing status adapter (2) | OP-201 Collect and label training data (5)<br>OP-212 Accessibility review of designs (3) | OP-306 Alerts page and push client (5)<br>OP-307 Accessibility settings (3) | OP-408 Accessibility audit (5)<br>OP-409 Pitch deck and Devpost (3) |
| Kaleb | OP-105 LRT elevator and escalator adapter (3)<br>OP-106 Map LRT elevators to the walking network (3)<br>OP-107 Construction sidewalk closures adapter (2) | OP-204 Camera vision worker (5)<br>OP-205 Black-ice risk from cameras and weather (3) | OP-310 City dashboard (3)<br>OP-311 Walk and LRT trips (5) | OP-406 End-to-end tests (5)<br>OP-407 Data quality checks (3) |
| Kashish | OP-113 Web app shell and live map (5)<br>OP-114 Wireframes for the main screens (2) | OP-210 Planner screens with mock data (5)<br>OP-211 Route report screen (3) | OP-301 Connect the planner to the live API (5)<br>OP-312 About, sources and privacy pages (2) | OP-410 Web polish and performance (5)<br>OP-411 Judge page and launch assets (2) |
| Matthew | OP-102 Deployable skeleton with CI (5)<br>OP-103 Source scheduler and status page (3) | OP-206 Match obstructions to the network (5)<br>OP-208 Route report (3) | OP-302 Address and place search (3)<br>OP-303 Live updates (5) | OP-404 Performance and load (5)<br>OP-405 Data backup and restore (2) |

## Story Points

Story points measure effort on the Fibonacci scale: 1, 2, 3, 5. As a rough team guide, one point is about one focused hour. Anything larger than 5 is split. Each person plans for about 8 points per phase, which leaves time for reviews, helping others and rest.

## Working on a Story

1. Open **Issues** and filter by your name label (`person: <your name>`) and the current phase milestone.
2. Check that the story's dependencies are merged, or that you can mock them.
3. Comment on the issue to show that you are working on it.
4. Create a branch from the current phase branch (for example `phase_1`) named `op-<id>-<short-name>`.
5. Give the issue to your coding agent (Claude Code, Codex, Cursor or Copilot) as the task. Every agent follows [AGENTS.md](AGENTS.md).
6. Open a pull request **into the phase branch** (`OP-1xx` into `phase_1`, `OP-2xx` into `phase_2`, and so on), titled `OP-<id>: <story title>`, that includes `Closes #<issue number>`. The template lists the Definition of Done.
7. Wait for the `CI passed` check. Tatsat or Ipsa reviews and approves; one approval is enough. Turn on **Enable auto-merge**, and the pull request merges itself once both are green. See [docs/CI_CD.md](docs/CI_CD.md).

```bash
git checkout phase_1 && git pull
git checkout -b op-105-lrt-outages
# ...work with your coding agent...
git add -A && git commit -m "Add LRT elevator and escalator outage adapter"
git push -u origin op-105-lrt-outages
gh pr create --base phase_1 --title "OP-105: LRT elevator and escalator adapter" --body "Closes #<issue number>"
```

## Definition of Done

- All acceptance criteria are met
- Tests are added and `make test` passes
- `CI passed` is green, and Tatsat or Ipsa has approved the pull request
- The preview deploy works
- Documentation is updated
- Any UI change passes the accessibility checklist in `web/ACCESSIBILITY.md`
- No secrets or images are committed

## Team Rules

- **Never push to `main` or a phase branch.** Both are protected: every change goes through a pull request into the current phase branch.
- **Stay in the current phase.** When your stories are done, help a teammate in the same phase.
- **Check in.** 15-minute kickoff at each phase start; 5-minute stand-up every 4 hours (done, next, blocked); checkpoint review at each phase end.
- **Agree before changing shared contracts.** Discuss changes to `config/`, `cloud/app/schemas.py` or the API with the team first, and record the decision in PLAN.md.
- **Use official data access only.** No scraping. City camera streams only with written permission.
- **Protect privacy.** Never commit keys, secrets or images. Camera frames and report photos are never stored.
- **Measure, never guess.** Every number in the app, the slides or the Devpost comes from a measurement or a cited source.
- **Ask for help early.** If you are blocked for more than 20 minutes, tell the team.
