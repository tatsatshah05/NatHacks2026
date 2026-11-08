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

const EFFECT_LABELS: Record<ReportItem["effect"], string> = {
  blocked: "Blocked",
  snow: "Snow",
  ice_risk: "Ice risk",
  caution: "Caution"
};

const CONFIDENCE_LABELS: Record<ReportItem["confidence"], string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence"
};

const EDMONTON = "America/Edmonton";

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source.replace(/_/g, " ");
}

export function effectLabel(effect: ReportItem["effect"]): string {
  return EFFECT_LABELS[effect];
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

export function formatDistance(metres: number): string {
  return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(1)} km`;
}

export function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}
