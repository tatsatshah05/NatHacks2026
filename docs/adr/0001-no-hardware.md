# ADR 0001: No hardware

- **Status:** Accepted
- **Date:** November 7, 2026
- **Recorded in:** [PLAN.md](../../PLAN.md) version 2.0, sections 1 and 3; [AGENTS.md](../../AGENTS.md#decisions-already-made); pull request [#82](https://github.com/tatsatshah05/NatHacks2026/pull/82) (merged 2026-11-07T20:49:27Z)

## Context

Version 1 of the project, named PathSense, was a hardware design: one window camera and one curb-cut sensor. PLAN.md section 3 lists what went wrong with it. The camera module (OV7670) could not be used, one camera covered one street, the demo depended on snow, night lighting and one window, filming a public sidewalk risked recording people, and outdoor sensors needed sealing against cold and cabling.

## Decision

OpenPath uses no hardware. Everything runs on public data, public road cameras and community reports. The vision model reads 511 Alberta cameras and community report photos instead of a camera the team owns.

## Consequences

- Coverage comes from City of Edmonton open data, which covers the whole city and is live every day (elevators, closures, construction). A labelled Replay mode covers winter scenarios that may not happen during the event.
- Camera coverage depends on where 511 Alberta places its cameras, mostly on provincial highways. The recorded metadata has 19 cameras inside the approximate Edmonton bounding box ([docs/DATA_SOURCES.md](../DATA_SOURCES.md)).
- The version 1 material is archived in `docs/archive/v1-hardware/` and is not used for new work.
- The product was renamed from PathSense to OpenPath, because the old name described sensors the project no longer uses (pull request #82).
