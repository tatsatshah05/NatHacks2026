// The city dashboard (OP-310) and its camera readings (OP-309): fetching, filters,
// plain words and the CSV export. API shapes come only from api-types.ts.
import type { components } from "./api-types";
import { cameraPlace } from "./camera-places";
import { effectLabel, formatAgo, sourceLabel } from "./report-format";

type Schemas = components["schemas"];

export type PriorityResponse = Schemas["PriorityResponse"];
export type PriorityItem = Schemas["PriorityItem"];
export type StatusResponse = Schemas["StatusResponse"];
export type SourceStatus = Schemas["SourceStatus"];
export type CameraCollection = Schemas["CameraCollection"];
export type CameraFeature = Schemas["CameraFeature"];
export type CameraReading = Schemas["CameraProperties"];

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

async function getJson<T>(path: string, what: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      signal,
      headers: { Accept: "application/json" },
      cache: "no-store"
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    // The page shows this sentence, so it says what to do, not what the browser said.
    const offline = typeof navigator !== "undefined" && navigator.onLine === false;
    throw new Error(
      offline ? `This device is offline, so the ${what} could not be checked.` : `The ${what} service did not answer. Check your connection.`
    );
  }
  if (!response.ok) throw new Error(`The ${what} service answered ${response.status}.`);
  try {
    return (await response.json()) as T;
  } catch {
    throw new Error(`The ${what} service sent an unexpected answer.`);
  }
}

/** GET /api/priority: every barrier, ranked clear-first. */
export async function getPriority(signal?: AbortSignal): Promise<PriorityResponse> {
  const data = await getJson<PriorityResponse>("/api/priority", "ranking", signal);
  if (!Array.isArray(data.items)) throw new Error("The ranking service sent an unexpected answer.");
  return data;
}

/** GET /api/status: how fresh each source is. */
export function getStatus(signal?: AbortSignal): Promise<StatusResponse> {
  return getJson<StatusResponse>("/api/status", "source status", signal);
}

/** GET /api/cameras: the latest reading of every road camera view. */
export async function getCameras(signal?: AbortSignal): Promise<CameraFeature[]> {
  const data = await getJson<CameraCollection>("/api/cameras", "camera", signal);
  return Array.isArray(data.features) ? data.features : [];
}

// ------------------------------------------------------------------ words

export const CATEGORY_LABELS: Record<PriorityItem["category"], string> = {
  indoor: "Indoor",
  sidewalk: "Sidewalks and paths",
  road: "Roads and crossings",
  weather: "Weather and ice",
  transit: "Transit"
};

export const CONFIDENCE_WORDS: Record<PriorityItem["confidence"], string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence"
};

export const EFFECT_ORDER: ReadonlyArray<PriorityItem["effect"]> = ["blocked", "snow", "ice_risk", "caution"];
export const CONFIDENCE_ORDER: ReadonlyArray<PriorityItem["confidence"]> = ["high", "medium", "low"];
export const CATEGORY_ORDER: ReadonlyArray<PriorityItem["category"]> = [
  "indoor",
  "sidewalk",
  "road",
  "weather",
  "transit"
];

/** "Wet road", "Snow-covered road", "Unknown: camera unavailable". Never "black ice". */
export function readingWords(reading: Pick<CameraReading, "label" | "reason">): string {
  switch (reading.label) {
    case "dry":
      return "Dry road";
    case "wet":
      return "Wet road";
    case "snow_partial":
      return "Some snow on the road";
    case "snow_covered":
      return "Snow-covered road";
    case "ice":
      return "Ice risk";
    default:
      return `Unknown: ${unknownReason(reading.reason)}`;
  }
}

/** The short word on a camera marker. */
export function readingShort(label: CameraReading["label"]): string {
  const words: Record<CameraReading["label"], string> = {
    dry: "Dry",
    wet: "Wet",
    snow_partial: "Some snow",
    snow_covered: "Snow",
    ice: "Ice risk",
    unknown: "Unknown"
  };
  return words[label];
}

const UNKNOWN_REASONS: Record<string, string> = {
  camera_unavailable: "camera unavailable",
  unusable_frame: "picture too unclear to judge",
  model_unusable: "picture too unclear to judge",
  low_confidence: "the model was not sure",
  invalid_model_output: "the model gave no usable answer",
  model_error: "the model could not run",
  ai_check_disagrees: "two checks disagreed",
  ai_check_unavailable: "the second check was unavailable"
};

function unknownReason(reason: string | null): string {
  if (!reason) return "no reading";
  return UNKNOWN_REASONS[reason] ?? reason.replace(/_/g, " ");
}

