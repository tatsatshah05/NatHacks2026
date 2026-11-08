# Accessibility: the audit and the checklist

This file has three parts. **Part 1** is the OP-408 audit of the whole web app: measured results, the fixes they led to and what is still open. **Part 2** is the screen reader walkthrough for Estella (VoiceOver on iOS Safari and TalkBack on Android Chrome), written as a step-by-step script and **not yet run**. **Part 3** is the OP-212 checklist every UI pull request follows.

Every number in Part 1 names the tool and version, the viewport and the date it was measured. Nothing here is an estimate. The checklist in Part 3 does not claim the app conforms; Part 1 says what was measured and what was found.

## Part 1. Accessibility audit (OP-408)

### Summary

Measured on November 8, 2026 (19:36 to 20:00 UTC) on commit `ed8966617345da39eabd4bf06d5092b849c1fa6e` (the Phase 4 integration), with axe-core 4.14.0 through @axe-core/playwright 4.14.0, Playwright 1.64.1 and its headless Chromium 156.0.8078.4, Node 24.5.0, on the production build (Next.js 15.5.29, MapLibre GL 6.17.0) against the API in replay mode with the clock frozen at 2026-11-10T18:00:00Z:

- **axe-core:** 0 violations of any impact in all 44 page-contexts (11 pages, each at 390 x 844 and 1280 x 800, light and dark); 18 to 29 rules passed per context. One "needs review" item (`color-contrast` on the planner with routes) is checked by hand in section 1.
- **Keyboard:** 22 Tab walks (11 pages at 390 and 1280 px), 479 stops in all, every stop with the 3 px focus ring and inside the viewport; every walk ends by leaving the document, so nothing traps focus. The skip link works on every page. Escape closes the place suggestions, the report dialog, the save form and the remove confirmation, and focus returns to the control that opened them.
- **The announced route change:** a seeded closure was read into the `aria-live="assertive"` region as one sentence, spoken and vibrated when those settings are on and neither when they are off.
- **200% zoom and 320 px:** 0 px of horizontal overflow and 0 clipped text elements on all 11 pages in both settings.
- **Reduced motion:** 0 animated or transitioning elements on all 11 pages with `prefers-reduced-motion: reduce` (8 without it, all intended).
- **Never colour alone:** every hazard mark on the city legend, the city list, the route report, the planner's route lines and the alerts page is a distinct shape and fill with words beside it; the map markers are hidden from assistive technology and the lists carry everything.
- **Touch targets at 390 px:** two of the app's own elements were under 44 x 44 CSS px (the footer's Demo link at 41.5 px wide, and MapLibre's attribution toggle at 24 x 24, which the app styles); both are fixed on this branch. Text links inside prose and list cells are 20 px tall and are listed, not changed.
- **Fixes on this branch (one commit each):** the route report lost focus, its announcement and refetched the routes when a route was chosen; the planner scrolled smoothly to the map under the "Always reduce" motion setting; the footer Demo link; the attribution toggle. Section "Findings, fixes and follow-ups" has the details and the follow-ups left for others.
- **Not run:** VoiceOver and TalkBack. Part 2 is the script for them.

### How it was measured

- **Date:** November 8, 2026. The sections ran between 19:36 and 20:00 UTC.
- **Commit:** `ed8966617345da39eabd4bf06d5092b849c1fa6e` ("before"). The fixes were measured again afterwards on this branch; see "Measured again after the fixes".
- **Machine:** a MacBook with an Apple M4 and 16 GB, macOS 26.3, on mains power, nothing else running except the two servers below and the audit.
- **Servers:** the API from this repository in replay mode, `DATA_MODE=replay REPLAY_SNAPSHOT=cloud/tests/fixtures REPLAY_AT=2026-11-10T18:00:00Z DATABASE_URL=sqlite:///…/api.db ADMIN_KEY=test WORKER_KEY=test CORS_ORIGINS=http://localhost:3811 SCHEDULER=on uvicorn cloud.app.main:app --port 8811` (no real key in the environment), and the web app's production build (`npm run build` with `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_API_URL` set to `http://localhost:8811` and `NEXT_PUBLIC_WS_URL` to `ws://localhost:8811/ws`, then `npx next start -p 3811`).
- **Browser:** Playwright 1.64.1 with its Chromium 156.0.8078.4 in headless mode. Two viewports: 390 x 844 CSS px with device scale 3, `isMobile` and touch (a phone), and 1280 x 800 (a laptop). Light and dark through `colorScheme` emulation. Locale `en-CA`, time zone `America/Edmonton`. Map tiles from OpenFreeMap were **not** blocked, and this Chromium has WebGL2, so both maps mounted (one `.maplibregl-canvas` on the planner, the planner with routes and the city page). Each page was scrolled from top to bottom once before measuring, so the lazily mounted maps were present.
- **Pages:** `/` (planner, empty), `/?from=…&to=…&profile=wheelchair` (the planner with routes), `/route?…&route=1` (the report for that trip), `/alerts` (with one saved route, seeded through `POST /api/saved-routes` under the device id every browser context was given), `/city`, `/settings`, `/about`, `/sources`, `/privacy`, `/demo` and `/no-such-page` (the 404 page).
- **The trip:** the judge page's trip, Edmonton Convention Centre (53.541286, -113.486191) to the Alberta Legislature (53.536229, -113.510275) for a wheelchair. At the frozen clock the API gives three routes; route 1 is 43 min over 2.6 km with "Escalator out of service" 14 m from it (take care), "Trail closed: McKay School Park" avoided (path closed) and "Road partly closed" on it (take care), plus the warning that the start point moves 20 m to the nearest connected path. The other candidate trips (the end-to-end tests' Rogers Place to MacEwan University, and Central Station to the Royal Alexandra Hospital) have no conditions at that clock, so they would have left the route line and the report empty.
- **The script:** `web/scripts/a11y-audit.mjs` (`cd web && npm run a11y`), committed on this branch. It uses only `@playwright/test` and `@axe-core/playwright`, both already devDependencies. It writes `web/test-results/a11y/results.json` (gitignored) and one full-page screenshot per page and context; the copies behind this document are on the measuring machine, not in the repository (no images are committed). The sections and what each measures are described below; the script measures and writes, and this document reads.
- **axe-core rules:** the tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` and `wcag22aa`, the same set as `web/e2e/accessibility.spec.ts`, with no rule excluded.
- **Accessible names in the tables** were computed by the script from `aria-labelledby`, `aria-label`, the associated label or the text content, in that order. A name built from visible text plus a visually hidden span ("Save this route" + ", route 1") is shown as the browser would expose it, "Save this route, route 1".

### 1. axe-core: violations by impact

axe-core 4.14.0, 44 page-contexts, November 8, 2026, 19:36 to 19:44 UTC (planner with routes re-measured at 20:00 UTC with the same result). Each page was scanned at 390 x 844 and 1280 x 800, in light and in dark. Zero serious or critical is the acceptance bar; minor and moderate are listed too.

| Page | Critical | Serious | Moderate | Minor | Rules passed (390 light, 390 dark, 1280 light, 1280 dark) | Needs review |
| --- | --- | --- | --- | --- | --- | --- |
| `/` planner, empty | 0 | 0 | 0 | 0 | 25, 25, 25, 25 | 0 |
| `/?from=…` planner with routes | 0 | 0 | 0 | 0 | 28, 28, 28, 28 | `color-contrast`: 6, 6, 7, 7 nodes (below) |
| `/route?…` route report | 0 | 0 | 0 | 0 | 25, 25, 25, 25 | 0 |
| `/alerts` with one saved route | 0 | 0 | 0 | 0 | 23, 23, 23, 23 | 0 |
| `/city` | 0 | 0 | 0 | 0 | 29, 29, 29, 29 | 0 |
| `/settings` | 0 | 0 | 0 | 0 | 23, 23, 23, 23 | 0 |
| `/about` | 0 | 0 | 0 | 0 | 23, 23, 23, 23 | 0 |
| `/sources` | 0 | 0 | 0 | 0 | 24, 24, 24, 24 | 0 |
| `/privacy` | 0 | 0 | 0 | 0 | 23, 23, 23, 23 | 0 |
| `/demo` | 0 | 0 | 0 | 0 | 23, 23, 23, 23 | 0 |
| `/no-such-page` (404) | 0 | 0 | 0 | 0 | 18, 18, 18, 18 | 0 |

The counts are the same in all four contexts of every page: **0 violations, 0 elements, in all 44 page-contexts.**

**The one "needs review" item, checked by hand.** On the planner with routes, axe could not compute `color-contrast` for 6 nodes at 390 px and 7 at 1280 px (the same in light and dark): the status words of the two condition stops on route 1 ("14 m from this route", "On this route") and their "Checked just now" lines, because the stop row draws the 4 px route line with a `::before` pseudo element ("background color could not be determined due to a pseudo element"); and at 1280 px the "Start" tag on the map, which overlaps the marker. These are not failures axe found; they are places it stopped. Their pairs are the design tokens whose ratios are recorded beside the tokens in `web/src/app/globals.css` and in `docs/design/DESIGN.md` section 11 (WCAG relative luminance, computed with a script on November 8, 2026): the status words are `--ink` on `--card`, `#0D1F2D` on `#FFFFFF`, 16.8:1 in light and `#F2F6F9` on `#16293A`, 13.7:1 in dark; the "Checked" lines are `--muted` on `--card`, `#4A5B6A` on `#FFFFFF`, 7.0:1 in light and `#A9B8C6` on `#16293A`, 7.3:1 in dark; the Start tag is `#0D1F2D` on its white tag, 16.8:1, on both map palettes.

