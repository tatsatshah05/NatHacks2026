"use client";

// `useLiveUpdates`: one WebSocket to `WS /ws` (OP-303) for an open route.
//
// - Connects to NEXT_PUBLIC_WS_URL (or the API URL with /ws, or this host's /ws).
// - Reconnects by itself: 1 s, 2 s, 4 s ... up to 30 s between attempts, with
//   jitter so many phones do not reconnect at the same moment. It also reconnects
//   at once when the browser comes back online or the tab becomes visible again.
// - Treats a connection with no message for 2.5 heartbeats as dead and reconnects.
// - Subscribes the route on every (re)connect and whenever the route changes.
// - Status: "live" (connected), "reconnecting" (connecting or waiting to retry) or
//   "offline" (disabled, no WebSocket support, or the browser is offline).
//
// Planner pages (OP-301) mount it with `LiveStatus` from components/live-status.tsx.

import { useEffect, useMemo, useRef, useState } from "react";
import type { PlannerQuery } from "./planner-types";
import {
  parseServerEvent,
  type ClientMessage,
  type LiveRoute,
  type RouteChangedEvent,
  type RouteSummary,
  type ServerEvent,
  type SourceStatusEvent
} from "./live-types";

export type LiveStatus = "live" | "reconnecting" | "offline";

export const RECONNECT_MIN_MS = 1_000;
export const RECONNECT_MAX_MS = 30_000;
const DEFAULT_HEARTBEAT_S = 25; // until the server's hello says otherwise
const MISSED_HEARTBEATS = 2.5;

export type RouteChange = {
  event: RouteChangedEvent;
  seq: number; // increases with every change, so the same sentence twice still counts
};

export type LiveUpdates = {
  status: LiveStatus;
  /** The last event of any type. */
  lastEvent: ServerEvent | null;
  /** The last `route_changed` for the route now subscribed. */
  routeChange: RouteChange | null;
  /** The subscribed route's current summary, from `subscribed` or `route_changed`. */
  summary: RouteSummary | null;
  /** The latest `source_status` per source. */
  sources: Record<string, SourceStatusEvent>;
  /** The server's last `error` message (for example, a point too far from any path). */
  error: string | null;
  /** Failed connection attempts since the last successful one. */
  reconnectAttempts: number;
};

export type UseLiveUpdatesOptions = {
  route: LiveRoute | null;
  enabled?: boolean;
  /** Called with every event, after the hook's own state is updated. */
  onEvent?: (event: ServerEvent) => void;
};

/** Milliseconds before reconnect attempt `attempt` (0-based): doubling, jittered, 1 s to 30 s. */
export function reconnectDelayMs(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(RECONNECT_MAX_MS, RECONNECT_MIN_MS * 2 ** Math.max(0, attempt));
  const jittered = base * (0.75 + random() * 0.5);
  return Math.round(Math.min(RECONNECT_MAX_MS, Math.max(RECONNECT_MIN_MS, jittered)));
}

