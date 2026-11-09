"""``POST /api/guide/frame`` (OP-418) with a fake Claude: no network, no stored image."""

from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from cloud.app import guide, guide_api
from cloud.app.guide import (
    HAZARD_KINDS,
    MAX_FRAME_BYTES,
    ClaudeGuideChecker,
    GuideAnswer,
    GuideContext,
    GuideFrameResponse,
    GuideLimits,
    build_guide_checker,
    client_hash,
    context_for_model,
    filter_answer,
    truncate_say,
)
from cloud.app.rate_limit import RateLimiter
from vision.ai_check import (
    GUIDE_CONFIDENCES,
    GUIDE_HAZARD_KINDS,
    GUIDE_SCHEMA,
    GUIDE_WHERES,
    ClaudeCheck,
)

CLIENT_ID = "3f9c2a7d1e8b4c6f9a0d2e4b6c8f1a3d"
GOOD_ANSWER: dict[str, Any] = {
    "say": "I see a glass door about two metres ahead on your left.",
    "hazards": [{"kind": "door", "where": "left", "text": "glass door ahead left"}],
    "landmarks": ["104 Street sign"],
    "confidence": "high",
}


def jpeg(width: int = 32) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, 24), (90, 90, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (90, 90, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def context(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "client_id": CLIENT_ID,
        "profile": "low_vision",
        "step": "Walk north on 104 Street",
        "next_step": "Turn right onto Jasper Avenue",
        "distance_to_turn_m": 18.5,
        "heading_deg": 2.0,
        "lat": 53.5405,
        "lng": -113.5015,
        "off_route_m": 3.2,
        "nearby": [
            {"title": "Sidewalk closed", "kind": "sidewalk_closed", "distance_m": 40}
        ],
        "previous": "There's a bike rack on your right.",
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------- the fake Claude


class FakeMessages:
    """Stands in for ``anthropic.Anthropic().messages``; records every request."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def fake_client(response: Any) -> tuple[Any, FakeMessages]:
    messages = FakeMessages(response)
    return SimpleNamespace(
        messages=messages, beta=SimpleNamespace(messages=messages)
    ), (messages)


def response(text: str, stop: str = "end_turn") -> Any:
    usage = SimpleNamespace(
        input_tokens=900,
        cache_read_input_tokens=1200,
        cache_creation_input_tokens=0,
        output_tokens=80,
    )
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=usage,
        model="claude-haiku-5-5",
    )


def connection_error() -> Exception:
    import anthropic
    import httpx

    return anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def claude(
    answer: Any, clock: FakeClock | None = None
) -> tuple[ClaudeGuideChecker, FakeMessages]:
    """A guide checker over a fake Anthropic client that answers ``answer``."""
    text = answer if isinstance(answer, str | Exception) else json.dumps(answer)
    client, messages = fake_client(
        text if isinstance(text, Exception) else response(text)
    )
    check = ClaudeCheck(
        "claude-haiku-5-5",
        client=client,
        clock=clock or FakeClock(),
        subject="guide_frame",
        max_retries=0,
        failures_before_pause=guide.FAILURES_BEFORE_PAUSE,
        pause_s=guide.PAUSE_S,
    )
    return ClaudeGuideChecker(check), messages


class StubChecker:
    """A GuideChecker that answers from a prepared GuideAnswer (or None)."""

    name = "stub"

    def __init__(self, answer: GuideAnswer | None, paused: bool = False) -> None:
        self.answer = answer
        self.paused = paused
        self.frames: list[int] = []

    def describe(self, frame: bytes, context: GuideContext) -> GuideAnswer | None:
        self.frames.append(len(frame))
        return self.answer


# ---------------------------------------------------------------------- the app


def make_app(
    checker: Any = None, mode: str = "live", limits: GuideLimits | None = None
) -> FastAPI:
    """A small app with the guide router only: no database, no scheduler."""
    app = FastAPI()
    app.state.mode = mode
    app.state.guide_checker = checker
    if limits is not None:
        app.state.guide_limits = limits
    app.include_router(guide_api.router)
    return app


def post(
    client: TestClient,
    frame: bytes | None = jpeg(),
    ctx: dict[str, Any] | str | None = None,
    **extra: Any,
) -> Any:
    files = {"frame": ("frame.jpg", frame, "image/jpeg")} if frame is not None else {}
    data: dict[str, str] = {}
    if ctx is None:
        ctx = context()
    if ctx != "":
        data["context"] = ctx if isinstance(ctx, str) else json.dumps(ctx)
    data.update(extra)
    return client.post("/api/guide/frame", files=files, data=data)


# ----------------------------------------------------------------- the answers


def test_a_good_answer_gives_the_exact_response_shape() -> None:
    checker, messages = claude(GOOD_ANSWER)
    got = post(TestClient(make_app(checker)))
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == {
        "say",
        "hazards",
        "landmarks",
        "confidence",
        "checked_by",
        "latency_ms",
        "mode",
    }
    assert body["say"] == GOOD_ANSWER["say"]
    assert body["hazards"] == GOOD_ANSWER["hazards"]
    assert body["landmarks"] == ["104 Street sign"]
    assert body["confidence"] == "high"
    assert body["checked_by"] == ["claude"]
    assert isinstance(body["latency_ms"], int) and body["latency_ms"] >= 0
    assert body["mode"] == "live"
    assert len(messages.requests) == 1
    GuideFrameResponse.model_validate(body)


def test_the_frame_the_context_and_previous_reach_the_model() -> None:
    checker, messages = claude(GOOD_ANSWER)
    frame = jpeg()
    ctx = context(previous="There's a bike rack on your right.")
    assert post(TestClient(make_app(checker)), frame, ctx).status_code == 200
    request = messages.requests[0]
    assert request["model"] == "claude-haiku-5-5"
    assert request["output_config"]["format"] == {
        "type": "json_schema",
        "schema": GUIDE_SCHEMA,
    }
    system = request["system"][0]["text"]
    assert "You describe, you do not decide" in system
    assert "ice risk" in system and '"previous"' in system
    content = request["messages"][0]["content"]
    image, text = content[0], content[1]["text"]
    assert image["source"]["media_type"] == "image/jpeg"
    assert image["source"]["data"]  # the frame itself, base64
    assert "There's a bike rack on your right." in text  # previous is passed
    assert "Turn right onto Jasper Avenue" in text and "Sidewalk closed" in text
    assert "low_vision" in text and '"off_route_m":3.2' in text
    # The position and the client id are never sent to the model.
    assert "53.54" not in text and "113.50" not in text and CLIENT_ID not in text


def test_context_for_model_sends_only_the_agreed_fields() -> None:
    sent = json.loads(
        context_for_model(GuideContext.model_validate(context()))
        .removeprefix("Route context (JSON): ")
        .split("\n", 1)[0]
    )
    assert set(sent) == set(guide.CONTEXT_FIELDS_SENT)
    assert "lat" not in sent and "lng" not in sent and "client_id" not in sent


@pytest.mark.parametrize(
    "say",
    [
        "It is safe to cross now.",
        "The path looks Safe ahead.",
        "Go now, the light is green.",
        "You are clear to continue.",
        "Looks like black ice ahead.",
        "The crossing is unsafe.",
    ],
)
def test_forbidden_phrases_are_filtered_and_logged(say: str, caplog: Any) -> None:
    answer = dict(GOOD_ANSWER, say=say)
    checker, _ = claude(answer)
    with caplog.at_level(logging.WARNING, logger="openpath.guide"):
        got = post(TestClient(make_app(checker)))
    assert got.status_code == 200
    body = got.json()
    assert body["say"] == ""
    assert body["hazards"] == GOOD_ANSWER["hazards"]  # kept
    assert body["landmarks"] == GOOD_ANSWER["landmarks"]  # kept
    assert body["checked_by"] == ["claude"]
    assert "WARN guide: filtered answer" in caplog.text
    assert say not in caplog.text  # the sentence itself is never logged


def test_an_answer_that_is_not_json_gives_an_empty_sentence() -> None:
    checker, messages = claude("I see a door on the left.")
    got = post(TestClient(make_app(checker)))
    assert got.status_code == 200
    assert got.json() == {
        "say": "",
        "hazards": [],
        "landmarks": [],
        "confidence": "low",
        "checked_by": [],
        "latency_ms": got.json()["latency_ms"],
        "mode": "live",
    }
    assert len(messages.requests) == 1


def test_one_failed_call_gives_200_with_nothing_checked() -> None:
    checker, _ = claude(connection_error())
    body = post(TestClient(make_app(checker))).json()
    assert body["checked_by"] == [] and body["say"] == ""
    assert body["confidence"] == "low"


def test_the_sentence_is_cut_at_a_word_boundary() -> None:
    long = " ".join(["word"] * 60)  # 299 characters
    cut = truncate_say(long)
    assert len(cut) <= 160 and not cut.endswith(" ") and cut.endswith("word")
    assert truncate_say("short") == "short"
    answer = GuideAnswer(say=long, hazards=[], landmarks=[], confidence="medium")
    say, *_ = filter_answer(answer)
    assert say == cut


def test_unknown_hazard_kinds_become_other_and_odd_values_are_dropped() -> None:
    answer = GuideAnswer(
        say="I see stairs going down ahead.",
        hazards=[
            {"kind": "escalator", "where": "ahead", "text": "steps"},
            {"kind": "stairs_down", "where": "behind", "text": 12},
            "not a hazard",
        ],
        landmarks=["Tim Hortons", 7, "  "],
        confidence="certain",
    )
    say, hazards, landmarks, confidence = filter_answer(answer)
    assert [h.model_dump() for h in hazards] == [
        {"kind": "other", "where": "ahead", "text": "steps"},
        {"kind": "stairs_down", "where": "unknown", "text": ""},
    ]
    assert landmarks == ["Tim Hortons"]
    assert confidence == "low"
    assert say == "I see stairs going down ahead."


def test_the_vocabulary_agrees_between_the_api_and_the_prompt() -> None:
    assert HAZARD_KINDS == GUIDE_HAZARD_KINDS
    assert guide.WHERES == GUIDE_WHERES
    assert guide.CONFIDENCES == GUIDE_CONFIDENCES


# ---------------------------------------------------------------- the 503 path


def test_without_a_checker_the_route_answers_503() -> None:
    got = post(TestClient(make_app(None)))
    assert got.status_code == 503
    assert got.json() == {"detail": guide_api.UNAVAILABLE}


def test_replay_mode_answers_503_before_anything_else() -> None:
    checker = StubChecker(GuideAnswer("x", [], [], "high"))
    got = post(TestClient(make_app(checker, mode="replay")))
    assert got.status_code == 503
    assert got.json() == {"detail": "Camera guidance runs only in live mode."}
    assert checker.frames == []


def test_three_failures_pause_the_call_for_two_minutes_and_give_503() -> None:
    clock = FakeClock()
    checker, messages = claude(connection_error(), clock=clock)
    client = TestClient(make_app(checker))
    for _ in range(3):
        assert post(client).json()["checked_by"] == []
    assert len(messages.requests) == 3
    paused = post(client)
    assert (
        paused.status_code == 503 and paused.json()["detail"] == guide_api.UNAVAILABLE
    )
    assert len(messages.requests) == 3  # no call while paused
    clock.now += guide.PAUSE_S - 1
    assert post(client).status_code == 503
    clock.now += 2
    messages.response = response(json.dumps(GOOD_ANSWER))
    assert post(client).json()["say"] == GOOD_ANSWER["say"]
    assert len(messages.requests) == 4


def test_build_guide_checker_needs_the_key_and_reads_the_model(
    monkeypatch: Any,
) -> None:
    assert build_guide_checker({}) is None
    made: list[dict[str, Any]] = []

    import anthropic

    monkeypatch.setattr(
        anthropic, "Anthropic", lambda **kw: made.append(kw) or SimpleNamespace()
    )
    checker = build_guide_checker({"ANTHROPIC_API_KEY": "fake-key-for-tests"})
    assert checker is not None and checker.name == "claude:claude-haiku-5-5"
    assert made == [{"timeout": 6.0, "max_retries": 0}]
    assert checker.check.subject == "guide_frame"
    assert checker.check.pause_s == 120.0 and checker.check.failures_before_pause == 3
    other = build_guide_checker(
        {
            "ANTHROPIC_API_KEY": "fake-key-for-tests",
            "GUIDE_AI_MODEL": "claude-sonnet-5-5",
        }
    )
    assert other is not None and other.name == "claude:claude-sonnet-5-5"
    # An unsupported model name means no guide, never a crash.
    assert (
        build_guide_checker(
            {"ANTHROPIC_API_KEY": "fake-key-for-tests", "GUIDE_AI_MODEL": "gpt"}
        )
        is None
    )


def test_the_full_app_answers_503_without_a_key_and_in_replay(monkeypatch: Any) -> None:
    from cloud.app.main import create_app

    monkeypatch.setenv("SCHEDULER", "off")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DATA_MODE", "live")
    with TestClient(create_app()) as client:
        got = post(client)
        assert got.status_code == 503 and got.json() == {
            "detail": guide_api.UNAVAILABLE
        }
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("REPLAY_SNAPSHOT", "cloud/tests/fixtures")
    with TestClient(create_app()) as client:
        got = post(client)
        assert got.status_code == 503
        assert got.json() == {"detail": "Camera guidance runs only in live mode."}


# ------------------------------------------------------------------ the inputs


def test_a_frame_over_the_limit_is_refused_with_413_before_the_model() -> None:
    checker = StubChecker(GuideAnswer("x", [], [], "high"))
    client = TestClient(make_app(checker))
    big = jpeg() + b"\0" * MAX_FRAME_BYTES  # a real JPEG header, too many bytes
    got = post(client, big)
    assert got.status_code == 413
    assert got.json()["detail"].startswith("The frame is too large.")
    assert checker.frames == []


def test_a_png_is_accepted_and_other_files_get_415() -> None:
    checker = StubChecker(GuideAnswer("I see a door.", [], [], "medium"))
    client = TestClient(make_app(checker))
    assert post(client, png()).status_code == 200
    got = post(client, b"GIF89a not really an image")
    assert got.status_code == 415
    assert got.json() == {"detail": "The frame must be a JPEG or PNG image."}
    assert (
        client.post("/api/guide/frame", json={"context": context()}).status_code == 415
    )
    assert checker.frames == [len(png())]


@pytest.mark.parametrize(
    ("ctx", "loc_tail"),
    [
        (context(extra="field"), "extra"),
        (context(nearby=[{"title": "x", "kind": "k", "distance_m": 1}] * 6), "nearby"),
        (context(profile="segway"), "profile"),
        (context(client_id="short"), "client_id"),
        (context(heading_deg=400), "heading_deg"),
        (context(nearby=[{"title": "x", "kind": "k"}]), "distance_m"),
    ],
)
def test_a_bad_context_gives_422_with_the_field_named(ctx: Any, loc_tail: str) -> None:
    checker = StubChecker(GuideAnswer("x", [], [], "high"))
    got = post(TestClient(make_app(checker)), ctx=ctx)
    assert got.status_code == 422, got.text
    locs = [tuple(item["loc"]) for item in got.json()["detail"]]
    assert all(loc[:2] == ("body", "context") for loc in locs)
    assert any(loc_tail in loc for loc in locs), locs
    assert checker.frames == []


def test_missing_or_malformed_parts_give_422() -> None:
    client = TestClient(make_app(StubChecker(GuideAnswer("x", [], [], "high"))))
    assert post(client, ctx="not json").status_code == 422
    assert post(client, ctx="[1, 2]").status_code == 422
    no_frame = client.post(
        "/api/guide/frame", files={"context": (None, json.dumps(context()))}
    )
    assert no_frame.status_code == 422  # multipart without a frame part
    assert post(client, frame=None).status_code == 415  # not multipart at all
    assert post(client, ctx="").status_code == 422  # no context
    assert post(client, other="x").status_code == 422  # an unknown part


# ------------------------------------------------------------------ the limits


def test_thirty_frames_a_minute_per_client_then_429() -> None:
    clock = FakeClock()
    limits = GuideLimits(
        RateLimiter(guide.PER_CLIENT_PER_MIN, clock=clock),
        RateLimiter(guide.TOTAL_PER_MIN, clock=clock),
    )
    checker = StubChecker(GuideAnswer("I see a door.", [], [], "medium"))
    client = TestClient(make_app(checker, limits=limits))
    for _ in range(30):
        assert post(client).status_code == 200
    refused = post(client)
    assert refused.status_code == 429
    assert refused.headers["retry-after"].isdigit()
    assert "faster than the guide can take them" in refused.json()["detail"]
    # Another client is not affected; after the window this one continues.
    assert post(client, ctx=context(client_id="b" * 32)).status_code == 200
    clock.now += 61
    assert post(client).status_code == 200
    assert len(checker.frames) == 32


def test_six_hundred_frames_a_minute_in_total_then_429() -> None:
    clock = FakeClock()
    limits = GuideLimits(
        RateLimiter(guide.PER_CLIENT_PER_MIN, clock=clock),
        RateLimiter(guide.TOTAL_PER_MIN, clock=clock),
    )
    checker = StubChecker(GuideAnswer("", [], [], "low"))
    client = TestClient(make_app(checker, limits=limits))
    for n in range(600):
        ctx = context(client_id=f"client-{n // 30:04d}-" + "x" * 20)
        assert post(client, ctx=ctx).status_code == 200
    refused = post(client, ctx=context(client_id="fresh-client-" + "y" * 20))
    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        "The guide is busy right now. The route steps still work."
    )


def test_the_limits_are_created_once_per_app() -> None:
    app = make_app(StubChecker(GuideAnswer("", [], [], "low")))
    client = TestClient(app)
    assert post(client).status_code == 200
    first = app.state.guide_limits
    assert post(client).status_code == 200
    assert app.state.guide_limits is first
    assert first.client.limit == 30 and first.total.limit == 600


# ------------------------------------------------------------------- the log


def test_the_log_line_has_counts_and_a_hashed_client_id_only(caplog: Any) -> None:
    checker, _ = claude(GOOD_ANSWER)
    frame = jpeg()
    with caplog.at_level(logging.INFO, logger="openpath.guide"):
        got = post(TestClient(make_app(checker)), frame)
    assert got.status_code == 200
    lines = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("GUIDE")
    ]
    assert len(lines) == 1
    line = lines[0]
    hashed = client_hash(CLIENT_ID)
    assert len(hashed) == 8 and hashed != CLIENT_ID[:8]
    assert line.startswith(f"GUIDE client={hashed} latency_ms=")
    assert f" hazards=1 say_chars={len(GOOD_ANSWER['say'])}" in line
    assert CLIENT_ID not in caplog.text
    assert GOOD_ANSWER["say"] not in caplog.text
    # No frame bytes, in any form.
    import base64

    assert base64.b64encode(frame).decode()[:24] not in caplog.text
    assert "\xff\xd8" not in caplog.text and "JFIF" not in caplog.text


def test_latency_is_a_whole_number_of_milliseconds() -> None:
    clock = FakeClock()
    checker = StubChecker(GuideAnswer("I see a pole.", [], [], "medium"))

    def ticking() -> float:
        clock.now += 0.0123
        return clock.now

    body = guide.describe_frame(
        jpeg(), GuideContext.model_validate(context()), checker, "live", clock=ticking
    )
    assert isinstance(body.latency_ms, int) and body.latency_ms == 12


def test_a_checker_that_raises_never_breaks_the_route(caplog: Any) -> None:
    class Broken:
        name = "broken"
        paused = False

        def describe(self, frame: bytes, context: GuideContext) -> GuideAnswer:
            raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="openpath.guide"):
        got = post(TestClient(make_app(Broken())))
    assert got.status_code == 200 and got.json()["checked_by"] == []
    assert "GUIDE check failed (broken)" in caplog.text


def test_the_openapi_schema_names_the_models_and_the_context() -> None:
    from cloud.app.main import create_app

    schema = create_app().openapi()
    components = schema["components"]["schemas"]
    assert set(components["GuideFrameResponse"]["properties"]) == {
        "say",
        "hazards",
        "landmarks",
        "confidence",
        "checked_by",
        "latency_ms",
        "mode",
    }
    assert components["GuideHazard"]["properties"]["kind"]["enum"] == list(HAZARD_KINDS)
    body = schema["paths"]["/api/guide/frame"]["post"]["requestBody"]
    form = body["content"]["multipart/form-data"]["schema"]
    assert set(form["properties"]) == {"frame", "context"}
    assert "$defs" not in form and "$ref" not in json.dumps(form)
    assert form["properties"]["context"]["properties"]["nearby"]["maxItems"] == 5
