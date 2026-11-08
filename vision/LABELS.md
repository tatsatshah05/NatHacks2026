# Label Guide

How to label road camera images for the OpenPath road-condition model (PLAN.md section 7). Read the whole guide once before you start, and keep the decision order at hand while you label. Every labeller follows the same rules, so the model learns one consistent idea of each class.

No example images are committed (repository rule: never commit camera frames), so each class below is described in words.

## Running the tool

```
python -m vision.tools.label --images vision/data/raw --out vision/data/labels.csv --labeller <name>
```

- `--images`: a folder of `.jpg` or `.png` files (subfolders are included). Name downloaded frames `<camera_id>_<YYYYMMDDTHHMMSSZ>.jpg` (for example `yeg_whitemud_gateway_20261107T150211Z.jpg`) so the tool records the camera and the capture time. Any other name is recorded with camera `unknown` and the file's modification time.
- `--out`: the CSV to create or continue. Images already in it are skipped, so you can stop and resume at any time. Progress is saved after every key press.
- `--labeller`: your name, written into every row.
- `vision/data/` is git-ignored. Never commit images or the CSV with images in it.
- The window needs the GUI build of OpenCV. `vision/requirements.txt` installs the headless build for the Render worker, so on your laptop run `pip uninstall opencv-python-headless` and `pip install "opencv-python>=4.9,<5"` once. If the tool prints that OpenCV cannot open a window, this is why.
- Use one CSV per labeller (`labels_<name>.csv`). The tool skips every image already in a CSV, so two labellers who label the same images for the agreement check (OP-201) each need their own file.

### Keys

| Key | Action |
| --- | --- |
| `1` | `dry` |
| `2` | `wet` |
| `3` | `snow_partial` |
| `4` | `snow_covered` |
| `5` | `ice` |
| `6` | `unusable` |
| `d` or `x` | Delete the image file. Use it whenever a person could be identified. Nothing is recorded for a deleted image. |
| `s` | Skip. Nothing is written; the image comes back in your next session. |
| `b` | Back to the previous image. Press a number to replace its label, or `s` to keep it. |
| `q` | Quit. Everything is already saved. |

### The CSV

One row per labelled image, with the columns `image, camera_id, captured_at, label, labeller, labelled_at`. `image` is the file path relative to the `--images` folder. Times are UTC in ISO 8601 (`2026-11-07T15:02:11Z`). Each image appears at most once; correcting a label with `b` replaces the row.

## Before you start

- **People come first.** If any person in the frame could be identified (a face, a recognisable figure near the camera, a child, a person using a mobility aid), press `d` to delete the file. Tiny distant figures with no identifying features may stay, but when in doubt, delete. Delete frames with a readable licence plate too. This rule applies before any label, and it applies to every class.
- **Label the paved surface the camera watches,** meaning the road, the crossing and the curb lanes, out to the point where the picture becomes too small to judge. Ignore the sky, the boulevard grass, roofs and parked cars except as clues.
- **Label what the pixels show,** not what the date, the camera's city or the weather forecast suggests. A camera in a snowstorm can show a bare, wet road.
- **Judge the picture at the size the tool shows it.** Do not open the file elsewhere to zoom in; the model sees the same picture you do.
- **Do not spend long on one image.** If you cannot decide within a few seconds, use the decision order below. Consistency matters more than any single hard case.

## Decision order

Work through these questions in order and stop at the first answer.

1. **Can I see the road surface at all?** If not (dark, black, offline, fogged, blinded by glare, lens covered, out of focus, pointed away), press `6` `unusable`. Do not guess.
2. **Is there snow on the pavement?** Look for white or grey-white cover, wheel tracks through white, snow between lanes, windrows at the curb, snow on the crosswalk. Fully covered with no bare pavement through the main view: `4` `snow_covered`. Some snow on the pavement but bare pavement visible as well: `3` `snow_partial`. Snow only on grass, roofs or cars, with the pavement bare: not snow, continue.
3. **Is there ice or frost?** A glassy or polished look with frost at the edges, white grainy frost over the surface, frozen ruts or a frozen sheen in the wheel tracks, with frost on nearby grass, roofs or the camera housing: `5` `ice`. If you are not confident, continue; the app combines `wet` with freezing weather to produce an ice warning on its own, so an uncertain glossy surface is labelled `wet`.
4. **Wet or dry?** Dark, saturated pavement with reflections of lights, sky or vehicles, spray behind tyres, puddles: `2` `wet`. Lighter grey, matte pavement with no reflections: `1` `dry`.

Tie-breakers: unsure whether you can see enough, `unusable`; unsure between `snow_partial` and `snow_covered`, pick `snow_partial` if any bare pavement shows; unsure between `ice` and `wet`, pick `wet`; unsure between `wet` and `dry`, look for reflections and pick `dry` if there are none.

## Classes

### `dry` (key 1)

The pavement is bare and matte, light to mid grey, with lane markings clearly visible and no reflections of lights, sky or vehicles. Shadows on a dry road are uniform and soft, not mirror-like. Dust, salt stains and old patches of dried brine are still `dry`. Use `dry` for a bare road at night too, as long as the street lights show the surface and there are no light streaks on it.

