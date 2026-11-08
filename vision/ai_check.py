"""Claude as a second check on camera frames (decided by the lead, November 8, 2026).

The ONNX model (OP-202) looks at every frame first. When it would post a reading,
Claude looks at the same frame with the team's label guide. The worker posts the
reading only when both agree; any disagreement, refusal, error or timeout becomes
`unknown`, so a disagreement can never turn into a false `dry`.

Frames are sent to the Anthropic API in memory and never stored by us. The key is
ANTHROPIC_API_KEY on the worker laptop only; the API on Render never needs it, and
`make dev` and the tests run without it.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic
from PIL import Image

from vision.inference import Classifier, Prediction

log = logging.getLogger("openpath.vision.ai_check")

LABELS = ("dry", "wet", "snow_partial", "snow_covered", "ice", "unusable")
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

SCHEMA = {
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
}


def _system_prompt() -> str:
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    return (
        "You label road-camera frames for OpenPath, an accessible route planner in Edmonton. "
        "Give exactly one label for the paved road surface the camera watches, following this guide.\n\n"
        f"{guide}\n{NIGHT_RULES}\n"
        "Answer with the JSON object only. The reason is one short plain sentence about the road surface."
    )


@dataclass
class AiLabel:
    label: str
    reason: str
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0


class ClaudeCheck:
    """One Claude call per frame; returns None on any failure (the caller posts unknown)."""

    def __init__(
        self,
        model: str,
        client: anthropic.Anthropic | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        if model not in PRICES:
            raise ValueError(f"Unsupported model for the AI check: {model}")
        self.model = model
        self.client = client or anthropic.Anthropic(timeout=timeout_s, max_retries=2)
        self.system = [
            {
                "type": "text",
                "text": _system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def label(self, frame: bytes) -> AiLabel | None:
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
                {"type": "text", "text": "Label this frame."},
            ]
            request: dict[str, Any] = {
                "model": self.model,
                "max_tokens": 2048,
                "system": self.system,
                "messages": [{"role": "user", "content": content}],
                "output_config": {
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": SCHEMA},
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
            return None
        if response.stop_reason == "refusal":
            log.warning("AI check refused")
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if data.get("label") not in LABELS:
            return None
        usage = response.usage
        counts = {
            "input": usage.input_tokens or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "output": usage.output_tokens or 0,
        }
        price_in, price_cache, price_out = PRICES[self.model]
        cost = (
            counts["input"] * price_in
            + counts["cache_write"] * price_in * 1.25
            + counts["cache_read"] * price_cache
            + counts["output"] * price_out
        ) / 1_000_000
        return AiLabel(data["label"], str(data.get("reason", ""))[:200], counts, cost)


class AgreeingClassifier:
    """Posts the model's reading only when Claude agrees; everything else is unknown."""

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
                "AI check disagrees: model %s, Claude %s (%s)",
                first.label,
                second.label,
                second.reason,
            )
            return Prediction("unknown", reason="ai_check_disagrees")
        return first
