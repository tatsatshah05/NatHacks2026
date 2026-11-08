# OpenPath design: the wayfinding line

Every Phase 3 screen is built from this page. The code is in `web/src/app/globals.css` (tokens and styles) and `web/src/components/ui/` (components). If a screen needs something this page does not cover, add it here in the same pull request.

## 1. Design plan

**Subject, audience, job.** OpenPath plans accessible walking routes across Edmonton. People open it on a phone, outdoors, often in winter: gloves, glare, cold hands and little patience. Many of them use a wheelchair, a walker or cane, or have low vision. The screen has one job: show the route and every known barrier on it, quickly and without doubt.

**Direction.** The route is the interface. After "Find routes", each route is drawn like a transit line diagram. A vertical line in the action colour runs from the start (a filled circle) to the end (a filled circle). Every known condition is a stop on that line, drawn as a distinct shape with a text label. Conditions the route avoids sit beside the line, where it kinks away from them. The map only illustrates the line and is never the only source of information. Everything around the line is quiet. The boldness is spent in one place.

**Colour.** Five base values: Snow `#F5F8FA` (page), Card `#FFFFFF` (panels), Ink `#0D1F2D` (text and rules), Muted `#4A5B6A` (secondary text and control borders) and Action `#1450A3` (buttons, links and the route line). Hazard colours appear only on hazard shapes and their labels.

**Type.** One family: Atkinson Hyperlegible 400 and 700, drawn by the Braille Institute for low-vision readers. Its slashed zero and distinct letterforms are the app's only typographic voice.

**Layout.** Mobile first: one left-aligned column with 16 px gutters. From 900 px wide, the plan and the route line sit on the left (at most 44ch) and the map sits on the right, sticky (16 px from the top) and visually secondary. Nothing is centred. Results show the recommended route with its whole line; alternates are comparison rows that open in place. Conditions that cover the whole city are shown once, above the routes.

```
Phone (390 px)                     Desktop (from 900 px)
+------------------------+         +----------------------+-------------------------+
| OpenPath  [Replay]     |         | OpenPath [Replay]     Plan  City  Alerts About |
| Plan City Alerts About |         +----------------------+-------------------------+
+------------------------+         | Where are you going? |                         |
| Where are you going?   |         | From [............]  |       route map         |
| From [..............]  |         | To   [............]  |   (sticky, secondary)   |
| To   [..............]  |         | [Wheelchair][Walker] |                         |
| [Wheelchair][Walker ]  |         | [Low vision][Std   ] |                         |
| [Low vision][Standard] |         | [   Find routes    ] |                         |
| [    Find routes     ] |         |                      |                         |
|                        |         | 35 min, 2.1 km, ...  |                         |
| 35 min, 2.1 km, ...    |         |  o  Central Station  |                         |
|  o  Central Station    |         |  |                   |                         |
|  |                     |         |  ^  Escalator out    |                         |
|  ^  Escalator out      |         |  (    [/] Avoided    |                         |
|  (    [/] Avoided      |         |  |                   |                         |
|  o  Royal Alexandra    |         |  o  Royal Alexandra  |                         |
| [View route report   ] |         | [View route report ] |                         |
| 37 min, 2.2 km, ...    |         | 37 min, 2.2 km, ...  |                         |
| [Show route 2        ] |         | [Show route 2]       |                         |
| map (after results)    |         +----------------------+-------------------------+
+------------------------+
```

**Principles.**

1. The line is the hero. If a screen has a route, the route line is the most visible thing on it.
2. Shape before colour. Every condition has a shape, a fill pattern and words, so it reads in greyscale, in glare and in a screen reader.
3. Quiet chrome. Panels are flat cards with one ink rule on top. No shadows, gradients, badges or decoration.
4. One reveal. The line draws itself once when routes arrive, or when someone opens an alternate (600 ms, top to bottom). Nothing else moves.
5. Plain words. Sentence case, verbs on buttons, errors that say what to do.

## 2. Review against generic tells

Before building, the plan was checked against the usual tells of a generated page. Where an item matched, the plan changed.