### `wet` (key 2)

Water on a bare pavement: the surface looks darker and saturated, and it reflects headlights, street lights, the sky or vehicles. At night, wet roads show long bright streaks under each light; by day, look for sheen, puddles and spray behind tyres. A road that is wet from melting snow is still `wet` as long as the pavement itself is bare. Choose `wet` over `ice` whenever you cannot see clear signs of freezing.

### `snow_partial` (key 3)

Snow covers part of the paved surface and bare pavement shows elsewhere. Typical cases are wheel tracks worn through fresh snow, plowed driving lanes with snow between lanes or in the crosswalk, slush at the curb, and windrows left by the plow. The bare parts may be wet or dry; the snow on the pavement is what counts. If the only snow is off the pavement, the image is not `snow_partial`.

### `snow_covered` (key 4)

Snow covers the whole paved surface in the main view, so no bare pavement and no lane markings show through. The snow may be fresh and white, packed and grey, or rutted, and the shape of the road may be visible only from curbs, poles and vehicles. A few dark spots that are small compared with the road do not change the label. If a clear bare lane runs through the cover, use `snow_partial` instead.

### `ice` (key 5)

A frozen layer on bare pavement: a glassy or polished sheen that does not look like liquid water, a white or grey-white grainy coating that is thinner and duller than snow (frost), frozen ruts, or a sheen in the wheel tracks while the surroundings show frost on grass, roofs, signs or the camera housing. Label `ice` only when you are confident. A dark glossy road with no other sign of freezing is `wet`, and the app handles the ice warning from the weather. Ice with snow on top of it is a snow class, not `ice`.

### `unusable` (key 6)

The picture does not show the road surface well enough to label: a black or offline frame, a "no image" placeholder or test pattern, deep darkness between point lights, thick fog or heavy snowfall hiding the surface, water drops, snow or ice on the lens, a washed-out overexposed frame, strong blur, or a camera pointed at the sky or a wall. The worker turns `unusable` into `unknown` in the app, never into `dry`, so it is always the safe choice when nothing can be seen.

## Edge cases

**Night.** Night is not automatically `unusable`. If street lights show the surface, label it: a dry road is matte with no light streaks, a wet road shows long reflections under each light, snow shows up as bright and even. If you see only points of light and darkness, or the camera has switched to a noisy low-light mode in which texture is lost, use `unusable`.

**Glare and low sun.** If part of the frame is blinded but the surface near the camera is still visible, label the visible surface. If glare washes out the whole road, use `unusable`. Bright glare on a wet or icy surface is not a snow class; snow is white and textured, glare is a smooth bloom.

**Wet-looking frost.** Frost can make a road look damp. Check the surroundings: white grass, white roofs, rime on signs, fences or the camera housing, and a dull grainy look to the sheen point to `ice`. A clean dark gloss with no frost anywhere is `wet`.

**Partially cleared lanes.** Plowed driving lanes that are bare while snow remains on the pavement elsewhere (between lanes, in the crosswalk, along the curb) are `snow_partial`. If every paved part is bare and wet from melt and the snow sits only on the boulevard, it is `wet`.

**Fog, mist and heavy snowfall.** Light haze with the surface still visible: label the surface. Fog or snowfall thick enough that you cannot tell snow from bare pavement: `unusable`. Falling snow on its own does not make an image a snow class; the pavement does.

**Camera offline or black frame.** Black frames, grey frames, "no signal" or "camera unavailable" text, frozen test patterns, and frames that are only a logo are `unusable`. A dark night frame that still shows a lit road is not offline; see Night.

**Overexposed.** A frame that is washed out, with the surface texture gone and lane markings invisible, is `unusable`. If only the sky or distant parts are blown out and the near road is readable, label the near road.

**Dirty or wet lens.** Water drops, salt film, snow or ice on the lens that blur or hide the road make the frame `unusable`. A few small drops at the edge with a clear view of the road are fine.

**Vehicles and plows.** A plow or truck passing through does not change the label of the surface you can still see. If a large vehicle fills the view so little pavement is visible, use `unusable` rather than guess.

**Shadows and dark pavement.** Fresh asphalt and shaded areas are dark but matte. Look for reflections before choosing `wet`.

**People.** Delete, do not label. See Before you start.

## Consistency checks

- Label in sessions of about 100 images, then take a short break. Tired labelling drifts toward `dry`.
- If you find yourself giving one class to almost everything from one camera, look at a few frames again against the class descriptions.
- When a rule in this guide is unclear or two labellers disagree on a kind of image, write the case down and raise it with the vision lead; the guide is updated and the disputed images are relabelled. Do not keep a private rule.
- Ten percent of the images are labelled twice, blind and under two different labeller names, to measure agreement (OP-201). For natHACKS 2026 both passes are AI-assisted (`Claude (AI-assisted)` first, `Claude second pass (blind)` second; issue #132), so the result is reported as AI-to-AI agreement, not human agreement. The result goes in `vision/MODEL_CARD.md`.
