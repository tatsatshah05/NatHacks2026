"""Keyboard labelling tool for camera images (OP-115).

Shows one image at a time and writes one CSV row per labelled image. The label
guide is vision/LABELS.md; read it before labelling.

Usage:
    python -m vision.tools.label --images vision/data/raw --out vision/data/labels.csv --labeller <name>

Keys:
    1 dry   2 wet   3 snow_partial   4 snow_covered   5 ice   6 unusable
    d or x  delete the image file (a person can be identified); nothing is recorded
    s       skip: nothing is written, and the image comes back in the next session
    b       back to the previous image (press a number to correct its label)
    q       quit; progress is saved after every key, so quitting loses nothing

File names `<camera_id>_<YYYYMMDDTHHMMSSZ>.jpg` give the camera id and the
capture time. Any other name falls back to camera_id "unknown" and the file's
modification time. Images already in the CSV are skipped, so a session resumes
where the last one stopped.

The core (`LabelSession`) is headless and tested. Only `run_ui()` imports cv2,
so this module imports without OpenCV and the tests need no display. The window
needs the GUI build of OpenCV (`pip install opencv-python`); the headless build
that vision/requirements.txt installs for the Render worker cannot open windows.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

log = logging.getLogger("vision.label")

LABELS: tuple[str, ...] = (
    "dry",
    "wet",
    "snow_partial",
    "snow_covered",
    "ice",
    "unusable",
)
KEY_TO_LABEL: dict[str, str] = {
    str(number): label for number, label in enumerate(LABELS, start=1)
}
DELETE_KEYS = frozenset({"d", "x"})
SKIP_KEY = "s"
BACK_KEY = "b"
QUIT_KEY = "q"

CSV_COLUMNS: tuple[str, ...] = (
    "image",
    "camera_id",
    "captured_at",
    "label",
    "labeller",
    "labelled_at",
)
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
UNKNOWN_CAMERA = "unknown"

# <camera_id>_<YYYYMMDDTHHMMSSZ>. The camera id may contain underscores, so the
# time stamp is anchored at the end of the file stem.
_NAME_PATTERN = re.compile(r"^(?P<camera_id>.+)_(?P<stamp>\d{8}T\d{6}Z)$")
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def iso_utc(value: datetime) -> str:
    """ISO 8601 in UTC with a Z suffix and whole seconds, e.g. 2026-11-07T15:02:11Z."""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------


def parse_image_name(name: str) -> tuple[str, datetime] | None:
    """Camera id and capture time (UTC) from `<camera_id>_<YYYYMMDDTHHMMSSZ>.<ext>`.

    Returns None when the name does not follow the pattern or the time stamp is
    not a real date, so the caller can fall back to the file's mtime.
    """
    match = _NAME_PATTERN.match(Path(name).stem)
    if not match:
        return None
    try:
        captured_at = datetime.strptime(match["stamp"], _STAMP_FORMAT).replace(
            tzinfo=UTC
        )
    except ValueError:
        return None
    return match["camera_id"], captured_at


@dataclass(frozen=True)
class ImageInfo:
    camera_id: str
    captured_at: datetime  # UTC
    from_name: bool  # False when the time came from the file's mtime


def image_info(path: Path) -> ImageInfo:
    """Camera id and capture time for an image, from its name or else its mtime."""
    parsed = parse_image_name(path.name)
    if parsed is not None:
        camera_id, captured_at = parsed
        return ImageInfo(camera_id, captured_at, from_name=True)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0)
    return ImageInfo(UNKNOWN_CAMERA, mtime, from_name=False)


def list_images(images_dir: Path) -> list[Path]:
    """Every .jpg, .jpeg or .png under the folder (recursively), in a stable order."""
    return sorted(
        (
            p
            for p in images_dir.rglob("*")
            if p.is_file()
            and p.suffix.lower() in IMAGE_SUFFIXES
            and not p.name.startswith(".")
        ),
        key=lambda p: p.relative_to(images_dir).as_posix(),
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelRow:
    image: str  # path relative to the images folder, POSIX separators
    camera_id: str
    captured_at: str  # ISO 8601 UTC
    label: str
    labeller: str
    labelled_at: str  # ISO 8601 UTC

    def as_dict(self) -> dict[str, str]:
        return {column: getattr(self, column) for column in CSV_COLUMNS}


def read_labels(path: Path) -> dict[str, LabelRow]:
    """Rows of a labels CSV keyed by image. A missing or empty file gives {}.

    Raises ValueError when the file exists but lacks the label columns, so the
    tool never rewrites a file that is not its own.
    """
    if not path.exists():
        return {}
    rows: dict[str, LabelRow] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return {}
        missing = [column for column in CSV_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"{path} is not a labels CSV: missing columns {', '.join(missing)}"
            )
        for record in reader:
            row = LabelRow(
                **{column: record.get(column) or "" for column in CSV_COLUMNS}
            )
            if row.image:
                rows[row.image] = row  # the last row for an image wins
    return rows


def write_labels(path: Path, rows: Iterable[LabelRow]) -> None:
    """Write the whole CSV atomically (temporary file, then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())
    os.replace(temporary, path)


