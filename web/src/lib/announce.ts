"use client";

// Speaking and vibrating route changes (OP-303). Both are user choices kept on the
// device by the accessibility settings (OP-307, lib/settings.ts): speech under
// `openpath.speak` (on by default) and vibration in `openpath.settings` (on by
// default). Every browser API here can be missing or throw (private windows, blocked
// storage, desktop browsers), so each call is wrapped and simply does nothing.

import { useCallback } from "react";
import { readSettings, updateSettings, useSettings } from "./settings";

export { SPEAK_STORAGE_KEY } from "./settings";
export const VIBRATE_PATTERN: number[] = [200, 100, 200];

/** Whether route changes are spoken. Default on; "off" is the only stored "no". */
export function getSpeakPreference(): boolean {
  return readSettings().speak;
}

export function setSpeakPreference(on: boolean): void {
  updateSettings({ speak: on });
  if (!on) stopSpeaking();
}

/** Whether route changes vibrate the device (where it can). Default on. */
export function getVibratePreference(): boolean {
  return readSettings().vibrate;
}

/** The speech toggle as React state, the same in every component and tab. */
export function useSpeakPreference(): [boolean, (on: boolean) => void] {
  const { speak } = useSettings();
  const update = useCallback((on: boolean) => setSpeakPreference(on), []);
  return [speak, update];
}

export function canSpeak(): boolean {
  try {
    return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
  } catch {
    return false;
  }
}

// Two channels (OP-419, lead feedback of November 9: PIP's voice lagged the camera).
// `speak` is the main channel: route changes and route steps. It cancels everything
// and is never cut short by PIP. `speakAside` is PIP's channel: one sentence at a
// time, where a new sentence replaces its own older one that has not finished, and a
// sentence that arrives while a step is being spoken waits for the step (only the
// newest waiting sentence is kept) rather than cutting it.
let mainUtterance: SpeechSynthesisUtterance | null = null;
let asideUtterance: SpeechSynthesisUtterance | null = null;
let asideWaiting: string | null = null;

function utteranceFor(text: string): SpeechSynthesisUtterance {
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "en-CA";
  return utterance;
}

/** The main channel still has a sentence that has not finished. */
function mainBusy(): boolean {
  if (!mainUtterance) return false;
  try {
    const synth = window.speechSynthesis;
    // `onend` can fail to fire in some browsers; the engine's own state is the truth.
    return synth.speaking || synth.pending;
  } catch {
    return false;
  }
}

/** Say `text` now, replacing anything still being spoken. Returns true when queued. */
export function speak(text: string): boolean {
  if (!text || !canSpeak()) return false;
  try {
    const utterance = utteranceFor(text);
    const done = () => {
      if (mainUtterance !== utterance) return;
      mainUtterance = null;
      // PIP's newest sentence, held back while the step was spoken.
      const waiting = asideWaiting;
      asideWaiting = null;
      if (waiting) speakAside(waiting);
    };
    utterance.onend = done;
    utterance.onerror = done;
    mainUtterance = utterance;
    asideUtterance = null;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    return true;
  } catch {
    return false;
  }
}

/**
 * PIP's channel: say `text` at once, replacing PIP's own older sentence if it has not
 * finished, and never cutting a step sentence (then it waits, and the newest wins).
 * Returns true when queued or kept waiting.
 */
export function speakAside(text: string): boolean {
  if (!text || !canSpeak()) return false;
  try {
    if (mainBusy()) {
      asideWaiting = text;
      return true;
    }
    mainUtterance = null;
    asideWaiting = null;
    const utterance = utteranceFor(text);
    const done = () => {
      if (asideUtterance === utterance) asideUtterance = null;
    };
    utterance.onend = done;
    utterance.onerror = done;
    asideUtterance = utterance;
    // Only PIP's own sentence can be in the queue here: steps cancel everything first.
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    return true;
  } catch {
    return false;
  }
}

export function stopSpeaking(): void {
  mainUtterance = null;
  asideUtterance = null;
  asideWaiting = null;
  try {
    if (canSpeak()) window.speechSynthesis.cancel();
  } catch {
    // Nothing to stop.
  }
}

/** Vibrate where supported. Returns true when the device accepted the pattern. */
export function vibrate(pattern: number[] = VIBRATE_PATTERN): boolean {
  try {
    if (typeof navigator === "undefined" || typeof navigator.vibrate !== "function") return false;
    return navigator.vibrate(pattern);
  } catch {
    return false;
  }
}

/**
 * Speak (when the speech setting is on) and vibrate (when the vibration setting is on
 * and the device supports it) one route change. The visible `aria-live="assertive"`
 * region is separate: `LiveStatus` renders it, so screen reader users hear the change
 * even with speech off.
 */
export function announceChange(
  text: string,
  speakOn: boolean = getSpeakPreference(),
  vibrateOn: boolean = getVibratePreference()
): void {
  if (speakOn) speak(text);
  if (vibrateOn) vibrate();
}
