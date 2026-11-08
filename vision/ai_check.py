"""Claude as a second check on camera frames (decided by the lead, November 8, 2026).

The ONNX model (OP-202) looks at every frame first. When it would post a reading,
Claude looks at the same frame with the team's label guide. The worker posts the
reading only when both agree; any disagreement, refusal, error or timeout becomes
`unknown`, so a disagreement can never turn into a false `dry`.

A slow or failing API never stalls the worker for long: each call has a 10 s timeout
and one retry, and after 3 API failures in a row the check pauses for 5 minutes
(every frame it would have checked posts `unknown`), then tries again.

Frames are sent to the Anthropic API in memory and never stored by us. The key is
ANTHROPIC_API_KEY on the worker laptop, and on Render for report photos (below);
`make dev` and the tests run without it.

Vehicle incidents (OP-415; lead decision, November 8)
-----------------------------------------------------
The same camera call also answers whether any vehicle in the frame appears to have
lost control (skidded, spun, left the road or stopped after a collision), as the
``incident`` object of the structured answer: ``{"seen": bool, "detail": "<one
line>"}``. It rides along on ``AiLabel.incident`` and ``Prediction.incident`` so the
worker can post it with the reading; it never changes the agreement decision, the
posted label or the number of API calls, and a missing or malformed field is simply
no incident information (``None``). It is a display-only signal whose accuracy is
not measured.

Report photos (OP-308; lead decision, November 8)
-------------------------------------------------
``ClaudeCheck(model, subject="report_photo")`` checks a community report photo instead
of a camera frame: a pedestrian's phone photo of a sidewalk, path or crossing. It has
its own system prompt (``REPORT_PHOTO_RULES``), labels with the same six classes, and
adds ``blocked()``, a yes/no/unclear answer to "Is the walking path in this photo
blocked?", because no road-condition class can confirm a blocked path. The default
subject, ``camera_frame``, sends exactly the request the camera worker always sent.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import anthropic
from PIL import Image

from vision.inference import Classifier, Incident, Prediction
from vision.tools.label import LABELS

log = logging.getLogger("openpath.vision.ai_check")

GUIDE_PATH = Path(__file__).resolve().parent / "LABELS.md"

# Prices per million tokens (input, cache read, output), from the Claude API reference
# cached on 2026-10-06. Used only to report measured cost from response.usage.
PRICES = {
    "claude-opus-5-5": (4.00, 0.20, 20.00),
    "claude-sonnet-5-5": (2.00, 0.20, 10.00),
    "claude-haiku-5-5": (0.10, 0.01, 0.50),
}
# Server-side refusal fallbacks exist for Opus 5.5 and Sonnet 5.5, not Haiku 5.5.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

TIMEOUT_S = 10.0  # per attempt; with one retry a failing frame costs about 20 s at most
MAX_RETRIES = 1
FAILURES_BEFORE_PAUSE = 3
PAUSE_S = 300.0

NIGHT_RULES = """
Additional rules the team agreed after reviewing night frames:
- Only the paved driving surface counts. Snow, frost or bright texture on shoulders, ditches,
  medians, grass or banks is not snow on the road; if the lanes are bare the label is dry or wet.
- In grayscale infrared night frames, roadside grass glows white and looks like snow. Use a snow
  class only when white cover is clearly on the lanes themselves.
- No guessing on snow: if you cannot tell the road from the shoulder, the label is unusable.
- Night is not automatically unusable: a lit or infrared-visible matte road with lane markings is
  dry; long bright streaks or mirror-like reflections are wet.
- Label what the pixels show, never what the weather or place suggests. ice only with clear frost
  or glassy signs; otherwise wet. Unsure whether you can see enough: unusable.
"""

# OP-415: the second question in the same call. Display only; accuracy is not measured.
INCIDENT_RULES = """
Vehicle incident (a separate question, answered in the same JSON object as "incident"):
- Set "seen" to true only when a vehicle in the frame appears to have lost control: it has
  skidded or spun across the lanes, left the road into a ditch, median or barrier, is on its
  side or roof, or has stopped after a collision with another vehicle or an object.
