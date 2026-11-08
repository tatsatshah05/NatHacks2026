"""Evaluate the road-condition model on unseen cameras and write the model card (OP-203).

Usage (needs onnxruntime from scripts/requirements-train.txt; no PyTorch):
    python -m vision.evaluate --run vision/data/runs/v1 --images vision/data/raw \
        --labels vision/data/labels_*.csv

Honest numbers (AGENTS.md):
- Only the test cameras listed in the run's split.json are scored. They never
  appeared in training or validation (vision/train.py), so the result is how the
  model does on cameras it has never seen. The test set is rebuilt from the label
  CSVs with the same rules as training, never re-split.
- Images are prepared exactly as labels.json describes, which is what the worker
  (OP-204) must do too; a test checks this matches train.py's own preprocessing.
- The model is identified by its SHA-256. evaluate.py refuses a model.onnx that
  does not hash to labels.json's sha256, and records the hash on the card, so the
  card describes exactly the release asset the worker loads.
- Every number written to MODEL_CARD.md comes from this run: accuracy and the
  per-class results from the test cameras here, speed from train.py's
  metrics.json, agreement from the label CSVs. Training details (RSCD, ImageNet
  weights) are taken from the run, never assumed. A section with nothing measured
  says "Not measured yet", and a value that cannot be computed says "n/a".

Writes <run>/evaluation.json and vision/MODEL_CARD.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vision.tools.label import LABELS, read_labels
from vision.train import load_labelled

log = logging.getLogger("vision.evaluate")

ACCURACY_TARGET = (
    0.80  # PLAN.md OP-203 checkpoint target, to be confirmed after the first baseline
)
DEFAULT_CARD = Path(__file__).resolve().parent / "MODEL_CARD.md"


# ---------------------------------------------------------------------------
# Metrics (pure Python, tested in CI)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Metrics:
    total: int
    correct: int
    confusion: list[list[int]]  # confusion[true][predicted], in LABELS order

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.total if self.total else None

    def recall(self, label: str) -> float | None:
        i = LABELS.index(label)
        row = self.confusion[i]
        return row[i] / sum(row) if sum(row) else None

    def precision(self, label: str) -> float | None:
        i = LABELS.index(label)
        column = sum(row[i] for row in self.confusion)
        return self.confusion[i][i] / column if column else None

    def support(self, label: str) -> int:
        return sum(self.confusion[LABELS.index(label)])


def score(truth: Sequence[str], predicted: Sequence[str]) -> Metrics:
    """Accuracy and the confusion matrix for paired true and predicted labels."""
    if len(truth) != len(predicted):
        raise ValueError("truth and predictions differ in length")
    index = {label: i for i, label in enumerate(LABELS)}
    confusion = [[0] * len(LABELS) for _ in LABELS]
    for t, p in zip(truth, predicted, strict=True):
        confusion[index[t]][index[p]] += 1
    correct = sum(confusion[i][i] for i in range(len(LABELS)))
    return Metrics(total=len(truth), correct=correct, confusion=confusion)


@dataclass(frozen=True)
class Agreement:
    images: int  # images labelled by two different labellers
    agreed: int
    kappa: float | None  # Cohen's kappa, first labeller of each image vs the second
    labellers: tuple[str, ...] = ()  # the labeller names behind those pairs

    @property
    def percent(self) -> float | None:
        return self.agreed / self.images if self.images else None


def _labels_by_labeller(
    label_csvs: Iterable[Path],
) -> dict[str, dict[str, tuple[str, str]]]:
    """image -> {labeller key: (labeller name, label)}, first label per labeller.

    Labeller names are compared without case or surrounding spaces. Rows with an
    empty labeller are skipped, because nobody can tell who made them.
    """
    by_image: dict[str, dict[str, tuple[str, str]]] = {}
    for csv_path in label_csvs:
        for image, row in read_labels(csv_path).items():
            name = row.labeller.strip()
            if name:
                by_image.setdefault(image, {}).setdefault(
                    name.casefold(), (name, row.label)
                )
    return by_image


def labeller_names(label_csvs: Iterable[Path]) -> list[str]:
    """Every labeller named in the label CSVs, as first written, in sorted order."""
    names: dict[str, str] = {}
    for labels in _labels_by_labeller(label_csvs).values():
        for key, (name, _) in labels.items():
            names.setdefault(key, name)
    return sorted(names.values(), key=str.casefold)


def labeller_agreement(label_csvs: Iterable[Path]) -> Agreement:
    """Agreement on images labelled by two different labellers (OP-201's agreement set).

    A pair counts only when the two labels come from different `labeller` values,
    so passing the same CSV twice, or two files from one labeller, measures
    nothing. When three or more labellers labelled an image, the first two (in
    --labels order) are compared.
    """
    pairs: list[tuple[str, str]] = []
    names: dict[str, str] = {}  # labeller key -> name as first written
    for labels in _labels_by_labeller(label_csvs).values():
        if len(labels) >= 2:
            (key_a, (name_a, label_a)), (key_b, (name_b, label_b)) = list(
                labels.items()
            )[:2]
            pairs.append((label_a, label_b))
            names.setdefault(key_a, name_a)
            names.setdefault(key_b, name_b)
    if not pairs:
        return Agreement(0, 0, None)
    agreed = sum(a == b for a, b in pairs)
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    n = len(pairs)
    observed = agreed / n
    expected = sum(first[label] * second[label] for label in LABELS) / (n * n)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return Agreement(
        images=n,
        agreed=agreed,
        kappa=kappa,
        labellers=tuple(sorted(names.values(), key=str.casefold)),
    )


# ---------------------------------------------------------------------------
# Model identity
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_model_sha256(
    model_path: Path, labels_spec: dict[str, Any], training: dict[str, Any]
) -> str:
    """The SHA-256 of model.onnx, after checking it is the model labels.json describes.

    The worker (OP-204) loads model.onnx and labels.json from the release asset
    and refuses a model with another hash, so the card must describe that exact
    file. metrics.json's model_sha256, when present, must agree too.
    """
    expected = labels_spec.get("sha256")
    if not expected:
        raise ValueError(
            "labels.json has no sha256; re-export the run with vision/train.py"
        )
    if not model_path.is_file():
        raise ValueError(f"{model_path} not found")
    actual = file_sha256(model_path)
    if actual != expected:
        raise ValueError(
            f"model.onnx has SHA-256 {actual}, but labels.json says {expected}; "
            "evaluate the model.onnx that was exported with this labels.json"
        )
    recorded = training.get("model_sha256")
    if recorded is not None and recorded != expected:
        raise ValueError(
            f"metrics.json model_sha256 {recorded} differs from labels.json sha256 {expected}"
        )
    return actual


# ---------------------------------------------------------------------------
# Inference (onnxruntime, Pillow, numpy)
# ---------------------------------------------------------------------------


def preprocess(path: Path, spec: dict[str, Any]):
    """One image as a (1, 3, H, W) float32 array, exactly as labels.json describes.

    Matches train.py's evaluation transform: stretch to size_px x size_px with
    bilinear resampling, RGB, divide by 255, subtract the mean, divide by the std.
    """
    import numpy as np
    from PIL import Image

    size_px = int(spec["size_px"])
    with Image.open(path) as image:
        rgb = image.convert("RGB").resize((size_px, size_px), Image.Resampling.BILINEAR)
    array = np.asarray(rgb, dtype=np.float32) / 255.0
    array = (array - np.array(spec["mean"], dtype=np.float32)) / np.array(
        spec["std"], dtype=np.float32
    )
    return array.transpose(2, 0, 1)[None, ...].astype(np.float32)


def predict(
    model_path: Path, labels_spec: dict[str, Any], images: Sequence[Path]
) -> list[str]:
    """Predicted class for each image, one image at a time as the worker runs it."""
    import numpy as np
    import onnxruntime as ort

    classes = labels_spec["classes"]
    if list(classes) != list(LABELS):
        raise ValueError(
            f"labels.json class order {classes} differs from {list(LABELS)}"
        )
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    input_name = labels_spec["input"]["name"]
    output_name = labels_spec["output"]["name"]
    predictions = []
    for path in images:
        logits = session.run(
            [output_name], {input_name: preprocess(path, labels_spec["input"])}
        )[0]
        predictions.append(classes[int(np.argmax(logits[0]))])
    return predictions


# ---------------------------------------------------------------------------
# Model card
# ---------------------------------------------------------------------------


def _pct(value: float | None, missing: str = "n/a") -> str:
    """A fraction as a percentage; `missing` when it cannot be computed."""
    return missing if value is None else f"{value * 100:.1f}%"


def _names(names: Sequence[str]) -> str:
    quoted = [f"`{n}`" for n in names]
    return (
        " and ".join(quoted)
        if len(quoted) <= 2
        else ", ".join(quoted[:-1]) + " and " + quoted[-1]
    )


def render_card(evaluation: dict[str, Any] | None) -> str:
    """vision/MODEL_CARD.md. With no evaluation, every result reads "Not measured yet"."""
    e = evaluation or {}
    metrics = e.get("metrics")
    training = e.get("training", {})
    agreement = e.get("labeller_agreement")
    rscd_images = training.get("rscd_train_images") or 0
    pretrained = training.get("pretrained")
    if pretrained is True:
        weights = "fine-tuned from ImageNet weights with `vision/train.py` (OP-202)"
    elif pretrained is False:
        weights = "trained from random weights (no ImageNet pre-training) with `vision/train.py` (OP-202)"
    else:
        weights = "trained with `vision/train.py` (OP-202)"
    frames = "**511 Alberta camera frames** labelled with `vision/LABELS.md` (OP-201)."
    if e.get("labellers"):
        frames += (
            f" Labeller names as recorded in the label CSVs: {_names(e['labellers'])}."
        )
    if "train_images" in training:
        frames += (
            f" Training: {training['train_images']} frames from "
            f"{len(training.get('train_cameras', []))} cameras; validation: "
            f"{training.get('val_images', 'n/a')} frames from "
            f"{len(training.get('val_cameras', []))} cameras."
        )
    else:
        frames += (
            f" Training cameras: {len(training.get('train_cameras', [])) or 'n/a'}; "
            f"validation cameras: {len(training.get('val_cameras', [])) or 'n/a'}."
        )
    frames += (
        " Frames are kept only where the terms allow (decision D-3, docs/DATA_SOURCES.md), "
        "and never one in which a person can be identified."
    )
    lines = [
        "# Model Card: OpenPath Road-Condition Model",
        "",
        "Generated by `python -m vision.evaluate` (OP-203). Do not edit the numbers by hand.",
        "Accuracy, recall, precision and the confusion matrix are measured by that script on test",
        "cameras the model never saw in training or validation. Speed is measured by `vision/train.py`",
        "on the machine that trained the model, and labeller agreement comes from the label CSVs, so",
        "neither of those is a result on unseen cameras.",
        "",
        "## Model",
        "",
        f"- **Task:** classify one road camera frame as {', '.join(f'`{c}`' for c in LABELS)}.",
        (
            f"- **Architecture:** {training.get('arch', 'MobileNetV3-Small (default) or EfficientNet-B0')}, "
            f"{weights}."
        ),
        (
            f"- **Run:** `{e.get('run', 'none yet')}`, model SHA-256 "
            f"{'`' + e['model_sha256'] + '`' if e.get('model_sha256') else 'n/a'}, "
            f"seed {training.get('seed', 'n/a')}, PyTorch {training.get('torch', 'n/a')}, "
            f"evaluated {e.get('evaluated_at', 'not yet')}."
        ),
        "- **Weights:** published as a GitHub release asset (`model.onnx` and `labels.json`), never committed.",
        (
            "- **Use in OpenPath:** the vision worker (OP-204) reads 511 Alberta cameras. An `ice` reading, or "
            "`wet` while the weather watch is active, becomes **ice risk** at medium confidence on crossings "
            "within 50 m. `unusable` becomes `unknown`, never `dry`, and a reading changes only after two "
            "agreeing frames."
        ),
        "",
        "## Training data",
        "",
        f"- {frames}",
    ]
    if rscd_images:
        lines.append(
            f"- **RSCD** road-surface patches, {rscd_images} in training only: Tong Zhao, "
            '"RSCD: Road Surface Classification Dataset with Detailed Annotations for Driving '
            'Assistance", IEEE DataPort, 2022, doi:10.21227/446p-xr65, CC BY 4.0. RSCD images '
            "are close-up and forward-facing, unlike roadside cameras."
        )
    lines += ["", "## Labeller agreement", ""]
    if agreement and agreement["images"]:
        names = agreement.get("labellers") or []
        lines.append(
            f"{agreement['images']} images were labelled in two labelling passes"
            f"{', by ' + _names(names) if names else ''}: "
            f"{_pct(agreement['percent'])} agreement, Cohen's kappa "
            f"{'n/a' if agreement['kappa'] is None else format(agreement['kappa'], '.2f')}. "
            "Agreement shows how consistent the labels are, not that they are correct. "
            "Images the two passes disagreed on are left out of training and testing."
        )
    else:
        lines.append(
            "Not measured yet: no image has been labelled by two different labellers "
            "(OP-201 targets 10%)."
        )

    lines += ["", "## Results on unseen cameras", ""]
    if metrics:
        accuracy = metrics["accuracy"]
        verdict = (
            "meets"
            if accuracy is not None and accuracy >= ACCURACY_TARGET
            else "does not yet meet"
        )
        lines += [
            (
                f"Test set: {metrics['total']} images from {len(e.get('test_cameras', []))} cameras "
                f"({', '.join(f'`{c}`' for c in e.get('test_cameras', []))})."
            ),
            "",
            (
                f"**Accuracy: {_pct(accuracy)}**, which {verdict} the checkpoint target of "
                f"{ACCURACY_TARGET:.0%} (a target, to be confirmed by the team after this baseline)."
            ),
            "",
            "| Class | Test images | Recall | Precision |",
            "| --- | --- | --- | --- |",
        ]
        for label in LABELS:
            c = metrics["per_class"][label]
            recall = _pct(c["recall"], "n/a (no test images)")
            precision = _pct(c["precision"], "n/a (never predicted)")
            lines.append(f"| `{label}` | {c['support']} | {recall} | {precision} |")
        lines += [
            "",
            "Confusion matrix (rows: true class, columns: predicted class):",
            "",
            "| True \\ Predicted | " + " | ".join(f"`{c}`" for c in LABELS) + " |",
            "| --- |" + " --- |" * len(LABELS),
        ]
        for label, row in zip(LABELS, metrics["confusion"], strict=True):
            lines.append(f"| `{label}` | " + " | ".join(str(n) for n in row) + " |")
        missing = [
            label for label in LABELS if metrics["per_class"][label]["support"] == 0
        ]
        if missing:
            lines += [
                "",
                "No test images for "
                + ", ".join(f"`{m}`" for m in missing)
                + ", so those classes are untested on unseen cameras.",
            ]
    else:
        lines.append(
            "Not measured yet. The first real run needs labelled 511 images (OP-201); "
            "nothing is reported until then."
        )

    latency = training.get("cpu_latency_ms")
    lines += ["", "## Speed", ""]
    if latency:
        lines.append(
            f"Single-image CPU inference, one thread: median {latency['median']} ms, "
            f"p95 {latency['p95']} ms over {latency['runs']} runs on {latency['measured_on']} "
            f"(target under {latency['target_ms']} ms). Measured where training ran, not on Render."
        )
    else:
        lines.append("Not measured yet.")

    lines += [
        "",
        "## Known limits",
        "",
        (
            "- **Black ice is often invisible.** A thin clear layer can look like a dry or wet road. The model "
            "reports what the picture shows, and OpenPath combines `wet` with the weather watch for ice risk. "
            "It never claims to detect black ice."
        ),
        (
            "- **Night.** Low light, noise and point lights hide surface texture. Frames that show too little "
            "should be `unusable`, but some night frames will be misread."
        ),
        "- **Glare and low sun.** Glare on a wet or icy road can look like snow or like a dry, washed-out surface.",
        (
            "- **Weather on the lens.** Water, snow or salt on the camera blurs the view; these frames should "
            "be `unusable`."
        ),
        (
            "- **Coverage.** Trained and tested only on Alberta 511 cameras"
            f"{' (training also used RSCD patches)' if rscd_images else ''}. Accuracy on other "
            "cameras, angles or regions is unknown."
        ),
        (
            "- **Small test set.** Results come from a few cameras over a short period; one camera's quirks can "
            "move the numbers by several points."
        ),
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline and command line
# ---------------------------------------------------------------------------


def evaluate(
    *, run_dir: Path, images_dir: Path, label_csvs: Sequence[Path]
) -> dict[str, Any]:
    """Score the run's model on its test cameras. Writes and returns evaluation.json."""
    split = json.loads((run_dir / "split.json").read_text())
    labels_spec = json.loads((run_dir / "labels.json").read_text())
    training = json.loads((run_dir / "metrics.json").read_text())
    test_cameras = set(split["test_cameras"])
    if not test_cameras:
        raise ValueError("this run has no test cameras; train with at least 3 cameras")
    leaked = sorted(test_cameras & set(split["train_cameras"] + split["val_cameras"]))
    if leaked:
        raise ValueError(f"test cameras also used in training: {', '.join(leaked)}")
    try:
        counts = split["counts"]
        rscd_train_images = sum(counts["train_from_rscd"].values())
        train_images = sum(counts["train"].values()) - rscd_train_images
        val_images = sum(counts["val"].values())
    except (KeyError, AttributeError, TypeError):
        raise ValueError(
            "split.json has no per-class counts; it was not written by vision/train.py"
        ) from None
    model_sha256 = check_model_sha256(run_dir / "model.onnx", labels_spec, training)

    samples, _ = load_labelled(images_dir, label_csvs)
    test = [s for s in samples if s.camera_id in test_cameras]
    if not test:
        raise ValueError(
            "no labelled images found for the test cameras; check --images and --labels"
        )

    predicted = predict(run_dir / "model.onnx", labels_spec, [s.path for s in test])
    metrics = score([s.label for s in test], predicted)
    agreement = labeller_agreement(label_csvs)
    evaluation = {
        "run": run_dir.name,
        "model_sha256": model_sha256,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "test_cameras": sorted(test_cameras),
        "labellers": labeller_names(label_csvs),
        "metrics": {
            "total": metrics.total,
            "correct": metrics.correct,
            "accuracy": metrics.accuracy,
            "per_class": {
                label: {
                    "support": metrics.support(label),
                    "recall": metrics.recall(label),
                    "precision": metrics.precision(label),
                }
                for label in LABELS
            },
            "confusion": metrics.confusion,
            "labels": list(LABELS),
        },
        "labeller_agreement": {
            "images": agreement.images,
            "agreed": agreement.agreed,
            "percent": agreement.percent,
            "kappa": agreement.kappa,
            "labellers": list(agreement.labellers),
        },
        "training": {
            "arch": training["config"]["arch"],
            "seed": training["config"]["seed"],
            "pretrained": training["config"].get("pretrained"),
            "torch": training.get("torch"),
            "best_val_accuracy": training.get("best_val_accuracy"),
            "cpu_latency_ms": training.get("cpu_latency_ms"),
            "train_cameras": split["train_cameras"],
            "val_cameras": split["val_cameras"],
            "train_images": train_images,
            "val_images": val_images,
            "rscd_train_images": rscd_train_images,
        },
    }
    (run_dir / "evaluation.json").write_text(json.dumps(evaluation, indent=2))
    log.info(
        "EVALUATED %s accuracy=%s on %d images from %d unseen cameras sha256=%s",
        run_dir.name,
        _pct(metrics.accuracy),
        metrics.total,
        len(test_cameras),
        model_sha256,
    )
    return evaluation


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the model and write MODEL_CARD.md."
    )
    parser.add_argument(
        "--run", type=Path, required=True, help="A vision/train.py output folder."
    )
    parser.add_argument("--images", type=Path, default=Path("vision/data/raw"))
    parser.add_argument("--labels", type=Path, nargs="+", required=True)
    parser.add_argument("--card", type=Path, default=DEFAULT_CARD)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        print(
            "Evaluation needs onnxruntime: pip install -r scripts/requirements-train.txt",
            file=sys.stderr,
        )
        return 2
    evaluation = evaluate(
        run_dir=args.run, images_dir=args.images, label_csvs=args.labels
    )
    args.card.write_text(render_card(evaluation))
    print(f"Wrote {args.card} and {args.run / 'evaluation.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
