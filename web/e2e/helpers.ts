// Shared pieces of the end-to-end tests (OP-406).
//
// Every locator here goes through roles and accessible names, never CSS classes or
// test ids: if a control cannot be found this way, that is an accessibility bug in
// the component, to be fixed there with a proper label. A name built from visible
// text plus a visually hidden span ("Save this route" + ", route 1") is computed as
// "Save this route , route 1", so those patterns allow a space before the comma.
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import type { components } from "../src/lib/api-types";

type RouteResponse = components["schemas"]["RouteResponse"];
export type RouteOut = components["schemas"]["RouteOut"];

/** The API the page under test talks to; the tests call it directly too. */
export const API_URL = (process.env.E2E_API_URL ?? "http://localhost:8740").replace(/\/+$/, "");
/** The key `scripts/e2e.py` starts the API with. Never a production value. */
export const ADMIN_KEY = process.env.E2E_ADMIN_KEY ?? "e2e";

export type Profile = "wheelchair" | "walker_cane" | "low_vision" | "standard";

export type Place = {
  /** The label the place search shows and the planner keeps. */
  name: string;
  /** What to type into the search field to find it. */
  search: string;
  lat: number;
  lng: number;
};

export type Trip = { from: Place; to: Place; profile: Profile; profileLabel: string };

// Real Edmonton places from the prebuilt search index (cloud/data/search.npz), so the
// same trips work in replay and against production. Rogers Place to MacEwan
// University passes a construction closure on 108 Street NW in the recorded
// fixtures, so its report always has at least one item with a source.
export const ROGERS_PLACE: Place = { name: "Rogers Place", search: "rogers place", lat: 53.547016, lng: -113.497787 };
export const MACEWAN: Place = { name: "MacEwan University", search: "macewan", lat: 53.547264, lng: -113.507523 };
export const CHURCHILL_SQUARE: Place = {
  name: "Sir Winston Churchill Square",
  search: "churchill square",
  lat: 53.545536,
  lng: -113.488846
};

export const TRIP: Trip = { from: ROGERS_PLACE, to: MACEWAN, profile: "wheelchair", profileLabel: "Wheelchair" };

/**
 * The seeded live change blocks a path on the route a page follows, so the two
 * projects (which run at the same time) each use their own trip and never block
 * each other's path. Any other project name falls back to the default trip.
 */
export const LIVE_TRIPS: Record<string, Trip> = {
  phone: TRIP,
  laptop: { from: CHURCHILL_SQUARE, to: ROGERS_PLACE, profile: "standard", profileLabel: "Standard walking" }
};

export function liveTrip(projectName: string): Trip {
  return LIVE_TRIPS[projectName] ?? TRIP;
}

export const REPORT_GROUPS = ["Indoor", "Sidewalks and paths", "Roads and crossings", "Weather and ice"] as const;

function latLng(place: Place): string {
  return `${place.lat.toFixed(6)},${place.lng.toFixed(6)}`;
}

/** The planner link that runs the search when it opens (web/src/lib/route-link.ts). */
export function plannerUrl(trip: Trip): string {
  const params = new URLSearchParams({
    from: latLng(trip.from),
    from_name: trip.from.name,
    to: latLng(trip.to),
    to_name: trip.to.name,
    profile: trip.profile
  });
  return `/?${params.toString()}`;
}

/** The route report link for one route of the trip. */
export function reportUrl(trip: Trip, rank = 1): string {
  return `/route${plannerUrl(trip).slice(1)}&route=${rank}`;
}

/**
 * The map's vector tiles come from OpenFreeMap, a third party. The tests are about
 * the app, so the tiles are dropped: no network dependence, and the same page
 * whether or not the tile server answers. The list is the source of truth anyway.
 */
export async function blockMapTiles(page: Page): Promise<void> {
  await page.route(/openfreemap\.org/, (route) => route.abort());
}

/** The recommended route's heading: "12 min, 687 m, step-free". */
export function recommendedRoute(page: Page) {
  return page.getByRole("heading", { level: 2, name: /^\d+ min, \d/ }).first();
}

/** The recommended route's line: start, each condition and end, as a list. */
export function routeLine(page: Page, rank = 1) {
  return page.getByRole("list", { name: new RegExp(`^Route ${rank}: start, `) });
}

/** Wait for routes: the status line says how many, and the first route has an ETA. */
export async function expectRoutes(page: Page, trip: Trip, timeout = 30_000): Promise<void> {
  await expect(page.getByRole("status").filter({ hasText: /\d+ routes? for / })).toBeVisible({ timeout });
  await expect(recommendedRoute(page)).toBeVisible();
  await expect(routeLine(page)).toBeVisible();
  const line = routeLine(page);
  await expect(line.getByRole("listitem").filter({ hasText: trip.from.name })).toContainText("Start");
  await expect(line.getByRole("listitem").filter({ hasText: trip.to.name })).toContainText("End");
}

/** Open the planner with the trip in the URL and wait for its routes. */
export async function openPlannedTrip(page: Page, trip: Trip): Promise<void> {
  await blockMapTiles(page);
  await page.goto(plannerUrl(trip));
  await expectRoutes(page, trip);
}

/** Type into a place field and choose the place from the suggestions. */
export async function choosePlace(page: Page, label: "From" | "To", place: Place): Promise<void> {
  const input = page.getByRole("textbox", { name: label, exact: true });
  await input.fill(place.search);
  const suggestions = page.getByRole("list", { name: `${label} suggestions` });
  await suggestions.getByRole("button", { name: new RegExp(`^${escapeRegExp(place.name)}`) }).first().click();
  await expect(page.getByText(`Selected: ${place.name}`)).toBeVisible();
}

export async function fetchBestRoute(request: APIRequestContext, trip: Trip): Promise<RouteOut> {
  const params = new URLSearchParams({ from: latLng(trip.from), to: latLng(trip.to), profile: trip.profile });
  const response = await request.get(`${API_URL}/api/route?${params.toString()}`);
  expect(response.status(), "GET /api/route").toBe(200);
  const body = (await response.json()) as RouteResponse;
  const best = body.routes[0];
  if (!best) throw new Error("the API found no route for the trip");
  return best;
}

/**
 * The longest straight piece of a route, as a GeoJSON LineString. A line along a
 * path the route uses always matches that path in the matcher (OP-206), whereas a
 * point at a junction can snap to a side path.
 */
export function longestSegment(route: RouteOut): { type: "LineString"; coordinates: [number, number][] } {
  const coords = route.geometry.coordinates as [number, number][];
  let best: [number, number][] = [coords[0], coords[1]];
  let bestLength = -1;
  for (let index = 0; index + 1 < coords.length; index += 1) {
    const [aLng, aLat] = coords[index];
    const [bLng, bLat] = coords[index + 1];
    // Equirectangular metres at Edmonton's latitude; only the comparison matters.
    const dx = (bLng - aLng) * Math.cos((53.54 * Math.PI) / 180) * 111_320;
    const dy = (bLat - aLat) * 110_574;
    const length = Math.hypot(dx, dy);
    if (length > bestLength) {
      bestLength = length;
      best = [coords[index], coords[index + 1]];
    }
  }
  return { type: "LineString", coordinates: best };
}

export function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
