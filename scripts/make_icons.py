"""Draw every OpenPath icon (OP-411): the launch assets in one command.

    python scripts/make_icons.py

Runs ``scripts/build_icons.py`` (OP-306) for the manifest icons, the notification
badge and ``web/public/apple-touch-icon.png`` (the path iOS tries when a page has
no link), then writes the icons Next links from every page by file convention, in
``web/src/app`` (an ``icons`` field in the layout's metadata would replace them):

- ``icon.svg``: the browser favicon, the same mark from the same coordinates, with a
  light and a dark variant. A ``prefers-color-scheme`` rule inside the SVG swaps the
  tile and the glyph between each theme's design tokens (action and on-action), so
  the mark keeps its contrast on a dark browser tab bar as well as a light one.
  Launcher icons cannot carry that rule, so ``web/public/icons/icon.svg`` stays
  single-coloured.
- ``icon1.png``: a 64 px PNG for browsers that ignore SVG favicons (Safari).
- ``apple-icon.png``: the 180 px, opaque Apple touch icon.

Everything is original artwork drawn from coordinates; no image is read or stored.
Pillow comes from ``vision/requirements.txt``. Commit the files it writes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "src" / "app"
FAVICON = APP / "icon.svg"
FAVICON_PNG = APP / "icon1.png"
APPLE_ICON = APP / "apple-icon.png"

# docs/design/DESIGN.md section 3: --action and --on-action in each theme.
LIGHT_TILE = "#1450A3"  # light --action; the glyph is white on it (7.8:1)
LIGHT_MARK = "#FFFFFF"
DARK_TILE = "#8DB8FF"  # dark --action; ink on it (8.3:1)
DARK_MARK = "#0D1F2D"


def load_build_icons() -> ModuleType:
    """Import scripts/build_icons.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "openpath_build_icons", ROOT / "scripts" / "build_icons.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("scripts/build_icons.py not found")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def favicon_svg(icons: ModuleType) -> str:
    """The favicon: build_icons' geometry with a light and a dark variant."""
    points = [
        icons._scaled(p, icons.SCALE_ANY)
        for p in (icons.START, icons.JOG_FROM, icons.JOG_TO, icons.END)
    ]
    path = " ".join(
        f"{'M' if index == 0 else 'L'}{x:g} {y:g}"
        for index, (x, y) in enumerate(points)
    )
    (sx, sy), (ex, ey) = points[0], points[-1]
    radius = icons.RADIUS * icons.SCALE_ANY
    stroke = icons.STROKE * icons.SCALE_ANY
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">\n'
        "<title>OpenPath</title>\n"
        "<style>\n"
        f".tile{{fill:{LIGHT_TILE}}}.mark{{fill:{LIGHT_MARK}}}"
        f".path{{stroke:{LIGHT_MARK}}}\n"
        "@media (prefers-color-scheme: dark){"
        f".tile{{fill:{DARK_TILE}}}.mark{{fill:{DARK_MARK}}}"
        f".path{{stroke:{DARK_MARK}}}}}\n"
        "</style>\n"
        f'<rect class="tile" width="512" height="512" rx="{icons.TILE_RADIUS:g}"/>\n'
        f'<path class="path" d="{path}" fill="none" stroke-width="{stroke:g}"'
        ' stroke-linejoin="round" stroke-linecap="round"/>\n'
        f'<circle class="mark" cx="{sx:g}" cy="{sy:g}" r="{radius:g}"/>\n'
        f'<circle class="mark" cx="{ex:g}" cy="{ey:g}" r="{radius:g}"/>\n'
        "</svg>\n"
    )


def main() -> int:
    icons = load_build_icons()
    status = icons.main()
    if status:
        return status
    APP.mkdir(parents=True, exist_ok=True)
    FAVICON.write_text(favicon_svg(icons), encoding="utf-8")
    print(f"wrote {FAVICON.relative_to(ROOT)} (light and dark variants)")
    outputs = {
        FAVICON_PNG: icons.png(
            64, scale=icons.SCALE_ANY, background=icons.ACTION, rounded=True
        ),
        APPLE_ICON: icons.png(
            180, scale=icons.SCALE_APPLE, background=icons.ACTION, rounded=False
        ),
    }
    for path, image in outputs.items():
        image.save(path, optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({image.width} x {image.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