# ---------------------------------------------------------------------------
# Session (headless)
# ---------------------------------------------------------------------------


class LabelSession:
    """The labelling state machine, with no window code.

    The session owns a queue of images that are not yet in the CSV. Every action
    (`set_label`, `skip`, `delete_current`) applies to the current image and moves
    to the next one; `back` returns to the previous image shown; `next_image`
    returns the image to show now, or None when the queue is finished. `save`
    rewrites the CSV from the merged rows, so a corrected label replaces the old
    row instead of adding a duplicate.
    """

    def __init__(
        self,
        images_dir: Path,
        out_csv: Path,
        labeller: str,
        now: Callable[[], datetime] | None = None,
        selected_images: set[str] | None = None,
    ) -> None:
        if not labeller.strip():
            raise ValueError("labeller must not be empty")
        self.images_dir = images_dir
        self.out_csv = out_csv
        self.labeller = labeller.strip()
        self._now = now or (lambda: datetime.now(UTC))
        self._rows: dict[str, LabelRow] = read_labels(out_csv)
        all_images = list_images(images_dir)
        if selected_images is not None:
            safe_selection = set()
            root = images_dir.resolve()
            for key in selected_images:
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("Selection contains an invalid image path")
                path = PurePosixPath(key.replace("\\", "/"))
                if (
                    path.is_absolute()
                    or PureWindowsPath(key).drive
                    or ".." in path.parts
                    or not (root / str(path)).resolve().is_relative_to(root)
                ):
                    raise ValueError(
                        "Selection contains a path outside the image folder"
                    )
                safe_selection.add(str(path))
            available = {self._key(path) for path in all_images}
            missing = safe_selection - available
            if missing:
                log.info("SKIP %d selected images no longer present", len(missing))
            all_images = [
                path for path in all_images if self._key(path) in safe_selection
            ]
        self._queue: list[Path] = [
            p for p in all_images if self._key(p) not in self._rows
        ]
        self.already_labelled = len(all_images) - len(self._queue)
        self._position = 0
        self._history: list[int] = []  # positions shown and left, for back()
        self._dirty = False
        self.labelled_count = 0
        self.deleted_count = 0
        self.skipped_count = 0

    # -- queries ------------------------------------------------------------

    def _key(self, path: Path) -> str:
        return path.relative_to(self.images_dir).as_posix()

    @property
    def current(self) -> Path | None:
        if self._position < len(self._queue):
            return self._queue[self._position]
        return None

    @property
    def total(self) -> int:
        """Images this session can show (not counting ones already in the CSV)."""
        return len(self._queue)

    @property
    def position(self) -> int:
        """1-based position of the current image, for a progress display."""
        return min(self._position + 1, len(self._queue))

    @property
    def remaining(self) -> int:
        return len(self._queue) - self._position

    def existing_label(self, path: Path) -> str | None:
        """The label already recorded for an image (after `back`), if any."""
        row = self._rows.get(self._key(path))
        return row.label if row else None

    def next_image(self) -> Path | None:
        """The image to show now, or None when every image has been handled."""
        return self.current

    # -- actions ------------------------------------------------------------

    def set_label(self, key: str) -> LabelRow:
        """Record a label for the current image and move on.

        `key` is a number key ("1" to "6") or a label name. Raises ValueError for
        anything else and RuntimeError when no image is current.
        """
        label = KEY_TO_LABEL.get(key) or (key if key in LABELS else None)
        if label is None:
            raise ValueError(f"unknown label key {key!r}")
        path = self._require_current()
        info = image_info(path)
        row = LabelRow(
            image=self._key(path),
            camera_id=info.camera_id,
            captured_at=iso_utc(info.captured_at),
            label=label,
            labeller=self.labeller,
            labelled_at=iso_utc(self._now()),
        )
        previous = self._rows.get(row.image)
        self._rows[row.image] = row
        self._dirty = True
        if previous is None:
            self.labelled_count += 1
            log.info("LABEL %s %s camera=%s", row.image, label, row.camera_id)
        else:
            log.info("LABEL %s %s->%s", row.image, previous.label, label)
        self._advance()
        return row

    def skip(self) -> None:
        """Move on without writing anything; the image returns next session."""
        path = self._require_current()
        self.skipped_count += 1
        log.info("SKIP %s", self._key(path))
        self._advance()

    def delete_current(self) -> Path:
        """Delete the current image file (a person is identifiable) and record nothing.

        Any row this session wrote for it is removed too. A deleted image cannot
        be reached with `back`.
        """
        path = self._require_current()
        key = self._key(path)
        path.unlink(missing_ok=True)
        if self._rows.pop(key, None) is not None:
            self._dirty = True
            self.labelled_count -= 1
        del self._queue[self._position]  # the next image slides into place
        self.deleted_count += 1
        log.info("DELETE %s person identifiable, file removed", key)
        return path

    def back(self) -> Path | None:
        """Return to the previous image shown, or None when there is none."""
        if not self._history:
            return None
        self._position = self._history.pop()
        path = self._queue[self._position]
        log.info("BACK %s", self._key(path))
        return path

    def save(self) -> bool:
        """Write the CSV when something changed since the last save."""
        if not self._dirty:
            return False
        write_labels(self.out_csv, self._rows.values())
        self._dirty = False
        return True

    # -- internals ----------------------------------------------------------

    def _require_current(self) -> Path:
        path = self.current
        if path is None:
            raise RuntimeError("no image is current; the queue is finished")
        return path

    def _advance(self) -> None:
        self._history.append(self._position)
        self._position += 1


