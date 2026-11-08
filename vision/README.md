# vision

Road-condition model and camera worker (PLAN.md section 7).

- `cameras.py`: 511 Alberta camera list adapter (OP-112).
- `LABELS.md` and `tools/label.py`: the label guide and the keyboard labelling tool (OP-115). Run `python -m vision.tools.label --images vision/data/raw --out vision/data/labels.csv --labeller <name>`; the guide explains the classes, the keys and the one-time OpenCV GUI install.
- `train.py`: trains the road-condition model and exports it to ONNX (OP-202). See [Training](#training) below.
- Planned: `MODEL_CARD.md` (OP-203), `worker.py` (OP-204).

Add runtime dependencies to `vision/requirements.txt`; `make setup` installs them. `vision/data/` is git-ignored: images, label CSVs and training runs stay local.

## Training

Training runs on a laptop or Colab, never on Render. PyTorch lives in `scripts/requirements-train.txt`, which CI and Render never install.

```
pip install -r vision/requirements.txt -r scripts/requirements-train.txt
python -m vision.train --images vision/data/raw --labels vision/data/labels_*.csv \
    --rscd vision/data/rscd --out vision/data/runs/v1
```

- `--labels`: one or more OP-115 CSVs. An image labelled twice is kept when both labels agree and dropped when they disagree. Images from camera `unknown` are dropped, because the split could not keep them out of the test cameras.
- `--rscd`: optional RSCD folder with one subfolder per RSCD class. The friction state in each folder name maps to our classes (`dry`, `wet`/`water` → `wet`, `fresh_snow` → `snow_covered`, `melted_snow` → `snow_partial`, `ice`). This mapping is provisional until the dataset is downloaded and checked; unmapped folders are listed in the log and in `metrics.json`. RSCD is sampled to `--rscd-per-class` images per class (default 1000) and only ever goes into training. RSCD has no `unusable` class, so that class comes only from 511 images.
- `--test-cameras`: fix the test cameras by ID. Without it, a seeded 20% of cameras are held out for testing and 10% for validation. Whole cameras are assigned, never single images.
- `--arch`: `mobilenet_v3_small` (default) or `efficientnet_b0`. `--epochs`, `--batch-size`, `--lr`, `--label-smoothing`, `--workers`, `--latency-runs` and `--seed` (default 202) are the other knobs. Out-of-range values (for example `--epochs 0`) are rejected before anything runs.

One run writes to `--out`:

| File | What it holds |
| --- | --- |
| `model.onnx` | The model: input `image` (N, 3, 224, 224), output `logits` (N, 6). This is what the worker loads |
| `labels.json` | Class order and the exact preprocessing (resize, RGB, ImageNet mean and std), so the worker matches training |
| `split.json` | Train, validation and test cameras, per-class counts, and the test image list for OP-203 |
| `metrics.json` | Everything measured in this run: loss and validation accuracy per epoch, the best epoch, the ONNX-versus-PyTorch difference, and single-thread CPU latency (median and p95) against the 200 ms target |
| `checkpoint.pt` | PyTorch weights of the best epoch |

`train.py` never touches the test cameras; OP-203 evaluates on them and writes `MODEL_CARD.md`.

### On Colab

1. Runtime → Change runtime type → T4 GPU.
2. Clone the repository and install, in three cells: `!git clone https://github.com/tatsatshah05/NatHacks2026`, then `%cd NatHacks2026` (a plain `!cd` does not persist), then `!pip install -r vision/requirements.txt -r scripts/requirements-train.txt`.
3. Upload the images and label CSVs to Google Drive (never to the repository), mount Drive, and point `--images`, `--labels` and `--rscd` at them.
4. Run `!python -m vision.train ... --out /content/drive/MyDrive/openpath/runs/v1`. Note that `metrics.json` then reports Colab's CPU latency, not the Render worker's.

### Publishing the weights

Weights are never committed. Publish `model.onnx` and `labels.json` as a GitHub release asset, then point the worker's `MODEL_URL` at the asset:

```
gh release create vision-model-v1 vision/data/runs/v1/model.onnx vision/data/runs/v1/labels.json \
    --title "Road-condition model v1" --notes "Trained with vision/train.py, seed 202. Metrics: see the attached run and vision/MODEL_CARD.md."
```
