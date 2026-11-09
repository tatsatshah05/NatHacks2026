"""Camera guide for blind and low-vision users (OP-418, a preview).

``POST /api/guide/frame`` (``guide_api.py``) takes one camera frame and the route
context and answers one short spoken sentence plus up to two structured hazards.
This module holds the request and response models, the per-client and global limits,
the post-filter and ``describe_frame``, which asks Claude and applies the rules.

PIP describes, it does not decide
    The model speaks as PIP, a calm companion walking beside the user: one sentence in
    everyday words, at most 20 words and 120 characters (``MAX_SAY_WORDS``,
    ``MAX_SAY_CHARS``; ``truncate_say`` cuts a longer one at a word boundary). It never
    writes turn instructions beyond confirming the context it is given, and the spoken
    sentence never contains "safe to", "safe", "go now", "cross now" or "clear to":
    ``filter_answer`` replaces such a sentence with "" (the hazards are kept) and logs
    ``WARN guide: filtered answer``. "black ice" is never asserted either, but that
    sentence is kept and rewritten, "black ice" becoming "ice" (logged the same way).
    A sentence equal to ``previous`` (the last one spoken) becomes "", so PIP repeats
    only when something changed. The guide does not replace a cane or a guide dog,
    and the web app says so.

The frame sent to the model
    ``downscale_frame`` shrinks a frame wider than 480 px to 480 px wide (JPEG, in
    memory) before the call: the walkway is still readable and the request is smaller.
    ``landmarks`` stays in the response as an empty list; the model no longer returns
    them (November 9: a smaller answer is a quicker one).

The demo never depends on it
    Without ``ANTHROPIC_API_KEY``, in replay mode, or while the Claude call is paused
    after three failures in a row (two minutes), the API answers 503 with one plain
    sentence and the web app keeps speaking the route steps. One failed call gives a
    200 with ``checked_by`` empty, ``say`` "" and ``confidence`` low: the next frame
    comes anyway.

No stored images
    The frame is bytes in memory for one request and is dropped with it. Nothing here
    writes it to disk, a database or a log. The one log line per request carries the
    first 8 characters of a SHA-256 of the client id, the latency, two counts and the
    model's output and cached token counts (to tune ``GUIDE_MAX_TOKENS`` and check
    that the system prompt is cached).

What is sent to the Anthropic API
    The frame and the route context: the profile, the current and next step, the
    distance to the turn, the heading, the off-route distance, the nearby barriers and
    the last sentence spoken. The position (``lat``, ``lng``) and the ``client_id``
    are never sent.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except (
    ImportError
):  # Pillow ships in cloud/requirements-photo-check.txt; without it the guide is off
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    UnidentifiedImageError = OSError  # type: ignore[assignment,misc]
from pydantic import BaseModel, ConfigDict, Field

from .rate_limit import RateLimiter
from .reports import CLIENT_ID_PATTERN

log = logging.getLogger("openpath.guide")

DEFAULT_MODEL = "claude-haiku-5-5"
TIMEOUT_S = 6.0  # one attempt, no retry: the next frame comes anyway
FAILURES_BEFORE_PAUSE = 3
PAUSE_S = 120.0

MAX_FRAME_BYTES = 1_500_000
MAX_SAY_CHARS = 120
MAX_SAY_WORDS = 20
MAX_HAZARDS = 2  # the web vibrates on the kinds; two is what the sentence can carry
MAX_NEARBY = 5
MAX_TEXT_CHARS = 400  # a step, the previous sentence, a nearby title
PER_CLIENT_PER_MIN = 30
TOTAL_PER_MIN = 600

# The frame the model sees: at most this wide, re-encoded as JPEG in memory.
FRAME_WIDTH_PX = 480
FRAME_JPEG_QUALITY = 80

# Any spoken sentence with one of these is dropped (lower-case substring check, so
# "unsafe" and "safely" go too: the guide describes, it never clears anyone to move).
# "clear to" is a phrase on purpose: "Clear path ahead" is a description and stays.
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "safe to",
    "safe",
    "go now",
    "cross now",
    "clear to",
)
# Phrases rewritten instead of dropped: PIP never asserts black ice, but "that looks
# like black ice" is still worth hearing as "that looks like ice".
REWRITES: tuple[tuple[str, str], ...] = (("black ice", "ice"),)

Profile = Literal["wheelchair", "walker_cane", "low_vision", "standard"]
HazardKind = Literal[
    "stairs_down",
    "stairs_up",
    "curb",
    "off_sidewalk",
    "vehicle",
    "bike",
    "person",
    "animal",
    "pole",
    "construction",
    "hole",
    "water_or_ice",
    "door",
    "crossing",
    "sign",
    "other",
]
HAZARD_KINDS: tuple[str, ...] = get_args(HazardKind)
Where = Literal["left", "right", "ahead", "unknown"]
WHERES: tuple[str, ...] = get_args(Where)
GuideConfidence = Literal["low", "medium", "high"]
CONFIDENCES: tuple[str, ...] = get_args(GuideConfidence)
GuideMode = Literal["live", "replay"]
CheckName = Literal["claude"]
FrameFormat = Literal["jpeg", "png"]


# ------------------------------------------------------------------- the models


class NearbyItem(BaseModel):
    """One known barrier near the walker, from the route report."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=MAX_TEXT_CHARS, description="The item's title.")
    kind: str = Field(max_length=64, description="The obstruction kind.")
    distance_m: float = Field(ge=0, description="Distance from the walker in metres.")


