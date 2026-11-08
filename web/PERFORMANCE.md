# Web performance and accessibility measurements (OP-410)

Measured numbers only. Every figure below was produced by the command named beside it, on the build and commit named beside it. Nothing here is a target or an estimate unless it says so.

## How it was measured

- **Date:** November 8, 2026.
- **Machine:** a MacBook with an Apple M4 and 16 GB, macOS 26.3, on mains power, nothing else running except the two servers below. Lighthouse ran alone; the Playwright scans ran after it, never at the same time.
- **Servers:** the API from this repository in replay mode (`DATA_MODE=replay REPLAY_SNAPSHOT=cloud/tests/fixtures DATABASE_URL=sqlite:///…/op410-api.db ADMIN_KEY=test WORKER_KEY=test CORS_ORIGINS=http://localhost:3363 uvicorn cloud.app.main:app --port 8763`) and the web app's production build (`npm run build`, then `npx next start -p 3363`, with `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_API_URL` set to `http://localhost:8763` and `NEXT_PUBLIC_WS_URL` to `ws://localhost:8763/ws`). The route pages used the trip from Central Station (53.5447, -113.4939) to the Royal Alexandra Hospital (53.5588, -113.4972) for a wheelchair, which routes in the replay fixtures.
- **Lighthouse:** 12.8.2 (Node 24.5.0), Chromium 141 (the Playwright build) in new headless mode without a GPU, `--form-factor=mobile --screenEmulation.mobile --only-categories=performance,accessibility,best-practices`. That is Lighthouse's default mobile run: a Moto G Power screen, simulated slow 4G and a 4x CPU slowdown. Three runs per page; the table shows the median with all three runs in brackets, and the metrics of the median run. Lighthouse does not scroll, so the maps, which mount when they come near the viewport, are part of the measurement on a laptop layout and not on a phone.
- **Console, axe, reflow and keyboard:** Playwright 1.56.1 with Chromium 141, axe-core 4.11.4 with the tags `wcag2a wcag2aa wcag21a wcag21aa wcag22aa`, on every page in light and dark at 390 and 1280 px wide, after scrolling the whole page so the maps mount. "App errors" are `console.error` calls, uncaught exceptions and failed page loads from OpenPath's own code; the browser's own "Failed to load resource" lines for API requests are counted apart as network failures, because with the API down they are expected and the page's job is to say so.
- **Before** is the Phase 4 base, commit `3967064` (phase_3 with the five open pull requests merged). **After** is this branch, `op-410-web-polish`, at the commit named in the pull request.
- Headless Chromium without a GPU has no WebGL2, so in the Lighthouse runs both maps show their sentence ("The map cannot be drawn in this browser…") instead of tiles. Before this branch the city page did not catch that error and fell into Next.js's bare error document, which is why its accessibility score was 79: that document has no `lang` and no viewport tag.

The scripts live outside the repository (a scratch folder on the measuring machine): `lh.mjs` (Lighthouse), `scan.mjs` (console, axe, screenshots), `checks.mjs` (#119, #120, reflow, 200% text) and `offline.mjs` (the offline line, the service worker's offline page, the error page).

## Lighthouse, mobile

| Page | Performance before | Performance after | Accessibility before | Accessibility after | Best practices before | Best practices after |
| --- | --- | --- | --- | --- | --- | --- |
| `/` (planner, empty) | 100 (99, 100, 100) | 100 (100, 100, 99) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) |
| `/?from=…&to=…` (planner with a trip) | 98 (98, 98, 98) | 97 (97, 97, 97) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) |
| `/route?…` (route report) | 87 (85, 89, 87) | 97 (99, 97, 97) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) |
| `/city` | 80 (80, 80, 80) | 99 (99, 99, 99) | 79 (79, 79, 79) | 100 (100, 100, 100) | 89 (89, 89, 89) | 100 (100, 100, 100) |
| `/alerts` | 99 (99, 99, 99) | 99 (99, 99, 99) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) |
| `/settings` | 100 (100, 99, 100) | 99 (99, 99, 99) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) |
| `/about` | 100 (100, 100, 100) | 99 (99, 100, 99) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) |
| `/sources` | 99 (99, 99, 99) | 99 (99, 99, 99) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) |
| `/privacy` | 100 (100, 100, 100) | 99 (99, 99, 99) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) | 100 (100, 100, 100) |

