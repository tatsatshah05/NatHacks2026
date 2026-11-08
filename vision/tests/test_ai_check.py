"""The Claude agree check (lead decision, November 8): offline, with fake model and API answers."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from vision.ai_check import (
    BLOCKED_QUESTION,
    BLOCKED_SCHEMA,
    LABELS,
    REPORT_LABEL_SCHEMA,
    AgreeingClassifier,
    AiBlocked,
    AiLabel,
    ClaudeCheck,
    _report_photo_prompt,
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


def response(text: str, stop: str = "end_turn", model: str | None = None):
    usage = SimpleNamespace(
        input_tokens=500,
        cache_read_input_tokens=4000,
        cache_creation_input_tokens=0,
        output_tokens=60,
    )
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=usage,
        model=model,
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


def connection_error():
    import anthropic
    import httpx2

    return anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_default_client_keeps_one_frame_short(monkeypatch) -> None:
    from vision import ai_check

    made: list[dict] = []
    monkeypatch.setattr(ai_check.anthropic, "Anthropic", lambda **kw: made.append(kw))
    ClaudeCheck("claude-haiku-5-5")
    assert made == [{"timeout": 10.0, "max_retries": 1}]


def test_three_api_failures_in_a_row_pause_the_check() -> None:
    clock = FakeClock()
    client, messages = fake_client(connection_error())
    check = ClaudeCheck("claude-haiku-5-5", client=client, clock=clock)
    for _ in range(3):
        assert check.label(jpeg()) is None
    assert len(messages.requests) == 3
    assert check.label(jpeg()) is None  # paused: no call, so the pass is not held up
    assert len(messages.requests) == 3

    clock.now += 301  # after the pause one more failure pauses again straight away
    assert check.label(jpeg()) is None
    assert len(messages.requests) == 4
    assert check.label(jpeg()) is None
    assert len(messages.requests) == 4

    clock.now += 301  # an answer ends the pause and resets the count
    messages.response = response(json.dumps({"label": "dry", "reason": "Matte."}))
    assert check.label(jpeg()).label == "dry"
    messages.response = connection_error()
    assert check.label(jpeg()) is None
    assert check.label(jpeg()) is None
    assert len(messages.requests) == 7  # two failures since the answer: not paused


def test_refusals_and_non_images_do_not_count_toward_the_pause() -> None:
    clock = FakeClock()
    client, messages = fake_client(
        response(json.dumps({"label": "dry"}), stop="refusal")
    )
    check = ClaudeCheck("claude-haiku-5-5", client=client, clock=clock)
    for _ in range(4):
        assert check.label(jpeg()) is None
        assert check.label(b"not an image") is None
    assert len(messages.requests) == 4


def test_an_answer_cut_short_is_logged_and_gives_none(caplog) -> None:
    client, _ = fake_client(response("", stop="max_tokens"))
    with caplog.at_level("WARNING", logger="openpath.vision.ai_check"):
        assert ClaudeCheck("claude-haiku-5-5", client=client).label(jpeg()) is None
    assert "stopped early: max_tokens" in caplog.text


def test_the_model_that_answered_is_recorded_and_priced() -> None:
    answer = json.dumps({"label": "wet", "reason": "Streaks."})
    client, _ = fake_client(response(answer, model="claude-sonnet-5-5"))
    got = ClaudeCheck("claude-opus-5-5", client=client).label(jpeg())
    assert got is not None and got.model == "claude-sonnet-5-5"
    assert got.cost_usd == pytest.approx(
        (500 * 2.00 + 4000 * 0.20 + 60 * 10.00) / 1_000_000
    )

    client, _ = fake_client(
        response(answer)
    )  # no model on the response: the requested one
    got = ClaudeCheck("claude-haiku-5-5", client=client).label(jpeg())
    assert got is not None and got.model == "claude-haiku-5-5"


def test_disagreements_name_the_model_that_answered(caplog) -> None:
    check = FakeCheck(AiLabel("wet", "streaks", model="claude-haiku-5-5"))
    with caplog.at_level("INFO", logger="openpath.vision.ai_check"):
        AgreeingClassifier(FakeModel("dry"), check).predict(b"frame")
    assert "Claude claude-haiku-5-5 wet" in caplog.text


def test_labels_come_from_the_label_tool() -> None:
    from vision.tools import label

    assert LABELS is label.LABELS


# ------------------------------------------------- the camera worker is unchanged

PROMPT_SNAPSHOT = Path(__file__).parent / "fixtures" / "camera_system_prompt.txt"


def test_the_camera_system_prompt_is_unchanged() -> None:
    """Snapshot taken from the code before report photos were added (OP-308).

    If LABELS.md or NIGHT_RULES change on purpose, regenerate the fixture with
    ``_system_prompt()`` and review the diff: it changes what the camera worker asks.
    """
    snapshot = PROMPT_SNAPSHOT.read_text(encoding="utf-8")
    assert _system_prompt() == snapshot
    assert ClaudeCheck("claude-haiku-5-5", client=SimpleNamespace()).system == [
        {"type": "text", "text": snapshot, "cache_control": {"type": "ephemeral"}}
    ]


def test_the_camera_request_is_unchanged() -> None:
    client, messages = fake_client(
        response(json.dumps({"label": "dry", "reason": "Matte."}))
    )
    frame = jpeg()
    check = ClaudeCheck("claude-haiku-5-5", client=client)
    assert check.subject == "camera_frame"
    assert check.label(frame).label == "dry"
    (request,) = messages.requests
    assert request == {
        "model": "claude-haiku-5-5",
        "max_tokens": 2048,
        "system": [
            {
                "type": "text",
                "text": PROMPT_SNAPSHOT.read_text(encoding="utf-8"),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.standard_b64encode(frame).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": "Label this frame."},
                ],
            }
        ],
        "output_config": {
            "effort": "low",
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "enum": list(LABELS)},
                        "reason": {
                            "type": "string",
                            "description": "One short sentence: what on the road shows this.",
                        },
                    },
                    "required": ["label", "reason"],
                    "additionalProperties": False,
                },
            },
        },
    }


def test_the_camera_check_never_asks_the_blocked_question() -> None:
    client, messages = fake_client(response("{}"))
    with pytest.raises(ValueError):
        ClaudeCheck("claude-haiku-5-5", client=client).blocked(jpeg())
    assert messages.requests == []


def test_an_unknown_subject_is_rejected() -> None:
    with pytest.raises(ValueError):
        ClaudeCheck("claude-haiku-5-5", client=SimpleNamespace(), subject="drone")  # type: ignore[arg-type]


# ------------------------------------------------------- report photos (OP-308)


def report_check(answer, model: str = "claude-haiku-5-5"):
    client, messages = fake_client(answer)
    return ClaudeCheck(model, client=client, subject="report_photo"), messages


def test_the_report_prompt_is_about_the_walking_surface() -> None:
    prompt = _report_photo_prompt()
    assert "walking surface" in prompt and "phone" in prompt
    assert "does not show a walking surface" in prompt  # unusable
    assert "fence" in prompt and "construction" in prompt and "snowbank" in prompt
    assert "paved road surface the camera watches" not in prompt
    for label in LABELS:
        assert f"{label}:" in prompt  # every class is described
    check, _ = report_check(response("{}"))
    assert check.system == [
        {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}
    ]


def test_a_report_photo_is_labelled_with_its_own_question_and_schema() -> None:
    check, messages = report_check(
        response(json.dumps({"label": "snow_partial", "reason": "Ruts in snow."}))
    )
    got = check.label(jpeg())
    assert (
        got is not None
        and got.label == "snow_partial"
        and got.model == "claude-haiku-5-5"
    )
    request = messages.requests[0]
    assert request["messages"][0]["content"][1] == {
        "type": "text",
        "text": "Label this photo.",
    }
    assert request["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": REPORT_LABEL_SCHEMA},
    }
    assert REPORT_LABEL_SCHEMA["properties"]["label"]["enum"] == list(LABELS)
    assert "fallbacks" not in request  # Haiku 5.5 has no server-side fallback


@pytest.mark.parametrize("answer", ["yes", "no", "unclear"])
def test_the_blocked_question_is_structured_yes_no_unclear(answer: str) -> None:
    check, messages = report_check(
        response(json.dumps({"blocked": answer, "reason": "A fence crosses it."}))
    )
    got = check.blocked(jpeg())
    assert isinstance(got, AiBlocked) and got.blocked == answer
    assert got.cost_usd == pytest.approx(
        (500 * 0.10 + 4000 * 0.01 + 60 * 0.50) / 1_000_000
    )
    request = messages.requests[0]
    assert request["messages"][0]["content"][1]["text"] == BLOCKED_QUESTION
    assert BLOCKED_QUESTION == "Is the walking path in this photo blocked?"
    assert request["output_config"]["format"] == {
        "type": "json_schema",
        "schema": BLOCKED_SCHEMA,
    }
    assert BLOCKED_SCHEMA["properties"]["blocked"]["enum"] == ["yes", "no", "unclear"]


@pytest.mark.parametrize(
    "answer",
    [
        response(json.dumps({"blocked": "yes"}), stop="refusal"),
        response("not json"),
        response(json.dumps({"blocked": "maybe", "reason": "?"})),
        response(json.dumps({"label": "ice", "reason": "wrong schema"})),
    ],
)
def test_refusals_and_bad_blocked_answers_give_none(answer) -> None:
    check, _ = report_check(answer)
    assert check.blocked(jpeg()) is None


def test_blocked_failures_share_the_pause() -> None:
    clock = FakeClock()
    client, messages = fake_client(connection_error())
    check = ClaudeCheck(
        "claude-haiku-5-5", client=client, clock=clock, subject="report_photo"
    )
    assert check.label(jpeg()) is None
    assert check.blocked(jpeg()) is None
    assert check.blocked(jpeg()) is None
    assert len(messages.requests) == 3
    assert check.label(jpeg()) is None  # paused for both questions
    assert check.blocked(jpeg()) is None
    assert len(messages.requests) == 3


def test_opus_report_checks_still_opt_into_refusal_fallbacks() -> None:
    check, messages = report_check(
        response(json.dumps({"blocked": "no", "reason": "Open."})), "claude-opus-5-5"
    )
    assert check.blocked(jpeg()).blocked == "no"
    assert messages.requests[0]["fallbacks"] == "default"
