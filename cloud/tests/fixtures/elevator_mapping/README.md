# OP-106 recorded City elevator inventory

`devices.json` is an unmodified response from the official City of Edmonton
[LRT Elevator/Escalator Master List](https://data.edmonton.ca/d/javh-pjj2),
recorded November 7, 2026. `manifest.json` records its URL and capture metadata.
Attribution: City of Edmonton Open Data.

`stations.json` is the official LRT Stations and Stops (`fhxi-cnhe`) response;
`stations-manifest.json` records its source/capture time. It supplies station
points for the explicitly unverified candidate report, not device coordinates.

The recorded response contains 73 devices, including 32 elevators. Only device
identity, station and location are used to prepare OP-106's review inventory.
Device status in this master list can lag the separate live outage feed and
must not cause route closures.

`osm/` records the four official OSM objects used as proposed device-match
evidence. Its manifest lists the exact API URLs and capture times. Attribution:
© OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright).
No second-person device-match reviews are claimed. Tests distinguish these
inactive candidates from synthetic reviewed test matches and make no network calls.
