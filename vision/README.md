# vision

Road-condition model and camera worker (PLAN.md section 7).

- `cameras.py`: 511 Alberta camera list adapter (OP-112).
- `LABELS.md` and `tools/label.py`: the label guide and the keyboard labelling tool (OP-115). Run `python -m vision.tools.label --images vision/data/raw --out vision/data/labels.csv --labeller <name>`; the guide explains the classes, the keys and the one-time OpenCV GUI install.
- `tools/dataset.py`: validate local labels, preserve camera-disjoint splits, prepare a blind second-label sample, and measure agreement (OP-201). Follow the [dataset workflow](../docs/ESTELLA_OP201.md); the label tool's `--only` option restricts a session to the sample CSV.
- `tools/collect.py`: preview approved live 511 frames before retaining them, one view per camera by default (`--views all` adds each camera's other enabled views under `raw/view_<view_id>/`); `python -m vision.tools.collect --limit 6 --reviewer Estella`. Supply the actual reviewer; live collection requires it. Press `k` only after privacy review. `--plan` lists camera IDs offline. Wait a minute between runs to respect the shared request window.
- Planned: `train.py` (OP-202), `MODEL_CARD.md` (OP-203), `worker.py` (OP-204).

For OP-202, use only the canonical `labels_primary.csv` and explicitly pass the audit's `test_cameras.txt` via `--test-cameras`; do not use a `labels_*.csv` glob or the training script's default split. See the [exact handoff command](../docs/ESTELLA_OP201.md#preserve-the-frozen-test-cameras-in-op-202). Independent second-label CSVs measure agreement and must stay separate from training input.

Add runtime dependencies to `vision/requirements.txt`; `make setup` installs them. `vision/data/` is git-ignored: images and label CSVs stay local.
