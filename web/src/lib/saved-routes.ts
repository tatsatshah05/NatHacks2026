// Saved routes (OP-304 API, OP-306 client): save a trip, list them, remove one, and
// the plain words for each route's status.
import type { components } from "./api-types";
import { ApiError, requestJson } from "./api-request";
import { getAnonymousClientId } from "./client-id";
import type { PlannerQuery } from "./planner-types";

type Schemas = components["schemas"];
export type SavedRoute = Schemas["SavedRouteOut"];
type SavedRouteIn = Schemas["SavedRouteIn"];
type SavedRoutesOut = Schemas["SavedRoutesOut"];

export const NAME_MAX = 80; // SavedRouteIn.name in cloud/app/push_api.py

export function listSavedRoutes(signal?: AbortSignal): Promise<SavedRoute[]> {
  const query = new URLSearchParams({ client_id: getAnonymousClientId() });
  return requestJson<SavedRoutesOut>(`/api/saved-routes?${query.toString()}`, { signal }).then((data) => data.routes);
}

/** POST /api/saved-routes for a planner trip. The API computes its usual route now. */
export function saveTrip(query: PlannerQuery, name: string): Promise<SavedRoute> {
  const [fromLng, fromLat] = query.from.coordinates;
  const [toLng, toLat] = query.to.coordinates;
  return saveRoute({
    client_id: getAnonymousClientId(),
    name,
    from: { lat: fromLat, lng: fromLng },
    to: { lat: toLat, lng: toLng },
    profile: query.profile
  });
}

/** Saves the same trip again under its old name (the undo for Remove). */
export function resaveRoute(route: SavedRoute): Promise<SavedRoute> {
  return saveRoute({
    client_id: getAnonymousClientId(),
    name: route.name,
    from: route.from,
    to: route.to,
    profile: route.profile
  });
}

function saveRoute(body: SavedRouteIn): Promise<SavedRoute> {
  return requestJson<SavedRoute>("/api/saved-routes", { method: "POST", body });
}

export function removeSavedRoute(route: SavedRoute): Promise<void> {
  const query = new URLSearchParams({ client_id: getAnonymousClientId() });
  return requestJson<void>(`/api/saved-routes/${route.id}?${query.toString()}`, { method: "DELETE" });
}

/** "Clear, about 12 min", "Changed: now 16 min (+4)", "No route right now". */
export function savedRouteStatusText(route: Pick<SavedRoute, "status" | "eta_min" | "baseline_eta_min">): string {
  const eta = route.eta_min === null ? null : Math.round(route.eta_min);
  if (route.status === "impossible") return "No route right now";
  if (route.status === "changed") {
    if (eta === null) return "Changed";
    // Whole minutes, as the push message counts them (cloud/app/push.py).
    const extra = route.baseline_eta_min === null ? 0 : eta - Math.round(route.baseline_eta_min);
    return extra >= 1 ? `Changed: now ${eta} min (+${extra})` : `Changed: now ${eta} min`;
  }
  return eta === null ? "Clear" : `Clear, about ${eta} min`;
}

/** What went wrong with a save, and what to do about it. */
export function saveErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "OpenPath could not reach the server. Check your connection and save again.";
    if (error.status === 429) return "You have saved as many routes as OpenPath keeps. Remove one on the Alerts page, then save this one.";
    if (error.status === 503) return "Saved routes are not available on this server right now. Try again later.";
    if (error.status === 422) {
      // RoutingError sentences from the API, such as "The start and end are at the same place."
      return error.detail ? `This route could not be saved. ${error.detail}` : "This route could not be saved. Check the name, then save again.";
    }
    return `This route could not be saved (error ${error.status}). Save again in a moment.`;
  }
  return "This route could not be saved. Save again in a moment.";
}
