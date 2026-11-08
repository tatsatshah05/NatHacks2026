# ADR 0005: The stack

- **Status:** Accepted. The vision worker's hosting was changed on November 8 ([ADR 0006](0006-camera-worker-on-lead-laptop.md)).
- **Date:** November 7, 2026
- **Recorded in:** [PLAN.md](../../PLAN.md) section 9 (layer table) and section 8 (memory budget); [AGENTS.md](../../AGENTS.md#decisions-already-made); pull request [#82](https://github.com/tatsatshah05/NatHacks2026/pull/82)

## Context

Six people had 48 hours to build a web app, an API with scheduled data sources, a routing engine for the whole city and a camera model. The API runs on a small Render instance, and PLAN.md section 8 sets a memory budget of under 300 MB for it. `cloud/app/network.py` records why the network cannot be loaded the usual way: the OSMnx graph of the whole city, as a networkx object, needs about 2.8 GB. The sources do not record why these frameworks were chosen over others.

## Decision

| Layer | Technology | Hosting |
| --- | --- | --- |
| Web app | Next.js (App Router), TypeScript, Tailwind, MapLibre, a PWA with Web Push | Vercel |
| API | Python 3.12, FastAPI, Pydantic v2, SQLModel, WebSocket | Render web service |
| Ingest | Scheduled adapters inside the API process, one per source | Render |
| Database | Postgres on Render; SQLite locally and in tests | Render |
| Routing | SciPy Dijkstra on a prebuilt OpenStreetMap walking network stored as compact NumPy arrays | Inside the API |
| Vision worker | Python and ONNX Runtime on the CPU | A team laptop ([ADR 0006](0006-camera-worker-on-lead-laptop.md)) |
| Training | PyTorch, on a laptop or Colab | Not deployed |

## Consequences

- The walking network is converted once, on a laptop (`make graph`, `scripts/build_graph.py`), into `cloud/data/network.npz`, which is committed. At run time `WalkNetwork.load()` reads plain arrays and never imports OSMnx or networkx. Measured in this repository: the file is 6.5 MB and its arrays take 20.5 MB in memory ([docs/ARCHITECTURE.md](../ARCHITECTURE.md#numbers-in-this-document)).
- Routing works on node and edge indices, never on OSM ids, and weight 0 in the sparse matrix means an edge is removed.
- Because ingest runs inside the API process, nothing polls while the free Render instance sleeps. The camera worker's posts every 5 minutes keep it awake ([docs/DEPLOY.md](../DEPLOY.md#4-camera-worker-on-demo-day)).
- The same store code runs on SQLite and Postgres. In-memory SQLite in tests shares one connection, so every thread that uses the store must stop before the engine is closed (`cloud/app/main.py`, `stop_store_users`).
- API types in the web app are generated from the API's OpenAPI document (`make types`), so the two sides cannot drift silently.
