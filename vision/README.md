# vision

Road-condition model and camera worker (PLAN.md section 7).

- `cameras.py`: 511 Alberta camera list adapter (OP-112).
- `LABELS.md` and `tools/label.py`: the label guide and the keyboard labelling tool (OP-115). Run `python -m vision.tools.label --images vision/data/raw --out vision/data/labels.csv --labeller <name>`; the guide explains the classes, the keys and the one-time OpenCV GUI install.
- `worker.py` and `inference.py` (OP-204): scheduled in-memory classification,
  shared 511 rate limiting, two-observation stability and a proposed ingest
  client. `python -m vision.worker` runs a network-free synthetic replay.
  Live model/image/API integration remains pending; see [OP-204](../docs/OP-204.md).
- Planned: `train.py` (OP-202), `MODEL_CARD.md` (OP-203).

Add runtime dependencies to `vision/requirements.txt`; `make setup` installs them. `vision/data/` is git-ignored: images and label CSVs stay local.
