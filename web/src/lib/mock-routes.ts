/**
 * Demo data for the planner (OP-210) and the route report (OP-211) until the web app
 * calls GET /api/route. It is typed from the generated api-types.ts, so a change to the
 * API contract breaks the build here first. Nothing in this file is a real route or a
 * current condition, and every screen that shows it says so.
 */
import type {
  Category,
  HazardOut,
  ItemStatus,
  PlannerPlace,
  PlannerQuery,
  ReportGroup,
  ReportItem,
  RouteOut,
  RouteResponse,
  SourceStatus,
  StatusResponse,
  TravelProfile
} from "./planner-types";
import { CITYWIDE_NOTE as CITYWIDE } from "./report-format";

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

// Assumed walking speeds from config/profiles.json.
const SPEED_M_S: Record<TravelProfile, number> = {
  wheelchair: 1.0,
  walker_cane: 0.9,
  low_vision: 1.0,
  standard: 1.3
};

// Same wording as cloud/app/maps_links.py APPLE_MAPS_NOTE.
const APPLE_MAPS_NOTE =
  "Apple Maps may not keep this detour; check the route it shows before you set off.";

// The report's groups, in the order and with the labels of cloud/app/report.py.
const GROUPS: Array<[Category, string]> = [
  ["indoor", "Indoor"],
  ["sidewalk", "Sidewalks and paths"],
  ["road", "Roads and crossings"],
  ["weather", "Weather and ice"]
];

const CITY = "City of Edmonton Open Data";
const STATION_NOTE =
  "Reported for the whole station: the exact elevator or escalator is not mapped, " +
  "so routes cannot avoid it.";
// Same wording as cloud/app/report.py NOTES["citywide"].
const CITYWIDE_NOTE = CITYWIDE;

type DemoItem = Omit<ReportItem, "status" | "distance_m" | "observed_at" | "starts_at" | "ends_at"> & {
  category: Category;
  checkedMinAgo: number;
  startedHoursAgo: number | null;
  endsInHours: number | null;
};

const ITEMS = {
  elevator: {
    id: "city_lrt_devices:demo-elevator",
    category: "indoor",
    kind: "elevator_out",
    effect: "blocked",
    confidence: "high",
    title: "Elevator out of service",
    detail: "Demo station, street level to concourse",
    source: "city_lrt_devices",
    attribution: CITY,
    url: "https://data.edmonton.ca/d/snws-u3zx",
    note: null,
    checkedMinAgo: 2,
    startedHoursAgo: 20,
    endsInHours: null
  },
  escalator: {
    id: "city_lrt_devices:demo-escalator",
    category: "indoor",
    kind: "escalator_out",
    effect: "caution",
    confidence: "high",
    title: "Escalator out of service",
    detail: "Demo station, east entrance, up",
    source: "city_lrt_devices",
    attribution: CITY,
    url: "https://data.edmonton.ca/d/snws-u3zx",
    note: STATION_NOTE,
    checkedMinAgo: 2,
    startedHoursAgo: 6,
    endsInHours: null
  },
  construction: {
    id: "city_construction:demo-hoarding",
    category: "sidewalk",
    kind: "sidewalk_closed",
    effect: "blocked",
    confidence: "high",
    title: "Sidewalk closed for construction",
    detail: "Hoarding covers the sidewalk on one side of the street. The other side is open.",
    source: "city_construction",
    attribution: CITY,
    url: "https://data.edmonton.ca/d/7wiq-4rgy",
    note: null,
    checkedMinAgo: 14,
    startedHoursAgo: 24 * 30,
    endsInHours: 24 * 5
  },
  trail: {
    id: "city_trail_closures:demo-caution",
    category: "sidewalk",
    kind: "trail_caution",
    effect: "caution",
    confidence: "high",
    title: "Trail caution: shared-use path",
    detail: "Crews are replacing light poles beside the path.",
    source: "city_trail_closures",
    attribution: CITY,
    url: "https://data.edmonton.ca/d/k4mi-dkvi",
    note: null,
    checkedMinAgo: 9,
    startedHoursAgo: 30,
    endsInHours: 48
  },
  roadClosure: {
    id: "city_traffic_disruptions:demo-lane",
    category: "road",
    kind: "road_closed",
    effect: "caution",
    confidence: "high",
    title: "Road partly closed",
    detail: "One lane is closed. Sidewalks may be affected.",
    source: "city_traffic_disruptions",
    attribution: CITY,
    url: "https://data.edmonton.ca/d/k4tx-5k8p",
    note: null,
    checkedMinAgo: 6,
    startedHoursAgo: 48,
    endsInHours: 36
  },
  plow: {
    id: "city_snow_clearing:demo-ridge",
    category: "road",
    kind: "plow_passed",
    effect: "caution",
    confidence: "medium",
    title: "Plow passed. Curb ramps may be blocked by snow ridges.",
    detail: "Residential street cleared in the last 12 hours.",
    source: "city_snow_clearing",
    attribution: CITY,
    url: "https://data.edmonton.ca/d/8pdx-hfxi",
    note: null,
    checkedMinAgo: 135,
    startedHoursAgo: 10,
    endsInHours: null
  },
  cameraIce: {
    id: "camera_model:demo-crossing",
    category: "weather",
    kind: "ice_risk",
    effect: "ice_risk",
    confidence: "medium",
    title: "Ice risk at a crossing",
    detail: "A road camera reads the road as wet while the ice-risk watch is active.",
    source: "camera_model",
    attribution: "Road camera data: 511 Alberta, Government of Alberta",
    url: "https://511.alberta.ca/",
    note: null,
    checkedMinAgo: 4,
    startedHoursAgo: 1,
    endsInHours: null
  },
  watch: {
    id: "weather_watch:demo-citywide",
    category: "weather",
    kind: "ice_risk",
    effect: "caution",
    confidence: "medium",
    title: "Ice-risk watch for the whole city",
    detail: "Air is near 0 °C and it rained in the last 6 hours.",
    source: "weather_watch",
    attribution: "Environment and Climate Change Canada; Open-Meteo",
    url: "https://weather.gc.ca/",
    note: CITYWIDE_NOTE,
    checkedMinAgo: 16,
    startedHoursAgo: 3,
    endsInHours: null
  }
} satisfies Record<string, DemoItem>;

