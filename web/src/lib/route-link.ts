// The route report's URL: the trip, the profile and the chosen route, so a report can
// be reloaded or shared. GET /api/route takes the same lat,lng points.
import { mockPlaces } from "./mock-routes";
import { PlannerPlace, PlannerQuery, TravelProfile, isTravelProfile } from "./planner-types";

export type ReportParams = PlannerQuery & {
  rank: number;
  empty: boolean;
  /** True when the URL had no usable trip and the demo trip is shown instead. */
  usedDefaultTrip: boolean;
};

const NAME_MAX = 80;

export function reportHref(query: PlannerQuery, rank: number): string {
  const params = new URLSearchParams({
    from: latLng(query.from),
    from_name: query.from.name,
    to: latLng(query.to),
    to_name: query.to.name,
    profile: query.profile,
    route: String(rank)
  });
  return `/route?${params.toString()}`;
}

export function parseReportParams(params: URLSearchParams): ReportParams {
  const profileParam = params.get("profile");
  const profile: TravelProfile = isTravelProfile(profileParam) ? profileParam : "wheelchair";
  const rank = Number(params.get("route"));
  const from = place("from", params);
  const to = place("to", params);
  const trip = from && to ? { from, to } : { from: mockPlaces[0], to: mockPlaces[1] };
  return {
    ...trip,
    profile,
    rank: Number.isInteger(rank) && rank >= 1 && rank <= 3 ? rank : 1,
    empty: params.get("demo") === "empty",
    usedDefaultTrip: !from || !to
  };
}

function place(field: "from" | "to", params: URLSearchParams): PlannerPlace | null {
  const point = (params.get(field) ?? "").split(",").map(Number);
  if (point.length !== 2 || point.some((value) => !Number.isFinite(value))) return null;
  const [lat, lng] = point;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  const name = (params.get(`${field}_name`) ?? "").trim().slice(0, NAME_MAX);
  return {
    id: `${field}-${lat},${lng}`,
    name: name || (field === "from" ? "Start" : "Destination"),
    description: `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
    coordinates: [lng, lat]
  };
}

function latLng(place: PlannerPlace): string {
  const [lng, lat] = place.coordinates;
  return `${lat.toFixed(5)},${lng.toFixed(5)}`;
}
