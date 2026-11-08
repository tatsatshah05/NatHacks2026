# ADR 0008: The black-ice rule and the "ice risk" wording

- **Status:** Accepted
- **Date:** November 7, 2026
- **Recorded in:** [PLAN.md](../../PLAN.md) section 7.4; [AGENTS.md](../../AGENTS.md#contracts) ("Black-ice rule"); pull request [#82](https://github.com/tatsatshah05/NatHacks2026/pull/82). Built in pull requests [#85](https://github.com/tatsatshah05/NatHacks2026/pull/85) (OP-109, the weather watch, merged 2026-11-07T23:57:52Z) and [#141](https://github.com/tatsatshah05/NatHacks2026/pull/141) (OP-205, camera ice risk, merged 2026-11-08T07:29:22Z)

## Context

Black ice is hard to see by definition. The model card's first known limit is that "a thin clear layer can look like a dry or wet road" ([vision/MODEL_CARD.md](../../vision/MODEL_CARD.md)). Claiming to detect it would overclaim, which PLAN.md section 15 lists as a risk.

## Decision

OpenPath never says "black ice detected". It reports **ice risk** from two signals.

- **The weather watch (city-wide)** is active when the air or the ground is at or below 0 °C and at least one of these holds: precipitation in the last 6 hours, air above 0 °C in the last 12 hours (melt and refreeze), or air within 1 °C of the dew point (frost).
- **A camera reading (local)** of `ice`, or of `wet` while the watch is active, sets `ice_risk` at medium confidence on the crossings within 50 m of that camera. A `wet` reading without freezing conditions is ignored.
- The watch covers every path equally, so it is a city-wide warning and never reroutes. Only local signals (cameras and community reports) change routes.

## Consequences

- `cloud/app/weather.py` holds the rule. `cloud/app/weather_service.py` refreshes it every 600 s, keeps the last good result when a refresh fails, and serves it on `GET /api/weather`.
- `cloud/app/camera_ice.py` applies the camera rule. A `wet` reading counts only when the watch is active and fresh (its last good refresh at most 30 minutes old, three refresh intervals); with a stale or missing watch it is left out and marked uncertain, with a warning. An `ice` reading counts on its own.
- Camera ice risks are placed on exact crossing edges (`footway=crossing` in OpenStreetMap) by `CameraIceHazardProvider`, so they never meet the matcher's rule that every `weather` item is city-wide.
- `ice_risk` multiplies cost by 3 for wheelchair and low vision, by 1.5 for standard, and removes the edge for walker or cane (`config/profiles.json`).
- The design system's hazard shapes and copy repeat the wording rule: "Ice risk", never "black ice detected" ([docs/design/DESIGN.md](../design/DESIGN.md)).
