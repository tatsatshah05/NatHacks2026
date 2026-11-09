"use client";

// OP-419: the trip on the route report, with PIP, the camera guide (OP-418), inside it.
// It sits right after the route summary, so the steps, conditions and buttons the page
// already has are not touched:
//   1. "Start route" (the page's one primary action) begins the trip: the current step
//      is shown large in a polite live region and spoken, the GPS follows the walker
//      and says the next step near each turn, and a large Stop ends it. Nothing is
//      sent to the API while PIP is off.
//   2. Inside the trip, "PIP" turns the camera guide on: the privacy notice the first
//      time, then the camera, a frame every two seconds, each sentence spoken and listed
//      as PIP's. "Stop PIP" turns the camera off while the trip goes on; Stop ends both.
//   3. Without the camera, or when the service says 503, one sentence says so and the
//      steps keep being spoken, by GPS or by Next step and the step list.
// PIP describes, it does not decide: the sentence under its heading says so, always.
// Opened with `?start=1` (the planner's Start action), the panel focuses Start route and
// says "Ready to start"; it never starts the trip by itself.

import { KeyboardEvent, useEffect, useId, useState } from "react";
import CameraGlyph from "../city/camera-glyph";
import Button from "../ui/button";
import Panel from "../ui/panel";
import StatusLine from "../ui/status-line";
import { currentSentence, startRouteLabel } from "@/lib/guide-route";
import type { RouteOut, TravelProfile } from "@/lib/planner-types";
import { useSettings } from "@/lib/settings";
import { CameraState, useGuide } from "./use-guide";
import styles from "./trip-panel.module.css";

export const TRIP_TITLE = "Your trip";
export const START_HELP = "Speaks each step as you go and follows your location.";
export const READY_MESSAGE = "Ready to start. Press Start route.";

export const PIP_TITLE = "PIP, the camera guide (preview)";
export const PIP_HELP = "PIP describes what the camera sees. It does not decide for you.";
export const PIP_PROMISE = "PIP does not replace a cane or a guide dog.";
// What the API forwards to Anthropic is the frame, the profile, the steps, the distances,
// the heading, the nearby barriers and the last sentence; the position and the anonymous
// id stay with OpenPath (cloud/app/guide.py, pinned by a backend test).
export const PIP_NOTICE =
  "Your camera's view and your current route step are sent to Anthropic to be described and are not stored. " +
  "Your position stays with OpenPath and is not sent to Anthropic. People may appear in the view. Press Stop PIP at any time.";

const STATUS: Record<CameraState, string> = {
  off: "",
  starting: "Starting the camera",
  on: "PIP is on",
  denied: "Camera guidance unavailable, reading route steps",
  unavailable: "Camera guidance unavailable, reading route steps"
};

type Props = {
  route: RouteOut;
  /** How many routes the trip has: the rank is said on Start only when there are alternates. */
  routeCount: number;
  profile: TravelProfile;
  /** The trip's destination name, for "You're at the end of the route, ...". */
  destination: string;
  /** Opened from the planner's Start action: focus Start route and announce it, once. */
  ready: boolean;
};