class GuideContext(BaseModel):
    """The ``context`` form field of ``POST /api/guide/frame``: where the walker is on
    the route and what was last said. Unknown fields are rejected (422)."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(
        pattern=CLIENT_ID_PATTERN,
        description="The device's anonymous id, the same one the Report button uses.",
    )
    profile: Profile
    step: str | None = Field(
        default=None, max_length=MAX_TEXT_CHARS, description="The current step text."
    )
    next_step: str | None = Field(
        default=None, max_length=MAX_TEXT_CHARS, description="The next step text."
    )
    distance_to_turn_m: float | None = Field(
        default=None, ge=0, description="Metres to the next turn, when known."
    )
    heading_deg: float | None = Field(
        default=None,
        ge=0,
        le=360,
        description="Compass heading of the camera: 0 is north, clockwise.",
    )
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    off_route_m: float | None = Field(
        default=None, ge=0, description="Distance from the route line in metres."
    )
    nearby: list[NearbyItem] = Field(
        default_factory=list,
        max_length=MAX_NEARBY,
        description="Known barriers near the walker, at most 5.",
    )
    previous: str | None = Field(
        default=None,
        max_length=MAX_TEXT_CHARS,
        description="The last sentence the guide spoke, so only what is new is said.",
    )


class GuideHazard(BaseModel):
    """One thing in the frame a walker should know about."""

    kind: HazardKind
    where: Where = Field(description="Left, right or ahead, relative to the camera.")
    text: str = Field(description="One short plain phrase.")


class GuideFrameResponse(BaseModel):
    """The ``POST /api/guide/frame`` body."""

    say: str = Field(
        max_length=MAX_SAY_CHARS,
        description="One short sentence to speak, at most 20 words and 120 characters; "
        '"" when nothing changed since the last sentence or the frame is unusable.',
    )
    hazards: list[GuideHazard] = Field(
        description="At most two, the most important first."
    )
    landmarks: list[str] = Field(
        description="Readable signs, storefronts, doors. Always empty since November 9: "
        "the model no longer returns landmarks, so it answers faster."
    )
    confidence: GuideConfidence
    checked_by: list[CheckName] = Field(
        description='["claude"] when the model answered; [] when it did not '
        '(then say is "" and confidence is low).'
    )
    latency_ms: int = Field(description="Server time spent on this frame.")
    mode: GuideMode = Field(description="The API's data mode (always live here).")


# -------------------------------------------------------------------- the frame


def frame_format(frame: bytes) -> FrameFormat | None:
    """``jpeg`` or ``png`` when Pillow reads the frame's header as one, else None.

    ``Image.open`` reads only the header; the pixels are never decoded here. Without
    Pillow every frame is unreadable, and ``build_guide_checker`` has already turned
    the guide off.
    """
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(frame)) as image:
            found = image.format
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if found == "JPEG":
        return "jpeg"
    if found == "PNG":
        return "png"
    return None


def client_hash(client_id: str) -> str:
    """The first 8 hex characters of a SHA-256 of the client id, for the log line."""
    return hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:8]


def downscale_frame(frame: bytes, width_px: int = FRAME_WIDTH_PX) -> bytes:
    """The frame at most ``width_px`` wide, as a JPEG, for the model.

    A frame that is already narrow enough is returned as it came, byte for byte. A
    wider one is decoded in memory, turned the way its EXIF orientation says, shrunk
    to ``width_px`` wide with the height in proportion and re-encoded as a JPEG (about
    5 ms for a 1280 by 720 frame on a laptop, measured November 9); nothing is written
    anywhere. Any failure returns the original frame: a frame the model can read is
    better than none.
    """
    if Image is None:
        return frame
    try:
        with Image.open(io.BytesIO(frame)) as image:
            upright = ImageOps.exif_transpose(image) or image
            if upright.width <= width_px:
                return frame
            upright.thumbnail((width_px, width_px * 8))
            out = io.BytesIO()
            upright.convert("RGB").save(out, format="JPEG", quality=FRAME_JPEG_QUALITY)
    except (UnidentifiedImageError, OSError, ValueError):
        return frame
    return out.getvalue()


# ------------------------------------------------------------------- the limits


class GuideLimited(Exception):
    """Too many frames from one client, or too many in total, this minute."""

    def __init__(self, scope: Literal["client", "total"], retry_after_s: int) -> None:
        super().__init__(scope)
        self.scope = scope
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class GuideLimits:
    """Fixed one-minute windows in memory, per process (``rate_limit.RateLimiter``).

    30 frames per minute per ``client_id`` and 600 per minute for everyone, which is
    the contract on issue #192; neither is read from the environment.
    """

    client: RateLimiter
    total: RateLimiter

    @classmethod
    def default(cls, clock: Callable[[], float] = time.monotonic) -> GuideLimits:
        return cls(
            RateLimiter(PER_CLIENT_PER_MIN, clock=clock),
            RateLimiter(TOTAL_PER_MIN, clock=clock),
        )

    def check(self, client_id: str) -> None:
        """Count one frame; raise ``GuideLimited`` when a limit is reached."""
        mine = self.client.check(client_id)
        if not mine.allowed:
            raise GuideLimited("client", mine.retry_after_s)
        everyone = self.total.check("everyone")
        if not everyone.allowed:
            raise GuideLimited("total", everyone.retry_after_s)


# -------------------------------------------------------------- the Claude side


@dataclass(frozen=True)
class GuideAnswer:
    """What the model said about one frame, before the filter. Raw on purpose: the
    filter below decides what of it reaches the walker."""

    say: Any
    hazards: Any
    confidence: Any
    model: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0


class GuideChecker(Protocol):
    """Describes one frame held in memory. None means the model gave no answer."""

    name: str

    @property
    def paused(self) -> bool:
        """True while the call is paused after repeated failures (the API says 503)."""
        ...

    def describe(self, frame: bytes, context: GuideContext) -> GuideAnswer | None: ...


# The context fields the model sees. Never the position or the client id.
CONTEXT_FIELDS_SENT: tuple[str, ...] = (
    "profile",
    "step",
    "next_step",
    "distance_to_turn_m",
    "heading_deg",
    "off_route_m",
    "nearby",
    "previous",
)


def context_for_model(context: GuideContext) -> str:
    """The user message that goes with the frame: the route context as JSON."""
    sent = context.model_dump(mode="json", include=set(CONTEXT_FIELDS_SENT))
    return (
        "Route context (JSON): "
        + json.dumps(sent, ensure_ascii=False, separators=(",", ":"))
        + "\nDescribe this frame."
    )


class ClaudeGuideChecker:
    """``vision.ai_check.ClaudeCheck(subject="guide_frame")`` as a GuideChecker."""

    def __init__(self, check: Any) -> None:
        self.check = check
        self.name = f"claude:{getattr(check, 'model', 'unknown')}"

    @property
    def paused(self) -> bool:
        return bool(self.check.paused)

    def describe(self, frame: bytes, context: GuideContext) -> GuideAnswer | None:
        result = self.check.describe(downscale_frame(frame), context_for_model(context))
        if result is None:
            return None
        return GuideAnswer(
            say=result.say,
            hazards=result.hazards,
            confidence=result.confidence,
            model=result.model,
            usage=result.usage,
            cost_usd=result.cost_usd,
        )


def build_guide_checker(env: Mapping[str, str] | None = None) -> GuideChecker | None:
    """Claude when ``ANTHROPIC_API_KEY`` is set (``GUIDE_AI_MODEL`` picks the model),
    else None, and the endpoint answers 503. Never raises."""
    env = os.environ if env is None else env
    if not env.get("ANTHROPIC_API_KEY"):
        log.info("GUIDE Claude is not configured; POST /api/guide/frame answers 503")
        return None
    if Image is None:
        log.warning(
            "GUIDE Pillow is not installed (cloud/requirements-photo-check.txt); "
            "POST /api/guide/frame answers 503"
        )
        return None
    try:
        from vision.ai_check import ClaudeCheck

        checker = ClaudeGuideChecker(
            ClaudeCheck(
                env.get("GUIDE_AI_MODEL") or DEFAULT_MODEL,
                timeout_s=TIMEOUT_S,
                max_retries=0,
                subject="guide_frame",
                failures_before_pause=FAILURES_BEFORE_PAUSE,
                pause_s=PAUSE_S,
            )
        )
    except Exception as error:  # noqa: BLE001 - any failure means "no guide"
        log.warning("GUIDE Claude is not available (%s)", type(error).__name__)
        return None
    log.info("GUIDE camera guide ready (%s)", checker.name)
    return checker


# ------------------------------------------------------------------- the filter

_SPACES = re.compile(r"\s+")


def _one_line(value: Any, limit: int) -> str:
    """Text on one line, trimmed to ``limit`` characters; "" for anything not text."""
    if not isinstance(value, str):
        return ""
    return _SPACES.sub(" ", value).strip()[:limit]


_REWRITES = tuple(
    (phrase, re.compile(re.escape(phrase), re.IGNORECASE), replacement)
    for phrase, replacement in REWRITES
)
_TRAILING_PUNCTUATION = " .,;:!?"


def forbidden_phrase(text: str) -> str | None:
    """The first forbidden phrase in ``text`` (case-insensitive), or None."""
    lowered = _SPACES.sub(" ", text.lower())
    return next((phrase for phrase in FORBIDDEN_PHRASES if phrase in lowered), None)


def rewrite_phrases(text: str) -> tuple[str, list[str]]:
    """``text`` with every ``REWRITES`` phrase replaced (case-insensitive), and the
    phrases that were found. A sentence that opened with the phrase keeps its capital."""
    found: list[str] = []
    for phrase, pattern, replacement in _REWRITES:
        rewritten, count = pattern.subn(replacement, text)
        if count:
            found.append(phrase)
            if text[:1].isupper() and rewritten[:1].islower():
                rewritten = rewritten[:1].upper() + rewritten[1:]
            text = rewritten
    return text, found


def truncate_say(
    text: str, limit: int = MAX_SAY_CHARS, words: int = MAX_SAY_WORDS
) -> str:
    """At most ``words`` words and ``limit`` characters, cut at a word boundary."""
    parts = text.split(" ")
    if len(parts) > words:
        text = " ".join(parts[:words])
    if len(text) > limit:
        text = text[:limit]
        if " " in text:
            text = text.rsplit(" ", 1)[0]
    return text.rstrip(" ,;:")


def same_as_previous(say: str, previous: str | None) -> bool:
    """True when ``say`` repeats the last sentence spoken, give or take case, spacing
    and the punctuation at the end: PIP speaks again only when something changed."""
    if not say or not previous:
        return False

    def plain(text: str) -> str:
        return _SPACES.sub(" ", text).strip(_TRAILING_PUNCTUATION).lower()

    return plain(say) == plain(previous)


def clean_hazards(raw: Any) -> list[GuideHazard]:
    """At most ``MAX_HAZARDS`` hazards with a kind in the vocabulary (others become
    ``other``) and a place. The model no longer sends ``text``; it stays "" then."""
    if not isinstance(raw, list):
        return []
    hazards: list[GuideHazard] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        where = item.get("where")
        hazards.append(
            GuideHazard(
                kind=kind if kind in HAZARD_KINDS else "other",
                where=where if where in WHERES else "unknown",
                text=_one_line(item.get("text"), MAX_SAY_CHARS),
            )
        )
        if len(hazards) == MAX_HAZARDS:
            break
    return hazards


def clean_confidence(raw: Any) -> GuideConfidence:
    return raw if raw in CONFIDENCES else "low"


def filter_answer(
    answer: GuideAnswer, previous: str | None = None
) -> tuple[str, list[GuideHazard], GuideConfidence]:
    """Apply the spoken-sentence rules to a raw answer.

    "black ice" is rewritten to "ice" (logged). A sentence with a forbidden phrase
    becomes "" (logged); the hazards are kept either way. The sentence is cut to 20
    words and 120 characters at a word boundary, and a sentence that repeats
    ``previous`` becomes "" so nothing is said twice.
    """
    say, rewritten = rewrite_phrases(_one_line(answer.say, 4 * MAX_SAY_CHARS))
    for phrase in rewritten:
        # The phrase only, never the sentence: it could repeat a sign or a name.
        log.warning("WARN guide: filtered answer (%r rewritten)", phrase)
    say = truncate_say(say)
    hit = forbidden_phrase(say)
    if hit is not None:
        log.warning("WARN guide: filtered answer (%r)", hit)
        say = ""
    if same_as_previous(say, previous):
        say = ""
    return say, clean_hazards(answer.hazards), clean_confidence(answer.confidence)


# ----------------------------------------------------------------- one request


def describe_frame(
    frame: bytes,
    context: GuideContext,
    checker: GuideChecker,
    mode: GuideMode,
    clock: Callable[[], float] = time.monotonic,
) -> GuideFrameResponse:
    """Ask the model, filter its answer, log one line and drop the frame.

    Called in a thread pool: the Claude call takes up to ``TIMEOUT_S``. The caller
    has already answered 503 when ``checker`` is None or paused.
    """
    started = clock()
    try:
        answer = checker.describe(frame, context)
    except Exception:  # a failing check never breaks the route steps
        log.exception("GUIDE check failed (%s)", checker.name)
        answer = None
    if answer is None:
        say, hazards, confidence = "", [], "low"
        checked_by: list[CheckName] = []
        usage: Mapping[str, int] = {}
    else:
        say, hazards, confidence = filter_answer(answer, context.previous)
        checked_by = ["claude"]
        usage = answer.usage
    latency_ms = round((clock() - started) * 1000)
    log.info(
        "GUIDE client=%s latency_ms=%d hazards=%d say_chars=%d tokens_out=%d cache_read=%d",
        client_hash(context.client_id),
        latency_ms,
        len(hazards),
        len(say),
        usage.get("output", 0),
        usage.get("cache_read", 0),
    )
    return GuideFrameResponse(
        say=say,
        hazards=hazards,
        landmarks=[],
        confidence=confidence,
        checked_by=checked_by,
        latency_ms=latency_ms,
        mode=mode,
    )
