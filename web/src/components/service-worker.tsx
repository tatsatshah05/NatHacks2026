"use client";

import { useEffect } from "react";

// OP-410: register the service worker (public/sw.js) on every page, after the page has
// loaded, so it never competes with the page's own requests. The worker shows push
// alerts (OP-306) and serves public/offline.html when a navigation fails offline. It
// caches nothing else: routes and conditions always come from the network.
//
// The URL is the same as SERVICE_WORKER_URL in lib/push.ts, repeated here so the root
// layout does not pull the alerts client into every page's bundle.
const SERVICE_WORKER_URL = "/sw.js";

export default function ServiceWorkerRegistration() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const register = () => {
      navigator.serviceWorker.register(SERVICE_WORKER_URL, { scope: "/" }).catch(() => {
        // No worker: pages still work, and alerts say so when they are turned on.
      });
    };
    if (document.readyState === "complete") {
      register();
      return;
    }
    window.addEventListener("load", register, { once: true });
    return () => window.removeEventListener("load", register);
  }, []);
  return null;
}
