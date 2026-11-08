// Links that reopen a trip. The planner (`/`) and the route report (`/route`) read the
// same parameters, so a shared link opens the same routes:
//
//   from=lat,lng&from_name=…&to=lat,lng&to_name=…&profile=wheelchair
//   [&mode=walk_lrt] [&saved=<id>]                      both pages
//   [&route=2] [&demo=1]                                the report only
//
// GET /api/route takes the same lat,lng points. `saved` is a saved route's id (OP-306),
// passed through untouched. `demo=1` marks a report of the labelled demo fallback.
import { PlannerPlace, PlannerQuery, RouteMode, TravelProfile, isTravelProfile } from "./planner-types";

export type TripParams = {
  from: PlannerPlace | null;
  to: PlannerPlace | null;
  profile: TravelProfile;
  mode: RouteMode;
  saved: string | null;
};

export type ReportParams = PlannerQuery & {
  mode: RouteMode;
  rank: number;
  saved: string | null;
  /** The planner fell back to demo routes; the report shows the same labelled demo. */
  demo: boolean;
};

type LinkOptions = { saved?: string | null };

const NAME_MAX = 80;
const SAVED_MAX = 64;

function tripSearch(query: PlannerQuery, { saved }: LinkOptions = {}): URLSearchParams {
  const params = new URLSearchParams({
    from: latLng(query.from),
    from_name: query.from.name,
    to: latLng(query.to),
    to_name: query.to.name,
    profile: query.profile
  });
  if (query.mode === "walk_lrt") params.set("mode", "walk_lrt");
  if (saved) params.set("saved", saved);
  return params;
}

/** `/?from=…&to=…&profile=…`: the planner, which runs the search when it opens. */
export function plannerHref(query: PlannerQuery, options: LinkOptions = {}): string {
  return `/?${tripSearch(query, options).toString()}`;
}

/** `/route?…&route=2`: the full report for one route of the trip. */
export function reportHref(query: PlannerQuery, rank: number, options: LinkOptions & { demo?: boolean } = {}): string {
  const params = tripSearch(query, options);
  params.set("route", String(rank));
  if (options.demo) params.set("demo", "1");
  return `/route?${params.toString()}`;
}

/** The trip in a planner or report URL. Missing or broken points come back as null. */
export function parseTripParams(params: URLSearchParams): TripParams {
  const profileParam = params.get("profile");
  const saved = (params.get("saved") ?? "").trim().slice(0, SAVED_MAX);
  return {
    from: place("from", params),
    to: place("to", params),
    profile: isTravelProfile(profileParam) ? profileParam : "wheelchair",
    mode: params.get("mode") === "walk_lrt" ? "walk_lrt" : "walk",
    saved: saved || null
  };
}

/** The report's trip, or null when the URL does not hold a whole trip. */
export function parseReportParams(params: URLSearchParams): ReportParams | null {
  const trip = parseTripParams(params);
  if (!trip.from || !trip.to) return null;
  const rank = Number(params.get("route"));
  return {
    from: trip.from,
    to: trip.to,
    profile: trip.profile,
    mode: trip.mode,
    saved: trip.saved,
    rank: Number.isInteger(rank) && rank >= 1 && rank <= 3 ? rank : 1,
    demo: params.get("demo") === "1"
  };
}

/** A place for a bare point, when the link carries no name. */
export function pointPlace(field: "from" | "to", lat: number, lng: number, name?: string): PlannerPlace {
  return {
    id: `${field}-${lat.toFixed(6)},${lng.toFixed(6)}`,
    name: name || (field === "from" ? "Start point" : "Destination point"),
    description: `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
    coordinates: [lng, lat]
  };
}

function place(field: "from" | "to", params: URLSearchParams): PlannerPlace | null {
  const point = (params.get(field) ?? "").split(",").map((part) => Number(part.trim()));
  if (point.length !== 2 || point.some((value) => !Number.isFinite(value))) return null;
  const [lat, lng] = point;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  const name = (params.get(`${field}_name`) ?? "").trim().slice(0, NAME_MAX);
  return pointPlace(field, lat, lng, name);
}

function latLng(place: PlannerPlace): string {
  const [lng, lat] = place.coordinates;
  return `${lat.toFixed(6)},${lng.toFixed(6)}`;
}
