// "What kind of trip?" on the planner (OP-420; lead decision, November 9): Walking,
// Walking and transit (the default, because LRT and bus trips are on in production and
// matter for the demo) or Transit only. The choice is kept on this device under the
// same `openpath.*` key pattern as the accessibility settings (lib/settings.ts). OP-419
// kept its two-state box under the same key as "on" and "off", which still read as
// Walking and transit and Walking. Storage can be missing or throw (private windows,
// blocked site data), so each call is wrapped and the default holds for the page visit.
//
// What GET /api/route is asked: Walking is `mode=walk`; Walking and transit is
// `mode=walk_lrt&transit=always`, so the best bus or LRT trip is shown even when it is
// slower than walking, with a reason that says by how much; Transit only is
// `mode=walk_lrt&transit=only`. The API's own default, `faster`, exists for older
// clients and is never sent.
//
// A trip link decides for itself (lib/route-link.ts): `mode=walk_lrt` alone is Walking
// and transit, `mode=walk_lrt&transit=only` is Transit only, and no mode (or mode=walk)
// is Walking. A link carries `transit` only when the choice is not the default.
import type { RouteMode, RouteTransit } from "./planner-types";

export type TransitChoice = "walk" | "transit" | "only";

export const TRANSIT_STORAGE_KEY = "openpath.transit";
export const TRANSIT_DEFAULT: TransitChoice = "transit";

/** The control's options, in order, each with the one line it shows under its label. */
export const TRANSIT_OPTIONS: ReadonlyArray<{ id: TransitChoice; label: string; description: string }> = [
  { id: "walk", label: "Walking", description: "Walking routes only." },
  {
    id: "transit",
    label: "Walking and transit",
    description: "Walking routes, plus the best bus or LRT trip even when it is slower."
  },
  {
    id: "only",
    label: "Transit only",
    description: "Bus and LRT trips only. When none is found, walking routes are shown."
  }
];

/** The part of a GET /api/route request that the choice decides. */
export type TransitRequest = { mode: RouteMode; transit?: RouteTransit };

export function isTransitChoice(value: unknown): value is TransitChoice {
  return value === "walk" || value === "transit" || value === "only";
}

/** What to ask the API for this choice. */
export function transitRequest(choice: TransitChoice): TransitRequest {
  if (choice === "walk") return { mode: "walk" };
  return { mode: "walk_lrt", transit: choice === "only" ? "only" : "always" };
}

/**
 * The choice behind a request, or behind a trip link's parameters. `transit` counts
 * only with `mode=walk_lrt`, as in the API, and `walk_lrt` alone is Walking and transit.
 */
export function transitChoiceOf(request: { mode?: string | null; transit?: string | null }): TransitChoice {
  if (request.mode !== "walk_lrt") return "walk";
  return request.transit === "only" ? "only" : "transit";
}

/** The `transit` value a trip link carries: "only" for Transit only, nothing otherwise. */
export function transitLinkParam(request: { mode?: string | null; transit?: string | null }): "only" | null {
  return transitChoiceOf(request) === "only" ? "only" : null;
}

/** The choice saved on this device, or null when nothing (readable) is saved. */
export function readTransitChoice(): TransitChoice | null {
  try {
    const value = window.localStorage.getItem(TRANSIT_STORAGE_KEY);
    if (isTransitChoice(value)) return value;
    // OP-419's two-state box: on was Walking and transit, off was Walking.
    return value === "on" ? "transit" : value === "off" ? "walk" : null;
  } catch {
    return null;
  }
}

export function saveTransitChoice(choice: TransitChoice): void {
  try {
    window.localStorage.setItem(TRANSIT_STORAGE_KEY, choice);
  } catch {
    // The choice lasts for this page visit.
  }
}
