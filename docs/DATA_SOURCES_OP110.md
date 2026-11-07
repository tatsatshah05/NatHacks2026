# OP-110 data sources

Supplement for the team's shared source inventory. Keep this as a separate file
until the weather/source documentation PRs are merged to avoid competing edits.

| Source | Official endpoint | Adapter cadence |
| --- | --- | --- |
| 311 Snow on Walk Complaints | https://data.edmonton.ca/resource/9i6v-cqzn.json | Hourly |
| River Valley Trail Cautions and Closures | https://data.edmonton.ca/resource/k4mi-dkvi.json | Every 15 minutes |

Schemas were verified against the official metadata endpoints:
https://data.edmonton.ca/api/views/9i6v-cqzn.json and
https://data.edmonton.ca/api/views/k4mi-dkvi.json.

Snow fields: `row_id`, `date_created`, `request_status`, `neighbourhood_id`,
`neighbourhood`, `nbhd_location`. The geometry is documented as a neighbourhood
centroid. Closed requests, future dates and requests older than 72 hours are
excluded. The upstream query limits status and dates; normalization checks them
again before grouping and counting unique request IDs.

Trail fields: `id_number`, `start_date`, `end_date`, `type_of_closure`, `details`,
`location_name`, `geometry_point`. The actual feed contains Temporary, Permanent
and Caution types. Some end dates are free text. The adapter retains unclear
end dates without inventing a timestamp; see the OP-110 guide for parsing rules.

Both sources use offset pagination with a stable source-ID ordering, 1000 rows
per page by default. A hard pagination limit produces an error, never a partial
successful refresh. Rows can change while pages are being fetched; conflicting
duplicate IDs fail the refresh rather than overcounting. Requests optionally use
`X-App-Token`, with no scraping or private endpoints.

Socrata floating timestamp documentation:
https://dev.socrata.com/docs/datatypes/floating_timestamp.html.
Publisher-local timezone is an assumption for the 311 feed. Trail start dates
use UTC, following `dateFieldsTimeReference.timeZone` in the official ArcGIS layer
linked by the trail dataset attribution:
https://gis.edmonton.ca/arcgishosting/rest/services/Hosted/Trail_Cautions_and_Closures_View/FeatureServer/0?f=json.
Human-readable trail end dates use Edmonton calendar days. Alberta time rules: https://www.alberta.ca/albertas-new-time-system-abt.

Attribution for every item: **City of Edmonton Open Data**. Terms:
https://www.edmonton.ca/sites/default/files/public-files/documents/Web-version2.1-OpenDataAgreement.pdf.
Each result also links to its official dataset page.

## Recordings

Fixtures were retrieved November 7, 2026 from the official APIs. `snow.json`
contains the five latest open complaints plus five latest complaints overall;
it is a sample for tests, not a complete city snapshot. `trails.json` contains
the 60 trail records returned in the complete response. `recording.json` records
the endpoints and replay reference time. Tests mutate copies explicitly to cover
additional boundary and error cases; these mutations are not live observations.

A live smoke check on November 7 returned zero qualifying snow-advisory areas
and 46 current trail items (40 closures, 6 cautions) after filtering definite
expired entries. These are measurements from that refresh, not fixed product
claims or promises of current conditions.
