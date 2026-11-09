// Every data source OpenPath uses, for /sources (OP-312). The facts come from
// docs/DATA_SOURCES.md, PLAN.md section 6 and the adapters; the dates are when the
// team measured or built each one. When a source changes there, change it here.

/** How fresh a source is: a live check from the API, or a fixed fact about a build. */
export type Freshness =
  | { kind: "status"; key: string } // a source in GET /api/status
  | { kind: "cameras" } // GET /api/cameras
  | { kind: "fixed"; text: string };

export type Link = { text: string; href: string };

export type SourceRow = {
  name: string;
  /** The official page for the source, when it has one. */
  href?: string;
  /** Dataset ids and other identifying details, one line each. */
  details?: string[];
  licence?: Link | string;
  credit?: string;
  usedFor: string;
  freshness: Freshness;
};

export type SourceSection = {
  id: string;
  title: string;
  /** One paragraph that applies to every row, such as a shared licence. */
  intro?: string;
  introLink?: Link;
  rows: SourceRow[];
};

const CITY_TERMS: Link = {
  text: "City of Edmonton Open Data Terms of Use",
  href: "https://data.edmonton.ca/stories/s/City-of-Edmonton-Open-Data-Terms-of-Use/msh8-if28/"
};

const city = (id: string) => `https://data.edmonton.ca/d/${id}`;

