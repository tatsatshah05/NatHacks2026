"use client";

// Speaking and vibrating route changes (OP-303). Speech is a user choice stored on the
// device under `openpath.speak` (on by default); vibration happens wherever the device
// supports it. Every browser API here can be missing or throw (private windows,
// blocked storage, desktop browsers), so each call is wrapped and simply does nothing.

import { useCallback, useSyncExternalStore } from "react";

export const SPEAK_STORAGE_KEY = "openpath.speak";
export const VIBRATE_PATTERN: number[] = [200, 100, 200];
const SPEAK_EVENT = "openpath:speak-preference";

// Used only when storage is blocked, so the toggle still works for this page.
let memoryPreference: boolean | null = null;

/** Whether route changes are spoken. Default on; "off" is the only stored "no". */
export function getSpeakPreference(): boolean {
  try {
    return window.localStorage.getItem(SPEAK_STORAGE_KEY) !== "off";
  } catch {
    return true;
  }
}

export function setSpeakPreference(on: boolean): void {
  try {
    window.localStorage.setItem(SPEAK_STORAGE_KEY, on ? "on" : "off");
  } catch {
    // Storage is blocked: the choice lasts for this page only.
    memoryPreference = on;
  }
  if (!on) stopSpeaking();
  try {
    window.dispatchEvent(new Event(SPEAK_EVENT));
  } catch {
    // Nothing is listening.
  }
}

function readPreference(): boolean {
  return memoryPreference ?? getSpeakPreference();
}

function subscribe(onChange: () => void): () => void {
  try {
    window.addEventListener("storage", onChange); // another tab changed it
    window.addEventListener(SPEAK_EVENT, onChange); // this tab changed it
  } catch {
    return () => undefined;
  }
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(SPEAK_EVENT, onChange);
  };
}

/** The speech toggle as React state, the same in every component and tab. */
export function useSpeakPreference(): [boolean, (on: boolean) => void] {
  const speakOn = useSyncExternalStore(subscribe, readPreference, () => true);
  const update = useCallback((on: boolean) => setSpeakPreference(on), []);
  return [speakOn, update];
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
 * Speak (when the user's toggle is on) and vibrate (where supported) one route change.
 * The visible `aria-live="assertive"` region is separate: `LiveStatus` renders it, so
 * screen reader users hear the change even with speech off.
 */
export function announceChange(text: string, speakOn: boolean = readPreference()): void {
  if (speakOn) speak(text);
  vibrate();
}