| Tell | In the old screens | In this design |
| --- | --- | --- |
| Uppercase tracked eyebrow above every heading | Yes ("ACCESSIBLE TRIP PLANNER", "1. YOUR TRIP") | Removed. Headings stand alone. |
| Marketing hero with a big headline and pitch copy | Yes | Removed. The first screen is the form under "Where are you going?". |
| SaaS card kit: identical rounded cards with soft shadows | Yes (16 px radius and the same shadow everywhere) | Flat card, 6 px radius, a 2 px ink top rule, no shadow. The city list uses rows, not cards. |
| Numbered markers where the content is not a sequence | Yes ("1.", "2.", "3." on form sections) | Removed. Numbers appear only on route steps, which are a real sequence. |
| Middle-dot meta strings ("A · B") | Yes | Removed. Commas and full sentences instead. |
| Arrows in button or link text | Yes ("→" in the report title, "←" on the back link) | Removed. Links and buttons say what happens. |
| Emoji or glyph standing in for a brand mark | Yes ("⌁") | Replaced by an inline SVG route glyph: two circles joined by a line. |
| Negative letter-spacing on display type | Yes (down to -0.06em) | Letter-spacing is 0 everywhere. |
| Cream with serif and terracotta, or near-black with acid green | No | Not used. Cold, clear winter palette. |
| Accenting one word in a headline | No | Not used. The ETA is larger because it is the answer, not decoration. |
| Monospace for small data labels | No | Not used. One family throughout. |
| Hover transitions on every card | No | None. Hover only underlines text. |

Changes made after reviewing the first screenshots ("remove one accessory"):

- The route report showed every condition twice, once on the line and once in the conditions panel. On the report the line is now compact (title and status only) and works as an index to the full panel.
- The chosen places on the demo map were solid ink boxes, louder than the route line. They are now outlined, and the drawn roads are thinner.
- The city list used bordered cards and repeated the kind ("Escalator out") above the title ("Escalator out of service"). It now uses plain rows, with the title as the hazard label.
- A kind sentence that repeated the title was dropped from each report condition.
- OP-301, live planner: "Replayed data" is said in full once in the status line and as two words in each route's count line. The sentence under the map ("The list has everything the map shows") appears only when there are routes. The route drawn thick on the map is named in words ("Shown on the map as the thick line") and its panel's top rule thickens to 6 px in the line's colour; nothing else marks it.
- Lead review: three fully drawn routes made a 5,000 px phone page that was hard to compare. Alternates are now rows with a count and a "Show route 2" disclosure. The ice-risk watch, which sat on every line, is shown once above the routes. Effect codes on stops ("caution") became plain words ("take care"). In dark mode the chosen profile was the same blue as "Find routes"; it now fills with ink, as in light mode.

## 3. Tokens

Contrast is the WCAG relative-luminance ratio, measured with a script against the page (snow) and the panel (card). Normal text needs 4.5:1; focus rings and meaningful graphics need 3:1.

| Token | Use | Light | Ratio (snow / card) | Dark | Ratio (snow / card) |
| --- | --- | --- | --- | --- | --- |
| `--snow` | Page background | `#F5F8FA` | n/a | `#0D1F2D` | n/a |
| `--card` | Panels, inputs | `#FFFFFF` | n/a | `#16293A` | n/a |
| `--ink` | Text, rules | `#0D1F2D` | 15.7 / 16.8 | `#F2F6F9` | 15.5 / 13.7 |
| `--muted` | Secondary text, control borders | `#4A5B6A` | 6.6 / 7.0 | `#A9B8C6` | 8.3 / 7.3 |
| `--line` | Decorative separators only | `#B9C6D1` | 1.6 / 1.7 | `#3C5063` | 2.0 / 1.8 |
| `--action` | Buttons, links, the route line | `#1450A3` | 7.3 / 7.8 | `#8DB8FF` | 8.3 / 7.4 |
| `--on-action` | Text on action | `#FFFFFF` | 7.8 on action | `#0D1F2D` | 8.3 on action |
| `--focus` | Focus ring | `#E06F00` | 3.1 / 3.3 | `#FFB454` | 9.5 / 8.4 |
| `--blocked` | Blocked shape and label | `#B3261E` | 6.1 / 6.5 | `#FF9A8F` | 8.2 / 7.3 |
| `--ice` | Ice-risk shape and label | `#0B6F80` | 5.5 / 5.8 | `#5FD4E8` | 9.7 / 8.6 |
| `--snow-hazard` | Snow shape and label | `#5B4FCF` | 5.7 / 6.1 | `#C2B8FF` | 9.3 / 8.2 |
| `--caution` | Caution shape and label | `#8A5300` | 5.9 / 6.3 | `#F4B860` | 9.5 / 8.4 |
| `--clear` | All-clear shape and label | `#1E6B3A` | 6.1 / 6.5 | `#7FD39A` | 9.3 / 8.3 |

