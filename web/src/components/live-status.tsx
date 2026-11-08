"use client";

// Live updates on screen (OP-303): the connection badge, the speech toggle and the
// `aria-live="assertive"` region that receives every `route_changed` sentence.
//
//   const live = useLiveUpdates({ route: liveRouteFromQuery(query) });
//   <LiveStatus live={live} />
//
// The badge says "Live", "Reconnecting" or "Offline" in text, with a circle whose
// shape also differs (filled, half, hollow), so colour is never the only cue. Each
// route change is shown and announced once, spoken when the user's toggle is on and
// vibrated where supported (lib/announce.ts).

import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { announceChange, canSpeak, useSpeakPreference } from "@/lib/announce";
import type { LiveStatus as Status, LiveUpdates } from "@/lib/live";

const LABEL: Record<Status, string> = {
  live: "Live",
  reconnecting: "Reconnecting",
  offline: "Offline"
};

const DETAIL: Record<Status, string> = {
  live: "Route changes arrive as they happen.",
  reconnecting: "Trying to reconnect; your route stays as last checked.",
  offline: "Live updates are paused; your route stays as last checked."
};

// Colours come from the design tokens; the fallbacks match today's globals.css.
const INK = "var(--ink, #15241f)";
const MUTED = "var(--muted, #52635c)";
const CARD = "var(--card, var(--surface, #ffffff))";
const LINE = "var(--line, #c9d6ce)";
const ACTION = "var(--action, var(--brand, #146b55))";
const TONE: Record<Status, string> = {
  live: "var(--clear, var(--brand, #146b55))",
  reconnecting: "var(--caution, var(--focus, #8a5300))",
  offline: MUTED
};

const visuallyHidden: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0
};

function glyphStyle(status: Status): CSSProperties {
  const tone = TONE[status];
  const shape: Record<Status, CSSProperties> = {
    live: { background: tone }, // filled
    reconnecting: { background: `linear-gradient(90deg, ${tone} 50%, transparent 50%)` }, // half
    offline: { background: "transparent" } // hollow
  };
  return {
    border: `2px solid ${tone}`,
    borderRadius: "50%",
    display: "inline-block",
    flex: "0 0 auto",
    height: "0.75rem",
    width: "0.75rem",
    ...shape[status]
  };
}

/** The connection badge alone, for headers. Changes are announced politely. */
export function LiveBadge({ status }: { status: Status }) {
  return (
    <p
      role="status"
      title={DETAIL[status]}
      style={{
        alignItems: "center",
        background: CARD,
        border: `1px solid ${TONE[status]}`,
        borderRadius: 999,
        color: INK,
        display: "inline-flex",
        fontSize: "0.875rem",
        fontWeight: 700,
        gap: "0.5rem",
        margin: 0,
        padding: "0.25rem 0.75rem"
      }}
    >
      <span aria-hidden="true" style={glyphStyle(status)} />
      <span style={visuallyHidden}>Live updates: </span>
      {LABEL[status]}
    </p>
  );
}

const noSubscribe = () => () => undefined;

/** The speech on/off switch. The choice is kept on this device (`openpath.speak`). */
export function SpeakToggle() {
  const [speakOn, setSpeakOn] = useSpeakPreference();
  const supported = useSyncExternalStore(noSubscribe, canSpeak, () => true);
  return (
    <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
      <button
        type="button"
        aria-pressed={speakOn}
        onClick={() => setSpeakOn(!speakOn)}
        style={{
          alignItems: "center",
          background: speakOn ? ACTION : CARD,
          border: `2px solid ${ACTION}`,
          borderRadius: 8,
          color: speakOn ? CARD : ACTION,
          cursor: "pointer",
          display: "inline-flex",
          fontWeight: 700,
          minHeight: 44,
          minWidth: 44,
          padding: "0.5rem 0.875rem"
        }}
      >
        Speak route changes: {speakOn ? "On" : "Off"}
      </button>
      {!supported && (
        <span style={{ color: MUTED, fontSize: "0.875rem" }}>
          This browser cannot speak; changes are still shown here and read by screen readers.
        </span>
      )}
    </div>
  );
}

/**
 * The assertive region. It is always in the page (a live region must exist before
 * its text changes); each new change clears it and fills it again, so even the same
 * sentence twice is read out.
 */
export function RouteChangeAnnouncer({ live }: { live: LiveUpdates }) {
  const [speakOn] = useSpeakPreference();
  const [message, setMessage] = useState("");
  const announced = useRef(0); // the last change spoken and vibrated
  const speakRef = useRef(speakOn); // read when a change arrives; toggling never re-announces
  const change = live.routeChange;
  const seq = change?.seq ?? 0;
  const sentence = change?.event.change ?? "";

  useEffect(() => {
    speakRef.current = speakOn;
  }, [speakOn]);

  useEffect(() => {
    if (!seq) return;
    if (seq !== announced.current) {
      announced.current = seq;
      announceChange(sentence, speakRef.current);
    }
    setMessage("");
    const fill = setTimeout(() => setMessage(sentence), 100);
    return () => clearTimeout(fill);
  }, [seq, sentence]);

  const summary = live.summary;
  return (
    <div
      style={{
        borderLeft: message ? `4px solid ${TONE.reconnecting}` : "4px solid transparent",
        color: INK,
        minHeight: "1.5rem",
        paddingLeft: "0.75rem"
      }}
    >
      <p aria-live="assertive" aria-atomic="true" style={{ fontWeight: 700, margin: 0 }}>
        {message ? `Route update: ${message}` : ""}
      </p>
      {message && summary && summary.possible && (
        <p style={{ color: MUTED, fontSize: "0.875rem", margin: "0.25rem 0 0" }}>
          The best route now takes about {Math.round(summary.eta_min)} min over{" "}
          {(summary.distance_m / 1000).toFixed(1)} km.
        </p>
      )}
    </div>
  );
}

/** Badge, speech toggle and announcer together: what a planner page mounts. */
export default function LiveStatus({ live }: { live: LiveUpdates }) {
  return (
    <section
      aria-label="Live updates"
      style={{
        background: CARD,
        border: `1px solid ${LINE}`,
        borderRadius: 12,
        color: INK,
        display: "grid",
        gap: "0.75rem",
        padding: "0.75rem 1rem"
      }}
    >
      <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
        <LiveBadge status={live.status} />
        <span style={{ color: MUTED, fontSize: "0.875rem" }}>{DETAIL[live.status]}</span>
      </div>
      <SpeakToggle />
      <RouteChangeAnnouncer live={live} />
    </section>
  );
}
