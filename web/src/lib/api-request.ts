// JSON requests to the OpenPath API for the alerts client (OP-306). Errors keep the
// HTTP status, so callers can say what went wrong and what to do in plain words.

/** The API base URL: empty for the same origin. */
export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "";
}

export class ApiError extends Error {
  /** The HTTP status, or 0 when the server could not be reached. */
  readonly status: number;
  /** The API's own sentence (FastAPI's string `detail`), when it sent one. */
  readonly detail: string | null;

  constructor(status: number, detail: string | null) {
    super(detail ?? (status ? `The server returned error ${status}.` : "OpenPath could not reach the server."));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
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

type Options = { method?: "GET" | "POST" | "DELETE"; body?: unknown; signal?: AbortSignal };

/** Sends a request and returns the parsed JSON body (undefined for 204). */
export async function requestJson<T>(path: string, { method = "GET", body, signal }: Options = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBase()}${path}`, {
      method,
      signal,
      cache: "no-store",
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, null);
  }
  if (!response.ok) throw new ApiError(response.status, await detailOf(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