- **Theme switching.** Dark follows `prefers-color-scheme`. It is guarded by `:root:not([data-theme="light"])` and repeated under `:root[data-theme="dark"]`, so OP-307 can force either theme by setting `data-theme` on `<html>`. The Settings page does exactly that (section 9).
- **Focus ring.** The focus ring is 3 px with a 2 px card-coloured gap, so it always sits next to card or snow, never next to the control's own fill. In light mode the focus colour on action is only 2.4:1, which is why the gap exists.
- **Line colour.** `--line` is 1.6:1, so it is never a control border. Controls use `--muted`.
- **Chosen state.** A chosen segment fills with `--ink` and uses `--snow` text in both themes (15.7:1 light, 15.5:1 dark). Action blue is only for actions, so a chosen option never looks like a second primary button.
- **New colours.** If you add a colour, measure it and put the ratio in a comment beside it.

## 4. Type

Atkinson Hyperlegible, 400 and 700, loaded with `next/font/google` (`display: swap`) as `--font-body` on `<html>`. Nothing on a phone is set below 16 px.

| Role | Size / line-height | Weight | Where |
| --- | --- | --- | --- |
| Body | 16 / 1.5 | 400 | Everything by default |
| Lead | 18 / 1.5 | 400 | Report summary, one line under a page title |
| h3 | 20 / 1.2 | 700 | Report groups |
| h2 | 25 / 1.2 | 700 | Panel titles, route heading |
| h1 | 31 / 1.2 | 700 | One per page |
| Route ETA | 39 / 1 | 700 | The minutes inside the route heading only |

Headings use letter-spacing 0. Lines never run past 70ch. Use sentence case everywhere and never uppercase.

## 5. Components

| Component | Use it for | Notes |
| --- | --- | --- |
| `SiteHeader` | Every page | Skip link, wordmark, Plan / City / Alerts / Settings / About, `ModeBadge`. Pass `current`: the link to this page gets `aria-current="page"`; `sources` and `privacy` mark About with `aria-current="true"`. The nav wraps to a second row on narrow screens and at large text sizes. |
| `SiteFooter` | Every page | The data warning, any page-specific children, then links to Data sources and Privacy. |
| `ModeBadge` | Inside `SiteHeader` (automatic) | Reads `GET /health`. Shows "Replay" in a bordered pill when `mode` is `replay`; nothing when live or unreachable. |
| `Button`, `ButtonLink` | Every action | `primary` (filled action) for the one main action on a screen; `secondary` (ink outline) for the rest; `text` for quiet inline actions. 44 px minimum height, `size="large"` gives 56 px. External hrefs get `rel="noreferrer"`. |
| `Field` | Every text input | Label above. Help and error are linked with `aria-describedby`. The error states what went wrong and what to do. |
| `SegmentedChoice` | One choice from 2 to 4 options | Native radios, so arrow keys move and the group is one tab stop. `layout="grid"` for short labels (profiles), `layout="stack"` for options with a description (route choice). |
| `Panel` | Grouping a section on a surface | Card with a 2 px ink top rule, 6 px radius, no shadow. Do not nest panels. |
| `StatusLine` | Results counts, loading, confirmations | `role="status"`. Keep it in the DOM even when empty so changes are announced. |
| `TimeAgo` | Any time a reading was taken | "Checked 4 min ago" for source readings, "Updated just now" for the page. The exact time is in the `title` and `datetime`. |
| `HazardMark` | Any condition in a list, legend or report | Shape plus visible label. `HazardShapeIcon` is the shape alone, only when the words sit right next to it. |
| `RouteLine` | Any screen that shows a route | Heading sentence, reason, then an ordered list: start, each condition, end. `reveal` draws it once; `compact` drops detail and check times when the full report follows. Its parts are exported: `RouteHeading` (sentence and reason), `RouteStops` (the line) and `routeTally` ("1 condition on this route, 1 within 50 m, 1 avoided."). |
| Route results (`route-result.tsx`) | A list of 2 or 3 routes | The recommended route shows its whole line and buttons. Alternates show heading, reason and count, plus a "Show route 2" button (`aria-expanded`, `aria-controls`) that opens the line and the buttons in place. |
| Planner map (`planner-map.tsx`) | The routes of `GET /api/route` on the planner | MapLibre on the OpenFreeMap liberty basemap, loaded after the form. The selected route is the thick line (6 px, action, on a 10 px white casing); the others are 3 px muted lines; an LRT ride is a double line (two 3 px rails, 5 px gap). Start and end are ink circles with a "Start" / "End" tag. Conditions use the hazard shapes as pointer shortcuts only (hidden from assistive technology); a click jumps to the same stop in the list. Fits the selected route; jumps instead of flying under reduced motion. A key under the map names each line weight in words. |
| `CheckField` | One on/off choice ("Include LRT trips", "Speak route changes aloud") | A native checkbox drawn in ink at 24 px on a 44 px row, help tied with `aria-describedby`. |
| `LiveStatus` | The live connection on a page with a route | One quiet line, not a badge: a circle that is filled (live), half filled (reconnecting) or hollow (offline), and the word. Then the speech switch and the assertive region for route changes, which is always in the page. |
| `RouteFailure`, `NoRoute` (`route-problem.tsx`) | A route request that failed or found nothing | An ink rule on the left, a short heading that says what happened, one sentence that says what to do, then "Try again" (and "Show demo routes instead" when the service cannot be reached). A refused request offers no retry: it needs new places. |
| `CityWideConditions` | Conditions that cover the whole city | One line each, shown once above the routes: "Ice-risk watch is on for the whole city." They are never stops on a line. The route report still lists them under Weather and ice. |
| `SaveRouteButton` (`save-route-button.tsx`, OP-306) | "Save this route" in each walking route's actions, right after "View route report" (a walk and LRT trip has none: a saved route has no mode, so the API watches the walking route) | One tap opens an inline form (name, filled with the destination). When alerts can be turned on, the form's main button is "Save and turn on alerts" (the permission request starts inside that press) with "Save without alerts" beside it; otherwise "Save route" plus a note saying why alerts are not offered. The result takes focus and says what happens: "Saved. You'll get an alert if this route closes or gets 20% slower." Saved without alerts, it offers "Turn on alerts for this route". The open form sits between two `--line` rules. |
| `OfflineLine` (OP-410) | Inside `SiteHeader` (automatic) | One `role="status"` line under the header rule: "You are offline. Showing what was loaded before." when `navigator.onLine` is false, empty otherwise. Its height is reserved, so it moves nothing (section 10). |
| `LazyMount` (OP-410) | Around each map | Mounts its children when its place comes within 400 px of the viewport; the placeholder has the children's size. On a laptop the maps mount at once; on a phone, when scrolled to. |
| Not-found and error pages (`app/not-found.tsx`, `app/error.tsx`, OP-410) | A missing page; a page that threw | The problem block alone (section 11): what happened, what to do, "Plan a route" and, on the error page, "Try again". |
| Alerts page (`alerts-page.tsx`, OP-306) | `/alerts` | Saved routes first (no route, then changed, then clear), as plain rows, then one panel "Alerts on this device" with the state in words. Remove asks first ("Keep it" takes focus, Escape keeps), then a status line with Undo, which saves the trip again. |