| Page | Metrics before (median) | Metrics after (median) |
| --- | --- | --- |
| `/` (planner, empty) | LCP 1.8 s, CLS 0.000, TBT 49 ms | LCP 1.8 s, CLS 0.000, TBT 45 ms |
| `/?from=…&to=…` (planner with a trip) | LCP 1.4 s, CLS 0.085, TBT 63 ms | LCP 1.8 s, CLS 0.085, TBT 80 ms |
| `/route?…` (route report) | LCP 2.1 s, CLS 0.241, TBT 7 ms | LCP 2.5 s, CLS 0.000, TBT 2 ms |
| `/city` | LCP 1.8 s, CLS 0.431, TBT 74 ms | LCP 2.0 s, CLS 0.051, TBT 1 ms |
| `/alerts` | LCP 1.4 s, CLS 0.062, TBT 4 ms | LCP 2.0 s, CLS 0.062, TBT 0 ms |
| `/settings` | LCP 1.7 s, CLS 0.000, TBT 11 ms | LCP 2.0 s, CLS 0.000, TBT 0 ms |
| `/about` | LCP 1.9 s, CLS 0.000, TBT 0 ms | LCP 2.0 s, CLS 0.000, TBT 0 ms |
| `/sources` | LCP 1.8 s, CLS 0.055, TBT 1 ms | LCP 2.0 s, CLS 0.055, TBT 0 ms |
| `/privacy` | LCP 1.7 s, CLS 0.000, TBT 44 ms | LCP 2.0 s, CLS 0.000, TBT 0 ms |

