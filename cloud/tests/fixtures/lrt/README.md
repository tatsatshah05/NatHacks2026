# Recorded City LRT feeds

These are unmodified JSON responses downloaded from the official City of
Edmonton Socrata API on November 7, 2026. `manifest.json` records the URLs, file
capture times, row counts and SHA-256 hashes. This data is a historical test
snapshot, not a source of live status. Attribution: City of Edmonton Open Data.

| File | Dataset | Purpose |
| --- | --- | --- |
| `outages.json` | `snws-u3zx` | Live-status authority at capture time |
| `devices.json` | `javh-pjj2` | Device metadata; status can lag the outage feed |
| `stations.json` | `fhxi-cnhe` | Station coordinates |

The captured outage feed contains escalator outages. Tests explicitly mutate
copies of these records to exercise elevator outages, bad schemas and failures;
the recorded files themselves are never changed by tests. The master list's
Clareview elevator 29 is marked out but absent from the outage feed, providing
a recorded regression case for ignoring stale master-list status.

The feed's floating timestamps do not declare a timezone in the metadata.
Tests and previews supply a timezone explicitly; that interpretation must be
confirmed before live integration. Do not refresh observation times or expiry
to make this recorded data appear current.
