# PathSense

Real-time accessible routing for winter sidewalks. Built for natHACKS 2026.

## What It Does

PathSense uses a camera and sensors to detect snow, ice and obstructions on sidewalks. When a sidewalk becomes unsafe, it finds an accessible alternative route and notifies the people who use it. The demo runs on a model of real Edmonton streets.

## How It Works

- **Hardware:** a Raspberry Pi with a camera watches the model. An Arduino reads distance sensors and controls two servos.
- **Vision:** the Pi identifies the condition of each sidewalk segment.
- **Server:** a Python API on Render stores conditions and calculates routes.
- **Web app:** a Next.js site on Vercel shows a live map, routes and notifications.

## How We Work

The team works through four phases together. Each person has one task per phase.

| Phase | Hours | Milestone |
| --- | --- | --- |
| 1. Hardware | 0–10 | Phase 1: Hardware |
| 2. Vision Model | 10–22 | Phase 2: Vision Model |
| 3. Web App | 22–34 | Phase 3: Web App |
| 4. Finish | 34–48 | Phase 4: Finish |

## Team

Tatsat, Ipsa, Estella, Kaleb, Matthew and Kashish.

## Getting Started

1. Read [GUIDELINE.md](GUIDELINE.md) for the phases, assignments and workflow.
2. Find your tasks under [Issues](https://github.com/tatsatshah05/NatHacks2026/issues) using your name label.
3. See [CLAUDE.md](CLAUDE.md) for the technical rules Claude Code follows, and [prompts/](prompts/) for reference prompts for each phase.

## Documents

- [Technical Spec](https://claude.ai/artifact/RXvZzsyhUkjnyc4ix92ZjE) (team access)
- [Team Plan](https://claude.ai/artifact/D7hBPDBBxcMad9je8PTQ63) (team access)