type ItemKey = keyof typeof ITEMS;
// An item, how it relates to the route, and metres from the route line (null: over 50 m).
type Mention = [ItemKey, ItemStatus, number | null];
type DemoRoute = { detour: number; labels: string[]; mentions: Mention[] };

// Step labels as the network names paths; each route splits its length over them.
const LABELS_A = ["footway", "sidewalk", "crosswalk", "sidewalk", "footway"];
const LABELS_B = ["service", "sidewalk", "shared-use path", "crosswalk", "sidewalk", "footway"];
const LABELS_C = ["footway", "shared-use path", "sidewalk", "crosswalk", "sidewalk"];

// Three demo routes per profile. `detour` is the length compared with the plain
// shortest route; every profile avoids what config/profiles.json removes for it.
const ROUTES: Record<TravelProfile, DemoRoute[]> = {
  wheelchair: [
    {
      detour: 1.06,
      labels: LABELS_A,
      mentions: [
        ["elevator", "avoided", null],
        ["construction", "avoided", null],
        ["watch", "on_route", 0],
        ["plow", "on_route", 0],
        ["escalator", "nearby", 12],
        ["roadClosure", "nearby", 31]
      ]
    },
    {
      detour: 1.12,
      labels: LABELS_B,
      mentions: [
        ["construction", "avoided", null],
        ["watch", "on_route", 0],
        ["cameraIce", "on_route", 0],
        ["trail", "nearby", 18]
      ]
    },
    {
      detour: 1.2,
      labels: LABELS_C,
      mentions: [
        ["elevator", "avoided", null],
        ["construction", "avoided", 22],
        ["watch", "on_route", 0]
      ]
    }
  ],
  walker_cane: [
    {
      detour: 1.05,
      labels: LABELS_A,
      mentions: [
        ["construction", "avoided", null],
        ["cameraIce", "avoided", null],
        ["watch", "on_route", 0],
        ["roadClosure", "nearby", 31]
      ]
    },
    {
      detour: 1.1,
      labels: LABELS_B,
      mentions: [
        ["cameraIce", "avoided", null],
        ["watch", "on_route", 0],
        ["plow", "on_route", 0],
        ["construction", "nearby", 15]
      ]
    },
    {
      detour: 1.18,
      labels: LABELS_C,
      mentions: [
        ["construction", "avoided", null],
        ["watch", "on_route", 0],
        ["trail", "on_route", 0]
      ]
    }
  ],
  low_vision: [
    {
      detour: 1.04,
      labels: LABELS_A,
      mentions: [
        ["construction", "avoided", null],
        ["watch", "on_route", 0],
        ["roadClosure", "on_route", 0],
        ["trail", "nearby", 25]
      ]
    },
    {
      detour: 1.09,
      labels: LABELS_B,
      mentions: [
        ["construction", "avoided", null],
        ["watch", "on_route", 0],
        ["cameraIce", "on_route", 0]
      ]
    },
    {
      detour: 1.16,
      labels: LABELS_C,
      mentions: [
        ["elevator", "avoided", null],
        ["watch", "on_route", 0],
        ["escalator", "nearby", 12]
      ]
    }
  ],
  standard: [
    {
      detour: 1.03,
      labels: LABELS_A,
      mentions: [
        ["construction", "avoided", null],
        ["watch", "on_route", 0],
        ["cameraIce", "on_route", 0],
        ["roadClosure", "nearby", 31]
      ]
    },
    {
      detour: 1.08,
      labels: LABELS_B,
      mentions: [
        ["construction", "avoided", null],
        ["watch", "on_route", 0],
        ["trail", "nearby", 20]
      ]
    },
    {
      detour: 1.15,
      labels: LABELS_C,
      mentions: [
        ["elevator", "avoided", null],
        ["watch", "on_route", 0],
        ["plow", "on_route", 0]
      ]
    }
  ]
};

