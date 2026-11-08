"""Purpose: preview approved 511 training frames and save only privacy-reviewed images.

Use cached camera metadata, one enabled view per physical camera (or every
enabled view with --views all), and the shared 511 rate limit. Images stay in
memory until a person explicitly presses k.
No API keys, request URLs, or automatic road-condition labels are written.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image

from vision.cameras import (
    DEFAULT_CACHE_PATH,
    EDMONTON_BBOX_APPROX,
    KEY_ENV,
    Camera,
    CameraSchemaError,
    CameraView,
    RateLimiter,
    in_bbox,
    parse_cameras,
)

MAX_IMAGE_BYTES = 8 * 1024 * 1024
VIEW_CHOICES = ("first", "all")
MANIFEST_COLUMNS = (
    "image",
    "camera_id",
    "view_id",
    "retrieved_at",
    "timestamp_basis",
    "privacy_checked_by",
)


def trusted_view(url: str) -> bool:
    """Only the team's approved HTTPS image host; never follow redirects elsewhere."""
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "511.alberta.ca"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def usable_views(camera: Camera) -> list[CameraView]:
    """Enabled views on the approved host, in the order the official API lists them."""
    return [
        view
        for view in camera.views
        if view.status.strip().lower() == "enabled" and trusted_view(view.url)
    ]


def select_views(
    cache: Path,
    limit: int,
    scope: str = "edmonton",
    camera_ids: set[int] | None = None,
    views: str = "first",
) -> list[tuple[Camera, CameraView]]:
    """Choose physical cameras; provincial training examples are allowed by issue #44.

    `limit` counts physical cameras. `views="first"` keeps each camera's first
    usable view; `views="all"` keeps every usable view, first view first.
    """
    if scope not in ("edmonton", "alberta"):
        raise ValueError("Unknown camera selection scope")
    if views not in VIEW_CHOICES:
        raise ValueError("Unknown view selection")
    cameras = parse_cameras(json.loads(cache.read_text())["cameras"])
    selected = []
    seen = set()
    for camera in sorted(cameras, key=lambda camera: camera.id):
        if camera.id in seen or (
            camera_ids is not None and camera.id not in camera_ids
        ):
            continue
        if scope == "edmonton" and not in_bbox(
            camera.latitude, camera.longitude, EDMONTON_BBOX_APPROX
        ):
            continue
        usable = usable_views(camera)
        if usable:
            chosen = usable if views == "all" else usable[:1]
            selected.extend((camera, view) for view in chosen)
            seen.add(camera.id)
        if len(seen) >= limit:
            break
    return selected


def review_frame(content: bytes, title: str) -> str:
    """A desktop preview: k confirms no identifiable people/plates, s skips, q quits."""
    import cv2
    import numpy as np

    frame = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return "s"
    height, width = frame.shape[:2]
    if max(height, width) > 1280:
        scale = 1280 / max(height, width)
        frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
    cv2.namedWindow(title, cv2.WINDOW_AUTOSIZE)
    try:
        cv2.imshow(title, frame)
        while True:
            key = cv2.waitKey(100) & 0xFF
            if key in (ord("k"), ord("s"), ord("q")):
                return chr(key)
            if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                return "q"
    finally:
        cv2.destroyAllWindows()


