export type TravelProfile = "wheelchair" | "walker" | "low_vision" | "standard";

export type PlannerPlace = {
  id: string;
  name: string;
  description: string;
  coordinates: [number, number];
};

export type MockRoute = {
  id: string;
  label: string;
  minutes: number;
  distanceKm: number;
  avoidedItems: string[];
  note: string;
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
  { id: "walker", label: "Walker or cane", icon: "♿", description: "Prefer gentle, clear paths" },
  { id: "low_vision", label: "Low vision", icon: "◉", description: "Prioritize clear crossings" },
  { id: "standard", label: "Standard walking", icon: "●", description: "Fastest practical route" }
];

export const mockPlaces: PlannerPlace[] = [
  {
    id: "central-station",
    name: "Central Station",
    description: "10025 Jasper Avenue",
    coordinates: [-113.4939, 53.5447]
  },
  {
    id: "royal-alexandra",
    name: "Royal Alexandra Hospital",
    description: "10240 Kingsway NW",
    coordinates: [-113.4972, 53.5588]
  },
  {
    id: "downtown-library",
    name: "Stanley A. Milner Library",
    description: "7 Sir Winston Churchill Square",
    coordinates: [-113.4903, 53.5461]
  },
  {
    id: "university",
    name: "University of Alberta",
    description: "116 St & 85 Ave NW",
    coordinates: [-113.5263, 53.5232]
  }
];

const routeTemplates: Record<TravelProfile, MockRoute[]> = {
  wheelchair: [
    { id: "accessible", label: "Most accessible", minutes: 24, distanceKm: 1.8, avoidedItems: ["1 elevator outage", "stairs"], note: "Step-free crossings checked" },
    { id: "clear", label: "Fewer obstructions", minutes: 27, distanceKm: 2.1, avoidedItems: ["1 sidewalk closure", "stairs"], note: "Best surface confidence" },
    { id: "direct", label: "Direct route", minutes: 20, distanceKm: 1.5, avoidedItems: ["stairs"], note: "Includes a caution near 101 St" }
  ],
  walker: [
    { id: "clear", label: "Fewer obstructions", minutes: 21, distanceKm: 1.6, avoidedItems: ["1 sidewalk closure"], note: "Gentle grade preferred" },
    { id: "accessible", label: "Most accessible", minutes: 24, distanceKm: 1.8, avoidedItems: ["1 elevator outage", "stairs"], note: "Step-free crossings checked" },
    { id: "direct", label: "Direct route", minutes: 18, distanceKm: 1.4, avoidedItems: ["1 snow report"], note: "Includes a caution near 101 St" }
  ],
  low_vision: [
    { id: "clear", label: "Clear crossings", minutes: 23, distanceKm: 1.7, avoidedItems: ["1 complex crossing"], note: "Signalized crossings preferred" },
    { id: "accessible", label: "Most accessible", minutes: 25, distanceKm: 1.9, avoidedItems: ["1 elevator outage"], note: "Step-free crossings checked" },
    { id: "direct", label: "Direct route", minutes: 19, distanceKm: 1.5, avoidedItems: ["1 construction zone"], note: "Includes a caution near 101 St" }
  ],
  standard: [
    { id: "fastest", label: "Fastest route", minutes: 17, distanceKm: 1.3, avoidedItems: ["1 sidewalk closure"], note: "Fastest practical route" },
    { id: "clear", label: "Fewer obstructions", minutes: 21, distanceKm: 1.6, avoidedItems: ["1 sidewalk closure", "1 snow report"], note: "Clearer path, four minutes longer" },
    { id: "accessible", label: "Most accessible", minutes: 24, distanceKm: 1.8, avoidedItems: ["1 elevator outage", "stairs"], note: "Step-free crossings checked" }
  ]
};

export function getMockRoutes(query: PlannerQuery): MockRoute[] {
  return routeTemplates[query.profile];
}
