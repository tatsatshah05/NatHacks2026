// Accessibility settings (OP-307), kept on this device only. Version 1 has no
// accounts, so nothing here is sent to the server.
//
//   const settings = useSettings();          // live in every component and tab
//   updateSettings({ textSize: "large" });   // saves, applies and notifies
//
// Storage can be missing or throw (private windows, blocked site data), so every read
// and write is wrapped. When a write fails the choice lasts for this page visit, and
// the page still renders correctly with the defaults.

import { useSyncExternalStore } from "react";
import { SETTINGS_STORAGE_KEY, SPEAK_STORAGE_KEY } from "./settings-boot";

export { SETTINGS_STORAGE_KEY, SPEAK_STORAGE_KEY } from "./settings-boot";

export type TextSize = "default" | "large" | "larger";
export type MotionChoice = "system" | "reduce" | "full";
export type ThemeChoice = "system" | "light" | "dark";

export type Settings = {
  /** Read route changes aloud with speechSynthesis. */
  speak: boolean;
  /** Vibrate on a route change, where the device can. */
  vibrate: boolean;
  /** Root font-size: 100%, 125% or 150% of the browser's own size. */
  textSize: TextSize;
  /** Follow the device, always reduce, or never reduce. */
  motion: MotionChoice;
  /** Follow the device, or force light or dark. */
  theme: ThemeChoice;
  /** The high-contrast map style from lib/map-style.ts. */
  highContrastMap: boolean;
};

export const DEFAULT_SETTINGS: Readonly<Settings> = Object.freeze({
  speak: true,
  vibrate: true,
  textSize: "default",
  motion: "system",
  theme: "system",
  highContrastMap: false
});

/** The multiplier each text size applies to the root font size. */
export const TEXT_SCALE: Readonly<Record<TextSize, number>> = { default: 1, large: 1.25, larger: 1.5 };

const TEXT_SIZES: readonly TextSize[] = ["default", "large", "larger"];
const MOTIONS: readonly MotionChoice[] = ["system", "reduce", "full"];
const THEMES: readonly ThemeChoice[] = ["system", "light", "dark"];
const CHANGE_EVENT = "openpath:settings";

// Used only after a write failed, so a choice still holds for this page visit.
let memory: Settings | null = null;

function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

function flag(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

/** Turn the two stored strings into settings. Anything unknown falls back to the default. */
export function parseSettings(speakRaw: string | null, settingsRaw: string | null): Settings {
  let stored: Record<string, unknown> = {};
  try {
    const parsed: unknown = settingsRaw ? JSON.parse(settingsRaw) : {};
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) stored = parsed as Record<string, unknown>;
  } catch {
    // Not JSON: use the defaults.
  }
  return {
    // "off" is the only stored "no", as in OP-303's announce.ts.
    speak: speakRaw !== "off",
    vibrate: flag(stored.vibrate, DEFAULT_SETTINGS.vibrate),
    textSize: pick(stored.textSize, TEXT_SIZES, DEFAULT_SETTINGS.textSize),
    motion: pick(stored.motion, MOTIONS, DEFAULT_SETTINGS.motion),
    theme: pick(stored.theme, THEMES, DEFAULT_SETTINGS.theme),
    highContrastMap: flag(stored.highContrastMap, DEFAULT_SETTINGS.highContrastMap)
  };
}

function readRaw(): [string | null, string | null] {
  try {
    return [window.localStorage.getItem(SPEAK_STORAGE_KEY), window.localStorage.getItem(SETTINGS_STORAGE_KEY)];
  } catch {
    return [null, null];
  }
}

// useSyncExternalStore needs the same object back while nothing changed.
let cacheKey: string | null = null;
let cacheValue: Settings = { ...DEFAULT_SETTINGS };

/** The current settings. Safe anywhere: on the server it returns the defaults. */
export function readSettings(): Settings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  if (memory) return memory;
  const [speakRaw, settingsRaw] = readRaw();
  const key = `${speakRaw ?? ""}\u0000${settingsRaw ?? ""}`;
  if (key !== cacheKey) {
    cacheKey = key;
    cacheValue = parseSettings(speakRaw, settingsRaw);
  }
  return cacheValue;
}

