# OpenPath design: the wayfinding line

Every Phase 3 screen is built from this page. The code is in `web/src/app/globals.css` (tokens and styles) and `web/src/components/ui/` (components). If a screen needs something this page does not cover, add it here in the same pull request.

## 1. Design plan

**Subject, audience, job.** OpenPath plans accessible walking routes across Edmonton. People open it on a phone, outdoors, often in winter: gloves, glare, cold hands and little patience. Many of them use a wheelchair, a walker or cane, or have low vision. The screen has one job: show the route and every known barrier on it, quickly and without doubt.

**Direction.** The route is the interface. After "Find routes", each route is drawn like a transit line diagram. A vertical line in the action colour runs from the start (a filled circle) to the end (a filled circle). Every known condition is a stop on that line, drawn as a distinct shape with a text label. Conditions the route avoids sit beside the line, where it kinks away from them. The map only illustrates the line and is never the only source of information. Everything around the line is quiet. The boldness is spent in one place.

**Colour.** Five base values: Snow `#F5F8FA` (page), Card `#FFFFFF` (panels), Ink `#0D1F2D` (text and rules), Muted `#4A5B6A` (secondary text and control borders) and Action `#1450A3` (buttons, links and the route line). Hazard colours appear only on hazard shapes and their labels.

**Type.** One family: Atkinson Hyperlegible 400 and 700, drawn by the Braille Institute for low-vision readers. Its slashed zero and distinct letterforms are the app's only typographic voice.

**Layout.** Mobile first: one left-aligned column with 16 px gutters. From 900 px wide, the plan and the route line sit on the left (at most 44ch) and the map sits on the right, sticky and visually secondary. Nothing is centred.

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
| map (after results)    |         +----------------------+-------------------------+
+------------------------+
```

**Principles.**

1. The line is the hero. If a screen has a route, the route line is the most visible thing on it.
2. Shape before colour. Every condition has a shape, a fill pattern and words, so it reads in greyscale, in glare and in a screen reader.
3. Quiet chrome. Panels are flat cards with one ink rule on top. No shadows, gradients, badges or decoration.
4. One reveal. The line draws itself once when routes arrive (600 ms, top to bottom). Nothing else moves.
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
| `--chosen` / `--on-chosen` | Chosen segment | ink / snow | 15.7 | action / ink | 8.3 |
| `--blocked` | Blocked shape and label | `#B3261E` | 6.1 / 6.5 | `#FF9A8F` | 8.2 / 7.3 |
| `--ice` | Ice-risk shape and label | `#0B6F80` | 5.5 / 5.8 | `#5FD4E8` | 9.7 / 8.6 |
| `--snow-hazard` | Snow shape and label | `#5B4FCF` | 5.7 / 6.1 | `#C2B8FF` | 9.3 / 8.2 |
| `--caution` | Caution shape and label | `#8A5300` | 5.9 / 6.3 | `#F4B860` | 9.5 / 8.4 |
| `--clear` | All-clear shape and label | `#1E6B3A` | 6.1 / 6.5 | `#7FD39A` | 9.3 / 8.3 |

- **Theme switching.** Dark follows `prefers-color-scheme`. It is guarded by `:root:not([data-theme="light"])` and repeated under `:root[data-theme="dark"]`, so OP-307 can force either theme by setting `data-theme` on `<html>`.
- **Focus ring.** The focus ring is 3 px with a 2 px card-coloured gap, so it always sits next to card or snow, never next to the control's own fill. In light mode the focus colour on action is only 2.4:1, which is why the gap exists.
- **Line colour.** `--line` is 1.6:1, so it is never a control border. Controls use `--muted`.
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
| `SiteHeader` | Every page | Skip link, wordmark, Plan / City / Alerts / About with `aria-current`, `ModeBadge`. Pass `current` for the active section. |
| `ModeBadge` | Inside `SiteHeader` (automatic) | Reads `GET /health`. Shows "Replay" in a bordered pill when `mode` is `replay`; nothing when live or unreachable. |
| `Button`, `ButtonLink` | Every action | `primary` (filled action) for the one main action on a screen; `secondary` (ink outline) for the rest; `text` for quiet inline actions. 44 px minimum height, `size="large"` gives 56 px. External hrefs get `rel="noreferrer"`. |
| `Field` | Every text input | Label above. Help and error are linked with `aria-describedby`. The error states what went wrong and what to do. |
| `SegmentedChoice` | One choice from 2 to 4 options | Native radios, so arrow keys move and the group is one tab stop. `layout="grid"` for short labels (profiles), `layout="stack"` for options with a description (route choice). |
| `Panel` | Grouping a section on a surface | Card with a 2 px ink top rule, 6 px radius, no shadow. Do not nest panels. |
| `StatusLine` | Results counts, loading, confirmations | `role="status"`. Keep it in the DOM even when empty so changes are announced. |
| `TimeAgo` | Any time a reading was taken | "Checked 4 min ago" for source readings, "Updated just now" for the page. The exact time is in the `title` and `datetime`. |
| `HazardMark` | Any condition in a list, legend or report | Shape plus visible label. `HazardShapeIcon` is the shape alone, only when the words sit right next to it. |
| `RouteLine` | Any screen that shows a route | Heading sentence, reason, then an ordered list: start, each condition, end. `reveal` draws it once; `compact` drops detail and check times when the full report follows. |

**Stop order.** The API does not yet say where along the route each condition lies. Stops therefore follow the report's group order (indoor, sidewalks and paths, roads and crossings, weather and ice), and within each group the report's own order. When `GET /api/route` adds a position, sort by it.

**City map markers.** Markers are pointer shortcuts only. The list beside the map is the keyboard and screen-reader path and selects the same items, so markers are hidden from assistive technology and stay out of the tab order.

## 6. Hazard shapes

The effect sets the shape. The kind supplies the words ("Elevator out"). Each shape differs in both outline and fill pattern, and stays distinct at 16 px.

| Meaning | Shape | Fill | Colour token | Label |
| --- | --- | --- | --- | --- |
| Start, end | Circle | Solid | `--ink` | "Start", "End" and the place name |
| `blocked` | Square | Diagonal stripes | `--blocked` | "Blocked" |
| `ice_risk` | Diamond | Dots | `--ice` | "Ice risk" (never "black ice detected") |
| `snow` | Hexagon | Dashes | `--snow-hazard` | "Snow" |
| `caution` | Triangle | Solid | `--caution` | "Caution" |
| Clear | Circle with a check | Outline | `--clear` | "No known obstructions" |

On the route line, an avoided condition is offset to the right, and the line kinks away from it. It is labelled "Avoided".

## 7. Copy

- **Buttons.** Say what happens: "Find routes", "View route report", "Open in Google Maps", "Save this route", "Refresh conditions".
- **Errors.** Say what went wrong and what to do: "Choose a starting place first."
- **Empty states.** Invite an action: "No current obstructions are reported. Plan a route to see conditions along it."
- **Data honesty.** Label demo and replayed data on the screen itself, in words.
- **Time.** Use "Checked" for when we last confirmed a reading, not "Updated".
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
