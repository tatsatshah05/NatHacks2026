export type ObstructionKind =
  | "elevator_out"
  | "escalator_out"
  | "sidewalk_closed"
  | "road_closed"
  | "trail_closed"
  | "trail_caution"
  | "plow_passed"
  | "snow_complaints"
  | "snow"
  | "ice_risk"
  | "blocked"
  | string;

export type Obstruction = {
  id: string;
  category: "indoor" | "sidewalk" | "road" | "weather" | "transit" | string;
  kind: ObstructionKind;
  effect: "blocked" | "snow" | "ice_risk" | "caution" | string;
  title: string;
  detail?: string | null;
  geometry: { type: "Point"; coordinates: [number, number] };
  confidence?: "high" | "medium" | "low" | string;
  observed_at?: string | null;
  expires_at?: string | null;
  url?: string | null;
  attribution?: string | null;
};

export type ObstructionCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    id?: string;
    geometry: Obstruction["geometry"];
    properties: Omit<Obstruction, "geometry"> & { id?: string };
  }>;
};

export function featureToObstruction(
  feature: ObstructionCollection["features"][number]
): Obstruction {
  return {
    ...feature.properties,
    id: feature.properties.id ?? String(feature.id ?? crypto.randomUUID()),
    geometry: feature.geometry
  };
}
