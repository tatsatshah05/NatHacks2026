// OP-420: the three-way transit choice, what it asks the API and how a trip link carries
// it. Run from web/ with `npm run test:lib` (Node 22.6 or newer strips the types).
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import {
  TRANSIT_DEFAULT,
  TRANSIT_OPTIONS,
  TRANSIT_STORAGE_KEY,
  isTransitChoice,
  readTransitChoice,
  saveTransitChoice,
  transitChoiceOf,
  transitLinkParam,
  transitRequest
} from "./transit-choice.ts";

const CHOICES = ["walk", "transit", "only"];

afterEach(() => {
  delete globalThis.window;
});

/** A window whose storage holds one value, or throws when `broken`. */
function fakeWindow(stored, { broken = false } = {}) {
  const store = new Map(stored === null ? [] : [[TRANSIT_STORAGE_KEY, stored]]);
  const fail = () => {
    throw new Error("storage is blocked");
  };
  globalThis.window = {
    localStorage: {
      getItem: broken ? fail : (key) => store.get(key) ?? null,
      setItem: broken ? fail : (key, value) => store.set(key, value)
    }
  };
  return store;
}

test("the options are Walking, Walking and transit and Transit only, with the middle one as the default", () => {
  assert.deepEqual(
    TRANSIT_OPTIONS.map((option) => [option.id, option.label]),
    [
      ["walk", "Walking"],
      ["transit", "Walking and transit"],
      ["only", "Transit only"]
    ]
  );
  assert.equal(TRANSIT_DEFAULT, "transit");
  assert.equal(TRANSIT_STORAGE_KEY, "openpath.transit");
  for (const option of TRANSIT_OPTIONS) assert.ok(option.description.length > 0, `${option.id} has a description`);
  assert.deepEqual(
    CHOICES.map((choice) => isTransitChoice(choice)),
    [true, true, true]
  );
  assert.equal(isTransitChoice("on"), false);
  assert.equal(isTransitChoice(null), false);
});

test("each choice maps to its mode and transit pair; faster is never sent", () => {
  assert.deepEqual(transitRequest("walk"), { mode: "walk" });
  assert.deepEqual(transitRequest("transit"), { mode: "walk_lrt", transit: "always" });
  assert.deepEqual(transitRequest("only"), { mode: "walk_lrt", transit: "only" });
  for (const choice of CHOICES) assert.notEqual(transitRequest(choice).transit, "faster");
});

test("a request or link maps back to its choice; transit counts only with walk_lrt", () => {
  assert.equal(transitChoiceOf({}), "walk");
  assert.equal(transitChoiceOf({ mode: null, transit: null }), "walk");
  assert.equal(transitChoiceOf({ mode: "walk" }), "walk");
  assert.equal(transitChoiceOf({ mode: "walk", transit: "only" }), "walk");
  assert.equal(transitChoiceOf({ mode: "walk_lrt" }), "transit");
  assert.equal(transitChoiceOf({ mode: "walk_lrt", transit: "always" }), "transit");
  assert.equal(transitChoiceOf({ mode: "walk_lrt", transit: "faster" }), "transit");
  assert.equal(transitChoiceOf({ mode: "walk_lrt", transit: "only" }), "only");
  assert.equal(transitChoiceOf({ mode: "bus", transit: "only" }), "walk");
});

test("a trip link carries transit=only for Transit only and nothing otherwise", () => {
  assert.equal(transitLinkParam(transitRequest("walk")), null);
  assert.equal(transitLinkParam(transitRequest("transit")), null);
  assert.equal(transitLinkParam(transitRequest("only")), "only");
  assert.equal(transitLinkParam({ mode: "walk", transit: "only" }), null);
});

test("a choice round-trips through the request and the link parameters", () => {
  for (const choice of CHOICES) {
    const request = transitRequest(choice);
    // The link keeps `mode` as the request has it and adds `transit` only for Transit only.
    const link = new URLSearchParams();
    if (request.mode === "walk_lrt") link.set("mode", "walk_lrt");
    const transit = transitLinkParam(request);
    if (transit) link.set("transit", transit);
    assert.equal(link.toString(), { walk: "", transit: "mode=walk_lrt", only: "mode=walk_lrt&transit=only" }[choice]);
    const parsed = transitChoiceOf({ mode: link.get("mode"), transit: link.get("transit") });
    assert.equal(parsed, choice);
    assert.deepEqual(transitRequest(parsed), request);
  }
});

test("the saved choice is read back, and the legacy on and off values still read", () => {
  fakeWindow("only");
  assert.equal(readTransitChoice(), "only");
  fakeWindow("walk");
  assert.equal(readTransitChoice(), "walk");
  fakeWindow("transit");
  assert.equal(readTransitChoice(), "transit");
  // OP-419 saved the two-state box as "on" and "off".
  fakeWindow("on");
  assert.equal(readTransitChoice(), "transit");
  fakeWindow("off");
  assert.equal(readTransitChoice(), "walk");
  fakeWindow("something else");
  assert.equal(readTransitChoice(), null);
  fakeWindow(null);
  assert.equal(readTransitChoice(), null);
});

test("saving writes the choice itself, and blocked storage is quiet", () => {
  const store = fakeWindow(null);
  saveTransitChoice("only");
  assert.equal(store.get(TRANSIT_STORAGE_KEY), "only");
  assert.equal(readTransitChoice(), "only");
  fakeWindow("only", { broken: true });
  assert.equal(readTransitChoice(), null);
  assert.doesNotThrow(() => saveTransitChoice("walk"));
  delete globalThis.window;
  assert.equal(readTransitChoice(), null, "no window at all reads as nothing saved");
  assert.doesNotThrow(() => saveTransitChoice("walk"));
});