### 2. Keyboard-only walkthrough

Playwright 1.64.1, Chromium 156.0.8078.4, November 8, 2026, 19:54 to 20:00 UTC. Each page at 1280 x 800 and at 390 x 844, light theme. The script pressed Tab from the top of the document until focus left the document or returned to a stop it had seen, and recorded each stop's role, accessible name, state, whether the element matched `:focus-visible`, which element carried the outline (the control itself, or the field box or label around a visually hidden input) and whether it was inside the viewport.

#### The skip link

On every one of the 11 pages, at both widths, the first Tab landed on the link "Skip to main content", which moved into view (its top edge went from -200 px to 12 px) with the focus ring. Enter on it, then Tab, landed inside `<main>` on:

| Page | First stop after the skip link |
| --- | --- |
| planner, empty and with routes | textbox "From" |
| route report | link "Back to the planner" |
| alerts | link "Open route Audit route" |
| city | disclosure "How the ranking works" |
| settings | switch "Speak route changes" |
| about | link "model card" |
| sources | link "City of Edmonton Open Data Terms of Use" |
| privacy | link "Alerts page" |
| demo | link "openpath-lyart.vercel.app" |
| 404 | link "Plan a route" |

#### Tab order, as accessible names (1280 x 800, light)

Every page starts with the same seven header stops: link "Skip to main content"; link "OpenPath"; link "Plan"; link "City"; link "Alerts"; link "Settings"; link "About" (the current page's link carries `aria-current="page"`; on `/sources` and `/privacy`, "About" carries `aria-current="true"`). The lists below start after the header. Native radio groups are one stop each (arrow keys move inside them). The 390 x 844 order has exactly the same stops on every page except the city page, where 10 rows are shown instead of 25 ("Show 10 more" instead of "Show 25 more"), so 39 stops instead of 54.

- **Planner, empty (24 stops):** textbox "From"; button "Use my current location"; textbox "To"; radio group "How do you get around?" on "Wheelchair"; checkbox "Include LRT trips"; button "Find routes"; the map canvas, region "Map of the routes. The route list has the same information."; button "Zoom in"; button "Zoom out"; disclosure "Toggle attribution"; link "OpenFreeMap"; link "© OpenMapTiles"; link "OpenStreetMap"; button "Report a problem" (`aria-haspopup="dialog"`); link "Data sources"; link "Privacy"; link "Demo".
- **Planner with routes (31 stops):** textbox "From"; button "Use my current location"; textbox "To"; radio group on "Wheelchair"; checkbox "Include LRT trips"; button "Find routes"; checkbox "Speak route changes aloud" (checked); link "View route report for route 1"; button "Save this route, route 1"; link "Open in Google Maps, route 1"; link "Open in Apple Maps, route 1"; button "Show route 2" (`aria-expanded="false"`); button "Show route 3" (`aria-expanded="false"`); the map canvas "Map of the routes. The route list has the same information."; button "Zoom in"; button "Zoom out"; disclosure "Toggle attribution"; link "OpenFreeMap"; link "© OpenMapTiles"; link "OpenStreetMap"; button "Report a problem"; link "Data sources"; link "Privacy"; link "Demo".
- **Route report (25 stops):** link "Back to the planner"; radio group "3 routes for this trip" on "Route 1, recommended: 43 min, 2.6 km. Avoids Trail closed: McKay School Park"; link "View source for Escalator out of service"; link "View source for Trail closed: McKay School Park"; link "View source for Road partly closed"; links "Dataset for LRT elevators and escalators", "Dataset for 311 snow complaints", "Dataset for Trail closures", "Dataset for Construction permits", "Dataset for Snow clearing", "Dataset for Traffic disruptions", "Dataset for Ice-risk weather watch"; link "Open in Google Maps, route 1"; link "Open in Apple Maps, route 1"; link "See all city conditions"; link "Data sources"; link "Privacy"; link "Demo".
- **Alerts (12 stops):** link "Open route Audit route"; button "Remove Audit route"; link "Data sources"; link "Privacy"; link "Demo". The device panel had no control in this run: the replay API has no push keys, so it is a sentence.
- **City (54 stops):** disclosure "How the ranking works"; button "Refresh list"; link "See source status"; searchbox "Search"; combobox "Effect"; combobox "Where"; combobox "Source"; combobox "Confidence"; button "Download all 58 as CSV"; link "Go to the map"; 25 buttons "Show on map" (`aria-pressed="false"`, each described by its row's heading "Rank N: title"); button "Show 25 more"; button "Show all 58"; the map canvas, region "Map"; button "Zoom in"; button "Zoom out"; disclosure "Toggle attribution"; link "OpenFreeMap"; link "© OpenMapTiles"; link "OpenStreetMap"; link "Data sources"; link "Privacy"; link "Demo". The "Road cameras" list had no camera in replay (no camera readings in the fixtures), so no stops there.
- **Settings (17 stops):** switch "Speak route changes" (on); switch "Vibrate on route changes" (on); radio group "Text size" on "Default"; radio group "Theme" on "Follow this device"; radio group "Motion" on "Follow this device"; switch "High-contrast map" (off); button "Reset to defaults"; link "Data sources"; link "Privacy"; link "Demo".
- **About (14 stops):** link "model card"; link "data sources page"; link "what it keeps about you"; link "Settings"; link "Data sources"; link "Privacy"; link "Demo".
- **Sources (35 stops):** 23 links in the tables, in reading order (City of Edmonton Open Data Terms of Use; LRT elevator and escalator outages; LRT device list; LRT stations and stops; On-street construction and maintenance; River valley trail cautions and closures; 311 snow on walk complaints; Traffic disruptions; Snow and ice clearing route status; Parcel addresses; Daily pedestrian counts; Environment and Climate Change Canada; Open Government Licence – Canada; Open-Meteo forecast; CC BY 4.0; Open-Meteo historical weather; CC BY 4.0; 511 Alberta cameras; "Free to reproduce for non-commercial or educational use…"; City of Edmonton traffic cameras; OpenStreetMap; Open Database License (ODbL); OpenFreeMap); link "the About page"; link "the Privacy page"; link "Data sources"; link "Privacy"; link "Demo".
- **Privacy (12 stops):** link "Alerts page"; link "OpenPath's GitHub repository"; link "Data sources"; link "Privacy"; link "Demo".
- **Demo (12 stops):** link "openpath-lyart.vercel.app"; link "Edmonton Convention Centre to the Alberta Legislature, for a wheelchair"; link "Data sources"; link "Privacy"; link "Demo".
- **404 (11 stops):** link "Plan a route"; link "Data sources"; link "Privacy"; link "Demo".

Every control on every page was reached by Tab; nothing interactive was skipped, and the order follows the reading order (header, main, map, footer). The map canvas is in the Tab order because MapLibre makes it focusable (arrow keys pan it); on the city page its name is MapLibre's default "Map", inside the container region named "Map of barriers and road cameras. The lists on this page hold everything it shows." (a note, not a failure; see the follow-ups).

#### Visible focus on each stop

Across the 22 walks and 479 stops, **0 stops lacked a visible ring and 0 were outside the viewport when focused.** On 469 stops the ring is on the focused element itself; on the 10 text-field stops (From, To and Search) it is on the field box around the input (`.control:has(input:focus-visible)` in `globals.css`). The computed ring on every stop was `outline: 3px solid` with `outline-offset: 2px` and a 2 px card-coloured `box-shadow` gap.

The ring's contrast, measured once per theme on the focused "Find routes" button at 1280 x 800 (Chromium 156.0.8078.4, November 8, 2026), from the computed tokens with the WCAG relative-luminance formula:

| Theme | Ring (`--focus`) | On the card (`--card`) | On the page (`--snow`) |
| --- | --- | --- | --- |
| Light | `#E06F00` | `#FFFFFF`, **3.26:1** | `#F5F8FA`, **3.06:1** |
| Dark | `#FFB454` | `#16293A`, **8.43:1** | `#0D1F2D`, **9.52:1** |

Both meet the 3:1 of WCAG 1.4.11 and 2.4.13. The ring never touches the control's own fill: the 2 px card-coloured gap under it means its neighbours are always the card or the page, so the ratio against an action-blue button (which would be 2.38:1 in light) does not apply.

#### No focus trap, and Escape

- All 22 Tab walks ended by **leaving the document** (focus went to the browser), never by cycling inside a widget or the map.
- **The Report a problem dialog** (planner with routes, 1280): Enter on the button opened one `<dialog open>` with focus on its heading "Report a problem"; 25 Tabs produced 23 stops, all inside the dialog and all with the ring (the native modal dialog keeps focus inside); Escape closed it (0 open dialogs) and focus returned to the button "Report a problem".
- **The place suggestions:** typing "churchill" in From showed 7 suggestions in the list "From suggestions"; Escape closed the list and left focus in the textbox "From".
- **The save form:** Enter on "Save this route, route 1" opened the form "Save route 1" with focus in the textbox "Name for this route", already filled with "Alberta Legislature", and the buttons "Save route" and "Cancel" (the replay server has no push keys, so no "Save and turn on alerts"); Escape closed the form and focus returned to "Save this route, route 1".
- **The remove confirmation** (alerts): Enter on "Remove Audit route" showed "Remove Audit route? Alerts for it stop." with focus on the button "Keep it" (and "Remove route Audit route" beside it); Escape closed it and focus returned to "Remove Audit route".

#### Enter and Space on the controls that open or toggle something

Playwright 1.64.1, Chromium 156.0.8078.4, 1280 x 800, light, November 8, 2026, 19:54 to 20:00 UTC. "Before" is commit `ed89666`.

| Page | Did | Observed |
| --- | --- | --- |
| planner | Tab to a suggestion, Enter | Tab moved from the From field to the first suggestion button ("Churchill Stop, LRT stop"); Enter chose it: "Selected: Churchill Stop, LRT stop." appeared and focus went back to the textbox "From". |
| planner | Enter on "Find routes" with no places | The field error "Choose a starting place from the suggestions first." appeared and focus moved to the textbox "From". |
| planner | ArrowDown in the profile group | Moved from "Wheelchair" to "Walker or cane", now checked, ring on the segment. |
| planner with routes | Enter, then Space, on "Show route 2" | Enter: "Hide route 2", `aria-expanded="true"`. Space: "Show route 2", `aria-expanded="false"`. Focus stayed on the button both times. |
| planner with routes | Open route 2, then Enter on "Show on the map, route 1" | Opening route 2 also draws it on the map, so route 2's details read "Shown on the map as the thick line." and route 1 offers the button. Enter on it moved focus to the map section (`<section>` "Map", inside main) and route 1 became the sentence. |
| planner with routes | Enter on "Report a problem", 25 Tabs, Escape | One dialog open, focus on its heading; 23 stops all inside, all ringed; closed by Escape, focus back on the button. |
| planner with routes | Enter on "Save this route, route 1", Escape | Form open with focus in "Name for this route" (value "Alberta Legislature"); Escape closed it and returned focus. |
| route report | ArrowDown on the route choice | **Before the fix:** "Conditions for route 1" became "Conditions for route 2", but focus fell to the document body, the status line that should say "Showing route 2: 45 min, 2.7 km. …" was empty, and `GET /api/route` was requested again. Cause: the report was keyed on the whole query string, and the `route=` rewrite (`replaceState`, which Next feeds into `useSearchParams`) remounted it. Fixed on this branch; see "Measured again after the fixes". |
| alerts | Enter on "Remove Audit route", Escape | Confirmation shown, focus on "Keep it"; Escape returned focus to "Remove Audit route". |
| city | Enter, then Space, on "How the ranking works" | Open after Enter, closed after Space. |
| city | Typing "trail" in Search | The count line became "46 of 58 barriers match. Showing the top 25." and "Clear filters" appeared. |
| city | ArrowDown on the closed "Effect" select | The value did not change and the count stayed "58 barriers. Showing the top 25.": on macOS an arrow key opens the native menu instead of changing the value. Choosing "Path closed" the way the menu does gave "43 of 58 barriers match. Showing the top 25." and "Clear filters". The select is the browser's own control. |
| city | Enter, then Space, on the first "Show on map" | Enter: `aria-pressed="true"`; Space: `aria-pressed="false"`; focus stayed on the button. The "See it on the map" link that follows is hidden at this width by design (`.mapHint` is `display: none` from 960 px, where the map is beside the list); it is in the DOM at 390 px. |
| city | Enter on "Show 25 more" | Focus moved to the first new row's heading, "Rank 26: Trail closed: Capilano Lower Granular Trail", with the ring on it. |
| settings | Space on "Speak route changes" | Checked went from true to false; the status line said "Speaking route changes is now off."; ring on the switch's label. |
| settings | ArrowDown on "Text size" | `<html data-text="large">`; the status line said "Text size is now large." |

#### The route change is announced

Playwright 1.64.1, Chromium 156.0.8078.4, 1280 x 800, light, November 8, 2026, 19:57 UTC. The planner with routes was open and its live line read "Live. Route changes arrive as they happen." A closure was seeded on the longest straight piece of route 1 with `POST /api/admin/override` (`X-Admin-Key`, title "Sidewalk closed for the accessibility audit", `ttl_s` 300, as `web/e2e/live-change.spec.ts` does), the API answered 201 with `rebuilt: true`, and the override was removed afterwards. The script wrapped `SpeechSynthesis.prototype.speak` and `Navigator.prototype.vibrate` to record the calls the page made, without changing them.

| Setting | What the `aria-live="assertive"` region (`aria-atomic="true"`) received | Then | `speechSynthesis.speak` | `navigator.vibrate` |
| --- | --- | --- | --- | --- |
| Speech on, vibration on (defaults) | "Route update: Test override: Sidewalk closed for the accessibility audit now blocks your route; the new route is 1 min longer." | The line "The best route now takes about 44 min over 2.6 km.", then "Routes checked again after the change.", and "Avoided: path closed" on each of the 3 routes | 1 call, with the same sentence, `lang` "en-CA"; `speechSynthesis.speaking` was true right after | 1 call with `[200, 100, 200]`, which Chromium accepted (returned true) |
| Speech off, vibration off (`openpath.speak` = off, `vibrate` false) | The same sentence | The same | 0 calls | 0 calls |

The live region is filled whether or not speech is on, so a screen reader user hears the change with speech off. Headless Chromium on this Mac has 180 synthesis voices, so `speaking` turned true, but no audio can be heard or recorded in a headless run, and there is no motor: whether the sentence was spoken aloud and whether the phone buzzed are **not observable in headless Chromium**. The device steps for that are in Part 2 (steps 26 and 27, and T4).

### 3. 200% zoom and 320 CSS px

Playwright 1.64.1, Chromium 156.0.8078.4, light theme, November 8, 2026, 19:58 UTC. "200% zoom at 1280 wide" is a 640 x 400 CSS px viewport at device scale 2, which is what the browser's 200% zoom gives a 1280 px window; "320 CSS px" is a 320 x 568 viewport. The script measured `scrollWidth` against `clientWidth` on the root, counted elements extending past the right edge, and counted text-bearing elements whose box hides overflowing text (`overflow` hidden, clip, auto or scroll with content wider or taller than the box). The root font size was 16 px (no text size setting).

| Page | 640 px (200% at 1280): overflow, past the edge, clipped | 320 px: overflow, past the edge, clipped |
| --- | --- | --- |
| planner, empty | 0 px, 0, 0 | 0 px, 0, 0 |
| planner with routes | 0 px, 0, 0 | 0 px, 0, 0 |
| route report | 0 px, 0, 0 | 0 px, 0, 0 |
| alerts | 0 px, 0, 0 | 0 px, 0, 0 |
| city | 0 px, 0, 0 | 0 px, 0, 0 |
| settings | 0 px, 0, 0 | 0 px, 0, 0 |
| about | 0 px, 0, 0 | 0 px, 0, 0 |
| sources | 0 px, 0, 0 | 0 px, 0, 0 |
| privacy | 0 px, 0, 0 | 0 px, 0, 0 |
| demo | 0 px, 0, 0 | 0 px, 0, 0 |
| 404 | 0 px, 0, 0 | 0 px, 0, 0 |

No horizontal page scroll and no clipped text on any page in either setting. The maps scroll inside their own frame, which the reflow criterion allows. (OP-410 measured the same for 320 px and for 200% text in `web/PERFORMANCE.md` on November 8 with Playwright 1.56.1.)

### 4. Reduced motion

Playwright 1.64.1, Chromium 156.0.8078.4, 1280 x 800, light, `reducedMotion: "reduce"` emulated, November 8, 2026, 19:58 UTC. For every element and its `::before` and `::after`, the script read the computed `animation-name`, `animation-duration`, `transition-property` and `transition-duration` and counted anything that animates or transitions for longer than 20 ms.

| Page | Elements still animating or transitioning | Route line `animation-name` | Map canvases mounted |
| --- | --- | --- | --- |
| planner, empty | 0 | (no line) | 1 |
| planner with routes | 0 | `none` (duration `1e-05s`) | 1 |
| route report | 0 | `none` | 0 (no map on this page) |
| alerts | 0 | (no line) | 0 |
| city | 0 | (no line) | 1 |
| settings, about, sources, privacy, demo, 404 | 0 each | (no line) | 0 |

**Control, without the preference:** the planner with routes had 8 moving elements: the three route lines' reveal (`draw-line`, 600 ms, the one animation the design allows) and five MapLibre markers' `opacity 0.2s` transition from the library's stylesheet. Under the preference both are zeroed by the rule in `globals.css`, which also sets `scroll-behavior: auto`.

**The maps' own motion** cannot be read from outside (MapLibre's camera easing leaves nothing in the DOM), so it is recorded from the code: both maps pass `fadeDuration: prefersReducedMotion() ? 0 : 300` (`planner-map.tsx` line 186, `city-map.tsx` line 61); the planner fits the route with `animate: !reduce, duration: animate ? 500 : 0` from `useReducedMotion()` (`planner-map.tsx` lines 248 to 256) and jumps with `jumpTo` when a condition is chosen (line 250); the city map fits with `duration: 0` and `jumpTo` always (`city-map.tsx` lines 127 and 148). Those read the OP-307 setting as well as the system preference.

**The OP-307 setting alone** ("Always reduce", `data-motion="reduce"`, no system preference): 0 CSS elements moving and the route line's animation `none`, as above. But "Show on the map" scrolled the page to the map with `scrollIntoView({ behavior: "smooth" })`: `planner-page.tsx` asked only the media query, not the setting. Under the system preference it was `"auto"`, and without either it was `"smooth"` as intended. Fixed on this branch; see "Measured again after the fixes".

### 5. Never colour alone

Playwright 1.64.1, Chromium 156.0.8078.4, 1280 x 800, light, November 8, 2026, 20:00 UTC. For every hazard mark the script read the SVG's outline element, its fill (a pattern, solid or none), the pattern tile, the colour token, `aria-hidden` and the words beside it.

| Where | Marks found | Shapes, fills and words |
| --- | --- | --- |
| City page, the legend "Map shapes" | 5 | Square (`rect`) with a diagonal-stripe pattern, `--blocked`, "Path closed"; diamond (4-point polygon) with a dot pattern, `--ice`, "Ice risk"; hexagon (6-point polygon) with a dash pattern, `--snow-hazard`, "Snow on the path"; triangle (3-point polygon), solid, `--caution`, "Take care"; the camera glyph (no hazard shape), "Road camera, with its reading". |
| City page, the ranked list (top 25) | 25 | All 25 are closures: striped square, `--blocked`, with the item's title as the label and the words "Path closed. High confidence. Checked …" in the row's meta line. |
| Route report, the conditions | 3 | Triangle solid `--caution` "Take care" (Escalator out of service); striped square `--blocked` "Path closed" (Trail closed: McKay School Park); triangle "Take care" (Road partly closed). |
| Planner, the three route lines | 14 | Start and end: filled circle, `--ink`, "Start" and "End" with the place name; each condition stop: its shape plus "On this route: take care", "14 m from this route: take care" or "Avoided: path closed"; the avoided stop also kinks the line. |
| Alerts, the saved route's status | 1 | Circle with a check, outline only, `--clear`, "Clear, about 43 min". |

Every shape is `aria-hidden="true"` with its words in the adjacent text, so a screen reader hears the words and a sighted person sees shape, fill and words. The four hazard shapes differ in outline (square, diamond, hexagon, triangle) and in fill (stripes, dots, dashes, solid), as `docs/design/DESIGN.md` section 6 specifies. The map markers (58 on the city page, 3 on the planner) are 44 x 44 CSS px discs with `aria-hidden="true"` and no `tabindex`: pointer shortcuts only, with the lists as the keyboard and screen reader path. The ice-risk and snow shapes did not occur in the replay data's items (its 58 priority items are closures and cautions); they were checked on the legend.

### 6. Touch targets on the phone viewport

Playwright 1.64.1, Chromium 156.0.8078.4, 390 x 844 (device scale 3, touch), light, November 8, 2026, 19:59 UTC, "before" commit `ed89666`. Every visible `a[href]`, `button`, `input`, `select`, `textarea`, `summary`, `[role=button]`, `[role=switch]` and `[tabindex]` was measured with `getBoundingClientRect()`; for a visually hidden radio, checkbox or switch the label that is its target was measured instead. The project target is 44 x 44 CSS px (WCAG 2.2 2.5.8 asks 24 x 24 with an exception for links inside text).

| Page | Interactive elements | Under 44 x 44 CSS px, with size |
| --- | --- | --- |
| planner, empty | 27 | MapLibre's attribution toggle (`summary`) 24 x 24; its three attribution links "OpenFreeMap" 76.9 x 14, "© OpenMapTiles" 91 x 14, "OpenStreetMap" 85.4 x 14; footer link "Demo" 41.5 x 44 |
| planner with routes | 34 | the same five |
| route report | 27 | 12 text links 20 px tall: "View source for …" (3, 84.5 x 20), "Dataset for …" (7, 53.7 x 20), "See all city conditions" 154.4 x 20; footer "Demo" 41.5 x 44 |
| alerts | 12 | footer "Demo" 41.5 x 44 |
| city | 40 | "See source status" 125.2 x 20; the attribution toggle 24 x 24 and its three links 14 px tall; footer "Demo" 41.5 x 44 |
| settings | 23 | footer "Demo" 41.5 x 44 |
| about | 14 | "model card" 79 x 20, "what it keeps about you" 166.1 x 20, "Settings" 58 x 20 (links in sentences); footer "Demo" 41.5 x 44 |
| sources | 35 | 22 links in the tables and text, 20 px tall (from 72.1 to 313.7 px wide); footer "Demo" 41.5 x 44 |
| privacy | 12 | "Alerts page" 80.1 x 20, "OpenPath's GitHub repository" 209.7 x 20 (links in sentences); footer "Demo" 41.5 x 44 |
| demo | 12 | footer "Demo" 41.5 x 44 |
| 404 | 11 | footer "Demo" 41.5 x 44 |

Everything else, 44 x 44 or more: every button, field, switch, radio and checkbox row, the map markers and zoom controls (44 x 44), the header links and the other footer links.

What was done with the exceptions:

- **Footer "Demo", 41.5 x 44:** fixed on this branch (`min-width: 44px` on the footer links; "Data sources" and "Privacy" were only wider because of their text).
- **MapLibre's attribution toggle, 24 x 24:** the library's stylesheet sets it; fixed on this branch with CSS in `globals.css`, as the zoom controls were in OP-410 (`#120`).
- **The attribution links, 14 px tall:** they are words inside the attribution sentence MapLibre draws ("OpenFreeMap © OpenMapTiles Data from OpenStreetMap"), the case WCAG 2.5.8 excepts. Not changed.
- **Text links inside sentences and table cells, 20 px tall** (about, privacy, sources, "See source status", "See all city conditions"): the same exception. Not changed; listed here so the choice is visible.
- **"View source for …" and "Dataset for …" on the route report, 20 px tall:** these stand on their own line under each item and each source, so they are closer to controls than to prose. Not changed here, because a 44 px line for each would change the report's rhythm, which is a design decision: recorded as a follow-up for the route report's owner (`web/src/components/report-conditions.tsx`, `.item a`; `web/src/components/source-freshness.tsx`, `.sources a`), for example `display: inline-flex; min-height: 44px; align-items: center` on those links.

### 7. Speech and vibration toggles

Playwright 1.64.1, Chromium 156.0.8078.4, 1280 x 800, light, November 8, 2026, 19:59 UTC. `docs/design/DESIGN.md` section 9 says the switches are native checkboxes with `role="switch"`, named by their label, described by their line of help, with the state shown by the knob and the word On or Off, and every change confirmed in the status line.

- **/settings:** three switches were found: "Speak route changes" (checked, word "On", described by "Reads each change aloud with this device's voice."), "Vibrate on route changes" (checked, "On", "Vibrates twice when a change arrives.") and "High-contrast map" (unchecked, "Off"). Clicking the first two turned both off: the words became "Off", the status line read "Speaking route changes is now off." and then "Vibrating on route changes is now off.", and storage held `openpath.speak` = "off" and `openpath.settings` = `{"vibrate":false,"textSize":"default","motion":"system","theme":"system","highContrastMap":false}`. Space on a switch toggled it with the ring on its label (section 2). "Reset to defaults" turned both on again with "Settings are back to their defaults."
- **The planner's "Speak route changes aloud" checkbox** read the same setting: unchecked after the switch was turned off on /settings, checked again after clicking it, and /settings then showed "Speak route changes" On.
- **Capability lines:** the sentences "This browser cannot speak…" and "This device cannot vibrate from a web page." did not appear, because this headless Chromium has `speechSynthesis` (with voices) and `navigator.vibrate`. Whether they appear on a device that lacks either is a Part 2 check (step 43).
- **What the toggles control** was shown in section 2: with both on, one `speak()` call with the sentence and one `vibrate([200, 100, 200])` call; with both off, none; the live region filled either way. Hearing the voice and feeling the buzz are not observable in headless Chromium.

### Findings, fixes and follow-ups

| # | Finding | Where | Rule | What was done |
| --- | --- | --- | --- | --- |
| 1 | Choosing another route on the route report dropped focus to the document body, emptied the "Showing route N…" status line and fetched the routes again. | `web/src/components/route-report-page.tsx` | WCAG 2.4.3 Focus Order, 4.1.3 Status Messages; the checklist's "choosing an alternate does not reset focus to the document body" | **Fixed** on this branch: the report is keyed by its trip, not the whole query, and its loading effect reads the trip when it runs. Commit "OP-408: Route report: keep focus and the announcement when choosing a route". |
| 2 | "Show on the map" scrolled smoothly when only the OP-307 "Always reduce" setting was on. | `web/src/components/planner-page.tsx` | WCAG 2.3.3 Animation from Interactions; the checklist's "reduced motion disables … smooth scrolling" | **Fixed**: `prefersReducedMotion()` from `lib/settings` instead of the media query alone. Commit "OP-408: Planner: respect the Always reduce motion setting when scrolling to the map". |
| 3 | The footer's "Demo" link was 41.5 px wide at 390 px. | `web/src/components/ui/site-footer.module.css` | The project's 44 x 44 target (WCAG 2.5.8 is met at 24) | **Fixed**: `min-width: 44px`. Commit "OP-408: Footer: 44 px wide Demo link". |
| 4 | MapLibre's attribution toggle was 24 x 24 on both maps. | `web/src/app/globals.css` (MapLibre's stylesheet sets it) | The project's 44 x 44 target | **Fixed**: 44 x 44 toggle with the text kept clear of it. Commit "OP-408: Maps: 44 x 44 attribution toggle". |
| 5 | "View source for …" and "Dataset for …" links on the route report are 20 px tall. | `report-conditions.tsx` (`.item a`), `source-freshness.tsx` (`.sources a`) | The project's 44 x 44 target | **Follow-up** for the route report's owner: a 44 px line for each is a design change (section 6). |
| 6 | Links inside sentences and table cells are 20 px tall; MapLibre's attribution links are 14 px tall. | about, privacy, sources, city, the maps | WCAG 2.5.8 excepts links in text | **Recorded**, not changed. |
| 7 | The city map's canvas is named "Map" (MapLibre's default) inside a region already named "Map of barriers and road cameras. The lists on this page hold everything it shows.", so a screen reader meets two map regions. The planner names its canvas itself. | `web/src/components/city/city-map.tsx` | WCAG 4.1.2 Name, Role, Value (quality, not a failure) | **Follow-up** for the city page's owner: give the canvas the sentence, as `planner-map.tsx` line 197 does, and drop the container's role, or the other way round. |
| 8 | axe asked for a human check of `color-contrast` on 6 to 7 route-line nodes. | planner with routes | WCAG 1.4.3 | **Checked by hand** (section 1): 16.8:1, 7.0:1, 13.7:1 and 7.3:1 from the tokens. |
| 9 | ArrowDown on a closed `<select>` did not change its value in headless Chromium on macOS. | city filters | WCAG 2.1.1 | **Not a defect**: on macOS arrow keys open the native menu; choosing from the menu filtered the list (section 2). The selects are the browser's own. |

