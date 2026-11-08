# ADR 0011: Vercel Git deployments for `main` only

- **Status:** Accepted
- **Date:** November 8, 2026
- **Recorded in:** pull request [#169](https://github.com/tatsatshah05/NatHacks2026/pull/169) ("chore: Create Vercel deployments only for main", merged into `phase_3` 2026-11-08T18:08:18Z), which also updates [docs/CI_CD.md](../CI_CD.md), [docs/DEPLOY.md](../DEPLOY.md) and AGENTS.md. The Phase 4 base this ADR was first written on (`3967064`) predates that merge.

## Context

Pull request #169 records the problem. Vercel's Hobby plan allows 100 deployments per rolling 24 hours. The Git integration created a preview deployment for every push to every pull request branch, so the limit was reached on November 8 (`api-deployments-free-per-day`), and production could not deploy until the window cleared. Vercel's Ignored Build Step does not help, because the deployment is created before that step runs.

## Decision

`web/vercel.json` sets `git.deploymentEnabled` to `false` for every branch and `true` for `main`, so Vercel creates deployments only for merges to `main`.

## Consequences

- There are no preview deployments. Web changes are reviewed locally with `make dev`, and CI still runs lint, typecheck and the production build on every pull request.
- Only merges to `main` use the Hobby plan's deployment allowance, so production can always deploy.
- The change is reversible: deleting the `git` block brings previews back, for example after a move to a paid plan.
