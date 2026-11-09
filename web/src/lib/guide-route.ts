// OP-418: the camera guide's geometry and its route follower. Pure functions and one
// small state machine, with no browser APIs and no local imports, so `npm run
// test:lib` runs this file in Node as it is.
//
// Distances are in metres. Positions are { lat, lng }; GeoJSON coordinates are
// [lng, lat]. Everything is local to one route (a few kilometres), so an
// equirectangular projection at the route's latitude is accurate to well under a
// metre for the comparisons made here (25 m to a turn, 20 m off the line).
//
// The follower's rules:
// - within TURN_M of the current step's end, the next step is spoken and becomes
//   current (several short steps may pass at once; only the last new one is spoken);
// - more than OFF_ROUTE_M from the route line on two consecutive fixes says so once,
//   with the side the path is on when the heading makes it clear;
// - reaching the last step's end says so once.

export type LatLng = { lat: number; lng: number };

const EARTH_RADIUS_M = 6_371_000;
const RAD = Math.PI / 180;

/** Great-circle distance between two positions, in metres. */
export function distanceM(a: LatLng, b: LatLng): number {
  const dLat = (b.lat - a.lat) * RAD;
  const dLng = (b.lng - a.lng) * RAD;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * RAD) * Math.cos(b.lat * RAD) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Initial bearing from `a` to `b`: 0 = north, clockwise, in [0, 360). */
export function bearingDeg(a: LatLng, b: LatLng): number {
  const lat1 = a.lat * RAD;
  const lat2 = b.lat * RAD;
  const dLng = (b.lng - a.lng) * RAD;
  const y = Math.sin(dLng) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng);
  return ((Math.atan2(y, x) / RAD) % 360 + 360) % 360;
}

/** The route's coordinates as positions. Anything that is not a LineString gives []. */
export function routePositions(geometry: { [key: string]: unknown } | null | undefined): LatLng[] {
  if (!geometry || geometry.type !== "LineString" || !Array.isArray(geometry.coordinates)) return [];
  const positions: LatLng[] = [];
  for (const pair of geometry.coordinates as unknown[]) {
    if (Array.isArray(pair) && pair.length >= 2 && typeof pair[0] === "number" && typeof pair[1] === "number") {
      positions.push({ lng: pair[0], lat: pair[1] });
    }
  }
  return positions;
}

/** Length of a polyline in metres. */
export function lineLengthM(line: LatLng[]): number {
  let total = 0;
  for (let index = 0; index + 1 < line.length; index += 1) total += distanceM(line[index], line[index + 1]);
  return total;
}

/** The position `along` metres from the start of the line (clamped to its ends). */
export function pointAlong(line: LatLng[], along: number): LatLng | null {
  if (line.length === 0) return null;
  if (line.length === 1 || along <= 0) return line[0];
  let walked = 0;
  for (let index = 0; index + 1 < line.length; index += 1) {
    const segment = distanceM(line[index], line[index + 1]);
    if (walked + segment >= along) {
      const t = segment === 0 ? 0 : (along - walked) / segment;
      return {
        lat: line[index].lat + (line[index + 1].lat - line[index].lat) * t,
        lng: line[index].lng + (line[index + 1].lng - line[index].lng) * t
      };
    }
    walked += segment;
  }
  return line[line.length - 1];
}

/**
 * Where each step ends. Steps carry only text and a distance (`StepOut`), so the
 * route line is split by the steps' distances in order; the last step always ends
 * at the line's last point. When the distances do not add up to the line's length
 * (a trip with an LRT ride, say), they are scaled to it.
 */
export function stepEnds(line: LatLng[], steps: ReadonlyArray<{ distance_m: number }>): LatLng[] {
  if (line.length === 0 || steps.length === 0) return [];
  const total = steps.reduce((sum, step) => sum + Math.max(0, step.distance_m), 0);
  const length = lineLengthM(line);
  const scale = total > 0 ? length / total : 0;
  const ends: LatLng[] = [];
  let cumulative = 0;
  steps.forEach((step, index) => {
    cumulative += Math.max(0, step.distance_m);
    const along = total > 0 ? cumulative * scale : (length * (index + 1)) / steps.length;
    ends.push(pointAlong(line, along) ?? line[line.length - 1]);
  });
  ends[ends.length - 1] = line[line.length - 1];
  return ends;
}

export type Nearest = { distance_m: number; point: LatLng };

/** The nearest point of the line to `position`, and how far away it is. */
export function nearestOnLine(line: LatLng[], position: LatLng): Nearest | null {
  if (line.length === 0) return null;
  if (line.length === 1) return { distance_m: distanceM(line[0], position), point: line[0] };
  // Metres per degree at this latitude, for the projection onto each segment.
  const kx = Math.cos(position.lat * RAD) * 111_320;
  const ky = 110_574;
  let best: Nearest | null = null;
  for (let index = 0; index + 1 < line.length; index += 1) {
    const a = line[index];
    const b = line[index + 1];
    const ax = (a.lng - position.lng) * kx;
    const ay = (a.lat - position.lat) * ky;
    const bx = (b.lng - position.lng) * kx;
    const by = (b.lat - position.lat) * ky;
    const dx = bx - ax;
    const dy = by - ay;
    const lengthSq = dx * dx + dy * dy;
    const t = lengthSq === 0 ? 0 : Math.min(1, Math.max(0, -(ax * dx + ay * dy) / lengthSq));
    const px = ax + dx * t;
    const py = ay + dy * t;
    const distance = Math.hypot(px, py);
    if (!best || distance < best.distance_m) {
      best = {
        distance_m: distance,
        point: { lat: a.lat + (b.lat - a.lat) * t, lng: a.lng + (b.lng - a.lng) * t }
      };
    }
  }
  return best;
}

