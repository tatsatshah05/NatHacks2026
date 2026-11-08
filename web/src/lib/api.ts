import { featureToObstruction, Obstruction, ObstructionCollection } from "./types";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

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
