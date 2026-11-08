"""Purpose: verify OP-201 dataset integrity using tiny synthetic images only."""

import csv
import json
from pathlib import Path

import pytest
from PIL import Image

from vision.tools.dataset import load_labels, main, prepare
from vision.tools.label import CSV_COLUMNS, LABELS, LabelRow, LabelSession, write_labels


def dataset(tmp_path: Path, count: int = 21):
    images = tmp_path / "raw"
    images.mkdir()
    rows = []
    for i in range(count):
        camera = f"cam{i % 5}"
        timestamp = f"20261107T1200{i:02d}Z"
        name = f"{camera}_{timestamp}.png"
        Image.new("RGB", (8, 8), (i, 0, 0)).save(images / name)
        rows.append(
            LabelRow(
                name,
                camera,
                f"2026-11-07T12:00:{i:02d}Z",
                LABELS[i % 6],
                "Estella",
                "2026-11-07T20:00:00Z",
            )
        )
    labels = tmp_path / "labels.csv"
    write_labels(labels, rows)
    return images, labels, rows, tmp_path / "dataset"


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_empty_dataset_reports_missing_evidence_without_inventing_agreement(tmp_path):
    images, labels, _, out = dataset(tmp_path, 0)
    report = prepare(labels, images, out)
    assert report["labelled_images"] == report["camera_count"] == 0
    assert report["agreement_percent"] is None
    assert report["measured_targets_met"] is False
    assert read_csv(out / "double_label.csv") == []
    assert read_csv(out / "train.csv") == []


def test_camera_split_is_disjoint_and_survives_new_images(tmp_path):
    images, labels, rows, out = dataset(tmp_path)
    report = prepare(labels, images, out)
    assert report["class_counts"] == {
        label: sum(row.label == label for row in rows) for label in LABELS
    }
    train = read_csv(out / "train.csv")
    test = read_csv(out / "test.csv")
    assert train and test
    assert not {r["camera_id"] for r in train} & {r["camera_id"] for r in test}
    assert len(train) + len(test) == len(rows)
    initial = json.loads((out / "registry.json").read_text())
    row = LabelRow(
        "new_cam_20261107T130000Z.png",
        "new_cam",
        "2026-11-07T13:00:00Z",
        "wet",
        "Estella",
        "2026-11-07T20:00:00Z",
    )
    Image.new("RGB", (8, 8), (200, 0, 0)).save(images / row.image)
    write_labels(labels, rows + [row])
    prepare(labels, images, out)
    updated = json.loads((out / "registry.json").read_text())
    assert all(
        updated["camera_splits"][key] == value
        for key, value in initial["camera_splits"].items()
    )
    assert set(initial["double_label_images"]) <= set(updated["double_label_images"])
    with pytest.raises(ValueError, match="seed changed"):
        prepare(labels, images, out, seed=99)


def test_blind_sample_and_real_agreement(tmp_path):
    images, labels, rows, out = dataset(tmp_path)
    report = prepare(labels, images, out)
    sample = read_csv(out / "double_label.csv")
    assert len(sample) == 3  # round 10% up, including small datasets
    assert set(sample[0]) == {"image", "camera_id", "captured_at"}
    assert report["double_label_completed"] == 0
    assert report["agreement_percent"] is None
    selected = {row["image"] for row in sample}
    originals = [row for row in rows if row.image in selected]
    second = tmp_path / "second.csv"
    revised = [
        LabelRow(
            **(
                row.as_dict()
                | {"labeller": "Ipsa", "label": "wet" if row.label != "wet" else "dry"}
            )
        )
        for row in originals
    ]
    write_labels(second, revised)
    report = prepare(labels, images, out, second)
    assert report["agreement_percent"] == 0
    assert len(report["disagreements"]) == 3
    assert (
        sum(sum(values.values()) for values in report["confusion_matrix"].values()) == 3
    )
    assert report["acceptance_checks"]["double_label_sample_complete"]
    assert not report["measured_targets_met"]
    write_labels(second, originals)
    with pytest.raises(ValueError, match="different second labeller"):
        prepare(labels, images, out, second)


def test_partial_second_labels_do_not_count_as_complete(tmp_path):
    images, labels, rows, out = dataset(tmp_path)
    prepare(labels, images, out)
    selected = read_csv(out / "double_label.csv")[0]["image"]
    row = next(row for row in rows if row.image == selected)
    second = tmp_path / "second.csv"
    write_labels(second, [LabelRow(**(row.as_dict() | {"labeller": "Ipsa"}))])
    report = prepare(labels, images, out, second)
    assert report["agreement_percent"] == 100
    assert report["double_label_completed"] == 1
    assert not report["acceptance_checks"]["double_label_sample_complete"]