export type Side = "left" | "right";

/**
 * Which side of the walker the route is on, from where they face. Null when there is
 * no heading, or the route is roughly ahead or behind (then no side is said).
 */
export function sideOfRoute(position: LatLng, headingDeg: number | null, routePoint: LatLng): Side | null {
  if (headingDeg === null || !Number.isFinite(headingDeg)) return null;
  const toRoute = bearingDeg(position, routePoint);
  const relative = ((toRoute - headingDeg + 540) % 360) - 180; // (-180, 180]
  if (relative >= 30 && relative <= 150) return "right";
  if (relative <= -30 && relative >= -150) return "left";
  return null;
}

// ----------------------------------------------------------- following the steps

export const TURN_M = 25;
export const OFF_ROUTE_M = 20;
export const OFF_ROUTE_FIXES = 2;

export type Step = { text: string; distance_m: number };

export type Fix = {
  position: LatLng;
  /** 0 = north, clockwise; null when the device does not know (standing still). */
  heading_deg: number | null;
};

export type FollowState = {
  /** Index of the current step. */
  index: number;
  /** Consecutive fixes off the route line. */
  offFixes: number;
  /** "Leaving the route" was said for this excursion. */
  offSaid: boolean;
  /** The end of the route was announced. */
  arrived: boolean;
};

export type FollowReading = {
  /** Metres to the end of the current step, from the fix. */
  distance_to_turn_m: number | null;
  /** Metres from the route line. */
  off_route_m: number | null;
};

export type FollowResult = { state: FollowState; say: string[]; reading: FollowReading };

export function initialFollowState(index = 0): FollowState {
  return { index, offFixes: 0, offSaid: false, arrived: false };
}

/** "Step 2 of 6: Turn right onto 104 Street." */
export function stepSentence(steps: ReadonlyArray<Step>, index: number): string {
  const step = steps[index];
  if (!step) return "";
  const text = step.text.trim().replace(/\.+$/, "");
  return `Step ${index + 1} of ${steps.length}: ${text}.`;
}

export function arrivalSentence(destination: string): string {
  return `You're at the end of the route, ${destination}.`;
}

export function offRouteSentence(side: Side | null): string {
  const base = "You seem to be leaving the route.";
  return side ? `${base} The path is to your ${side}.` : base;
}

/**
 * What the trip's large status line shows (OP-419): the current step, or the arrival
 * once the end is reached. "" when the route has no step at that index.
 */
export function currentSentence(
  steps: ReadonlyArray<Step>,
  index: number,
  arrived: boolean,
  destination: string
): string {
  if (arrived) return arrivalSentence(destination);
  return stepSentence(steps, index);
}

/** The Start button's label: the rank is said only when there are alternates. */
export function startRouteLabel(rank: number, routeCount: number): string {
  return routeCount > 1 ? `Start route ${rank}` : "Start route";
}

export class RouteFollower {
  readonly line: LatLng[];
  readonly steps: ReadonlyArray<Step>;
  readonly destination: string;
  readonly ends: LatLng[];

  // Plain fields, not parameter properties: Node's type stripping (test:lib) cannot
  // rewrite those.
  constructor(line: LatLng[], steps: ReadonlyArray<Step>, destination: string) {
    this.line = line;
    this.steps = steps;
    this.destination = destination;
    this.ends = stepEnds(line, steps);
  }

  /** Metres from the fix to the end of step `index`, or null without geometry. */
  distanceToTurn(index: number, position: LatLng): number | null {
    const end = this.ends[index];
    return end ? distanceM(position, end) : null;
  }

  /** Apply one position fix. Returns the new state and the sentences to say, in order. */
  onFix(state: FollowState, fix: Fix): FollowResult {
    const say: string[] = [];
    let next: FollowState = { ...state };

    // Steps: pass every end within reach, say only the last new step.
    let advanced = false;
    while (!next.arrived && next.index < this.steps.length) {
      const toEnd = this.distanceToTurn(next.index, fix.position);
      if (toEnd === null || toEnd > TURN_M) break;
      if (next.index + 1 < this.steps.length) {
        next = { ...next, index: next.index + 1 };
        advanced = true;
      } else {
        next = { ...next, arrived: true };
        if (advanced) say.push(stepSentence(this.steps, next.index));
        advanced = false;
        say.push(arrivalSentence(this.destination));
        break;
      }
    }
    if (advanced) say.push(stepSentence(this.steps, next.index));

    // Off the route line, on two fixes in a row, said once per excursion.
    const nearest = nearestOnLine(this.line, fix.position);
    let offRoute: number | null = null;
    if (nearest) {
      offRoute = nearest.distance_m;
      if (nearest.distance_m > OFF_ROUTE_M) {
        next = { ...next, offFixes: next.offFixes + 1 };
        if (next.offFixes >= OFF_ROUTE_FIXES && !next.offSaid) {
          next = { ...next, offSaid: true };
          say.push(offRouteSentence(sideOfRoute(fix.position, fix.heading_deg, nearest.point)));
        }
      } else {
        next = { ...next, offFixes: 0, offSaid: false };
      }
    }

    return {
      state: next,
      say,
      reading: { distance_to_turn_m: this.distanceToTurn(next.index, fix.position), off_route_m: offRoute }
    };
  }
}