/** Where the live socket is. `null` when it cannot be worked out (server rendering). */
export function liveUrl(): string | null {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return explicit;
  const api = process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_BASE_URL;
  if (api) return `${api.replace(/^http/, "ws").replace(/\/+$/, "")}/ws`;
  if (typeof window === "undefined") return null;
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/ws`;
}

/** The live route for a planner query (planner places are GeoJSON [lng, lat]). */
export function liveRouteFromQuery(query: PlannerQuery): LiveRoute {
  const [fromLng, fromLat] = query.from.coordinates;
  const [toLng, toLat] = query.to.coordinates;
  return { from: [fromLat, fromLng], to: [toLat, toLng], profile: query.profile };
}

/** A stable identity for a route, to compare the subscription with incoming events. */
export function routeKey(route: LiveRoute | null): string {
  return route ? JSON.stringify([route.from[0], route.from[1], route.to[0], route.to[1], route.profile]) : "";
}

function routeFromKey(key: string): LiveRoute | null {
  if (!key) return null;
  const [fromLat, fromLng, toLat, toLng, profile] = JSON.parse(key) as [
    number,
    number,
    number,
    number,
    LiveRoute["profile"]
  ];
  return { from: [fromLat, fromLng], to: [toLat, toLng], profile };
}

function send(socket: WebSocket | null, message: ClientMessage): void {
  try {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  } catch {
    // The socket is closing; the next connection subscribes again.
  }
}

function subscription(route: LiveRoute | null): ClientMessage {
  return route ? { type: "subscribe", route } : { type: "unsubscribe" };
}

function browserOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

export function useLiveUpdates({ route, enabled = true, onEvent }: UseLiveUpdatesOptions): LiveUpdates {
  const key = routeKey(route);
  const current = useMemo(() => routeFromKey(key), [key]);

  const [connection, setConnection] = useState<LiveStatus>("reconnecting");
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [lastEvent, setLastEvent] = useState<ServerEvent | null>(null);
  const [routeChange, setRouteChange] = useState<RouteChange | null>(null);
  const [summary, setSummary] = useState<{ key: string; value: RouteSummary } | null>(null);
  const [sources, setSources] = useState<Record<string, SourceStatusEvent>>({});
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const routeRef = useRef<LiveRoute | null>(current);
  const onEventRef = useRef(onEvent);
  const seqRef = useRef(0);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  // A new route: remember it and subscribe it on the open socket (or on the next one).
  useEffect(() => {
    routeRef.current = current;
    send(socketRef.current, subscription(current));
  }, [current]);

  // The connection itself: open, watch, reconnect, close on unmount.
  useEffect(() => {
    if (!enabled) return; // the status reads "offline" while disabled
    const found = liveUrl();
    if (!found || typeof WebSocket === "undefined") {
      setConnection("offline");
      return;
    }
    const url: string = found;

    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let stopped = false;
    let heartbeatMs = DEFAULT_HEARTBEAT_S * 1000;

    const armWatchdog = (target: WebSocket) => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => {
        try {
          target.close(4000, "no heartbeat");
        } catch {
          // Already closed.
        }
      }, heartbeatMs * MISSED_HEARTBEATS);
    };

    const handle = (event: ServerEvent) => {
      setLastEvent(event);
      const following = routeRef.current;
      switch (event.type) {
        case "hello":
          heartbeatMs = Math.max(1, event.heartbeat_s) * 1000;
          break;
        case "subscribed":
          if (routeKey(event.route) === routeKey(following)) {
            setSummary({ key: routeKey(following), value: event.summary });
            setError(null);
          }
          break;
        case "route_changed":
          // Ignore a change for a route this page no longer follows.
          if (routeKey(event.route) !== routeKey(following)) return;
          seqRef.current += 1;
          setRouteChange({ event, seq: seqRef.current });
          setSummary({ key: routeKey(following), value: event.summary });
          break;
        case "source_status":
          setSources((previous) => ({ ...previous, [event.source]: event }));
          break;
        case "error":
          setError(event.message);
          break;
      }
      try {
        onEventRef.current?.(event);
      } catch {
        // A page callback must never break the connection.
      }
    };

    const scheduleRetry = () => {
      if (stopped) return;
      clearTimeout(retry);
      if (browserOffline()) {
        setConnection("offline"); // the "online" listener reconnects
        return;
      }
      setConnection("reconnecting");
      const delay = reconnectDelayMs(attempt);
      attempt += 1;
      setReconnectAttempts(attempt);
      retry = setTimeout(connect, delay);
    };

    function connect() {
      clearTimeout(retry);
      if (stopped || socket) return;
      if (browserOffline()) {
        setConnection("offline");
        return;
      }
      setConnection("reconnecting");
      let next: WebSocket;
      try {
        next = new WebSocket(url);
      } catch {
        scheduleRetry();
        return;
      }
      socket = next;
      socketRef.current = next;
      next.onopen = () => {
        if (socket !== next) return;
        attempt = 0;
        setReconnectAttempts(0);
        setConnection("live");
        armWatchdog(next);
        send(next, subscription(routeRef.current));
      };
      next.onmessage = (message: MessageEvent) => {
        if (socket !== next) return;
        armWatchdog(next);
        const event = parseServerEvent(message.data);
        if (event) handle(event);
      };
      next.onerror = () => {
        // A close event always follows; it schedules the retry.
      };
      next.onclose = () => {
        if (socket !== next) return;
        socket = null;
        socketRef.current = null;
        clearTimeout(watchdog);
        scheduleRetry();
      };
    }

    const reconnectNow = () => {
      if (stopped || socket) return;
      attempt = 0;
      connect();
    };
    const goOffline = () => {
      setConnection("offline");
      clearTimeout(retry);
      try {
        socket?.close(1000, "browser offline");
      } catch {
        // Already closed.
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") reconnectNow();
    };

    window.addEventListener("online", reconnectNow);
    window.addEventListener("offline", goOffline);
    document.addEventListener("visibilitychange", onVisibility);
    connect();

    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(watchdog);
      window.removeEventListener("online", reconnectNow);
      window.removeEventListener("offline", goOffline);
      document.removeEventListener("visibilitychange", onVisibility);
      const open = socket;
      socket = null;
      socketRef.current = null;
      try {
        open?.close(1000, "page closed");
      } catch {
        // Already closed.
      }
    };
  }, [enabled]);

  return {
    status: enabled ? connection : "offline",
    lastEvent,
    routeChange: routeChange && routeKey(routeChange.event.route) === key ? routeChange : null,
    summary: summary && summary.key === key ? summary.value : null,
    sources,
    error,
    reconnectAttempts
  };
}