**City-wide items.** The API marks an item that covers the whole city only through its note (`cloud/app/report.py` `NOTES["citywide"]`), so `isCityWide()` in `lib/report-format.ts` tests that note. If `ReportItemOut` gains an `everywhere` flag, test that instead.

**Stop order.** The API does not yet say where along the route each condition lies. Stops therefore follow the report's group order (indoor, sidewalks and paths, roads and crossings, weather and ice), and within each group the report's own order. When `GET /api/route` adds a position, sort by it.

**Saved route status.** A saved route's status uses the hazard shapes with words: `ok` is the clear circle, "Clear, about 12 min"; `changed` is the caution triangle, "Changed: now 16 min (+4)" (whole minutes, as the push message counts them); `impossible` is the blocked square, "No route right now".

**App icon.** `scripts/build_icons.py` draws the icons from coordinates: the wordmark's start and end circles joined by a line that jogs at 45 degrees, as the route line kinks around a barrier, in white on `--action`. The maskable icon keeps the glyph inside the central 40% safe circle. `scripts/make_icons.py` (OP-411) runs it and adds the browser favicon `web/src/app/icon.svg` from the same geometry, with a light and a dark variant: a `prefers-color-scheme` rule inside the SVG swaps the tile and glyph between each theme's `--action` and `--on-action`, so the mark keeps its contrast on a dark tab bar. Launcher icons cannot carry that rule, so `web/public/icons/icon.svg` stays single-coloured.

**City map markers.** Markers are pointer shortcuts only. The list beside the map is the keyboard and screen-reader path and selects the same items, so markers are hidden from assistive technology and stay out of the tab order.

