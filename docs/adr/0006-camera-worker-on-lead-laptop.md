# ADR 0006: The camera worker runs on the lead's laptop

- **Status:** Accepted
- **Date:** November 8, 2026
- **Recorded in:** [PLAN.md](../../PLAN.md) section 16 ("Decided: the camera worker ... runs on one team laptop"); [AGENTS.md](../../AGENTS.md) (Current Status, "Camera worker hosting"); [docs/DEPLOY.md](../DEPLOY.md#4-camera-worker-on-demo-day); pull requests [#126](https://github.com/tatsatshah05/NatHacks2026/pull/126) (merged 2026-11-08T13:32:39Z) and [#147](https://github.com/tatsatshah05/NatHacks2026/pull/147) (merged 2026-11-08T14:32:33Z)

## Context

The camera worker (OP-204) fetches 511 Alberta frames every 5 minutes, classifies them and posts readings to the API. The original plan ran it on Render. Render's free plan has no background workers; pull request #126 quotes Render's documentation: "Other service types don't support Free instances". The 511 key also allows only 10 calls per 60 seconds for the whole team.

## Decision

The worker runs on one team laptop during the demo and posts to the live API on Render with `WORKER_KEY`. Pull request #126 first named Estella to run it; the lead then decided the same day that it runs on Tatsat's laptop for the whole event, including demo day (pull request #147).

## Consequences

- No new service is needed, and the `RENDER_WORKER_DEPLOY_HOOK` secret is not used.
- Only one machine may run the worker, and frame collection must stop while it runs, because the 511 rate limit is shared.
- The laptop must stay awake and on power (`caffeinate -i` on macOS). If it stops, its readings expire after 15 minutes (three 5-minute intervals) and camera ice risks disappear with them; the weather watch keeps running. The fallback notice in the web app is OP-412.
- The worker's posts every 5 minutes also keep the free API awake, so it should start at least 10 minutes before judging.
- The worker's secrets (`ALBERTA_511_KEY`, `WORKER_KEY`, `ANTHROPIC_API_KEY`) stay on that laptop.
