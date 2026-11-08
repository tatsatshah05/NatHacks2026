# OP-212: accessibility review of designs

Purpose: review OpenPath's four main screen designs and current colour tokens before the remaining UI is built, and define evidence required for every UI PR.

Reviewed by Estella's coding agent on November 7, 2026, against `phase_2` commit `7ed1b65`. Scope: [OP-114 wireframes](design/OP-114-wireframes.md), `web/src/app/globals.css`, `web/src/components/map-page.tsx`, `map-view.tsx`, `obstruction-meta.ts` and `web/src/app/layout.tsx`. Governing story: [#55](https://github.com/tatsatshah05/NatHacks2026/issues/55).

This is a design/source review with computed contrast ratios. The browser connection failed during setup, so no live keyboard, axe, screen-reader, rendered target-size or zoom result is claimed. Those checks remain explicit gates in [web/ACCESSIBILITY.md](../web/ACCESSIBILITY.md). Full release/device conformance testing belongs to OP-408.

## Wireframes

| Area | Evidence in OP-114 | Review result / implementation requirement |
| --- | --- | --- |
| Planner reading/focus order | Labelled From/To/profile; keyboard search; errors after inputs; conditions repeated in text | Design supports the task. Preserve DOM/tab order, associate errors and place validation focus deliberately. Geolocation cannot be the only origin input. Runtime verification pending. |
| Route report and alternatives | Endpoints/profile/time and safety summary first; full text mode and numbered steps; keyboard stepper/text comparisons | Design provides map/colour alternatives. Each route needs a complete list/report even when tiles fail, plus alternate/save announcements. Runtime verification pending. |
| Alerts | Named on/off toggles, nonintrusive announcements, pause, remove confirmation/undo | Behavior notes are sound, but diagrams place recent alerts below saved routes/settings while notes require safety first. Follow the notes during implementation. Runtime focus/announcement verification pending. |
| City view | Labelled search/filter, announced count, equivalent list, icon/pattern/text legend, 200% stacked layout | Design covers non-colour indicators and text alternatives. Add 320 CSS px reflow and long-text checks. Verify tile failure does not remove list access. |
| Focus/targets | Menu Enter/Space/Escape and focus return; handoff requests 44px controls | Positive design intent. ASCII wireframes cannot prove rendered dimensions or focus appearance; current source has findings below. |
| Motion | Pausable announcements; global reduced-motion CSS | Explicitly check map-library animation, all-user pause controls where required, and safe flashing. CSS alone does not prove runtime behavior. |

## Measured token contrast

Method: read hex `:root` colours from `globals.css`; convert sRGB channels to linear values (`c/12.92` at or below 0.04045, otherwise `((c+0.055)/1.055)^2.4`); luminance = `0.2126 R + 0.7152 G + 0.0722 B`; ratio = `(lighter + 0.05)/(darker + 0.05)`. Compare unrounded values to thresholds; ratios below are displayed to two decimals. These are declared solid-colour pairs, not screenshot samples or a full rendered map audit.

| Actual pair | Light ratio | Dark ratio | Result for this pair |
| --- | --- | --- | --- |
| `--ink` on `--paper` | 15.31:1 | 16.25:1 | Pass normal text |
| `--ink` on `--surface` | 16.10:1 | 14.40:1 | Pass normal text |
| `--muted` on `--paper` | 6.05:1 | 10.33:1 | Pass normal text |
| `--muted` on `--surface` | 6.37:1 | 9.15:1 | Pass normal text |
| `--brand-dark` on `--paper` | 9.83:1 | 13.51:1 | Pass normal text |
| `--focus` on `--paper` | 3.33:1 | 11.23:1 | Pass project 3:1 indicator target on this background |
| `--focus` on `--surface` | 3.50:1 | 9.95:1 | Pass project 3:1 indicator target on this background |

Pattern backgrounds remain pale in dark mode, but `.map-marker` inherits `--ink: #edf5ef`. Its visible `.map-marker strong` label therefore has failing text contrast:

| Background | Dark marker text ratio |
| --- | --- |
| White `#ffffff` stripe | 1.11:1 |
| Cross `#f8d7a4` | 1.24:1 |
| Diagonal `#bde8d6` | 1.21:1 |
| Stripe `#ffd2cc` | 1.23:1 |
| Dot base `#e8ddff` | 1.17:1 |

The marker label is 7px text, so normal-text contrast applies. Passing base-text pairs does not establish whole-theme compliance. Gradients, dots, tiles, overlays and map focus still need rendered checks.

## Findings to track

1. **Dark-mode map label contrast — declared pairs fail.** Give labels an appropriate foreground/background in both themes; verify at least 4.5:1 over their patterns. Keep icons/patterns/text. Source: `.map-marker`, `.map-marker strong`, `.pattern-*` in `globals.css`. WCAG 1.4.3. Tracked in [#118](https://github.com/tatsatshah05/NatHacks2026/issues/118).
2. **Marker selection removes focused elements — source finding, runtime check required.** `map-view.tsx` removes/recreates markers in an effect depending on `selectedId`. Keyboard selection updates that state and removes the focused button. Keep marker elements stable across selection, or deliberately preserve focus. Reproduce Tab → marker → Enter → Tab; focus must not reset to body. WCAG 2.4.3, 2.4.7. Tracked in [#119](https://github.com/tatsatshah05/NatHacks2026/issues/119).
3. **Map zoom controls undershoot project target — source finding, rendered size pending.** Global button minimum height is 44px, but `.maplibregl-ctrl button` overrides it to 30px. Check MapLibre's width too. Set both dimensions to at least 44 CSS px, retaining labels/focus. A 30px control is not an automatic WCAG 2.5.8 failure: AA minimum is 24px with exceptions. Tracked in [#120](https://github.com/tatsatshah05/NatHacks2026/issues/120).

## Outcome

The design review is recorded and the [UI PR checklist](../web/ACCESSIBILITY.md) covers contrast, focus order/return, touch targets, text alternatives, status, reflow and motion with explicit evidence requirements. Track findings until implementation fixes are verified. This report does not approve the UI as WCAG-conformant or mark runtime/device checks as passed.