### Measured again after the fixes

The whole audit ran a second time on November 8, 2026 from 20:03 UTC, with the same tools (Playwright 1.64.1, Chromium 156.0.8078.4, axe-core 4.14.0), the same servers and the same pages, against a fresh production build of the fixed sources (the working tree that became the commits "Route report: keep focus and the announcement when choosing a route", "Planner: respect the Always reduce motion setting when scrolling to the map", "Footer: 44 px wide Demo link" and "Maps: 44 x 44 attribution toggle"; the script recorded `ed89666` as the commit because those were committed after the run began).

| Check | Before (`ed89666`) | After the fixes |
| --- | --- | --- |
| Route report, ArrowDown on the route choice (1280, light) | Focus on the document body; status line empty; 1 new `GET /api/route` | Focus on the radio "Route 2: 45 min, 2.7 km. Avoids Trail closed: McKay School Park"; status line "Showing route 2: 45 min, 2.7 km. 0 conditions on this route, 1 within 50 m, 1 avoided."; 0 new requests |
| Planner, "Show on the map" with the "Always reduce" setting only (1280, light) | `scrollIntoView` behavior `"smooth"` | `"auto"` (instant); still `"smooth"` with no preference and no setting, still `"auto"` under the system preference |
| Footer "Demo" link (390 x 844) | 41.5 x 44 CSS px | 44 x 44 CSS px on every page |
| MapLibre attribution toggle (390 x 844) | 24 x 24 CSS px on both maps | 44 x 44 CSS px on both maps |
| axe-core, 44 page-contexts | 0 violations | 0 violations (planner with routes: the same `color-contrast` review item, 6 and 7 nodes) |
| Keyboard, 22 walks | 479 stops, 0 without a ring, all left the document | 479 stops, 0 without a ring, all left the document |
| 200% zoom and 320 px, 22 page-settings | 0 px overflow, 0 clipped | 0 px overflow, 0 clipped |
| Reduced motion, 11 pages | 0 moving | 0 moving |
| Touch targets under 44 px that are the app's own | Demo link, attribution toggle | none; the 14 px attribution links and the 20 px text links listed in section 6 remain as recorded |

