import type { components } from "./api-types";

export type TravelProfile = "wheelchair" | "walker_cane" | "low_vision" | "standard";
export type RouteResponse = components["schemas"]["RouteResponse"];
export type RouteOut = components["schemas"]["RouteOut"];

export type PlannerPlace = {
  id: string;
  name: string;
  description: string;
  coordinates: [number, number];
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

export const mockPlaces: PlannerPlace[] = [
  { id: "central-station", name: "Central Station", description: "10025 Jasper Avenue", coordinates: [-113.4939, 53.5447] },
  { id: "royal-alexandra", name: "Royal Alexandra Hospital", description: "10240 Kingsway NW", coordinates: [-113.4972, 53.5588] },
  { id: "downtown-library", name: "Stanley A. Milner Library", description: "7 Sir Winston Churchill Square", coordinates: [-113.4903, 53.5461] },
  { id: "university", name: "University of Alberta", description: "116 St & 85 Ave NW", coordinates: [-113.5263, 53.5232] }
];

function route(
  rank: number,
  eta_min: number,
  distance_m: number,
  title: string,
  hazards_avoided: string[]
): RouteOut {
  return {
    rank,
    geometry: {},
    distance_m,
    eta_min,
    steps: [{ text: "Follow the accessible walking network to your destination.", distance_m }],
    hazards_on_route: [],
    hazards_avoided: hazards_avoided.map((item_id, index) => ({
      item_id: `${item_id}-${index}`,
      effect: "blocked",
      confidence: "high",
      title: item_id
    })),
    warnings: [],
    reason: title,
    google_maps_url: null,
    apple_maps_url: null
  };
}

export const mockRouteResponses: Record<TravelProfile, RouteResponse> = {
  wheelchair: {
    profile: "wheelchair",
    generated_at: "2026-11-07T22:00:00Z",
    origin_snap_m: 0,
    destination_snap_m: 0,
    warnings: [],
    routes: [
      route(1, 24, 1800, "Most accessible", ["elevator outage", "stairs"]),
      route(2, 27, 2100, "Fewer obstructions", ["sidewalk closure", "stairs"]),
      route(3, 20, 1500, "Direct route", ["stairs"])
    ]
  },
  walker_cane: {
    profile: "walker_cane",
    generated_at: "2026-11-07T22:00:00Z",
    origin_snap_m: 0,
    destination_snap_m: 0,
    warnings: [],
    routes: [
      route(1, 21, 1600, "Fewer obstructions", ["sidewalk closure"]),
      route(2, 24, 1800, "Most accessible", ["elevator outage", "stairs"]),
      route(3, 18, 1400, "Direct route", ["snow report"])
    ]
  },
  low_vision: {
    profile: "low_vision",
    generated_at: "2026-11-07T22:00:00Z",
    origin_snap_m: 0,
    destination_snap_m: 0,
    warnings: [],
    routes: [
      route(1, 23, 1700, "Clear crossings", ["complex crossing"]),
      route(2, 25, 1900, "Most accessible", ["elevator outage"]),
      route(3, 19, 1500, "Direct route", ["construction zone"])
    ]
  },
  standard: {
    profile: "standard",
    generated_at: "2026-11-07T22:00:00Z",
    origin_snap_m: 0,
    destination_snap_m: 0,
    warnings: [],
    routes: [
      route(1, 17, 1300, "Fastest route", ["sidewalk closure"]),
      route(2, 21, 1600, "Fewer obstructions", ["sidewalk closure", "snow report"]),
      route(3, 24, 1800, "Most accessible", ["elevator outage", "stairs"])
    ]
  }
};

export function getMockRoutes(query: PlannerQuery): RouteOut[] {
  return mockRouteResponses[query.profile].routes;
}
