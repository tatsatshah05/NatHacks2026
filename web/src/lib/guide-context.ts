// OP-418: what the camera guide tells the API about the route, and what it does with
// the hazards it gets back. Pure functions for `npm run test:lib` (only type imports,
// which Node strips); the API shapes come from the generated api-types.ts through
// lib/guide-api.ts.

import type { GuideContext, GuideProfile } from "./guide-api";

export const MAX_NEARBY = 5;
export const NEARBY_WITHIN_M = 100;
export const TRANSCRIPT_LINES = 5;
// The API's caps (cloud/app/guide.py): a step, the previous sentence or a title, and a kind.
export const MAX_TEXT_CHARS = 400;
export const MAX_KIND_CHARS = 64;

/** One barrier the route report knows, as the guide's context lists it. */
export type NearbyItem = { title: string; kind: string; distance_m: number };

type ReportLike = {
  groups: ReadonlyArray<{
    items: ReadonlyArray<{
      status: "on_route" | "nearby" | "avoided";
      distance_m: number | null;
      kind: string | null;
      effect: string;
      title: string;
    }>;
  }>;
};

/**
 * The report's items that are on the route, or near it within 100 m, nearest first,
 * at most five. Avoided items are not on the walker's path and are left out.
 */
export function nearbyItems(report: ReportLike): NearbyItem[] {
  const items: NearbyItem[] = [];
  for (const group of report.groups) {
    for (const item of group.items) {
      if (item.status === "avoided") continue;
      const distance = item.distance_m ?? 0;
      if (item.status === "nearby" && (item.distance_m === null || distance > NEARBY_WITHIN_M)) continue;
      items.push({ title: item.title, kind: item.kind ?? item.effect, distance_m: Math.round(distance) });
    }
  }
  return items.sort((a, b) => a.distance_m - b.distance_m).slice(0, MAX_NEARBY);
}

export type ContextInput = {
  clientId: string;
  profile: GuideProfile;
  steps: ReadonlyArray<{ text: string }>;
  /** The current step. */
  index: number;
  /** The last GPS fix, or null without one. */
  position: { lat: number; lng: number; heading_deg: number | null } | null;
  /** What the follower read from that fix, or null without one. */
  reading: { distance_to_turn_m: number | null; off_route_m: number | null } | null;
  report: ReportLike;
  /** The last sentence the API gave, so it says only what is new. */
  previous: string | null;
};

function text(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed.slice(0, MAX_TEXT_CHARS) : null;
}

function metres(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.round(value) : null;
}

/** The `context` field of one frame, within every bound the API checks (else 422). */
export function buildGuideContext(input: ContextInput): GuideContext {
  const { position } = input;
  const heading = position?.heading_deg;
  return {
    client_id: input.clientId,
    profile: input.profile,
    step: text(input.steps[input.index]?.text),
    next_step: text(input.steps[input.index + 1]?.text),
    distance_to_turn_m: metres(input.reading?.distance_to_turn_m),
    heading_deg:
      typeof heading === "number" && Number.isFinite(heading) ? Math.round((((heading % 360) + 360) % 360) * 10) / 10 : null,
    lat: position ? Math.round(position.lat * 1e6) / 1e6 : null,
    lng: position ? Math.round(position.lng * 1e6) / 1e6 : null,
    off_route_m: metres(input.reading?.off_route_m),
    nearby: nearbyItems(input.report).map((item) => ({
      title: item.title.slice(0, MAX_TEXT_CHARS),
      kind: item.kind.slice(0, MAX_KIND_CHARS),
      distance_m: item.distance_m
    })),
    previous: text(input.previous)
  };
}

// Vibration per hazard kind (guide-web.md): a short double pulse for a drop or an
// edge, one long pulse for something moving or built, nothing for a door, a sign or a
// landmark. Kinds not listed vibrate nothing.
export const SHORT_DOUBLE_PULSE: readonly number[] = [80, 60, 80];
export const LONG_PULSE: readonly number[] = [400];

const SHORT_KINDS = new Set(["stairs_down", "curb", "hole", "water_or_ice", "off_sidewalk"]);
const LONG_KINDS = new Set(["vehicle", "bike", "construction"]);

/** The one pattern for a set of hazards: the long pulse wins over the short one. */
export function vibrationFor(hazards: ReadonlyArray<{ kind: string }>): number[] | null {
  if (hazards.some((hazard) => LONG_KINDS.has(hazard.kind))) return [...LONG_PULSE];
  if (hazards.some((hazard) => SHORT_KINDS.has(hazard.kind))) return [...SHORT_DOUBLE_PULSE];
  return null;
}

/** Keep the last five lines of the transcript. */
export function appendTranscript<T>(lines: ReadonlyArray<T>, line: T, limit = TRANSCRIPT_LINES): T[] {
  return [...lines, line].slice(-limit);
}

/** The JPEG the guide sends: 640 px wide, the height in proportion, never upscaled. */
export function frameSize(videoWidth: number, videoHeight: number, targetWidth = 640): { width: number; height: number } {
  if (videoWidth <= 0 || videoHeight <= 0) return { width: 0, height: 0 };
  const width = Math.min(targetWidth, videoWidth);
  return { width, height: Math.max(1, Math.round((videoHeight * width) / videoWidth)) };
}
