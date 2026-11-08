// Map styles for the planner and city maps (OP-307).
//
//   new maplibregl.Map({ style: mapStyleFor(settings, prefersDarkTheme(settings)), ... })
//   map.setStyle(mapStyleFor(next, dark));   // when the setting or theme changes
//
// Normally this is OpenFreeMap's "liberty" style. With the high-contrast map setting
// on, it is a style built here on the same OpenFreeMap vector tiles (OpenMapTiles
// schema): near-black roads and paths on white, or white on near-black in dark mode;
// water in one mid blue that is at least 4:1 from both the land and the roads; labels
// with a solid halo; nothing else (no parks, buildings, icons or boundaries).
//
// Contrast, WCAG relative luminance, measured with a script (also in
// docs/design/DESIGN.md, section 3):
//
//   Light: roads and paths #0D1F2D on land #FFFFFF        16.8:1
//          water #5A7FA6 on land #FFFFFF                    4.2:1
//          roads and paths #0D1F2D on water #5A7FA6         4.0:1 (bridges)
//          rail #4A5B6A on land #FFFFFF                     7.0:1
//          label text #0D1F2D on its halo #FFFFFF          16.8:1
//   Dark:  roads and paths #FFFFFF on land #0D1F2D        16.8:1
//          water #5A7FA6 on land #0D1F2D                    4.0:1
//          roads and paths #FFFFFF on water #5A7FA6         4.2:1 (bridges)
//          rail #A9B8C6 on land #0D1F2D                     8.3:1
//          label text #FFFFFF on its halo #0D1F2D          16.8:1
//
// Roads, paths and the land are non-text graphics, so 1.4.11 asks for 3:1; labels
// always sit on their halo, so 1.4.3 asks for 4.5:1 against it. Paths are dashed and
// roads solid, so the two differ by shape as well as width.

import type { MapOptions } from "maplibre-gl";
import { TEXT_SCALE, type Settings } from "./settings";

/** A MapLibre style document (the `style` option without its URL form). */
export type MapStyle = Exclude<MapOptions["style"], string | undefined>;

/** OpenFreeMap's default style, used whenever high contrast is off. */
export const LIBERTY_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

const TILES_URL = "https://tiles.openfreemap.org/planet"; // TileJSON; carries the attribution
const GLYPHS_URL = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const REGULAR = ["Noto Sans Regular"];
const BOLD = ["Noto Sans Bold"];

/** The colours of the high-contrast style, for layers a page adds on top (the route). */
export type MapPalette = {
  land: string;
  ink: string;
  water: string;
  rail: string;
  /** The route line: the action token of the same theme. */
  route: string;
  /**
   * Draw the route over a casing of this colour, 2 px wider on each side. On its own the
   * route is only 2.2:1 against the roads under it in light mode (#1450A3 on #0D1F2D)
   * and 2.0:1 in dark mode (#8DB8FF on #FFFFFF); the land-coloured casing keeps it at
   * 7.8:1 (light) and 8.3:1 (dark).
   */
  routeCasing: string;
};

export const HIGH_CONTRAST_LIGHT: Readonly<MapPalette> = {
  land: "#ffffff",
  ink: "#0d1f2d",
  water: "#5a7fa6",
  rail: "#4a5b6a",
  route: "#1450a3", // 7.8:1 on land
  routeCasing: "#ffffff"
};

export const HIGH_CONTRAST_DARK: Readonly<MapPalette> = {
  land: "#0d1f2d",
  ink: "#ffffff",
  water: "#5a7fa6",
  rail: "#a9b8c6",
  route: "#8db8ff", // 8.3:1 on land
  routeCasing: "#0d1f2d"
};

/** The high-contrast palette for a theme. */
export function mapPalette(dark: boolean): Readonly<MapPalette> {
  return dark ? HIGH_CONTRAST_DARK : HIGH_CONTRAST_LIGHT;
}

type Settable = Pick<Settings, "highContrastMap" | "textSize">;

/**
 * The style for a map: the liberty URL normally, or the high-contrast style document.
 * `dark` is whether the page is dark now (`prefersDarkTheme(settings)` or
 * `useDarkTheme()` from lib/settings.ts); the liberty style has no dark variant.
 */
export function mapStyleFor(settings: Settable, dark = false): string | MapStyle {
  if (!settings.highContrastMap) return LIBERTY_STYLE_URL;
  return highContrastStyle(mapPalette(dark), TEXT_SCALE[settings.textSize]);
}

// Line widths by zoom, in px: [zoom, width] pairs for an exponential interpolation.
function width(...stops: number[]): unknown[] {
  return ["interpolate", ["exponential", 1.4], ["zoom"], ...stops];
}

