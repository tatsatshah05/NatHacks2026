/*
  OpenPath service worker (OP-306): push alerts for saved routes, nothing else.

  - push: shows the alert the API sent (cloud/app/push.py notice_payload):
    { title, body, url, tag, saved_route_id, status, eta_min }.
  - notificationclick: opens the saved route (data.url) in an open OpenPath tab,
    or in a new one.
  - No fetch handler and no cache. Routes and conditions always come from the
    network, so an alert can never show an old route as if it were current.
*/

const FALLBACK_URL = "/alerts";
const ICON = "/icons/icon-192.png";
const BADGE = "/icons/badge-96.png";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
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
