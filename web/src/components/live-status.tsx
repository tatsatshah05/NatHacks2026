"use client";

// Live updates on screen (OP-303, styled for the design system in OP-301): the
// connection line, the speech switch and the `aria-live="assertive"` region that
// receives every `route_changed` sentence.
//
//   const live = useLiveUpdates({ route: liveRouteFromQuery(query) });
//   <LiveStatus live={live} />
//
// The connection reads "Live", "Reconnecting" or "Offline" in words, beside a circle
// whose shape also differs (filled, half, hollow), so colour is never the only cue.
// Each route change is shown and announced once, spoken when the user's switch is on
// and vibrated where supported (lib/announce.ts).

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { announceChange, canSpeak, useSpeakPreference } from "@/lib/announce";
import type { LiveStatus as Status, LiveUpdates } from "@/lib/live";
import { formatDistance, formatMinutes } from "@/lib/report-format";
import CheckField from "./ui/check-field";
import styles from "./live-status.module.css";

const LABEL: Record<Status, string> = {
  live: "Live",
  reconnecting: "Reconnecting",
  offline: "Offline"
};

const DETAIL: Record<Status, string> = {
  live: "Route changes arrive as they happen.",
  reconnecting: "Trying to reconnect. Your routes stay as last checked.",
  offline: "Live updates are paused. Your routes stay as last checked."
};

/** Filled when live, half when reconnecting, hollow when offline. Decorative. */
function StatusGlyph({ status }: { status: Status }) {
  return (
    <svg className={styles.glyph} width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="6" fill={status === "live" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2" />
      {status === "reconnecting" && <path d="M8 2a6 6 0 0 0 0 12z" fill="currentColor" />}
    </svg>
  );
}

/** The connection state alone, for headers. Changes are announced politely. */
export function LiveBadge({ status }: { status: Status }) {
  return (
    <p className={styles.badge} role="status" title={DETAIL[status]}>
      <StatusGlyph status={status} />
      <span className="sr-only">Live updates: </span>
      {LABEL[status]}
    </p>
  );
}

const noSubscribe = () => () => undefined;

/** The speech switch, a native checkbox. The choice is kept on this device (`openpath.speak`). */
export function SpeakToggle() {
  const [speakOn, setSpeakOn] = useSpeakPreference();
  const supported = useSyncExternalStore(noSubscribe, canSpeak, () => true);
  return (
    <div className={styles.speak}>
      <CheckField id="speak-route-changes" label="Speak route changes aloud" checked={speakOn} onChange={setSpeakOn} />
      {!supported && (
        <p className={styles.detail}>This browser cannot speak. Changes still appear here and screen readers read them.</p>
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
    <div className={message ? `${styles.announcer} ${styles.hasMessage}` : styles.announcer}>
      <p aria-live="assertive" aria-atomic="true" className={styles.message}>
        {message ? `Route update: ${message}` : ""}
      </p>
      {message && summary && summary.possible && (
        <p className={styles.detail}>
          The best route now takes about {formatMinutes(summary.eta_min)} over {formatDistance(summary.distance_m)}.
        </p>
      )}
    </div>
  );
}

/** Connection line, speech switch and announcer together: what a planner page mounts. */
export default function LiveStatus({ live }: { live: LiveUpdates }) {
  return (
    <section className={styles.live} aria-label="Live updates">
      <p className={styles.line} role="status">
        <StatusGlyph status={live.status} />
        <span>
          <strong>{LABEL[live.status]}.</strong> {DETAIL[live.status]}
        </span>
      </p>
      <SpeakToggle />
      <RouteChangeAnnouncer live={live} />
    </section>
  );
}
