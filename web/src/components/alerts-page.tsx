"use client";

// OP-306: the Alerts page. Saved routes come first (their status is what matters
// for safety), then the alerts setting for this device, in plain words:
// on, off, blocked, not supported, or "Add to Home Screen" on iPhone and iPad.

import { useCallback, useEffect, useRef, useState } from "react";
import Button, { ButtonLink } from "./ui/button";
import HazardMark, { HazardShape } from "./ui/hazard-mark";
import Panel from "./ui/panel";
import SiteFooter from "./ui/site-footer";
import SiteHeader from "./ui/site-header";
import StatusLine from "./ui/status-line";
import TimeAgo from "./ui/time-ago";
import { ApiError } from "@/lib/api-request";
import { isTravelProfile, profileLabel } from "@/lib/planner-types";
import {
  AlertsState,
  IOS_STEPS,
  PushConfig,
  getPushConfigOrNull,
  readAlertsState,
  refreshSubscription,
  sendTestAlert,
  turnOffAlerts,
  turnOnAlerts
} from "@/lib/push";
import { SavedRoute, listSavedRoutes, removeSavedRoute, resaveRoute, savedRouteStatusText } from "@/lib/saved-routes";
import styles from "./alerts-page.module.css";

const STATUS_SHAPE: Record<SavedRoute["status"], HazardShape> = {
  ok: "clear",
  changed: "caution",
  impossible: "blocked"
};

const DEVICE_STATE_ID = "alerts-device-state";

function profileText(profile: string): string {
  return isTravelProfile(profile) ? profileLabel(profile) : profile;
}

function listErrorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) return "Saved routes are not available on this server right now. Reload the page later.";
  return "Your saved routes could not be loaded. Check your connection, then reload the page.";
}

// Routes that need attention first: no route, then changed, then clear; oldest first within each.
const STATUS_ORDER: Record<SavedRoute["status"], number> = { impossible: 0, changed: 1, ok: 2 };

function byAttention(routes: SavedRoute[]): SavedRoute[] {
  return [...routes].sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.id - b.id);
}

function countText(count: number): string {
  if (count === 0) return "No saved routes yet. Plan a trip and choose Save this route.";
  return count === 1 ? "1 saved route." : `${count} saved routes.`;
}

type Removed = { route: SavedRoute; restoring: boolean };

