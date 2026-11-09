"""``POST /api/guide/frame``: one camera frame in, one spoken sentence out (OP-418).

``multipart/form-data`` with two parts: ``frame`` (JPEG or PNG, at most 1,500,000
bytes) and ``context`` (JSON text, ``guide.GuideContext``). The body is read here,
capped, and split by ``reports_api.parse_multipart``, never by Starlette's form parser,
which spools uploads over 1 MB to a temporary file. The frame lives in memory for this
request and is dropped with it.

Responses
    200 ``guide.GuideFrameResponse``
    413 the frame (or the body) is too large
    415 not multipart, or the frame is not a JPEG or PNG
    422 the context is not valid (unknown fields, too many ``nearby`` items)
    429 over 30 frames a minute for this client, or 600 in total (``Retry-After``)
    503 no ``ANTHROPIC_API_KEY``, replay mode, or the Claude call is paused after
        repeated failures

This route keeps its own limits (``guide.GuideLimits``) instead of OP-401's
per-connection write limit: at one frame every 2 to 3 seconds the guide alone would
meet that limit, and a Report sent while it runs must still get through.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from . import guide
from .guide import (
    MAX_FRAME_BYTES,
    GuideChecker,
    GuideContext,
    GuideFrameResponse,
    GuideLimited,
    GuideLimits,
    build_guide_checker,
)
from .reports_api import parse_multipart

__all__ = ["build_guide_checker", "router"]

router = APIRouter(prefix="/api/guide", tags=["guide"])

MAX_CONTEXT_BYTES = 8 * 1024  # the context is a few hundred bytes of JSON
# The frame, the context and the multipart framing.
MAX_BODY_BYTES = MAX_FRAME_BYTES + MAX_CONTEXT_BYTES + 4 * 1024

UNAVAILABLE = "Camera guidance is not available right now. The route steps still work."
REPLAY_ONLY = "Camera guidance runs only in live mode."
TOO_LARGE = "The frame is too large. Send a JPEG of at most 1.5 MB."
NOT_AN_IMAGE = "The frame must be a JPEG or PNG image."


# ------------------------------------------------------------------ dependencies


def get_guide_checker(request: Request) -> GuideChecker | None:
    """``app.state.guide_checker``, built in ``create_app``; None means 503."""
    return getattr(request.app.state, "guide_checker", None)


def get_guide_limits(request: Request) -> GuideLimits:
    """``app.state.guide_limits``, created on first use (one per process)."""
    state = request.app.state
    limits = getattr(state, "guide_limits", None)
    if limits is None:
        limits = GuideLimits.default()
        state.guide_limits = limits
    return limits


# ------------------------------------------------------------ reading the body


async def _read_body(request: Request) -> bytes:
    """The request body, refused with a 413 as soon as it passes ``MAX_BODY_BYTES``."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(413, TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(413, TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def _invalid(message: str, loc: tuple[str, ...] = ("body",)) -> RequestValidationError:
    """A 422 in FastAPI's usual ``detail`` shape."""
    return RequestValidationError([{"type": "value_error", "loc": loc, "msg": message}])


def _parts(body: bytes, content_type: str) -> tuple[bytes, bytes]:
    """The ``frame`` and ``context`` parts, or a 422."""
    try:
        parts = parse_multipart(body, content_type)
    except ValueError as error:
        raise _invalid(f"The form body could not be read: {error}.") from None
    frame: bytes | None = None
    context: bytes | None = None
    for part in parts:
        if part.name == "frame" and frame is None:
            frame = part.data
        elif part.name == "context" and context is None:
            context = part.data
        else:
            raise _invalid(
                f"{part.name} is not a field of this request or was sent twice.",
                ("body", part.name),
            )
    if not frame:
        raise _invalid("frame is required: one JPEG or PNG file.", ("body", "frame"))
    if context is None:
        raise _invalid("context is required: a JSON object.", ("body", "context"))
    return frame, context


def _context(raw: bytes) -> GuideContext:
    """The validated context, or a 422 with pydantic's detail."""
    if len(raw) > MAX_CONTEXT_BYTES:
        raise _invalid("context is too long.", ("body", "context"))
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid("context is not valid JSON.", ("body", "context")) from None
    if not isinstance(data, dict):
        raise _invalid("context must be a JSON object.", ("body", "context"))
    try:
        return GuideContext.model_validate(data)
    except ValidationError as error:
        detail = error.errors(
            include_url=False, include_context=False, include_input=False
        )
        for item in detail:
            item["loc"] = ("body", "context", *item["loc"])
        raise RequestValidationError(detail) from None


# -------------------------------------------------------------------- endpoint


class GuideFrameForm(BaseModel):
    """The multipart request, for the OpenAPI schema only (the route parses it)."""

    frame: bytes = Field(
        description="One JPEG or PNG camera frame, at most 1,500,000 bytes. "
        "Processed in memory and never stored."
    )
    context: GuideContext = Field(description="The route context, as JSON text.")


def _inline_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """A JSON schema with its ``$defs`` inlined, so the OpenAPI type generator sees
    the whole ``context`` object inside the multipart body."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return walk(deepcopy(defs[ref.removeprefix("#/$defs/")]))
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(schema)


_REQUEST_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": _inline_defs(GuideFrameForm.model_json_schema()),
            "encoding": {"context": {"contentType": "application/json"}},
        }
    },
}


@router.post(
    "/frame",
    response_model=GuideFrameResponse,
    responses={
        413: {"description": "The frame is larger than 1,500,000 bytes."},
        415: {"description": "Not multipart, or the frame is not a JPEG or PNG."},
        422: {"description": "The context is not valid."},
        429: {"description": "Too many frames this minute; see Retry-After."},
        503: {
            "description": "No ANTHROPIC_API_KEY, replay mode, or the Claude call "
            "is paused after repeated failures. The route steps still work."
        },
    },
    openapi_extra={"requestBody": _REQUEST_BODY},
)
async def post_guide_frame(
    request: Request,
    checker: Annotated[GuideChecker | None, Depends(get_guide_checker)],
    limits: Annotated[GuideLimits, Depends(get_guide_limits)],
) -> GuideFrameResponse:
    """Describe one camera frame for a blind or low-vision walker (preview).

    Claude describes what is in front of the camera in one short sentence and lists
    hazards and landmarks. It describes, it does not decide: it never gives turn
    instructions beyond confirming the route context, and never says a path is safe.
    The frame is processed in memory and discarded.
    """
    mode: guide.GuideMode = request.app.state.mode
    if mode == "replay":
        raise HTTPException(503, REPLAY_ONLY)
    if checker is None or checker.paused:
        raise HTTPException(503, UNAVAILABLE)
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Send the frame as multipart/form-data.")
    body = await _read_body(request)
    frame, raw_context = _parts(body, content_type)
    del body  # the frame is the only copy left, and it goes with this request
    if len(frame) > MAX_FRAME_BYTES:  # the size, before any decoding
        raise HTTPException(413, TOO_LARGE)
    context = _context(raw_context)
    try:
        limits.check(context.client_id)
    except GuideLimited as limited:
        message = (
            "You are sending frames faster than the guide can take them. "
            "It continues in a moment."
            if limited.scope == "client"
            else "The guide is busy right now. The route steps still work."
        )
        raise HTTPException(
            429, message, headers={"Retry-After": str(limited.retry_after_s)}
        ) from None
    if guide.frame_format(frame) is None:
        raise HTTPException(415, NOT_AN_IMAGE)
    # The Claude call takes up to 6 s: keep it off the event loop.
    return await run_in_threadpool(guide.describe_frame, frame, context, checker, mode)
