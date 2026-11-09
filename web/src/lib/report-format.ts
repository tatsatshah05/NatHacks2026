// Plain-language text for the route report (OP-211).
import type { ItemStatus, ReportItem } from "./planner-types";

const SOURCE_LABELS: Record<string, string> = {
  city_lrt_devices: "LRT elevators and escalators",
  city_311_snow: "311 snow complaints",
  city_trail_closures: "Trail closures",
  city_construction: "Construction permits",
  city_snow_clearing: "Snow clearing",
  city_traffic_disruptions: "Traffic disruptions",
  weather_watch: "Ice-risk weather watch",
  camera_model: "Road cameras"
};

// What each effect means for someone on the path, in plain words (docs/design/DESIGN.md).
const EFFECT_LABELS: Record<ReportItem["effect"], string> = {
  blocked: "Path closed",
  snow: "Snow on the path",
  ice_risk: "Ice risk",
  caution: "Take care"
};

/**
 * The note GET /api/route gives an item that covers the whole city (cloud/app/report.py
 * NOTES["citywide"]). The API marks city-wide items only through this note today; if
 * ReportItemOut gains an `everywhere` flag, test that instead.
 */
export const CITYWIDE_NOTE = "Covers the whole city, so it applies to every route and changes none.";

/** True for an item that applies everywhere (the ice-risk watch), not at a place on the route. */
export function isCityWide(item: Pick<ReportItem, "note">): boolean {
  return item.note === CITYWIDE_NOTE;
}

/** One sentence for a city-wide item: "Ice-risk watch is on for the whole city." */
export function cityWideSentence(item: Pick<ReportItem, "source" | "title">): string {
  if (item.source === "weather_watch") return "Ice-risk watch is on for the whole city.";
  return /[.!?]$/.test(item.title) ? item.title : `${item.title}.`;
}

const CONFIDENCE_LABELS: Record<ReportItem["confidence"], string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence"
};

const EDMONTON = "America/Edmonton";

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source.replace(/_/g, " ");
}

/** "Path closed", "Snow on the path", "Ice risk", "Take care"; never the raw effect code. */
export function effectLabel(effect: string): string {
  return EFFECT_LABELS[effect as ReportItem["effect"]] ?? "Take care";
}

export function confidenceLabel(confidence: ReportItem["confidence"]): string {
  return CONFIDENCE_LABELS[confidence];
}

export function statusLabel(status: ItemStatus, distanceM: number | null): string {
  const away = distanceM === null ? "" : `, ${Math.round(distanceM)} m from the route`;
  if (status === "on_route") return "On this route";
  if (status === "nearby") return `Near this route${away}`;
  return `Avoided by this route${away}`;
}

/**
 * A travel time in whole minutes: "12 min", "1 h", "1 h 18 min". Never less than a
 * minute, so a short hop is not "0 min". Used for every ETA, ride and wait the pages show
 * or speak, so an hour-long trip reads as hours rather than "78 min".
 */
export function formatMinutes(minutes: number): string {
  const whole = Math.max(1, Math.round(minutes));
  if (whole < 60) return `${whole} min`;
  const hours = Math.floor(whole / 60);
  const rest = whole % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** "3 min ago", "2 h 15 min ago"; `null` when the time is unknown or invalid. */
export function formatAgo(iso: string | null | undefined, now: Date): string | null {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.valueOf())) return null;
  const minutes = Math.max(0, Math.floor((now.getTime() - then.getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min ago` : `${hours} h ago`;
  return `on ${formatDate(iso)}`;
}

/** An Edmonton date and time such as "Nov 10, 3:00 p.m.". */
export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: EDMONTON,
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(iso));
}

/** An Edmonton time of day such as "3:05 p.m.", for something seen within the last hours. */
export function formatTime(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: EDMONTON,
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(iso));
}

export function formatDistance(metres: number): string {
  return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(1)} km`;
}

export function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}
