"""OP-204: verify in-memory decoding, conservative unknowns and real CPU ONNX calls.

All images and the tiny test model are generated in memory. They exercise the
worker plumbing, not the accuracy or speed of Ipsa's future trained model.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from types import SimpleNamespace

import httpx
import numpy as np
import onnx
import pytest
from PIL import Image
from pydantic import ValidationError

from vision import inference
from vision.inference import ModelSpec, OnnxClassifier, download_model, image_tensor
from vision.tools.label import LABELS


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not call the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


def spec_for(**updates: object) -> ModelSpec:
    data = {
        "sha256": "0" * 64,
        "labels": LABELS,
        "input_name": "frame",
        "output_name": "scores",
        "width": 32,
        "height": 24,
        "color": "RGB",
        "layout": "NCHW",
        "resize": "stretch_bilinear",
        "pixel_scale": 1 / 255,
        "mean": [0, 0, 0],
        "std": [1, 1, 1],
        "output": "logits",
        "min_score": 0.6,
    }
    return ModelSpec.model_validate({**data, **updates})


def png(*, level: int | None = None, seed: int = 1) -> bytes:
    pixels = (
        np.random.default_rng(seed).integers(20, 240, (80, 100, 3), dtype=np.uint8)
        if level is None
        else np.full((80, 100, 3), level, dtype=np.uint8)
    )
    buffer = BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeSession:
    def __init__(self, output: object) -> None:
        self.output = output
        self.calls: list[dict[str, np.ndarray]] = []

    def get_inputs(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(name="frame", type="tensor(float)", shape=[1, 3, 24, 32])
        ]

    def get_outputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="scores")]

    def run(self, names: list[str], inputs: dict[str, np.ndarray]) -> list[object]:
        self.calls.append(inputs)
        return [self.output]


@pytest.mark.parametrize("level", [0, 5, 128, 250, 255])
def test_dark_flat_foglike_and_washed_out_frames_never_become_dry(level: int) -> None:
    session = FakeSession([[20, 0, 0, 0, 0, 0]])
    result = OnnxClassifier(session, spec_for()).predict(png(level=level))
    assert result.label == "unknown"
    assert not session.calls


@pytest.mark.parametrize(
    "frame", [b"", b"not an image", b"<html>Camera offline</html>"]
)
def test_unreadable_images_are_unknown(frame: bytes) -> None:
    assert (
        OnnxClassifier(FakeSession([[20, 0, 0, 0, 0, 0]]), spec_for())
        .predict(frame)
        .label
        == "unknown"
    )


def test_frame_and_decoded_pixel_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inference, "MAX_FRAME_BYTES", 4)
    assert image_tensor(b"large", spec_for()) is None
    monkeypatch.setattr(inference, "MAX_FRAME_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(inference, "MAX_IMAGE_PIXELS", 100)
    assert image_tensor(png(), spec_for()) is None


def test_rgb_nchw_normalization_matches_explicit_manifest() -> None:
    spec = spec_for(mean=[0.1, 0.2, 0.3], std=[0.5, 0.5, 0.5])
    frame = png()
    tensor = image_tensor(frame, spec)
    with Image.open(BytesIO(frame)) as image:
        expected = (
            np.asarray(
                image.resize((32, 24), Image.Resampling.BILINEAR), dtype=np.float32
            )
            / 255
        )
    expected = (expected - np.array(spec.mean)) / np.array(spec.std)
    assert tensor.shape == (1, 3, 24, 32)
    assert tensor.dtype == np.float32
    assert tensor.flags.c_contiguous
    np.testing.assert_allclose(tensor[0].transpose(1, 2, 0), expected, atol=1e-6)


@pytest.mark.parametrize("label", LABELS)
def test_all_classes_follow_manifest_order(label: str) -> None:
    order = tuple(reversed(LABELS))
    scores = np.zeros((1, 6))
    scores[0, order.index(label)] = 20
    result = OnnxClassifier(FakeSession(scores), spec_for(labels=order)).predict(png())
    assert result.label == ("unknown" if label == "unusable" else label)


@pytest.mark.parametrize(
    "scores",
    [
        [[0, 0, 0, 0, 0, 0]],
        [[float("nan"), 1, 0, 0, 0, 0]],
        [[1, 2]],
    ],
)
def test_low_confidence_and_invalid_logits_are_unknown(scores: object) -> None:
    assert (
        OnnxClassifier(FakeSession(scores), spec_for()).predict(png()).label
        == "unknown"
    )


@pytest.mark.parametrize(
    "scores",
    [
        [[2, 0, 0, 0, 0, 0]],
        [[-1, 2, 0, 0, 0, 0]],
        [[0.5, 0, 0, 0, 0, 0]],
    ],
)
def test_invalid_probability_outputs_are_unknown(scores: object) -> None:
    result = OnnxClassifier(
        FakeSession(scores), spec_for(output="probabilities")
    ).predict(png())
    assert result.label == "unknown"


def test_inference_failure_is_unknown_without_exception_details() -> None:
    class Broken(FakeSession):
        def run(self, *args: object) -> list[object]:
            raise RuntimeError("sensitive image content")

    result = OnnxClassifier(Broken(None), spec_for()).predict(png())
    assert result.label == "unknown"
    assert result.reason == "model_error"


@pytest.mark.parametrize(
    "update",
    [
        {"labels": ["dry"] * 6},
        {"std": [1, 0, 1]},
        {"mean": [float("nan"), 0, 0]},
        {"output": "guess"},
        {"layout": "NHWC"},
        {"color": "BGR"},
        {"width": 0},
    ],
)
def test_export_manifest_cannot_silently_guess_preprocessing(
    update: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        spec_for(**update)


@pytest.mark.parametrize(
    "update", [{"width": 33}, {"input_name": "other"}, {"output_name": "other"}]
)
def test_manifest_must_match_model_metadata(update: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        OnnxClassifier(FakeSession(None), spec_for(**update))


def test_tiny_real_onnx_model_runs_on_cpu_without_writing_files() -> None:
    logits = onnx.helper.make_tensor(
        "values", onnx.TensorProto.FLOAT, [1, 6], [0, 20, 0, 0, 0, 0]
    )
    graph = onnx.helper.make_graph(
        [onnx.helper.make_node("Constant", [], ["scores"], value=logits)],
        "worker-smoke",
        [
            onnx.helper.make_tensor_value_info(
                "frame", onnx.TensorProto.FLOAT, [1, 3, 24, 32]
            )
        ],
        [onnx.helper.make_tensor_value_info("scores", onnx.TensorProto.FLOAT, [1, 6])],
    )
    model = onnx.helper.make_model(
        graph, opset_imports=[onnx.helper.make_opsetid("", 17)], ir_version=9
    ).SerializeToString()
    classifier = OnnxClassifier.load(
        model, spec_for(sha256=hashlib.sha256(model).hexdigest())
    )
    assert classifier.session.get_providers() == ["CPUExecutionProvider"]
    assert classifier.predict(png()).label == "wet"


def test_model_checksum_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="checksum"):
        OnnxClassifier.load(b"not the approved export", spec_for())


def test_model_download_and_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"model"))
    ) as client:
        assert download_model(client, "https://example.org/model.onnx") == b"model"
        monkeypatch.setattr(inference, "MAX_MODEL_BYTES", 4)
        with pytest.raises(ValueError, match="limit"):
            download_model(client, "https://example.org/model.onnx")
        with pytest.raises(ValueError, match="HTTPS"):
            download_model(client, "http://example.org/model.onnx")


def training_labels() -> dict[str, object]:
    # The actual labels.json shape written by OP-202 at f8879bf; no torch import.
    return {
        "classes": list(LABELS),
        "input": {
            "name": "image",
            "layout": "NCHW",
            "size_px": 224,
            "resize": "stretch to size_px x size_px",
            "color": "RGB",
            "scale": "pixel / 255",
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
        },
        "output": {"name": "logits", "softmax": "apply in the worker"},
    }


def test_worker_consumes_op202_labels_json_without_another_export_format() -> None:
    metadata = training_labels()
    spec = ModelSpec.from_training_labels(metadata, sha256="a" * 64, min_score=0.7)
    assert spec.input_name == "image" and spec.output_name == "logits"
    assert spec.width == spec.height == 224
    assert spec.mean == (0.485, 0.456, 0.406)
    assert spec.labels == LABELS
    assert spec.min_score == 0.7
    assert spec.pixel_scale == 1 / 255


@pytest.mark.parametrize(
    "field,value", [("resize", "center crop"), ("scale", "unknown"), ("color", "BGR")]
)
def test_changed_training_transforms_fail_instead_of_silently_mismatching(
    field: str, value: str
) -> None:
    metadata = training_labels()
    metadata["input"][field] = value
    with pytest.raises(ValueError, match="preprocessing"):
        ModelSpec.from_training_labels(metadata, sha256="a" * 64, min_score=0.7)


def test_invalid_native_model_load_is_sanitised() -> None:
    model = b"invalid ONNX graph"
    with pytest.raises(ValueError, match="initialise"):
        OnnxClassifier.load(model, spec_for(sha256=hashlib.sha256(model).hexdigest()))
