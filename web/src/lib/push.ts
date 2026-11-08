// Push alerts in the browser (OP-306): what this device can do, the service worker,
// and the push subscription the API (OP-304) sends saved-route alerts to.
//
// - Ask for notification permission only from a person's action (a button press).
// - The anonymous id is the shared one from client-id.ts; there is never a second id.
// - iPhone and iPad show web push only in an app added to the home screen
//   (iOS 16.4 and later), so Safari in a tab is "needs_install", not a failure.
import type { components } from "./api-types";
import { requestJson } from "./api-request";
import { getAnonymousClientId } from "./client-id";

type Schemas = components["schemas"];
export type PushConfig = Schemas["PublicKeyOut"];
type SubscribeIn = Schemas["SubscribeIn"];
type SubscribeOut = Schemas["SubscribeOut"];
type TestOut = Schemas["TestOut"];

export const SERVICE_WORKER_URL = "/sw.js";

/** What this browser can do with alerts, before asking the server or the person. */
export type PushSupport = "supported" | "unsupported" | "needs_install" | "needs_ios_update";

/** The alerts state the pages show, in the order they are decided. */
export type AlertsState =
  | "checking"
  | "server_unreachable"
  | "server_off"
  | Exclude<PushSupport, "supported">
  | "denied"
  | "off"
  | "on";

type StandaloneNavigator = Navigator & { standalone?: boolean };

/** iPhone, iPad or iPod. iPadOS reports itself as a Mac, so touch points tell them apart. */
export function isIosDevice(): boolean {
  const agent = navigator.userAgent;
  return /iPhone|iPad|iPod/.test(agent) || (/Macintosh/.test(agent) && navigator.maxTouchPoints > 1);
}

/** True when the page runs as an app opened from the home screen. */
export function isStandalone(): boolean {
  return (
    window.matchMedia?.("(display-mode: standalone)").matches === true ||
    (navigator as StandaloneNavigator).standalone === true
  );
}

export function pushSupport(): PushSupport {
  if (typeof window === "undefined") return "unsupported";
  const ios = isIosDevice();
  if (ios && !isStandalone()) return "needs_install";
  const capable = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (!capable) return ios ? "needs_ios_update" : "unsupported";
  return "supported";
}

/** The steps that make alerts possible on iPhone and iPad. */
export const IOS_STEPS = "Tap Share, then Add to Home Screen, then open OpenPath from your home screen.";

let configRequest: Promise<PushConfig> | null = null;

/**
 * GET /api/push/public-key: the VAPID key and whether the server can send at all.
 * One request per page visit; a failed one is forgotten so the next call retries.
 */
export function getPushConfig(): Promise<PushConfig> {
  configRequest ??= requestJson<PushConfig>("/api/push/public-key").catch((error: unknown) => {
    configRequest = null;
    throw error;
  });
  return configRequest;
}

/** The config, or null when the API could not be reached. */
export async function getPushConfigOrNull(): Promise<PushConfig | null> {
  try {
    return await getPushConfig();
  } catch {
    return null;
  }
}

/** The VAPID key the API gives (base64url) as the bytes pushManager.subscribe takes. */
export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

/** Registers /sw.js for the whole site and waits until it is active. */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  await navigator.serviceWorker.register(SERVICE_WORKER_URL, { scope: "/" });
  return navigator.serviceWorker.ready;
}

/** The push subscription this browser holds for OpenPath, if any. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== "supported") return null;
  const registration = await navigator.serviceWorker.getRegistration("/");
  return registration ? registration.pushManager.getSubscription() : null;
}

/** Decides the state the alerts page and the save flow show. */
export async function readAlertsState(config: PushConfig | null): Promise<Exclude<AlertsState, "checking">> {
  if (!config) return "server_unreachable";
  if (!config.push_enabled || !config.public_key) return "server_off";
  const support = pushSupport();
  if (support !== "supported") return support;
  if (Notification.permission === "denied") return "denied";
  if (Notification.permission !== "granted") return "off";
  const subscription = await currentSubscription();
  return subscription && sameKey(subscription, config.public_key) ? "on" : "off";
}

function sameKey(subscription: PushSubscription, publicKey: string): boolean {
  const current = subscription.options.applicationServerKey;
  if (!current) return true; // Some browsers do not report it; trust the subscription.
  const expected = urlBase64ToUint8Array(publicKey);
  const actual = new Uint8Array(current);
  return actual.length === expected.length && actual.every((byte, index) => byte === expected[index]);
}

/** POST /api/push/subscribe with PushSubscription.toJSON(). Also refreshes a stored one. */
export async function postSubscription(subscription: PushSubscription): Promise<SubscribeOut> {
  const json = subscription.toJSON();
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!json.endpoint || !p256dh || !auth) throw new Error("This browser returned an incomplete push subscription.");
  const body: SubscribeIn = {
    client_id: getAnonymousClientId(),
    subscription: { endpoint: json.endpoint, keys: { p256dh, auth } }
  };
  return requestJson<SubscribeOut>("/api/push/subscribe", { method: "POST", body });
}

export type TurnOnResult = "on" | "denied" | "dismissed";

/**
 * Turns alerts on for this device. Call it straight from a click or key press:
 * the permission request is the first thing it does, which Safari requires.
 */
export async function turnOnAlerts(publicKey: string): Promise<TurnOnResult> {
  const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  if (permission === "denied") return "denied";
  if (permission !== "granted") return "dismissed";
  const registration = await registerServiceWorker();
  let subscription = await registration.pushManager.getSubscription();
  if (subscription && !sameKey(subscription, publicKey)) {
    // The server's key changed: an old subscription would never receive anything.
    await subscription.unsubscribe();
    subscription = null;
  }
  subscription ??= await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey)
  });
  await postSubscription(subscription);
  return "on";
}

/**
 * Turns alerts off for this device. The browser drops the subscription at once; the
 * API removes its copy the next time a push to it fails (404 or 410), as OP-304 does.
 */
export async function turnOffAlerts(): Promise<void> {
  const subscription = await currentSubscription();
  if (subscription) await subscription.unsubscribe();
}

/** Sends this device's subscription to the API again, so a reset server still knows it. */
export async function refreshSubscription(): Promise<boolean> {
  const subscription = await currentSubscription();
  if (!subscription) return false;
  await postSubscription(subscription);
  return true;
}

/** POST /api/push/test: one test alert to every subscription of this client. */
export function sendTestAlert(): Promise<TestOut> {
  return requestJson<TestOut>("/api/push/test", { method: "POST", body: { client_id: getAnonymousClientId() } });
}
