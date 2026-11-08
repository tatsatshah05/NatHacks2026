"use client";

import { useEffect, useState } from "react";
import { getHealth, Health } from "@/lib/api";

// Shows "Replay" when the API serves recorded data; shows nothing when it is live or
// unreachable. Replayed data is never presented as live (AGENTS.md).
export default function ModeBadge() {
  const [mode, setMode] = useState<Health["mode"] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => setMode(health.mode))
      .catch(() => setMode(null));
    return () => controller.abort();
  }, []);

  if (mode !== "replay") return null;
  return (
    <span className="mode-badge">
      Replay<span className="sr-only">: recorded data, not current conditions</span>
    </span>
  );
}
