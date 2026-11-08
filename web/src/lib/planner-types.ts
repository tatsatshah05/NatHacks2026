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
};

export const travelProfiles: Array<{
  id: TravelProfile;
  label: string;
  icon: string;
  description: string;
}> = [
  { id: "wheelchair", label: "Wheelchair", icon: "♿", description: "Avoid stairs and blocked paths" },
  { id: "walker_cane", label: "Walker or cane", icon: "◒", description: "Prefer gentle, clear paths" },
  { id: "low_vision", label: "Low vision", icon: "◉", description: "Prioritize clear crossings" },
  { id: "standard", label: "Standard walking", icon: "●", description: "Fastest practical route" }
];

export function profileLabel(profile: TravelProfile): string {
  return travelProfiles.find((option) => option.id === profile)?.label ?? profile;
}

export function isTravelProfile(value: string | null): value is TravelProfile {
  return travelProfiles.some((option) => option.id === value);
}
