import { effectLabel } from "@/lib/report-format";

/**
 * The shape vocabulary, shared by the React HazardShape and the string builder the
 * MapLibre markers use. Every shape differs in outline and in fill pattern, so it
 * reads without colour; the text label beside it carries the words.
 *
 *   start    filled circle, ink
 *   blocked  square, diagonal stripes, --blocked
 *   ice      diamond, dots, --ice
 *   snow     hexagon, dashes, --snow-hazard
 *   caution  triangle, solid, --caution
 *   clear    circle with a check, --clear
 */
export type HazardShape = "start" | "blocked" | "ice" | "snow" | "caution" | "clear";

export const HAZARD_SHAPES: readonly HazardShape[] = ["start", "blocked", "ice", "snow", "caution", "clear"];

type ShapeSpec = {
  /** A CSS colour, applied as `color`; the SVG draws in currentColor. */
  colour: string;
  /** The outline, in a 24 x 24 box. */
  outline: { tag: "circle" | "rect" | "polygon"; attrs: Record<string, string> };
  /** One tile of the fill pattern, or null for a solid or empty fill. */
  pattern: { width: number; height: number; rotate: number; tile: string } | null;
  fill: "pattern" | "solid" | "none";
  /** Extra marks drawn over the outline. */
  extra: string;
};

export const SHAPE_SPECS: Record<HazardShape, ShapeSpec> = {
  start: {
    colour: "var(--ink)",
    outline: { tag: "circle", attrs: { cx: "12", cy: "12", r: "9" } },
    pattern: null,
    fill: "solid",
    extra: ""
  },
  blocked: {
    colour: "var(--blocked)",
    outline: { tag: "rect", attrs: { x: "3", y: "3", width: "18", height: "18" } },
    pattern: { width: 4, height: 4, rotate: 45, tile: '<rect width="2" height="4" fill="currentColor"/>' },
    fill: "pattern",
    extra: ""
  },
  ice: {
    colour: "var(--ice)",
    outline: { tag: "polygon", attrs: { points: "12,2 22,12 12,22 2,12" } },
    pattern: { width: 4, height: 4, rotate: 0, tile: '<circle cx="2" cy="2" r="1.1" fill="currentColor"/>' },
    fill: "pattern",
    extra: ""
  },
  snow: {
    colour: "var(--snow-hazard)",
    outline: { tag: "polygon", attrs: { points: "12,2 20.7,7 20.7,17 12,22 3.3,17 3.3,7" } },
    pattern: {
      width: 6,
      height: 4,
      rotate: 0,
      tile: '<rect x="0" y="1.25" width="3.5" height="1.5" fill="currentColor"/>'
    },
    fill: "pattern",
    extra: ""
  },
  caution: {
    colour: "var(--caution)",
    outline: { tag: "polygon", attrs: { points: "12,3 22,21 2,21" } },
    pattern: null,
    fill: "solid",
    extra: ""
  },
  clear: {
    colour: "var(--clear)",
    outline: { tag: "circle", attrs: { cx: "12", cy: "12", r: "9" } },
    pattern: null,
    fill: "none",
    extra:
      '<path d="M7.5 12.5l3 3 6-6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
  }
};

/** The effect of an obstruction decides its shape; the kind decides its words. */
export function effectShape(effect: string): HazardShape {
  switch (effect) {
    case "blocked":
      return "blocked";
    case "ice_risk":
      return "ice";
    case "snow":
      return "snow";
    default:
      return "caution";
  }
}

/** The effect in plain words; one mapping for the whole app (lib/report-format.ts). */
export function effectText(effect: string): string {
  return effectLabel(effect);
}

/** The words for a shape, for legends. */
export const SHAPE_TEXT: Record<Exclude<HazardShape, "start">, string> = {
  blocked: effectLabel("blocked"),
  ice: effectLabel("ice_risk"),
  snow: effectLabel("snow"),
  caution: effectLabel("caution"),
  clear: "No known obstructions"
};

/** The shape as an SVG string, for DOM built outside React (the map markers). */
export function hazardShapeSvg(shape: HazardShape, size: number, id: string): string {
  const spec = SHAPE_SPECS[shape];
  const patternId = `${id}-${shape}`;
  const attrs = Object.entries(spec.outline.attrs)
    .map(([key, value]) => `${key}="${value}"`)
    .join(" ");
  const fill = spec.fill === "pattern" ? `url(#${patternId})` : spec.fill === "solid" ? "currentColor" : "none";
  const defs = spec.pattern
    ? `<defs><pattern id="${patternId}" patternUnits="userSpaceOnUse" width="${spec.pattern.width}" height="${spec.pattern.height}" patternTransform="rotate(${spec.pattern.rotate})">${spec.pattern.tile}</pattern></defs>`
    : "";
  return (
    `<svg class="hazard-shape" width="${size}" height="${size}" viewBox="0 0 24 24" style="color:${spec.colour}" aria-hidden="true" focusable="false">` +
    defs +
    `<${spec.outline.tag} ${attrs} fill="${fill}" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>` +
    spec.extra +
    `</svg>`
  );
}
