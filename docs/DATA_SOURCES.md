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

**What it is.** Government of Alberta road cameras across the province, published through the 511 Alberta developer API. The app uses cameras inside Edmonton to read road conditions (OP-202, OP-204). OP-201 additionally uses provincial cameras for training examples; that does not widen routing coverage.

**How we access it.** `GET https://511.alberta.ca/api/v2/get/cameras?key=…&format=json` with our free developer key (register, log in, request a key on the [API documentation page](https://511.alberta.ca/developers/doc)). The key is `ALBERTA_511_KEY` and is never committed. It travels in the query string, so `vision/cameras.py` redacts `key=` from the httpx and httpcore loggers and keeps them at WARNING. Each camera has `Id`, `Source`, `SourceId`, `Roadway`, `Direction`, `Latitude`, `Longitude`, `Location`, `SortOrder` and `Views` (`Id`, `Url`, `Status`, `Description`) ([GET Cameras](https://511.alberta.ca/help/endpoint/cameras)).

**Licence and attribution.** 511 Alberta information may be reproduced for non-commercial or educational purposes without charge or further permission; commercial reproduction needs written permission from the Government of Alberta, and information is provided as is ([About 511 Alberta](https://511.alberta.ca/about/about)). Shown as "Road camera data: 511 Alberta, Government of Alberta" on `/sources` and beside each camera reading.

**Camera images (decided November 8, 2026; [issue #132](https://github.com/tatsatshah05/NatHacks2026/issues/132)).** Tatsat, the lead, decided that keeping 511 camera frames for training is allowed under the general alberta.ca non-commercial terms. No 511 page addresses training images, so this is the team's decision, not permission from 511 Alberta. It comes with these rules:

- Frames stay private on Tatsat's laptop. They are never committed to git and never put anywhere public: no public links, release assets or public repositories. Any other copy, such as the OP-201 pilot files, stays team-only and access-restricted under the same rules.
- Training frames are credited to the Government of Alberta / 511 Alberta, in `vision/MODEL_CARD.md` and on `/sources`.
- Any frame with an identifiable person or a readable plate is deleted, not labelled.
- Everything is deleted after the event, including `op201_handoff.zip`, `op201_blind_review.zip` and any other copies.
- Live app and worker frames are still processed in memory and discarded. The decision does not allow re-hosting camera images in the app.
- **Third-party processing.** With the agree check on (`--ai-check`, decided November 8), the worker also sends each frame the model would post to the Anthropic API, so Claude can check the label. We send it in memory and store nothing; Anthropic processes it under its API terms. The training labels were also made with Claude's help, as the model card says. The `/sources` and privacy pages must say both when they are built.

**Update cadence and limits.** The camera list is meant to refresh daily; the scheduling arrives with the worker (OP-204). 511 Alberta allows 10 calls every 60 seconds; one shared `RateLimiter` enforces that for every request, including the image requests OP-204 will add.

**Reliability.** 20-second timeout. A failed fetch or a changed schema keeps the last good list and marks it stale, with one log line when it goes stale and one when it recovers. A single malformed camera is skipped with a log line rather than failing the whole list. The last good list is cached in `vision/.cache/` (ignored by git); if that disk is read-only, fetching carries on without the cache.

**Measured November 7, 2026, 21:11:11 MST (November 8, 04:11:11 UTC).** Estella's successful `python -m vision.cameras` terminal output returned 392 total camera records, 19 within the approximate Edmonton bounding box and 47 enabled views on those records. Multiple views are not additional physical cameras. Without a City boundary passed with `--boundary`, this is approximate and can include neighbouring municipalities. Cached metadata stays in ignored `vision/.cache/`. A fresh metadata response may be recorded with `--record` when needed, without committing image frames or keys.

**Recorded fixture (OP-204).** Official metadata recorded on November 8 at 04:19:13 UTC contains 392 cameras, with 19 inside the approximate Edmonton bbox and 47 enabled views there. The count includes neighbouring municipalities until an exact boundary is supplied. `vision/tests/fixtures/alberta511_cameras_recorded.json` and its manifest contain public metadata/provenance, with no query credentials or images. A one-request check of camera 56/view 117 returned a valid 704x518 JPEG; it was processed in memory and discarded. This verifies one direct image response, not all view availability or trained-model accuracy. The hand-written sample remains for small tests.

## City of Edmonton Traffic Cameras (OP-112, Ipsa)

**Status (open).** Not used. On November 7 we sent the City a written request to use its traffic camera streams; the reply is tracked in issue #40. Until the City gives written permission, the app only links to the City's own viewer ([edmontontrafficcam.com](https://edmontontrafficcam.com/)).

## RSCD Road Surface Dataset (OP-112, Ipsa)

**What it is.** The Road Surface Classification Dataset: 240 × 360 pixel road-surface patches cropped from a vehicle-mounted camera in China, labelled in 27 categories of friction, material and unevenness. The IEEE DataPort page describes about 1 million images; the Mendeley Data release (version 3) lists 370,151.

**How we use it.** Mixed into the training split only (never validation or test), capped at `--rscd-per-class` images per class (default 1,000); there is no separate pre-training stage (OP-202). Its close-up, forward-facing view differs from our roadside cameras, so it does not replace labelled 511 images. `vision/train.py` maps the friction state in each RSCD folder name: `dry` → `dry`, `wet` and `water` → `wet`, `fresh_snow` → `snow_covered`, `melted_snow` → `snow_partial`, `ice` → `ice`; RSCD has no `unusable` class. **Open:** this mapping is provisional; confirm it against the real folder names after downloading, and check the run's `metrics.json` for unmapped folders. IEEE DataPort needs a subscription, so download from Mendeley Data.

**Licence and attribution.** CC BY 4.0 ([IEEE DataPort](https://ieee-dataport.org/documents/rscd-road-surface-classification-dataset-detailed-annotations-driving-assistance), [Mendeley Data](https://data.mendeley.com/datasets/w86hvkrzc5)). Credit: Tong Zhao, "RSCD: Road Surface Classification Dataset with Detailed Annotations for Driving Assistance", IEEE DataPort, 2022, doi:10.21227/446p-xr65, in `vision/MODEL_CARD.md` and on `/sources`.

**City-owned cameras in the 511 feed (November 8, 2026).** Some 511 Alberta cameras belong to cities and carry a City's watermark (for example the City of Calgary). The team's rules require a City's written permission for City cameras, so their frames are not kept for training. The camera list's `Source` field does not show ownership (City of Calgary cameras also appear as `Alberta511`), so the Calgary area is excluded by location as well, and frames are reviewed for City watermarks before labelling.

**Decision D-3 (decided by Tatsat on November 8, 2026; [issue #132](https://github.com/tatsatshah05/NatHacks2026/issues/132)).** No RSCD for the November 8 models: they train only on real 511 camera frames. Any class with no real 511 examples (likely `ice`) is reported as not covered in `vision/MODEL_CARD.md`, never filled in from another dataset. Keeping 511 frames follows the camera images decision above. Never keep an image in which a person can be identified, and never commit images.

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
- Historical archive (OP-305, replay only): https://archive-api.open-meteo.com/v1/archive
  (ERA5 reanalysis), hourly `temperature_2m`, `dew_point_2m`, `precipitation` and
  `soil_temperature_0_to_7cm` in UTC. Recorded by `scripts/record_snapshot.py
  --weather-archive`; the committed freeze-thaw night is in
  `cloud/tests/fixtures/weather/freeze_thaw/` (see [OP-305](OP-305.md)). Its soil
  value is a 0 to 7 cm layer average, not the 0 cm surface value. Attribution adds
  "Contains modified Copernicus Climate Change Service information".

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

## Parcel Addresses, LRT Stations and Named Places: Search (OP-302, Matthew)

**What it is.** The index behind `GET /api/search?q=`, which turns typed text into a point to route from or to. It has three parts:

- **Addresses:** the City's Parcel Addresses (dataset `ut27-nrpn`, updated weekly).
- **LRT stations and stops:** the City's LRT Stations and Stops (dataset `fhxi-cnhe`).
- **Named places:** about fifty well-known destinations from `config/places.json`.

**How we access it.** `scripts/build_search_index.py` reads both datasets once, on a laptop, from the City open data API (`https://data.edmonton.ca/resource/<id>.json`, paged with `$limit`/`$offset`, sending `SOCRATA_APP_TOKEN` as `X-App-Token` when it is set). It writes `cloud/data/search.npz`, which is committed. The API loads that file at startup (`cloud/app/search.py`). No external geocoder is ever called, and nothing is downloaded at request time. `python scripts/build_search_index.py --fixtures` builds a small index offline from the recorded sample.

**Licence and attribution.** City of Edmonton Open Data Terms of Use (the dataset metadata lists "See Terms of Use" and attribution "City of Edmonton"). Shown as "City of Edmonton Open Data" on `/sources` and with search results. The index stores public address points only: no owner names or other personal information.

**Update cadence.** Rebuild weekly, or when a new address is missing. Run `python scripts/build_search_index.py --write-places` (20 seconds on November 8, almost all of it downloading), check the printed counts, and commit `cloud/data/search.npz` and `config/places.json`.

**What the index holds.**

- **Addresses:** one entry per (house number, street). The point is the mean of the address's parcel points. An address that exists only as suites (58,064 of them, including Edmonton City Centre and Stantec Tower) uses the mean of its suite points instead.
- **Stations:** one entry per station or stop name, at the mean of its platform points.
- **Places:** each place in `config/places.json` with its street address. Its coordinate source is recorded:
  - `parcel_address`: the parcel point of the place's own address (48 places).
  - `lrt_station`: the address is not in the dataset, so the point is the station beside the place (6 places). The entry's note says how far away the station is (about 100 to 370 m).
- **Station aliases:** `config/places.json` also adds names people use, such as "Government Centre" for Government Station.

**Measured on November 8, 2026** (live build at 15:51 UTC):

| Measurement | Value |
| --- | --- |
| Rows read | 298,785 parcel rows and 264,967 distinct suite points (all 778,288 rows of the dataset) |
| Addresses indexed / streets | 346,922 / 2,913 |
| Stations / named places | 29 / 54 |
| Coverage | The whole city: bbox -113.7134, 53.3384 to -113.2784, 53.7152, so no central-area limit is needed |
| File size | 1.7 MB |
| Load time / memory added | about 20 ms / about 10 MB |
| Search time, 200 realistic queries in process (`python -m cloud.app.search --bench`) | p50 0.12 ms, p95 0.70 ms, max 0.75 ms |
| Search time, the same 200 queries over HTTP to a local `uvicorn` | p50 0.69 ms, p95 1.87 ms, max 18.85 ms |

The target is under 200 ms for the top 8 results. These measurements are from one laptop (Apple M4, macOS). Render's CPU is not measured yet; it will be slower, but these times leave a wide margin.

**Recorded sample.** `cloud/tests/fixtures/parcel_addresses/` holds unmodified responses for a downtown box (latitude 53.539 to 53.548, longitude -113.500 to -113.485): 310 parcel rows and 515 suite points, recorded on November 8, 2026. `manifest.json` lists the queries and SHA-256 hashes. Tests and the `--fixtures` build use it with the recorded LRT stations; they never call the network.

**Known limits.**
- Unit numbers (`sub_address`, such as suite 71 or REAR) are merged into the base address, so units are not searchable.
- A street with no house number ("jasper ave") finds no addresses, only places and stations whose names or addresses contain those words.
- House numbers with a letter suffix ("1A Sir Winston Churchill Square") are not in the dataset's numeric `house_number`, so places at such addresses are left out (the Law Courts).
- Some well-known sites have no matching parcel address: the Valley Zoo, Louise McKinney Park, Victoria Park and the Old Strathcona Farmers' Market. They are left out of `config/places.json` rather than given a guessed point. Whyte Avenue is a street, so its place uses one Whyte Avenue parcel near 104 Street.

## ETS GTFS Schedule (OP-311)

**What it is.** The Edmonton Transit Service timetable in GTFS form: routes, stops, trips, stop times, calendars, station pathways and transfers. OpenPath uses only the light-rail routes (`route_type` 0: Capital Line `021R`, Metro Line `022R`, Valley Line `023R`, all `agency_id` 1, Edmonton Transit Service) for walk and LRT trips. The feed also carries the regional agencies (St. Albert, Strathcona County and others); they are ignored.

**How we access it.** One official download of `https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/GTFS.zip`, the City's GTFS server (the producer URL the feed lists), by `scripts/build_lrt_graph.py` (`make lrt`) on a laptop. The script reads the zip in a temporary folder, writes the compact `cloud/data/lrt.json` and deletes the zip. The zip is never committed, and the API never downloads or reads GTFS: it loads `lrt.json` at startup.

**Licence and attribution.** The feed's own `attributions.txt` names Edmonton Transit Service as producer and links the [City of Edmonton Open Data Terms of Use](https://data.edmonton.ca/stories/s/City-of-Edmonton-Open-Data-Terms-of-Use/msh8-if28/). Shown as "Schedule: Edmonton Transit Service, City of Edmonton Open Data" on every walk and LRT trip (`lrt.attribution`) and, once the web app lists it, on `/sources`. The terms were checked only through the feed's `attributions.txt`; confirm the wording on that page before launch.

**Update cadence.** The City republishes the feed several times a season; PLAN.md section 6 plans a weekly rebuild. Each walk and LRT trip warns when the date has passed the schedule's `feed_end_date`.

**Measured on November 8, 2026** (`python scripts/build_lrt_graph.py --gtfs <zip>`):

| Measurement | Value |
| --- | --- |
| Feed version / service dates | `261023_1059` / October 23 to December 12, 2026 |
| Zip size | 17.7 MB (`stop_times.txt` alone is 166 MB unzipped) |
| Light-rail trips with stop times | 7,350 (0 irregular) |
| Passenger stations | 30: Capital 15, Metro 10, Valley 12 (shared stations counted once) |
| Scheduled end-to-end ride | Capital 36 min, Metro 19 min, Valley 32 min |
| Weekday headway at 08:00 | Capital 6 min, Metro 12 min, Valley 5 min |
| City elevators tied to a station / at-grade stations | 32 of 32 / 16 |
| `cloud/data/lrt.json` | 20.3 kB |
| Build time from a downloaded zip | about 5 s |

**What `lrt.json` holds.** Stations (GTFS parent station id, City name, location, access type, the City elevators and escalators by device id with a `platform` or `entrance` role, and any reviewed OP-106 entry nodes), lines (station order, median scheduled ride time between consecutive stations, headsigns, median headway per hour for a weekday, Saturday and Sunday) and transfer pairs between nearby stations of different lines. `docs/OP-311.md` explains each rule.

**Known limits.**
- Three platforms where no trip lets anyone board or alight (Heath Sciences Tail Track, DL Macdonald Platform and Kathleen Andrews Platform) are not passenger stations; ride times span them.
- Holidays are not modelled: the busiest date of each day type stands for it.
- A station with no City elevator and no GTFS elevator pathway is treated as reachable without stairs (`at_grade`). That is an inference from the two datasets, not a survey.
- Live LRT status is the City outage feed (`snws-u3zx`, OP-105), never the GTFS feed. Waits are schedule estimates, never live arrivals.

## Community Reports (OP-308)

**What it is.** Reports of snow, ice or a blocked path that people send from the web app's Report button (`POST /api/reports`), each with an optional photo. A report becomes a `community_report` obstruction and expires per `config/reports.json`. Shown as "Community report".

**The photo check (decided by Tatsat on November 8, 2026).** The ONNX road-condition model and Claude check each photo together, with the agree rule the camera worker uses. A report gets `medium` confidence only when both agree with the reported type; anything else is `low`, and the reported type is kept. The six road-condition classes cannot show a blocked path, so for a `blocked` report Claude is asked "Is the walking path in this photo blocked?" and the model only has to be able to read the photo: **confirmation of a blocked path rests on Claude alone.** The model was trained and tested on 511 road-camera frames, not on phone photos of sidewalks, so its accuracy on report photos is not measured.

**Privacy.**
- A report photo is held in memory for one request and never stored by OpenPath: not on disk, not in the database, not in a log. Only its size and the class a check saw are logged.
- **Third-party processing.** For the Claude check, the API sends the photo to the Anthropic API (Claude, model `claude-haiku-5-5` by default), in memory, over HTTPS. Anthropic processes it under its API terms; OpenPath keeps no copy. Claude is not sent the reported type, the note, the location or the device's anonymous id. The model check runs on our own server.
- Before sending, the web app re-encodes the photo as a JPEG at most 1600 px on its longest edge, which drops its metadata (such as GPS position). If the browser cannot decode the photo, it sends the original JPEG or PNG file instead, metadata included; Claude receives the photo as sent.
- The `/sources` and privacy pages must say that report photos are checked by Claude through the Anthropic API when they are built, as for camera frames.
