"""Purpose: prepare and audit OP-201's locally labelled camera dataset.

Read the existing label tool's CSVs and approved local images. Freeze camera
splits, prepare a blind 10% second-label sample, and measure real agreement.
Only metadata is written; this tool never downloads, labels, or copies images.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from PIL import Image

from vision.tools.label import CSV_COLUMNS, LABELS, LabelRow, parse_image_name


def image_key(value: str) -> str:
    """Keep label paths relative to the approved image folder."""
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        not value.strip()
        or path.is_absolute()
        or PureWindowsPath(value).drive
        or ".." in path.parts
        or str(path) == "."
    ):
        raise ValueError("Image paths must stay inside the image folder")
    return str(path)


def utc_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Label timestamps must include a timezone")
    return result.astimezone(UTC)


def load_labels(path: Path) -> dict[str, LabelRow]:
    """Validate every row without silently dropping duplicates or unknown cameras."""
    rows = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not set(CSV_COLUMNS) <= set(reader.fieldnames):
            raise ValueError(f"{path}: expected the OP-115 label CSV columns")
        for line, record in enumerate(reader, start=2):
            values = {
                column: (record.get(column) or "").strip() for column in CSV_COLUMNS
            }
            key = image_key(values["image"])
            values["image"] = key
            if key in rows:
                raise ValueError(f"{path}:{line}: duplicate image {key}")
            parsed = parse_image_name(PurePosixPath(key).name)
            if parsed is None or values["camera_id"].casefold() == "unknown":
                raise ValueError(
                    f"{path}:{line}: use a known camera ID and timestamped filename"
                )
            if parsed[0] != values["camera_id"] or parsed[1] != utc_time(
                values["captured_at"]
            ):
                raise ValueError(
                    f"{path}:{line}: filename and camera/capture metadata disagree"
                )
            if values["label"] not in LABELS or not values["labeller"]:
                raise ValueError(f"{path}:{line}: invalid label or missing labeller")
            utc_time(values["labelled_at"])
            rows[key] = LabelRow(**values)
    return rows


def rank(value: str, seed: int) -> str:
    """Stable ordering that does not change with Python's randomized hash seed."""
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def plan_dataset(
    rows: dict[str, LabelRow], previous: dict[str, Any] | None, seed: int
) -> dict[str, Any]:
    """Preserve prior assignments so adding data never moves a training camera to test."""
    if previous is not None and (
        not isinstance(previous, dict)
        or previous.get("version") != 1
        or previous.get("seed") != seed
    ):
        raise ValueError(
            "Dataset registry version/seed changed; use a new output folder"
        )
    if previous and (
        not isinstance(previous.get("camera_splits"), dict)
        or not isinstance(previous.get("double_label_images"), list)
        or not all(isinstance(key, str) for key in previous["double_label_images"])
    ):
        raise ValueError("Invalid dataset registry")
    cameras = dict(previous.get("camera_splits", {})) if previous else {}
    if any(
        not isinstance(key, str) or value not in ("train", "test")
        for key, value in cameras.items()
    ):
        raise ValueError("Invalid camera split registry")
    current = {row.camera_id for row in rows.values()}
    new = sorted(current - cameras.keys(), key=lambda camera: rank(camera, seed))
    if not cameras:
        test_count = (
            min(len(new) - 1, max(1, math.ceil(len(new) * 0.2))) if len(new) >= 2 else 0
        )
        cameras.update(
            {
                camera: "test" if i < test_count else "train"
                for i, camera in enumerate(new)
            }
        )
    else:
        cameras.update(
            {
                camera: "test" if int(rank(camera, seed), 16) % 100 < 20 else "train"
                for camera in new
            }
        )
        # Only newly added cameras can fill a missing split; old assignments stay fixed.
        for split in ("train", "test"):
            if (
                len(current) >= 2
                and new
                and not any(cameras[camera] == split for camera in current)
            ):
                cameras[new.pop(0)] = split
    selected = (
        set(previous.get("double_label_images", [])) & rows.keys()
        if previous
        else set()
    )
    target = math.ceil(len(rows) / 10)
    remaining = sorted(rows.keys() - selected, key=lambda image: rank(image, seed))
    selected.update(remaining[: max(0, target - len(selected))])
    return {
        "version": 1,
        "seed": seed,
        "camera_splits": cameras,
        "double_label_images": sorted(selected),
    }


def audit_images(rows: dict[str, LabelRow], root: Path) -> dict[str, list[str]]:
    """Require real readable files; group exact decoded duplicates across file names."""
    root = root.resolve()
    hashes = defaultdict(list)
    for key in sorted(rows):
        path = (root / key).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Missing image or image outside approved folder: {key}")
        try:
            with Image.open(path) as original:
                image = original.convert("RGB")
                digest = hashlib.sha256(
                    str(image.size).encode() + image.tobytes()
                ).hexdigest()
        except (OSError, ValueError) as exc:
            raise ValueError(f"Unreadable image: {key}") from exc
        hashes[digest].append(key)
    return hashes