**City dashboard (OP-310, OP-309).** `/city` is a ranking, so each row shows its rank number in a 25 px bold column; that column is the page's one strong element. Rows are plain rows with decorative rules, 25 per page on a laptop and 10 on a phone, with "Show 10 more" and "Show all 59" (focus moves to the first new row). Filters are native selects with their label above and a chevron drawn with borders, so it follows the theme. Road cameras use their own glyph (a camera body with a lens hood, never a hazard shape) on a squared tag that carries the reading in words ("Wet", "Ice risk", "Unknown"); the "Road cameras" list holds each view's reading, model confidence and time. Camera readings are said as "Wet road", "Some snow on the road", "Snow-covered road", "Ice risk" (never "black ice") and "Unknown: camera unavailable".

## 6. Hazard shapes

The effect sets the shape. The kind supplies the words ("Elevator out"). Each shape differs in both outline and fill pattern, and stays distinct at 16 px.

| Meaning | Shape | Fill | Colour token | Label |
| --- | --- | --- | --- | --- |
| Start, end | Circle | Solid | `--ink` | "Start", "End" and the place name |
| `blocked` | Square | Diagonal stripes | `--blocked` | "Path closed" |
| `ice_risk` | Diamond | Dots | `--ice` | "Ice risk" (never "black ice detected") |
| `snow` | Hexagon | Dashes | `--snow-hazard` | "Snow on the path" |
| `caution` | Triangle | Solid | `--caution` | "Take care" |
| Clear | Circle with a check | Outline | `--clear` | "No known obstructions" |

On the route line, an avoided condition is offset to the right, and the line kinks away from it. Each stop reads status, then the effect in words: "Avoided: path closed", "On this route: take care", "12 m from this route: take care".

**Walk and LRT trips (OP-311 legs, drawn since OP-301).** A trip with `legs` draws walks as the usual single line, with a quiet line of text ("Walk 155 m, about 3 min"); a ride as a double line (two 3 px action rails with a 6 px gap) titled with the line and direction ("Capital Line toward Century Park"), then "9 stops, 18 min" and "About 8 min wait (estimate)"; and each station as an ink ring (a hollow circle, not a hazard shape) with "LRT station" and its elevator note. A station's elevator or escalator outage is a stop beside the station, on its walking side. Conditions without a position follow the last leg, in report order.

**On the map.** The basemap is light in both themes, so the map's lines, place markers and focus ring keep the light theme's values (action `#1450A3`, muted `#4A5B6A`, ink `#0D1F2D`, focus `#E06F00`) instead of the theme tokens, which would turn light in dark mode.

## 7. Copy

- **Buttons.** Say what happens: "Find routes", "View route report", "Open in Google Maps", "Save this route", "Refresh conditions".
- **Errors.** Say what went wrong and what to do: "Choose a starting place first."
- **Empty states.** Invite an action: "No current obstructions are reported. Plan a route to see conditions along it."
- **Data honesty.** Label demo and replayed data on the screen itself, in words.
- **Time.** Use "Checked" for when we last confirmed a reading, not "Updated".
- **Effects.** Never show an effect code. Use these words everywhere, from `effectLabel()` in `lib/report-format.ts`:

  | Effect code | Words |
  | --- | --- |
  | `blocked` | Path closed |
  | `snow` | Snow on the path |
  | `ice_risk` | Ice risk |
  | `caution` | Take care |
- **Tone.** Use sentence case. Write no filler, no exclamation marks and no apologies.

## 8. Do not

- Do not use uppercase eyebrows, tracked-out labels or all-caps text.
- Do not add a hero, marketing headline or pitch copy above the task.
- Do not add shadows, gradients, glows or hover transitions. Do not give every item its own bordered card.
- Do not number content that is not a sequence.
- Do not use middle-dot meta strings ("A · B · C") or labels built as a word, a spaced dash and a fragment.
- Do not put arrows ("→", "←") in button or link text.
- Do not use emoji or Unicode glyphs as icons. Draw icons as inline SVG with `aria-hidden`.
- Do not use negative letter-spacing, a second typeface or monospace labels.
- Do not use a hazard colour on chrome, or colour without a shape and words.
- Do not use `--line` as a control border.
- Do not add motion beyond the single line reveal, and keep it off under reduced motion.
- Do not present replayed or demo data as live.

## 9. Settings and reading pages (OP-307, OP-312)

**Settings live on the device.** `lib/settings.ts` keeps them in `localStorage` (`openpath.settings` as JSON, and `openpath.speak`, which OP-303 already used) and gives every component `useSettings()`. A tiny inline script in `app/layout.tsx` (`lib/settings-boot.ts`) applies them to `<html>` before the first paint, so pages never flash at the wrong size or theme. When storage is blocked, a choice lasts for the page visit and the page says so.

