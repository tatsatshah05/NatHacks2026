// GET /api/status and GET /api/cameras for the sources page (OP-312). Kept apart from
// api.ts so the stories building Phase 3 in parallel do not edit the same lines; the
// lead can fold them into api.ts after merging.

import type { components } from "./api-types";

type Schemas = components["schemas"];
export type StatusResponse = Schemas["StatusResponse"];
export type CameraCollection = Schemas["CameraCollection"];

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    signal,
    headers: { Accept: "application/json" },
    cache: "no-store"
  });
  if (!response.ok) throw new Error(`${path} returned ${response.status}.`);
  return (await response.json()) as T;
}

/** Freshness of every scheduled source, plus the data mode. */
export async function getStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const data = await getJson<Partial<StatusResponse>>("/api/status", signal);
  if (!Array.isArray(data.sources) || (data.mode !== "live" && data.mode !== "replay")) {
    throw new Error("/api/status returned an unexpected body.");
  }
  return data as StatusResponse;
}

/** The latest unexpired road camera readings. */
export async function getCameras(signal?: AbortSignal): Promise<CameraCollection> {
  const data = await getJson<Partial<CameraCollection>>("/api/cameras", signal);
  if (!Array.isArray(data.features)) throw new Error("/api/cameras returned an unexpected body.");
  return data as CameraCollection;
}
