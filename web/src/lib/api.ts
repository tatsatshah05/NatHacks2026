import type { components } from "./api-types";
import { featureToObstruction, Obstruction, ObstructionCollection } from "./types";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type Health = components["schemas"]["Health"];

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