| Setting | Values (default first) | Applied as |
| --- | --- | --- |
| Speak route changes | on, off | `announce.ts` speaks only when on |
| Vibrate on route changes | on, off | `announce.ts` vibrates only when on |
| Text size | default, large (125%), larger (150%) | `data-text` on `<html>`: the root font size, so every rem scales and layouts reflow; px targets stay at least 44 px |
| Theme | follow the device, light, dark | `data-theme` on `<html>` (section 3) |
| Motion | follow the device, always reduce, never reduce | `data-motion` on `<html>`; the reduced-motion rules apply under the media query unless `data-motion="full"`, and always under `data-motion="reduce"`. Script motion (map fades, fly-to) reads `useReducedMotion()` |
| High-contrast map | off, on | `mapStyleFor(settings, dark)` in `lib/map-style.ts` |

**The settings page** uses the system's own controls: switches are native checkboxes with `role="switch"` (named by their label, described by their line of help, state shown by knob position and fill plus the word On or Off), and the three-way choices are `SegmentedChoice` in the stacked layout with a one-line description each. Every change is confirmed in the status line ("Text size is now large.").

**High-contrast map.** Built on the same OpenFreeMap vector tiles as the liberty style, with only land, water, roads, paths, rail and labels. Paths are dashed and roads solid, so they differ by shape. Measured with the WCAG relative-luminance formula:

| Pair | Light | Ratio | Dark | Ratio |
| --- | --- | --- | --- | --- |
| Roads and paths on land | `#0D1F2D` on `#FFFFFF` | 16.8:1 | `#FFFFFF` on `#0D1F2D` | 16.8:1 |
| Water on land | `#5A7FA6` on `#FFFFFF` | 4.2:1 | `#5A7FA6` on `#0D1F2D` | 4.0:1 |
| Roads on water (bridges) | `#0D1F2D` on `#5A7FA6` | 4.0:1 | `#FFFFFF` on `#5A7FA6` | 4.2:1 |
| Rail on land | `#4A5B6A` on `#FFFFFF` | 7.0:1 | `#A9B8C6` on `#0D1F2D` | 8.3:1 |
| Label text on its halo | `#0D1F2D` on `#FFFFFF` | 16.8:1 | `#FFFFFF` on `#0D1F2D` | 16.8:1 |
| Route line on land | `#1450A3` on `#FFFFFF` | 7.8:1 | `#8DB8FF` on `#0D1F2D` | 8.3:1 |

The route line alone is only 2.2:1 (light) and 2.0:1 (dark) against the roads it runs over, so a page that draws a route on this style puts a land-coloured casing under it (`mapPalette(dark).routeCasing`, 2 px wider on each side). Label sizes follow the text size setting.

**Reading pages** (`/about`, `/sources`, `/privacy`) share `components/reading.module.css`: one left-aligned column at a 68ch measure, the type scale in section 4, headings that stand alone, real list markers, and no hero, cards or images. The only drawn element is on `/about`: "How a route is built" is a real sequence, so it is drawn as the route line (filled start and end, open steps). `/sources` uses one table per publisher (Source, Used for, Freshness); below about 44 characters of width each row becomes a block with its column names shown, and explicit table roles keep the semantics. Freshness comes live from `GET /api/status` and `GET /api/cameras`.

**Changes after the first screenshots ("remove one accessory").** The reading lists had lost their markers to Tailwind's preflight; they are back. A credit that already ended in a full stop got a second one; credits are now quoted exactly. The long "What OpenPath uses it for" column name became "Used for". Numbers and units are joined by non-breaking spaces so "30 m" never splits across lines. The explanation above the speech switches was cut to two short sentences.

## 10. Judge page and launch assets (OP-411)

**Subject, audience, job.** `/demo` is the hand-off. A judge sits across a table from a laptop, or looks at a projector, and has seconds: get the app onto their own phone, then tell them one trip to try. The page is read from a distance, so it is one screen at 1280 × 800, quiet and large. On a phone it is the same page in one column.

**Direction: one square, one sentence, one trip.** The QR code is the one bold element here; the route line is the system's bold element everywhere else, and the two are never on the same screen. Everything around the square is the system's own type, left aligned, with no panel, rule or drawing to compete with the three finder squares. The square sits in the right column, where other screens put the map, and the words sit in the left column at a 52ch measure (the planner's 44ch made the trip's description wrap past the fold at 800 px): the page keeps the grid people already know.

