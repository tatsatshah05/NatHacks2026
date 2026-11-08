# ADR 0007: A reading posts only when the model and Claude agree

- **Status:** Accepted
- **Date:** November 8, 2026 (camera frames, then report photos the same afternoon)
- **Recorded in:** [PLAN.md](../../PLAN.md) section 16 (two "Decided" entries); issue [#132](https://github.com/tatsatshah05/NatHacks2026/issues/132); pull requests [#146](https://github.com/tatsatshah05/NatHacks2026/pull/146) (camera frames, merged 2026-11-08T13:45:29Z) and [#158](https://github.com/tatsatshah05/NatHacks2026/pull/158) (report photos, merged 2026-11-08T16:59:57Z); [vision/MODEL_CARD.md](../../vision/MODEL_CARD.md)

## Context

The worst mistake a road reading can make is a false `dry`: it tells a person a surface is fine when it is not. `vision/ai_check.py` is built so that "a disagreement can never turn into a false `dry`". The model card measures how often the ONNX model alone posts a false `dry` on frames from 26 cameras it never trained on, and how often that happens when Claude must agree.

## Decision

- **Camera frames.** The ONNX model looks at every frame first. When it would post a reading, Claude looks at the same frame with the team's label guide (`vision/LABELS.md`). The worker posts the reading only when both agree. A disagreement, a refusal, an error or a timeout gives `unknown`. Claude Haiku 5.5 is the default; Opus 5.5 and Sonnet 5.5 can be chosen with `--ai-check`.
- **Report photos.** The model runs first, and Claude is asked only when the model confirms the reported type. A report gets `medium` confidence only when both agree. For `blocked`, which none of the six classes can show, Claude answers "Is the walking path in this photo blocked?" and the model's class must not be `unusable`, so blocked confirmation rests on Claude alone. Anything else gives `low`, the reported type is kept, and the response lists the checks that answered (`photo.checked_by`).

## Consequences

- From the model card, on 308 test frames from unseen 511 cameras: the main model alone posted 242 readings with 19 false `dry`; with Claude it posted 211 readings with 4 false `dry`. Fewer readings are posted, and fewer of them are wrong.
- These are agreement figures with labels that were themselves made with Claude's help (`Claude (AI-assisted)` in the label CSVs), so they measure agreement with Claude-made labels, not with human labels. The model card says so.
- Each call has a 10 s timeout and one retry. After 3 API failures in a row the check pauses for 5 minutes, and those frames post `unknown` (report photos get `low`). A slow API never holds up the worker for long.
- Cost: the model card measured US$0.04 for the Claude check on the 240 test frames the model would have posted. The cost per report photo has not been measured ([docs/DEPLOY.md](../DEPLOY.md#6-report-photo-check-on-render-op-308)).
- Frames and photos are sent to the Anthropic API in memory and never stored by OpenPath ([ADR 0003](0003-no-stored-images.md)). Claude is sent only the photo and the question, never the note, the location or the device id.
- `ANTHROPIC_API_KEY` is needed on the worker laptop and on Render. Without it, the worker refuses `--ai-check` at startup, and the API checks report photos with the model alone and logs a warning (with no model either, every report gets `low`). `make dev` and the tests run without it.