def handle_key(session: LabelSession, key: str) -> bool:
    """Apply one key press to the session. Returns False when the user quits.

    Keys that mean nothing are ignored, so a stray press never changes state.
    """
    key = key.lower()
    if key == QUIT_KEY:
        return False
    if key == BACK_KEY:
        session.back()  # also works once the queue is finished
    elif session.next_image() is not None:
        if key in KEY_TO_LABEL:
            session.set_label(key)
        elif key in DELETE_KEYS:
            session.delete_current()
        elif key == SKIP_KEY:
            session.skip()
    return True


# ---------------------------------------------------------------------------
# Window (the only part that needs OpenCV; tests never call it)
# ---------------------------------------------------------------------------

WINDOW_NAME = "OpenPath labeller"
KEY_HELP = (
    "1 dry  2 wet  3 snow_partial  4 snow_covered  5 ice  6 unusable  |  "
    "d/x delete (person)  s skip  b back  q quit"
)
_STRIP_PX = 64


def run_ui(session: LabelSession, max_side_px: int = 1280) -> None:
    """Show images one at a time and feed key presses to the session."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit(
            "OpenCV is not installed. Run: pip install opencv-python"
        ) from exc

    try:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
    except cv2.error as exc:  # the headless build has no window support
        raise SystemExit(
            "This OpenCV build cannot open windows (opencv-python-headless). "
            "On the labelling laptop run: pip uninstall opencv-python-headless "
            "&& pip install opencv-python"
        ) from exc

    def fit(frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        scale = max_side_px / max(height, width)
        if scale >= 1.0:
            return frame
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)

    def compose(path: Path | None) -> np.ndarray:
        if path is None:
            frame = np.zeros((200, 640, 3), dtype=np.uint8)
            status = "All images handled. Press b to go back or q to quit."
        else:
            frame = cv2.imread(str(path))
            if frame is None:
                # Unreadable file: show black so it can be marked 6 or deleted.
                frame = np.zeros((360, 640, 3), dtype=np.uint8)
            frame = fit(frame)
            info = image_info(path)
            existing = session.existing_label(path)
            status = (
                f"{session.position}/{session.total}  {path.name}  "
                f"camera={info.camera_id}  captured={iso_utc(info.captured_at)}"
                + ("" if info.from_name else " (from file time)")
                + (f"  current label: {existing}" if existing else "")
            )
        height, width = frame.shape[:2]
        canvas = np.zeros((height + _STRIP_PX, max(width, 900), 3), dtype=np.uint8)
        canvas[:height, :width] = frame
        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(canvas, status, (8, height + 24), font, 0.5, (255, 255, 255), 1)
        cv2.putText(canvas, KEY_HELP, (8, height + 50), font, 0.45, (200, 200, 200), 1)
        return canvas

    def wait_key() -> str | None:
        """A key as a lower-case character, "" for a non-character key, None if the window closed."""
        while True:
            code = cv2.waitKey(50)
            if code >= 0:
                code &= 0xFF
                return chr(code).lower() if 32 <= code < 127 else ""
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                return None

    keys_seen = 0
    try:
        while True:
            cv2.imshow(WINDOW_NAME, compose(session.next_image()))
            key = wait_key()
            if key is None:
                if keys_seen == 0:
                    # Some headless builds create a window that is never shown,
                    # so the loop would end here with nothing labelled. Say so.
                    print(
                        "The window closed before any key was pressed. If no "
                        "window appeared, this OpenCV build cannot show one: run "
                        "pip uninstall opencv-python-headless && pip install "
                        "opencv-python",
                        file=sys.stderr,
                    )
                break
            keys_seen += 1
            if not handle_key(session, key):
                break
            session.save()
    finally:
        session.save()
        cv2.destroyAllWindows()


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Label camera images with the keyboard (see vision/LABELS.md)."
    )
    parser.add_argument(
        "--images", type=Path, required=True, help="Folder of .jpg/.png images."
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="Labels CSV to create or resume."
    )
    parser.add_argument(
        "--labeller", required=True, help="Your name, written into every row."
    )
    parser.add_argument(
        "--only",
        type=Path,
        help="Only show images listed in a blind sample CSV (image column).",
    )
    parser.add_argument(
        "--max-side",
        type=int,
        default=1280,
        help="Shrink images larger than this many pixels on their long side.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.images.is_dir():
        print(f"{args.images} is not a folder.", file=sys.stderr)
        return 2
    try:
        selection = None
        if args.only is not None:
            with args.only.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or "image" not in reader.fieldnames:
                    raise ValueError("Selection CSV needs an image column")
                selection = {row["image"] for row in reader}
        session = LabelSession(
            args.images, args.out, args.labeller, selected_images=selection
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        f"{session.total} images to label in {args.images} "
        f"({session.already_labelled} already in {args.out})"
    )
    if session.total == 0:
        print("Nothing to label.")
        return 0

    run_ui(session, max_side_px=args.max_side)
    print(
        f"Labelled {session.labelled_count}, deleted {session.deleted_count}, "
        f"skipped {session.skipped_count}; {session.remaining} left. "
        f"Saved to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
