// The web app's client for the OpenPath API. Every shape comes from the generated
// api-types.ts (`make types`); nothing here is a hand-written API type.
import type { components } from "./api-types";
import type { PlannerQuery, RouteMode, RouteResponse, SearchResult, StatusResponse } from "./planner-types";
import { featureToObstruction, Obstruction, ObstructionCollection } from "./types";

/**
 * The API's base URL. OP-113 named it NEXT_PUBLIC_API_BASE_URL and AGENTS.md names it
 * NEXT_PUBLIC_API_URL; `make dev` sets both, and either works. Empty means this host.
 */
export const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || process.env.NEXT_PUBLIC_API_URL || "").replace(
  /\/+$/,
  ""
);

export type Health = components["schemas"]["Health"];
type ObstructionFeature = components["schemas"]["ObstructionFeature"];

/**
 * Why a request failed, in a form the page can turn into "what happened and what to do".
 *
 * - `offline`: the browser says it has no connection.
 * - `no_answer`: the request never got an answer (network error or timeout).
 * - `unavailable`: the service answered with a server error (it is starting or down).
 * - `rejected`: the service refused this request and said why (`message` is its words).
 * - `invalid`: the answer was not the expected shape.
 */
export type ApiFailure = "offline" | "no_answer" | "unavailable" | "rejected" | "invalid";

export class ApiError extends Error {
  constructor(
    readonly failure: ApiFailure,
    message: string,
    readonly status: number | null = null
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function browserOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

/** The `detail` string FastAPI puts in an error body, when there is one. */
async function detailOf(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const { detail } = body as { detail: unknown };
      if (typeof detail === "string") return detail;
    }
  } catch {
    // Not JSON.
  }
  return null;
}

type FetchOptions = { signal?: AbortSignal; timeoutMs?: number; accept?: string };

/**
 * GET a JSON body. Aborts after `timeoutMs` (reported as `no_answer`); an abort from
 * the caller's `signal` is passed through as an AbortError so stale requests stay quiet.
 */
async function getJson(path: string, { signal, timeoutMs = 15000, accept = "application/json" }: FetchOptions = {}) {
  if (browserOffline()) throw new ApiError("offline", "This device is offline.");
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const forward = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", forward);
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      signal: controller.signal,
      headers: { Accept: accept },
      cache: "no-store"
    });
  } catch (error) {
    if (signal?.aborted) throw error; // the caller moved on
    if (browserOffline()) throw new ApiError("offline", "This device is offline.");
    throw new ApiError("no_answer", timedOut ? "The request timed out." : "The request got no answer.");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forward);
  }
  if (!response.ok) {
    const detail = await detailOf(response);
    if (response.status >= 500) {
      throw new ApiError("unavailable", detail ?? `The service returned ${response.status}.`, response.status);
    }
    throw new ApiError("rejected", detail ?? `The service returned ${response.status}.`, response.status);
  }
  try {
    return (await response.json()) as unknown;
  } catch {
    throw new ApiError("invalid", "The service returned a body that is not JSON.", response.status);
  }
}

function lngLatToParam([lng, lat]: [number, number]): string {
  return `${lat.toFixed(6)},${lng.toFixed(6)}`;
}

/** GET /api/route: up to three routes for the trip, best first (OP-207, OP-311). */
export async function getRoutes(query: PlannerQuery, signal?: AbortSignal): Promise<RouteResponse> {
  const params = new URLSearchParams({
    from: lngLatToParam(query.from.coordinates),
    to: lngLatToParam(query.to.coordinates),
    profile: query.profile
  });
  const mode: RouteMode = query.mode ?? "walk";
  if (mode === "walk_lrt") {
    params.set("mode", mode);
    // OP-420: the API reads `transit` only with walk_lrt, so it is sent only then.
    if (query.transit) params.set("transit", query.transit);
  }
  const data = (await getJson(`/api/route?${params.toString()}`, { signal, timeoutMs: 20000 })) as Partial<RouteResponse>;
  if (!data || !Array.isArray(data.routes) || !Array.isArray(data.warnings)) {
    throw new ApiError("invalid", "The route service returned an unexpected body.");
  }
  return data as RouteResponse;
}

/** GET /api/search: up to 8 addresses, LRT stations and places, best first (OP-302). */
export async function searchPlaces(text: string, signal?: AbortSignal): Promise<SearchResult[]> {
  const data = await getJson(`/api/search?${new URLSearchParams({ q: text }).toString()}`, {
    signal,
    timeoutMs: 8000
  });
  if (!Array.isArray(data)) throw new ApiError("invalid", "Place search returned an unexpected body.");
  return data as SearchResult[];
}

/** GET /api/status: when each source was last read, and which are out of date. */
export async function getStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const data = (await getJson("/api/status", { signal, timeoutMs: 10000 })) as Partial<StatusResponse>;
  if (!data || !Array.isArray(data.sources)) throw new ApiError("invalid", "Status returned an unexpected body.");
  return data as StatusResponse;
}

/** A point for an obstruction of any geometry: the point itself, or the middle vertex. */
function representativePoint(geometry: ObstructionFeature["geometry"]): [number, number] | null {
  const flat: number[][] = [];
  const walk = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number") {
      flat.push(value as number[]);
      return;
    }
    value.forEach(walk);
  };
  walk(geometry.coordinates);
  const middle = flat[Math.floor(flat.length / 2)];
  return middle ? [middle[0], middle[1]] : null;
}

export type ObstructionPoint = { id: string; lng: number; lat: number };

/**
 * Where the obstructions inside a box are, by id (GET /api/obstructions?bbox=). The route
 * report has no coordinates, so the planner map joins its items to these by id.
 */
export async function getObstructionPoints(
  bbox: [number, number, number, number],
  signal?: AbortSignal
): Promise<Map<string, ObstructionPoint>> {
  const data = (await getJson(`/api/obstructions?bbox=${bbox.map((value) => value.toFixed(5)).join(",")}`, {
    signal,
    timeoutMs: 10000,
    accept: "application/geo+json, application/json"
  })) as { features?: ObstructionFeature[] };
  const points = new Map<string, ObstructionPoint>();
  for (const feature of data.features ?? []) {
    const point = representativePoint(feature.geometry);
    if (point) points.set(feature.properties.id, { id: feature.properties.id, lng: point[0], lat: point[1] });
  }
  return points;
}

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
