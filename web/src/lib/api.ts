import type { components, paths } from "./api-types";
import type { PlannerQuery, RouteResponse } from "./planner-types";
import { featureToObstruction, Obstruction, ObstructionCollection } from "./types";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type Health = components["schemas"]["Health"];
type RouteOperation = paths["/api/route"]["get"];

export async function getObstructions(signal?: AbortSignal): Promise<Obstruction[]> {
  const response = await fetch(`${apiBase}/api/obstructions`, {
    signal,
    headers: { Accept: "application/geo+json, application/json" },
    cache: "no-store"
  });

  if (!response.ok) {
    throw new Error(`Obstruction service returned ${response.status}.`);
  }

  const data = (await response.json()) as ObstructionCollection;
  if (data.type !== "FeatureCollection" || !Array.isArray(data.features)) {
    throw new Error("Obstruction service returned an invalid GeoJSON response.");
  }

  return data.features
    .filter((feature) => feature.geometry?.type === "Point")
    .map(featureToObstruction);
}

/** GET /health: the API's git commit and whether it serves live or replayed data. */
export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch(`${apiBase}/health`, {
    signal,
    headers: { Accept: "application/json" },
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error(`Health check returned ${response.status}.`);
  }

  const data = (await response.json()) as Partial<Health>;
  if (data.status !== "ok" || (data.mode !== "live" && data.mode !== "replay")) {
    throw new Error("Health check returned an unexpected body.");
  }
  return { status: "ok", version: String(data.version ?? ""), mode: data.mode };
}

export async function getRoute(query: PlannerQuery, signal?: AbortSignal): Promise<RouteResponse> {
  const [fromLng, fromLat] = query.from.coordinates;
  const [toLng, toLat] = query.to.coordinates;
  const search = new URLSearchParams({
    from: `${fromLat},${fromLng}`,
    to: `${toLat},${toLng}`,
    profile: query.profile
  });
  const response = await fetch(`${apiBase}/api/route?${search.toString()}`, {
    signal,
    headers: { Accept: "application/json" },
    cache: "no-store"
  });

  if (!response.ok) {
    throw new Error(`Route service returned ${response.status}.`);
  }

  const data = (await response.json()) as RouteOperation["responses"][200]["content"]["application/json"];
  if (!data || !Array.isArray(data.routes)) {
    throw new Error("Route service returned an invalid response.");
  }
  return data;
}