```
Desktop (1280 x 800)                                   Phone (390)
+----------------------------------------------------+  +--------------------------+
| o-o OpenPath [Replay]   Plan City Alerts ... About |  | o-o OpenPath [Replay]    |
+----------------------------------------------------+  | Plan City Alerts ...     |
| OpenPath                    +------------------+   |  +--------------------------+
| Accessible routes across    |                  |   |  | OpenPath                 |
| Edmonton, with every known  |   [QR code]      |   |  | Accessible routes ...    |
| barrier between A and B.    |                  |   |  | +----------------------+ |
|                             |                  |   |  | |      [QR code]       | |
| Try this trip               +------------------+   |  | +----------------------+ |
| Edmonton Convention Centre  openpath-lyart.        |  | openpath-lyart.vercel.app|
| to the Alberta Legislature, vercel.app             |  | Try this trip            |
| for a wheelchair.                                  |  | ...                      |
| Three routes, drawn as ...                         |  | Then                     |
|                                                    |  | ...                      |
| Then                                               |  +--------------------------+
| Switch the profile. ...                            |
| Open the route report ...                          |
| Save the route and turn on alerts. ...             |
+----------------------------------------------------+
| Conditions come from public data ...  Data sources  Privacy |
+----------------------------------------------------+
```

**Colour.** Ink on snow, as everywhere. The QR code is the one exception to the theme tokens: its modules are always `#0D1F2D` on a `#FFFFFF` tile, in dark mode too, because many phone cameras refuse an inverted code and the page has one job. The tile is the code's quiet zone (four modules on every side) made visible. The address under it is a link in `--action`.

**Type.** The system's scale, one step up where a judge reads from a distance: h1 31 for the name, the pitch and the address at 25 (400 and 700), the trip link and the "then" lines at 18 (the lead size), the trip's detail at 16. Nothing is tracked or capitalised. The address drops its `https://`, as people read it.

**Layout.** One column with 16 px gutters; from 900 px, `minmax(0, 52ch)` of words on the left and the square on the right, left aligned in its column, never centred. The square's side is `clamp(240px, 100vh - 360px, 520px)`, so the whole page, header and footer included, fits 800 px without scrolling and the square stays the largest thing on the screen. On a phone the square is the full column width. The DOM order is name, pitch, square and address, trip, then, in every layout.

**Copy.** The pitch is PLAN.md's one sentence. The trip is a real pair from `config/places.json`, written as a link that opens the planner pre-filled (`lib/route-link.ts`, the same `from`, `from_name`, `to`, `to_name` and `profile` parameters a shared route uses), and what the judge will see is stated from the replay data and labelled as such: live, the page shows today's conditions. The three "then" lines are plain verbs, not a numbered sequence.

**Principles.**

1. The square is the hero. Nothing else on the page is drawn.
2. Readable at three metres: the name, the address and the trip are the three things a judge needs, and each is one glance.
3. One screen. Header, square, words and footer fit 1280 × 800.
4. Honest. The Replay badge shows as on every page, and the trip's description says which conditions are recorded and which are live.

**Review against the usual tells.** A "scan me" poster centres the code and sets the address under it in a monospace face; this page keeps the system's left-aligned grid and one typeface. A "try this" list is usually numbered; these lines are not a sequence, so they take plain markers. A first draft put the square in a `Panel`: its 2 px ink top rule read as a fourth finder mark, so the square sits on the page with no frame. A caption that said "Scan with your phone's camera" was cut: the address beneath the square says what it is, and the alt text names the URL. The product name stays at the system's h1 size rather than a display size: the page has one bold element, and the square is it.