function textSize(scale: number, ...stops: number[]): unknown[] {
  const scaled = stops.map((value, index) => (index % 2 ? Math.round(value * scale * 10) / 10 : value));
  return ["interpolate", ["linear"], ["zoom"], ...scaled];
}

const NAME = ["coalesce", ["get", "name_en"], ["get", "name:latin"], ["get", "name"]];
const NOT_TUNNEL = ["!=", ["get", "brunnel"], "tunnel"];
const isLine = ["match", ["geometry-type"], ["LineString", "MultiLineString"], true, false];

function classes(...names: string[]): unknown[] {
  return ["match", ["get", "class"], names, true, false];
}

/** The high-contrast style document for one palette and text scale. */
export function highContrastStyle(palette: Readonly<MapPalette>, scale = 1): MapStyle {
  const halo = { "text-color": palette.ink, "text-halo-color": palette.land, "text-halo-width": 2, "text-halo-blur": 0 };
  const layers: unknown[] = [
    { id: "land", type: "background", paint: { "background-color": palette.land } },
    {
      id: "water",
      type: "fill",
      source: "openmaptiles",
      "source-layer": "water",
      filter: NOT_TUNNEL,
      paint: { "fill-color": palette.water }
    },
    {
      id: "waterway",
      type: "line",
      source: "openmaptiles",
      "source-layer": "waterway",
      filter: NOT_TUNNEL,
      paint: { "line-color": palette.water, "line-width": width(10, 1, 18, 6) }
    },
    {
      id: "rail",
      type: "line",
      source: "openmaptiles",
      "source-layer": "transportation",
      minzoom: 11,
      filter: ["all", isLine, NOT_TUNNEL, classes("rail", "transit")],
      paint: { "line-color": palette.rail, "line-width": width(11, 1, 18, 4), "line-dasharray": [3, 1.5] }
    },
    {
      id: "road-minor",
      type: "line",
      source: "openmaptiles",
      "source-layer": "transportation",
      minzoom: 12,
      filter: ["all", isLine, NOT_TUNNEL, classes("minor", "service", "track", "street", "street_limited")],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": palette.ink, "line-width": width(12, 0.75, 14, 1.5, 18, 8) }
    },
    {
      id: "road-major",
      type: "line",
      source: "openmaptiles",
      "source-layer": "transportation",
      minzoom: 6,
      filter: ["all", isLine, NOT_TUNNEL, classes("motorway", "trunk", "primary", "secondary", "tertiary")],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": palette.ink, "line-width": width(8, 1, 12, 2, 14, 3.5, 18, 14) }
    },
    {
      // Sidewalks, footways and trails: dashed, so they read as paths, not roads.
      id: "path",
      type: "line",
      source: "openmaptiles",
      "source-layer": "transportation",
      minzoom: 13,
      filter: ["all", isLine, NOT_TUNNEL, classes("path", "pedestrian")],
      layout: { "line-join": "round" },
      paint: { "line-color": palette.ink, "line-width": width(13, 1, 16, 2, 18, 4), "line-dasharray": [2, 1] }
    },
    {
      id: "water-name",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "water_name",
      minzoom: 12,
      layout: { "text-field": NAME, "text-font": REGULAR, "text-size": textSize(scale, 12, 13, 16, 16) },
      paint: halo
    },
    {
      id: "street-name",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "transportation_name",
      minzoom: 14,
      filter: isLine,
      layout: {
        "symbol-placement": "line",
        "text-field": NAME,
        "text-font": REGULAR,
        "text-size": textSize(scale, 14, 13, 18, 16),
        "text-rotation-alignment": "map"
      },
      paint: halo
    },
    {
      // LRT and train stations by name: the places people orient by.
      id: "station-name",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "poi",
      minzoom: 13,
      filter: classes("rail"),
      layout: { "text-field": NAME, "text-font": BOLD, "text-size": textSize(scale, 13, 13, 18, 16), "text-max-width": 8 },
      paint: halo
    },
    {
      id: "neighbourhood-name",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      minzoom: 11,
      filter: classes("suburb", "neighbourhood", "quarter"),
      layout: { "text-field": NAME, "text-font": BOLD, "text-size": textSize(scale, 11, 13, 16, 16), "text-max-width": 8 },
      paint: halo
    },
    {
      id: "city-name",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      maxzoom: 12,
      filter: classes("city", "town"),
      layout: { "text-field": NAME, "text-font": BOLD, "text-size": textSize(scale, 6, 14, 11, 20) },
      paint: halo
    }
  ];
  return {
    version: 8,
    name: palette === HIGH_CONTRAST_DARK ? "OpenPath high contrast, dark" : "OpenPath high contrast",
    glyphs: GLYPHS_URL,
    sources: { openmaptiles: { type: "vector", url: TILES_URL } },
    layers
  } as MapStyle;
}