export const SOURCE_SECTIONS: ReadonlyArray<SourceSection> = [
  {
    id: "city",
    title: "City of Edmonton open data",
    intro:
      "Published by the City of Edmonton and read through the City's open data API. Each item is credited as \"City of Edmonton Open Data\", under the",
    introLink: CITY_TERMS,
    rows: [
      {
        name: "LRT elevator and escalator outages",
        href: city("snws-u3zx"),
        details: ["Dataset snws-u3zx"],
        usedFor: "Elevators and escalators that are out of service, shown at their station.",
        freshness: { kind: "status", key: "city_lrt_devices" }
      },
      {
        name: "LRT device list",
        href: city("javh-pjj2"),
        details: ["Dataset javh-pjj2"],
        usedFor: "Which station each elevator and escalator belongs to. Live status always comes from the outage feed, which is fresher.",
        freshness: { kind: "fixed", text: "Read once a day with the outage feed." }
      },
      {
        name: "LRT stations and stops",
        href: city("fhxi-cnhe"),
        details: ["Dataset fhxi-cnhe"],
        usedFor: "Station locations, and station names in place search.",
        freshness: { kind: "fixed", text: "Read once a day with the outage feed. Built into search on November 8, 2026." }
      },
      {
        name: "On-street construction and maintenance",
        href: city("7wiq-4rgy"),
        details: ["Dataset 7wiq-4rgy"],
        usedFor: "Sidewalk closures from permits that affect pedestrians.",
        freshness: { kind: "status", key: "city_construction" }
      },
      {
        name: "River valley trail cautions and closures",
        href: city("k4mi-dkvi"),
        details: ["Dataset k4mi-dkvi"],
        usedFor: "Closed trails, and trails to take care on.",
        freshness: { kind: "status", key: "city_trail_closures" }
      },
      {
        name: "311 snow on walk complaints",
        href: city("9i6v-cqzn"),
        details: ["Dataset 9i6v-cqzn"],
        usedFor: "Neighbourhood snow advisories. They are located only to the neighbourhood, so they never change a route.",
        freshness: { kind: "status", key: "city_311_snow" }
      },
      {
        name: "Traffic disruptions",
        href: city("k4tx-5k8p"),
        details: ["Dataset k4tx-5k8p"],
        usedFor: "Road closures, listed as take care so you can expect a detour at the crossing. They never change a route.",
        freshness: { kind: "status", key: "city_traffic_disruptions" }
      },
      {
        name: "Snow and ice clearing route status",
        href: city("8pdx-hfxi"),
        details: ["Dataset 8pdx-hfxi"],
        usedFor: "Where plows have passed during a declared snow event, so you can check curb ramps.",
        freshness: { kind: "status", key: "city_snow_clearing" }
      },
      {
        name: "Parcel addresses",
        href: city("ut27-nrpn"),
        details: ["Dataset ut27-nrpn"],
        usedFor: "Address search. Only address points are kept, never owner names.",
        freshness: { kind: "fixed", text: "Built into search on November 8, 2026. Rebuilt weekly." }
      },
      {
        name: "ETS GTFS schedule",
        details: ["Published by Edmonton Transit Service at gtfs.edmonton.ca"],
        credit: "Schedule: Edmonton Transit Service, City of Edmonton Open Data",
        usedFor:
          "Timetables for walk and LRT trips and, where they are switched on, walk and bus trips: one ETS bus ride, no transfers. Waits are estimates from the schedule, never live arrivals.",
        freshness: {
          kind: "fixed",
          text: "Built on November 8, 2026 from the schedule for October 23 to December 12, 2026."
        }
      },
      {
        name: "Daily pedestrian counts",
        href: city("sw7k-ptx8"),
        details: ["Dataset sw7k-ptx8"],
        usedFor: "Planned: ranking which barriers to clear first on the city dashboard.",
        freshness: { kind: "fixed", text: "Not read yet." }
      }
    ]
  },
  {
    id: "weather",
    title: "Weather",
    rows: [
      {
        name: "Environment and Climate Change Canada",
        href: "https://eccc-msc.github.io/open-data/msc-data/obs_station/readme_obs_insitu_en/",
        details: ["Real-time observations, Edmonton Blatchford station (MSC ID 3012209)"],
        licence: { text: "Open Government Licence – Canada", href: "https://open.canada.ca/en/open-government-licence-canada" },
        credit: "Environment and Climate Change Canada",
        usedFor: "Air temperature and dew point for the ice-risk watch.",
        freshness: { kind: "status", key: "weather_watch" }
      },
      {
        name: "Open-Meteo forecast",
        href: "https://open-meteo.com/en/docs",
        details: ["Hourly values for latitude 53.57, longitude -113.52"],
        licence: { text: "CC BY 4.0", href: "https://open-meteo.com/en/licence" },
        credit: "Weather data by Open-Meteo.com",
        usedFor:
          "Ground temperature, recent rain or snow, and the last 12 hours of air temperature for the ice-risk watch. Ground temperature is modelled, not measured on the sidewalk.",
        freshness: { kind: "status", key: "weather_watch" }
      },
      {
        name: "Open-Meteo historical weather",
        href: "https://open-meteo.com/en/docs/historical-weather-api",
        details: ["ERA5 reanalysis"],
        licence: { text: "CC BY 4.0", href: "https://open-meteo.com/en/licence" },
        credit: "Weather data by Open-Meteo.com. Contains modified Copernicus Climate Change Service information.",
        usedFor: "Replay mode only: a recorded freeze-thaw night, November 5 to 6, 2025, for demonstrations.",
        freshness: { kind: "fixed", text: "Recorded on November 8, 2026." }
      }
    ]
  },
  {
    id: "cameras",
    title: "Road cameras",
    rows: [
      {
        name: "511 Alberta cameras",
        href: "https://511.alberta.ca/developers/doc",
        details: ["Government of Alberta road cameras, through the 511 Alberta developer API"],
        licence: {
          text: "Free to reproduce for non-commercial or educational use, provided as is",
          href: "https://511.alberta.ca/about/about"
        },
        credit: "Road camera data: 511 Alberta, Government of Alberta",
        usedFor:
          "Road condition readings from cameras in and near Edmonton. Frames are read by OpenPath's model in memory and never stored.",
        freshness: { kind: "cameras" }
      },
      {
        name: "City of Edmonton traffic cameras",
        href: "https://edmontontrafficcam.com/",
        details: ["The City's own camera viewer"],
        usedFor:
          "Not used. OpenPath asked the City for written permission on November 7, 2026. Until the City gives it, we only link to its viewer.",
        freshness: { kind: "fixed", text: "Not used." }
      }
    ]
  },
  {
    id: "maps",
    title: "Maps and routing",
    rows: [
      {
        name: "OpenStreetMap",
        href: "https://www.openstreetmap.org/copyright",
        details: ["Edmonton walking network"],
        licence: { text: "Open Database License (ODbL)", href: "https://www.openstreetmap.org/copyright" },
        credit: "© OpenStreetMap contributors",
        usedFor: "The network routes are planned on: sidewalks, footpaths, trails, crossings, stairs and elevators.",
        freshness: { kind: "fixed", text: "Built into OpenPath on November 7, 2026. Rebuilt weekly." }
      },
      {
        name: "OpenStreetMap points of interest",
        href: "https://www.openstreetmap.org/copyright",
        details: ["Named shops, restaurants, cafes, clinics, pharmacies, libraries, parks and other places in Edmonton"],
        licence: { text: "Open Database License (ODbL)", href: "https://www.openstreetmap.org/copyright" },
        credit: "© OpenStreetMap contributors",
        usedFor:
          "Place search, so you can type a shop or place name instead of its address. Each result says what the place is and its address.",
        freshness: { kind: "fixed", text: "Built into search on November 8, 2026. Rebuilt weekly." }
      },
      {
        name: "OpenFreeMap",
        href: "https://openfreemap.org",
        details: ["Map tiles in the OpenMapTiles schema"],
        credit: "OpenFreeMap, © OpenMapTiles, data from OpenStreetMap",
        usedFor: "The background map. Your browser loads the tiles straight from OpenFreeMap.",
        freshness: { kind: "fixed", text: "Loaded by your browser whenever a map is shown." }
      },
      {
        name: "Named places",
        details: ["54 well-known destinations, chosen by the OpenPath team"],
        credit: "Locations from City of Edmonton Open Data",
        usedFor: "Place search, each placed at its own City address or at the LRT station beside it.",
        freshness: { kind: "fixed", text: "Updated November 8, 2026." }
      }
    ]
  },
  {
    id: "reports",
    title: "Community reports",
    rows: [
      {
        name: "Reports from people using OpenPath",
        details: ["Snow, ice or a blocked path at one point"],
        credit: "Community report",
        usedFor:
          "Barriers people report. A photo is checked by OpenPath's road-condition model and by Claude through the Anthropic API, then dropped; it is never stored. A report has low confidence unless the photo check agrees with it.",
        freshness: { kind: "fixed", text: "Ice reports last 2 hours, blocked paths 6 hours and snow 12 hours." }
      }
    ]
  }
];

/** The status keys this page already shows, so new ones can be listed as well. */
export const KNOWN_STATUS_KEYS: ReadonlySet<string> = new Set(
  SOURCE_SECTIONS.flatMap((section) =>
    section.rows.flatMap((row) => (row.freshness.kind === "status" ? [row.freshness.key] : []))
  )
);
