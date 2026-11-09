# Recorded GTFS sample: three ETS bus routes (OP-417)

Rows copied verbatim from the ETS GTFS feed, version `261023_1059` (service October 23 to December 12, 2026), downloaded from `https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/GTFS.zip` on November 8, 2026 at 23:15 UTC. Only the byte-order mark at the start of each file was dropped; nothing else was edited.

| File | Rows | What was kept |
| --- | --- | --- |
| `feed_info.txt`, `agency.txt` | 1, 7 | whole files |
| `routes.txt` | 3 | routes `654`, `920X` and `511` |
| `trips.txt` | 258 | the trips of those routes |
| `stop_times.txt` | 3,585 | the stop times of those trips |
| `stops.txt` | 99 | the 94 stops those trips call at, plus their 5 parent stations |
| `calendar_dates.txt` | 118 | the service ids of those trips (the feed has no `calendar.txt`) |

Why these three: `920X` (University and Lewis Farms Express) is a weekday peak express with three stop patterns, so headways and a second pattern can be checked by hand; `511` (the Owl night route) has departures past midnight, written as GTFS hours 24 and later; `654` is a school special whose direction 1 is a loop that calls twice at two stops and runs once a day, so it has no headway.

`cloud/tests/test_bus.py` zips this folder in memory and feeds it to `cloud.app.lrt_gtfs.read_bus`; tests never download the feed. To record it again: filter each file's rows on the key column above (route id, trip id, stop id or service id) and write the header plus the kept lines unchanged.
