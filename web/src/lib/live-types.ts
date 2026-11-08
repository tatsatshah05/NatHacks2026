// Message shapes of `WS /ws` (OP-303), hand-written to mirror the Pydantic models in
// cloud/app/ws.py. OpenAPI does not describe WebSockets, so `make types` cannot
// generate these; keep them in step with ws.py by hand. Times are ISO 8601 UTC strings.

import type { TravelProfile } from "./planner-types";

/** A point as `[lat, lng]` (not GeoJSON order). */
export type LatLng = [number, number];

/** The route a connection follows. One per connection; subscribing again replaces it. */
export type LiveRoute = {
  from: LatLng;
  to: LatLng;
  profile: TravelProfile;
};

// ----------------------------------------------------------- client to server

export type SubscribeMessage = { type: "subscribe"; route: LiveRoute };
export type UnsubscribeMessage = { type: "unsubscribe" };
export type PingMessage = { type: "ping" };
export type ClientMessage = SubscribeMessage | UnsubscribeMessage | PingMessage;

// ----------------------------------------------------------- server to client

/** The best route for a subscription, in a few numbers. */
export type RouteSummary = {
  possible: boolean; // false: no route for this profile between these points
  eta_min: number;
  distance_m: number;
  reason: string | null;
};

/** What `obstruction_changed` says about one item. */
export type ObstructionBrief = {
  id: string;
  kind: string | null;
  effect: string;
  title: string;
};

export type HelloEvent = { type: "hello"; heartbeat_s: number; at: string };
export type HeartbeatEvent = { type: "heartbeat"; at: string };
export type PongEvent = { type: "pong"; at: string };
export type ErrorEvent = { type: "error"; message: string };
export type SubscribedEvent = { type: "subscribed"; route: LiveRoute; summary: RouteSummary; at: string };
export type UnsubscribedEvent = { type: "unsubscribed" };

export type SourceStatusEvent = {
  type: "source_status";
  source: string;
  ok: boolean; // this run succeeded
  stale: boolean;
  item_count: number;
  age_s: number | null;
  last_error: string | null;
  at: string;
};

export type ObstructionChangedEvent = {
  type: "obstruction_changed";
  added: ObstructionBrief[];
  removed: ObstructionBrief[];
  at: string;
};

export type RouteChangedEvent = {
  type: "route_changed";
  route: LiveRoute;
  summary: RouteSummary; // the route now
  previous: RouteSummary; // the route the client last knew about
  change: string; // one sentence; the client announces it
  at: string;
};

export type ServerEvent =
  | HelloEvent
  | HeartbeatEvent
  | PongEvent
  | ErrorEvent
  | SubscribedEvent
  | UnsubscribedEvent
  | SourceStatusEvent
  | ObstructionChangedEvent
  | RouteChangedEvent;

export type ServerEventType = ServerEvent["type"];

const EVENT_TYPES: ReadonlySet<string> = new Set<ServerEventType>([
  "hello",
  "heartbeat",
  "pong",
  "error",
  "subscribed",
  "unsubscribed",
  "source_status",
  "obstruction_changed",
  "route_changed"
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSummary(value: unknown): value is RouteSummary {
  return (
    isRecord(value) &&
    typeof value.possible === "boolean" &&
    typeof value.eta_min === "number" &&
    typeof value.distance_m === "number"
  );
}

function isRoute(value: unknown): value is LiveRoute {
  return isRecord(value) && Array.isArray(value.from) && Array.isArray(value.to) && typeof value.profile === "string";
}

/**
 * One server message, or null for anything that is not a known event. The checks
 * cover the fields the client acts on, so a malformed message is ignored instead of
 * breaking the page.
 */
export function parseServerEvent(data: unknown): ServerEvent | null {
  if (typeof data !== "string") return null;
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return null;
  }
  if (!isRecord(value) || typeof value.type !== "string" || !EVENT_TYPES.has(value.type)) return null;
  switch (value.type) {
    case "route_changed":
      if (!isRoute(value.route) || !isSummary(value.summary) || !isSummary(value.previous)) return null;
      if (typeof value.change !== "string") return null;
      break;
    case "subscribed":
      if (!isRoute(value.route) || !isSummary(value.summary)) return null;
      break;
    case "source_status":
      if (typeof value.source !== "string" || typeof value.ok !== "boolean") return null;
      break;
    case "obstruction_changed":
      if (!Array.isArray(value.added) || !Array.isArray(value.removed)) return null;
      break;
    case "hello":
      if (typeof value.heartbeat_s !== "number") return null;
      break;
    case "error":
      if (typeof value.message !== "string") return null;
      break;
  }
  return value as ServerEvent;
}