export default function AlertsPage() {
  const [routes, setRoutes] = useState<SavedRoute[] | null>(null);
  const [listError, setListError] = useState("");
  const [now, setNow] = useState(() => new Date());
  const [confirming, setConfirming] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [removed, setRemoved] = useState<Removed | null>(null);
  const [listMessage, setListMessage] = useState("");
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [alerts, setAlerts] = useState<AlertsState>("checking");
  const [deviceMessage, setDeviceMessage] = useState("");
  const [deviceBusy, setDeviceBusy] = useState(false);
  const [focusId, setFocusId] = useState<string | null>(null);
  const live = useRef(true);

  const loadRoutes = useCallback(async () => {
    try {
      const next = await listSavedRoutes();
      if (!live.current) return;
      setRoutes(byAttention(next));
      setListError("");
      setNow(new Date());
    } catch (error) {
      if (!live.current) return;
      setListError(listErrorText(error));
      setRoutes((current) => current ?? []);
    }
  }, []);

  useEffect(() => {
    live.current = true;
    void loadRoutes();
    void (async () => {
      const next = await getPushConfigOrNull();
      const state = await readAlertsState(next).catch((): AlertsState => "unsupported");
      if (!live.current) return;
      setConfig(next);
      setAlerts(state);
      // Keep the server's copy of this device's subscription current (a reset
      // database would otherwise forget it). Quiet: the page already says "on".
      if (state === "on") void refreshSubscription().catch(() => undefined);
    })();
    return () => {
      live.current = false;
    };
  }, [loadRoutes]);

  // Fresh status when someone comes back to the page or an alert arrives.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void loadRoutes();
    };
    const onMessage = (event: MessageEvent<unknown>) => {
      const data = event.data;
      if (data && typeof data === "object" && (data as { type?: unknown }).type === "openpath-push") void loadRoutes();
    };
    const tick = window.setInterval(() => setNow(new Date()), 60_000);
    document.addEventListener("visibilitychange", onVisible);
    navigator.serviceWorker?.addEventListener("message", onMessage);
    return () => {
      window.clearInterval(tick);
      document.removeEventListener("visibilitychange", onVisible);
      navigator.serviceWorker?.removeEventListener("message", onMessage);
    };
  }, [loadRoutes]);

  useEffect(() => {
    if (!focusId) return;
    document.getElementById(focusId)?.focus();
    setFocusId(null);
  }, [focusId, routes, alerts, removed, confirming]);

  // ---------------------------------------------------------------- saved routes

  function askRemove(route: SavedRoute) {
    setConfirming(route.id);
    setFocusId(`saved-${route.id}-keep`);
  }

  function keep(route: SavedRoute) {
    setConfirming(null);
    setFocusId(`saved-${route.id}-remove`);
  }

  async function remove(route: SavedRoute) {
    if (removing !== null) return;
    setRemoving(route.id);
    try {
      await removeSavedRoute(route);
      setRoutes((current) => (current ?? []).filter((item) => item.id !== route.id));
      setRemoved({ route, restoring: false });
      setListMessage(`Removed ${route.name}. Alerts for it have stopped.`);
      setFocusId("saved-undo");
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        // Already gone (another tab removed it): show the list as it is now.
        setListMessage(`${route.name} was already removed.`);
        await loadRoutes();
        setFocusId("saved-title");
      } else {
        setListMessage(`${route.name} could not be removed. Check your connection, then choose Remove again.`);
        setFocusId(`saved-${route.id}-remove`);
      }
    } finally {
      setConfirming(null);
      setRemoving(null);
    }
  }

  async function undo() {
    if (!removed || removed.restoring) return;
    setRemoved({ ...removed, restoring: true });
    try {
      const restored = await resaveRoute(removed.route);
      setRoutes((current) => byAttention([...(current ?? []), restored]));
      setRemoved(null);
      setListMessage(`${restored.name} is saved again.`);
      setFocusId(`saved-${restored.id}-name`);
    } catch {
      setRemoved({ ...removed, restoring: false });
      setListMessage(`${removed.route.name} could not be saved again. Check your connection, then choose Undo again.`);
    }
  }

  // ---------------------------------------------------------------- this device

  async function turnOn() {
    if (deviceBusy || !config?.public_key) return;
    setDeviceBusy(true);
    setDeviceMessage("Turning on alerts.");
    try {
      const result = await turnOnAlerts(config.public_key);
      if (result === "on") {
        setAlerts("on");
        setDeviceMessage("");
        setFocusId(DEVICE_STATE_ID);
      } else if (result === "denied") {
        setAlerts("denied");
        setDeviceMessage("");
        setFocusId(DEVICE_STATE_ID);
      } else {
        setDeviceMessage("Alerts are still off because the browser did not get permission. Choose Turn on alerts to try again.");
      }
    } catch {
      setDeviceMessage("Alerts could not be turned on. Check your connection, then choose Turn on alerts again.");
    } finally {
      setDeviceBusy(false);
    }
  }

  async function turnOff() {
    if (deviceBusy) return;
    setDeviceBusy(true);
    setDeviceMessage("Turning off alerts.");
    try {
      await turnOffAlerts();
      setAlerts("off");
      setDeviceMessage("");
      setFocusId(DEVICE_STATE_ID);
    } catch {
      setDeviceMessage("Alerts could not be turned off. Reload the page, then choose Turn off alerts again.");
    } finally {
      setDeviceBusy(false);
    }
  }

  async function test() {
    if (deviceBusy) return;
    setDeviceBusy(true);
    setDeviceMessage("Sending a test alert.");
    try {
      let result: Awaited<ReturnType<typeof sendTestAlert>>;
      try {
        result = await sendTestAlert();
      } catch (error) {
        // The server lost this device's subscription: send it again and retry once.
        if (!(error instanceof ApiError && error.status === 404) || !(await refreshSubscription())) throw error;
        result = await sendTestAlert();
      }
      setDeviceMessage(
        result.sent > 0
          ? "Test alert sent. It should appear on this device in a few seconds."
          : "The test alert did not reach this device. Turn alerts off and on again, then send another test."
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 503) {
        setAlerts("server_off");
        setDeviceMessage("");
      } else if (error instanceof ApiError && error.status === 404) {
        setDeviceMessage("The server has no alert subscription for this device. Turn alerts off and on again.");
      } else {
        setDeviceMessage("The test alert could not be sent. Check your connection, then try again.");
      }
    } finally {
      setDeviceBusy(false);
    }
  }

  // ---------------------------------------------------------------- render

  const listStatus = routes === null ? "Loading your saved routes." : listError ? listError : countText(routes.length);

  return (
    <div className="shell">
      <SiteHeader current="alerts" />

      <main id="main" className="page">
        <div className="page-main">
          <h1 className="page-title">Alerts</h1>
          <p className="lead">Your saved routes, checked again whenever conditions change.</p>

          <section aria-labelledby="saved-title" className={styles.saved}>
            <h2 id="saved-title" tabIndex={-1}>
              Saved routes
            </h2>
            <StatusLine>{listStatus}</StatusLine>
            {/* Always in the DOM, so "Removed Work." and "Work is saved again." are announced. */}
            <div className={styles.notice}>
              <StatusLine>{listMessage}</StatusLine>
              {removed && (
                <Button id="saved-undo" variant="text" className={styles.undo} onClick={() => void undo()} aria-disabled={removed.restoring || undefined}>
                  Undo<span className="sr-only"> removing {removed.route.name}</span>
                </Button>
              )}
            </div>

            {routes !== null && routes.length === 0 && !listError && (
              <div className={styles.empty}>
                <ButtonLink variant="secondary" href="/">
                  Plan a trip
                </ButtonLink>
              </div>
            )}

            {routes !== null && routes.length > 0 && (
              <ul className={styles.routes}>
                {routes.map((route) => (
                  <li key={route.id} className={styles.route} aria-labelledby={`saved-${route.id}-name`}>
                    <h3 className={styles.name} id={`saved-${route.id}-name`} tabIndex={-1}>
                      {route.name}
                    </h3>
                    <HazardMark shape={STATUS_SHAPE[route.status]} label={savedRouteStatusText(route)} />
                    <p className={styles.meta}>
                      {profileText(route.profile)}. <TimeAgo iso={route.last_checked_at} now={now} />.
                    </p>
                    {confirming === route.id ? (
                      <div
                        className={styles.confirm}
                        role="group"
                        aria-labelledby={`saved-${route.id}-ask`}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") keep(route);
                        }}
                      >
                        <p id={`saved-${route.id}-ask`}>Remove {route.name}? Alerts for it stop.</p>
                        <div className={styles.actions}>
                          <Button
                            variant="secondary"
                            onClick={() => void remove(route)}
                            aria-disabled={removing === route.id || undefined}
                          >
                            Remove route<span className="sr-only"> {route.name}</span>
                          </Button>
                          <Button
                            id={`saved-${route.id}-keep`}
                            variant="text"
                            onClick={() => keep(route)}
                          >
                            Keep it
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className={styles.actions}>
                        <ButtonLink variant="secondary" href={route.url}>
                          Open route<span className="sr-only"> {route.name}</span>
                        </ButtonLink>
                        <Button id={`saved-${route.id}-remove`} variant="text" onClick={() => askRemove(route)}>
                          Remove<span className="sr-only"> {route.name}</span>
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <div className="page-side">
          <Panel aria-labelledby="device-title">
            <h2 id="device-title">Alerts on this device</h2>
            <DeviceState state={alerts} />
            <DeviceActions
              state={alerts}
              busy={deviceBusy}
              onTurnOn={() => void turnOn()}
              onTurnOff={() => void turnOff()}
              onTest={() => void test()}
            />
            <StatusLine className={styles.deviceStatus}>{deviceMessage}</StatusLine>
          </Panel>
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}

/** The alerts state in plain words. It takes focus after the state changes. */
function DeviceState({ state }: { state: AlertsState }) {
  let first: string;
  let rest: string[] = [];
  switch (state) {
    case "checking":
      first = "Checking whether this device can show alerts.";
      break;
    case "server_unreachable":
      first = "Alerts cannot be checked right now.";
      rest = ["OpenPath could not reach the server. Reload the page to try again."];
      break;
    case "server_off":
      first = "Alerts are not set up on this server yet.";
      rest = ["Your saved routes still show their status on this page."];
      break;
    case "needs_install":
      first = "Add to Home Screen to get alerts.";
      rest = [
        `On iPhone and iPad, alerts work only when OpenPath is opened from the home screen. ${IOS_STEPS}`,
        "The home screen app keeps its own list, so save your routes again there."
      ];
      break;
    case "needs_ios_update":
      first = "Alerts need iOS 16.4 or later.";
      rest = ["Update iOS, then open OpenPath from your home screen."];
      break;
    case "unsupported":
      first = "This browser cannot show alerts.";
      rest = ["Your saved routes still show their status on this page."];
      break;
    case "denied":
      first = "Alerts are blocked for this site.";
      rest = ["Allow notifications for this site in your browser's settings, then reload this page."];
      break;
    case "off":
      first = "Alerts are off on this device.";
      rest = ["Your saved routes are kept and still show their status on this page."];
      break;
    case "on":
      first = "Alerts are on for this device.";
      rest = ["You'll get an alert when a saved route closes or gets 20% slower."];
      break;
  }
  return (
    <div id={DEVICE_STATE_ID} className={styles.state} tabIndex={-1}>
      <p>
        <strong>{first}</strong>
      </p>
      {rest.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </div>
  );
}

type ActionsProps = {
  state: AlertsState;
  busy: boolean;
  onTurnOn: () => void;
  onTurnOff: () => void;
  onTest: () => void;
};

function DeviceActions({ state, busy, onTurnOn, onTurnOff, onTest }: ActionsProps) {
  const disabled = busy || undefined;
  if (state === "off") {
    return (
      <div className={styles.deviceActions}>
        <Button variant="primary" onClick={onTurnOn} aria-disabled={disabled}>
          Turn on alerts
        </Button>
        <p className="note">Your browser will ask to allow notifications from OpenPath.</p>
      </div>
    );
  }
  if (state === "on") {
    return (
      <div className={styles.deviceActions}>
        <Button variant="secondary" onClick={onTest} aria-disabled={disabled}>
          Send me a test alert
        </Button>
        <Button variant="text" onClick={onTurnOff} aria-disabled={disabled}>
          Turn off alerts
        </Button>
      </div>
    );
  }
  return null;
}
