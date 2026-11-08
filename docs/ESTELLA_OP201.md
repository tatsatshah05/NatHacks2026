# OP-201: collect and label the camera dataset

Purpose: prepare real road-camera examples for OpenPath's six-class surface model. OP-202 trains the model; this task supplies trustworthy labels and a test set from cameras the model has never trained on.

## Current status

The workflow is ready and an **incomplete pilot dataset has been collected**. Estella confirmed that Tatsat cleared training-image retention and explicitly authorized AI-assisted visual privacy screening, collection and labeling, later extending the limit to 1,500 total frames. The desktop collector still supports human preview before saving. Agent-assisted collection has retained 54 new frames; six earlier human-collected frames were also visually reviewed. The [data source notes](DATA_SOURCES.md#511-alberta-cameras-op-112-ipsa) record this November 7 user-reported team clearance, its source/date and the measured camera counts. A written provider reply has not been supplied or independently verified here; do not describe the user's confirmation as a letter from 511. Keep actual permission evidence with the private handoff. The dataset and label tools read local files; only collection fetches frames.

**Pilot audit snapshot:** 60 labeled images, 60 unique decoded frames, 60 physical camera IDs; 29 `dry`, 3 `wet`, 28 `unusable`, and zero snow/ice classes. The persistent split currently contains 47 training-pool cameras and 13 test cameras, without camera overlap or exact decoded duplicates across splits. Six images are selected for independent second labeling; none are completed and agreement is unmeasured. Current images are nighttime examples. The primary rows explicitly identify AI-assisted labels/reviews; original human labels are preserved. These counts describe this pilot, not completed acceptance criteria or validated model performance. The test split has 5 dry and 8 unusable images, with no wet/snow/ice coverage; collect missing conditions on held-out cameras too, rather than reshuffling the frozen holdout to improve reported metrics.

Current local files (all ignored by Git): `vision/data/labels_primary.csv` is the canonical combined primary set; `labels_estella.csv` preserves the original six human labels, `labels_reviewed.csv` records their AI-assisted review, and `labels_codex.csv` contains the 54 new AI-assisted labels. `HANDOFF.md` explains limits; `op201_handoff.zip` packages the current data/metadata, and `op201_blind_review.zip` contains only the six sample images, metadata and instructions without first labels. Do not count an AI review that has already seen original labels as independent agreement. Image previews that could not be inspected were skipped without retention.

Next collection must address missing class and daylight coverage, not repeatedly download the same nighttime snapshot or manufacture snow/ice labels. Daytime examples require daylight or an approved retained daytime archive. Night glare and infrared-bright roadside vegetation are common confounders. The remaining target is 1,440 additional unique labeled frames; blind independent review is still a separate required task. Keep the PR in draft while these are missing.

**Measured November 7, 2026, 21:11 MST:** the metadata request returned 392 cameras in total, 19 within the approximate Edmonton bounding box, and 47 enabled views on those cameras. Multiple views are not additional physical cameras. The latest [issue #44](https://github.com/tatsatshah05/NatHacks2026/issues/44) explicitly permits snow and ice training examples from 511 cameras anywhere in Alberta. Use those additional cameras to reach the 20-camera target and diversify real conditions; this does not expand the routing coverage beyond Edmonton. The local, ignored `vision/data/camera_inventory.csv` lists the 19 nearby camera IDs and locations from this cached response, without view URLs, keys or image files.

## Check camera access first (metadata only)

In the IDE's terminal on macOS (zsh), run the following. Paste the actual key only at the hidden prompt, then press Enter. The key is not displayed or included in the command saved in shell history. Set it again when you open a new terminal; do not paste its value into a tracked file or chat.

```sh
read -s 'ALBERTA_511_KEY?Paste the 511 key (hidden), then press Enter: '
export ALBERTA_511_KEY
printf '\n'
```

After installing and activating the environment below, run:

```sh
python -m vision.cameras
```

This fetches camera metadata, caches it in ignored `vision/.cache/`, and prints the approximate Edmonton camera count. It does not download image frames. All teammates share a limit of 10 requests per minute on this key, so coordinate live calls and avoid repeated polling. Share only the counts or a redacted error when reporting the result. Getting metadata successfully does not establish permission to retain training frames.

OP-201 (#44) targets at least **1,500 unique labelled frames from 20 cameras**, all six classes, separate training/test cameras, and independent second labels on at least 10% of images. Prepare the workflow now; keep the issue open until the real data and review exist.

## 1. Set up a labelling environment

Run from the repository root with Python 3.12. A separate local environment gives the desktop tool OpenCV's GUI build without changing the worker's headless dependencies:

```sh
python3.12 -m venv vision/.cache/label-env
source vision/.cache/label-env/bin/activate
python -m pip install 'pillow>=13,<14' 'opencv-python>=4.9,<5' 'httpx>=0.27,<0.29' 'pydantic>=2.9,<3'
```

On Windows, create the environment with `py -3.12` and activate `vision\.cache\label-env\Scripts\Activate.ps1` in PowerShell. No API key is needed to label local files or audit the dataset.

## 2. Organize approved images, then label them

Start with six cameras in the terminal where the key and environment are set:

```sh
python -m vision.tools.collect --limit 6 --reviewer Estella
```

The collector uses the cached list, one enabled view per physical camera, and only HTTPS image URLs on `511.alberta.ca`. It opens a preview for each frame. Press `k` only after confirming there are no identifiable people or readable plates, `s` to skip without saving, or `q` to quit. No image is written before `k`. Accepted originals go in ignored `vision/data/raw/`, with camera/view IDs, retrieval time and reviewer in ignored `vision/data/collection.csv`. `--reviewer` is required for live collection; pass the actual reviewer's name, not somebody else's. The collector does not assign condition labels. Each process starts a new local rate limiter, so wait at least a full minute between quitting/restarting collection and coordinate the shared limit with teammates. HTTP 429 stops the collector; wait at least a minute before another attempt.

For a list of planned camera IDs without a key, network call or window, use `python -m vision.tools.collect --plan --limit 6`. Once the first batch is labelled, `--limit 19` previews one frame from each available camera. Collect further batches at genuinely different times/conditions rather than repeatedly downloading the same snapshot. The dataset audit detects exact decoded duplicates. Run only one collector or live worker on the team key at a time.

For additional provincial snow/ice examples, inspect the camera metadata to choose locations, then preview their actual frames. Weather or location alone never establishes a label. The following lists 20 distinct provincial cameras without contacting 511:

```sh
python -m vision.tools.collect --plan --scope alberta --limit 20
```

Remove `--plan` to preview live frames, or add `--camera-ids 1 2 3` with the actual physical camera IDs you selected. `--scope alberta` is an explicit training-data option; the default remains the approximate Edmonton box. Fetching 20 images takes at least a minute under the local 10-request/60-second limiter, plus your review time. Each local process limits only itself, so coordinate with teammates before starting. The plan counts cameras, not collected or labelled images.

Keep images in `vision/data/raw/`, outside Git. Name each frame `<camera_id>_<YYYYMMDDTHHMMSSZ>.jpg` or `.png`, for example `cam123_20261107T150211Z.jpg`. The collector uses the UTC retrieval time as the timestamp and explicitly records that the actual capture time is unverified; a camera may serve an older or frozen image. Do not describe retrieval times as verified capture times in the model card. Use the **same physical camera ID for every view from that camera**, or the train/test separation will be misleading. Do not substitute a view ID for the camera ID. Organize subfolders if helpful; the CSV stores paths relative to `raw/`.

Collect varied conditions and day/night examples across at least 20 cameras. Repeated copies of one frame do not increase the unique-frame count. Before labelling, follow the privacy rules in [LABELS.md](../vision/LABELS.md): delete identifiable people and readable plates. The audit cannot detect these automatically.

```sh
python -m vision.tools.label --images vision/data/raw --out vision/data/labels_primary.csv --labeller Estella
```

The window shows one image at a time. Keys `1`–`6` select `dry`, `wet`, `snow_partial`, `snow_covered`, `ice`, `unusable`. Use the existing guide's decision order. `d` deletes a privacy-sensitive image, `s` skips, `b` goes back, and `q` saves and quits. Resume with the same command. Never fill in labels from the weather forecast or filenames.

## 3. Prepare splits and the blind sample

```sh
python -m vision.tools.dataset --images vision/data/raw --labels vision/data/labels_primary.csv --out vision/data/dataset
```

The command checks CSV metadata against filenames, checks that labelled images can be decoded, and produces:

| File in `vision/data/dataset/` | Purpose |
| --- | --- |
| `registry.json` | Persistent camera assignments and second-label sample; preserve this between runs. |
| `train.csv`, `test.csv` | Full label rows, separated by camera; initially roughly 80%/20% of cameras. |
| `test_cameras.txt` | Frozen test camera IDs, one per line, exported explicitly for OP-202's `--test-cameras`. |
| `double_label.csv` | A deterministic sample rounded up to 10%, with image paths and capture metadata only. No original labels or labeller names. |
| `report.json` | Counts by class (overall and `split_class_counts` per split), cameras, exact decoded duplicates, measured agreement, disagreements, and outstanding targets. |

Rerun after adding labels. Existing camera assignments stay fixed. New cameras get a deterministic assignment; their frame counts may make the image split different from 80%/20%. Keep `registry.json` when handing the dataset to OP-202, and never reshuffle cameras after training starts. Changing the seed requires a new output folder and a deliberate new dataset version. Do not merge or train from a report containing identical frames across splits. Near-duplicate frames and overlapping camera scenes still need human review.

## 4. Ask a different person to label the sample independently

Give the second person access only to the approved images, the label guide and `double_label.csv`, without your original labels or report. Have them choose their own labels before discussing any disagreements. They run the following with their own name and a **separate output CSV**:

```sh
python -m vision.tools.label --images vision/data/raw --out vision/data/labels_second.csv --labeller Ipsa --only vision/data/dataset/double_label.csv
```

`--only` shows just the selected images and resumes their progress. An empty sample shows nothing. Valid sample paths whose files were deleted are skipped on resume; paths outside the image folder are still rejected, including symlink escapes. The second person must still delete privacy-sensitive images; remove those entries from the primary labels and all copies/archives too before rebuilding the dataset.

Then measure agreement:

```sh
python -m vision.tools.dataset --images vision/data/raw --labels vision/data/labels_primary.csv --second-labels vision/data/labels_second.csv --out vision/data/dataset
```

`agreement_percent` is the fraction of completed sample pairs that match; it is `null` before any second labels exist. Check `double_label_completed` against `double_label_selected`: 100% agreement on an incomplete sample does not pass the sample target. Review the confusion matrix and disagreements together after both people finish. Preserve the independent labels; record any reconciled training labels separately so the agreement measurement stays honest.

## 5. Hand off real evidence

Share the ignored dataset folder only through **team-only, access-restricted private storage**, including the registry, image files, CSVs, source permission/attribution and report. Never use a public link, release asset or repository for training frames. Delete frames and all image-containing copies/archives if 511 declines retention. Commit code and documentation only. OP-203's model card should record real counts, split, source permission, coverage limitations, sample size and measured agreement. No accuracy or F1 score comes from this tool; OP-202/203 measure those on the held-out cameras.

### Preserve the frozen test cameras in OP-202

OP-202's `train.py` (PR #110) does **not** read `registry.json`. Its default seed and camera split differ from the dataset audit and can move our held-out cameras into training/validation. Once OP-202 is available in the checkout and its training dependencies are installed, use this exact macOS/Linux command:

```sh
python -m vision.train \
  --images vision/data/raw \
  --labels vision/data/labels_primary.csv \
  --test-cameras $(cat vision/data/dataset/test_cameras.txt) \
  --out vision/data/runs/v1
```

`test_cameras.txt` comes directly from `report.json` → `split_cameras.test`; camera IDs here are the numeric 511 IDs. Passing an empty list must fail, not fall back to a new split. OP-202 may choose validation cameras from the remaining training pool; the frozen test IDs must never appear in either training or validation. Compare `runs/v1/split.json` → `test_cameras` with `dataset/report.json` → `split_cameras.test` before using the model. Preserve those assignments as data grows.

Compatibility was checked against PR #110 commit `6c088726`: its data loader accepted all 60 canonical samples without dropping disagreements, and `split_by_camera(..., seed=202, test_cameras=...)` produced 41 training, 6 validation and the exact 13 exported test cameras. Adding a synthetic future camera did not move any held-out camera into training or validation. This was a data-preparation check without PyTorch training or model metrics.

Pass **only** `labels_primary.csv` for this dataset. Do not use the `labels_*.csv` glob in the older training example: it includes original/reviewed copies and `labels_second.csv`, and OP-202 drops an image if any input labels disagree. Keep blind second labels for agreement measurement; explicitly adjudicate disagreements before preparing a new version of the canonical primary CSV. Use optional RSCD inputs only under OP-202's documented source/mapping rules, never as a replacement for held-out 511 test images.

The report's `measured_targets_met` covers machine-checkable counts only. A person must still confirm retention permission, privacy, day/night coverage and label quality. An empty header-only CSV is supported for setup and reports zero progress; it does not complete OP-201.
