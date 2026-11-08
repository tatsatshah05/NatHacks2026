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
| Where are you going?   |         | From [............]  |       demo map          |
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
| `CityWideConditions` | Conditions that cover the whole city | One line each, shown once above the routes: "Ice-risk watch is on for the whole city." They are never stops on a line. The route report still lists them under Weather and ice. |

**City-wide items.** The API marks an item that covers the whole city only through its note (`cloud/app/report.py` `NOTES["citywide"]`), so `isCityWide()` in `lib/report-format.ts` tests that note. If `ReportItemOut` gains an `everywhere` flag, test that instead.

**Stop order.** The API does not yet say where along the route each condition lies. Stops therefore follow the report's group order (indoor, sidewalks and paths, roads and crossings, weather and ice), and within each group the report's own order. When `GET /api/route` adds a position, sort by it.

**City map markers.** Markers are pointer shortcuts only. The list beside the map is the keyboard and screen-reader path and selects the same items, so markers are hidden from assistive technology and stay out of the tab order.

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

