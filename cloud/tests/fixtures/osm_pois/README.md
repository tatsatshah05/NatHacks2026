# Recorded OpenStreetMap points of interest (Overpass API)

One unmodified JSON response from the public Overpass API
(`https://overpass-api.de/api/interpreter`), recorded on November 8, 2026 for
the same downtown box as the Parcel Addresses fixture (latitude 53.539 to
53.548, longitude -113.500 to -113.485). `manifest.json` records the query, the
capture time, the OpenStreetMap data timestamp, the element count and the
SHA-256 hash.

| File | What it holds |
| --- | --- |
| `elements.json` | The 322 named features in the box with an `amenity`, `shop`, `tourism`, `leisure`, `healthcare` or `office` tag, as nodes with a point or ways and relations with their `center` (`out center`). No geometry. |

It holds restaurants, cafes, banks, hotels, shops, parks, theatres, artwork and
offices, and also what `parse_poi_elements` must drop: named parking lots and
parkade entrances, a clock, an ATM, two `amenity=fixme` features, one
`public_transport=station`, four `Tim Hortons` nodes without address tags, and
OpenStreetMap's own Rogers Place, Edmonton City Hall and Sir Winston Churchill
Square, which duplicate `config/places.json`. The search tests and
`scripts/build_search_index.py --fixtures` use it with the recorded Parcel
Addresses and LRT stations; they never call the network, and they never change
these files.

Licence: Open Database License (ODbL). Attribution: © OpenStreetMap contributors
(https://www.openstreetmap.org/copyright). The full citywide points of interest
in `cloud/data/search.npz` come from the live Overpass API through
`scripts/build_search_index.py`; see docs/DATA_SOURCES.md.
