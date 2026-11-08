"""Train the road-condition model (OP-202).

Fine-tunes a small ImageNet-pretrained network (MobileNetV3-Small by default, or
EfficientNet-B0) on the labelled 511 camera images from OP-201, plus RSCD road
surface patches when given, then exports it to ONNX and measures CPU latency.

Usage (on a laptop or Colab, never on Render):
    pip install -r scripts/requirements-train.txt
    python -m vision.train --images vision/data/raw --labels vision/data/labels_*.csv \
        --rscd vision/data/rscd --out vision/data/runs/v1

Honest numbers (AGENTS.md):
- The data is split by camera. Test cameras never appear in training or
  validation, and RSCD (other cameras, other roads) only ever goes into training.
- This script never looks at the test set. OP-203 evaluates on it, using the
  camera lists saved in split.json.
- Everything in metrics.json is measured in this run; nothing is estimated.

Reproducible: one --seed drives the camera split, RSCD sampling, weight init of
the new head, data order and augmentation.

Only the data preparation below runs without PyTorch, so it is importable and
tested in CI; torch, torchvision and onnxruntime are imported inside the
functions that need them (scripts/requirements-train.txt).
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import random
import statistics
import sys
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vision.tools.label import IMAGE_SUFFIXES, LABELS, UNKNOWN_CAMERA, read_labels

log = logging.getLogger("vision.train")

CLASS_TO_INDEX: dict[str, int] = {label: i for i, label in enumerate(LABELS)}
INPUT_SIZE_PX = 224
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
ARCHITECTURES = ("mobilenet_v3_small", "efficientnet_b0")
RSCD_SOURCE = "rscd"

# RSCD names each image class "<friction>_<material>_<unevenness>", or just the
# friction state for snow and ice. Only the friction state matters here.
# PROVISIONAL until the dataset is downloaded and checked (docs/DATA_SOURCES.md):
# confirm these prefixes against the real folder names before the first real run.
RSCD_FRICTION_TO_CLASS: dict[str, str] = {
    "dry": "dry",
    "wet": "wet",
    "water": "wet",
    "fresh_snow": "snow_covered",
    "melted_snow": "snow_partial",
    "ice": "ice",
}


# ---------------------------------------------------------------------------
# Data preparation (no PyTorch needed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    path: Path
    label: str
    camera_id: str  # "rscd" for RSCD patches
    source: str  # "511" or "rscd"


def load_labelled(
    images_dir: Path, label_csvs: Sequence[Path]
) -> tuple[list[Sample], Counter]:
    """Read one or more OP-115 label CSVs into samples.

    The same image labelled by two people (OP-201's 10% agreement set) is kept once
    when both agree and dropped when they disagree: an ambiguous label teaches the
    model nothing. Images from camera "unknown" are dropped too, because the split
    could not keep them out of the test cameras. Missing files are dropped.
    """
    labels_by_image: dict[str, set[str]] = {}
    camera_by_image: dict[str, str] = {}
    for csv_path in label_csvs:
        for image, row in read_labels(csv_path).items():
            if row.label not in CLASS_TO_INDEX:
                raise ValueError(f"{csv_path}: unknown label {row.label!r} for {image}")
            labels_by_image.setdefault(image, set()).add(row.label)
            camera_by_image[image] = row.camera_id

    dropped: Counter = Counter()
    samples: list[Sample] = []
    for image in sorted(labels_by_image):
        labels = labels_by_image[image]
        camera_id = camera_by_image[image]
        path = images_dir / image
        if len(labels) > 1:
            dropped["labellers_disagree"] += 1
        elif camera_id == UNKNOWN_CAMERA:
            dropped["unknown_camera"] += 1
        elif not path.is_file():
            dropped["file_missing"] += 1
        else:
            samples.append(Sample(path, next(iter(labels)), camera_id, "511"))
    return samples, dropped


def rscd_class(name: str) -> str | None:
    """Our class for an RSCD class name such as "wet_asphalt_slight", or None."""
    name = name.strip().lower().replace("-", "_").replace(" ", "_")
    for prefix in sorted(RSCD_FRICTION_TO_CLASS, key=len, reverse=True):
        if name == prefix or name.startswith(prefix + "_"):
            return RSCD_FRICTION_TO_CLASS[prefix]
    return None


def load_rscd(
    rscd_dir: Path, per_class_limit: int, seed: int
) -> tuple[list[Sample], Counter]:
    """RSCD images labelled by their folder name, sampled to at most per_class_limit each.

    RSCD has about a million patches; a balanced sample keeps training fast and
    stops it from drowning out the 511 images. Unmapped folders are counted, not used.
    """
    by_class: dict[str, list[Path]] = {}
    unmapped: Counter = Counter()
    for path in sorted(rscd_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        label = rscd_class(path.parent.name)
        if label is None:
            unmapped[path.parent.name] += 1
            continue
        by_class.setdefault(label, []).append(path)

    rng = random.Random(seed)
    samples: list[Sample] = []
    for label in sorted(by_class):
        paths = by_class[label]
        if len(paths) > per_class_limit:
            paths = sorted(rng.sample(paths, per_class_limit))
        samples.extend(Sample(p, label, RSCD_SOURCE, RSCD_SOURCE) for p in paths)
    return samples, unmapped


@dataclass
class Split:
    train_cameras: list[str]
    val_cameras: list[str]
    test_cameras: list[str]
    train: list[Sample] = field(repr=False)
    val: list[Sample] = field(repr=False)
    test: list[Sample] = field(repr=False)


def split_by_camera(
    samples: Sequence[Sample],
    *,
    seed: int,
    test_fraction: float = 0.2,
    val_fraction: float = 0.1,
    test_cameras: Iterable[str] | None = None,
) -> Split:
    """Assign whole cameras to train, validation and test; never split a camera.

    Test cameras come from `test_cameras` when given (so the team can fix them),
    otherwise from a seeded shuffle. RSCD samples always go to training.
    """
    cameras = sorted({s.camera_id for s in samples if s.source != RSCD_SOURCE})
    if test_cameras is not None:
        test = sorted(set(test_cameras))
        missing = sorted(set(test) - set(cameras))
        if missing:
            raise ValueError(
                f"test cameras with no labelled images: {', '.join(missing)}"
            )
        rest = [c for c in cameras if c not in set(test)]
        rng = random.Random(seed)
        rng.shuffle(rest)
    else:
        rest = list(cameras)
        rng = random.Random(seed)
        rng.shuffle(rest)
        n_test = max(1, round(len(rest) * test_fraction)) if len(rest) >= 3 else 0
        test, rest = sorted(rest[:n_test]), rest[n_test:]
    n_val = max(1, round(len(cameras) * val_fraction)) if len(rest) >= 2 else 0
    val, train = sorted(rest[:n_val]), sorted(rest[n_val:])

    test_set, val_set = set(test), set(val)
    split = Split(train, val, test, [], [], [])
    for s in samples:
        if s.source == RSCD_SOURCE:
            split.train.append(s)
        elif s.camera_id in test_set:
            split.test.append(s)
        elif s.camera_id in val_set:
            split.val.append(s)
        else:
            split.train.append(s)
    return split


def class_counts(samples: Iterable[Sample]) -> dict[str, int]:
    counts = Counter(s.label for s in samples)
    return {label: counts.get(label, 0) for label in LABELS}


def describe_split(split: Split) -> dict[str, Any]:
    """The split as saved in split.json: camera lists plus per-class counts."""
    return {
        "test_cameras": split.test_cameras,
        "val_cameras": split.val_cameras,
        "train_cameras": split.train_cameras,
        "counts": {
            "train": class_counts(split.train),
            "train_from_rscd": class_counts(
                s for s in split.train if s.source == RSCD_SOURCE
            ),
            "val": class_counts(split.val),
            "test": class_counts(split.test),
        },
        "test_images": sorted(s.path.as_posix() for s in split.test),
    }


def class_weights(samples: Sequence[Sample]) -> list[float]:
    """Inverse-frequency weights (mean 1 over present classes); 0 for absent classes."""
    counts = class_counts(samples)
    present = [c for c in counts.values() if c]
    if not present:
        raise ValueError("no training samples")
    total, n_present = sum(present), len(present)
    return [
        (total / (n_present * counts[label])) if counts[label] else 0.0
        for label in LABELS
    ]


# ---------------------------------------------------------------------------
# Training, export and measurement (needs scripts/requirements-train.txt)
# ---------------------------------------------------------------------------


@dataclass
class TrainConfig:
    arch: str = "mobilenet_v3_small"
    pretrained: bool = True
    epochs: int = 10
    batch_size: int = 32
    lr: float = 3e-4
    weight_decay: float = 1e-4
    label_smoothing: float = 0.05
    seed: int = 202
    num_workers: int = 2
    input_size_px: int = INPUT_SIZE_PX
    latency_runs: int = 50


def seed_everything(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    torch.backends.cudnn.benchmark = False


def build_model(arch: str, num_classes: int, pretrained: bool):
    """An ImageNet-pretrained backbone with a new classification layer."""
    from torch import nn
    from torchvision import models

    if arch == "mobilenet_v3_small":
        weights = models.MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
        model = models.mobilenet_v3_small(weights=weights)
        model.classifier[3] = nn.Linear(model.classifier[3].in_features, num_classes)
    elif arch == "efficientnet_b0":
        weights = models.EfficientNet_B0_Weights.DEFAULT if pretrained else None
        model = models.efficientnet_b0(weights=weights)
        model.classifier[1] = nn.Linear(model.classifier[1].in_features, num_classes)
    else:
        raise ValueError(
            f"unknown architecture {arch!r}; use one of {', '.join(ARCHITECTURES)}"
        )
    return model


def _transforms(size_px: int, train: bool):
    from torchvision import transforms as T

    normalize = T.Normalize(IMAGENET_MEAN, IMAGENET_STD)
    if train:
        return T.Compose(
            [
                T.RandomResizedCrop(size_px, scale=(0.6, 1.0), ratio=(0.75, 1.33)),
                T.RandomHorizontalFlip(),
                T.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.2),
                T.ToTensor(),
                normalize,
            ]
        )
    # Must match the preprocessing the worker (OP-204) applies; saved in labels.json.
    return T.Compose([T.Resize((size_px, size_px)), T.ToTensor(), normalize])


def _dataset(samples: Sequence[Sample], transform):
    from PIL import Image
    from torch.utils.data import Dataset

    class _Images(Dataset):
        def __len__(self) -> int:
            return len(samples)

        def __getitem__(self, index: int):
            sample = samples[index]
            with Image.open(sample.path) as image:
                return transform(image.convert("RGB")), CLASS_TO_INDEX[sample.label]

    return _Images()


def _loader(samples, transform, config: TrainConfig, *, shuffle: bool):
    import torch
    from torch.utils.data import DataLoader

    generator = torch.Generator().manual_seed(config.seed)

    def worker_init(worker_id: int) -> None:
        import numpy as np

        worker_seed = (config.seed + worker_id) % 2**32
        random.seed(worker_seed)
        np.random.seed(worker_seed)

    return DataLoader(
        _dataset(samples, transform),
        batch_size=config.batch_size,
        shuffle=shuffle,
        num_workers=config.num_workers,
        generator=generator,
        worker_init_fn=worker_init,
    )


def _accuracy(model, loader, device) -> float | None:
    import torch

    if len(loader.dataset) == 0:
        return None
    correct = total = 0
    model.eval()
    with torch.no_grad():
        for images, targets in loader:
            predictions = model(images.to(device)).argmax(dim=1).cpu()
            correct += int((predictions == targets).sum())
            total += len(targets)
    return correct / total


def train_model(split: Split, config: TrainConfig, out_dir: Path) -> dict[str, Any]:
    """Fine-tune on split.train, keep the epoch with the best validation accuracy.

    Validation uses only validation cameras; the test cameras are never touched.
    Returns the training history for metrics.json and writes checkpoint.pt.
    """
    import torch
    from torch import nn

    seed_everything(config.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model(config.arch, len(LABELS), config.pretrained).to(device)

    train_loader = _loader(
        split.train, _transforms(config.input_size_px, True), config, shuffle=True
    )
    val_loader = _loader(
        split.val, _transforms(config.input_size_px, False), config, shuffle=False
    )
    weights = torch.tensor(
        class_weights(split.train), dtype=torch.float32, device=device
    )
    loss_fn = nn.CrossEntropyLoss(
        weight=weights, label_smoothing=config.label_smoothing
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.lr, weight_decay=config.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=config.epochs
    )

    history: list[dict[str, Any]] = []
    best_val, best_epoch = -1.0, 0
    checkpoint = out_dir / "checkpoint.pt"
    for epoch in range(1, config.epochs + 1):
        model.train()
        loss_sum, seen = 0.0, 0
        for images, targets in train_loader:
            images, targets = images.to(device), targets.to(device)
            optimizer.zero_grad()
            loss = loss_fn(model(images), targets)
            loss.backward()
            optimizer.step()
            loss_sum += loss.item() * len(targets)
            seen += len(targets)
        scheduler.step()
        val_accuracy = _accuracy(model, val_loader, device)
        history.append(
            {
                "epoch": epoch,
                "train_loss": loss_sum / max(seen, 1),
                "val_accuracy": val_accuracy,
            }
        )
        log.info(
            "EPOCH %d loss=%.4f val_accuracy=%s",
            epoch,
            loss_sum / max(seen, 1),
            "n/a" if val_accuracy is None else f"{val_accuracy:.3f}",
        )
        # No validation cameras: keep the last epoch instead of the best one.
        score = -float(epoch) if val_accuracy is None else val_accuracy
        if val_accuracy is None or score > best_val:
            best_val, best_epoch = score, epoch
            torch.save(model.state_dict(), checkpoint)

    model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
    best = history[best_epoch - 1]
    return {
        "model": model.cpu().eval(),
        "device": str(device),
        "history": history,
        "best_epoch": best_epoch,
        "best_val_accuracy": best["val_accuracy"],
    }


def export_onnx(model, path: Path, size_px: int) -> None:
    """Export with a dynamic batch dimension: input "image" (N,3,H,W), output "logits" (N,6).

    Uses PyTorch's current exporter (torch.export). The example batch has 2 images,
    because an example of 1 lets the exporter fix the batch size at 1. The legacy
    TorchScript exporter is only a fallback for older PyTorch. check_onnx() then
    verifies the file against PyTorch on a batch of 4.
    """
    import torch

    names = {"input_names": ["image"], "output_names": ["logits"]}
    try:
        batch = torch.export.Dim("batch", min=1, max=64)
        torch.onnx.export(
            model,
            (torch.zeros(2, 3, size_px, size_px),),
            str(path),
            dynamo=True,
            dynamic_shapes=({0: batch},),
            external_data=False,  # one self-contained file for the release asset
            verbose=False,
            **names,
        )
    except Exception as exc:  # noqa: BLE001 - any exporter failure falls back
        log.warning("New ONNX exporter failed (%s); using the legacy exporter", exc)
        torch.onnx.export(
            model,
            torch.zeros(1, 3, size_px, size_px),
            str(path),
            dynamo=False,
            dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
            opset_version=17,
            **names,
        )


def check_onnx(
    model, path: Path, size_px: int, latency_runs: int, seed: int
) -> dict[str, Any]:
    """Compare ONNX with PyTorch on a random batch and measure single-image CPU latency."""
    import numpy as np
    import onnxruntime as ort
    import torch

    batch = torch.from_numpy(
        np.random.default_rng(seed)
        .standard_normal((4, 3, size_px, size_px))
        .astype(np.float32)
    )
    with torch.no_grad():
        expected = model(batch).numpy()
    options = ort.SessionOptions()
    options.intra_op_num_threads = (
        1  # the Render worker is small; measure the worst case
    )
    session = ort.InferenceSession(
        str(path), options, providers=["CPUExecutionProvider"]
    )
    actual = session.run(["logits"], {"image": batch.numpy()})[0]
    max_abs_diff = float(np.max(np.abs(actual - expected)))
    if max_abs_diff > 1e-3:
        raise RuntimeError(f"ONNX output differs from PyTorch by {max_abs_diff:.2e}")

    one = batch[:1].numpy()
    for _ in range(5):  # warm-up runs are not timed
        session.run(["logits"], {"image": one})
    timings_ms = []
    for _ in range(latency_runs):
        start = time.perf_counter()
        session.run(["logits"], {"image": one})
        timings_ms.append((time.perf_counter() - start) * 1000)
    timings_ms.sort()
    return {
        "onnx_max_abs_diff_vs_torch": max_abs_diff,
        "cpu_latency_ms": {
            "median": round(statistics.median(timings_ms), 2),
            "p95": round(timings_ms[max(0, int(len(timings_ms) * 0.95) - 1)], 2),
            "runs": latency_runs,
            "threads": 1,
            "target_ms": 200,
            "measured_on": " ".join(
                dict.fromkeys(
                    p
                    for p in (
                        platform.system(),
                        platform.machine(),
                        platform.processor(),
                    )
                    if p
                )
            ),
        },
        "onnx_size_mb": round(path.stat().st_size / 1e6, 2),
    }


def run(
    *,
    images_dir: Path,
    label_csvs: Sequence[Path],
    out_dir: Path,
    config: TrainConfig,
    rscd_dir: Path | None = None,
    rscd_per_class: int = 1000,
    test_cameras: Sequence[str] | None = None,
) -> dict[str, Any]:
    """The whole pipeline. Writes split.json, labels.json, model.onnx and metrics.json."""
    import torch

    out_dir.mkdir(parents=True, exist_ok=True)
    samples, dropped = (
        load_labelled(images_dir, label_csvs) if label_csvs else ([], Counter())
    )
    unmapped: Counter = Counter()
    if rscd_dir is not None:
        rscd_samples, unmapped = load_rscd(rscd_dir, rscd_per_class, config.seed)
        samples += rscd_samples
    if dropped:
        log.warning("Dropped labelled images: %s", dict(dropped))
    if unmapped:
        log.warning("RSCD folders with no class mapping (not used): %s", dict(unmapped))

    split = split_by_camera(samples, seed=config.seed, test_cameras=test_cameras)
    if not split.train:
        raise ValueError("no training images; check --images, --labels and --rscd")
    (out_dir / "split.json").write_text(json.dumps(describe_split(split), indent=2))
    log.info(
        "SPLIT train=%d (cameras=%d, rscd=%d) val=%d (cameras=%d) test=%d (cameras=%d, not used here)",
        len(split.train),
        len(split.train_cameras),
        sum(s.source == RSCD_SOURCE for s in split.train),
        len(split.val),
        len(split.val_cameras),
        len(split.test),
        len(split.test_cameras),
    )

    result = train_model(split, config, out_dir)
    model = result.pop("model")
    onnx_path = out_dir / "model.onnx"
    export_onnx(model, onnx_path, config.input_size_px)
    checks = check_onnx(
        model, onnx_path, config.input_size_px, config.latency_runs, config.seed
    )

    # What the worker (OP-204) needs to use the model exactly as it was trained.
    (out_dir / "labels.json").write_text(
        json.dumps(
            {
                "classes": list(LABELS),
                "input": {
                    "name": "image",
                    "layout": "NCHW",
                    "size_px": config.input_size_px,
                    "resize": "stretch to size_px x size_px",
                    "color": "RGB",
                    "scale": "pixel / 255",
                    "mean": list(IMAGENET_MEAN),
                    "std": list(IMAGENET_STD),
                },
                "output": {"name": "logits", "softmax": "apply in the worker"},
            },
            indent=2,
        )
    )

    metrics = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": asdict(config),
        "torch": torch.__version__,
        "device": result["device"],
        "dropped_labelled_images": dict(dropped),
        "rscd_unmapped_folders": dict(unmapped),
        "best_epoch": result["best_epoch"],
        "best_val_accuracy": result["best_val_accuracy"],
        "history": result["history"],
        **checks,
        "note": "Validation accuracy only. Test-camera results come from OP-203.",
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    log.info(
        "DONE best_epoch=%d val_accuracy=%s cpu_median_ms=%s -> %s",
        result["best_epoch"],
        result["best_val_accuracy"],
        checks["cpu_latency_ms"]["median"],
        out_dir,
    )
    return metrics


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train the OpenPath road-condition model."
    )
    parser.add_argument("--images", type=Path, default=Path("vision/data/raw"))
    parser.add_argument(
        "--labels",
        type=Path,
        nargs="*",
        default=[],
        help="One or more OP-115 label CSVs.",
    )
    parser.add_argument(
        "--rscd", type=Path, help="RSCD root folder (one folder per class)."
    )
    parser.add_argument("--rscd-per-class", type=int, default=1000)
    parser.add_argument(
        "--test-cameras",
        nargs="*",
        help="Fix the test cameras (otherwise a seeded 20%% of cameras).",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arch", choices=ARCHITECTURES, default=TrainConfig.arch)
    parser.add_argument("--epochs", type=int, default=TrainConfig.epochs)
    parser.add_argument("--batch-size", type=int, default=TrainConfig.batch_size)
    parser.add_argument("--lr", type=float, default=TrainConfig.lr)
    parser.add_argument("--seed", type=int, default=TrainConfig.seed)
    parser.add_argument("--workers", type=int, default=TrainConfig.num_workers)
    parser.add_argument(
        "--no-pretrained",
        action="store_true",
        help="Start from random weights (tests only).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.labels and args.rscd is None:
        parser.error("give --labels, --rscd or both")
    try:
        import torch  # noqa: F401
    except ImportError:
        print(
            "Training needs PyTorch: pip install -r scripts/requirements-train.txt",
            file=sys.stderr,
        )
        return 2

    config = TrainConfig(
        arch=args.arch,
        pretrained=not args.no_pretrained,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        seed=args.seed,
        num_workers=args.workers,
    )
    run(
        images_dir=args.images,
        label_csvs=args.labels,
        out_dir=args.out,
        config=config,
        rscd_dir=args.rscd,
        rscd_per_class=args.rscd_per_class,
        test_cameras=args.test_cameras,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
