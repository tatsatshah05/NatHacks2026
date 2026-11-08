"""Tests for the labelling tool's headless core (OP-115). No window, no cv2."""

from __future__ import annotations

import csv
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

import vision.tools.label as label_module
from vision.tools.label import (
    CSV_COLUMNS,
    KEY_TO_LABEL,
    LABELS,
    LabelSession,
    handle_key,
    image_info,
    list_images,
    main,
    parse_image_name,
    read_labels,
    run_ui,
)

FIXED_NOW = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
FIXED_NOW_ISO = "2026-11-07T22:00:00Z"


def write_image(path: Path, shade: int = 128) -> Path:
    """A tiny synthetic grey image; no camera frame is ever committed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (shade, shade, shade)).save(path)
    return path


def make_images(folder: Path, names: list[str]) -> list[Path]:
    return [write_image(folder / name) for name in names]


def make_session(folder: Path, out: Path, labeller: str = "ipsa") -> LabelSession:
    return LabelSession(folder, out, labeller, now=lambda: FIXED_NOW)


def read_rows(out: Path) -> list[dict[str, str]]:
    with out.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# -- file names ---------------------------------------------------------------


def test_parse_image_name_reads_camera_and_utc_time():
    parsed = parse_image_name("cam12_20261107T150211Z.jpg")
    assert parsed == ("cam12", datetime(2026, 11, 7, 15, 2, 11, tzinfo=UTC))


def test_parse_image_name_keeps_underscores_in_the_camera_id():
    parsed = parse_image_name("yeg_whitemud_gateway_20261107T150211Z.JPG")
    assert parsed is not None
    assert parsed[0] == "yeg_whitemud_gateway"


@pytest.mark.parametrize(
    "name",
    [
        "IMG_0001.jpg",  # no time stamp
        "cam12-20261107T150211Z.jpg",  # wrong separator
        "cam12_20261107T150211.jpg",  # no Z
        "cam12_20261399T150211Z.jpg",  # month 13 is not a date
        "20261107T150211Z.jpg",  # no camera id
    ],
)
def test_parse_image_name_rejects_other_names(name: str):
    assert parse_image_name(name) is None


def test_image_info_falls_back_to_unknown_camera_and_file_mtime(tmp_path: Path):
    path = write_image(tmp_path / "IMG_0001.jpg")
    mtime = datetime(2026, 11, 6, 8, 30, 0, tzinfo=UTC).timestamp()
    os.utime(path, (mtime, mtime))

    info = image_info(path)

    assert info.camera_id == "unknown"
    assert info.captured_at == datetime(2026, 11, 6, 8, 30, 0, tzinfo=UTC)
    assert info.from_name is False


def test_image_info_prefers_the_file_name(tmp_path: Path):
    path = write_image(tmp_path / "cam3_20261107T010203Z.png")
    info = image_info(path)
    assert (info.camera_id, info.from_name) == ("cam3", True)
    assert info.captured_at == datetime(2026, 11, 7, 1, 2, 3, tzinfo=UTC)


def test_list_images_is_recursive_sorted_and_ignores_other_files(tmp_path: Path):
    make_images(tmp_path, ["b.jpg", "a.jpeg", "sub/c.PNG"])
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / ".hidden.jpg").write_text("")

    found = [p.relative_to(tmp_path).as_posix() for p in list_images(tmp_path)]

    assert found == ["a.jpeg", "b.jpg", "sub/c.PNG"]


# -- keys ---------------------------------------------------------------------


def test_number_keys_map_to_the_six_classes_in_guide_order():
    assert LABELS == ("dry", "wet", "snow_partial", "snow_covered", "ice", "unusable")
    assert KEY_TO_LABEL == {
        "1": "dry",
        "2": "wet",
        "3": "snow_partial",
        "4": "snow_covered",
        "5": "ice",
        "6": "unusable",
    }


def test_set_label_accepts_a_key_or_a_label_name_and_rejects_others(tmp_path: Path):
    make_images(tmp_path / "raw", ["a.jpg", "b.jpg", "c.jpg"])
    session = make_session(tmp_path / "raw", tmp_path / "labels.csv")

    assert session.set_label("3").label == "snow_partial"
    assert session.set_label("ice").label == "ice"
    with pytest.raises(ValueError):
        session.set_label("7")
    with pytest.raises(ValueError):
        session.set_label("snow")
    assert (
        session.next_image() == tmp_path / "raw" / "c.jpg"
    )  # bad keys did not advance


def test_handle_key_dispatches_every_key(tmp_path: Path):
    paths = make_images(tmp_path / "raw", ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    session = make_session(tmp_path / "raw", tmp_path / "labels.csv")

    assert handle_key(session, "1") is True  # a -> dry
    assert handle_key(session, "S") is True  # b skipped (upper case works too)
    assert handle_key(session, "?") is True  # ignored
    assert session.next_image() == paths[2]
    assert handle_key(session, "x") is True  # c deleted
    assert not paths[2].exists()
    assert session.next_image() == paths[3]
    assert handle_key(session, "b") is True  # back to b
    assert session.next_image() == paths[1]
    assert handle_key(session, "q") is False

    assert session.labelled_count == 1
    assert session.skipped_count == 1
    assert session.deleted_count == 1


def test_handle_key_after_the_queue_is_finished(tmp_path: Path):
    paths = make_images(tmp_path / "raw", ["a.jpg"])
    session = make_session(tmp_path / "raw", tmp_path / "labels.csv")
    session.set_label("1")
    assert session.next_image() is None

    assert handle_key(session, "2") is True  # nothing to label: ignored
    assert session.next_image() is None
    assert handle_key(session, "b") is True  # back still works
    assert session.next_image() == paths[0]
    assert handle_key(session, "q") is False


# -- CSV, save and resume -----------------------------------------------------


def test_save_appends_rows_and_a_new_session_resumes(tmp_path: Path):
    raw = tmp_path / "raw"
    out = tmp_path / "labels.csv"
    make_images(
        raw, ["cam1_20261107T150211Z.jpg", "cam2_20261107T150212Z.jpg", "x.jpg"]
    )

    first = make_session(raw, out)
    assert first.total == 3 and first.already_labelled == 0
    first.set_label("4")
    first.set_label("1")
    assert first.save() is True
    assert first.save() is False  # nothing new

    rows = read_rows(out)
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert rows == [
        {
            "image": "cam1_20261107T150211Z.jpg",
            "camera_id": "cam1",
            "captured_at": "2026-11-07T15:02:11Z",
            "label": "snow_covered",
            "labeller": "ipsa",
            "labelled_at": FIXED_NOW_ISO,
        },
        {
            "image": "cam2_20261107T150212Z.jpg",
            "camera_id": "cam2",
            "captured_at": "2026-11-07T15:02:12Z",
            "label": "dry",
            "labeller": "ipsa",
            "labelled_at": FIXED_NOW_ISO,
        },
    ]

    second = make_session(raw, out, labeller="estella")
    assert second.already_labelled == 2
    assert second.total == 1
    assert second.next_image() == raw / "x.jpg"
    second.set_label("6")
    second.save()

    rows = read_rows(out)
    assert [r["image"] for r in rows] == [
        "cam1_20261107T150211Z.jpg",
        "cam2_20261107T150212Z.jpg",
        "x.jpg",
    ]
    assert rows[2]["label"] == "unusable"
    assert rows[2]["labeller"] == "estella"
    assert rows[2]["camera_id"] == "unknown"
    assert out.read_text(encoding="utf-8").count("image,camera_id") == 1  # one header

    third = make_session(raw, out)
    assert third.next_image() is None
    assert third.total == 0


def test_images_in_subfolders_are_keyed_by_relative_path(tmp_path: Path):
    raw = tmp_path / "raw"
    make_images(raw, ["cam1/cam1_20261107T150211Z.jpg"])
    session = make_session(raw, tmp_path / "labels.csv")
    row = session.set_label("2")
    assert row.image == "cam1/cam1_20261107T150211Z.jpg"
    assert row.camera_id == "cam1"


def test_save_writes_nothing_when_nothing_was_labelled(tmp_path: Path):
    raw = tmp_path / "raw"
    out = tmp_path / "labels.csv"
    make_images(raw, ["a.jpg"])
    session = make_session(raw, out)
    assert session.save() is False
    assert not out.exists()


def test_read_labels_handles_missing_and_empty_files(tmp_path: Path):
    assert read_labels(tmp_path / "missing.csv") == {}
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    assert read_labels(empty) == {}


def test_read_labels_refuses_a_file_that_is_not_a_labels_csv(tmp_path: Path):
    other = tmp_path / "other.csv"
    other.write_text("name,value\nx,1\n")
    with pytest.raises(ValueError, match="not a labels CSV"):
        read_labels(other)
    with pytest.raises(ValueError):
        make_session(tmp_path, other)


def test_labeller_must_not_be_empty(tmp_path: Path):
    with pytest.raises(ValueError):
        LabelSession(tmp_path, tmp_path / "labels.csv", "  ")


# -- skip, delete, back -------------------------------------------------------


def test_skipped_images_write_nothing_and_come_back_next_session(tmp_path: Path):
    raw = tmp_path / "raw"
    out = tmp_path / "labels.csv"
    paths = make_images(raw, ["a.jpg", "b.jpg"])

    session = make_session(raw, out)
    session.skip()
    session.set_label("2")
    session.save()

    assert [r["image"] for r in read_rows(out)] == ["b.jpg"]
    assert paths[0].exists()
    again = make_session(raw, out)
    assert again.next_image() == paths[0]
    assert again.total == 1


def test_delete_removes_the_file_and_records_nothing(tmp_path: Path):
    raw = tmp_path / "raw"
    out = tmp_path / "labels.csv"
    paths = make_images(raw, ["a.jpg", "b.jpg", "c.jpg"])

    session = make_session(raw, out)
    session.set_label("1")
    deleted = session.delete_current()
    assert deleted == paths[1]
    assert not paths[1].exists()
    assert session.next_image() == paths[2]  # the next image slid into place
    session.set_label("5")
    session.save()

    assert [r["image"] for r in read_rows(out)] == ["a.jpg", "c.jpg"]
    assert session.deleted_count == 1


def test_delete_after_back_also_drops_the_row_written_this_session(tmp_path: Path):
    raw = tmp_path / "raw"
    out = tmp_path / "labels.csv"
    paths = make_images(raw, ["a.jpg", "b.jpg"])

    session = make_session(raw, out)
    session.set_label("1")
    session.save()
    assert [r["image"] for r in read_rows(out)] == ["a.jpg"]

    assert session.back() == paths[0]
    session.delete_current()
    assert session.save() is True

    assert read_rows(out) == []
    assert not paths[0].exists()
    assert session.next_image() == paths[1]
    assert session.back() is None  # a deleted image cannot be revisited
    assert session.labelled_count == 0


def test_back_revisits_the_previous_image_and_a_new_label_replaces_the_row(
    tmp_path: Path,
):
    raw = tmp_path / "raw"
    out = tmp_path / "labels.csv"
    paths = make_images(raw, ["a.jpg", "b.jpg", "c.jpg"])

    session = make_session(raw, out)
    assert session.back() is None  # nothing to go back to yet
    session.set_label("1")
    session.set_label("2")
    assert session.next_image() == paths[2]

    assert session.back() == paths[1]
    assert session.existing_label(paths[1]) == "wet"
    assert session.back() == paths[0]
    assert session.back() is None
    assert session.next_image() == paths[0]

    session.set_label("4")  # correction
    assert session.next_image() == paths[1]
    session.skip()  # keep b as it is
    session.set_label("6")
    assert session.next_image() is None
    session.save()

    rows = read_rows(out)
    assert [(r["image"], r["label"]) for r in rows] == [
        ("a.jpg", "snow_covered"),
        ("b.jpg", "wet"),
        ("c.jpg", "unusable"),
    ]
    assert session.labelled_count == 3  # a correction is not a new label


def test_actions_without_a_current_image_raise(tmp_path: Path):
    make_images(tmp_path / "raw", ["a.jpg"])
    session = make_session(tmp_path / "raw", tmp_path / "labels.csv")
    session.skip()
    with pytest.raises(RuntimeError):
        session.set_label("1")
    with pytest.raises(RuntimeError):
        session.skip()
    with pytest.raises(RuntimeError):
        session.delete_current()


def test_one_log_line_per_state_change(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    raw = tmp_path / "raw"
    # Sorted order: cam1_... < d.jpg < e.jpg
    make_images(raw, ["cam1_20261107T150211Z.jpg", "d.jpg", "e.jpg"])
    session = make_session(raw, tmp_path / "labels.csv")

    with caplog.at_level(logging.INFO, logger="vision.label"):
        session.set_label("3")
        session.skip()
        session.back()
        session.set_label("1")
        session.delete_current()

    assert caplog.messages == [
        "LABEL cam1_20261107T150211Z.jpg snow_partial camera=cam1",
        "SKIP d.jpg",
        "BACK d.jpg",
        "LABEL d.jpg dry camera=unknown",
        "DELETE e.jpg person identifiable, file removed",
    ]


# -- window code stays out of the core ---------------------------------------


def test_module_does_not_import_cv2_at_import_time():
    assert "cv2" not in vars(label_module)
    assert "numpy" not in vars(label_module) and "np" not in vars(label_module)


def test_run_ui_explains_when_opencv_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    make_images(tmp_path / "raw", ["a.jpg"])
    session = make_session(tmp_path / "raw", tmp_path / "labels.csv")
    monkeypatch.setitem(sys.modules, "cv2", None)  # makes `import cv2` fail

    with pytest.raises(SystemExit, match="opencv-python"):
        run_ui(session)


# -- command line -------------------------------------------------------------


def test_main_exits_early_when_there_is_nothing_to_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    raw = tmp_path / "raw"
    raw.mkdir()

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("run_ui must not be called")

    monkeypatch.setattr(label_module, "run_ui", fail)
    code = main(
        ["--images", str(raw), "--out", str(tmp_path / "l.csv"), "--labeller", "x"]
    )

    assert code == 0
    assert "Nothing to label" in capsys.readouterr().out


def test_main_rejects_a_missing_folder(tmp_path: Path):
    code = main(
        [
            "--images",
            str(tmp_path / "nope"),
            "--out",
            str(tmp_path / "l.csv"),
            "--labeller",
            "x",
        ]
    )
    assert code == 2


def test_main_runs_the_ui_with_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    raw = tmp_path / "raw"
    out = tmp_path / "labels.csv"
    make_images(raw, ["a.jpg"])
    seen: list[LabelSession] = []

    def fake_ui(session: LabelSession, max_side_px: int = 1280) -> None:
        seen.append(session)
        session.set_label("1")
        session.save()

    monkeypatch.setattr(label_module, "run_ui", fake_ui)
    code = main(["--images", str(raw), "--out", str(out), "--labeller", "kaleb"])

    assert code == 0
    assert seen[0].labeller == "kaleb"
    assert [r["labeller"] for r in read_rows(out)] == ["kaleb"]
