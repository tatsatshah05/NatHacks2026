# Recorded construction permits

Unmodified JSON samples from the City of Edmonton's official `7wiq-4rgy`
Socrata endpoint, recorded November 7, 2026. Attribution: City of Edmonton
Open Data. The manifest includes the exact selection URLs, capture times and
row counts. These are historical test samples, not a complete live snapshot.

- `pedestrian_lines.json`: pedestrian impacts with line geometry; includes
  identical spatial duplicates and different streets under one permit.
- `pedestrian_points.json`: pedestrian impacts with only point locations.
- `other_permits.json` and `repeated_permit.json`: records whose pedestrian
  impact is `Unknown`, including repeated road segments under one permit.
- `unlocated_permits.json`: actual pedestrian permits with no map geometry.

Tests mutate copies to exercise expired/future permits, finish-day boundaries,
missing fields and failures. Tests never call the live network. Optional
`sha256_lf` values hash the response bytes after normalizing CRLF to LF, so
verification also works after a Windows Git checkout.
