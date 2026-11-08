# ADR 0009: Phase branches, squash-only merges and required CI

- **Status:** Accepted
- **Date:** November 7, 2026, with one change on November 8, 2026
- **Recorded in:** [docs/CI_CD.md](../CI_CD.md); [AGENTS.md](../../AGENTS.md#how-to-work-a-story); [PLAN.md](../../PLAN.md) section 12; pull requests [#84](https://github.com/tatsatshah05/NatHacks2026/pull/84) (CI pipeline, pull request rules and squash merges, merged 2026-11-07T21:32:05Z), [#87](https://github.com/tatsatshah05/NatHacks2026/pull/87) (phase branches, merged 2026-11-07T23:50:50Z), [#107](https://github.com/tatsatshah05/NatHacks2026/pull/107) (releases through `release/phase-N`, merged 2026-11-08T02:52:03Z) and [#137](https://github.com/tatsatshah05/NatHacks2026/pull/137) (deploy after CI on a push, merged 2026-11-08T07:29:18Z)

## Context

Six people work in four phases of a 48-hour event, each phase ending at a checkpoint (PLAN.md section 12). `main` must always pass, and the live demo must never fail (AGENTS.md, priorities 1 and 5). Many pull requests land in a few hours, so the rules have to be checked by machines, not remembered.

## Decision

- Story pull requests go into the current phase branch (`OP-1xx` into `phase_1`, `OP-2xx` into `phase_2`, and so on). When a phase passes its checkpoint, the lead releases it into `main` with one pull request, and the next phase branch starts from `main`.
- Every pull request needs the `CI passed` check and one approval from Tatsat, Ipsa or Kashish. New commits reset the approval, and every conversation must be resolved.
- Merges are squash-only, and nobody pushes directly to `main` or a phase branch.
- CI checks the pull request title and issue link, repository hygiene (no secrets, no model weights, no stray images, a gitleaks scan), the Python code (ruff and pytest) and the web app (lint, typecheck and build).
- Render's auto-deploy is off. CI's deploy job calls the Render deploy hook only after every check passes, then waits until `/health` reports the new commit.

## Consequences

- Each story becomes one commit on its phase branch, and each phase one commit on `main`.
- Because squash merges leave a phase branch without `main` as an ancestor, a release goes through `release/phase-N`, the phase branch with `main` merged in (`scripts/phase.sh finish N`, pull request #107).
- Merges into a phase branch deploy to staging, and releases into `main` deploy to production.
- GitHub does not let authors approve their own work, so a reviewer's own pull requests are approved by one of the other two. The admin bypass is for emergencies only, after `CI passed` is green.
- The deploy job had never run on a push until pull request #137 fixed its condition; the early release of Phase 2 (#134) therefore did not deploy on its own.
