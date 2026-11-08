"""The snow and ice model beside the main model (lead decision, November 8): offline, with fakes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vision import worker
from vision.ai_check import AgreeingClassifier, AiLabel
from vision.inference import Prediction
from vision.snow_assist import SNOW_MODEL_REASON, SnowAssistClassifier


class FakeModel:
    def __init__(self, label: str, version: str = "a" * 64, score: float = 0.9) -> None:
        self.label, self.version, self.score = label, version, score

    def predict(self, frame: bytes) -> Prediction:
        return Prediction(self.label, score=self.score)


class FakeCheck:
    def __init__(self, label: str) -> None:
        self.answer = label

    def label(self, frame: bytes) -> AiLabel:
        return AiLabel(self.answer, "fake", model="claude-haiku-5-5")


@pytest.mark.parametrize(
    ("main", "snow", "expected", "reason"),
    [
        ("dry", "unknown", "dry", None),
        ("dry", "dry", "dry", None),
        ("wet", "dry", "wet", None),  # the snow model never decides dry or wet
        ("unknown", "wet", "unknown", None),
        ("unknown", "unknown", "unknown", None),
        ("dry", "ice", "ice", SNOW_MODEL_REASON),
        ("unknown", "snow_covered", "snow_covered", SNOW_MODEL_REASON),
        ("wet", "snow_partial", "snow_partial", SNOW_MODEL_REASON),
    ],
)
def test_the_snow_model_decides_only_snow_and_ice(main, snow, expected, reason) -> None:
    got = SnowAssistClassifier(FakeModel(main), FakeModel(snow, score=0.8)).predict(
        b"frame"
    )
    assert got.label == expected
    assert got.reason == reason
    if reason:
        assert got.score == 0.8


def test_the_version_names_both_models_within_the_api_limit() -> None:
    version = SnowAssistClassifier(
        FakeModel("dry", "a" * 64), FakeModel("dry", "b" * 64)
    ).version
    assert version == "a" * 64 + "+snow:" + "b" * 16
    assert len(version) <= 128


def test_claude_must_still_agree_with_a_snow_reading() -> None:
    combined = SnowAssistClassifier(FakeModel("dry"), FakeModel("ice"))
    assert AgreeingClassifier(combined, FakeCheck("ice")).predict(b"f").label == "ice"
    got = AgreeingClassifier(combined, FakeCheck("wet")).predict(b"f")
    assert got.label == "unknown" and got.reason == "ai_check_disagrees"


def live_argv(tmp_path: Path) -> list[str]:
    (tmp_path / "labels.json").write_text("{}")
    (tmp_path / "model.onnx").write_bytes(b"MAIN")
    return [
        "--live",
        "--once",
        "--model-file",
        str(tmp_path / "model.onnx"),
        "--model-labels",
        str(tmp_path / "labels.json"),
        "--model-sha256",
        "a" * 64,
        "--min-score",
        "0.5",
    ]


def test_the_snow_model_flags_go_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATA_MODE", raising=False)
    monkeypatch.setenv("ALBERTA_511_KEY", "test")
    with pytest.raises(SystemExit) as error:
        worker.main(
            [*live_argv(tmp_path), "--snow-model-file", str(tmp_path / "model.onnx")]
        )
    assert error.value.code == 2
    assert "go together" in capsys.readouterr().err


def test_the_worker_wires_the_snow_model_beside_the_main_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DATA_MODE", raising=False)
    monkeypatch.setenv("ALBERTA_511_KEY", "test")
    (tmp_path / "snow.onnx").write_bytes(b"SNOW")
    (tmp_path / "snow_labels.json").write_text("{}")
    monkeypatch.setattr(
        worker.ModelSpec, "from_training_labels", lambda *a, **k: SimpleNamespace()
    )
    monkeypatch.setattr(
        worker.OnnxClassifier,
        "load",
        lambda data, spec: (
            FakeModel("dry", "a" * 64)
            if data == b"MAIN"
            else FakeModel("ice", "b" * 64)
        ),
    )
    monkeypatch.setattr(worker, "CameraAdapter", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(worker, "FrameFetcher", lambda *a, **k: SimpleNamespace())
    captured: dict[str, object] = {}

    def camera_worker(adapter, fetcher, classifier, publisher, **kwargs):
        captured["classifier"] = classifier
        return SimpleNamespace(
            cycle=list, publish_attempted=0, publish_succeeded=0, publish_failed=0
        )

    monkeypatch.setattr(worker, "CameraWorker", camera_worker)
    argv = [
        *live_argv(tmp_path),
        "--snow-model-file",
        str(tmp_path / "snow.onnx"),
        "--snow-model-labels",
        str(tmp_path / "snow_labels.json"),
        "--snow-model-sha256",
        "b" * 64,
    ]
    assert worker.main(argv) == 0
    classifier = captured["classifier"]
    assert isinstance(classifier, SnowAssistClassifier)
    assert classifier.predict(b"frame").label == "ice"
    assert classifier.version == "a" * 64 + "+snow:" + "b" * 16
