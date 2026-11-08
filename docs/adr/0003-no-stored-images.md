# ADR 0003: No stored images

- **Status:** Accepted
- **Date:** November 7, 2026 (the rule); November 8, 2026 (how training frames are kept)
- **Recorded in:** [PLAN.md](../../PLAN.md) sections 3 and 7.5; [AGENTS.md](../../AGENTS.md#vision-rules); pull request [#82](https://github.com/tatsatshah05/NatHacks2026/pull/82); issue [#132](https://github.com/tatsatshah05/NatHacks2026/issues/132) (lead decisions, November 8)

## Context

Road cameras and phone photos of sidewalks can show people and licence plates. Version 1 already found that filming a public sidewalk risked recording people (PLAN.md section 3). OpenPath only needs the road condition a picture shows, not the picture.

## Decision

- Camera frames are processed in memory and never stored.
- Community report photos are classified in memory and dropped with the request.
- The only images kept are labelled training images, where the camera's terms allow it, and never an image in which a person can be identified.
- Training frames from 511 Alberta are kept privately on the lead's laptop, never in Git or anywhere public, credited to the Government of Alberta and 511 Alberta. Any frame with an identifiable person or a readable plate is deleted, and every frame is deleted after the event (issue #132).

## Consequences

- The camera worker classifies only `image/jpeg` responses, in memory, and posts metadata only (`vision/worker.py`, `vision/inference.py`).
- The reports endpoint parses the request body itself, because Starlette's form parser spools large uploads to a temporary file (`cloud/app/reports.py`). A test with a disk-write spy checks that a report photo is never written anywhere.
- CI rejects model weights and images outside a few allowed folders ([docs/CI_CD.md](../CI_CD.md)).
- Frames and report photos are sent in memory to the Anthropic API for the Claude check ([ADR 0007](0007-model-and-claude-agree.md)). The privacy page says so.
- The web app never shows camera images. The city dashboard links to 511 Alberta's camera list instead.