export default function TripPanel({ route, routeCount, profile, destination, ready }: Props) {
  const ids = useId();
  const id = (name: string) => `${ids}-${name}`;
  const guide = useGuide({ route, profile, destination });
  const { speak } = useSettings();
  // Focus follows each change of state: Stop after Start, Start route after Stop, Start
  // PIP after PIP (the notice), PIP after Start PIP, Close or Stop PIP.
  const [focusTarget, setFocusTarget] = useState<string | null>(null);
  const [readyMessage, setReadyMessage] = useState("");

  useEffect(() => {
    if (!focusTarget) return;
    document.getElementById(focusTarget)?.focus();
    setFocusTarget(null);
  }, [focusTarget, guide.trip, guide.pip]);

  // From the planner's Start action: focus the button and fill the status line after it
  // exists (a live region announces only changes). The trip itself waits for a tap.
  useEffect(() => {
    if (!ready) return;
    document.getElementById(`${ids}-start`)?.focus();
    setReadyMessage(READY_MESSAGE);
  }, [ready, ids]);

  function start() {
    guide.startTrip();
    setReadyMessage("");
    setFocusTarget(id("stop"));
  }

  function stop() {
    guide.stopTrip();
    setFocusTarget(id("start"));
  }

  function pressPip() {
    // The notice the first time; after that the camera starts and focus stays on the button.
    if (guide.openPip() === "notice") setFocusTarget(id("pip-start"));
  }

  function closeNotice() {
    guide.closePip();
    setFocusTarget(id("pip"));
  }

  function startPip() {
    guide.startPip();
    setFocusTarget(id("pip"));
  }

  function stopPip() {
    guide.stopPip();
    setFocusTarget(id("pip"));
  }

  function onNoticeKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeNotice();
    }
  }

  if (guide.trip === "idle") {
    return (
      <div className={styles.start}>
        <Button id={id("start")} size="large" block onClick={start} aria-describedby={id("start-help")}>
          {startRouteLabel(route.rank, routeCount)}
        </Button>
        <p id={id("start-help")} className={styles.note}>
          {START_HELP}
        </p>
        <StatusLine>{readyMessage}</StatusLine>
      </div>
    );
  }

  const headline = currentSentence(route.steps, guide.stepIndex, guide.arrived, destination);

  return (
    <Panel aria-labelledby={id("title")}>
      <h2 id={id("title")}>{TRIP_TITLE}</h2>
      {/* The current step, large. Polite: it changes as the walker moves, and the same sentence is spoken. */}
      <p className={styles.headline} role="status">
        {headline}
      </p>
      <div className={styles.controls}>
        <Button id={id("stop")} size="large" onClick={stop}>
          Stop
        </Button>
        <div className={styles.buttons}>
          <Button variant="secondary" onClick={guide.repeatStep}>
            Repeat step
          </Button>
          <Button variant="secondary" onClick={guide.nextStep}>
            Next step
          </Button>
        </div>
      </div>
      {guide.location === "off" && <p className={styles.note}>Location is off. Press Next step as you go.</p>}

      <section className={styles.pip} aria-labelledby={id("pip-title")}>
        <h3 id={id("pip-title")}>{PIP_TITLE}</h3>
        <p id={id("pip-help")} className={styles.promise}>
          {PIP_HELP}
        </p>
        <p className={styles.note}>{PIP_PROMISE}</p>

        {guide.pip === "closed" && (
          <div className={styles.buttons}>
            <Button id={id("pip")} variant="secondary" onClick={pressPip} aria-describedby={id("pip-help")}>
              <CameraGlyph size={20} />
              PIP
            </Button>
          </div>
        )}

        {guide.pip === "notice" && (
          <div className={styles.body} onKeyDown={onNoticeKeyDown}>
            <p id={id("notice")} className={styles.notice}>
              {PIP_NOTICE}
            </p>
            <div className={styles.buttons}>
              <Button id={id("pip-start")} variant="secondary" onClick={startPip} aria-describedby={id("notice")}>
                Start PIP
              </Button>
              <Button variant="text" onClick={closeNotice}>
                Close
              </Button>
            </div>
          </div>
        )}

        {guide.pip === "guiding" && (
          <div className={styles.body}>
            <div className={styles.buttons}>
              <Button id={id("pip")} variant="secondary" onClick={stopPip}>
                <CameraGlyph size={20} />
                Stop PIP
              </Button>
            </div>
            <StatusLine>{STATUS[guide.camera]}</StatusLine>
            {guide.camera !== "denied" && (
              // The preview is for sighted and low-vision users; the transcript carries the words.
              <video ref={guide.videoRef} className={styles.video} autoPlay muted playsInline aria-hidden="true" />
            )}
          </div>
        )}
      </section>

      <h3 id={id("transcript-title")}>Said so far</h3>
      <ol className={styles.transcript} aria-labelledby={id("transcript-title")} aria-live="polite">
        {guide.lines.map((line) => (
          <li key={line.id}>
            {line.source === "camera" ? (
              <>
                <span className={styles.who}>PIP:</span> {line.text}
              </>
            ) : (
              line.text
            )}
          </li>
        ))}
      </ol>
      {!speak && <p className={styles.note}>Speech is off in Settings, so sentences appear here only.</p>}

      <h3 id={id("steps-title")}>Steps</h3>
      <p id={id("steps-help")} className={styles.note}>
        Press a step to hear it.
      </p>
      <ol className={styles.stepList} aria-labelledby={id("steps-title")} aria-describedby={id("steps-help")}>
        {route.steps.map((step, index) => {
          const current = index === guide.stepIndex;
          return (
            <li key={`${index}-${step.text}`} className={current ? styles.current : undefined}>
              <button
                type="button"
                className={`button button-text ${styles.stepButton}`}
                aria-current={current ? "step" : undefined}
                onClick={() => guide.sayStep(index)}
              >
                {step.text}
              </button>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}
