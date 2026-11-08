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

export type RouteReportItem = {
  id: string;
  category: "Indoor" | "Sidewalks and paths" | "Roads and crossings" | "Weather and ice";
  icon: string;
  title: string;
  detail: string;
  source: string;
  sourceUrl: string;
  updatedMinutesAgo: number;
};

export type RouteStep = {
  number: number;
  instruction: string;
  detail: string;
};

export const mockRouteReportItems: RouteReportItem[] = [
  {
    id: "central-elevator",
    category: "Indoor",
    icon: "⇅",
    title: "Central Station elevator out of service",
    detail: "Use the street-level entrance on 100 Street instead.",
    source: "City of Edmonton Open Data",
    sourceUrl: "https://data.edmonton.ca/",
    updatedMinutesAgo: 3
  },
  {
    id: "jasper-sidewalk",
    category: "Sidewalks and paths",
    icon: "▣",
    title: "Sidewalk closure on 100 Street",
    detail: "The route uses the east-side sidewalk around the work zone.",
    source: "City construction permits",
    sourceUrl: "https://data.edmonton.ca/",
    updatedMinutesAgo: 8
  },
  {
    id: "crossing-caution",
    category: "Roads and crossings",
    icon: "⊘",
    title: "Crossing caution at 101 Street",
    detail: "Allow extra time at the signalized crossing.",
    source: "City traffic disruptions",
    sourceUrl: "https://data.edmonton.ca/",
    updatedMinutesAgo: 12
  },
  {
    id: "snow-risk",
    category: "Weather and ice",
    icon: "❄",
    title: "Snow reported near Jasper Avenue",
    detail: "Surface may be uneven; use caution near curb ramps.",
    source: "Open-Meteo weather and community reports",
    sourceUrl: "https://open-meteo.com/",
    updatedMinutesAgo: 16
  }
];

export const mockRouteSteps: RouteStep[] = [
  { number: 1, instruction: "Exit Central Station at 100 Street.", detail: "Use the street-level entrance because the station elevator is unavailable." },
  { number: 2, instruction: "Continue north on the east-side sidewalk.", detail: "Stay east of the sidewalk closure and follow the temporary signs." },
  { number: 3, instruction: "Cross Jasper Avenue at the signalized crossing.", detail: "The crossing at 101 Street has a current caution." },
  { number: 4, instruction: "Turn east toward Royal Alexandra Hospital.", detail: "The destination entrance is ahead on the north side of Kingsway." }
];

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
