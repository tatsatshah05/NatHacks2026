# PathSense

Detect when accessible winter pathways become inaccessible. Our natHACKS 2026 project.

PathSense spots snow, black ice and obstructions on sidewalks using an ESP32-CAM and low-cost sensors on a tabletop model of real Edmonton streets. It then reroutes people live, based on how they get around: wheelchair, walker or cane, low vision, or standard walking. The web app runs on Vercel and the API runs on Render.

## For the team

- [Technical spec](https://claude.ai/artifact/RXvZzsyhUkjnyc4ix92ZjE): architecture, contracts, wiring, gates and open decisions. Edit it and discuss in comments (team access only).
- [Team plan](https://claude.ai/artifact/D7hBPDBBxcMad9je8PTQ63): roles, timeline and parts list (team access only).
- [`CLAUDE.md`](CLAUDE.md): the rules Claude Code follows in this repo.
- [`prompts/`](prompts/): one Claude Code prompt per workstream. The lead runs `00-kickoff.md` once hacking officially starts. It creates `edge/`, `cloud/`, `web/`, `firmware/` and `config/`.

## Workstreams

| Prompt | Owner | Builds |
| --- | --- | --- |
| `00-kickoff.md` | Lead | Repo skeleton, contracts, simulator, tests |
| `01-edge-vision.md` | Edge and vision | ESP32-CAM pipeline, fusion, uplink |
| `02-firmware.md` | Hardware | ESP32-CAM, ice station (Path A), Arduino street controller |
| `03-cloud.md` | Lead | FastAPI on Render: hazards, routing, black ice, push |
| `04-web.md` | Web | Next.js on Vercel: map, routes, alerts, dashboard |
| `05-integration.md` | Lead | Demo checks, offline mode, freeze |