const STATUS_ORDER: Record<ItemStatus, number> = { on_route: 0, nearby: 1, avoided: 2 };
const EFFECT_ORDER: Record<ReportItem["effect"], number> = { blocked: 0, snow: 1, ice_risk: 2, caution: 3 };
// Effects that change a route's cost for a profile (config/profiles.json multipliers).
const COSTLY: Record<TravelProfile, ReadonlyArray<ReportItem["effect"]>> = {
  wheelchair: ["ice_risk"],
  walker_cane: ["snow"],
  low_vision: ["snow", "ice_risk"],
  standard: ["snow", "ice_risk"]
};

export type MockOptions = {
  /** Routes with no known obstructions, to show the report's empty state. */
  empty?: boolean;
};

/** A typed demo GET /api/route response. `now` dates the "checked" times. */
export function getMockRouteResponse(query: PlannerQuery, now: Date, options: MockOptions = {}): RouteResponse {
  const { from, to, profile } = query;
  // The plain shortest route: the straight line plus a typical street-grid detour.
  const plainM = Math.max(400, straightLineM(from.coordinates, to.coordinates) * 1.25);
  const speed = SPEED_M_S[profile];

  const routes = ROUTES[profile].map((demo, index): RouteOut => {
    const distance = round1(plainM * demo.detour);
    const etaMin = round1(distance / speed / 60);
    const extraMin = (distance - plainM) / speed / 60;
    const entries = options.empty ? [] : demo.mentions.map((mention) => entry(mention, now));
    const items = entries.map(({ item }) => item);
    const avoided = items.filter((item) => item.status === "avoided");
    avoided.sort((a, b) => Number(a.effect !== "blocked") - Number(b.effect !== "blocked") || a.id.localeCompare(b.id));
    const onRoute = items.filter((item) => item.status === "on_route" && COSTLY[profile].includes(item.effect));
    const first = avoided[0];
    const waypoints = index === 0 ? [] : [detourPoint(from.coordinates, to.coordinates, index)];

    return {
      rank: index + 1,
      geometry: { type: "LineString", coordinates: [from.coordinates, ...waypoints, to.coordinates] },
      distance_m: distance,
      eta_min: etaMin,
      steps: steps(demo.labels, distance),
      hazards_on_route: onRoute.map(hazard),
      hazards_avoided: avoided.map(hazard),
      warnings: [],
      reason: first ? `Avoids ${first.title}` + (extraMin >= 0.5 ? `; +${Math.round(extraMin)} min` : "") : null,
      report: report(entries),
      google_maps_url: googleMapsUrl(from.coordinates, to.coordinates, waypoints),
      apple_maps_url: appleMapsUrl(from.coordinates, to.coordinates)
    };
  });

  return {
    profile,
    generated_at: now.toISOString(),
    origin_snap_m: 0,
    destination_snap_m: 0,
    routes,
    warnings: [],
    apple_maps_note: APPLE_MAPS_NOTE
  };
}

type Entry = { category: Category; item: ReportItem };

function entry([key, status, distance]: Mention, now: Date): Entry {
  const { category, checkedMinAgo, startedHoursAgo, endsInHours, ...item } = ITEMS[key];
  return {
    category,
    item: {
      ...item,
      status,
      distance_m: distance,
      observed_at: shift(now, -checkedMinAgo * 60),
      starts_at: startedHoursAgo === null ? null : shift(now, -startedHoursAgo * 3600),
      ends_at: endsInHours === null ? null : shift(now, endsInHours * 3600)
    }
  };
}

