"use client";

import { useEffect } from "react";
import { DEFAULT_SETTINGS, applySettings, useSettings } from "@/lib/settings";

// Keeps <html> in step with the accessibility settings after the first paint: a
// change on /settings, in another tab, or a choice held only in memory because
// storage is blocked. The inline script in layout.tsx handles the first paint.
export default function SettingsApplier() {
  const settings = useSettings();
  useEffect(() => {
    // During hydration useSettings returns the server snapshot, which is this very
    // object. Applying it would undo the boot script for a frame; the stored
    // settings follow in the next render. (On the client, readSettings always
    // returns a copy, so a reset to the defaults still applies.)
    if (settings === DEFAULT_SETTINGS) return;
    applySettings(settings);
  }, [settings]);
  return null;
}
