// OP-412: the one sentence shown when ice risk cannot come from the road cameras.
//
// Ice risk reaches a route two ways: the city-wide ice-risk weather watch, and camera
// readings that become ice risk on the crossings near a camera. When the camera worker
// stops posting (laptop off, 511 down, model missing, second check unavailable) the
// camera path goes quiet without an error, so GET /api/status carries a `cameras`
// block (cloud/app/camera_status.py) and this file turns it into words. The planner's
// city-wide list and the city page's camera list both render it from here, so the
// wording lives in one place. Pure functions only: camera-status.ts has the hook.

import type { components } from "./api-types";

export type CamerasStatus = components["schemas"]["CamerasStatus"];

/** The block plus when this browser fetched it, so ages can tick between fetches. */
export type CamerasReading = { cameras: CamerasStatus; fetchedAt: Date };

export type CameraFallback = {
  state: "stale" | "none";
  /** What the cameras are doing and where ice risk comes from, in plain words. */
  sentence: string;
};

/** "40 minutes", "1 h 5 min", "2 h". Readings expire within 2 hours, so days never occur. */
export function ageWords(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/**
 * Seconds since the newest usable frame: the API's age at its own clock plus the
 * time since this browser fetched it. Never the browser's clock against the frame
 * time, because a replay runs on a frozen clock while the browser does not.
 */
function newestAgeSeconds({ cameras, fetchedAt }: CamerasReading, now: Date): number | null {
  if (cameras.newest_age_s === null) return null;
  return cameras.newest_age_s + Math.max(0, (now.getTime() - fetchedAt.getTime()) / 1000);
}

/**
 * The sentence to show when ice risk cannot come from the cameras, or null while they
 * are live or the state is unknown (no block yet, or /api/status unreachable).
 *
 * The weather watch is the fallback whether or not it is active, and today it is shown
 * nowhere else, so the sentence says whether it is on. When the API says the watch is
 * off, the sentence says so plainly instead of implying a risk that is not there.
 */
export function cameraFallback(reading: CamerasReading | null | undefined, now: Date): CameraFallback | null {
  if (!reading) return null;
  const { cameras } = reading;
  const state = cameras.state;
  if (state === "live") return null;
  const lead =
    state === "none" ? "Road cameras are not reporting right now" : `Camera readings are ${ageClause(reading, now)}`;
  if (cameras.watch_active === false) {
    return { state, sentence: `${lead}, and the ice-risk weather watch is off.` };
  }
  const watch = cameras.watch_active ? " The ice-risk watch is on." : "";
  return { state, sentence: `${lead}; ice risk comes from the weather watch alone.${watch}` };
}

function ageClause(reading: CamerasReading, now: Date): string {
  const age = newestAgeSeconds(reading, now);
  return age === null ? `older than ${ageWords(reading.cameras.live_within_s)}` : `${ageWords(age)} old`;
}