### Limits of this audit

- **No screen reader was run.** The announcements in section 2 are what the DOM exposes (roles, names, states and live regions), read with Playwright; how VoiceOver and TalkBack speak them is Part 2, not yet run.
- **One engine.** Everything was measured in Chromium 156.0.8078.4. Safari (iOS) and Firefox were not measured; Part 2 covers Safari on a device.
- **Headless.** Speech output, vibration, the map's camera easing and real browser zoom were not observable; the document says so where it applies and records the calls the page made instead.
- **Replay data.** The conditions are the recorded fixtures at 2026-11-10T18:00:00Z. Live data changes the numbers in the trip, not the structure that was checked.
- **Not measured here:** text spacing (WCAG 1.4.12), Windows forced colours, pointer cancellation and dragging alternatives, the offline and error states (OP-410 measured those in `web/PERFORMANCE.md`), and the contrast of the map tiles themselves (`docs/design/DESIGN.md` sections 9 and 11 record the high-contrast style's pairs).

### Re-running the audit

Start the API and the web app as `scripts/e2e.py` does (replay mode, the production build, `ADMIN_KEY` set), then in `web/`:

```bash
E2E_WEB_URL=http://localhost:3811 E2E_API_URL=http://localhost:8811 E2E_ADMIN_KEY=test npm run a11y
```

Results land in `web/test-results/a11y/results.json` with one screenshot per page and context in `screens/`, and a summary line per measurement on stdout. `A11Y_ONLY=axe,keyboard,interactions,focus,live,zoom,motion,colour,targets,settings` runs a subset and merges it into the existing `results.json`; `A11Y_PAGES=planner-trip,city` limits axe to named pages; `A11Y_BLOCK_TILES=1` drops the OpenFreeMap tile requests, as the end-to-end tests do. The script prints the tool versions it ran with; put them beside any number copied from it.

## Part 2. Screen reader walkthroughs: written, not yet run

**Status: these two walkthroughs have not been run.** Nothing in this part is a result. They are a script for Estella to run on real devices; every Result cell is empty until she fills it in. Record the device, OS version, browser version, the date and whether the Replay badge was showing in the table below before anything else.

### Setup (both devices)

1. Open the app at the address under test (production, or the staging deploy of the pull request). Note whether the header shows the Replay badge. In replay the route numbers below hold; live, the day's data decides them and only the structure is checked.
2. Go to Settings and switch **Speak route changes** off, so the screen reader is the only voice during the run. Step 27 turns it back on to test the app's own speech on its own.
3. Use the judge page's trip: **Edmonton Convention Centre** to **Alberta Legislature**, for a **wheelchair**. In replay it gives three routes; route 1 is 43 min over 2.6 km with "Escalator out of service" near it, "Trail closed: McKay School Park" avoided and "Road partly closed" on it.
4. Have a teammate ready for step 26, who can call `POST /api/admin/override` against the same API (the exact call is in `web/e2e/live-change.spec.ts`), or skip that step and say so.
5. Expected announcements below are written as name, role and state. VoiceOver and TalkBack phrase these differently ("button, collapsed" against "collapsed, button"; "switch button, on" against "on, switch"). A step passes when the name, the role and the state are all spoken, in any order, and nothing is spoken that the screen does not show.

| | VoiceOver (iOS Safari) | TalkBack (Android Chrome) |
| --- | --- | --- |
| Device, OS, browser version | | |
| Date and time | | |
| Replay badge showing | | |
| Tester | | |

### Gesture legend

| Action | VoiceOver | TalkBack |
| --- | --- | --- |
| Next item | Swipe right | Swipe right |
| Previous item | Swipe left | Swipe left |
| Activate | Double-tap | Double-tap |
| Next heading | Rotor to Headings, then swipe down | Swipe up then down to reach Headings, then swipe down |
| Read from the top | Two-finger swipe up | Reading controls, then Read from top |
| Scroll | Three-finger swipe | Two-finger swipe |
| Go back or close | Two-finger scrub (Z shape) | Back gesture or Back button |
| Type in a field | Double-tap the field, then type | Double-tap the field, then type |

### Script

Fill in Pass, Fail or Partly in each result column, and write what was actually spoken when it differs.

| Step | Do | Expected announcement | VoiceOver | TalkBack | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | Open `/`. | Page title "Plan a route, OpenPath". First item: "Skip to main content, link". | | | |
| 2 | Double-tap the skip link, then swipe right. | Focus moves into the main area: next item is "Where are you going?, heading level 1" or the "From" field, not a header link. | | | |
| 3 | Swipe right from the top of the page. | "OpenPath, link"; in replay "Replay: recorded data, not current conditions"; "Main, navigation"; "Plan, current page, link"; "City, link"; "Alerts, link"; "Settings, link"; "About, link". | | | |
| 4 | Continue to the first field. | "From, search text field" and, after a pause, the hint "Type an address, an LRT station or a place name, then choose a suggestion." | | | |
| 5 | Double-tap From and type "convention". | A status line is spoken without moving focus: "1 suggestion" or "N suggestions". | | | |
| 6 | Swipe right into the suggestions. | "From suggestions, list"; "Edmonton Convention Centre, 9797 Jasper Avenue NW, button". | | | |
| 7 | Double-tap the suggestion. | The list closes; focus is back on the From field; the note "Selected: Edmonton Convention Centre, 9797 Jasper Avenue NW." is read with the field. | | | |
| 8 | Swipe right. | "Use my current location, button", then a status line that is empty until it is used. | | | |
| 9 | Fill To the same way with "legislature". | "To, search text field" with the same hint; "Alberta Legislature, 10800 97 Avenue NW, button"; "Selected: Alberta Legislature, 10800 97 Avenue NW." | | | |
| 10 | Swipe right into the profile group. | "How do you get around?" then "Wheelchair, radio button, selected, 1 of 4"; "Walker or cane, 2 of 4"; "Low vision, 3 of 4"; "Standard walking, 4 of 4". Changing the choice is spoken as selected. | | | |
| 11 | Swipe right. | "Include LRT trips, checkbox, not checked" with the hint "Walk, ride the LRT and walk on, when that is faster." | | | |
| 12 | Swipe right and double-tap. | "Find routes, button". Then, without moving focus: "Finding routes for a wheelchair from Edmonton Convention Centre to Alberta Legislature." followed by "3 routes for a wheelchair, best first." (in replay also "Replayed data, not current conditions."). | | | |
| 13 | Swipe right. | "Live. Route changes arrive as they happen." then "Speak route changes aloud, checkbox, not checked" (off from the setup). | | | |
| 14 | Swipe right. | "Notes about this trip, list": "The start point is on paths cut off from the rest of the network for this profile; the route begins 20 m away at the nearest connected path." (replay). | | | |
| 15 | Swipe right. | "Conditions across the whole city, list" with one item per city-wide condition, for example "Road cameras are not reporting right now; ice risk comes from the weather watch alone." | | | |
| 16 | Swipe right into route 1. | "43 min, 2.6 km, step-free, heading level 2"; "Recommended route." and its reason; "1 condition on this route, 1 within 50 m, 1 avoided."; "Shown on the map as the thick line." | | | |
| 17 | Swipe right through the line. | "Route 1: start, conditions and end, list, 5 items": "Edmonton Convention Centre, Start"; "Escalator out of service, 14 m from this route: take care"; "Trail closed: McKay School Park, Avoided: path closed"; "Road partly closed, On this route: take care"; "Alberta Legislature, End". Each condition also says when it was checked. | | | |
| 18 | Swipe right through the actions. | "View route report for route 1, link"; "Save this route, route 1, button"; "Open in Google Maps, route 1, link"; "Open in Apple Maps, route 1, link"; the Apple Maps note. | | | |
| 19 | Swipe right into route 2 and double-tap its button. | "45 min, 2.7 km, step-free, heading level 2"; "Route 2 of 3."; "Show route 2, button, collapsed". After the double-tap: "Hide route 2, button, expanded", focus still on the button, and the line of route 2 follows. Route 2 is now drawn on the map, so its details say "Shown on the map as the thick line." and route 1 offers "Show on the map, route 1, button". | | | |
| 20 | Swipe to "Show on the map, route 1, button" and double-tap. | Focus moves to the map section ("Map, heading level 2" then the map); the button it left is now the sentence "Shown on the map as the thick line." | | | |
| 21 | Swipe right through the map. | "Map of the routes. The route list has the same information." then "Zoom in, button", "Zoom out, button", the attribution. No marker is spoken: markers are hidden from assistive technology. | | | |
| 22 | Swipe right. | "See snow, ice or a blocked path?, heading level 2"; the sentence; "Report a problem, button" (with "pop-up" or "opens a dialog" where the screen reader says it). | | | |
| 23 | Double-tap Report a problem. | A dialog opens and focus lands on "Report a problem, heading level 2"; next items: "Close, button"; "What is the problem?" with three radios "Snow on the path", "Ice on the path", "Path blocked". Swiping does not leave the dialog. | | | |
| 24 | Close it with the back gesture (scrub, or Back). | The dialog closes and focus returns to "Report a problem, button". | | | |
| 25 | Swipe right to the end of the page. | "Conditions come from public data and can change. Check again before you leave."; "Map data © OpenStreetMap contributors, map style OpenFreeMap."; "About OpenPath, navigation": "Data sources, link"; "Privacy, link"; "Demo, link". | | | |
| 26 | Stay on the page while a teammate seeds the override (setup step 4). | The screen reader interrupts with "Route update: Test override: <title> now blocks your route; the new route is N min longer." then "Conditions changed. Checking the routes again." and "Routes checked again after the change." Focus does not move. Write "skipped" if nobody seeded it. | | | |
| 27 | Go to Settings, switch Speak route changes on, return to the planner and repeat step 26. | The same sentence is spoken twice: once by the screen reader (the live region) and once by the device's own voice (speechSynthesis). On a phone that can vibrate, two short pulses. Write down what happened to each. | | | |
| 28 | Open "View route report for route 1". | Page title "Route report, OpenPath"; "Back to the planner, link"; "Edmonton Convention Centre to Alberta Legislature, heading level 1"; "Route report for wheelchair."; in replay "Replayed data. These conditions were recorded earlier and are not current." | | | |
| 29 | Next heading, repeatedly. | "Safety summary, heading level 2" with "1 condition on this route, 1 within 50 m, 1 avoided."; "Choose a route, heading level 2"; the route 1 heading; "Steps for route 1, heading level 2"; "Conditions for route 1, heading level 2"; "Sources checked, heading level 3"; "Open in a maps app, heading level 2". | | | |
| 30 | In "Choose a route", select route 2. | "3 routes for this trip" then radios; choosing route 2 is followed by a status line "Showing route 2: 45 min, 2.7 km. 0 conditions on this route, 1 within 50 m, 1 avoided." without moving focus, and focus stays on the chosen radio. | | | |
| 31 | Swipe through "Conditions for route 1". | "Indoor (1), heading level 3": "Take care", "Escalator out of service", "Near this route, 14 m from the route.", its detail, confidence, when checked, source and "View source for Escalator out of service, link". Then "Sidewalks and paths (1)" with "Path closed"; "Roads and crossings (1)" with "Take care"; "Weather and ice (0)" with "Nothing known in this group." | | | |
| 32 | Open Alerts from the main navigation. | Page title "Alerts, OpenPath"; "Alerts, heading level 1"; "Your saved routes, checked again whenever conditions change."; "Saved routes, heading level 2"; the count "No saved routes yet. Plan a trip and choose Save this route." or "1 saved route." | | | |
| 33 | If there is no saved route, go back and save route 1 (Save this route, route 1; the name field is focused and filled with "Alberta Legislature"; Save without alerts or Save route). Return to Alerts. | "Alberta Legislature, heading level 3"; its status mark in words, for example "Clear, about 43 min"; "Wheelchair. Checked just now."; "Open route Alberta Legislature, link"; "Remove Alberta Legislature, button". | | | |
| 34 | Double-tap Remove. | "Remove Alberta Legislature? Alerts for it stop." Focus lands on "Keep it, button"; "Remove route Alberta Legislature, button" is next. The back gesture or Keep it closes the question and focus returns to "Remove Alberta Legislature, button". | | | |
| 35 | Swipe right into "Alerts on this device". | "Alerts on this device, heading level 2" then one sentence in words: in iOS Safari "Add to Home Screen to get alerts." with the steps; in Android Chrome "Alerts are off on this device." and "Turn on alerts, button", or "Alerts are not set up on this server yet." | | | |
| 36 | Open City. | Page title "Barriers to clear first, OpenPath"; "Barriers to clear first, heading level 1"; the lead; "How the ranking works" as a collapsed disclosure; "Updated N min ago"; "Refresh list, button". | | | |
| 37 | Swipe right into the filters. | "Filter barriers, search" landmark; "Search, search field"; "Effect, pop-up button" (or "drop-down list"); "Where"; "Source"; "Confidence". Changing Effect is followed by the count line, for example "43 of 58 barriers match." | | | |
| 38 | Swipe right into the list. | The count, for example "58 barriers. Showing the top 10."; "Download all 58 as CSV, button"; "Go to the map, link"; "Barriers, most important first, heading level 2"; then rows: "Rank 1: <title>, heading level 3", why it ranks, "Path closed. High confidence. Checked N min ago.", "Source: …", "Show on map, toggle button, not pressed". | | | |
| 39 | Double-tap Show on map on row 1. | The same button is now "pressed"; a "See it on the map, link" appears after it (phone layout); focus stays on the button. | | | |
| 40 | Swipe to "Show 10 more, button" and double-tap. | Focus moves to "Rank 11: <title>, heading level 3". | | | |
| 41 | Swipe right into the map and its legend. | "Map of barriers and road cameras. The lists on this page hold everything it shows."; the map ("Map"); zoom buttons; "Map shapes, list": "Path closed", "Ice risk", "Snow on the path", "Take care", "Road camera, with its reading". No marker is spoken. | | | |
| 42 | Continue to "Road cameras, heading level 2" and "Source status, heading level 2". | Each camera: its name, "View 1: <reading in words>", when checked, "Show on map, toggle button", "Open 511 Alberta's camera list to find <name>, link" (in replay there may be no camera, and a sentence says so). Each source: its name, "Up to date" or "Out of date" or "Not read yet", and when it was last read. | | | |
| 43 | Open Settings. | "Settings, heading level 1"; "Saved on this device only. Each change applies at once, on every page."; "Route changes, heading level 2"; "Speak route changes, switch, off" with the hint "Reads each change aloud with this device's voice."; "Vibrate on route changes, switch, on" with its hint. On a device that cannot speak or vibrate from a web page, the extra sentence saying so is read with the switch. | | | |
| 44 | Double-tap Speak route changes. | "On" is spoken for the switch and the status line says "Speaking route changes is now on." without moving focus. | | | |
| 45 | Swipe right through Reading. | "Reading, heading level 2"; "Text size" with "Default, The text size your browser is set to., radio button, selected, 1 of 3", "Large, 125% of your browser's size.", "Larger, 150% of your browser's size."; "Theme" with "Follow this device", "Light", "Dark"; "Motion" with "Follow this device", "Always reduce", "Never reduce". | | | |
| 46 | Choose Larger. | "Text size is now larger." is spoken; the page grows; nothing is cut off or needs sideways scrolling. | | | |
| 47 | Swipe right to Map and Reset. | "Map, heading level 2"; "High-contrast map, switch, off"; "Reset to defaults, button" and its note. Double-tap Reset: "Settings are back to their defaults." | | | |
| 48 | Open About, Data sources and Privacy by heading. | Each page: its title, one heading level 1 ("About OpenPath", "Data sources", "Privacy"), then heading level 2 sections in order. On Data sources each table reads its column names with each cell. | | | |
| 49 | Open Demo. | "OpenPath, heading level 1"; the pitch; "QR code for <address>, image"; the address as a link; "Try this trip, heading level 2" with the trip link; "Then try, heading level 2" with three items. | | | |
| 50 | Open a wrong address, for example `/no-such-page`. | Page title "Page not found, OpenPath"; "This page does not exist., heading level 1"; "Check the address, or start from the planner."; "Plan a route, link". | | | |

### Device-specific steps

**VoiceOver (iOS Safari)**

| Step | Do | Expected | Result | Notes |
| --- | --- | --- | --- | --- |
| V1 | Settings, Accessibility, Motion, Reduce Motion on; reload the planner with the trip. | The route line appears at once (no drawing animation) and choosing a route makes the map jump, not glide; "Show on the map" jumps the page to the map instead of gliding. | | |
| V2 | Safari page zoom 200% (AA in the address bar, or Settings, Safari, Page Zoom) on the planner with routes and on the city page. | No sideways scrolling and no text cut off; the map may scroll inside its own frame. | | |
| V3 | Share, Add to Home Screen, then open OpenPath from the home screen and go to Alerts. | "Alerts are off on this device." and "Turn on alerts, button" replace the Add to Home Screen sentence. Double-tap: iOS asks to allow notifications; afterwards "Alerts are on for this device." takes focus. | | |
| V4 | With alerts on, double-tap "Send me a test alert, button". | "Test alert sent. It should appear on this device in a few seconds." and a notification arrives. | | |

**TalkBack (Android Chrome)**

| Step | Do | Expected | Result | Notes |
| --- | --- | --- | --- | --- |
| T1 | Settings, Accessibility, Remove animations on; reload the planner with the trip. | The route line appears at once and choosing a route makes the map jump, not glide; "Show on the map" jumps the page to the map. | | |
| T2 | Chrome, Settings, Accessibility, Text scaling 200%, on the planner with routes and on the city page. | No sideways scrolling and no text cut off. | | |
| T3 | On Alerts, double-tap "Turn on alerts, button". | Chrome asks to allow notifications; afterwards "Alerts are on for this device." takes focus. | | |
| T4 | Double-tap "Send me a test alert, button". | "Test alert sent. It should appear on this device in a few seconds." and a notification arrives; the phone vibrates if that is on. | | |
| T5 | On the planner with routes, swipe to the map and use the Back gesture while it has focus. | Focus leaves the map to the page; TalkBack is not trapped on the canvas. | | |

### What to send back

The two filled tables, the device lines at the top, and for every Fail the exact words spoken and a screenshot. They go into this file under a "Run on <date>" heading, next to the automated results, so both are read side by side.

## Part 3. Checklist for every UI pull request (OP-212)

Purpose: keep OpenPath usable with keyboard, screen reader, touch and magnification. This OP-212 review gate follows WCAG 2.2 AA and the project's additional requirements. It does not claim the app already conforms; Part 1 records what was measured.

The [design review](../docs/ESTELLA_OP212.md) records the wireframes, measured token contrast, findings and verification limits. Device and screen reader testing is Part 2 of this file.

### Evidence required in each UI PR

- [ ] List affected screens and test the changed flow with keyboard only. Record browser/device, commit, viewport and theme.
- [ ] Attach relevant desktop/mobile, light/dark and focus-state evidence. Describe screen-reader announcements in words; screenshots cannot demonstrate them.
- [ ] Run the available automated accessibility scan and report its tool/version, scope and result. Never claim an axe/Lighthouse result without running it. (`cd web && npm run a11y` runs the OP-408 audit against a local replay API and production build; `make e2e` runs axe on the critical path.)
- [ ] Complete applicable checks below. Explain N/A items; link an issue and mitigation for unresolved failures. An unchecked safety-critical flow is not approval.

### Keyboard and focus

- [ ] Every action works with Tab, Shift+Tab, Enter/Space and appropriate arrow keys. Use native controls and provide equivalents for map-only interactions (2.1.1).
- [ ] Reading/tab order follows the task. No keyboard trap; users can leave maps, menus and dialogs. Provide a way to bypass repeated navigation (2.1.2, 2.4.1, 2.4.3).
- [ ] Focus is visible in both themes, including over maps, and not wholly hidden by overlays/sticky panels. Custom indicators meet the project's 3:1 contrast target against adjacent backgrounds (1.4.11, 2.4.7, 2.4.11).
- [ ] Modal opening places focus appropriately; Escape closes and returns focus to its trigger. Selecting a marker, refreshing or choosing an alternate does not remove the focused element or reset focus to the document body.
- [ ] Address results have a labelled input and keyboard-operable list. Associate errors with fields and preserve entered values; announce result status (3.3.1, 3.3.2, 4.1.2, 4.1.3).

### Contrast, colour and touch

- [ ] Measure actual text/background pairs in both themes: include patterned marker labels, selected/hover/disabled states and overlays. Normal text needs 4.5:1; large text needs 3:1. Large means 24 CSS px regular or approximately 18.67 CSS px bold (1.4.3).
- [ ] Meaningful custom controls/state graphics have 3:1 contrast against adjacent colours (1.4.11). Decorative separators do not automatically need this ratio; identify which graphics convey information.
- [ ] Kind and selected-route identity use text and another cue such as icon, pattern or shape; never colour alone (1.4.1).
- [ ] Primary controls, map controls, marker targets and icon buttons meet the **project's 44 × 44 CSS px target**, including width and height. A minimum-height rule alone is insufficient. WCAG 2.2 AA 2.5.8 specifies 24 × 24 with exceptions; our target is stricter.

### Text alternatives and announcements

- [ ] Maps have an equivalent keyboard-accessible text flow: endpoints/profile, route choices with time/distance, numbered steps and full obstruction report. It works without tiles/WebGL; a legend alone is insufficient (1.1.1, 1.3.1).
- [ ] Visible labels agree with accessible names. Icon buttons have action names; decorative icons are hidden. Selected/expanded/toggle state is programmatic (2.5.3, 4.1.2).
- [ ] Loading, success, result counts and empty states have suitable status semantics without moving focus. Errors, stale data, freshness and Replay mode are explained in text (4.1.3).
- [ ] Safety-critical route changes use the project's concise assertive announcement. Routine updates stay polite without repeated interruption. Respect speech/vibration preferences; settings have named on/off states.

### Zoom, reflow and motion

- [ ] At 200% text zoom, no essential text, control or safety information is clipped. Test long names/details, not just short mock strings (1.4.4).
- [ ] At 320 CSS px content width, ordinary content reflows without two-dimensional page scrolling. Check mobile and browser zoom. Maps may require two-dimensional layout; the exception does not cover adjacent forms, navigation or reports (1.4.10).
- [ ] Text spacing leaves all functionality available: line-height 1.5, paragraph spacing 2em, letter spacing 0.12em and word spacing 0.16em (1.4.12).
- [ ] Reduced motion disables nonessential CSS and map-library animation, including fly-to, smooth scrolling and pulsing loaders. A stylesheet rule alone does not verify library behavior. Script motion reads the OP-307 setting (`prefersReducedMotion()`, `useReducedMotion()`), not the media query alone.
- [ ] Automatically moving/updating content has required pause/stop/hide controls where applicable; no dangerous flashing (2.2.2, 2.3.1). Reduced motion does not replace all users' pause controls. Do not add time limits to route choices or warnings.

### Screen-specific walkthrough

| Screen | Required order and equivalent access |
| --- | --- |
| Planner | Origin → destination → profile → find route → result status → choices/report. Denied geolocation has a text-entry fallback. Search/map choices also work without the map. |
| Route report | Endpoints/profile → safety summary/freshness → route choice/time → numbered steps → grouped conditions/sources → save/share/Maps. Alternatives have text comparisons. |
| Alerts | New safety-relevant changes before secondary settings. Notification denied/offline states remain operable. Confirm/undo removal and return focus. |
| City view | Heading/freshness → search/filter → count → equivalent list/map. Filters announce counts; list includes complete condition information. |

Verify against the final responsive DOM. OP-114's alert diagrams and prose differ on priority; use safety-first ordering during implementation.

### Sources

- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) and [Understanding WCAG 2.2](https://www.w3.org/WAI/WCAG22/Understanding/).
- [Contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html), [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).
- [Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html), [focus not obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html), [status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html), [pause/stop/hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html).