def collect(
    selected: list[tuple[Camera, CameraView]],
    out: Path,
    reviewer: str,
    client: httpx.Client,
    limiter: RateLimiter,
    review: Callable[[bytes, str], str] = review_frame,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    view_folders: bool = False,
) -> int:
    """Fetch once per selected view, review in memory, then save the original bytes.

    By default every frame goes in `out`. With `view_folders`, a camera's first
    usable view still goes in `out`, and each other view goes in
    `out/view_<view_id>/`, so two views of one camera never share a filename.
    """
    if not reviewer.strip():
        raise ValueError("A privacy reviewer name is required")
    saved = 0
    for camera, view in selected:
        if not trusted_view(view.url):
            print(f"SKIP camera={camera.id} unapproved image host")
            continue
        limiter.acquire()
        try:
            with client.stream("GET", view.url, follow_redirects=False) as response:
                if response.status_code == 429:
                    print(
                        "STOP: 511 rate limit reached; wait at least 60 seconds and coordinate team requests."
                    )
                    break
                if response.status_code != 200:
                    print(f"SKIP camera={camera.id} HTTP {response.status_code}")
                    continue
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError("image size limit exceeded")
                    chunks.append(chunk)
                content = b"".join(chunks)
            retrieved = now().astimezone(UTC)
            with Image.open(io.BytesIO(content)) as image:
                extension = {"JPEG": ".jpg", "PNG": ".png"}.get(image.format)
                image.verify()
            if extension is None:
                raise ValueError("unsupported image format")
        except (httpx.HTTPError, OSError, ValueError, Image.DecompressionBombError):
            # Exception messages can contain request URLs; report only camera IDs.
            print(f"SKIP camera={camera.id} image request or decoding failed")
            continue
        title = f"Camera {camera.id} view {view.id} | k: privacy clear/save | s: skip | q: quit"
        action = review(content, title)
        if action == "q":
            break
        if action != "k":
            print(f"SKIP camera={camera.id} not retained")
            continue
        folder = out
        if view_folders:
            usable = usable_views(camera)
            if not usable or usable[0].id != view.id:
                folder = out / f"view_{view.id}"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{camera.id}_{retrieved.strftime('%Y%m%dT%H%M%SZ')}{extension}"
        path = folder / name
        # Manifest paths are relative to `out`, like the label CSVs' image column.
        image = path.relative_to(out).as_posix()
        try:
            with path.open("xb") as handle:
                handle.write(content)
        except FileExistsError:
            print(f"SKIP camera={camera.id} filename already exists")
            continue
        manifest = out.parent / "collection.csv"
        has_header = manifest.exists() and manifest.stat().st_size > 0
        with manifest.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
            if not has_header:
                writer.writeheader()
            writer.writerow(
                {
                    "image": image,
                    "camera_id": str(camera.id),
                    "view_id": str(view.id),
                    "retrieved_at": retrieved.isoformat(),
                    "timestamp_basis": "retrieved_at_not_verified_capture_time",
                    "privacy_checked_by": reviewer.strip(),
                }
            )
        saved += 1
        print(f"SAVED camera={camera.id} {image}")
    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--out", type=Path, default=Path("vision/data/raw"))
    parser.add_argument(
        "--limit",
        type=int,
        default=6,
        help="Physical cameras to include; default 6 for the first review batch.",
    )
    parser.add_argument(
        "--views",
        choices=VIEW_CHOICES,
        default="first",
        help=(
            "first (default): one enabled view per camera, saved in --out. "
            "all: every enabled view; views after the first go in --out/view_<view_id>/."
        ),
    )
    parser.add_argument(
        "--reviewer",
        help="Required for live collection; recorded on every retained frame.",
    )
    parser.add_argument(
        "--scope",
        choices=("edmonton", "alberta"),
        default="edmonton",
        help="Use provincial cameras for additional snow/ice training examples (issue #44).",
    )
    parser.add_argument(
        "--camera-ids",
        type=int,
        nargs="+",
        help="Optional physical camera IDs to include.",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="List camera IDs only; no network, images, or key required.",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    if not args.plan and (args.reviewer is None or not args.reviewer.strip()):
        parser.error("--reviewer is required for live collection")
    if not args.plan and not os.environ.get(KEY_ENV, "").strip():
        parser.error(
            f"Set {KEY_ENV} in this terminal first; never paste it into code or chat"
        )
    try:
        selected = select_views(
            args.cache,
            args.limit,
            args.scope,
            set(args.camera_ids) if args.camera_ids is not None else None,
            args.views,
        )
    except (OSError, ValueError, KeyError, TypeError, CameraSchemaError):
        parser.exit(
            2,
            "Cannot read cached camera metadata; run python -m vision.cameras first.\n",
        )
    area = (
        "the approximate Edmonton bounding box"
        if args.scope == "edmonton"
        else "Alberta (training examples only)"
    )
    camera_count = len({camera.id for camera, _ in selected})
    view_note = f" ({len(selected)} views)" if args.views == "all" else ""
    print(f"Selected {camera_count} physical cameras in {area}{view_note}.")
    for camera, view in selected:
        print(f"camera={camera.id} view={view.id}")
    if args.plan:
        return 0
    if not selected:
        parser.exit(
            2, "No approved enabled camera views found in the cached metadata.\n"
        )
    print(
        "Keep only frames with no identifiable people or readable plates. k saves, s skips, q quits."
    )
    print(
        "Run only one team collector/worker at a time; this process allows 10 requests per 60 seconds."
    )
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            saved = collect(
                selected,
                args.out,
                args.reviewer,
                client,
                RateLimiter(),
                view_folders=args.views == "all",
            )
    except (OSError, ValueError) as exc:
        parser.exit(
            2,
            f"Collection stopped ({type(exc).__name__}); check local output and GUI setup.\n",
        )
    print(
        f"Saved {saved} privacy-reviewed frames to {args.out}; road-condition labels still needed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
