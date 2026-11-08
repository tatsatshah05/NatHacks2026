import type { components } from "./api-types";

type Schemas = components["schemas"];

// API shapes come only from the generated api-types.ts (`make types`).
export type RouteResponse = Schemas["RouteResponse"];
export type RouteOut = Schemas["RouteOut"];
export type HazardOut = Schemas["HazardOut"];
export type RouteReport = Schemas["RouteReportOut"];
export type ReportGroup = Schemas["ReportGroupOut"];
export type ReportItem = Schemas["ReportItemOut"];
export type Category = Schemas["Category"];
export type ItemStatus = Schemas["Status"];
export type StatusResponse = Schemas["StatusResponse"];
export type SourceStatus = Schemas["SourceStatus"];
export type SearchResult = Schemas["SearchResult"];
// OP-311: a walk and LRT trip's legs and stations (set only with mode=walk_lrt).
export type LegOut = Schemas["LegOut"];
export type LrtTripOut = Schemas["LrtTripOut"];
export type StationOut = Schemas["StationOut"];
export type RouteMode = NonNullable<RouteResponse["mode"]>;

// The profile ids of config/profiles.json and GET /api/route.
export type TravelProfile = "wheelchair" | "walker_cane" | "low_vision" | "standard";
export type PlaceField = "from" | "to";

export type PlannerPlace = {
  id: string;
  name: string;
  description: string;
  coordinates: [number, number]; // [lng, lat], as in GeoJSON
};

export type PlannerQuery = {
  from: PlannerPlace;
  to: PlannerPlace;
  profile: TravelProfile;
  /** "walk_lrt" asks GET /api/route for walk and LRT trips too (OP-311). */
  mode?: RouteMode;
};

export const travelProfiles: ReadonlyArray<{
  id: TravelProfile;
  label: string;
  /** Reads after "routes": "3 routes for a wheelchair". */
  phrase: string;
}> = [
  { id: "wheelchair", label: "Wheelchair", phrase: "for a wheelchair" },
  { id: "walker_cane", label: "Walker or cane", phrase: "for a walker or cane" },
  { id: "low_vision", label: "Low vision", phrase: "for low vision" },
  { id: "standard", label: "Standard walking", phrase: "for standard walking" }
];

export function profileLabel(profile: TravelProfile): string {
  return travelProfiles.find((option) => option.id === profile)?.label ?? profile;
}

export function profilePhrase(profile: TravelProfile): string {
  return travelProfiles.find((option) => option.id === profile)?.phrase ?? `for ${profile}`;
}

export function isTravelProfile(value: string | null): value is TravelProfile {
  return travelProfiles.some((option) => option.id === value);
}
