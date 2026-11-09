// "Include LRT and bus trips" (lead decision, November 9): the box is on by default,
// because LRT and bus trips are on in production and matter for the demo. A person who
// turns it off keeps that choice on this device, under the same `openpath.*` key
// pattern as the accessibility settings (lib/settings.ts). Storage can be missing or
// throw (private windows, blocked site data), so each call is wrapped and the default
// holds for the page visit. A trip link decides for itself: `mode=walk_lrt` turns the
// box on and a link without it turns it off, as before, without touching the choice.

export const TRANSIT_STORAGE_KEY = "openpath.transit";
export const TRANSIT_DEFAULT = true;

/** The saved choice, or null when nothing (readable) is saved. */
export function readTransitChoice(): boolean | null {
  try {
    const value = window.localStorage.getItem(TRANSIT_STORAGE_KEY);
    return value === "on" ? true : value === "off" ? false : null;
  } catch {
    return null;
  }
}

export function saveTransitChoice(on: boolean): void {
  try {
    window.localStorage.setItem(TRANSIT_STORAGE_KEY, on ? "on" : "off");
  } catch {
    // The choice lasts for this page visit.
  }
}
