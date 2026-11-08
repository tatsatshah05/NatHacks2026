"""Tests for vision/train.py (OP-202).

The data-preparation tests need no PyTorch and run in CI. The end-to-end test
trains a tiny model on synthetic images and checks the ONNX export; it runs only
where scripts/requirements-train.txt is installed and is skipped elsewhere.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from vision.tools.label import CSV_COLUMNS, LABELS
from vision.train import (
    RSCD_SOURCE,
    Sample,
    class_counts,
    class_weights,
    describe_split,
    load_labelled,
    load_rscd,
    rscd_class,
    split_by_camera,
)


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "captured_at": "2026-11-07T15:00:00Z",
                    "labeller": "test",
                    "labelled_at": "2026-11-07T16:00:00Z",
                    **row,
                }
            )
    return path


def touch_image(root: Path, name: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not decoded in these tests")


def samples_for(cameras: int, per_camera: int = 6) -> list[Sample]:
    return [
        Sample(Path(f"cam{c}_{i}.jpg"), LABELS[i % len(LABELS)], f"cam{c}", "511")
        for c in range(cameras)
        for i in range(per_camera)
    ]


# -- labelled CSVs ------------------------------------------------------------


def test_load_labelled_reads_rows_and_drops_unusable_inputs(tmp_path: Path):
    images = tmp_path / "raw"
    for name in (
        "a_20261107T150000Z.jpg",
        "b_20261107T150000Z.jpg",
        "c_20261107T150000Z.jpg",
    ):
        touch_image(images, name)
    touch_image(images, "photo.jpg")
    one = write_csv(
        tmp_path / "labels_ipsa.csv",
        [
            {"image": "a_20261107T150000Z.jpg", "camera_id": "a", "label": "dry"},
            {"image": "b_20261107T150000Z.jpg", "camera_id": "b", "label": "ice"},
            {"image": "c_20261107T150000Z.jpg", "camera_id": "c", "label": "wet"},
            {"image": "photo.jpg", "camera_id": "unknown", "label": "dry"},
            {"image": "gone_20261107T150000Z.jpg", "camera_id": "gone", "label": "dry"},
        ],
    )
    two = write_csv(
        tmp_path / "labels_estella.csv",
        [
            {
                "image": "a_20261107T150000Z.jpg",
                "camera_id": "a",
                "label": "dry",
            },  # agrees
            {
                "image": "b_20261107T150000Z.jpg",
                "camera_id": "b",
                "label": "wet",
            },  # disagrees
        ],
    )

    samples, dropped = load_labelled(images, [one, two])

    assert [(s.camera_id, s.label) for s in samples] == [("a", "dry"), ("c", "wet")]
    assert dict(dropped) == {
        "labellers_disagree": 1,
        "unknown_camera": 1,
        "file_missing": 1,
    }


def test_load_labelled_rejects_a_label_outside_the_six_classes(tmp_path: Path):
    csv_path = write_csv(
        tmp_path / "labels.csv",
        [{"image": "a.jpg", "camera_id": "a", "label": "slush"}],
    )
    with pytest.raises(ValueError, match="slush"):
        load_labelled(tmp_path, [csv_path])


# -- RSCD -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("dry_asphalt_smooth", "dry"),
        ("wet_concrete_slight", "wet"),
        ("water_asphalt_severe", "wet"),
        ("fresh_snow", "snow_covered"),
        ("melted_snow", "snow_partial"),
        ("ice", "ice"),
        ("Fresh-Snow", "snow_covered"),
        ("dryish", None),
        ("gravel", None),
    ],
)
def test_rscd_folder_names_map_to_our_classes(name: str, expected: str | None):
    assert rscd_class(name) == expected


def test_load_rscd_samples_each_class_reproducibly(tmp_path: Path):
    for folder, count in (("dry_asphalt_smooth", 5), ("ice", 2), ("gravel_road", 3)):
        for i in range(count):
            touch_image(tmp_path / folder, f"{i}.jpg")
    (tmp_path / "ice" / "notes.txt").write_text("not an image")

    first, unmapped = load_rscd(tmp_path, per_class_limit=3, seed=7)
    again, _ = load_rscd(tmp_path, per_class_limit=3, seed=7)

    assert class_counts(first) == {**dict.fromkeys(LABELS, 0), "dry": 3, "ice": 2}
    assert dict(unmapped) == {"gravel_road": 3}
    assert first == again
    assert all(s.source == RSCD_SOURCE and s.camera_id == RSCD_SOURCE for s in first)


# -- camera split ----------------------------------------------------------------


def test_split_never_shares_a_camera_between_train_val_and_test():
    split = split_by_camera(samples_for(cameras=20), seed=202)

    train, val, test = (
        set(split.train_cameras),
        set(split.val_cameras),
        set(split.test_cameras),
    )
    assert not (train & val or train & test or val & test)
    assert len(test) == 4 and len(val) == 2 and len(train) == 14
    assert {s.camera_id for s in split.test} == test
    assert {s.camera_id for s in split.val} == val
    assert {s.camera_id for s in split.train} == train


def test_split_is_reproducible_and_depends_on_the_seed():
    samples = samples_for(cameras=20)
    a = split_by_camera(samples, seed=202)
    b = split_by_camera(samples, seed=202)
    c = split_by_camera(samples, seed=7)
    assert a.test_cameras == b.test_cameras and a.val_cameras == b.val_cameras
    assert a.test_cameras != c.test_cameras


def test_rscd_only_ever_goes_to_training():
    rscd = [
        Sample(Path(f"r{i}.jpg"), "ice", RSCD_SOURCE, RSCD_SOURCE) for i in range(5)
    ]
    split = split_by_camera(samples_for(cameras=10) + rscd, seed=202)
    assert all(s in split.train for s in rscd)
    assert (
        RSCD_SOURCE not in split.test_cameras + split.val_cameras + split.train_cameras
    )


def test_fixed_test_cameras_are_respected_and_checked():
    samples = samples_for(cameras=6)
    split = split_by_camera(samples, seed=202, test_cameras=["cam1", "cam4"])
    assert split.test_cameras == ["cam1", "cam4"]
    with pytest.raises(ValueError, match="cam99"):
        split_by_camera(samples, seed=202, test_cameras=["cam99"])


def test_too_few_cameras_trains_on_everything_instead_of_failing():
    split = split_by_camera(samples_for(cameras=1), seed=202)
    assert split.test_cameras == [] and split.val_cameras == []
    assert len(split.train) == 6


def test_describe_split_lists_test_cameras_and_counts():
    described = describe_split(split_by_camera(samples_for(cameras=10), seed=202))
    assert set(described) == {
        "test_cameras",
        "val_cameras",
        "train_cameras",
        "counts",
        "test_images",
    }
    assert sum(described["counts"]["test"].values()) == len(described["test_images"])
    json.dumps(described)  # must be serialisable for split.json


def test_class_weights_favour_rare_classes_and_skip_absent_ones():
    samples = [Sample(Path(f"{i}.jpg"), "dry", "a", "511") for i in range(9)]
    samples.append(Sample(Path("x.jpg"), "ice", "a", "511"))
    weights = dict(zip(LABELS, class_weights(samples), strict=True))
    assert weights["ice"] == pytest.approx(5.0)
    assert weights["dry"] == pytest.approx(10 / 18)
    assert weights["wet"] == 0.0


# -- end to end (needs scripts/requirements-train.txt) --------------------------


def test_trains_exports_onnx_and_measures_latency(tmp_path: Path):
    pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    pytest.importorskip("onnxruntime")
    from PIL import Image

    from vision.train import TrainConfig, run

    images = tmp_path / "raw"
    images.mkdir()
    rows = []
    # Synthetic frames: each class gets its own flat colour so a tiny model can learn it.
    for camera in range(6):
        for index, label in enumerate(LABELS):
            name = f"cam{camera}_20261107T1500{index:02d}Z.png"
            shade = 30 + 40 * index
            Image.new("RGB", (64, 48), (shade, shade, 255 - shade)).save(images / name)
            rows.append({"image": name, "camera_id": f"cam{camera}", "label": label})
    labels_csv = write_csv(tmp_path / "labels.csv", rows)
    out = tmp_path / "run"

    config = TrainConfig(
        pretrained=False,
        epochs=2,
        batch_size=8,
        num_workers=0,
        input_size_px=64,
        latency_runs=5,
    )
    metrics = run(
        images_dir=images, label_csvs=[labels_csv], out_dir=out, config=config
    )

    assert {p.name for p in out.iterdir()} == {
        "checkpoint.pt",
        "labels.json",
        "metrics.json",
        "model.onnx",
        "split.json",
    }
    split = json.loads((out / "split.json").read_text())
    assert split["test_cameras"] and not set(split["test_cameras"]) & set(
        split["train_cameras"]
    )
    assert json.loads((out / "labels.json").read_text())["classes"] == list(LABELS)
    assert metrics["onnx_max_abs_diff_vs_torch"] < 1e-3
    assert (
        metrics["cpu_latency_ms"]["runs"] == 5
        and metrics["cpu_latency_ms"]["median"] > 0
    )
    assert len(metrics["history"]) == 2
    # The worker verifies the model by SHA-256 (review on #110): same value everywhere.
    import hashlib

    digest = hashlib.sha256((out / "model.onnx").read_bytes()).hexdigest()
    assert metrics["model_sha256"] == digest
    assert json.loads((out / "labels.json").read_text())["sha256"] == digest
    assert metrics["exporter"] in {"dynamo", "legacy"} and isinstance(
        metrics["opset"], int
    )
    assert (
        json.loads((out / "metrics.json").read_text())["best_epoch"]
        == metrics["best_epoch"]
    )


def test_same_seed_gives_the_same_model(tmp_path: Path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    from vision.train import TrainConfig, build_model, seed_everything

    def weights_after_seed() -> list:
        seed_everything(TrainConfig.seed)
        model = build_model("mobilenet_v3_small", len(LABELS), pretrained=False)
        return [p.detach().clone() for p in model.parameters()]

    first, second = weights_after_seed(), weights_after_seed()
    assert all(torch.equal(a, b) for a, b in zip(first, second, strict=True))


# -- input validation (review on #110) -----------------------------------------------


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--epochs", "0", "must be at least 1"),
        ("--batch-size", "0", "must be at least 1"),
        ("--latency-runs", "0", "must be at least 1"),
        ("--rscd-per-class", "0", "must be at least 1"),
        ("--workers", "-1", "must be at least 0"),
        ("--lr", "0", "must be greater than 0"),
        ("--lr", "nan", "must be greater than 0"),
        ("--label-smoothing", "1.5", "must be between 0 and 1"),
        ("--epochs", "ten", "not a valid int"),
    ],
)
def test_cli_rejects_invalid_numbers_before_training(flag, value, message, capsys):
    from vision.train import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--out", "unused", "--labels", "unused.csv", flag, value])
    assert exit_info.value.code == 2  # argparse usage error, nothing was trained
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "settings",
    [
        {"epochs": 0},
        {"batch_size": 0},
        {"num_workers": -1},
        {"latency_runs": 0},
        {"lr": 0.0},
        {"lr": float("inf")},
        {"label_smoothing": -0.1},
        {"input_size_px": 8},
        {"arch": "resnet50"},
    ],
)
def test_train_config_rejects_invalid_settings(settings):
    from vision.train import TrainConfig

    with pytest.raises(ValueError, match="Invalid training settings"):
        TrainConfig(**settings)


def test_train_config_accepts_the_edges_of_valid_ranges():
    from vision.train import TrainConfig

    TrainConfig(
        epochs=1, batch_size=1, num_workers=0, latency_runs=1, label_smoothing=0.0
    )
    TrainConfig(label_smoothing=1.0, weight_decay=0.0)


def test_run_rejects_rscd_per_class_below_one(tmp_path: Path):
    from vision.train import TrainConfig, run

    with pytest.raises(ValueError, match="rscd_per_class"):
        run(
            images_dir=tmp_path,
            label_csvs=[],
            out_dir=tmp_path / "out",
            config=TrainConfig(),
            rscd_dir=tmp_path,
            rscd_per_class=0,
        )


# -- review on #110 (Tatsat) -----------------------------------------------------------


def test_empty_test_camera_list_is_refused_not_silently_ignored(capsys):
    from vision.train import main

    with pytest.raises(ValueError, match="at least one camera id"):
        split_by_camera(samples_for(cameras=6), seed=202, test_cameras=[])
    with pytest.raises(SystemExit) as exit_info:
        main(["--out", "unused", "--labels", "unused.csv", "--test-cameras"])
    assert exit_info.value.code == 2
    assert "--test-cameras" in capsys.readouterr().err


def test_dataset_and_worker_seeding_can_be_pickled_for_spawned_workers():
    # macOS and Windows start DataLoader workers with "spawn", which pickles both.
    import pickle

    from vision.train import _ImageDataset, _seed_worker

    dataset = _ImageDataset(samples_for(cameras=1), transform=str)
    restored = pickle.loads(pickle.dumps(dataset))
    assert len(restored) == len(dataset) == 6
    assert pickle.loads(pickle.dumps(_seed_worker)) is _seed_worker


def test_loader_works_with_a_spawned_worker(tmp_path: Path):
    pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    from PIL import Image
    from torch.utils.data import DataLoader

    from vision.train import _ImageDataset, _seed_worker, _transforms

    samples = []
    for index, label in enumerate(LABELS[:2]):
        path = tmp_path / f"cam_{index}.png"
        Image.new("RGB", (40, 30), (40 * index, 80, 120)).save(path)
        samples.append(Sample(path, label, "cam", "511"))
    loader = DataLoader(
        _ImageDataset(samples, _transforms(32, train=False)),
        batch_size=2,
        num_workers=1,
        multiprocessing_context="spawn",  # what macOS and Windows use by default
        worker_init_fn=_seed_worker,
    )
    images, targets = next(iter(loader))
    assert tuple(images.shape) == (2, 3, 32, 32)
    assert targets.tolist() == [0, 1]


def test_labels_manifest_is_the_worker_contract():
    from vision.model_contract import labels_manifest

    digest = "ab" * 32
    manifest = labels_manifest(224, digest)
    assert manifest["classes"] == list(LABELS)
    assert manifest["sha256"] == digest
    assert manifest["input"] == {
        "name": "image",
        "layout": "NCHW",
        "size_px": 224,
        "resize": "stretch to size_px x size_px",
        "color": "RGB",
        "scale": "pixel / 255",
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225],
    }
    assert manifest["output"] == {"name": "logits", "softmax": "apply in the worker"}
    with pytest.raises(ValueError):
        labels_manifest(224, "not-a-digest")
