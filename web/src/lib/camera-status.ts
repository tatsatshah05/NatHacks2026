"use client";

// OP-412: the `cameras` block of GET /api/status, read by the components that show
// the camera fallback sentence (camera-fallback.ts has the words).

import { useEffect, useState } from "react";
import { getStatus } from "./api";
import type { CamerasReading } from "./camera-fallback";

export { cameraFallback } from "./camera-fallback";
export type { CameraFallback, CamerasReading, CamerasStatus } from "./camera-fallback";

/** How often a page re-reads /api/status for the camera state. The worker posts every 5 minutes. */
export const CAMERA_STATUS_REFRESH_MS = 60_000;

/**
 * The `cameras` block and when it was fetched, re-read every minute, or null until the
 * API answers. A failed read keeps the last answer: a missing status never removes a
 * warning, and the pages report an unreachable API elsewhere.
 */
export function useCamerasStatus(): CamerasReading | null {
  const [reading, setReading] = useState<CamerasReading | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const read = () => {
      getStatus(controller.signal)
        .then((status) => {
          if (controller.signal.aborted) return;
          setReading(status.cameras ? { cameras: status.cameras, fetchedAt: new Date() } : null);
        })
        .catch(() => {
          // Unreachable or aborted: keep what we have.
        });
    };
    read();
    const timer = window.setInterval(read, CAMERA_STATUS_REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);
  return reading;
}
