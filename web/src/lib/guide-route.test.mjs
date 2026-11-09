// OP-418: the camera guide's geometry, step following and context rules. Run from web/
// with `npm run test:lib` (Node 22.6 or newer strips the types; no test runner is added).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  OFF_ROUTE_M,
  RouteFollower,
  TURN_M,
  arrivalSentence,
  bearingDeg,
  distanceM,
  initialFollowState,
  lineLengthM,
  nearestOnLine,
  offRouteSentence,
  pointAlong,
  routePositions,
  sideOfRoute,
  stepEnds,
  stepSentence
} from "./guide-route.ts";
import {
  LONG_PULSE,
  SHORT_DOUBLE_PULSE,
  appendTranscript,
  buildGuideContext,
  frameSize,
  nearbyItems,
  vibrationFor
} from "./guide-context.ts";

// A straight line east along Jasper Avenue, then north: 300 m, then 200 m.
const START = { lat: 53.5405, lng: -113.49 };
const M_PER_DEG_LNG = Math.cos((53.5405 * Math.PI) / 180) * 111_320;
const M_PER_DEG_LAT = 110_574;
const east = (metres) => ({ lat: START.lat, lng: START.lng + metres / M_PER_DEG_LNG });
const CORNER = east(300);
const END = { lat: CORNER.lat + 200 / M_PER_DEG_LAT, lng: CORNER.lng };
const LINE = [START, CORNER, END];
const STEPS = [
  { text: "Head east on Jasper Avenue for 300 m", distance_m: 300 },
  { text: "Turn left onto 101 Street for 200 m.", distance_m: 200 }
];

test("distance, bearing and line length", () => {
  assert.ok(Math.abs(distanceM(START, east(300)) - 300) < 1);
  assert.ok(Math.abs(bearingDeg(START, east(300)) - 90) < 0.5);
  assert.ok(Math.abs(bearingDeg(CORNER, END) - 0) < 0.5);
  assert.ok(Math.abs(lineLengthM(LINE) - 500) < 2);
});

test("routePositions reads a LineString and nothing else", () => {
  assert.deepEqual(routePositions({ type: "LineString", coordinates: [[-113.49, 53.54], [-113.48, 53.55]] }), [
    { lng: -113.49, lat: 53.54 },
    { lng: -113.48, lat: 53.55 }
  ]);
  assert.deepEqual(routePositions({ type: "Point", coordinates: [-113.49, 53.54] }), []);
  assert.deepEqual(routePositions(null), []);
  assert.deepEqual(routePositions({ type: "LineString", coordinates: [["a", 1], [-113.48, 53.55]] }), [
    { lng: -113.48, lat: 53.55 }
  ]);
});

test("pointAlong walks the line and clamps at both ends", () => {
  assert.deepEqual(pointAlong(LINE, -5), START);
  assert.deepEqual(pointAlong(LINE, 10_000), END);
  const half = pointAlong(LINE, 150);
  assert.ok(Math.abs(distanceM(START, half) - 150) < 1);
  assert.equal(pointAlong([], 10), null);
});

test("stepEnds splits the line by the steps' distances, the last at the line's end", () => {
  const ends = stepEnds(LINE, STEPS);
  assert.equal(ends.length, 2);
  assert.ok(distanceM(ends[0], CORNER) < 1);
  assert.deepEqual(ends[1], END);
  // Distances that do not add up to the line are scaled to it.
  const scaled = stepEnds(LINE, [{ distance_m: 600 }, { distance_m: 400 }]);
  assert.ok(distanceM(scaled[0], CORNER) < 1);
  assert.deepEqual(scaled[1], END);
  // All-zero distances fall back to an even split.
  const even = stepEnds(LINE, [{ distance_m: 0 }, { distance_m: 0 }]);
  assert.ok(Math.abs(distanceM(START, even[0]) - 250) < 2);
  assert.deepEqual(stepEnds([], STEPS), []);
  assert.deepEqual(stepEnds(LINE, []), []);
});

test("nearestOnLine measures the distance from the route line", () => {
  const beside = { lat: START.lat - 30 / M_PER_DEG_LAT, lng: east(100).lng }; // 30 m south of the first leg
  const nearest = nearestOnLine(LINE, beside);
  assert.ok(Math.abs(nearest.distance_m - 30) < 1, `got ${nearest.distance_m}`);
  assert.ok(Math.abs(nearest.point.lat - START.lat) < 1e-9);
  const onLine = nearestOnLine(LINE, east(50));
  assert.ok(onLine.distance_m < 0.5);
  assert.equal(nearestOnLine([], beside), null);
});

