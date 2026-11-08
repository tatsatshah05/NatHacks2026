# Data Sources

One section per source. Each adapter's owner keeps their section current: what the source is, how we access it, its licence and attribution, its update cadence, and anything measured about it. PLAN.md section 6 has the summary table.

## OpenStreetMap Walking Network (OP-104, Tatsat)

**What it is.** Every walkable path in Edmonton from OpenStreetMap: sidewalks, footpaths, trails, crossings, stairs and elevators. This is the network that routes are computed on.

**How we access it.** Downloaded once on a laptop with [OSMnx](https://osmnx.readthedocs.io/) (`scripts/build_graph.py`, `network_type="walk"`), converted to compact NumPy arrays, and committed as `cloud/data/network.npz`. The API loads that file at startup (`cloud/app/network.py`). OSMnx is never installed on Render, and nothing is fetched at request time.

**Licence and attribution.** OpenStreetMap data is © OpenStreetMap contributors, under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright). The web app shows "© OpenStreetMap contributors" on the map and on `/sources`.

**Update cadence.** Rebuilt weekly, or when the team needs a fix from OpenStreetMap. Run `python scripts/build_graph.py` (about two minutes with a warm cache) and commit the new file.

**What the file holds.** Nodes (OSM id, latitude, longitude, flags), undirected edges (end nodes, length in metres, flags, highway type, street name, OSM way id) and each edge's polyline for drawing. Flags mark stairs, elevators, crossings and sidewalks, so routing can remove stairs for wheelchair and walker profiles, OP-106 can match LRT elevators, and OP-205 can place ice risk on crossings. The exact layout is documented at the top of `cloud/app/network.py`.

**Measured on November 7, 2026** (`scripts/build_graph.py`, Edmonton, Alberta, Canada):

| Measurement | Value |
| --- | --- |
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

## 511 Alberta Cameras (OP-112, Ipsa)

**What it is.** Government of Alberta road cameras across the province, published through the 511 Alberta developer API. OpenPath uses the cameras inside Edmonton to read road conditions (OP-202, OP-204). Only camera metadata is fetched for now: location, roadway and views.

**How we access it.** `GET https://511.alberta.ca/api/v2/get/cameras?key=…&format=json` with our free developer key (register, log in, request a key on the [API documentation page](https://511.alberta.ca/developers/doc)). The key is `ALBERTA_511_KEY` and is never committed. It travels in the query string, so `vision/cameras.py` redacts `key=` from the httpx and httpcore loggers and keeps them at WARNING. Each camera has `Id`, `Source`, `SourceId`, `Roadway`, `Direction`, `Latitude`, `Longitude`, `Location`, `SortOrder` and `Views` (`Id`, `Url`, `Status`, `Description`) ([GET Cameras](https://511.alberta.ca/help/endpoint/cameras)).

**Licence and attribution.** 511 Alberta information may be reproduced for non-commercial or educational purposes without charge or further permission; commercial reproduction needs written permission from the Government of Alberta, and information is provided as is ([About 511 Alberta](https://511.alberta.ca/about/about)). Shown as "Road camera data: 511 Alberta, Government of Alberta" on `/sources` and beside each camera reading.

**Camera images (open).** The terms do not mention camera images specifically. On November 7 we asked 511 Alberta to confirm in writing that keeping labelled frames for non-commercial training is allowed. Until then, live frames are processed in memory only, and the app links to the official 511 viewer instead of re-hosting images.

**Update cadence and limits.** The camera list is meant to refresh daily; the scheduling arrives with the worker (OP-204). 511 Alberta allows 10 calls every 60 seconds; one shared `RateLimiter` enforces that for every request, including the image requests OP-204 will add.

**Reliability.** 20-second timeout. A failed fetch or a changed schema keeps the last good list and marks it stale, with one log line when it goes stale and one when it recovers. A single malformed camera is skipped with a log line rather than failing the whole list. The last good list is cached in `vision/.cache/` (ignored by git); if that disk is read-only, fetching carries on without the cache.

**Measured.** Not yet: the Edmonton camera count is recorded here once the key arrives (`python -m vision.cameras --record vision/tests/fixtures/alberta511_cameras_recorded.json`). Until a City boundary file is passed with `--boundary`, the count uses a bounding box, over-counts neighbouring municipalities, and is labelled approximate. The test fixture is hand-written from the documented fields, not a recorded response.

## City of Edmonton Traffic Cameras (OP-112, Ipsa)

**Status (open).** Not used. On November 7 we sent the City a written request to use its traffic camera streams; the reply is tracked in issue #40. Until the City gives written permission, the app only links to the City's own viewer ([edmontontrafficcam.com](https://edmontontrafficcam.com/)).

## RSCD Road Surface Dataset (OP-112, Ipsa)

**What it is.** The Road Surface Classification Dataset: 240 × 360 pixel road-surface patches cropped from a vehicle-mounted camera in China, labelled in 27 categories of friction, material and unevenness. The IEEE DataPort page describes about 1 million images; the Mendeley Data release (version 3) lists 370,151.

**How we use it.** Pre-training only (OP-202). Its close-up, forward-facing view differs from our roadside cameras, so it does not replace labelled 511 images. **Open:** confirm which categories map to `snow_partial`, `snow_covered` and `ice` before training. IEEE DataPort needs a subscription, so download from Mendeley Data.

**Licence and attribution.** CC BY 4.0 ([IEEE DataPort](https://ieee-dataport.org/documents/rscd-road-surface-classification-dataset-detailed-annotations-driving-assistance), [Mendeley Data](https://data.mendeley.com/datasets/w86hvkrzc5)). Credit: Tong Zhao, "RSCD: Road Surface Classification Dataset with Detailed Annotations for Driving Assistance", IEEE DataPort, 2022, doi:10.21227/446p-xr65, in `vision/MODEL_CARD.md` and on `/sources`.

**Decision D-3 (proposed; the lead records the final decision in PLAN.md section 16).** Pre-train on RSCD with attribution. Keep labelled 511 frames privately for non-commercial training, outside the repo and never redistributed, once 511 Alberta confirms; if it says no, delete them and train on RSCD and community photos only. Never keep an image in which a person can be identified, and never commit images.

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

## Traffic Disruptions (OP-108, Tatsat)

**What it is.** The City's list of current road disruptions: construction, incidents and special events, each with a point, the street and cross streets, an impact and start/finish dates. Dataset `k4tx-5k8p`, updated several times a day.

**How we access it.** The City open data API (`https://data.edmonton.ca/resource/k4tx-5k8p.json`), filtered to statuses `Current`, `Revised` and `Emergency`, read in pages every 15 minutes by `cloud/app/adapters/traffic_disruptions.py`. The `SOCRATA_APP_TOKEN` raises the rate limit when set.

**What becomes an obstruction.** Only closures (`Total Closure`, `Directional Closure`, `Permanent Closure`, `Traffic Incident Closure`) whose dates cover now. They are `caution` items in category `road`: they never reroute anyone, they appear in the route report so people expect a detour at the crossing. Delays are skipped because they do not affect pedestrians. A row without a point or dates is skipped and logged; it never fails the refresh.

**Licence and attribution.** City of Edmonton Open Data Terms of Use; shown as "City of Edmonton Open Data".

**Recorded sample.** `cloud/tests/fixtures/traffic_disruptions/current.json` holds 30 current rows recorded on November 8, 2026 (see `manifest.json`), used by the tests and by replay mode.
