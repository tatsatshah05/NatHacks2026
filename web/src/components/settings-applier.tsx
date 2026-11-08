"use client";

import { useEffect } from "react";
import { applySettings, useSettings } from "@/lib/settings";

// Keeps <html> in step with the accessibility settings after the first paint: a
// change on /settings, in another tab, or a choice held only in memory because
// storage is blocked. The inline script in layout.tsx handles the first paint.
export default function SettingsApplier() {
  const settings = useSettings();
  useEffect(() => {
    applySettings(settings);
  }, [settings]);
  return null;
}
