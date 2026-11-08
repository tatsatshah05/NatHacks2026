# Changelog

All notable changes to OpenPath, built from the merged pull requests on GitHub (`gh pr list --state merged`). Each line names its pull request. Pull requests that were closed without merging are left out.

**Releases.** `v1.0` is tagged at the feature freeze, at hour 40 (OP-403). After the freeze, only bug fixes land. No version is tagged yet. In this file, "released into `main`" means a phase release that deployed to production.

## Unreleased

### Phase 4: Production and Launch

No Phase 4 pull request has merged yet.

### Pending merge into `phase_3`

This pull request is open, not merged. It is included in the Phase 4 base that the first Phase 4 branches start from, so it is listed here until it merges.

- OP-310: The city dashboard, with OP-309 camera readings on the map (#167).

### Phase 3: Web App and Alerts (merged into `phase_3`, not yet released into `main`)

#### Added

- OP-301: The planner connected to the live API: place search, live routes, map markers and links that reopen a trip (#166).
- OP-302: Address and place search on `GET /api/search`, from a prebuilt index of City parcel addresses, LRT stations and named places (#151).
- OP-303: Live updates on `WS /ws`: source status, obstruction changes and route changes for a subscribed route (#155).
- OP-304: Saved routes and Web Push alerts on the server, with the route watcher (#150).
- OP-305: Replay mode with recorded snapshots, a running replay clock and a recorded freeze-thaw night (#152).
- OP-306: The alerts page and the push client in the browser, with the service worker and an installable app (#161).
- OP-307: Accessibility settings on the device, with OP-312's about, sources and privacy pages (#160).
- OP-308: Community reports with an optional photo check (#153).
- OP-311: Walk and LRT trips behind the `LRT_TRIPS` flag (#154).
- OP-313: Each LRT outage lists the other elevators and escalators in service at the station (#157).
- A snow and ice model beside the main camera model, and the day-and-night model card (#164).

#### Changed

- The Phase 3 design foundation: the wayfinding-line design system, its tokens and components, and the existing screens moved onto it (#156).
- Report photos are checked by the ONNX model and Claude together, and the API on Render installs what both checks need (#158).
- Vercel creates deployments only for `main`, because preview deployments used up the Hobby plan's daily limit (#169).

#### Fixed

- Every thread that uses the store stops before the database closes, which ended a segmentation fault in CI (#159).
- Over-long City text is shortened at a word instead of the item being dropped (#163).
- The report photo disk-write test ignores Python's own cache writes, which failed on fresh CI runners (#168).
- Access to the shared in-memory SQLite connection is serialised, one thread at a time (#165).

## Phase 2: Vision and Routing (released into `main` on November 8, 2026)

#### Added

- OP-201: Collect and label the training data from 511 Alberta cameras (#138).
- OP-202: The road-condition training pipeline (#139).
- OP-203: The evaluation script and the model card (#140), then the measured model card for the night model (#144).
- OP-204: The camera vision worker (#145), which posts a reading only when the model and Claude agree (#146).
- OP-205: Black-ice risk from cameras and weather, on crossings near the camera (#141).
- OP-206: Matching obstructions to the walking network (#116).
- OP-207: Profile routing with alternates on `GET /api/route` (#108).
- OP-208: The route report on every route (#133).
- OP-209: The Google Maps and Apple Maps hand-off (#109).
- OP-210: The planner screens with mock data (#142).
- OP-211: The route report screen (#143).
- OP-212: The accessibility review of the designs and the checklist in `web/ACCESSIBILITY.md` (#121).
- OP-213: The camera ingest endpoint and the camera readings API (#114).

#### Changed

- The Phase 1 release was brought into `phase_2` (#105).
- Phase releases go through a branch that already contains `main`, and the live deployment is recorded in `docs/DEPLOY.md` (#107).
- `phase_2` was released into `main` early, so the live API had the camera ingest endpoint before the camera worker ran (#134).
- The camera worker runs on a team laptop during the demo, because Render's free plan has no background workers (#126); it runs on Tatsat's laptop, including on demo day (#147).
- Phase 2 was released into `main` (#148).

#### Fixed

- Camera ingest review fixes: the reading's source is stored, an older reading never replaces a newer one, and `mode` must equal `DATA_MODE` (#123).
- Each route end snaps to a node connected to the main network, so a wheelchair route no longer gets stuck in a pocket cut off by stairs and a closure (#135).
- The replay clock now applies to obstructions and route hazards, not only the scheduler, so replay with `REPLAY_AT` shows the recorded closures (#136).
- The deploy job runs after CI passes on a push; before this it never ran (#137).

## Phase 1: Data Foundation (released into `main` on November 8, 2026)

#### Added

- OP-101: The Obstruction contract and the store (#93).
- OP-102: The deployable skeleton with CI: the FastAPI app, `GET /health`, the Makefile, `render.yaml` and `.env.example` (#91).
- OP-103: The source scheduler and `GET /api/status` (#100).
- OP-104: The city walking network, prebuilt from OpenStreetMap (#97).
- OP-105: The LRT elevator and escalator outage adapter (#83).
- OP-106: The proposed mapping of LRT elevators to the walking network (#98).
- OP-107: The construction sidewalk closures adapter (#88).
- OP-108: The traffic disruptions adapter (#99).
- OP-109: The weather and black-ice watch (#85).
- OP-110: The 311 snow complaints and trail closures adapters (#92).
- OP-111: The snow-clearing status adapter (#96).
- OP-112: Public camera access through the 511 Alberta API (#89).
- OP-113: The web app shell and live map (#94).
- OP-114: Wireframes for the main screens (#95).
- OP-115: The label guide and the labelling tool (#101).

#### Changed

- Story pull requests go into the current phase branch, and phase branches deploy to staging (#87).
- `main` was merged into `phase_1` before the Phase 1 release (#103).
- Phase 1 was released into `main` (#104).

## Planning (November 7, 2026, into `main`)

#### Added

- The project guideline, the Claude Code setup and the phase prompts (#1).
- The Work Divided document and the planning updates that followed (#27).
- The OpenPath city-data plan (version 2), which replaces the hardware design, with the user stories and AGENTS.md (#82).
- The CI/CD pipeline and pull request rules: the required `CI passed` check, repository hygiene and the production deploy job (#84).

#### Changed

- The project was renamed to OpenPath, AGENTS.md was added for every coding agent, and the merge rules were documented (#86).
- Ipsa and Kashish became code owners on `main`, so any of the three reviewers can approve a release (#90).