- Normal traffic, a vehicle stopped or parked in a lane or on the shoulder, a plow, a tow truck
  or a work crew is not an incident. Judge the pixels, never the weather or the place. Unsure,
  or no vehicle visible: false.
- "detail" is one short plain sentence: what shows the incident, or that no vehicle appears to
  have lost control.
"""

# The incident object of the camera answer (OP-415). Every property is required so
# the answer always carries it; the parser still treats a missing or malformed field
# as "no incident information", never as an incident and never as an error.
INCIDENT_SCHEMA = {
    "type": "object",
    "properties": {
        "seen": {
            "type": "boolean",
            "description": (
                "True only when a vehicle in the frame appears to have lost control: "
                "skidded, spun, left the road or stopped after a collision."
            ),
        },
        "detail": {
            "type": "string",
            "description": (
                "One short sentence: what shows it, or that no vehicle appears to "
                "have lost control."
            ),
        },
    },
    "required": ["seen", "detail"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "reason": {
            "type": "string",
            "description": "One short sentence: what on the road shows this.",
        },
        "incident": INCIDENT_SCHEMA,
    },
    "required": ["label", "reason", "incident"],
    "additionalProperties": False,
}

INCIDENT_DETAIL_MAX = 200  # characters; the API stores the same limit (OP-415)


def parse_incident(value: Any) -> Incident | None:
    """The ``incident`` object of an answer, or None when it is missing or malformed.

    Never raises: a wrong type, a missing key or a non-boolean ``seen`` is "no
    incident information", so a bad field can neither invent an incident nor touch
    the label the caller decides on. ``detail`` becomes one line of at most 200
    characters.
    """
    if not isinstance(value, dict):
        return None
    seen, detail = value.get("seen"), value.get("detail")
    if not isinstance(seen, bool) or not isinstance(detail, str):
        return None
    return Incident(seen, " ".join(detail.split())[:INCIDENT_DETAIL_MAX])


# What ClaudeCheck looks at: a road-camera frame (the worker) or a report photo (the API).
Subject = Literal["camera_frame", "report_photo"]
SUBJECTS: tuple[Subject, ...] = ("camera_frame", "report_photo")

# The report-photo label schema: the same classes, with the reason about the walking surface.
REPORT_LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "reason": {
            "type": "string",
            "description": "One short sentence: what on the walking surface shows this.",
        },
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}

BlockedAnswer = Literal["yes", "no", "unclear"]
BLOCKED_ANSWERS: tuple[BlockedAnswer, ...] = ("yes", "no", "unclear")
BLOCKED_QUESTION = "Is the walking path in this photo blocked?"
BLOCKED_SCHEMA = {
    "type": "object",
    "properties": {
        "blocked": {"type": "string", "enum": list(BLOCKED_ANSWERS)},
        "reason": {
            "type": "string",
            "description": "One short sentence: what in the photo shows this.",
        },
    },
    "required": ["blocked", "reason"],
    "additionalProperties": False,
}

REPORT_PHOTO_RULES = """
You check photos for OpenPath, an accessible route planner in Edmonton. A person sends a photo
from their phone with a community report of snow, ice or a blocked path, taken where they walk
or roll. Look only at the walking surface in the photo: the sidewalk, path, curb ramp or
pedestrian crossing the person is on or facing. Lawns, boulevards, roofs, parked cars, the sky
and the road for cars are clues at most. You are asked one of two questions.

To label the walking surface, give exactly one class, the first that fits in this order:
1. unusable: the photo does not show a walking surface well enough to judge. For example it is
   dark, blurred or covered by a finger; it shows mostly sky, a wall, a person, a room, a
   screen or an object up close; or it shows no sidewalk, path or crossing at all. Do not guess.
2. snow_covered: snow, packed snow or slush covers the whole walking surface in view, with no
   bare pavement showing through.
3. snow_partial: snow or slush covers part of the walking surface (ruts, a windrow or snowbank
   on the path or curb ramp, snow in the crosswalk) and bare pavement shows elsewhere. Snow only
   on the lawn or boulevard beside a bare sidewalk is not snow on the walking surface.
