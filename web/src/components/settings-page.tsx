"use client";

import { useId, useState, useSyncExternalStore } from "react";
import { canSpeak, stopSpeaking } from "@/lib/announce";
import {
  DEFAULT_SETTINGS,
  type MotionChoice,
  type Settings,
  type TextSize,
  type ThemeChoice,
  resetSettings,
  settingsSaved,
  updateSettings,
  useSettings
} from "@/lib/settings";
import Button from "./ui/button";
import Panel from "./ui/panel";
import SegmentedChoice, { type SegmentOption } from "./ui/segmented-choice";
import SiteFooter from "./ui/site-footer";
import SiteHeader from "./ui/site-header";
import StatusLine from "./ui/status-line";
import styles from "./settings-page.module.css";

const TEXT_SIZES: ReadonlyArray<SegmentOption<TextSize>> = [
  { id: "default", label: "Default", description: "The text size your browser is set to." },
  { id: "large", label: "Large", description: "125% of your browser's size." },
  { id: "larger", label: "Larger", description: "150% of your browser's size." }
];

const TEXT_WORDS: Record<TextSize, string> = { default: "the default size", large: "large", larger: "larger" };

const THEME_DONE: Record<ThemeChoice, string> = {
  system: "Theme now follows this device.",
  light: "Theme is now light.",
  dark: "Theme is now dark."
};

const MOTION_DONE: Record<MotionChoice, string> = {
  system: "Motion now follows this device.",
  reduce: "Motion is now always reduced.",
  full: "Motion is now never reduced."
};

const noSubscribe = () => () => undefined;

function useMedia(query: string): boolean | null {
  return useSyncExternalStore(
    (onChange) => {
      try {
        const list = window.matchMedia(query);
        list.addEventListener("change", onChange);
        return () => list.removeEventListener("change", onChange);
      } catch {
        return () => undefined;
      }
    },
    () => {
      try {
        return window.matchMedia(query).matches;
      } catch {
        return null;
      }
    },
    () => null
  );
}

function canVibrate(): boolean {
  try {
    return typeof navigator !== "undefined" && typeof navigator.vibrate === "function";
  } catch {
    return false;
  }
}

type SwitchProps = {
  label: string;
  description: string;
  /** A second line when the device cannot do it, also tied to the switch. */
  limit?: string | null;
  checked: boolean;
  onChange: (on: boolean) => void;
};

