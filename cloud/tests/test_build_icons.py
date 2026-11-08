"""OP-306: scripts/build_icons.py and the app icons and manifest it serves."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

pytest.importorskip("PIL")
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "web" / "public"


def load_icons() -> ModuleType:
    """Import scripts/build_icons.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "openpath_build_icons", ROOT / "scripts" / "build_icons.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


icons = load_icons()


def test_glyph_fits_the_maskable_safe_zone_and_every_tile() -> None:
    assert icons.glyph_reach(icons.SCALE_MASKABLE) <= icons.SAFE_RADIUS
    for scale in (icons.SCALE_ANY, icons.SCALE_APPLE, icons.SCALE_BADGE):
        assert icons.glyph_reach(scale) < icons.BOX / 2


def test_committed_svg_matches_the_script() -> None:
    assert (PUBLIC / "icons" / "icon.svg").read_text(encoding="utf-8") == icons.svg()


def test_manifest_icons_exist_with_their_declared_sizes() -> None:
    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text("utf-8"))
    assert manifest["name"] == "OpenPath"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    purposes = set()
    for icon in manifest["icons"]:
        path = PUBLIC / icon["src"].lstrip("/")
        assert path.is_file(), icon["src"]
        purposes.add(icon["purpose"])
        if icon["type"] == "image/png":
            width, height = (int(n) for n in icon["sizes"].split("x"))
            with Image.open(path) as image:
                assert image.size == (width, height)
    assert {"any", "maskable"} <= purposes


def test_apple_and_maskable_icons_are_opaque_and_the_badge_is_not() -> None:
    with Image.open(PUBLIC / "apple-touch-icon.png") as apple:
        assert apple.size == (180, 180)
        assert apple.mode == "RGB"  # iOS fills transparency with black
    with Image.open(PUBLIC / "icons" / "icon-maskable-512.png") as maskable:
        assert maskable.convert("RGBA").getpixel((0, 0))[3] == 255
    with Image.open(PUBLIC / "icons" / "icon-512.png") as rounded:
        assert rounded.convert("RGBA").getpixel((0, 0))[3] == 0  # rounded corner
    with Image.open(PUBLIC / "icons" / "badge-96.png") as badge:
        assert badge.convert("RGBA").getpixel((0, 0))[3] == 0


def test_png_draws_the_glyph_in_white_on_action_blue() -> None:
    image = icons.png(64, scale=1.0, background=icons.ACTION, rounded=False)
    colours = {colour for _, colour in image.getcolors(64 * 64)}
    assert (255, 255, 255) in colours
    assert (0x14, 0x50, 0xA3) in colours