/** "Model confidence 0.82", or nothing when the reading has no score. */
export function scoreWords(score: number | null): string | null {
  return score === null ? null : `Model confidence ${score.toFixed(2)}`;
}

/** Where the camera is, from 511 Alberta's own camera list. */
export function cameraName(reading: Pick<CameraReading, "camera_id" | "view_id">): string {
  return cameraPlace(reading.camera_id) ?? `Camera ${reading.camera_id}`;
}

/** 511 Alberta's official camera list. It has no page for a single camera. */
export const CAMERA_VIEWER_URL = "https://511.alberta.ca/cctv";

// ---------------------------------------------------------------- filters

export type Filters = {
  query: string;
  effect: PriorityItem["effect"] | "";
  category: PriorityItem["category"] | "";
  source: string;
  confidence: PriorityItem["confidence"] | "";
};

export const NO_FILTERS: Filters = { query: "", effect: "", category: "", source: "", confidence: "" };

export function hasFilters(filters: Filters): boolean {
  return Boolean(filters.query.trim() || filters.effect || filters.category || filters.source || filters.confidence);
}

export function applyFilters(items: readonly PriorityItem[], filters: Filters): PriorityItem[] {
  const words = filters.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter((item) => {
    if (filters.effect && item.effect !== filters.effect) return false;
    if (filters.category && item.category !== filters.category) return false;
    if (filters.source && item.source !== filters.source) return false;
    if (filters.confidence && item.confidence !== filters.confidence) return false;
    if (!words.length) return true;
    const text = [item.title, item.detail ?? "", item.why, sourceLabel(item.source), effectLabel(item.effect)]
      .join(" ")
      .toLowerCase();
    return words.every((word) => text.includes(word));
  });
}

// -------------------------------------------------------------------- CSV

const CSV_COLUMNS: ReadonlyArray<{ header: string; value: (item: PriorityItem, now: Date) => string | number }> = [
  { header: "rank", value: (item) => item.rank },
  { header: "title", value: (item) => item.title },
  { header: "effect", value: (item) => effectLabel(item.effect) },
  { header: "kind", value: (item) => item.kind },
  { header: "category", value: (item) => CATEGORY_LABELS[item.category] },
  { header: "why", value: (item) => item.why },
  { header: "saved_routes_affected", value: (item) => item.saved_routes_affected },
  { header: "pedestrians_nearby_per_day", value: (item) => Math.round(item.pedestrian_weight) },
  { header: "nearest_counter", value: (item) => item.nearest_counter?.name ?? "" },
  { header: "nearest_counter_m", value: (item) => (item.nearest_counter ? Math.round(item.nearest_counter.distance_m) : "") },
  { header: "score", value: (item) => item.score },
  { header: "confidence", value: (item) => item.confidence },
  { header: "source", value: (item) => sourceLabel(item.source) },
  { header: "attribution", value: (item) => item.attribution },
  { header: "checked_at_utc", value: (item) => item.observed_at },
  { header: "checked", value: (item, now) => formatAgo(item.observed_at, now) ?? "" },
  { header: "lat", value: (item) => item.lat.toFixed(6) },
  { header: "lng", value: (item) => item.lng.toFixed(6) },
  { header: "source_url", value: (item) => item.url ?? "" },
  { header: "id", value: (item) => item.id }
];

/** One CSV cell: quoted when needed, and never read as a formula by a spreadsheet. */
export function csvCell(value: string | number): string {
  let text = String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** The rows exactly as filtered, with a header row. Public data only: no client ids. */
export function toCsv(items: readonly PriorityItem[], now: Date): string {
  const lines = [CSV_COLUMNS.map((column) => column.header).join(",")];
  for (const item of items) lines.push(CSV_COLUMNS.map((column) => csvCell(column.value(item, now))).join(","));
  return `${lines.join("\r\n")}\r\n`;
}

/** "openpath-clear-first-2026-11-08-0930.csv", in Edmonton time. */
export function csvFilename(now: Date, filtered: boolean): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/Edmonton",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value])
  );
  const stamp = `${parts.year}-${parts.month}-${parts.day}-${parts.hour}${parts.minute}`;
  return `openpath-clear-first${filtered ? "-filtered" : ""}-${stamp}.csv`;
}

export function downloadCsv(items: readonly PriorityItem[], now: Date, filtered: boolean): void {
  // A byte-order mark so spreadsheet apps read the accented names as UTF-8.
  const blob = new Blob(["﻿", toCsv(items, now)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = csvFilename(now, filtered);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
