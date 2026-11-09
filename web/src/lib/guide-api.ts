// OP-418: POST /api/guide/frame, the camera guide's one request. Every shape comes
// from the generated api-types.ts (`make types`); nothing here is a hand-written API
// type. The frame is a Blob the page just drew; it goes out and is forgotten.

import type { components, operations } from "./api-types";
import { apiBase } from "./api";

type FrameForm = operations["post_guide_frame_api_guide_frame_post"]["requestBody"]["content"]["multipart/form-data"];

export type GuideContext = FrameForm["context"];
export type GuideProfile = GuideContext["profile"];
export type GuideFrameResponse = components["schemas"]["GuideFrameResponse"];
export type GuideHazard = components["schemas"]["GuideHazard"];

/** Claude answers in a few seconds; past this the frame is stale anyway. */
export const FRAME_TIMEOUT_MS = 15_000;

/**
 * The request failed: `status` is the HTTP status, or 0 when nothing answered.
 * `retryAfterMs` is the API's `Retry-After` on a 429 (seconds, as the rate limiter
 * sends it), or null when the header is missing or not readable from the browser.
 */
export class GuideError extends Error {
  readonly status: number;
  readonly detail: string | null;
  readonly retryAfterMs: number | null;

  constructor(status: number, detail: string | null, retryAfterMs: number | null = null) {
    super(detail ?? (status ? `The guide returned error ${status}.` : "The guide got no answer."));
    this.name = "GuideError";
    this.status = status;
    this.detail = detail;
    this.retryAfterMs = retryAfterMs;
  }
}

/** `Retry-After` in milliseconds, from a whole number of seconds; null otherwise. */
export function retryAfterMs(header: string | null): number | null {
  if (header === null) return null;
  const seconds = Number(header.trim());
  return Number.isFinite(seconds) && seconds >= 0 ? Math.round(seconds * 1000) : null;
}

async function detailOf(response: Response): Promise<string | null> {
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object" && "detail" in data) {
      const { detail } = data as { detail: unknown };
      if (typeof detail === "string") return detail;
    }
  } catch {
    // Not JSON: the status is enough.
  }
  return null;
}

function isFrameResponse(data: unknown): data is GuideFrameResponse {
  if (!data || typeof data !== "object") return false;
  const body = data as Partial<GuideFrameResponse>;
  return typeof body.say === "string" && Array.isArray(body.hazards) && Array.isArray(body.checked_by);
}

/**
 * Send one JPEG frame with the route context. Resolves with the sentence and the
 * hazards; rejects with a `GuideError` (503 without a key or in replay, 429 over the
 * limit, 0 for a network drop) or the caller's AbortError.
 */
export async function postGuideFrame(frame: Blob, context: GuideContext, signal?: AbortSignal): Promise<GuideFrameResponse> {
  const body = new FormData();
  body.set("frame", frame, "frame.jpg");
  body.set("context", JSON.stringify(context));
  let response: Response;
  try {
    response = await fetch(`${apiBase}/api/guide/frame`, { method: "POST", body, signal, cache: "no-store" });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new GuideError(0, null);
  }
  if (!response.ok) {
    let after: number | null = null;
    try {
      after = retryAfterMs(response.headers.get("Retry-After"));
    } catch {
      // The header is not readable; the caller's default wait applies.
    }
    throw new GuideError(response.status, await detailOf(response), after);
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new GuideError(response.status, "The guide returned a body that is not JSON.");
  }
  if (!isFrameResponse(data)) throw new GuideError(response.status, "The guide returned an unexpected body.");
  return data;
}
