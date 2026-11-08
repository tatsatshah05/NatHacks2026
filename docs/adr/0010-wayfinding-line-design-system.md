# ADR 0010: The wayfinding-line design system

- **Status:** Accepted
- **Date:** November 8, 2026
- **Recorded in:** [docs/design/DESIGN.md](../design/DESIGN.md); pull request [#156](https://github.com/tatsatshah05/NatHacks2026/pull/156) ("Phase 3 design foundation", merged 2026-11-08T16:18:47Z)

## Context

Pull request #156 records that the earlier web look was rejected as generic: Arial, a marketing hero with an uppercase eyebrow, identical rounded cards with shadows, and a green brand. Phase 3 added several screens at once, so they needed one visual system first. The people OpenPath serves often use it on a phone, outdoors, in winter, and many use a wheelchair, a walker or cane, or have low vision (DESIGN.md section 1).

## Decision

- **The route is the interface.** Each route is drawn like a transit line diagram: a vertical line in the action colour from the start to the end, with every known condition as a stop drawn as its own shape with a text label. Conditions the route avoids sit beside the line.
- **Shape before colour.** Every condition has a shape, a fill pattern and words, so it reads in greyscale, in glare and in a screen reader.
- **Quiet chrome.** Flat panels with one ink rule on top; no shadows, gradients or decoration. The route line is the one bold element.
- **One type family**, Atkinson Hyperlegible, drawn by the Braille Institute for low-vision readers.
- **Five base colours**, with hazard colours only on hazard shapes and their labels, and a dark theme.
- **One reveal**: the line draws itself once when routes arrive (600 ms); nothing else moves.
- **Plain words**: sentence case, verbs on buttons, errors that say what to do.

## Consequences

- Every Phase 3 screen is built from DESIGN.md, with tokens in `web/src/app/globals.css` and components in `web/src/components/ui/`. A screen that needs something new adds it to DESIGN.md in the same pull request.
- The map is never the only source of information: every route is also a text list of steps and a grouped report (AGENTS.md, Accessibility Requirements).
- Conditions that cover the whole city are shown once, above the routes, not as stops on each line.
