"use client";

// Which palette and style a map draws with (OP-410, DESIGN.md section 10). The liberty
// basemap and the high-contrast light style are light in both page themes; only the
// high-contrast dark style is dark. The map components put `surfaceClass` on the
// element that holds the canvas, so the markers, tags and controls inside it take the
// map's tokens (globals.css `.map-light` / `.map-dark`), draw their own lines with
// `lines`, and call `map.setStyle(style)` whenever `styleKey` changes.

import { type MapPalette, type MapStyle, mapPalette, mapStyleFor } from "./map-style";
import { type Settings, useDarkTheme, useSettings } from "./settings";

export type LinePalette = {
  /** The selected route, and the swatch beside its words. */
  route: string;
  /** Under the route, 2 px wider on each side, so it stays off the roads. */
  casing: string;
  /** The other routes. */
  others: string;
  /** The land colour, for the swatches in the key. */
  land: string;
};

// The liberty basemap: the light theme's action (#1450A3, 7.8:1 on white and about
// 7:1 on the land) and muted (#4A5B6A, 7.0:1 on white), with a white casing.
const LIBERTY: Readonly<LinePalette> = { route: "#1450a3", casing: "#ffffff", others: "#4a5b6a", land: "#f1efe9" };

export type MapSurface = {
  /** True when the map draws the dark high-contrast style. */
  dark: boolean;
  /** `map-light` or `map-dark`, for the element that holds the canvas. */
  surfaceClass: "map-light" | "map-dark";
  lines: Readonly<LinePalette>;
  /** The high-contrast palette when that setting is on, else null. */
  palette: Readonly<MapPalette> | null;
  /** The MapLibre style: the liberty URL, or the high-contrast style document. */
  style: string | MapStyle;
  /** Changes exactly when `style` would draw differently. */
  styleKey: string;
};

export function mapSurfaceFor(settings: Pick<Settings, "highContrastMap" | "textSize">, pageDark: boolean): MapSurface {
  const style = mapStyleFor(settings, pageDark);
  if (!settings.highContrastMap) {
    return { dark: false, surfaceClass: "map-light", lines: LIBERTY, palette: null, style, styleKey: "liberty" };
  }
  const palette = mapPalette(pageDark);
  return {
    dark: pageDark,
    surfaceClass: pageDark ? "map-dark" : "map-light",
    lines: { route: palette.route, casing: palette.routeCasing, others: palette.rail, land: palette.land },
    palette,
    style,
    styleKey: `high-contrast:${pageDark ? "dark" : "light"}:${settings.textSize}`
  };
}

/** The surface for the current settings and theme, as live React state. */
export function useMapSurface(): MapSurface {
  const settings = useSettings();
  const dark = useDarkTheme();
  return mapSurfaceFor(settings, dark);
}