**The QR code.** `lib/qr.ts` is a dependency-free encoder written for this project from ISO/IEC 18004 (byte mode, versions 1 to 40, levels L to H, mask by the standard's penalty rules). It runs on the server, so the page carries one inline SVG path and no script. The production address encodes at version 3, level M (29 modules). Verified on November 8, 2026: the module matrices match the Python `qrcode` package for 211 of 216 combinations of six texts, four levels and all eight masks (the five differences are automatic mask choice only, where both masks are valid), and OpenCV's `QRCodeDetector` decodes the rendered page's screenshot to the production URL. `?url=` overrides the target for local testing (http or https only).

**Social preview and favicon.** `app/opengraph-image.tsx` renders the mark, the name, the pitch and the address at 1200 × 630 in the light tokens, with Atkinson Hyperlegible fetched when the image is built and the renderer's default sans serif when that fetch fails (a preview in the wrong face beats a failed build). Twitter cards fall back to the same image, so there is no second file. The icons Next links from every page are files beside the layout, because an `icons` field in the metadata export would replace them: `app/icon.svg` (the favicon with its light and dark variants, section 5, "App icon"), `app/icon1.png` (64 px, for browsers that ignore SVG favicons) and `app/apple-icon.png` (180 px). `scripts/make_icons.py` writes all three.

## 11. States and the map surface (OP-410)

**Plan.** Three things were missing or inconsistent before Phase 4: what a page says when it fails or does not exist, what the site says when the device is offline, and how the map looks in the dark theme. The plan adds nothing new to the chrome: one problem block for every failure, one offline sentence in one place, and the map as its own surface.

| Element | Choice |
| --- | --- |
| Problem block | The shape `RouteFailure` already has: an ink rule on the left, a heading that says what happened, one sentence that says what to do, then the action. On the not-found and error pages it is the whole page (`.page-problem`). No illustration, no "oops", no exclamation mark. |
| Offline line | One sentence under the header rule: "You are offline. Showing what was loaded before." It is a `role="status"` line that is always in the page, pulled up into the page's top padding with a negative margin, so it moves nothing when it appears or clears. Fetches that fail while offline say so and keep the last good data on screen. |
| Offline page | `public/offline.html`, which the service worker serves only when a navigation fails: the wordmark, "You are offline.", what to do and "Try again". It reloads itself when the connection returns. The worker never caches API responses or pages, so a stale route can never be shown as current. |
| Map surface | The liberty basemap and the high-contrast light style are light in both page themes, and the high-contrast dark style is dark. Everything drawn inside a map (condition markers, camera tags, Start and End tags, the zoom controls, the focus ring) therefore takes the map's palette, not the page's: `.map-light` and `.map-dark` redefine the tokens for the map frame, and the map components choose one from the settings. Nothing outside the frame (the key, the detail, the problem sentence) is inside the scope. |
| Zoom controls | 44 x 44 CSS px, the ink border, and the project's focus ring drawn 4 px inside the control, on both maps. |

**Review against the tells.** The not-found and error pages invite a centred illustration with a joke: refused; they are the problem block, left aligned, in the type scale. The offline line could be a coloured banner: it is one sentence in ink on the page background. The map markers could grow a label strip: they keep the shape on a disc, and the words stay in the list, which is the keyboard and screen-reader path.

**Measured contrast** (WCAG relative luminance, computed November 8, 2026 with a script; the same pairs appear as comments in `globals.css`). The light map applies on the liberty basemap, whose land is `#F1EFE9`, and on the high-contrast light style; the dark map is the high-contrast dark style.

| Pair | Light map | Dark map |
| --- | --- | --- |
| Blocked shape on the marker disc | `#B3261E` on `#FFFFFF`, 6.5:1 | `#FF9A8F` on `#16293A`, 7.3:1 |
| Ice-risk shape on the disc | `#0B6F80` on `#FFFFFF`, 5.8:1 | `#5FD4E8` on `#16293A`, 8.6:1 |
| Snow shape on the disc | `#5B4FCF` on `#FFFFFF`, 6.1:1 | `#C2B8FF` on `#16293A`, 8.2:1 |
| Caution shape on the disc | `#8A5300` on `#FFFFFF`, 6.3:1 | `#F4B860` on `#16293A`, 8.4:1 |
| Disc border on the land | `#0D1F2D` on `#F1EFE9`, 14.6:1 | `#F2F6F9` on `#0D1F2D`, 15.5:1 |
| Camera reading and Start/End words on their tag | `#0D1F2D` on `#FFFFFF`, 16.8:1 | `#F2F6F9` on `#16293A`, 13.7:1 |
| Selected ring on its gap and on the land | `#1450A3` on `#FFFFFF`, 7.8:1; on `#F1EFE9`, 6.8:1 | `#8DB8FF` on `#16293A`, 7.4:1; on `#0D1F2D`, 8.3:1 |
| Focus ring on the controls | `#E06F00` on `#FFFFFF`, 3.3:1 | `#FFB454` on `#16293A`, 8.4:1 |
| Zoom icons (MapLibre's grey, inverted on the dark map) | MapLibre's `#333333` on `#FFFFFF`, 12.6:1 | `#CCCCCC` on `#16293A`, 9.3:1 |

Before this change, the city markers took the page's tokens, so in the dark theme on the light basemap the disc border was 1.06:1 against the land and the selected ring 1.75:1, below the 3:1 that a meaningful graphic needs (issue #118).
