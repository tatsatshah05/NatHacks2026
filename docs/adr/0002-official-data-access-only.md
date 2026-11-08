# ADR 0002: Official data access only, no scraping

- **Status:** Accepted
- **Date:** November 7, 2026
- **Recorded in:** [PLAN.md](../../PLAN.md) section 6 ("Rules for every source") and section 7.1; [AGENTS.md](../../AGENTS.md#data-source-rules); pull requests [#82](https://github.com/tatsatshah05/NatHacks2026/pull/82) and [#86](https://github.com/tatsatshah05/NatHacks2026/pull/86) (merged 2026-11-07)

## Context

The information OpenPath brings together is spread across City datasets, weather services and road cameras (PLAN.md section 2). Some of it is also visible on web pages that are not meant to be read by programs. Reading those pages would be fragile and could break their terms of use.

## Decision

- OpenPath reads data only through official access: the City of Edmonton open data API (`data.edmonton.ca/resource/<id>.json`, with `SOCRATA_APP_TOKEN`), the 511 Alberta API with the team's key (10 calls per 60 seconds), Environment Canada MSC GeoMet and Open-Meteo.
- No scraping and no private endpoints.
- City of Edmonton camera streams are used only with written permission from the City. Until then, OpenPath links to the City's own viewer.
- The walking network (OpenStreetMap through OSMnx) and the address index (City parcel addresses) are built ahead of time on a laptop and committed, so nothing calls a geocoder or OSMnx at request time.

## Consequences

- Every adapter has recorded fixtures, and replay mode serves them through a fake HTTP transport, so tests and `make dev` never touch the network (`cloud/app/sources.py`).
- Every item carries its source, attribution and a link to the official dataset, and `/sources` lists each source with its licence.
- The camera code reads only the 511 Alberta API (`vision/cameras.py`), through one shared rate limiter for metadata and images.
- Outside training datasets are allowed only where their licence permits it, with attribution (decision D-3, revised on November 8, PLAN.md section 16).
- Open-Meteo's free API is for non-commercial use, so a commercial launch needs a paid plan (decision D-5, PLAN.md section 16).
