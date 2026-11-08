# Changed-schema fixtures (OP-407)

Copies of recorded City responses edited to look like a feed whose shape changed:
one row has a required field renamed, and every row has one field added that the
adapter does not know. `cloud/tests/test_quality_schema_change.py` reads them, and
also checks that each copy differs from its recorded original only as listed here.

| File | Recorded original | Renamed (one row) | Added (every row) |
| --- | --- | --- | --- |
| `traffic_disruptions/current.json` | `../traffic_disruptions/current.json` | disruption `1309`: `point` becomes `location` | `feed_version: "2"` |
| `city_advisories/trails.json` | `../city_advisories/trails.json` | notice `1`: `details` becomes `description` | `source_system: "ArcGIS"` |
| `lrt/outages.json` | `../lrt/outages.json` | first row (device `43`): `since_time_stamp` becomes `outage_since` | `data_as_of: "2026-11-07T21:05:17Z"` |

Measured at the frozen test clock `2026-11-10T18:00:00Z` on commit `3967064`'s
fixtures: traffic disruptions give 2 items from the original and 1 from the copy,
trail closures 46 and 45, LRT outages 7 and 6. In each case the renamed row is
rejected and logged, the rest still load, and the two names (`location` and
`feed_version`, and so on) appear in `unexpected_fields` on `GET /api/status`.

To use this folder as a replay snapshot, add `lrt/devices.json` and
`lrt/stations.json` from the recorded fixtures: the LRT adapter needs its lookups.
Public City of Edmonton open data; no personal information. Rebuild with the
script in the pull request description if the recorded originals are re-recorded.
