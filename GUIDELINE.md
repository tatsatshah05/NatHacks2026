# PathSense Team Guideline

## Overview

PathSense plans accessible routes across Edmonton and lists every known barrier between A and B, using City of Edmonton open data, live weather, a vision model on public road cameras and community reports. There is no hardware. The full plan is in [PLAN.md](PLAN.md) ([PDF](docs/PathSense%20Plan.pdf)).

The team works through four phases, in order. Everyone works on the same phase at the same time. We move to the next phase only after the current phase passes its checkpoint.

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
| Tatsat | PS-101 Obstruction contract and store (3)<br>PS-104 City walking network (5) | PS-207 Profile routing with alternates (5)<br>PS-209 Google Maps and Apple Maps hand-off (2) | PS-304 Saved routes and push alerts (server) (5)<br>PS-305 Replay mode (3) | PS-401 Security and privacy review (3)<br>PS-402 Monitoring and runbook (3)<br>PS-403 Release v1.0 (2) |
| Ipsa | PS-108 Traffic disruptions adapter (2)<br>PS-112 Public camera access (3)<br>PS-115 Label guide and labelling tool (3) | PS-202 Train the road-condition model (5)<br>PS-203 Evaluate the model and write the model card (3) | PS-308 Community reports with photo check (5)<br>PS-309 Camera readings on the map (3) | PS-412 Model monitoring and fallback (3)<br>PS-413 Technical write-up (3)<br>PS-414 Backup demo video (2) |
| Estella | PS-109 Weather and black-ice watch (3)<br>PS-110 311 snow complaints and trail closures (3)<br>PS-111 Snow-clearing status adapter (2) | PS-201 Collect and label training data (5)<br>PS-212 Accessibility review of designs (3) | PS-306 Alerts page and push client (5)<br>PS-307 Accessibility settings (3) | PS-408 Accessibility audit (5)<br>PS-409 Pitch deck and Devpost (3) |
| Kaleb | PS-105 LRT elevator and escalator adapter (3)<br>PS-106 Map LRT elevators to the walking network (3)<br>PS-107 Construction sidewalk closures adapter (2) | PS-204 Camera vision worker (5)<br>PS-205 Black-ice risk from cameras and weather (3) | PS-310 City dashboard (3)<br>PS-311 Walk and LRT trips (5) | PS-406 End-to-end tests (5)<br>PS-407 Data quality checks (3) |
| Kashish | PS-113 Web app shell and live map (5)<br>PS-114 Wireframes for the main screens (2) | PS-210 Planner screens with mock data (5)<br>PS-211 Route report screen (3) | PS-301 Connect the planner to the live API (5)<br>PS-312 About, sources and privacy pages (2) | PS-410 Web polish and performance (5)<br>PS-411 Judge page and launch assets (2) |
| Matthew | PS-102 Deployable skeleton with CI (5)<br>PS-103 Source scheduler and status page (3) | PS-206 Match obstructions to the network (5)<br>PS-208 Route report (3) | PS-302 Address and place search (3)<br>PS-303 Live updates (5) | PS-404 Performance and load (5)<br>PS-405 Data backup and restore (2) |

## Story Points

Story points measure effort on the Fibonacci scale: 1, 2, 3, 5. As a rough team guide, one point is about one focused hour. Anything larger than 5 is split. Each person plans for about 8 points per phase, which leaves time for reviews, helping others and rest.

## Working on a Story

1. Open **Issues** and filter by your name label (`person: <your name>`) and the current phase milestone.
2. Check that the story's dependencies are merged, or that you can mock them.
3. Comment on the issue to show that you are working on it.
4. Create a branch from `main` named `ps-<id>-<short-name>`.
5. Use Claude Code with the issue as the task. Claude Code follows [CLAUDE.md](CLAUDE.md).
6. Open a pull request titled `PS-<id>: <story title>` that includes `Closes #<issue number>`, and ask a teammate to review it.
7. Merge after the review and a passing CI run, then check the preview deploy.

```bash
git checkout main && git pull
git checkout -b ps-105-lrt-outages
# ...work with Claude Code...
git add -A && git commit -m "Add LRT elevator and escalator outage adapter"
git push -u origin ps-105-lrt-outages
gh pr create --title "PS-105: LRT elevator and escalator adapter" --body "Closes #<issue number>"
```

## Definition of Done

- All acceptance criteria are met
- Tests are added and `make test` passes
- One teammate has reviewed and approved the pull request
- The preview deploy works
- Documentation is updated
- Any UI change passes the accessibility checklist in `web/ACCESSIBILITY.md`
- No secrets or images are committed

## Team Rules

- **Stay in the current phase.** When your stories are done, help a teammate in the same phase.
- **Check in.** 15-minute kickoff at each phase start; 5-minute stand-up every 4 hours (done, next, blocked); checkpoint review at each phase end.
- **Agree before changing shared contracts.** Discuss changes to `config/`, `cloud/app/schemas.py` or the API in the Technical Spec comments first.
- **Use official data access only.** No scraping. City camera streams only with written permission.
- **Protect privacy.** Never commit keys, secrets or images. Camera frames and report photos are never stored.
- **Measure, never guess.** Every number in the app, the slides or the Devpost comes from a measurement or a cited source.
- **Ask for help early.** If you are blocked for more than 20 minutes, tell the team.
