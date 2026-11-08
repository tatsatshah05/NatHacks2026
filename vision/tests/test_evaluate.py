"""Tests for vision/evaluate.py (OP-203).

Metric, agreement and model-card tests need nothing beyond the vision requirements
and run in CI. The end-to-end test builds a tiny ONNX model by hand (onnx +
onnxruntime, no PyTorch); the preprocessing parity test needs torchvision. Both
skip where those packages are missing.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from vision.evaluate import (
    ACCURACY_TARGET,
    labeller_agreement,
    render_card,
    score,
)
from vision.tools.label import CSV_COLUMNS, LABELS


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


# -- metrics ----------------------------------------------------------------------


def test_score_counts_accuracy_recall_precision_and_confusion():
    truth = ["dry", "dry", "wet", "ice", "ice", "ice"]
    predicted = ["dry", "wet", "wet", "ice", "wet", "ice"]
    m = score(truth, predicted)

    assert m.total == 6 and m.correct == 4
    assert m.accuracy == pytest.approx(4 / 6)
    assert m.recall("dry") == pytest.approx(0.5)
    assert m.recall("ice") == pytest.approx(2 / 3)
    assert m.precision("wet") == pytest.approx(1 / 3)
    assert m.support("ice") == 3
    d, w, i = LABELS.index("dry"), LABELS.index("wet"), LABELS.index("ice")
    assert m.confusion[d][w] == 1 and m.confusion[i][w] == 1 and m.confusion[i][i] == 2


def test_missing_classes_give_none_not_zero():
    m = score(["dry"], ["dry"])
    assert m.recall("snow_covered") is None
    assert m.precision("snow_covered") is None
    assert score([], []).accuracy is None


def test_score_rejects_mismatched_lengths_and_unknown_labels():
    with pytest.raises(ValueError):
        score(["dry"], [])
    with pytest.raises(KeyError):
        score(["slush"], ["dry"])


# -- labeller agreement --------------------------------------------------------------


def test_agreement_and_kappa_on_double_labelled_images(tmp_path: Path):
    a = write_csv(
        tmp_path / "a.csv",
        [
            {"image": "1.jpg", "camera_id": "c", "label": "dry"},
            {"image": "2.jpg", "camera_id": "c", "label": "wet"},
            {"image": "3.jpg", "camera_id": "c", "label": "ice"},
            {"image": "4.jpg", "camera_id": "c", "label": "dry"},
            {"image": "only-once.jpg", "camera_id": "c", "label": "dry"},
        ],
    )
    b = write_csv(
        tmp_path / "b.csv",
        [
            {"image": "1.jpg", "camera_id": "c", "label": "dry"},
            {"image": "2.jpg", "camera_id": "c", "label": "wet"},
            {"image": "3.jpg", "camera_id": "c", "label": "wet"},
            {"image": "4.jpg", "camera_id": "c", "label": "dry"},
        ],
    )
    agreement = labeller_agreement([a, b])
    assert agreement.images == 4 and agreement.agreed == 3
    assert agreement.percent == pytest.approx(0.75)
    # observed 0.75; expected (2*2 + 1*2 + 1*0) / 16 = 0.375; kappa = 0.375 / 0.625
    assert agreement.kappa == pytest.approx(0.6)


def test_no_double_labels_means_not_measured(tmp_path: Path):
    a = write_csv(
        tmp_path / "a.csv", [{"image": "1.jpg", "camera_id": "c", "label": "dry"}]
    )
    agreement = labeller_agreement([a])
    assert (
        agreement.images == 0 and agreement.percent is None and agreement.kappa is None
    )


# -- model card --------------------------------------------------------------------


def test_card_without_an_evaluation_reports_nothing_as_measured():
    card = render_card(None)
    assert "## Results on unseen cameras" in card
    assert "Not measured yet" in card
    assert "%" not in card.split("## Known limits")[0].split("## Results")[1]
    for limit in ("Black ice is often invisible", "Night", "Glare"):
        assert limit in card
    assert "doi:10.21227/446p-xr65" in card  # RSCD attribution (CC BY 4.0)
    assert "black ice detected" not in card.lower()


def test_card_with_an_evaluation_shows_measured_numbers():
    evaluation = {
        "run": "v1",
        "evaluated_at": "2026-11-08T05:00:00+00:00",
        "test_cameras": ["cam1", "cam2"],
        "metrics": {
            "total": 10,
            "correct": 9,
            "accuracy": 0.9,
            "per_class": {
                label: {"support": 0, "recall": None, "precision": None}
                for label in LABELS
            }
            | {"dry": {"support": 10, "recall": 0.9, "precision": 1.0}},
            "confusion": [[9, 1, 0, 0, 0, 0]] + [[0] * 6 for _ in range(5)],
            "labels": list(LABELS),
        },
        "labeller_agreement": {"images": 4, "agreed": 3, "percent": 0.75, "kappa": 0.6},
        "training": {
            "arch": "mobilenet_v3_small",
            "seed": 202,
            "torch": "2.8.0",
            "cpu_latency_ms": {
                "median": 12.3,
                "p95": 15.0,
                "runs": 50,
                "measured_on": "Linux x86_64",
                "target_ms": 200,
            },
            "train_cameras": ["cam3", "cam4"],
            "val_cameras": ["cam5"],
        },
    }
    card = render_card(evaluation)
    assert "**Accuracy: 90.0%**, which meets the checkpoint target of 80%" in card
    assert "| `dry` | 10 | 90.0% | 100.0% |" in card
    assert "| `dry` | 9 | 1 | 0 | 0 | 0 | 0 |" in card
    assert "75.0% agreement, Cohen's kappa 0.60" in card
    assert "median 12.3 ms" in card
    assert (
        "untested on unseen cameras" in card
    )  # the classes with no test images are named
    evaluation["metrics"]["accuracy"] = ACCURACY_TARGET - 0.01
    assert "does not yet meet" in render_card(evaluation)


# -- end to end (needs onnx and onnxruntime) ----------------------------------------------


def _colour_model(path: Path) -> None:
    """A hand-built ONNX classifier: mean colour per channel -> logits for 6 classes.

    Bright red frames score highest on `dry`, bright blue on `ice`, so the test knows
    exactly what the model should predict without any training.
    """
    import numpy as np
    from onnx import TensorProto, helper, numpy_helper, save

    weights = np.zeros((3, 6), dtype=np.float32)
    weights[0, LABELS.index("dry")] = 1.0  # red channel
    weights[2, LABELS.index("ice")] = 1.0  # blue channel
    graph = helper.make_graph(
        [
            helper.make_node("GlobalAveragePool", ["image"], ["pooled"]),
            helper.make_node("Flatten", ["pooled"], ["flat"], axis=1),
            helper.make_node("MatMul", ["flat", "w"], ["logits"]),
        ],
        "colour",
        [
            helper.make_tensor_value_info(
                "image", TensorProto.FLOAT, ["batch", 3, 32, 32]
            )
        ],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", 6])],
        initializer=[numpy_helper.from_array(weights, "w")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8  # readable by every onnxruntime release that supports opset 17
    save(model, str(path))


def test_evaluates_only_the_test_cameras_and_writes_the_card(tmp_path: Path):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from PIL import Image

    from vision.evaluate import main

    images = tmp_path / "raw"
    images.mkdir()
    rows = []
    for camera, colour, label in (
        ("cam1", (255, 0, 0), "dry"),  # test camera, predicted dry: correct
        ("cam2", (0, 0, 255), "ice"),  # test camera, predicted ice: correct
        ("cam3", (0, 0, 255), "wet"),  # test camera, predicted ice: wrong
        ("cam9", (255, 0, 0), "ice"),  # training camera: must not be scored
    ):
        name = f"{camera}_20261107T150000Z.png"
        Image.new("RGB", (40, 30), colour).save(images / name)
        rows.append({"image": name, "camera_id": camera, "label": label})
    labels_csv = write_csv(tmp_path / "labels.csv", rows)

    run = tmp_path / "run"
    run.mkdir()
    _colour_model(run / "model.onnx")
    (run / "labels.json").write_text(
        json.dumps(
            {
                "classes": list(LABELS),
                "input": {
                    "name": "image",
                    "size_px": 32,
                    "mean": [0.0, 0.0, 0.0],
                    "std": [1.0, 1.0, 1.0],
                },
                "output": {"name": "logits"},
            }
        )
    )
    (run / "split.json").write_text(
        json.dumps(
            {
                "test_cameras": ["cam1", "cam2", "cam3"],
                "val_cameras": [],
                "train_cameras": ["cam9"],
            }
        )
    )
    (run / "metrics.json").write_text(
        json.dumps(
            {"config": {"arch": "mobilenet_v3_small", "seed": 202}, "torch": "test"}
        )
    )
    card = tmp_path / "MODEL_CARD.md"

    assert (
        main(
            [
                "--run",
                str(run),
                "--images",
                str(images),
                "--labels",
                str(labels_csv),
                "--card",
                str(card),
            ]
        )
        == 0
    )

    evaluation = json.loads((run / "evaluation.json").read_text())
    assert evaluation["metrics"]["total"] == 3  # cam9 (training) is not scored
    assert evaluation["metrics"]["correct"] == 2
    assert "**Accuracy: 66.7%**, which does not yet meet" in card.read_text()


def test_refuses_a_run_whose_test_cameras_leaked_into_training(tmp_path: Path):
    from vision.evaluate import evaluate

    (tmp_path / "split.json").write_text(
        json.dumps({"test_cameras": ["a"], "val_cameras": [], "train_cameras": ["a"]})
    )
    (tmp_path / "labels.json").write_text("{}")
    (tmp_path / "metrics.json").write_text("{}")
    labels_csv = write_csv(
        tmp_path / "labels.csv", [{"image": "a.png", "camera_id": "a", "label": "dry"}]
    )
    (tmp_path / "a.png").write_bytes(b"x")
    with pytest.raises(ValueError, match="also used in training"):
        evaluate(run_dir=tmp_path, images_dir=tmp_path, label_csvs=[labels_csv])


# -- the worker's contract: same preprocessing as training -------------------------------


def test_preprocessing_matches_train_py_exactly(tmp_path: Path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    import numpy as np
    from PIL import Image

    from vision.evaluate import preprocess
    from vision.train import IMAGENET_MEAN, IMAGENET_STD, _transforms

    rng = np.random.default_rng(0)
    path = tmp_path / "frame.png"
    Image.fromarray(rng.integers(0, 256, (90, 160, 3), dtype=np.uint8)).save(path)
    spec = {"size_px": 64, "mean": list(IMAGENET_MEAN), "std": list(IMAGENET_STD)}

    ours = preprocess(path, spec)
    with Image.open(path) as image:
        theirs = _transforms(64, train=False)(image.convert("RGB")).unsqueeze(0)
    assert ours.shape == tuple(theirs.shape) == (1, 3, 64, 64)
    assert np.abs(ours - theirs.numpy()).max() < 1e-5
    assert torch.is_tensor(theirs)
