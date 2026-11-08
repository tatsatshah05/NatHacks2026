# Accessibility checklist for every UI pull request

Purpose: keep OpenPath usable with keyboard, screen reader, touch and magnification. This OP-212 review gate follows WCAG 2.2 AA and the project's additional requirements. It does not claim the app already conforms.

The [design review](../docs/ESTELLA_OP212.md) records the wireframes, measured token contrast, findings and verification limits. Full device/screen-reader release testing belongs to OP-408.

## Evidence required in each UI PR

- [ ] List affected screens and test the changed flow with keyboard only. Record browser/device, commit, viewport and theme.
- [ ] Attach relevant desktop/mobile, light/dark and focus-state evidence. Describe screen-reader announcements in words; screenshots cannot demonstrate them.
- [ ] Run the available automated accessibility scan and report its tool/version, scope and result. Never claim an axe/Lighthouse result without running it.
- [ ] Complete applicable checks below. Explain N/A items; link an issue and mitigation for unresolved failures. An unchecked safety-critical flow is not approval.

## Keyboard and focus

- [ ] Every action works with Tab, Shift+Tab, Enter/Space and appropriate arrow keys. Use native controls and provide equivalents for map-only interactions (2.1.1).
- [ ] Reading/tab order follows the task. No keyboard trap; users can leave maps, menus and dialogs. Provide a way to bypass repeated navigation (2.1.2, 2.4.1, 2.4.3).
- [ ] Focus is visible in both themes, including over maps, and not wholly hidden by overlays/sticky panels. Custom indicators meet the project's 3:1 contrast target against adjacent backgrounds (1.4.11, 2.4.7, 2.4.11).
- [ ] Modal opening places focus appropriately; Escape closes and returns focus to its trigger. Selecting a marker, refreshing or choosing an alternate does not remove the focused element or reset focus to the document body.
- [ ] Address results have a labelled input and keyboard-operable list. Associate errors with fields and preserve entered values; announce result status (3.3.1, 3.3.2, 4.1.2, 4.1.3).

## Contrast, colour and touch

- [ ] Measure actual text/background pairs in both themes: include patterned marker labels, selected/hover/disabled states and overlays. Normal text needs 4.5:1; large text needs 3:1. Large means 24 CSS px regular or approximately 18.67 CSS px bold (1.4.3).
- [ ] Meaningful custom controls/state graphics have 3:1 contrast against adjacent colours (1.4.11). Decorative separators do not automatically need this ratio; identify which graphics convey information.
- [ ] Kind and selected-route identity use text and another cue such as icon, pattern or shape; never colour alone (1.4.1).
- [ ] Primary controls, map controls, marker targets and icon buttons meet the **project's 44 × 44 CSS px target**, including width and height. A minimum-height rule alone is insufficient. WCAG 2.2 AA 2.5.8 specifies 24 × 24 with exceptions; our target is stricter.

## Text alternatives and announcements

- [ ] Maps have an equivalent keyboard-accessible text flow: endpoints/profile, route choices with time/distance, numbered steps and full obstruction report. It works without tiles/WebGL; a legend alone is insufficient (1.1.1, 1.3.1).
- [ ] Visible labels agree with accessible names. Icon buttons have action names; decorative icons are hidden. Selected/expanded/toggle state is programmatic (2.5.3, 4.1.2).
- [ ] Loading, success, result counts and empty states have suitable status semantics without moving focus. Errors, stale data, freshness and Replay mode are explained in text (4.1.3).
- [ ] Safety-critical route changes use the project's concise assertive announcement. Routine updates stay polite without repeated interruption. Respect speech/vibration preferences; settings have named on/off states.

## Zoom, reflow and motion

- [ ] At 200% text zoom, no essential text, control or safety information is clipped. Test long names/details, not just short mock strings (1.4.4).
- [ ] At 320 CSS px content width, ordinary content reflows without two-dimensional page scrolling. Check mobile and browser zoom. Maps may require two-dimensional layout; the exception does not cover adjacent forms, navigation or reports (1.4.10).
- [ ] Text spacing leaves all functionality available: line-height 1.5, paragraph spacing 2em, letter spacing 0.12em and word spacing 0.16em (1.4.12).
- [ ] Reduced motion disables nonessential CSS and map-library animation, including fly-to, smooth scrolling and pulsing loaders. A stylesheet rule alone does not verify library behavior.
- [ ] Automatically moving/updating content has required pause/stop/hide controls where applicable; no dangerous flashing (2.2.2, 2.3.1). Reduced motion does not replace all users' pause controls. Do not add time limits to route choices or warnings.

## Screen-specific walkthrough

| Screen | Required order and equivalent access |
| --- | --- |
| Planner | Origin → destination → profile → find route → result status → choices/report. Denied geolocation has a text-entry fallback. Search/map choices also work without the map. |
| Route report | Endpoints/profile → safety summary/freshness → route choice/time → numbered steps → grouped conditions/sources → save/share/Maps. Alternatives have text comparisons. |
| Alerts | New safety-relevant changes before secondary settings. Notification denied/offline states remain operable. Confirm/undo removal and return focus. |
| City view | Heading/freshness → search/filter → count → equivalent list/map. Filters announce counts; list includes complete condition information. |

Verify against the final responsive DOM. OP-114's alert diagrams and prose differ on priority; use safety-first ordering during implementation.

## Sources

- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) and [Understanding WCAG 2.2](https://www.w3.org/WAI/WCAG22/Understanding/).
- [Contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html), [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).
- [Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html), [focus not obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html), [status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html), [pause/stop/hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html).
