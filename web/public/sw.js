/*
  OpenPath service worker: push alerts for saved routes (OP-306) and one offline page
  for navigations (OP-410). Nothing else.

  - push: shows the alert the API sent (cloud/app/push.py notice_payload):
    { title, body, url, tag, saved_route_id, status, eta_min }.
  - notificationclick: opens the saved route (data.url) in an open OpenPath tab,
    or in a new one.
  - fetch: only for navigations (a page load or a link), and only when the network
    fails: then it answers with /offline.html, which says so and reloads itself when
    the connection returns. Pages, routes, conditions and API answers are never
    cached, so an old route can never be shown as if it were current.
*/

const FALLBACK_URL = "/alerts";
const ICON = "/icons/icon-192.png";
const BADGE = "/icons/badge-96.png";
const OFFLINE_CACHE = "openpath-offline-v1";
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(OFFLINE_CACHE)
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: "reload" })))
      .catch(() => undefined) // the worker still installs; alerts work without the page
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      // Older offline caches go; nothing else of ours is cached.
      const names = await caches.keys();
      await Promise.all(names.filter((name) => name.startsWith("openpath-") && name !== OFFLINE_CACHE).map((name) => caches.delete(name)));
      // The browser starts the page request in parallel with this worker.
      if (self.registration.navigationPreload) {
        await self.registration.navigationPreload.enable().catch(() => undefined);
      }
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || request.mode !== "navigate") return;
  if (new URL(request.url).origin !== self.location.origin) return;
  event.respondWith(
    (async () => {
      try {
        const preloaded = await event.preloadResponse;
        if (preloaded) return preloaded;
        return await fetch(request);
      } catch (error) {
        const offline = await caches.match(OFFLINE_URL);
        if (offline) return offline;
        throw error;
      }
    })()
  );
});

/** Only paths on this site: a payload can never send someone elsewhere. */
function sameSiteUrl(value) {
  if (typeof value !== "string" || value === "") return new URL(FALLBACK_URL, self.location.origin).href;
  try {
    const url = new URL(value, self.location.origin);
    if (url.origin === self.location.origin) return url.href;
  } catch {
    // Not a URL: use the fallback below.
  }
  return new URL(FALLBACK_URL, self.location.origin).href;
}

function readPayload(event) {
  if (!event.data) return {};
  try {
    const data = event.data.json();
    return data && typeof data === "object" ? data : {};
  } catch {
    return { body: event.data.text() };
  }
}

self.addEventListener("push", (event) => {
  const data = readPayload(event);
  const title = typeof data.title === "string" && data.title ? data.title : "OpenPath";
  const tag = typeof data.tag === "string" && data.tag ? data.tag : undefined;
  const options = {
    body: typeof data.body === "string" ? data.body : "",
    icon: ICON,
    badge: BADGE,
    lang: "en-CA",
    data: {
      url: sameSiteUrl(data.url),
      saved_route_id: typeof data.saved_route_id === "number" ? data.saved_route_id : null,
      status: typeof data.status === "string" ? data.status : null
    }
  };
  if (tag) {
    // A newer alert for the same route replaces the older one, and still alerts.
    options.tag = tag;
    options.renotify = true;
  }
  event.waitUntil(
    Promise.all([
      self.registration.showNotification(title, options),
      // Open OpenPath pages (the alerts page) refresh their saved routes.
      self.clients
        .matchAll({ type: "window", includeUncontrolled: true })
        .then((windows) => windows.forEach((client) => client.postMessage({ type: "openpath-push", status: options.data.status })))
    ])
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = sameSiteUrl(event.notification.data && event.notification.data.url);
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const exact = windows.find((client) => client.url === target);
      if (exact) {
        await exact.focus().catch(() => undefined);
        return;
      }
      const open = windows.find((client) => new URL(client.url).origin === self.location.origin);
      if (open) {
        // Focus first, while the click still allows it; a failed focus is not fatal.
        await open.focus().catch(() => undefined);
        try {
          if (await open.navigate(target)) return;
        } catch {
          // navigate() needs a tab this worker controls; open a new one instead.
        }
      }
      await self.clients.openWindow(target);
    })()
  );
});
