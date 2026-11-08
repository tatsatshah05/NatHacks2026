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

/** Say `text` now, replacing anything still being spoken. Returns true when queued. */
export function speak(text: string): boolean {
  if (!text || !canSpeak()) return false;
  try {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-CA";
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    return true;
  } catch {
    return false;
  }
}

export function stopSpeaking(): void {
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