def test_duplicate_pixels_across_splits_block_acceptance(tmp_path):
    images, labels, rows, out = dataset(tmp_path)
    prepare(labels, images, out)
    registry = json.loads((out / "registry.json").read_text())
    train = next(
        row for row in rows if registry["camera_splits"][row.camera_id] == "train"
    )
    test = next(
        row for row in rows if registry["camera_splits"][row.camera_id] == "test"
    )
    (images / test.image).write_bytes((images / train.image).read_bytes())
    report = prepare(labels, images, out)
    assert report["unique_frames"] == len(rows) - 1
    assert not report["acceptance_checks"]["no_identical_frames_across_splits"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"camera_id": "unknown"}, "known camera"),
        ({"camera_id": "wrong"}, "metadata disagree"),
        ({"captured_at": "2026-11-07T12:01:00Z"}, "metadata disagree"),
        ({"captured_at": "2026-11-07T12:00:00"}, "timezone"),
        ({"label": "maybe_ice"}, "invalid label"),
        ({"image": "../cam0_20261107T120000Z.png"}, "inside the image folder"),
    ],
)
def test_reject_bad_label_metadata(tmp_path, change, message):
    _, labels, rows, _ = dataset(tmp_path, 1)
    write_labels(labels, [LabelRow(**(rows[0].as_dict() | change))])
    with pytest.raises(ValueError, match=message):
        load_labels(labels)


def test_duplicate_csv_rows_are_rejected_instead_of_silently_overwritten(tmp_path):
    _, labels, rows, _ = dataset(tmp_path, 1)
    with labels.open("a", newline="") as handle:
        csv.DictWriter(handle, CSV_COLUMNS).writerow(rows[0].as_dict())
    with pytest.raises(ValueError, match="duplicate image"):
        load_labels(labels)


@pytest.mark.parametrize("bad_image", ["missing", "unreadable", "symlink"])
def test_invalid_images_do_not_overwrite_previous_report(tmp_path, bad_image):
    images, labels, rows, out = dataset(tmp_path, 1)
    prepare(labels, images, out)
    before = (out / "report.json").read_bytes()
    path = images / rows[0].image
    path.unlink()
    if bad_image == "unreadable":
        path.write_text("not an image")
    elif bad_image == "symlink":
        outside = tmp_path / "outside.png"
        Image.new("RGB", (8, 8)).save(outside)
        path.symlink_to(outside)
    with pytest.raises(ValueError, match="image"):
        prepare(labels, images, out)
    assert (out / "report.json").read_bytes() == before


def test_selection_only_shows_blind_sample_and_resumes(tmp_path):
    images, _, rows, _ = dataset(tmp_path, 3)
    second = tmp_path / "second.csv"
    selected = {rows[1].image}
    session = LabelSession(images, second, "Ipsa", selected_images=selected)
    assert session.total == 1
    assert session.current.name == rows[1].image
    session.set_label("wet")
    session.save()
    assert LabelSession(images, second, "Ipsa", selected_images=selected).total == 0
    assert LabelSession(images, second, "Ipsa", selected_images=set()).total == 0
    with pytest.raises(ValueError, match="missing images"):
        LabelSession(images, second, "Ipsa", selected_images={"../outside.png"})


def test_cli_reports_bad_registry_cleanly(tmp_path, capsys):
    images, labels, _, out = dataset(tmp_path, 0)
    out.mkdir()
    (out / "registry.json").write_text("[]")
    with pytest.raises(SystemExit) as error:
        main(["--labels", str(labels), "--images", str(images), "--out", str(out)])
    assert error.value.code == 2
    assert "Dataset audit failed" in capsys.readouterr().err


def test_label_cli_uses_blind_manifest_without_loading_original_labels(
    tmp_path, monkeypatch
):
    import vision.tools.label as tool

    images, labels, _, out = dataset(tmp_path)
    prepare(labels, images, out)
    expected = {row["image"] for row in read_csv(out / "double_label.csv")}
    shown = []

    def inspect_session(session, **kwargs):
        while session.current is not None:
            assert session.existing_label(session.current) is None
            shown.append(session.current.name)
            session.skip()

    monkeypatch.setattr(tool, "run_ui", inspect_session)
    result = tool.main(
        [
            "--images",
            str(images),
            "--out",
            str(tmp_path / "second.csv"),
            "--labeller",
            "Ipsa",
            "--only",
            str(out / "double_label.csv"),
        ]
    )
    assert result == 0
    assert set(shown) == expected