def build_report(
    rows: dict[str, LabelRow],
    secondary: dict[str, LabelRow],
    plan: dict[str, Any],
    hashes: dict[str, list[str]],
) -> dict[str, Any]:
    """Count evidence honestly; missing second labels never become perfect agreement."""
    if secondary.keys() - rows.keys():
        raise ValueError("Second labels reference images absent from the primary CSV")
    cameras = plan["camera_splits"]
    sample = plan["double_label_images"]
    paired = []
    for key in sample:
        if key in secondary:
            if rows[key].labeller.casefold() == secondary[key].labeller.casefold():
                raise ValueError("Agreement needs a different second labeller")
            paired.append(key)
    matrix = {label: {other: 0 for other in LABELS} for label in LABELS}
    disagreements = []
    for key in paired:
        first, second = rows[key].label, secondary[key].label
        matrix[first][second] += 1
        if first != second:
            disagreements.append({"image": key, "primary": first, "secondary": second})
    classes = Counter(row.label for row in rows.values())
    split_cameras = {
        split: sorted(
            {row.camera_id for row in rows.values() if cameras[row.camera_id] == split}
        )
        for split in ("train", "test")
    }
    split_class_counts = {}
    for split in ("train", "test"):
        counts = Counter(
            row.label for row in rows.values() if cameras[row.camera_id] == split
        )
        split_class_counts[split] = {label: counts[label] for label in LABELS}
    duplicates = [images for images in hashes.values() if len(images) > 1]
    cross_split = [
        images
        for images in duplicates
        if len({cameras[rows[key].camera_id] for key in images}) > 1
    ]
    checks = {
        "at_least_1500_unique_frames": len(hashes) >= 1500,
        "at_least_20_cameras": len(split_cameras["train"]) + len(split_cameras["test"])
        >= 20,
        "every_class_present": all(classes[label] > 0 for label in LABELS),
        "nonempty_train_and_test": all(split_cameras.values()),
        "camera_disjoint_split": not set(split_cameras["train"])
        & set(split_cameras["test"]),
        "no_identical_frames_across_splits": not cross_split,
        "double_label_sample_complete": bool(sample) and len(paired) == len(sample),
    }
    return {
        "labelled_images": len(rows),
        "unique_frames": len(hashes),
        "camera_count": sum(len(value) for value in split_cameras.values()),
        "class_counts": {label: classes[label] for label in LABELS},
        "split_cameras": split_cameras,
        "split_class_counts": split_class_counts,
        "duplicate_image_groups": duplicates,
        "cross_split_duplicate_groups": cross_split,
        "double_label_target": math.ceil(len(rows) / 10),
        "double_label_selected": len(sample),
        "double_label_completed": len(paired),
        "agreement_percent": round(
            100 * (len(paired) - len(disagreements)) / len(paired), 2
        )
        if paired
        else None,
        "confusion_matrix": matrix,
        "disagreements": disagreements,
        "acceptance_checks": checks,
        "measured_targets_met": all(checks.values()),
        "human_checks_remaining": [
            "source retention permission",
            "privacy review",
            "day/night coverage",
            "label quality and disagreement review",
        ],
    }


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def prepare(
    labels: Path,
    images: Path,
    out: Path,
    second_labels: Path | None = None,
    seed: int = 2026,
) -> dict[str, Any]:
    """Validate first, then write local manifests, a blind sample, and audit results."""
    primary = load_labels(labels)
    secondary = load_labels(second_labels) if second_labels else {}
    registry = out / "registry.json"
    previous = json.loads(registry.read_text()) if registry.exists() else None
    plan = plan_dataset(primary, previous, seed)
    hashes = audit_images(primary, images)
    report = build_report(primary, secondary, plan, hashes)
    out.mkdir(parents=True, exist_ok=True)
    write_json(registry, plan)
    for split in ("train", "test"):
        write_csv(
            out / f"{split}.csv",
            list(CSV_COLUMNS),
            [
                row.as_dict()
                for key, row in sorted(primary.items())
                if plan["camera_splits"][row.camera_id] == split
            ],
        )
    # Deliberately omit primary labels and labeller names from the blind second-pass queue.
    blind_columns = ["image", "camera_id", "captured_at"]
    write_csv(
        out / "double_label.csv",
        blind_columns,
        [
            {column: getattr(primary[key], column) for column in blind_columns}
            for key in plan["double_label_images"]
        ],
    )
    write_json(out / "report.json", report)
    # OP-202 must receive these frozen IDs explicitly; its default split differs.
    temporary = out / "test_cameras.txt.tmp"
    temporary.write_text(
        "".join(f"{camera}\n" for camera in report["split_cameras"]["test"]),
        encoding="utf-8",
    )
    os.replace(temporary, out / "test_cameras.txt")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("vision/data/dataset"))
    parser.add_argument("--second-labels", type=Path)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)
    try:
        report = prepare(
            args.labels, args.images, args.out, args.second_labels, args.seed
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Dataset audit failed: {exc}\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
