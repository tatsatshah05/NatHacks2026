"use client";

// OP-418: the camera guide (a preview) on the route report. One panel after the Maps
// hand-off, so the route steps, conditions and buttons the page already has are not
// touched:
//   1. "Guide" shows the privacy notice and Start;
//   2. Start speaks the current step, opens the rear camera and sends a frame every
//      2.5 s; each sentence that comes back is spoken, vibrated per hazard and listed
//      in a polite live region; a large Stop comes first in the tab order;
//   3. without the camera, or when the service says 503, one sentence says so and the
//      steps keep being spoken, by GPS or by the Next step button and the step list.
// It describes, it does not decide: the sentence under the heading says so, always.

import { KeyboardEvent, useEffect, useId, useState } from "react";
import Button from "../ui/button";
import Panel from "../ui/panel";
import StatusLine from "../ui/status-line";
import type { RouteOut, TravelProfile } from "@/lib/planner-types";
import { useSettings } from "@/lib/settings";
import { CameraState, useGuide } from "./use-guide";
import styles from "./guide-panel.module.css";

export const GUIDE_TITLE = "Camera guide (preview)";
export const GUIDE_PROMISE = "Guide describes what the camera sees. It does not replace a cane or a guide dog.";
export const GUIDE_NOTICE =
  "Your camera's view is sent to Anthropic to be described and is not stored. People may appear in it. Press Stop at any time.";

const STATUS: Record<CameraState, string> = {
  off: "",
  starting: "Starting the camera",
  on: "Guiding",
  denied: "Camera guidance unavailable, reading route steps",
  unavailable: "Camera guidance unavailable, reading route steps"
};

type Props = {
  route: RouteOut;
  profile: TravelProfile;
  /** The trip's destination name, for "You're at the end of the route, ...". */
  destination: string;
};

export default function GuidePanel({ route, profile, destination }: Props) {
  const ids = useId();
  const id = (name: string) => `${ids}-${name}`;
  const guide = useGuide({ route, profile, destination });
  const { speak } = useSettings();
  // Focus follows each change of state: Start after Guide, Stop while guiding, Guide after Stop.
  const [focusTarget, setFocusTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!focusTarget) return;
    document.getElementById(focusTarget)?.focus();
    setFocusTarget(null);
  }, [focusTarget, guide.phase]);

  function open() {
    guide.open();
    setFocusTarget(id("start"));
  }

  function close() {
    guide.close();
    setFocusTarget(id("guide"));
  }

  function start() {
    guide.start();
    setFocusTarget(id("stop"));
  }

  function stop() {
    guide.stop();
    setFocusTarget(id("guide"));
  }

  function onNoticeKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }

  return (
    <Panel aria-labelledby={id("title")}>
      <h2 id={id("title")}>{GUIDE_TITLE}</h2>
      <p className={styles.promise}>{GUIDE_PROMISE}</p>

      {guide.phase === "closed" && (
        <div className="report-actions">
          <Button id={id("guide")} variant="secondary" onClick={open}>
            Guide
          </Button>
        </div>
      )}

      {guide.phase === "notice" && (
        <div className={styles.body} onKeyDown={onNoticeKeyDown}>
          <p id={id("notice")} className={styles.notice}>
            {GUIDE_NOTICE}
          </p>
          <div className={styles.buttons}>
            <Button id={id("start")} onClick={start} aria-describedby={id("notice")}>
              Start
            </Button>
            <Button variant="text" onClick={close}>
              Close
            </Button>
          </div>
        </div>
      )}

      {guide.phase === "guiding" && (
        <div className={styles.body}>
          <Button id={id("stop")} size="large" onClick={stop}>
            Stop
          </Button>
          <StatusLine>{STATUS[guide.camera]}</StatusLine>
          {guide.camera !== "denied" && (
            // The preview is for sighted and low-vision users; the transcript carries the words.
            <video ref={guide.videoRef} className={styles.video} autoPlay muted playsInline aria-hidden="true" />
          )}

          <h3 id={id("transcript-title")}>Said so far</h3>
          <ol className={styles.transcript} aria-labelledby={id("transcript-title")} aria-live="polite">
            {guide.lines.map((line) => (
              <li key={line.id}>{line.text}</li>
            ))}
          </ol>
          {!speak && <p className={styles.note}>Speech is off in Settings, so sentences appear here only.</p>}
          {guide.location === "off" && <p className={styles.note}>Location is off. Press Next step as you go.</p>}

          <div className={styles.buttons}>
            <Button variant="secondary" onClick={guide.repeatStep}>
              Repeat step
            </Button>
            <Button variant="secondary" onClick={guide.nextStep}>
              Next step
            </Button>
          </div>

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
        </div>
      )}
    </Panel>
  );
}
