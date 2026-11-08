# Recorded Parcel Addresses (`ut27-nrpn`)

Unmodified JSON responses from the official City of Edmonton open data API,
recorded on November 8, 2026. `manifest.json` records each query, the capture
time, the row count and the SHA-256 hash. Attribution: City of Edmonton Open Data.

| File | What it holds |
| --- | --- |
| `parcels.json` | Every `PARCEL` address in a downtown box (latitude 53.539 to 53.548, longitude -113.500 to -113.485): 310 rows |
| `suites.json` | The distinct `SUITE` address points in the same box: 515 rows. Some buildings (Edmonton City Centre, Stantec Tower) exist only as suites |

The box covers Rogers Place, Sir Winston Churchill Square, City Hall, Edmonton
City Centre, Jasper Avenue and Central, Churchill, Bay Enterprise Square and
MacEwan stations, so the search tests and `scripts/build_search_index.py
--fixtures` can build a small index with no network. Tests never change these
files; they build modified copies when they need malformed rows.

The full citywide index (`cloud/data/search.npz`) is built from the live API by
`scripts/build_search_index.py`; see docs/DATA_SOURCES.md.
