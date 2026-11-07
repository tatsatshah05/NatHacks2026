"use client";

import { useEffect, useState } from "react";
import type { components } from "@/lib/api-types";

type Health = components["schemas"]["Health"];
type State = "checking" | "ok" | "unreachable";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const LABELS: Record<State, string> = {
  checking: "Checking the API…",
  ok: "API: connected",
  unreachable: "API: not reachable",
};

/** Shows whether the web app can reach the API's /health endpoint. */
export function ApiStatus() {
  const [state, setState] = useState<State>("checking");

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/health`, { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body = (await response.json()) as Health;
        setState(body.status === "ok" ? "ok" : "unreachable");
      })
      .catch(() => {
        if (!controller.signal.aborted) setState("unreachable");
      });
    return () => controller.abort();
  }, []);

  return (
    <p role="status" className="text-base">
      {LABELS[state]}
    </p>
  );
}