4. ice: a frozen layer on the walking surface: a glassy or polished sheen that does not look like
   liquid water, white grainy frost, frozen puddles or frozen ruts. Only when you are confident;
   an uncertain glossy surface is wet. Ice under snow is a snow class.
5. wet: bare pavement that is dark and saturated, with reflections, sheen or puddles.
6. dry: bare, matte pavement with no reflections.

To say whether the walking path is blocked, answer:
- yes: something in the photo stops a person walking or using a wheelchair from going along
  the path or across the crossing, for example a fence, barrier, closed sign or construction
  across it, a snowbank or windrow across the path or its curb ramp, or a vehicle or object
  filling its width.
- no: the path in the photo is open, even if it is snowy, icy or narrow.
- unclear: the photo does not show a walking path well enough to tell.

Judge what the pixels show, never what the report, the weather or the place suggests.
Answer with the JSON object only. The reason is one short plain sentence about the walking
surface or what blocks it.
"""


def _system_prompt() -> str:
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    return (
        "You label road-camera frames for OpenPath, an accessible route planner in Edmonton. "
        "Give exactly one label for the paved road surface the camera watches, following this guide.\n\n"
        f"{guide}\n{NIGHT_RULES}\n{INCIDENT_RULES}\n"
        "Answer with the JSON object only. The reason is one short plain sentence about the road surface."
    )


def _report_photo_prompt() -> str:
    """The system prompt for community report photos (subject ``report_photo``)."""
    return REPORT_PHOTO_RULES.strip()


def _prices(answered: str, requested: str) -> tuple[float, float, float]:
    """Prices of the model that answered; the requested model's if it is not in the table."""
    for name, prices in PRICES.items():
        if answered.startswith(name):
            return prices
    return PRICES[requested]


@dataclass
class AiLabel:
    label: str
    reason: str
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    model: str = (
        ""  # the model that answered; differs from the requested one after a fallback
    )
    # OP-415: the vehicle-incident answer from the same call; None when the answer
    # had no usable incident field (always None for report photos).
    incident: Incident | None = None


@dataclass
class AiBlocked:
    """Claude's answer to "Is the walking path in this photo blocked?" (report photos)."""

    blocked: BlockedAnswer
    reason: str
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    model: str = ""