function write(next: Settings): void {
  const { speak, ...rest } = next;
  try {
    window.localStorage.setItem(SPEAK_STORAGE_KEY, speak ? "on" : "off");
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(rest));
    memory = null;
  } catch {
    // Storage is blocked: the choice lasts for this page only.
    memory = next;
  }
}

function notify(): void {
  try {
    window.dispatchEvent(new Event(CHANGE_EVENT));
  } catch {
    // Nothing is listening.
  }
}

/** Save a change, apply it to <html> and tell every subscriber. Returns the new settings. */
export function updateSettings(change: Partial<Settings>): Settings {
  const next: Settings = { ...readSettings(), ...change };
  write(next);
  applySettings(next);
  notify();
  return readSettings();
}

/** False when the last change could not be stored and lasts only for this page visit. */
export function settingsSaved(): boolean {
  return memory === null;
}

/** Back to the defaults: speech and vibration on, normal text, follow the device. */
export function resetSettings(): Settings {
  return updateSettings({ ...DEFAULT_SETTINGS });
}

/**
 * Put text size, motion and theme on <html> as data attributes, which globals.css
 * reads (OP-307 section). The defaults leave the attribute off, so the OS settings
 * apply through the media queries. Keep in step with SETTINGS_BOOT_SCRIPT.
 */
export function applySettings(settings: Settings, root?: HTMLElement): void {
  try {
    const element = root ?? document.documentElement;
    const set = (name: string, value: string | null) =>
      value === null ? element.removeAttribute(name) : element.setAttribute(name, value);
    set("data-text", settings.textSize === "default" ? null : settings.textSize);
    set("data-motion", settings.motion === "system" ? null : settings.motion);
    set("data-theme", settings.theme === "system" ? null : settings.theme);
  } catch {
    // No document (server) or a locked-down page: the defaults stay.
  }
}

/** Listen for changes from this tab (CHANGE_EVENT) and from other tabs (storage). */
export function subscribeSettings(onChange: () => void): () => void {
  try {
    window.addEventListener("storage", onChange);
    window.addEventListener(CHANGE_EVENT, onChange);
  } catch {
    return () => undefined;
  }
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

const serverSettings = (): Settings => DEFAULT_SETTINGS;

/** The settings as React state, the same in every component and tab. */
export function useSettings(): Settings {
  return useSyncExternalStore(subscribeSettings, readSettings, serverSettings);
}

function mediaMatches(query: string): boolean {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  } catch {
    return false;
  }
}

function subscribeMedia(query: string, onChange: () => void): () => void {
  try {
    const list = window.matchMedia(query);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  } catch {
    return () => undefined;
  }
}

const REDUCE_QUERY = "(prefers-reduced-motion: reduce)";
const DARK_QUERY = "(prefers-color-scheme: dark)";

/** True when motion should be reduced: the setting, or the OS when it follows the device. */
export function prefersReducedMotion(settings: Settings = readSettings()): boolean {
  if (settings.motion === "reduce") return true;
  if (settings.motion === "full") return false;
  return mediaMatches(REDUCE_QUERY);
}

/** True when the page is dark: the setting, or the OS when it follows the device. */
export function prefersDarkTheme(settings: Settings = readSettings()): boolean {
  if (settings.theme === "dark") return true;
  if (settings.theme === "light") return false;
  return mediaMatches(DARK_QUERY);
}

function subscribeAll(onChange: () => void): () => void {
  const stops = [subscribeSettings(onChange), subscribeMedia(REDUCE_QUERY, onChange), subscribeMedia(DARK_QUERY, onChange)];
  return () => stops.forEach((stop) => stop());
}

/**
 * Reduced motion as live React state, for code the stylesheet cannot reach: map
 * fly-to and fade durations, smooth scrolling. Pass `fadeDuration: reduce ? 0 : 300`.
 */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeAll, () => prefersReducedMotion(), () => false);
}

/** Whether the page is dark right now, following the setting and then the OS. */
export function useDarkTheme(): boolean {
  return useSyncExternalStore(subscribeAll, () => prefersDarkTheme(), () => false);
}
