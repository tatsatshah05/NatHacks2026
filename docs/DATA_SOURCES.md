# Data Sources

Licences, terms and access rules for every PathSense source. Dataset IDs and polling cadences are in [PLAN.md section 6](../PLAN.md#6-data-sources). Each source owner adds their section here.

## Cameras and training data (OP-112)

Checked on November 7, 2026, by Ipsa. Open questions are marked **Open** and tracked in issue #40.

### 511 Alberta cameras

| Item | Finding |
| --- | --- |
| Access | Official API with a free developer key: register an account, log in, request a key on the API documentation page. Key stored as `ALBERTA_511_KEY`, never committed. |
| Endpoint | `GET https://511.alberta.ca/api/v2/get/cameras?key=…&format=json`. Each camera has `Id`, `Source`, `SourceId`, `Roadway`, `Direction`, `Latitude`, `Longitude`, `Location`, `SortOrder` and `Views` (`Id`, `Url`, `Status`, `Description`). |
| Rate limit | 10 calls every 60 seconds. `vision/cameras.py` enforces it with one shared `RateLimiter`. |
| Terms of use | Information on 511 Alberta may be reproduced for non-commercial or educational purposes without charge or further permission. Commercial reproduction needs written permission from the Government of Alberta. Information is provided as is, without warranty. |
| Camera images | **Open:** the published terms do not mention camera images specifically; the non-commercial reproduction clause covers "information" on the site. We have asked 511 Alberta to confirm in writing that keeping labelled frames for non-commercial model training is allowed. Meanwhile, live frames are processed in memory only, and the app links to the official 511 viewer instead of re-hosting images. |
| Attribution | "Road camera data: 511 Alberta, Government of Alberta" on `/sources` and next to each camera reading. |
| Commercial use | Any use beyond the hackathon needs written permission from the Government of Alberta. |

Sources: [511 Alberta API documentation](https://511.alberta.ca/developers/doc), [GET Cameras](https://511.alberta.ca/help/endpoint/cameras), [About 511 Alberta (terms of use)](https://511.alberta.ca/about/about).

### City of Edmonton traffic cameras

| Item | Finding |
| --- | --- |
| Status | **Open:** written request to the City not yet answered. Date sent and replies are tracked in issue #40. |
| Until permission | Not used. The app links to the City's own viewer ([edmontontrafficcam.com](https://edmontontrafficcam.com/)). |

### RSCD (Road Surface Classification Dataset)

| Item | Finding |
| --- | --- |
| Licence | CC BY 4.0 on both IEEE DataPort and Mendeley Data. Training a model on it is allowed with attribution. |
| Attribution | Tong Zhao, "RSCD: Road Surface Classification Dataset with Detailed Annotations for Driving Assistance", IEEE DataPort, 2022, doi:10.21227/446p-xr65. Credit it in `vision/MODEL_CARD.md` and on `/sources`. |
| Size | The IEEE DataPort page describes about 1 million images; the Mendeley Data release (version 3) lists 370,151. Both are 240 x 360 pixel road-surface patches. |
| How images were taken | Cropped from a vehicle-mounted camera looking at the road just ahead, in China. Our cameras are roadside and look down a road from a distance, so RSCD is a pre-training aid, not a substitute for labelled 511 images. |
| Classes | 27 categories of friction, material and unevenness. **Open:** confirm which categories map to our `snow_partial`, `snow_covered` and `ice` before training (OP-202). |
| Access | **Open:** IEEE DataPort needs a subscription; download from the Mendeley Data release or the authors' GitHub instead. |

Sources: [RSCD on IEEE DataPort](https://ieee-dataport.org/documents/rscd-road-surface-classification-dataset-detailed-annotations-driving-assistance), [Road Surface Image Dataset on Mendeley Data](https://data.mendeley.com/datasets/w86hvkrzc5).

### Decision D-3 (training data), proposed

- Use RSCD (CC BY 4.0) for pre-training, with attribution.
- Keep labelled 511 frames for non-commercial training under the 511 non-commercial reproduction terms, stored privately outside the repo and never redistributed, and ask 511 Alberta to confirm in writing. If 511 Alberta says no, delete the stored frames and train on RSCD plus community photos only.
- Never keep an image in which a person can be identified, and never commit images to the repo.

The lead records the final decision in PLAN.md section 16.
One section per source. Each adapter's owner keeps their section current: what the source is, how we access it, its licence and attribution, its update cadence, and anything measured about it. PLAN.md section 6 has the summary table.
## OpenStreetMap Walking Network (OP-104, Tatsat)
**What it is.** Every walkable path in Edmonton from OpenStreetMap: sidewalks, footpaths, trails, crossings, stairs and elevators. This is the network that routes are computed on.
**How we access it.** Downloaded once on a laptop with [OSMnx](https://osmnx.readthedocs.io/) (`scripts/build_graph.py`, `network_type="walk"`), converted to compact NumPy arrays, and committed as `cloud/data/network.npz`. The API loads that file at startup (`cloud/app/network.py`). OSMnx is never installed on Render, and nothing is fetched at request time.
**Licence and attribution.** OpenStreetMap data is © OpenStreetMap contributors, under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright). The web app shows "© OpenStreetMap contributors" on the map and on `/sources`.
**Update cadence.** Rebuilt weekly, or when the team needs a fix from OpenStreetMap. Run `python scripts/build_graph.py` (about two minutes with a warm cache) and commit the new file.
**What the file holds.** Nodes (OSM id, latitude, longitude, flags), undirected edges (end nodes, length in metres, flags, highway type, street name, OSM way id) and each edge's polyline for drawing. Flags mark stairs, elevators, crossings and sidewalks, so routing can remove stairs for wheelchair and walker profiles, OP-106 can match LRT elevators, and OP-205 can place ice risk on crossings. The exact layout is documented at the top of `cloud/app/network.py`.
**Measured on November 7, 2026** (`scripts/build_graph.py`, Edmonton, Alberta, Canada):
| Measurement | Value |
| Nodes | 134,948 |
| Edges (undirected) | 194,643 |
| Stair edges / elevator edges / elevator nodes | 418 / 4 / 9 |
| Crossing edges / sidewalk edges | 22,469 / 42,226 |
| File size | 6.5 MB |
| Load time (fresh process) | 0.055 s |
| Memory: arrays / process peak after load | 20.5 MB / 102.5 MB |
| Build time / build peak memory (laptop) | 60 s / 3.4 GB |
**Memory decision.** PLAN.md section 8 allows under 300 MB for the network on the API instance. Measured: 20.5 MB of arrays and a 102.5 MB process right after loading (most of that is Python, NumPy and SciPy themselves). The whole city fits with room to spare, so routing covers all of Edmonton and no fallback to the central service area is needed.
**Known limits.**
- As a networkx graph the same data needs about 2.8 GB, which is why the arrays exist. Never load the GraphML on Render.
- **OpenStreetMap has few LRT elevators.** All of Edmonton has 9 elevator nodes and 4 elevator ways, while the City's master list has 32 LRT elevators. OP-106 should match each elevator to the nearest station entrance or platform node when no elevator node exists, and the route report should always carry the station-level warning.
- Coordinates in edge polylines are stored as 32-bit floats (about 0.4 m precision at Edmonton's latitude). Snapping uses the 64-bit node coordinates.
- OpenStreetMap coverage of sidewalks varies by neighbourhood. Where sidewalks are not mapped separately, routing follows the street.
## Environment and Climate Change Canada
- Official API: https://api.weather.gc.ca/collections/swob-realtime/items
- Documentation: https://eccc-msc.github.io/open-data/msc-data/obs_station/readme_obs_insitu_en/
- Station: Edmonton Blatchford, MSC ID `3012209`.
- Query: `msc_id-value=3012209`, `sortby=-obs_date_tm`, past two hours,
  `limit=24`, JSON format.
- Fields: `obs_date_tm`, `air_temp`, `dwpt_temp`, their `-uom` metadata and
  `-qa` quality values where available. Temperatures must be Celsius; a present
  quality value other than 100 is rejected.
- Poll every 10 minutes. Allow up to five seconds of provider clock skew; reject
  observations beyond that tolerance or older than two hours. Preserve last-good
  data on failures, rather than clearing it.
- Attribution: Environment and Climate Change Canada; Open Government Licence –
  Canada: https://open.canada.ca/en/open-government-licence-canada
## Open-Meteo
- Official API: https://api.open-meteo.com/v1/forecast
- Documentation: https://open-meteo.com/en/docs
- Default grid request: latitude `53.57`, longitude `-113.52`, timezone UTC.
- Hourly fields: `temperature_2m`, `soil_temperature_0cm`, `precipitation`.
  Request `past_days=1`, `forecast_days=1`, Celsius and millimeters.
- Poll every 10 minutes. Soil temperature is modelled ground temperature, not a
  measured sidewalk temperature. Recent past values are model data, not station
  observations. The watch combines these with Blatchford's observed air/dew point.
- Anchor to the latest station observation's UTC hour. Use six preceding-hour
  precipitation totals and twelve hourly air-temperature samples ending at that
  hour. Ignore all future hours. Reject gaps, nulls, nonfinite values, duplicate
  timestamps, wrong units and negative precipitation.
- Attribution: Weather data by Open-Meteo.com, CC BY 4.0:
  https://open-meteo.com/en/licence
## Reliability and recordings
Both providers are fetched concurrently, with a 10-second timeout per request.
Timeouts and 5xx responses get up to three retries with exponential backoff
(1, 2, 4 seconds). Client errors, malformed JSON and other request errors fail
immediately. Failed concurrent refreshes cancel and await unfinished requests.
Both source responses must validate before publishing a new watch. The endpoint
returns HTTP 200 with the last successful result, or `active: null` before any
successful reading. Freshness remains internal for `/api/status` integration.
The JSON fixtures in `cloud/tests/fixtures/weather/` were retrieved from the
official APIs on November 7, 2026. The ECCC fixture retains the latest actual
Blatchford feature (21:00 UTC); the Open-Meteo fixture retains the complete
returned hourly arrays. ECCC collection links and historical features were
removed to keep the fixture focused. These are recorded responses, not invented
observations. Tests modify copies to exercise failure and boundary cases.
Production replay reads a configured `REPLAY_SNAPSHOT` file rather than the test
directory. The snapshot's `weather` section contains raw `eccc` and `open_meteo`
responses; other source sections can coexist. The frozen replay serves original
observation timestamps with `mode=replay`. A web consumer must display that mode
and use `/api/status` for freshness. Source attribution must also appear on the
shared app's sources page when integrated.
