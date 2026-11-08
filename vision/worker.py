"""OP-204: refresh camera conditions without storing camera frames.

Share OP-112's limiter between metadata and image requests, run ONNX in memory,
and require two agreeing observations before publishing a changed condition.
The default command is a clearly labelled, network-free synthetic replay.
The payload matches the lead's OP-213 receiver; no shared API is changed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from vision.cameras import (
    DEFAULT_CACHE_PATH,
    Camera,
    CameraAdapter,
    CameraView,
    Polygon,
    RateLimiter,
    cameras_in_edmonton,
    load_boundary,
    view_enabled,
)
from vision.inference import (
    MAX_FRAME_BYTES,
    Classifier,
    ModelSpec,
    OnnxClassifier,
    Prediction,
    download_model,
)
from vision.tools.label import LABELS

log = logging.getLogger("vision.worker")
DEFAULT_INTERVAL_S = 300.0
CATALOG_INTERVAL_S = 86_400.0
REPLAY_PATH = Path(__file__).parent / "tests/fixtures/worker_replay.json"
Condition = Literal["dry", "wet", "snow_partial", "snow_covered", "ice", "unknown"]
VALID_CONDITIONS = frozenset(LABELS) - {"unusable"} | {"unknown"}
RECENT_FRAME_LIMIT = 32


class Reading(BaseModel):
    """Worker serialization of the agreed OP-213 payload (docs/OP-204.md)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    source: Literal["camera_model"] = "camera_model"
    source_ref: str
    camera_id: int
    view_id: int
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: Condition
    score: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = None
    observed_at: datetime
    expires_at: datetime
    model_version: str
    mode: Literal["live", "replay"]
    attribution: str = "Road camera data: 511 Alberta, Government of Alberta"

    @field_validator("observed_at", "expires_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Camera times must include a timezone")
        return value.astimezone(UTC)


@dataclass
class Vote:
    confirmed: str | None = None
    candidate: str | None = None
    count: int = 0
    sample: str | None = None
    recent_frames: deque[str] = field(
        default_factory=lambda: deque(maxlen=RECENT_FRAME_LIMIT)
    )


class StabilityFilter:
    """One state per camera/view; duplicate frames cannot confirm or renew data."""

    def __init__(self) -> None:
        self.states: dict[str, Vote] = {}

    def observe(self, ref: str, label: str, sample: str) -> bool:
        if label not in VALID_CONDITIONS:
            raise ValueError("Invalid camera condition")
        state = self.states.setdefault(ref, Vote())
        if state.sample == sample or (
            label != "unknown" and sample in state.recent_frames
        ):
            return False
        state.sample = sample
        if label != "unknown":
            # Failed attempts must not evict good hashes: alternating cached
            # images remain old evidence even after an extended camera outage.
            state.recent_frames.append(sample)
        if label == state.candidate:
            state.count += 1
        else:
            state.candidate, state.count = label, 1
        if label == state.confirmed:
            return True  # A fresh agreeing frame renews an already confirmed label.
        if state.count < 2:
            return False
        before = state.confirmed or "pending"
        state.confirmed = label
        log.info("CHANGE camera_model:%s %s -> %s", ref, before, label)
        return True


class FrameUnavailable(Exception):
    """A safe reason code, never a URL, response body or credential."""


class FrameFetcher:
    def __init__(self, client: httpx.Client, limiter: RateLimiter) -> None:
        self.client, self.limiter = client, limiter

    def fetch(self, view: CameraView) -> bytes:
        try:
            url = urlsplit(view.url)
            port = url.port
        except ValueError:
            raise FrameUnavailable("camera_unavailable") from None
        if (
            url.scheme != "https"
            or url.hostname != "511.alberta.ca"
            or url.username
            or url.password
            or port not in (None, 443)
        ):
            raise FrameUnavailable("unapproved_image_url")
        self.limiter.acquire()
        try:
            # A documented view URL may return a viewer, not an image. Never
            # scrape that page, chase redirects or derive a private image URL.
            with self.client.stream(
                "GET", view.url, timeout=20, follow_redirects=False
            ) as response:
                if response.status_code == 429:
                    self.limiter.cool_down()
                if response.status_code != 200:
                    raise FrameUnavailable("camera_http_error")
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if content_type not in {"image/jpeg", "image/png"}:
                    raise FrameUnavailable("not_an_image")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    size += len(chunk)
                    if size > MAX_FRAME_BYTES:
                        raise FrameUnavailable("frame_too_large")
                    chunks.append(chunk)
                frame = b"".join(chunks)
                if not frame:
                    raise FrameUnavailable("empty_frame")
                return frame
        except httpx.HTTPError:
            raise FrameUnavailable("camera_unavailable") from None


class Publisher(Protocol):
    def publish(self, reading: Reading) -> bool: ...