Scores are the median of three runs, with the three runs in brackets. Performance and accessibility are at least 90 on every page after the change (the story's target); before it, the route report (87) and the city page (80 performance, 79 accessibility, 89 best practices) were below it.

Largest contentful paint moved by 0 to 0.6 s on pages whose first screen did not change (for example `/alerts` 1.4 s to 2.0 s). Lighthouse's simulated slow 4G quantises load times into round trips, so a few hundred bytes more CSS can cost one; the performance scores on those pages stayed at 99 or 100, and this document records what was measured rather than an explanation it cannot prove.

The planner with a trip in the URL keeps a CLS of 0.085 (score 97): when the trip is read from the URL after hydration, the "Selected: Central Station." lines appear under the two fields and push the form down. Reserving that space would add an empty line under every field for everyone, so it stays as it is and is noted here.

## What changed and what moved the numbers

- **Layout shift.** The route report and the city page rendered a one-line "Loading…" status and then their content, so the footer (and on a phone the map) jumped. Both now reserve `min-height: 70vh` (`.loading-space`) while loading, so what follows starts below the fold. The alerts page does the same for its saved-routes list.
- **The city page no longer crashes without WebGL2.** `city-map.tsx` catches the MapLibre constructor error and shows one sentence; the lists hold everything.
- **The MapLibre chunk mounts when the map nears the viewport** (`LazyMount`, 400 px margin). On a laptop the map is beside the form and mounts at once; on a phone it sits below the form or the list.
- **Nothing was added to the first-load bundle** beyond the offline line, the service worker registration and the lazy mount (First Load JS for `/` is 123 kB before and after, from `next build`).

## Console errors, uncaught exceptions and failed requests

| Page | before, API up: app errors / network failures / axe violations | after, API up: app errors / network failures / axe violations | after, API down: app errors / network failures / axe violations |
| --- | --- | --- | --- |
| `/` | 0 / 0 / 0 (4 contexts) | 0 / 0 / 0 (4 contexts) | 0 / 16 / 0 (4 contexts) |
| `/?from=…&to=…` | 0 / 7 / 0 (4 contexts) | 0 / 8 / 0 (4 contexts) | 0 / 24 / 0 (4 contexts) |
| `/route?…` | 0 / 0 / 0 (4 contexts) | 0 / 0 / 0 (4 contexts) | 0 / 32 / 0 (4 contexts) |
| `/city` | 0 / 0 / 0 (4 contexts) | 0 / 0 / 0 (4 contexts) | 0 / 32 / 0 (4 contexts) |
| `/alerts` | 0 / 0 / 0 (4 contexts) | 0 / 0 / 0 (4 contexts) | 0 / 24 / 0 (4 contexts) |
| `/settings` | 0 / 0 / 0 (4 contexts) | 0 / 0 / 0 (4 contexts) | 0 / 8 / 0 (4 contexts) |
| `/about` | 0 / 0 / 0 (4 contexts) | 0 / 0 / 0 (4 contexts) | 0 / 8 / 0 (4 contexts) |
| `/sources` | 0 / 0 / 0 (4 contexts) | 0 / 0 / 0 (4 contexts) | 0 / 24 / 0 (4 contexts) |
| `/privacy` | 0 / 0 / 0 (4 contexts) | 0 / 0 / 0 (4 contexts) | 0 / 8 / 0 (4 contexts) |
| `/no-such-page` | 0 / 4 / 0 (4 contexts) | 0 / 4 / 0 (4 contexts) | 0 / 12 / 0 (4 contexts) |

Each cell counts all four contexts (light and dark, 390 and 1280 px) of one page. **App errors** are `console.error` calls, uncaught exceptions and failed page loads from OpenPath's own code: **0 on every page in every run**, so Lighthouse's `errors-in-console` audit passes on every page (best practices 100). **Network failures** are the browser's own "Failed to load resource" lines and Playwright's `requestfailed` events:

- With the API up, the 7 (before) and 8 (after) failures on the planner with a trip are OpenFreeMap tile requests MapLibre itself cancels (`net::ERR_ABORTED`) when the map fits the route and the zoom changes; the 4 on `/no-such-page` are that page's own 404 status, which is the right answer for a missing page.
- With the API down (the server stopped), the 184 failures are the API requests themselves (`/health`, `/api/route`, `/api/status`, `/api/priority`, `/api/cameras`, `/api/saved-routes`, `/api/push/public-key`), two console lines each. They are expected; the page's job is to say so, and every page did: the planner's "The route service did not answer. Check your connection and try again." with Try again and the labelled demo routes, the city page's alert with Try again and the last good list, the alerts page's "Alerts cannot be checked right now" with Try again, the sources page's "Check again", and the Replay badge simply absent because `/health` cannot say the mode. Screenshots of every page in that state are in `after-api-down/`.
- The baseline run was made with the same scripts against the same base build, so the two "API up" columns compare like with like.

## axe, keyboard, reflow and text size

- **axe-core 4.11.4**, tags `wcag2a wcag2aa wcag21a wcag21aa wcag22aa`, on the ten pages (the nine above plus `/no-such-page`) in light and dark at 390 and 1280 px, after scrolling the whole page so the maps mount: **0 violations in all 40 page-contexts**, before and after. The after run passed between 19 and 30 rules per context; the full results are in `scan-api-up.json` beside the screenshots. No rule needed review ("incomplete") on either run.
- **Keyboard** (Playwright, Chromium 141, 1280 px, light): see the map section below for #119 and #120. The skip link, the planner form, the route choice radios and the city filters are unchanged from OP-301/OP-310 and were covered by their reviews.
- **Reflow at 320 px:** horizontal overflow 0 px and 0 clipped text elements on all ten pages, before and after.
- **200% text** (root font size 32 px at 390 px wide): horizontal overflow 0 px and 0 clipped text elements on all ten pages, before and after.
- **Reduced motion:** not re-measured in this story. The maps now read the OP-307 setting (`prefersReducedMotion()` for the tile fade, `useReducedMotion()` for fitting the route) instead of the media query alone, which is the behaviour OP-307 documented.

## Map controls and markers (#118, #119, #120)

Measured with Playwright 1.56.1 (Chromium 141) on the final build, `checks.mjs`.

**#120, zoom controls.** `getBoundingClientRect()` of `.maplibregl-ctrl-zoom-in` and `.maplibregl-ctrl-zoom-out`, after scrolling the map into view so it mounts:

| Map | Theme | Viewport | Before | After |
| --- | --- | --- | --- | --- |
| Planner (with a trip) | light | 390 px | 29.0 x 29.0 CSS px (both) | 44.0 x 44.0 CSS px (both) |
| Planner (with a trip) | light | 1280 px | 29.0 x 29.0 | 44.0 x 44.0 |
| Planner (with a trip) | dark | 390 px | 29.0 x 29.0 | 44.0 x 44.0 |
| Planner (with a trip) | dark | 1280 px | 29.0 x 29.0 | 44.0 x 44.0 |
| City | light | 390 px | 29.0 x 29.0 | 44.0 x 44.0 |
| City | light | 1280 px | 29.0 x 29.0 | 44.0 x 44.0 |
| City | dark | 390 px | 29.0 x 29.0 | 44.0 x 44.0 |
| City | dark | 1280 px | 29.0 x 29.0 | 44.0 x 44.0 |

Before, `globals.css` already asked for 44 px, but MapLibre's stylesheet arrives with the map chunk, after `globals.css`, and its `.maplibregl-ctrl-group button { width: 29px; height: 29px }` won on equal specificity. The rules now live under `.maplibregl-map`, which outweighs it. Shift+Tab then Tab lands on the button named "Zoom in" with the project's ring drawn 4 px inside the control (`zoom-focus-*.png` in the screenshots folder); the ring colour follows the map surface: `#E06F00` on the white controls (3.3:1), `#FFB454` on the dark controls (8.4:1).

**#119, focus when selecting a marker.** Markers are pointer shortcuts and never in the tab order (DESIGN.md, City map markers), so the keyboard path is the list's "Show on map" button. On `/city` at 1280 px: focus before Enter was `button "Show on map" pressed=false`; after Enter it was the same button with `aria-pressed="true"`; all 61 marker elements tagged before the selection were the same DOM nodes afterwards (61 of 61 kept, 1 selected); Tab moved to the next row's "Show on map" and Space toggled the selection with focus staying put. On the planner, "Show route 2" keeps focus on the same control, now reading "Hide route 2". The markers are rebuilt only when the data changes (`city-map.tsx`, `planner-map.tsx`); selection toggles a class. If a selected item disappears on refresh, its row (and the focused button) go with it and the count line announces the new total; focus falls back to the document, which this story did not change.

**#118, marker contrast.** The element around each map canvas carries the map's palette (`.map-light` or `.map-dark`), so the markers, Start/End tags, camera tags and controls take the basemap's tokens, not the page theme's. The computed pairs are in [docs/design/DESIGN.md](../docs/design/DESIGN.md#10-states-and-the-map-surface-op-410): every hazard shape is at least 5.8:1 on its disc in both palettes, the camera reading word 16.8:1 (light) and 13.7:1 (dark), the selected ring 6.8:1 on the liberty land and 8.3:1 on the dark land. Before, in the dark theme on the light basemap, the disc border was 1.06:1 against the land and the selected ring 1.75:1. Rendered evidence: `city-dark-390.png` and `city-dark-1280.png` in the before and after screenshot folders.

## Offline

Measured with Playwright 1.56.1 (Chromium 141) at 390 px, light and dark, on the final build (`offline.mjs`), with `context.setOffline(true)` standing in for a lost connection.

- **The offline line.** `/city` loaded online with 10 rows on screen; the device then went offline. The line read "You are offline. Showing what was loaded before." (24 px tall), the `<h1>` stayed at 125 px from the top in both themes (nothing moved), and the 10 rows stayed.
- **A refresh while offline** kept the 10 rows and showed the alert "The ranking could not be loaded. This device is offline, so the ranking could not be checked. The list below is from the last good load. Try again in a minute." with its Try again button.
- **The service worker** was active after one page load (`/about`). A navigation to `/settings` while offline showed `public/offline.html`: title "Offline | OpenPath", heading "You are offline.", `lang="en"`. When the connection returned, the page reloaded itself and showed Settings.
- **The not-found page** answers `/no-such-page` with HTTP 404, the title "Page not found | OpenPath" and the heading "This page does not exist."
- **The error page** was checked on an earlier build of this branch with a temporary page that throws after it mounts (never committed): it rendered "This page stopped working." with a "Try again" button in both themes (`after-error-page/error-page-*.png` in the screenshots folder). The final build has no such page, so the final run reports the not-found page at that address instead.
- **App errors during the offline checks:** 0 in both themes on the final build.
