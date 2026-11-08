// The device's anonymous id (version 1 has no accounts). Community reports (OP-308)
// send it for the rate limit, and push alerts (OP-304) can share it. It lives only in
// this browser's localStorage; when storage is blocked, it lasts for the page visit.

const STORAGE_KEY = "openpath.client_id";
const VALID_ID = /^[A-Za-z0-9_-]{16,64}$/;

let pageId: string | null = null;

function newId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID().replace(/-/g, "");
  // randomUUID needs a secure context; getRandomValues does not.
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function getAnonymousClientId(): string {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && VALID_ID.test(saved)) return saved;
    const id = newId();
    window.localStorage.setItem(STORAGE_KEY, id);
    return id;
  } catch {
    pageId ??= newId();
    return pageId;
  }
}
