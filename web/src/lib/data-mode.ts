"use client";

import { useEffect, useState } from "react";
import { getHealth, Health } from "./api";

export type DataMode = Health["mode"];

/**
 * Whether the API serves live or replayed data (GET /health), or null until it answers.
 * Pages use it to label every result "Replayed data" in replay mode (AGENTS.md: never
 * present replayed data as live). The header's ModeBadge reads the same endpoint.
 */
export function useDataMode(): DataMode | null {
  const [mode, setMode] = useState<DataMode | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => setMode(health.mode))
      .catch(() => {
        // Unreachable: the route request reports the problem; the label stays unset.
      });
    return () => controller.abort();
  }, []);
  return mode;
}
