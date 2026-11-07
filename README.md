# OpenPath

Every barrier between A and B, in one place. Accessible routing for Edmonton. Built for natHACKS 2026 by Team ClaudesPlan.

## What It Does

Enter where you are and where you are going, and choose how you get around: wheelchair, walker or cane, low vision, or standard. OpenPath finds the best route and lists every known barrier along it:

- **Indoor:** LRT elevators and escalators that are out of service
- **Sidewalks and paths:** construction closures, trail closures, snow complaints and community reports
- **Roads and crossings:** road closures, incidents, and snow ridges left by plows
- **Weather and ice:** a black-ice watch from live weather, and road conditions read from public cameras by our vision model

Routes avoid what each person cannot pass. Saved routes send an alert when they change, and every route opens in Google Maps or Apple Maps.

## How It Works

- **Data:** City of Edmonton open data, Environment Canada and Open-Meteo weather, 511 Alberta cameras, community reports and OpenStreetMap
- **Vision:** a small model classifies road conditions (dry, wet, snow, ice) from public camera images
- **Server:** a Python API on Render gathers every source, matches it to the walking network and calculates routes
- **Web app:** a Next.js site on Vercel shows the map, routes, the barrier report and live alerts

There is no hardware.

## How We Work

The team works through four phases together. Each person owns about 8 story points per phase.

| Phase | Hours | Milestone |
| --- | --- | --- |
| 1. Data Foundation | 0–10 | Phase 1: Data Foundation |
| 2. Vision and Routing | 10–22 | Phase 2: Vision and Routing |
| 3. Web App and Alerts | 22–34 | Phase 3: Web App and Alerts |
| 4. Production and Launch | 34–48 | Phase 4: Production and Launch |

## Team

Tatsat (lead), Ipsa, Estella, Kaleb, Matthew and Kashish.

## Getting Started

1. Read [PLAN.md](PLAN.md) for the product, architecture and backlog.
2. Read [User Stories](docs/USER_STORIES.md) to see your stories, and [GUIDELINE.md](GUIDELINE.md) for the workflow.
3. Find your stories under [Issues](https://github.com/tatsatshah05/NatHacks2026/issues) using your name label.
4. See [CLAUDE.md](CLAUDE.md) for the technical rules Claude Code follows.

## Documents

- [OpenPath Plan (PDF)](docs/OpenPath%20Plan.pdf): the full plan, ready to share
- [User Stories](docs/USER_STORIES.md) ([PDF](docs/OpenPath%20User%20Stories.pdf)): all 53 user stories, who owns each one, and story points
- [Version 1 hardware design](docs/archive/v1-hardware/) (superseded)

## Data Attribution

Contains information licensed under the City of Edmonton Open Data Terms of Use, Environment and Climate Change Canada data under the Open Government Licence – Canada, weather data by Open-Meteo, and map data © OpenStreetMap contributors.
