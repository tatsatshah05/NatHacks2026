// Travel times in hours and minutes. Run from web/ with `npm run test:lib`
// (Node 22.6 or newer strips the types of the imported module; no test runner is added).
import assert from "node:assert/strict";
import { test } from "node:test";
import { formatMinutes } from "./report-format.ts";

test("under an hour it is whole minutes, never less than one", () => {
  assert.equal(formatMinutes(0.2), "1 min");
  assert.equal(formatMinutes(6.04), "6 min");
  assert.equal(formatMinutes(59.4), "59 min");
});

test("from an hour on it reads as hours, with the minutes left over", () => {
  assert.equal(formatMinutes(59.6), "1 h");
  assert.equal(formatMinutes(60), "1 h");
  assert.equal(formatMinutes(77.8), "1 h 18 min");
  assert.equal(formatMinutes(120.2), "2 h");
  assert.equal(formatMinutes(167.2), "2 h 47 min");
});
