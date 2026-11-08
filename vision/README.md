# vision

Road-condition model and camera worker (PLAN.md section 7).

- `cameras.py`: 511 Alberta camera list adapter (OP-112).
- `LABELS.md` and `tools/label.py`: the label guide and the keyboard labelling tool (OP-115). Run `python -m vision.tools.label --images vision/data/raw --out vision/data/labels.csv --labeller <name>`; the guide explains the classes, the keys and the one-time OpenCV GUI install.
- `train.py`: trains the road-condition model and exports it to ONNX (OP-202). See [Training](#training) below.
- `evaluate.py` and `MODEL_CARD.md`: scores a trained run on its unseen test cameras and regenerates the model card (OP-203). See [Evaluation](#evaluation) below.
- `tools/dataset.py`: validate local labels, preserve camera-disjoint splits, prepare a blind second-label sample, and measure agreement (OP-201). Follow the [dataset workflow](../docs/ESTELLA_OP201.md); the label tool's `--only` option restricts a session to the sample CSV.
- `tools/collect.py`: preview approved live 511 frames before retaining them, one view per camera by default (`--views all` adds each camera's other enabled views under `raw/view_<view_id>/`); `python -m vision.tools.collect --limit 6 --reviewer Estella`. Supply the actual reviewer; live collection requires it. Press `k` only after privacy review. `--plan` lists camera IDs offline. Wait a minute between runs to respect the shared request window.
- Planned: `worker.py` (OP-204).

Add runtime dependencies to `vision/requirements.txt`; `make setup` installs them. `vision/data/` is git-ignored: images, label CSVs and training runs stay local.

For OP-202, use only the canonical `labels_primary.csv` and explicitly pass the audit's `test_cameras.txt` via `--test-cameras`; do not use a `labels_*.csv` glob or the training script's default split. See the [exact handoff command](../docs/ESTELLA_OP201.md#preserve-the-frozen-test-cameras-in-op-202). Independent second-label CSVs measure agreement and must stay separate from training input.

## Training

Training runs on a laptop or Colab, never on Render. PyTorch lives in `scripts/requirements-train.txt`, which CI and Render never install.

```
pip install -r vision/requirements.txt -r scripts/requirements-train.txt
python -m vision.train --images vision/data/raw --labels vision/data/labels_primary.csv \
    --test-cameras $(cat vision/data/dataset/test_cameras.txt) --out vision/data/runs/v1
```

- `--labels`: one or more OP-115 CSVs. An image labelled twice is kept when both labels agree and dropped when they disagree. Images from camera `unknown` are dropped, because the split could not keep them out of the test cameras.
- `--rscd`: optional RSCD folder (not used for the November 8 models; see D-3 in `docs/DATA_SOURCES.md`) with one subfolder per RSCD class. The friction state in each folder name maps to our classes (`dry`, `wet`/`water` → `wet`, `fresh_snow` → `snow_covered`, `melted_snow` → `snow_partial`, `ice`). This mapping is provisional until the dataset is downloaded and checked; unmapped folders are listed in the log and in `metrics.json`. RSCD is mixed into the training split only, never validation or test, sampled to `--rscd-per-class` images per class (default 1000). There is no separate pre-training stage. With few labelled 511 frames, RSCD can outnumber them many times over, and the class weights balance classes, not sources, so lower `--rscd-per-class` until 511 frames are a large share of each class. RSCD has no `unusable` class, so that class comes only from 511 images.
- `--test-cameras`: fix the test cameras by ID. Without it, a seeded 20% of cameras are held out for testing and 10% for validation. Whole cameras are assigned, never single images.
- `--arch`: `mobilenet_v3_small` (default) or `efficientnet_b0`. `--epochs`, `--batch-size`, `--lr`, `--label-smoothing`, `--workers`, `--latency-runs` and `--seed` (default 202) are the other knobs. Out-of-range values (for example `--epochs 0`) are rejected before anything runs.

One run writes to `--out`:

| File | What it holds |
| --- | --- |
| `model.onnx` | The model: input `image` (N, 3, 224, 224), output `logits` (N, 6). This is what the worker loads |
| `labels.json` | Class order, the exact preprocessing (resize, RGB, ImageNet mean and std) and the model's SHA-256, so the worker matches training. Its wording lives in `vision/model_contract.py`, shared with the worker |
| `split.json` | Train, validation and test cameras, per-class counts, and the test image list for OP-203 |
| `metrics.json` | Everything measured in this run: loss and validation accuracy per epoch, the best epoch, the model's SHA-256, which ONNX exporter ran and its opset, the ONNX-versus-PyTorch difference, and single-thread CPU latency (median and p95) against the 200 ms target |
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
SHA=$(python -c "import json; print(json.load(open('vision/data/runs/v1/metrics.json'))['model_sha256'])")
gh release create vision-model-v1 vision/data/runs/v1/model.onnx vision/data/runs/v1/labels.json \
    --title "Road-condition model v1" \
    --notes "Trained with vision/train.py, seed 202. model.onnx SHA-256: $SHA. Metrics: vision/MODEL_CARD.md."
```

The worker refuses a model whose SHA-256 differs from the one it is given, and reports it as `model_version`. Copy the value from `metrics.json` (it is also in `labels.json` and the `DONE` log line); never retype it.

## Evaluation

`evaluate.py` scores a training run on its test cameras only (from that run's `split.json`) and rewrites `vision/MODEL_CARD.md` from the measured results. It needs onnxruntime, Pillow and numpy, not PyTorch.

```
python -m vision.evaluate --run vision/data/runs/v1 --images vision/data/raw --labels vision/data/labels_*.csv
```

- The test set is rebuilt from the label CSVs with the same rules as training (disagreements and camera `unknown` dropped) and is never re-split. The script refuses a run whose test cameras also appear in training.
- It refuses a `model.onnx` that does not hash to the `sha256` in that run's `labels.json` (or to `model_sha256` in `metrics.json`), and writes the SHA-256 to `evaluation.json` and the card's Run line, so the card describes exactly the release asset the worker loads.
- It reports accuracy, recall and precision per class, the confusion matrix, and labeller agreement (percent and Cohen's kappa). Agreement counts an image only when two rows with different `labeller` values label it, so passing one CSV twice measures nothing. The card names the labellers as recorded and calls them labelling passes, never people, because the labels may be AI-assisted. Results go to `<run>/evaluation.json` and the card; commit the card, not the run.
- What the card says about training comes from the run: RSCD is credited only when `split.json` shows RSCD images in training, and "fine-tuned from ImageNet weights" appears only when `metrics.json` records `pretrained: true`. A value that cannot be computed, such as the precision of a class the model never predicts, reads "n/a".
- Images are prepared exactly as `labels.json` describes: stretch to `size_px` with bilinear resampling, RGB, divide by 255, subtract the mean, divide by the std. The worker (OP-204) must do the same; `test_preprocessing_matches_train_py_exactly` guards it.