test("sideOfRoute says left or right only when the heading makes it clear", () => {
  const walker = { lat: START.lat - 30 / M_PER_DEG_LAT, lng: east(100).lng };
  const onRoute = { lat: START.lat, lng: walker.lng }; // due north of the walker
  assert.equal(sideOfRoute(walker, 90, onRoute), "left"); // facing east, route is north
  assert.equal(sideOfRoute(walker, 270, onRoute), "right"); // facing west
  assert.equal(sideOfRoute(walker, 0, onRoute), null); // facing it
  assert.equal(sideOfRoute(walker, 180, onRoute), null); // back to it
  assert.equal(sideOfRoute(walker, null, onRoute), null);
  assert.equal(sideOfRoute(walker, Number.NaN, onRoute), null);
});

test("stepSentence numbers the step and ends with one full stop", () => {
  assert.equal(stepSentence(STEPS, 0), "Step 1 of 2: Head east on Jasper Avenue for 300 m.");
  assert.equal(stepSentence(STEPS, 1), "Step 2 of 2: Turn left onto 101 Street for 200 m.");
  assert.equal(stepSentence(STEPS, 5), "");
  assert.equal(arrivalSentence("MacEwan University"), "You're at the end of the route, MacEwan University.");
  assert.equal(offRouteSentence(null), "You seem to be leaving the route.");
  assert.equal(offRouteSentence("left"), "You seem to be leaving the route. The path is to your left.");
});

test("the follower speaks the next step within 25 m of the turn, then the arrival", () => {
  const follower = new RouteFollower(LINE, STEPS, "MacEwan University");
  let state = initialFollowState();
  let result = follower.onFix(state, { position: east(100), heading_deg: 90 });
  assert.deepEqual(result.say, []);
  assert.equal(result.state.index, 0);
  assert.ok(Math.abs(result.reading.distance_to_turn_m - 200) < 1);
  assert.ok(result.reading.off_route_m < 0.5);

  result = follower.onFix(result.state, { position: east(300 - TURN_M + 1), heading_deg: 90 });
  assert.deepEqual(result.say, ["Step 2 of 2: Turn left onto 101 Street for 200 m."]);
  assert.equal(result.state.index, 1);
  // The same fix again says nothing new.
  result = follower.onFix(result.state, { position: east(300 - TURN_M + 1), heading_deg: 90 });
  assert.deepEqual(result.say, []);

  const nearEnd = { lat: END.lat - 10 / M_PER_DEG_LAT, lng: END.lng };
  result = follower.onFix(result.state, { position: nearEnd, heading_deg: 0 });
  assert.deepEqual(result.say, ["You're at the end of the route, MacEwan University."]);
  assert.equal(result.state.arrived, true);
  result = follower.onFix(result.state, { position: nearEnd, heading_deg: 0 });
  assert.deepEqual(result.say, []);
});

test("several short steps within reach pass at once; only the last new one is spoken", () => {
  const steps = [
    { text: "Go 5 m", distance_m: 5 },
    { text: "Go 5 m more", distance_m: 5 },
    { text: "Then 490 m", distance_m: 490 }
  ];
  const follower = new RouteFollower(LINE, steps, "End");
  const result = follower.onFix(initialFollowState(), { position: START, heading_deg: null });
  assert.deepEqual(result.say, ["Step 3 of 3: Then 490 m."]);
  assert.equal(result.state.index, 2);
});

test("leaving the route is said once after two fixes off the line, with the side when known", () => {
  const follower = new RouteFollower(LINE, STEPS, "End");
  const off = { lat: START.lat - (OFF_ROUTE_M + 10) / M_PER_DEG_LAT, lng: east(100).lng };
  let result = follower.onFix(initialFollowState(), { position: off, heading_deg: 90 });
  assert.deepEqual(result.say, [], "one fix is not enough");
  assert.equal(result.state.offFixes, 1);
  result = follower.onFix(result.state, { position: off, heading_deg: 90 });
  assert.deepEqual(result.say, ["You seem to be leaving the route. The path is to your left."]);
  result = follower.onFix(result.state, { position: off, heading_deg: 90 });
  assert.deepEqual(result.say, [], "not repeated while still off");
  // Back on the route resets; the next excursion is said again, without a side when the heading is unknown.
  result = follower.onFix(result.state, { position: east(120), heading_deg: null });
  assert.equal(result.state.offFixes, 0);
  assert.equal(result.state.offSaid, false);
  result = follower.onFix(result.state, { position: off, heading_deg: null });
  result = follower.onFix(result.state, { position: off, heading_deg: null });
  assert.deepEqual(result.say, ["You seem to be leaving the route."]);
  assert.ok(Math.abs(result.reading.off_route_m - (OFF_ROUTE_M + 10)) < 1);
});

test("a route with no geometry follows nothing but still reads nulls", () => {
  const follower = new RouteFollower([], STEPS, "End");
  const result = follower.onFix(initialFollowState(), { position: START, heading_deg: 0 });
  assert.deepEqual(result.say, []);
  assert.deepEqual(result.reading, { distance_to_turn_m: null, off_route_m: null });
});

