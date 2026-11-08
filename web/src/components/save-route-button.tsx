"use client";

// OP-306: "Save this route" on a route card, and alerts for it, in two taps:
//   1. "Save this route" opens a small form with the name filled in.
//   2. "Save and turn on alerts" saves the trip (POST /api/saved-routes) and asks the
//      browser for permission, or "Save route" when alerts are already on.
// Saving without alerts offers "Turn on alerts for this route" afterwards. Where the
// device cannot show alerts (iPhone in Safari, a server without keys), it says why.

import Link from "next/link";
import { FormEvent, KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import Button from "./ui/button";
import Field from "./ui/field";
import StatusLine from "./ui/status-line";
import type { PlannerQuery } from "@/lib/planner-types";
import {
  AlertsState,
  IOS_STEPS,
  PushConfig,
  TurnOnResult,
  getPushConfigOrNull,
  readAlertsState,
  turnOnAlerts
} from "@/lib/push";
import { NAME_MAX, SavedRoute, saveErrorText, saveTrip } from "@/lib/saved-routes";
import styles from "./save-route-button.module.css";

type Props = {
  query: PlannerQuery;
  /** The route's rank: the API watches a trip's recommended route, so alternates say so. */
  rank: number;
};

type Phase = "closed" | "editing" | "saving" | "saved";
type AlertOutcome = TurnOnResult | "failed" | null;

function promise(rank: number): string {
  return rank === 1
    ? "You'll get an alert if this route closes or gets 20% slower."
    : "OpenPath watches the recommended route for this trip and alerts you if it closes or gets 20% slower.";
}

/** The sentence after "Saved." for each alerts state. */
function savedText(name: string, rank: number, alerts: AlertsState, outcome: AlertOutcome): string {
  if (alerts === "on") return promise(rank);
  if (outcome === "dismissed") return `${name} is on your Alerts page. Alerts are still off because the browser did not get permission.`;
  if (outcome === "failed") return `${name} is on your Alerts page. Alerts could not be turned on just now.`;
  switch (alerts) {
    case "denied":
      return `${name} is on your Alerts page. Alerts are blocked for this site; allow notifications in your browser's site settings to get them.`;
    case "needs_install":
      return `${name} is on your Alerts page. Add to Home Screen to get alerts: ${IOS_STEPS.charAt(0).toLowerCase()}${IOS_STEPS.slice(1)}`;
    case "needs_ios_update":
      return `${name} is on your Alerts page. Alerts need iOS 16.4 or later.`;
    case "unsupported":
      return `${name} is on your Alerts page. This browser cannot show alerts, so check the route there before you leave.`;
    case "server_off":
      return `${name} is on your Alerts page. Alerts are not set up on this server yet, so check the route there before you leave.`;
    case "server_unreachable":
      return `${name} is on your Alerts page. Alerts could not be checked just now.`;
    default:
      return `${name} is on your Alerts page. Alerts are off on this device.`;
  }
}

/** A note in the form when this device cannot turn alerts on; empty when it can. */
function formNote(alerts: AlertsState): string {
  switch (alerts) {
    case "denied":
      return "Alerts are blocked for this site, so the route is kept on your Alerts page only. Allow notifications in your browser's site settings to get alerts.";
    case "needs_install":
      return `Add to Home Screen to get alerts. ${IOS_STEPS}`;
    case "needs_ios_update":
      return "Alerts need iOS 16.4 or later. The route is kept on your Alerts page.";
    case "unsupported":
      return "This browser cannot show alerts. The route is kept on your Alerts page.";
    case "server_off":
      return "Alerts are not set up on this server yet. The route is kept on your Alerts page.";
    default:
      return "";
  }
}

function cleanName(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ");
}

export default function SaveRouteButton({ query, rank }: Props) {
  const ids = useId();
  const inputId = `${ids}-name`;
  const triggerId = `${ids}-trigger`;
  const inputRef = useRef<HTMLInputElement>(null);
  const resultRef = useRef<HTMLParagraphElement>(null);
  const [phase, setPhase] = useState<Phase>("closed");
  const [name, setName] = useState(query.to.name.slice(0, NAME_MAX));
  const [nameError, setNameError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [alerts, setAlerts] = useState<AlertsState>("checking");
  const [outcome, setOutcome] = useState<AlertOutcome>(null);
  const [saved, setSaved] = useState<SavedRoute | null>(null);
  const [progress, setProgress] = useState("");
  const [focusTarget, setFocusTarget] = useState<"input" | "result" | "trigger" | null>(null);

  // Learn what alerts can do here before the first tap, so the buttons never change
  // under a finger. The config request is shared by every route on the page.
  useEffect(() => {
    let live = true;
    void (async () => {
      const next = await getPushConfigOrNull();
      const state = await readAlertsState(next).catch((): AlertsState => "unsupported");
      if (!live) return;
      setConfig(next);
      setAlerts(state);
    })();
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (!focusTarget) return;
    const target =
      focusTarget === "trigger" ? document.getElementById(triggerId) : { input: inputRef, result: resultRef }[focusTarget].current;
    target?.focus();
    setFocusTarget(null);
  }, [focusTarget, phase, triggerId]);

  const canTurnOn = alerts === "off" && Boolean(config?.public_key);
  const busy = phase === "saving" || progress !== "";

  function open() {
    setPhase("editing");
    setFocusTarget("input");
  }

  function cancel() {
    if (phase === "saving") return;
    setPhase("closed");
    setNameError("");
    setSaveError("");
    setFocusTarget("trigger");
  }

  function onKeyDown(event: KeyboardEvent<HTMLFormElement>) {
    if (event.key === "Escape" && phase === "editing") {
      event.preventDefault();
      cancel();
    }
  }

  function save(withAlerts: boolean) {
    if (busy) return;
    const trimmed = cleanName(name);
    if (!trimmed) {
      setNameError("Enter a name for this route, such as Work.");
      setFocusTarget("input");
      return;
    }
    setNameError("");
    setSaveError("");
    setPhase("saving");
    // The permission request starts first, inside the button press, as browsers require.
    const alertsTask: Promise<AlertOutcome> | null =
      withAlerts && config?.public_key ? turnOnAlerts(config.public_key).catch((): AlertOutcome => "failed") : null;
    void (async () => {
      let route: SavedRoute;
      try {
        route = await saveTrip(query, trimmed);
      } catch (error) {
        const result = alertsTask ? await alertsTask : null;
        if (result === "on") setAlerts("on");
        if (result === "denied") setAlerts("denied");
        setSaveError(saveErrorText(error));
        setPhase("editing");
        setFocusTarget("input");
        return;
      }
      const result = alertsTask ? await alertsTask : null;
      if (result === "on") setAlerts("on");
      if (result === "denied") setAlerts("denied");
      setOutcome(result);
      setSaved(route);
      setPhase("saved");
      setFocusTarget("result");
    })();
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save(canTurnOn);
  }

  function turnOn() {
    if (busy || !config?.public_key) return;
    setProgress("Turning on alerts.");
    void turnOnAlerts(config.public_key)
      .catch((): AlertOutcome => "failed")
      .then((result) => {
        if (result === "on") setAlerts("on");
        if (result === "denied") setAlerts("denied");
        setOutcome(result);
        setProgress("");
        setFocusTarget("result");
      });
  }

  if (phase === "closed") {
    return (
      <div className={styles.save}>
        <Button id={triggerId} variant="secondary" block onClick={open}>
          Save this route<span className="sr-only">, route {rank}</span>
        </Button>
      </div>
    );
  }

  if (phase === "saved" && saved) {
    const offerTurnOn = alerts === "off" && Boolean(config?.public_key);
    return (
      <div className={styles.save}>
        <p className={styles.result} ref={resultRef} tabIndex={-1}>
          <strong>Saved.</strong> {savedText(saved.name, rank, alerts, outcome)}
        </p>
        <div className={styles.buttons}>
          {offerTurnOn && (
            <Button variant="primary" onClick={turnOn} aria-disabled={busy || undefined}>
              Turn on alerts for this route
            </Button>
          )}
          <Link className={styles.link} href="/alerts">
            See your saved routes
          </Link>
        </div>
        <StatusLine>{progress}</StatusLine>
      </div>
    );
  }

  const saving = phase === "saving";
  const note = canTurnOn ? "" : formNote(alerts);
  return (
    <div className={styles.save}>
      <form className={styles.form} onSubmit={onSubmit} onKeyDown={onKeyDown} noValidate aria-label={`Save route ${rank}`}>
        <Field
          id={inputId}
          label="Name for this route"
          help="For example, Work or Home."
          error={nameError}
          inputProps={{
            ref: inputRef,
            value: name,
            maxLength: NAME_MAX,
            autoComplete: "off",
            enterKeyHint: "done",
            onChange: (event) => setName(event.target.value)
          }}
        />
        {note && <p className={styles.note}>{note}</p>}
        <div className={styles.buttons}>
          <Button type="submit" variant="primary" aria-disabled={saving || undefined}>
            {canTurnOn ? "Save and turn on alerts" : "Save route"}
          </Button>
          {canTurnOn && (
            <Button variant="secondary" onClick={() => save(false)} aria-disabled={saving || undefined}>
              Save without alerts
            </Button>
          )}
          <Button variant="text" onClick={cancel} aria-disabled={saving || undefined}>
            Cancel
          </Button>
        </div>
        {saveError && <p className={styles.error}>{saveError}</p>}
      </form>
      <StatusLine>{saving ? `Saving ${cleanName(name)}.` : ""}</StatusLine>
    </div>
  );
}