class ClaudeCheck:
    """One Claude call per frame; returns None on any failure (the caller posts unknown)."""

    def __init__(
        self,
        model: str,
        client: anthropic.Anthropic | None = None,
        timeout_s: float = TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
        subject: Subject = "camera_frame",
    ) -> None:
        if model not in PRICES:
            raise ValueError(f"Unsupported model for the AI check: {model}")
        if subject not in SUBJECTS:
            raise ValueError(f"Unsupported subject for the AI check: {subject}")
        self.model = model
        self.subject = subject
        self.client = client or anthropic.Anthropic(
            timeout=timeout_s, max_retries=MAX_RETRIES
        )
        prompt = (
            _system_prompt() if subject == "camera_frame" else _report_photo_prompt()
        )
        self.system = [
            {
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        self._clock = clock
        self._failures = 0  # API failures in a row; any answer from the API resets it
        self._paused_until = 0.0

    def label(self, frame: bytes) -> AiLabel | None:
        """One of LABELS for the frame or photo, or None on any failure."""
        if self.subject == "camera_frame":
            asked = self._ask(frame, "Label this frame.", SCHEMA)
        else:
            asked = self._ask(frame, "Label this photo.", REPORT_LABEL_SCHEMA)
        if asked is None:
            return None
        data, response, answered = asked
        if data.get("label") not in LABELS:
            return None
        counts, cost = self._measure(response, answered)
        return AiLabel(
            data["label"],
            str(data.get("reason", ""))[:200],
            counts,
            cost,
            answered,
            # Only camera frames are asked the incident question (OP-415).
            incident=parse_incident(data.get("incident"))
            if self.subject == "camera_frame"
            else None,
        )

    def blocked(self, photo: bytes) -> AiBlocked | None:
        """Whether the walking path in a report photo is blocked, or None on any failure."""
        if self.subject != "report_photo":
            raise ValueError("The blocked question is only asked about report photos")
        asked = self._ask(photo, BLOCKED_QUESTION, BLOCKED_SCHEMA)
        if asked is None:
            return None
        data, response, answered = asked
        if data.get("blocked") not in BLOCKED_ANSWERS:
            return None
        counts, cost = self._measure(response, answered)
        return AiBlocked(
            data["blocked"], str(data.get("reason", ""))[:200], counts, cost, answered
        )

    def _ask(
        self, frame: bytes, question: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Any, str] | None:
        """One structured call: (the JSON object, the response, the model that answered)."""
        if self._clock() < self._paused_until:
            return None
        try:
            with Image.open(io.BytesIO(frame)) as image:
                media_type = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(
                    image.format or ""
                )
            if media_type is None:
                return None
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(frame).decode("ascii"),
                    },
                },
                {"type": "text", "text": question},
            ]
            request: dict[str, Any] = {
                "model": self.model,
                "max_tokens": 2048,
                "system": self.system,
                "messages": [{"role": "user", "content": content}],
                "output_config": {
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": schema},
                },
            }
            if self.model in FALLBACK_MODELS:
                response = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **request,
                )
            else:
                response = self.client.messages.create(**request)
        except (anthropic.APIError, OSError, ValueError) as exc:
            log.warning("AI check failed: %s", type(exc).__name__)
            if isinstance(exc, anthropic.APIError | OSError):
                self._failed()  # not for ValueError: a frame that is not an image says nothing about the API
            return None
        self._failures = 0
        answered = str(getattr(response, "model", None) or self.model)
        if answered != self.model:
            log.info("AI check answered by %s instead of %s", answered, self.model)
        if response.stop_reason == "refusal":
            log.warning("AI check refused (%s)", answered)
            return None
        if response.stop_reason != "end_turn":
            # For example max_tokens: the answer is cut off and becomes unknown below.
            log.warning(
                "AI check stopped early: %s (%s)", response.stop_reason, answered
            )
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data, response, answered

    def _measure(self, response: Any, answered: str) -> tuple[dict[str, int], float]:
        """Token counts from response.usage and their measured cost in USD."""
        usage = response.usage
        counts = {
            "input": usage.input_tokens or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "output": usage.output_tokens or 0,
        }
        price_in, price_cache, price_out = _prices(answered, self.model)
        cost = (
            counts["input"] * price_in
            + counts["cache_write"] * price_in * 1.25
            + counts["cache_read"] * price_cache
            + counts["output"] * price_out
        ) / 1_000_000
        return counts, cost

    def _failed(self) -> None:
        self._failures += 1
        if self._failures >= FAILURES_BEFORE_PAUSE:
            # The count stays at the threshold until the API answers again, so one more
            # failure after the pause starts the next pause straight away.
            self._paused_until = self._clock() + PAUSE_S
            log.warning(
                "AI check paused for %.0f s after %d API failures in a row",
                PAUSE_S,
                self._failures,
            )


class AgreeingClassifier:
    """Posts the model's reading only when Claude agrees; everything else is unknown.

    OP-415: whenever Claude answered, its vehicle-incident answer rides along on the
    prediction (``Prediction.incident``), whether or not the labels agree. It never
    changes the label, the reason or the number of calls.
    """

    def __init__(self, model: Classifier, check: ClaudeCheck) -> None:
        self.model = model
        self.check = check
        self.version = (
            model.version
        )  # the ONNX SHA-256 stays the reading's model_version

    def predict(self, frame: bytes) -> Prediction:
        first = self.model.predict(frame)
        if first.label == "unknown":
            return first  # dark, unusable or unsure: no API call needed
        second = self.check.label(frame)
        if second is None:
            return Prediction("unknown", reason="ai_check_unavailable")
        if second.label != first.label:
            log.info(
                "AI check disagrees: model %s, Claude %s %s (%s)",
                first.label,
                second.model,
                second.label,
                second.reason,
            )
            return Prediction(
                "unknown", reason="ai_check_disagrees", incident=second.incident
            )
        return replace(first, incident=second.incident)
