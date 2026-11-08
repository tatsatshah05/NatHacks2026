"""OP-204: classify camera frames in memory with an explicitly described ONNX model.

The training export supplies class order and preprocessing in a sidecar JSON.
Reject unusable images and invalid predictions rather than guessing dry roads.
No camera image or image-derived array is saved to disk.
"""

from __future__ import annotations

import hashlib
import io
import math
import warnings
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from vision.tools.label import LABELS

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 10_000_000
MAX_MODEL_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class Prediction:
    label: str
    score: float | None = None
    reason: str | None = None


class Classifier(Protocol):
    version: str

    def predict(self, frame: bytes) -> Prediction: ...


class ModelSpec(BaseModel):
    """Validated runtime settings derived from OP-202's labels.json export."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    labels: tuple[str, ...]
    input_name: str = Field(min_length=1)
    output_name: str = Field(min_length=1)
    width: int = Field(gt=0, le=2048)
    height: int = Field(gt=0, le=2048)
    color: Literal["RGB"]
    layout: Literal["NCHW"]
    resize: Literal["stretch_bilinear"]
    pixel_scale: float = Field(gt=0)
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    output: Literal["logits", "probabilities"]
    min_score: float = Field(ge=0, le=1)

    @classmethod
    def from_training_labels(
        cls, metadata: object, *, sha256: str, min_score: float
    ) -> ModelSpec:
        """Consume the actual OP-202 export format without importing PyTorch.

        Matches vision/train.py at f8879bf. Checksum and score policy are worker
        settings because that training export does not currently contain them.
        """
        if not isinstance(metadata, dict):
            raise TypeError("Expected OP-202 labels.json object")
        try:
            inputs, outputs = metadata["input"], metadata["output"]
            if not isinstance(inputs, dict) or not isinstance(outputs, dict):
                raise TypeError("Invalid model input/output metadata")
            required = {
                "layout": "NCHW",
                "resize": "stretch to size_px x size_px",
                "color": "RGB",
                "scale": "pixel / 255",
            }
            if any(inputs.get(key) != value for key, value in required.items()):
                raise ValueError(
                    "Unsupported training preprocessing; agree it with Ipsa"
                )
            if outputs.get("softmax") != "apply in the worker":
                raise ValueError("Unsupported training output")
            return cls(
                sha256=sha256,
                labels=metadata["classes"],
                input_name=inputs["name"],
                output_name=outputs["name"],
                width=inputs["size_px"],
                height=inputs["size_px"],
                color="RGB",
                layout="NCHW",
                resize="stretch_bilinear",
                pixel_scale=1 / 255,
                mean=inputs["mean"],
                std=inputs["std"],
                output="logits",
                min_score=min_score,
            )
        except KeyError:
            raise ValueError("Missing required OP-202 labels.json fields") from None

    @field_validator("labels")
    @classmethod
    def all_classes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(LABELS) or set(value) != set(LABELS):
            raise ValueError("labels must contain each LABELS.md class exactly once")
        return value

    @field_validator("std")
    @classmethod
    def positive_std(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(number <= 0 for number in value):
            raise ValueError("std must be positive")
        return value


class Session(Protocol):
    def get_inputs(self) -> list[Any]: ...
    def get_outputs(self) -> list[Any]: ...
    def run(self, names: list[str], inputs: dict[str, np.ndarray]) -> list[Any]: ...


def image_tensor(frame: bytes, spec: ModelSpec) -> np.ndarray | None:
    """Decode one bounded image; conservative quality gates return no tensor.

    The brightness/contrast cutoffs are provisional rejection guards, not a
    validated fog detector. OP-202 must also recognise unusable real frames.
    """
    if not frame or len(frame) > MAX_FRAME_BYTES:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(frame)) as image:
                if image.format not in {"JPEG", "PNG"}:
                    return None
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    return None
                image.load()
                # Inspect a small copy so large frames do not create large float arrays.
                rgb = image.convert("RGB")
                preview = np.asarray(
                    rgb.resize((64, 64)).convert("L"), dtype=np.float32
                )
                low, high = np.percentile(preview, [10, 90])
                if float(preview.mean()) < 16 or high - low < 12:
                    return None
                resized = rgb.resize(
                    (spec.width, spec.height), Image.Resampling.BILINEAR
                )
                pixels = np.asarray(resized, dtype=np.float32) * spec.pixel_scale
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        return None
    pixels = (pixels - np.asarray(spec.mean, dtype=np.float32)) / np.asarray(
        spec.std, dtype=np.float32
    )
    return np.ascontiguousarray(pixels.transpose(2, 0, 1)[None], dtype=np.float32)


class OnnxClassifier:
    """CPU classifier with one reusable session and no implicit class ordering."""

    def __init__(self, session: Session, spec: ModelSpec) -> None:
        self.session = session
        self.spec = spec
        self.version = spec.sha256
        inputs = session.get_inputs()
        outputs = session.get_outputs()
        if len(inputs) != 1 or inputs[0].name != spec.input_name:
            raise ValueError("Model input does not match its manifest")
        if inputs[0].type != "tensor(float)":
            raise ValueError("Model input must be float32")
        expected = [1, 3, spec.height, spec.width]
        shape = inputs[0].shape
        if len(shape) != 4 or any(
            isinstance(actual, int) and actual != wanted
            for actual, wanted in zip(shape, expected, strict=True)
        ):
            raise ValueError("Model shape does not match its manifest")
        if spec.output_name not in {output.name for output in outputs}:
            raise ValueError("Model output does not match its manifest")

    @classmethod
    def load(cls, model: bytes, spec: ModelSpec) -> OnnxClassifier:
        if hashlib.sha256(model).hexdigest() != spec.sha256:
            raise ValueError("Model checksum does not match its manifest")
        import onnxruntime as ort

        try:
            session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        except Exception:  # noqa: BLE001 - Native provider errors have unrelated types.
            raise ValueError("Could not initialise the CPU ONNX model") from None
        return cls(session, spec)

    def predict(self, frame: bytes) -> Prediction:
        tensor = image_tensor(frame, self.spec)
        if tensor is None:
            return Prediction("unknown", reason="unusable_frame")
        try:
            result = self.session.run(
                [self.spec.output_name], {self.spec.input_name: tensor}
            )
            scores = np.asarray(result[0], dtype=np.float64)
            if (
                scores.shape != (1, len(self.spec.labels))
                or not np.isfinite(scores).all()
            ):
                return Prediction("unknown", reason="invalid_model_output")
            scores = scores[0]
            if self.spec.output == "logits":
                scores = np.exp(scores - scores.max())
                scores /= scores.sum()
            elif (
                (scores < 0).any()
                or (scores > 1).any()
                or not math.isclose(float(scores.sum()), 1, abs_tol=1e-4)
            ):
                return Prediction("unknown", reason="invalid_model_output")
            index = int(scores.argmax())
            label, score = self.spec.labels[index], float(scores[index])
            if label == "unusable":
                return Prediction("unknown", score, "model_unusable")
            if score < self.spec.min_score:
                return Prediction("unknown", score, "low_confidence")
            return Prediction(label, score)
        except Exception:  # noqa: BLE001 - Isolate native inference failures at this boundary.
            # Third-party inference failures have several exception types. Never
            # log input arrays, frame bytes, URLs or an exception containing them.
            return Prediction("unknown", reason="model_error")


def download_model(client: httpx.Client, url: str) -> bytes:
    """Load a release asset into memory; frames never use this redirecting path."""
    if not url.startswith("https://"):
        raise ValueError("MODEL_URL must use HTTPS")
    chunks: list[bytes] = []
    size = 0
    with client.stream("GET", url, timeout=30, follow_redirects=True) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_MODEL_BYTES:
                raise ValueError("Model exceeds the 128 MiB download limit")
            chunks.append(chunk)
    return b"".join(chunks)