// A native checkbox with role="switch": Space toggles it, and screen readers say
// "on" or "off". Its name is the label alone; the description is read after it. The
// whole row is the target. The visible state is shape (knob left and hollow, or right
// and filled) and a word, never colour alone.
function SettingSwitch({ label, description, limit, checked, onChange }: SwitchProps) {
  const id = useId();
  const nameId = `${id}-name`;
  const descriptionId = `${id}-description`;
  const limitId = `${id}-limit`;
  return (
    <div className={styles.switch}>
      <input
        id={id}
        className={styles.switchInput}
        type="checkbox"
        role="switch"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        aria-labelledby={nameId}
        aria-describedby={limit ? `${descriptionId} ${limitId}` : descriptionId}
      />
      <label className={styles.switchLabel} htmlFor={id}>
        <span className={styles.switchText}>
          <span className={styles.switchName} id={nameId}>
            {label}
          </span>
          <span className={styles.switchDescription} id={descriptionId}>
            {description}
          </span>
        </span>
        <span className={styles.switchState} aria-hidden="true">
          <span className={styles.track}>
            <span className={styles.knob} />
          </span>
          <span className={styles.stateWord}>{checked ? "On" : "Off"}</span>
        </span>
      </label>
      {limit && (
        <p className={styles.limit} id={limitId}>
          {limit}
        </p>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const settings = useSettings();
  const [message, setMessage] = useState("");
  const speechSupported = useSyncExternalStore(noSubscribe, canSpeak, () => true);
  const vibrationSupported = useSyncExternalStore(noSubscribe, canVibrate, () => true);
  const systemReduces = useMedia("(prefers-reduced-motion: reduce)");
  const systemDark = useMedia("(prefers-color-scheme: dark)");

  function change(update: Partial<Settings>, done: string) {
    updateSettings(update);
    setMessage(
      settingsSaved()
        ? done
        : `${done} This browser is not saving site data, so it lasts until you close this page.`
    );
  }

  const motionOptions: ReadonlyArray<SegmentOption<MotionChoice>> = [
    {
      id: "system",
      label: "Follow this device",
      description:
        systemReduces === null
          ? "Reduce motion when your system asks for it."
          : systemReduces
            ? "Your system asks for less motion, so motion is reduced."
            : "Your system does not ask for less motion."
    },
    { id: "reduce", label: "Always reduce", description: "Routes appear at once, without the line drawing itself." },
    { id: "full", label: "Never reduce", description: "The route line draws itself once, even if your system asks for less motion." }
  ];

  const themeOptions: ReadonlyArray<SegmentOption<ThemeChoice>> = [
    {
      id: "system",
      label: "Follow this device",
      description:
        systemDark === null
          ? "Light or dark, as your system is set."
          : `Your system is set to ${systemDark ? "dark" : "light"}.`
    },
    { id: "light", label: "Light", description: "Dark text on a light page." },
    { id: "dark", label: "Dark", description: "Light text on a dark page." }
  ];

  const isDefault = (Object.keys(DEFAULT_SETTINGS) as Array<keyof Settings>).every(
    (key) => settings[key] === DEFAULT_SETTINGS[key]
  );

  return (
    <div className="shell">
      <SiteHeader current="settings" />
      <main id="main" className={styles.main}>
        <h1 className={styles.title}>Settings</h1>
        <p className="lead">Saved on this device only. Each change applies at once, on every page.</p>
        <StatusLine className={styles.status}>{message}</StatusLine>

        <Panel aria-labelledby="settings-changes">
          <h2 id="settings-changes">Route changes</h2>
          <p className={styles.intro}>
            A route change is always shown on screen and read by screen readers. These add sound and touch.
          </p>
          <div className={styles.group}>
            <SettingSwitch
              label="Speak route changes"
              description="Reads each change aloud with this device's voice."
              limit={speechSupported ? null : "This browser cannot speak, so changes are shown and read by screen readers only."}
              checked={settings.speak}
              onChange={(on) => {
                if (!on) stopSpeaking(); // stop anything already being said
                change({ speak: on }, `Speaking route changes is now ${on ? "on" : "off"}.`);
              }}
            />
            <SettingSwitch
              label="Vibrate on route changes"
              description="Vibrates twice when a change arrives."
              limit={vibrationSupported ? null : "This device cannot vibrate from a web page."}
              checked={settings.vibrate}
              onChange={(on) => change({ vibrate: on }, `Vibrating on route changes is now ${on ? "on" : "off"}.`)}
            />
          </div>
        </Panel>

        <Panel aria-labelledby="settings-reading">
          <h2 id="settings-reading">Reading</h2>
          <div className={styles.group}>
            <SegmentedChoice
              legend="Text size"
              name="text-size"
              layout="stack"
              options={TEXT_SIZES}
              value={settings.textSize}
              onChange={(textSize) => change({ textSize }, `Text size is now ${TEXT_WORDS[textSize]}.`)}
            />
            <SegmentedChoice
              legend="Theme"
              name="theme"
              layout="stack"
              options={themeOptions}
              value={settings.theme}
              onChange={(theme) => change({ theme }, THEME_DONE[theme])}
            />
            <SegmentedChoice
              legend="Motion"
              name="motion"
              layout="stack"
              options={motionOptions}
              value={settings.motion}
              onChange={(motion) => change({ motion }, MOTION_DONE[motion])}
            />
          </div>
        </Panel>

        <Panel aria-labelledby="settings-map">
          <h2 id="settings-map">Map</h2>
          <div className={styles.group}>
            <SettingSwitch
              label="High-contrast map"
              description="Near-black roads and paths on white (white on dark in the dark theme), clear water and labels on a solid outline."
              checked={settings.highContrastMap}
              onChange={(on) => change({ highContrastMap: on }, `High-contrast map is now ${on ? "on" : "off"}.`)}
            />
          </div>
        </Panel>

        <div className={styles.reset}>
          <Button
            variant="secondary"
            onClick={() => {
              resetSettings();
              setMessage(
                isDefault ? "Settings are already at their defaults." : "Settings are back to their defaults."
              );
            }}
          >
            Reset to defaults
          </Button>
          <p className={styles.resetNote}>
            Speech and vibration on, default text size, and theme and motion following this device.
          </p>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
