// OP-412: the camera fallback sentence. Run from web/ with `npm run test:lib`
// (Node 22.6 or newer strips the types of the imported module; no test runner is added).
import assert from "node:assert/strict";
import { test } from "node:test";
import { ageWords, cameraFallback } from "./camera-fallback.ts";

const NOW = new Date("2026-11-07T20:10:00Z");

function block(overrides = {}) {
  return {
    state: "none",
    ice_source: "weather_watch",
    readings: 0,
    usable: 0,
    views_total: 0,
    newest_observed_at: null,
    newest_age_s: null,
    oldest_age_s: null,
    model_versions: [],
    score_min: null,
    score_median: null,
    labels: {},
    watch_active: null,
    live_within_s: 900,
    ...overrides
  };
}

const reading = (overrides, fetchedAt = NOW) => ({ cameras: block(overrides), fetchedAt });

test("live cameras need no sentence, and an unknown state says nothing", () => {
  assert.equal(cameraFallback(reading({ state: "live", ice_source: "cameras" }), NOW), null);
  assert.equal(cameraFallback(null, NOW), null);
  assert.equal(cameraFallback(undefined, NOW), null);
});

test("no readings: the watch alone, and whether it is on", () => {
  assert.deepEqual(cameraFallback(reading({ watch_active: null }), NOW), {
    state: "none",
    sentence: "Road cameras are not reporting right now; ice risk comes from the weather watch alone."
  });
  assert.equal(
    cameraFallback(reading({ watch_active: true }), NOW).sentence,
    "Road cameras are not reporting right now; ice risk comes from the weather watch alone. The ice-risk watch is on."
  );
});

test("no readings and no watch: unavailable, without alarm", () => {
  assert.equal(
    cameraFallback(reading({ watch_active: false }), NOW).sentence,
    "Road cameras are not reporting right now, and the ice-risk weather watch is off."
  );
});

test("stale readings say how old they are, from the API's age plus time since the fetch", () => {
  const stale = reading({ state: "stale", usable: 2, readings: 2, newest_age_s: 2400, watch_active: true });
  assert.deepEqual(cameraFallback(stale, NOW), {
    state: "stale",
    sentence: "Camera readings are 40 minutes old; ice risk comes from the weather watch alone. The ice-risk watch is on."
  });
  const later = new Date(NOW.getTime() + 25 * 60_000);
  assert.match(cameraFallback(stale, later).sentence, /^Camera readings are 1 h 5 min old;/);
  // The browser clock never runs the age backwards, and the frame time is never used.
  const earlier = new Date(NOW.getTime() - 60 * 60_000);
  assert.match(cameraFallback(stale, earlier).sentence, /^Camera readings are 40 minutes old;/);
});

test("stale readings with the watch off", () => {
  const stale = reading({ state: "stale", newest_age_s: 3600, watch_active: false });
  assert.equal(
    cameraFallback(stale, NOW).sentence,
    "Camera readings are 1 h old, and the ice-risk weather watch is off."
  );
});

test("a stale block without an age falls back to the live limit", () => {
  const stale = reading({ state: "stale", newest_age_s: null });
  assert.equal(
    cameraFallback(stale, NOW).sentence,
    "Camera readings are older than 15 minutes; ice risk comes from the weather watch alone."
  );
});

test("ages read as a person would say them", () => {
  assert.equal(ageWords(20), "1 minute");
  assert.equal(ageWords(900), "15 minutes");
  assert.equal(ageWords(3600), "1 h");
  assert.equal(ageWords(3900), "1 h 5 min");
  assert.equal(ageWords(7200), "2 h");
});
