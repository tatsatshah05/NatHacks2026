"use client";

import { useSyncExternalStore } from "react";

// OP-410: one sentence when the device is offline, in the header area of every page.
// The line is always in the page (role="status"), so the change is announced, and its
// height is reserved by globals.css (.site-status), so nothing moves when it appears.
// Pages keep whatever they had already loaded; the sentence says so.

export const OFFLINE_SENTENCE = "You are offline. Showing what was loaded before.";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

function isOffline(): boolean {
  try {
    return typeof navigator !== "undefined" && navigator.onLine === false;
  } catch {
    return false;
  }
}

// The server renders it empty; a device that is offline when the page opens fills it
// right after hydration.
const onServer = () => false;

export default function OfflineLine() {
  const offline = useSyncExternalStore(subscribe, isOffline, onServer);
  return (
    <p className="site-status" role="status">
      {offline ? OFFLINE_SENTENCE : ""}
    </p>
  );
}
