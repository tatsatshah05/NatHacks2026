"""Draw OpenPath's app icons (OP-306) and write them to ``web/public``.

    python scripts/build_icons.py

The mark is the wordmark's route glyph turned into a trip: a start circle and an
end circle joined by a line that rises, jogs at 45 degrees the way the route line
kinks around a barrier, and rises again. White on the action blue (#1450A3, 7.8:1
for white), so it reads on light and dark home screens. One geometry feeds both
the SVG (``icons/icon.svg``, the favicon) and the PNGs, which Pillow draws at four
times the size and scales down for smooth edges. Everything here is original
artwork drawn from coordinates; no photo or outside image is used.

Files written (commit them):

- ``icons/icon.svg``: rounded tile, the browser favicon
- ``icons/icon-192.png``, ``icons/icon-512.png``: manifest icons, purpose ``any``
- ``icons/icon-maskable-512.png``: purpose ``maskable``; the glyph stays inside the
  central safe circle (radius 40% of the size) so any launcher mask keeps it whole
- ``icons/badge-96.png``: the white glyph alone on transparency, for the Android
  status bar (the notification ``badge``)
- ``apple-touch-icon.png``: 180 px, full bleed and opaque, as iOS expects
"""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "web" / "public"

ACTION = "#1450A3"  # --action (light), the route line colour
GLYPH = "#FFFFFF"  # --on-action: 7.8:1 on action

# The glyph in a 512 unit box, centred on (256, 256). Diameter to stroke is about
# 3 to 1, as in the wordmark (circles r 4.5, line 3).
BOX = 512
START = (208.0, 400.0)
JOG_FROM = (208.0, 304.0)
JOG_TO = (304.0, 208.0)  # 96 across and 96 up: a 45 degree jog
END = (304.0, 112.0)
RADIUS = 44.0
STROKE = 30.0
TILE_RADIUS = 96.0  # corner radius of the rounded tile, in the 512 box
SUPERSAMPLE = 4
SAFE_RADIUS = 0.4 * BOX  # the maskable safe zone

# How large the glyph is drawn on each kind of icon (1.0 fits the safe zone).
SCALE_ANY = 1.0
SCALE_MASKABLE = 1.0
SCALE_APPLE = 1.0
SCALE_BADGE = 1.25


def _scaled(point: tuple[float, float], scale: float) -> tuple[float, float]:
    centre = BOX / 2
    return (centre + (point[0] - centre) * scale, centre + (point[1] - centre) * scale)


def glyph_reach(scale: float) -> float:
    """How far the glyph reaches from the centre, in the 512 box."""
    reach = 0.0
    for point in (START, END):
        x, y = _scaled(point, scale)
        reach = max(reach, math.hypot(x - BOX / 2, y - BOX / 2) + RADIUS * scale)
    return reach


def svg(scale: float = SCALE_ANY) -> str:
    """The rounded-tile icon as SVG, from the same geometry as the PNGs."""
    points = [_scaled(p, scale) for p in (START, JOG_FROM, JOG_TO, END)]
    path = " ".join(
        f"{'M' if index == 0 else 'L'}{x:g} {y:g}"
        for index, (x, y) in enumerate(points)
    )
    (sx, sy), (ex, ey) = points[0], points[-1]
    r = RADIUS * scale
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        "<title>OpenPath</title>"
        f'<rect width="512" height="512" rx="{TILE_RADIUS:g}" fill="{ACTION}"/>'
        f'<path d="{path}" fill="none" stroke="{GLYPH}" stroke-width="{STROKE * scale:g}"'
        ' stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{sx:g}" cy="{sy:g}" r="{r:g}" fill="{GLYPH}"/>'
        f'<circle cx="{ex:g}" cy="{ey:g}" r="{r:g}" fill="{GLYPH}"/>'
        "</svg>\n"
    )


def png(
    size: int, *, scale: float, background: str | None, rounded: bool
) -> Image.Image:
    """One icon. ``background`` None leaves the glyph on transparency."""
    big = size * SUPERSAMPLE
    unit = big / BOX
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if background is not None:
        if rounded:
            draw.rounded_rectangle(
                (0, 0, big - 1, big - 1), radius=TILE_RADIUS * unit, fill=background
            )
        else:
            draw.rectangle((0, 0, big - 1, big - 1), fill=background)
    points = [
        (x * unit, y * unit)
        for x, y in (_scaled(p, scale) for p in (START, JOG_FROM, JOG_TO, END))
    ]
    half = STROKE * scale * unit / 2
    # Each segment is drawn as its own polygon so diagonal and upright parts keep
    # the same width; a disc at every point rounds the joins as the SVG does.
    for (x1, y1), (x2, y2) in pairwise(points):
        length = math.hypot(x2 - x1, y2 - y1)
        nx, ny = -(y2 - y1) / length * half, (x2 - x1) / length * half
        draw.polygon(
            [
                (x1 + nx, y1 + ny),
                (x2 + nx, y2 + ny),
                (x2 - nx, y2 - ny),
                (x1 - nx, y1 - ny),
            ],
            fill=GLYPH,
        )
    for x, y in points:
        draw.ellipse((x - half, y - half, x + half, y + half), fill=GLYPH)
    r = RADIUS * scale * unit
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=GLYPH)
    small = image.resize((size, size), Image.Resampling.LANCZOS)
    if background is not None and not rounded:
        small = small.convert("RGB")  # opaque: iOS fills transparency with black
    return small


def main() -> int:
    assert glyph_reach(SCALE_MASKABLE) <= SAFE_RADIUS, "glyph leaves the safe zone"
    assert glyph_reach(max(SCALE_ANY, SCALE_APPLE, SCALE_BADGE)) < BOX / 2, (
        "glyph clipped"
    )
    icons = PUBLIC / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    (icons / "icon.svg").write_text(svg(), encoding="utf-8")
    outputs = {
        icons / "icon-192.png": png(
            192, scale=SCALE_ANY, background=ACTION, rounded=True
        ),
        icons / "icon-512.png": png(
            512, scale=SCALE_ANY, background=ACTION, rounded=True
        ),
        icons / "icon-maskable-512.png": png(
            512, scale=SCALE_MASKABLE, background=ACTION, rounded=False
        ),
        icons / "badge-96.png": png(
            96, scale=SCALE_BADGE, background=None, rounded=False
        ),
        PUBLIC / "apple-touch-icon.png": png(
            180, scale=SCALE_APPLE, background=ACTION, rounded=False
        ),
    }
    for path, image in outputs.items():
        image.save(path, optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({image.width} x {image.height})")
    print(f"wrote {(icons / 'icon.svg').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