class HttpPublisher:
    """Send only reading metadata; OP-213 deployment is a separate dependency."""

    def __init__(self, client: httpx.Client, cloud_url: str, worker_key: str) -> None:
        url = urlsplit(cloud_url)
        local_http = url.scheme == "http" and url.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not (url.scheme == "https" or local_http)
            or not worker_key
        ):
            raise ValueError("Set a valid CLOUD_URL and WORKER_KEY")
        self.client = client
        self.url = cloud_url.rstrip("/") + "/api/ingest/camera"
        self.key = worker_key

    def publish(self, reading: Reading) -> bool:
        if reading.mode != "live":
            raise ValueError("Replay readings must never be posted to the live API")
        try:
            response = self.client.post(
                self.url,
                json=reading.model_dump(mode="json"),
                headers={"X-Worker-Key": self.key},
                timeout=10,
                follow_redirects=False,
            )
            return 200 <= response.status_code < 300
        except httpx.HTTPError:
            return False


class CameraWorker:
    """A serial background worker; metadata and frames use one shared limiter."""

    def __init__(
        self,
        adapter: CameraAdapter,
        frames: FrameFetcher,
        classifier: Classifier,
        publisher: Publisher | None = None,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        boundary: Sequence[Polygon] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("Camera interval must be positive and finite")
        self.adapter, self.frames, self.classifier = adapter, frames, classifier
        self.publisher, self.interval_s, self.boundary = publisher, interval_s, boundary
        self.clock, self.now = clock, now
        self.stability = StabilityFilter()
        self.last_good: dict[str, Reading] = {}
        self._cameras: list[Camera] = []
        self._refresh_at = -math.inf
        self._attempt = 0
        self._publish_failed: set[str] = set()
        self._pending_posts: dict[str, Reading] = {}
        self.publish_attempted = 0
        self.publish_succeeded = 0
        self.publish_failed = 0

    def _deliver(self, reading: Reading) -> None:
        """Retry metadata on a later pass without renewing its original timestamps."""
        ref = reading.source_ref
        if reading.expires_at <= self.now():
            if self._pending_posts.pop(ref, None) is not None:
                log.warning("CHANGE camera_model:%s expired undelivered", ref)
                self._publish_failed.discard(ref)
            return
        delivered = True
        if self.publisher is not None:
            self.publish_attempted += 1
            delivered = self.publisher.publish(reading)
            if delivered:
                self.publish_succeeded += 1
            else:
                self.publish_failed += 1
        if delivered:
            self.last_good[ref] = reading
            self._pending_posts.pop(ref, None)
            if ref in self._publish_failed:
                log.info("CHANGE camera_model:%s publishing recovered", ref)
                self._publish_failed.remove(ref)
        else:
            self._pending_posts[ref] = reading
            if ref not in self._publish_failed:
                log.warning(
                    "CHANGE camera_model:%s publish failed; retained last good", ref
                )
                self._publish_failed.add(ref)

    def cycle(self) -> list[Reading]:
        """One scheduled pass; a failed camera cannot interrupt its neighbours."""
        self.publish_attempted = self.publish_succeeded = self.publish_failed = 0
        if self.clock() >= self._refresh_at:
            snapshot = self.adapter.refresh()
            self._cameras = snapshot.cameras
            self._refresh_at = self.clock() + (
                self.interval_s if snapshot.stale else CATALOG_INTERVAL_S
            )
        readings: list[Reading] = []
        active_refs: set[str] = set()
        for camera in cameras_in_edmonton(self._cameras, self.boundary):
            for view in camera.views:
                ref = f"511:{camera.id}:{view.id}"
                if ref in active_refs:
                    continue
                active_refs.add(ref)
                self._attempt += 1
                sample = f"unavailable:{self._attempt}"
                try:
                    if not view_enabled(view):
                        raise FrameUnavailable("camera_disabled")
                    frame = self.frames.fetch(view)
                except FrameUnavailable as exc:
                    prediction = Prediction("unknown", reason=str(exc))
                except Exception:  # noqa: BLE001 - Isolate unexpected source/transport failures.
                    prediction = Prediction("unknown", reason="camera_unavailable")
                else:
                    sample = hashlib.sha256(frame).hexdigest()
                    try:
                        prediction = self.classifier.predict(frame)
                    except Exception:  # noqa: BLE001 - Isolate third-party inference errors.
                        prediction = Prediction("unknown", reason="model_error")
                    finally:
                        del frame
                # Defend against a malformed prediction from an injected/exported model.
                if prediction.label not in VALID_CONDITIONS or (
                    prediction.score is not None
                    and (
                        not math.isfinite(prediction.score)
                        or not 0 <= prediction.score <= 1
                    )
                ):
                    prediction = Prediction("unknown", reason="invalid_model_output")
                if prediction.label == "unknown":
                    # Repeated failed attempts can confirm unavailability, but
                    # repeated copies of a good frame cannot confirm safety.
                    sample = f"unavailable:{self._attempt}"
                if not self.stability.observe(ref, prediction.label, sample):
                    # Pending/failed samples never refresh the previous dry timestamp.
                    if ref in self._pending_posts:
                        self._deliver(self._pending_posts[ref])
                    continue
                observed_at = self.now()
                reading = Reading(
                    source_ref=ref,
                    camera_id=camera.id,
                    view_id=view.id,
                    latitude=camera.latitude,
                    longitude=camera.longitude,
                    label=prediction.label,
                    score=prediction.score,
                    reason=prediction.reason,
                    observed_at=observed_at,
                    expires_at=observed_at + timedelta(seconds=3 * self.interval_s),
                    model_version=self.classifier.version,
                    mode="live",
                )
                readings.append(reading)
                self._deliver(reading)
        # Removed cameras/views must not leave unbounded voting state in memory.
        for ref in self.stability.states.keys() - active_refs:
            del self.stability.states[ref]
            self.last_good.pop(ref, None)
            self._publish_failed.discard(ref)
            self._pending_posts.pop(ref, None)
        return readings

    def run(self, sleep: Callable[[float], None] = time.sleep) -> None:
        """Wait between complete passes; no catch-up bursts after a slow pass."""
        while True:
            self.cycle()
            sleep(self.interval_s)


def replay(path: Path) -> dict[str, object]:
    """Exercise voting from synthetic predictions; never fetch, infer or post."""
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, dict) or data.get("mode") != "replay":
        raise ValueError("Worker replay must be explicitly marked mode=replay")
    stability = StabilityFilter()
    readings = []
    for row in data["observations"]:
        reading = Reading.model_validate({**row["reading"], "mode": "replay"})
        if stability.observe(reading.source_ref, reading.label, row["sample"]):
            readings.append(reading.model_dump(mode="json"))
    return {"mode": "replay", "synthetic_predictions": True, "readings": readings}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OP-204 camera worker (defaults to offline replay)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live",
        action="store_true",
        help="Use official camera metadata and image responses",
    )
    mode.add_argument("--replay", type=Path, help="Synthetic prediction replay JSON")
    parser.add_argument("--model-labels", type=Path, help="OP-202 labels.json export")
    parser.add_argument(
        "--model-sha256", help="Expected SHA-256 of the ONNX release asset"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        help="Acceptance threshold agreed from model validation",
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        help="Edmonton boundary GeoJSON; otherwise approximate bbox",
    )
    parser.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single live pass (startup voting may still be pending)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Post live readings to the agreed API; requires WORKER_KEY",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not math.isfinite(args.interval_s) or args.interval_s <= 0:
        parser.error("--interval-s must be positive and finite")
    if not args.live:
        if args.publish:
            parser.error("--publish requires --live; replay never sends readings")
        try:
            print(json.dumps(replay(args.replay or REPLAY_PATH), indent=2))
            return 0
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            parser.error("Invalid worker replay; use the documented replay fixture")
    if os.environ.get("DATA_MODE") == "replay":
        parser.error("--live is disabled when DATA_MODE=replay")
    if (
        not args.model_labels
        or not args.model_sha256
        or args.min_score is None
        or not os.environ.get("ALBERTA_511_KEY")
        or not os.environ.get("MODEL_URL")
    ):
        parser.error(
            "--live requires --model-labels, --model-sha256, --min-score, ALBERTA_511_KEY and MODEL_URL"
        )
    try:
        spec = ModelSpec.from_training_labels(
            json.loads(args.model_labels.read_text("utf-8")),
            sha256=args.model_sha256,
            min_score=args.min_score,
        )
        boundary = load_boundary(args.boundary) if args.boundary else None
        with httpx.Client(timeout=20) as client:
            classifier = OnnxClassifier.load(
                download_model(client, os.environ["MODEL_URL"]), spec
            )
            limiter = RateLimiter()
            adapter = CameraAdapter(
                os.environ["ALBERTA_511_KEY"],
                client=client,
                limiter=limiter,
                cache_path=DEFAULT_CACHE_PATH,
            )
            publisher = (
                HttpPublisher(
                    client,
                    os.environ.get("CLOUD_URL", ""),
                    os.environ.get("WORKER_KEY", ""),
                )
                if args.publish
                else None
            )
            worker = CameraWorker(
                adapter,
                FrameFetcher(client, limiter),
                classifier,
                publisher,
                interval_s=args.interval_s,
                boundary=boundary,
            )
            if args.once:
                readings = worker.cycle()
                print(
                    json.dumps(
                        {
                            "mode": "live",
                            "publish_requested": args.publish,
                            "published": worker.publish_succeeded > 0,
                            "publish_attempted": worker.publish_attempted,
                            "publish_succeeded": worker.publish_succeeded,
                            "publish_failed": worker.publish_failed,
                            "readings": [r.model_dump(mode="json") for r in readings],
                        },
                        indent=2,
                    )
                )
                if args.publish and worker.publish_succeeded == 0:
                    return 1
            else:
                worker.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError, httpx.HTTPError):
        # HTTP/ONNX errors may contain credentials or release URLs: print only guidance.
        print(
            "Worker startup failed; check model manifest, credentials and service availability. No frame was stored.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