const item = (overrides) => ({
  status: "on_route",
  distance_m: 0,
  kind: "sidewalk_closed",
  effect: "blocked",
  title: "Sidewalk closed",
  ...overrides
});

test("nearbyItems keeps on-route and near items within 100 m, nearest first, at most five", () => {
  const report = {
    groups: [
      { items: [item({ title: "A", distance_m: 40, status: "nearby" }), item({ title: "Far", distance_m: 140, status: "nearby" })] },
      { items: [item({ title: "Avoided", status: "avoided", distance_m: 10 })] },
      {
        items: [
          item({ title: "On", distance_m: null, kind: null, effect: "caution" }),
          item({ title: "B", distance_m: 90, status: "nearby" }),
          item({ title: "Unknown distance", distance_m: null, status: "nearby" }),
          item({ title: "C", distance_m: 12.4, status: "nearby" }),
          item({ title: "D", distance_m: 60, status: "nearby" }),
          item({ title: "E", distance_m: 70, status: "nearby" })
        ]
      }
    ]
  };
  const items = nearbyItems(report);
  assert.deepEqual(
    items.map((entry) => entry.title),
    ["On", "C", "A", "D", "E"]
  );
  assert.deepEqual(items[0], { title: "On", kind: "caution", distance_m: 0 });
  assert.deepEqual(items[1], { title: "C", kind: "sidewalk_closed", distance_m: 12 });
  assert.deepEqual(nearbyItems({ groups: [] }), []);
});

test("vibration follows the hazard kind: long beats short, and a door or sign is silent", () => {
  assert.deepEqual(vibrationFor([{ kind: "curb" }]), [...SHORT_DOUBLE_PULSE]);
  assert.deepEqual(vibrationFor([{ kind: "stairs_down" }, { kind: "door" }]), [...SHORT_DOUBLE_PULSE]);
  assert.deepEqual(vibrationFor([{ kind: "curb" }, { kind: "vehicle" }]), [...LONG_PULSE]);
  assert.deepEqual(vibrationFor([{ kind: "bike" }]), [...LONG_PULSE]);
  assert.equal(vibrationFor([{ kind: "door" }, { kind: "sign" }]), null);
  assert.equal(vibrationFor([{ kind: "stairs_up" }]), null);
  assert.equal(vibrationFor([]), null);
});

test("the transcript keeps the last five lines", () => {
  let lines = [];
  for (const text of ["a", "b", "c", "d", "e", "f"]) lines = appendTranscript(lines, text);
  assert.deepEqual(lines, ["b", "c", "d", "e", "f"]);
});

test("buildGuideContext stays inside every bound the API checks", () => {
  const input = {
    clientId: "abcdefghijklmnop",
    profile: "low_vision",
    steps: [{ text: "  Head east on Jasper Avenue. " }, { text: "Turn left" }],
    index: 0,
    position: { lat: 53.54051234567, lng: -113.4900000049, heading_deg: -90 },
    reading: { distance_to_turn_m: 12.6, off_route_m: -1 },
    report: { groups: [{ items: [item({ title: "Sidewalk closed", distance_m: 8, status: "nearby" })] }] },
    previous: "x".repeat(500)
  };
  const context = buildGuideContext(input);
  assert.deepEqual(context, {
    client_id: "abcdefghijklmnop",
    profile: "low_vision",
    step: "Head east on Jasper Avenue.",
    next_step: "Turn left",
    distance_to_turn_m: 13,
    heading_deg: 270,
    lat: 53.540512,
    lng: -113.49,
    off_route_m: null,
    nearby: [{ title: "Sidewalk closed", kind: "sidewalk_closed", distance_m: 8 }],
    previous: "x".repeat(400)
  });
  assert.equal(buildGuideContext({ ...input, position: { ...input.position, heading_deg: 360 } }).heading_deg, 0);
  assert.equal(buildGuideContext({ ...input, position: { ...input.position, heading_deg: Number.NaN } }).heading_deg, null);
  const bare = buildGuideContext({ ...input, index: 5, position: null, reading: null, previous: null, report: { groups: [] } });
  assert.equal(bare.step, null);
  assert.equal(bare.next_step, null);
  assert.equal(bare.heading_deg, null);
  assert.equal(bare.lat, null);
  assert.equal(bare.lng, null);
  assert.equal(bare.distance_to_turn_m, null);
  assert.equal(bare.previous, null);
  assert.deepEqual(bare.nearby, []);
});

test("frameSize is 640 wide in proportion and never upscales", () => {
  assert.deepEqual(frameSize(1280, 720), { width: 640, height: 360 });
  assert.deepEqual(frameSize(480, 640), { width: 480, height: 640 });
  assert.deepEqual(frameSize(0, 0), { width: 0, height: 0 });
});