// Grouped and ordered as cloud/app/report.py: on the route, nearby, avoided; then
// blocked, snow, ice risk, caution; then nearest first. Every group, even when empty.
function report(entries: Entry[]): RouteOut["report"] {
  const sorted = [...entries].sort(
    ({ item: a }, { item: b }) =>
      STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
      EFFECT_ORDER[a.effect] - EFFECT_ORDER[b.effect] ||
      (a.distance_m ?? Infinity) - (b.distance_m ?? Infinity)
  );
  const groups: ReportGroup[] = GROUPS.map(([category, label]) => ({
    category,
    label,
    items: sorted.filter((entry) => entry.category === category).map((entry) => entry.item)
  }));
  const count = (status: ItemStatus) => entries.filter(({ item }) => item.status === status).length;
  return { on_route: count("on_route"), nearby: count("nearby"), avoided: count("avoided"), groups };
}

function hazard(item: ReportItem): HazardOut {
  return { item_id: item.id, effect: item.effect, confidence: item.confidence, title: item.title };
}

function steps(labels: string[], distance: number): RouteOut["steps"] {
  // Split the length over the labels; ends are short, the middle carries the walk.
  const weights = labels.map((label, index) => (label === "crosswalk" ? 0.03 : index % 2 ? 1.6 : 1));
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  return labels.map((label, index) => {
    const metres = (distance * weights[index]) / total;
    return { text: `Follow ${label} for ${roundStep(metres)} m`, distance_m: round1(metres) };
  });
}

// As cloud/app/routing.py _step_text: nearest 10 m from 10 m up.
function roundStep(metres: number): number {
  return metres >= 10 ? Math.round(metres / 10) * 10 : Math.round(metres);
}

function straightLineM([lng1, lat1]: [number, number], [lng2, lat2]: [number, number]): number {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLng = (lng2 - lng1) * rad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

function detourPoint(from: [number, number], to: [number, number], index: number): [number, number] {
  const offset = 0.002 * (index % 2 ? 1 : -1);
  return [round6((from[0] + to[0]) / 2 + offset), round6((from[1] + to[1]) / 2)];
}

function latLng([lng, lat]: [number, number]): string {
  return `${lat.toFixed(6)},${lng.toFixed(6)}`;
}

// The OP-209 link formats (cloud/app/maps_links.py).
function googleMapsUrl(from: [number, number], to: [number, number], waypoints: Array<[number, number]>): string {
  const params = new URLSearchParams({
    api: "1",
    origin: latLng(from),
    destination: latLng(to),
    travelmode: "walking"
  });
  if (waypoints.length) params.set("waypoints", waypoints.map(latLng).join("|"));
  return `https://www.google.com/maps/dir/?${params.toString()}`;
}

function appleMapsUrl(from: [number, number], to: [number, number]): string {
  const params = new URLSearchParams({ saddr: latLng(from), daddr: latLng(to), dirflg: "w" });
  return `https://maps.apple.com/?${params.toString()}`;
}

function shift(now: Date, seconds: number): string {
  return new Date(now.getTime() + seconds * 1000).toISOString();
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

// Each scheduled source, its dataset and how long ago the demo last read it. Snow
// clearing is out of date on purpose, so the report shows how a stale source reads.
const SOURCES: Array<[source: string, url: string, cadenceS: number, ageMin: number, items: number]> = [
  ["city_lrt_devices", "https://data.edmonton.ca/d/snws-u3zx", 120, 2, 7],
  ["city_311_snow", "https://data.edmonton.ca/d/9i6v-cqzn", 3600, 41, 12],
  ["city_trail_closures", "https://data.edmonton.ca/d/k4mi-dkvi", 900, 9, 44],
  ["city_construction", "https://data.edmonton.ca/d/7wiq-4rgy", 3600, 14, 5],
  ["city_snow_clearing", "https://data.edmonton.ca/d/8pdx-hfxi", 900, 135, 31],
  ["city_traffic_disruptions", "https://data.edmonton.ca/d/k4tx-5k8p", 900, 6, 4],
  ["weather_watch", "https://api.weather.gc.ca/", 900, 16, 1]
];
const STALE = new Set(["city_snow_clearing"]);

/** A typed demo GET /api/status response, for source freshness. */
export function getMockStatus(now: Date): StatusResponse {
  const sources = SOURCES.map(([source, url, cadenceS, ageMin, items]): SourceStatus => {
    const stale = STALE.has(source);
    const lastSuccess = shift(now, -ageMin * 60);
    return {
      source,
      cadence_s: cadenceS,
      url,
      last_attempt: stale ? shift(now, -60) : lastSuccess,
      last_success: lastSuccess,
      last_error: stale ? "Timed out after 20 s" : null,
      consecutive_failures: stale ? 3 : 0,
      item_count: items,
      skipped_items: 0,
      stale,
      age_s: ageMin * 60
    };
  });
  return { mode: "demo", version: "demo", scheduler_running: true, generated_at: now.toISOString(), sources };
}
