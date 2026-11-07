# PathSense

Detect when accessible winter pathways become inaccessible. Our natHACKS 2026 project.

PathSense spots snow, black ice and obstructions on sidewalks using an ESP32-CAM and low-cost sensors on a tabletop model of real Edmonton streets. It then reroutes people live, based on how they get around: wheelchair, walker or cane, low vision, or standard walking. The web app runs on Vercel and the API runs on Render.

## For the team

- [Technical spec](https://claude.ai/artifact/RXvZzsyhUkjnyc4ix92ZjE): architecture, contracts, wiring, gates and open decisions. Edit it and discuss in comments (team access only).
- [Team plan](https://claude.ai/artifact/D7hBPDBBxcMad9je8PTQ63): roles, timeline and parts list (team access only).
- [`CLAUDE.md`](CLAUDE.md): the rules Claude Code follows in this repo.
- [`prompts/`](prompts/): one Claude Code prompt per workstream. The lead runs `00-kickoff.md` once hacking officially starts. It creates `edge/`, `cloud/`, `web/`, `firmware/` and `config/`.

## How we work

We move as one team through four phases: **hardware, then vision model, then web app, then finish**. Each person has one task per phase. Read [`GUIDELINE.md`](GUIDELINE.md) for the full split, the gates and the Git workflow, and find your tasks under [Issues](https://github.com/tatsatshah05/NatHacks2026/issues) by your `person:` label.

| Phase | Hours | Milestone |
| --- | --- | --- |
| 1. Hardware | 0-10 | Phase 1: Hardware |
| 2. Vision model | 10-22 | Phase 2: Vision model |
| 3. Web app | 22-34 | Phase 3: Web app |
| 4. Finish | 34-48 | Phase 4: Finish |
