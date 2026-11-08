import type { CameraFeature, CameraReading } from "@/lib/city";

/** One physical camera and its views (each view looks in one direction). */
export type CameraGroup = {
  id: string; // "camera:56"
  cameraId: number;
  lng: number;
  lat: number;
  views: CameraFeature[]; // by view id
  /** The most serious known reading among the views, for the marker. */
  headline: CameraReading;
};

// Most serious first; "unknown" never outranks a real reading.
const SEVERITY: Record<CameraReading["label"], number> = {
  ice: 5,
  snow_covered: 4,
  snow_partial: 3,
  wet: 2,
  dry: 1,
  unknown: 0
};

export function groupCameras(features: readonly CameraFeature[]): CameraGroup[] {
  const groups = new Map<number, CameraFeature[]>();
  for (const feature of features) {
    const id = feature.properties.camera_id;
    groups.set(id, [...(groups.get(id) ?? []), feature]);
  }
  return [...groups.entries()]
    .map(([cameraId, views]) => {
      const sorted = [...views].sort((a, b) => a.properties.view_id - b.properties.view_id);
      const headline = sorted
        .map((view) => view.properties)
        .reduce((best, reading) => (SEVERITY[reading.label] > SEVERITY[best.label] ? reading : best));
      const [lng, lat] = sorted[0].geometry.coordinates;
      return { id: `camera:${cameraId}`, cameraId, lng, lat, views: sorted, headline };
    })
    .sort((a, b) => SEVERITY[b.headline.label] - SEVERITY[a.headline.label] || a.cameraId - b.cameraId);
}

/** The latest frame time among a camera's views. */
export function latestFrame(group: CameraGroup): string {
  return group.views.map((view) => view.properties.observed_at).reduce((a, b) => (a > b ? a : b));
}

export type CameraIncident = NonNullable<CameraReading["incident"]>;

/**
 * OP-415: the newest view answer saying a vehicle in view appeared to have lost control,
 * or null. One line per camera, never per view; display only and unverified.
 */
export function latestIncident(group: CameraGroup): CameraIncident | null {
  return group.views
    .map((view) => view.properties.incident ?? null)
    .filter((incident): incident is CameraIncident => incident !== null && incident.seen)
    .reduce<CameraIncident | null>((newest, incident) => (!newest || incident.observed_at > newest.observed_at ? incident : newest), null);
}
