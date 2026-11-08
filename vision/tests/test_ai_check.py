"""The Claude agree check (lead decision, November 8): offline, with fake model and API answers."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from vision.ai_check import (
    LABELS,
    AgreeingClassifier,
    AiLabel,
    ClaudeCheck,
    _system_prompt,
)
from vision.inference import Prediction


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (90, 90, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeModel:
    version = "a" * 64

    def __init__(self, label: str) -> None:
        self.label = label

    def predict(self, frame: bytes) -> Prediction:
        return Prediction(self.label, score=0.9)


class FakeCheck:
    def __init__(self, answer: AiLabel | None) -> None:
        self.answer = answer
        self.calls = 0

    def label(self, frame: bytes) -> AiLabel | None:
        self.calls += 1
        return self.answer


@pytest.mark.parametrize(
    ("model_label", "claude", "expected", "reason", "calls"),
    [
        ("dry", AiLabel("dry", "matte lanes"), "dry", None, 1),
        ("wet", AiLabel("wet", "light streaks"), "wet", None, 1),
        ("dry", AiLabel("wet", "streaks"), "unknown", "ai_check_disagrees", 1),
        ("dry", AiLabel("unusable", "too dark"), "unknown", "ai_check_disagrees", 1),
        ("dry", None, "unknown", "ai_check_unavailable", 1),
        ("unknown", AiLabel("dry", "x"), "unknown", None, 0),  # no API call for unknown
    ],
)
def test_a_reading_is_posted_only_when_both_agree(
    model_label, claude, expected, reason, calls
) -> None:
    check = FakeCheck(claude)
    got = AgreeingClassifier(FakeModel(model_label), check).predict(b"frame")
    assert got.label == expected
    if reason:
        assert got.reason == reason
    assert check.calls == calls


def test_version_stays_the_onnx_sha256() -> None:
    assert AgreeingClassifier(FakeModel("dry"), FakeCheck(None)).version == "a" * 64


def test_system_prompt_carries_the_label_guide_and_night_rules() -> None:
    prompt = _system_prompt()
    assert (
        "Decision order" in prompt
        and "infrared" in prompt
        and "No guessing on snow" in prompt
    )


class FakeMessages:
    def __init__(self, response) -> None:
        self.response = response
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def fake_client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(
        messages=messages, beta=SimpleNamespace(messages=messages)
    ), messages


def response(text: str, stop: str = "end_turn"):
    usage = SimpleNamespace(
        input_tokens=500,
        cache_read_input_tokens=4000,
        cache_creation_input_tokens=0,
        output_tokens=60,
    )
    return SimpleNamespace(
        stop_reason=stop, content=[SimpleNamespace(type="text", text=text)], usage=usage
    )


def test_claude_check_parses_the_structured_answer_and_measures_cost() -> None:
    client, messages = fake_client(
        response(json.dumps({"label": "wet", "reason": "Long streaks under lights."}))
    )
    got = ClaudeCheck("claude-haiku-5-5", client=client).label(jpeg())
    assert (
        got is not None and got.label == "wet" and got.reason.startswith("Long streaks")
    )
    assert got.usage == {
        "input": 500,
        "cache_read": 4000,
        "cache_write": 0,
        "output": 60,
    }
    assert got.cost_usd == pytest.approx(
        (500 * 0.10 + 4000 * 0.01 + 60 * 0.50) / 1_000_000
    )
    request = messages.requests[0]
    assert request["output_config"]["format"]["schema"]["properties"]["label"][
        "enum"
    ] == list(LABELS)
    assert request["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"
    assert "fallbacks" not in request  # Haiku 5.5 has no server-side fallback


def test_opus_and_sonnet_requests_opt_into_refusal_fallbacks() -> None:
    client, messages = fake_client(
        response(json.dumps({"label": "dry", "reason": "Matte."}))
    )
    ClaudeCheck("claude-opus-5-5", client=client).label(jpeg())
    assert messages.requests[0]["fallbacks"] == "default"
    assert messages.requests[0]["betas"] == ["server-side-fallback-2026-07-01"]


@pytest.mark.parametrize(
    "answer",
    [
        response(json.dumps({"label": "dry"}), stop="refusal"),
        response("not json"),
        response(json.dumps({"label": "slush", "reason": "?"})),
    ],
)
def test_refusals_and_bad_answers_give_none(answer) -> None:
    client, _ = fake_client(answer)
    assert ClaudeCheck("claude-haiku-5-5", client=client).label(jpeg()) is None


def test_api_errors_give_none() -> None:
    import anthropic
    import httpx2

    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    client, _ = fake_client(error)
    assert ClaudeCheck("claude-haiku-5-5", client=client).label(jpeg()) is None


def test_non_image_bytes_give_none_without_a_call() -> None:
    client, messages = fake_client(response("{}"))
    assert ClaudeCheck("claude-haiku-5-5", client=client).label(b"not an image") is None
    assert messages.requests == []


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(ValueError):
        ClaudeCheck("claude-unknown", client=SimpleNamespace())
