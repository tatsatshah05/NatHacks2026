"""``POST /api/reports``: a community report of snow, ice or a blocked path (OP-308).

The web app's Report button sends ``multipart/form-data`` (the photo as a file part);
scripts and tests may send JSON with the photo base64 encoded. Both carry the same
fields (``reports.ReportFields``): ``client_id``, ``type``, ``lat``, ``lng``, an
optional ``note`` and an optional ``photo``.

The photo stays in memory
    The body is read here, capped at ``MAX_BODY_BYTES``, and parsed by
    ``parse_multipart``, never by Starlette's form parser, which spools uploads over
    1 MB to a temporary file. The photo is classified and dropped with the request.

Responses
    201 the stored report (``reports.ReportResult``), with the photo outcome in words
    and the checks that answered (``photo.checked_by``: "model", "claude")
    413 body or photo too large; 415 not JSON or multipart, or a photo that is not
    JPEG or PNG; 422 invalid fields; 429 rate limited (``Retry-After`` header).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from . import reports
from .obstructions_api import get_now, get_store
from .rate_limit import public_write_limit
from .reports import (
    MAX_PHOTO_BYTES,
    PhotoChecks,
    RateLimited,
    ReportCounter,
    ReportFields,
    ReportFormBody,
    ReportJsonBody,
    ReportResult,
    ReportsConfig,
    build_photo_checker,
    photo_format,
)
from .store import ObstructionStore

__all__ = ["build_photo_checker", "parse_multipart", "router"]

router = APIRouter(prefix="/api", tags=["reports"])

# A 5 MB photo is about 6.7 MB as base64, plus the other fields.
MAX_BODY_BYTES = 7 * 1024 * 1024
MAX_PARTS = 8  # more form parts than this is not our web app
FIELD_NAMES = frozenset(ReportFields.model_fields)
MAX_FIELD_BYTES = 1024  # every text field is short; the note is 120 characters


# ------------------------------------------------------------------ dependencies


@lru_cache(maxsize=1)
def _config() -> ReportsConfig:
    return reports.load_config()


def get_reports_config() -> ReportsConfig:
    """``config/reports.json``, read once. Tests override this dependency."""
    return _config()


def get_report_counter(
    request: Request, store: Annotated[ObstructionStore, Depends(get_store)]
) -> ReportCounter:
    """The rate-limit counter on the store's database, created on first use."""
    state = request.app.state
    counter = getattr(state, "report_counter", None)
    if counter is None:
        counter = ReportCounter(store.engine)
        counter.create_tables()  # safe to repeat; the table survives restarts
        state.report_counter = counter
    return counter


def get_photo_checker(request: Request) -> PhotoChecks | None:
    """``app.state.photo_checker`` (``reports.PhotoChecks``), set at startup; None means
    photos are not checked."""
    return getattr(request.app.state, "photo_checker", None)


# ------------------------------------------------------------ reading the body


@dataclass(frozen=True)
class FormPart:
    name: str
    filename: str | None
    data: bytes


_BOUNDARY = re.compile(r'boundary=(?:"([^"]{1,70})"|([^;\s"]{1,70}))', re.IGNORECASE)
_PARAM = re.compile(r';\s*([A-Za-z*]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;\s]*))')


def parse_multipart(body: bytes, content_type: str) -> list[FormPart]:
    """Split a ``multipart/form-data`` body into parts, entirely in memory.

    Enough of RFC 7578 for browsers' ``FormData``: CRLF line breaks, one
    ``Content-Disposition: form-data; name=...`` per part, no nesting. Raises
    ``ValueError`` on anything else.
    """
    match = _BOUNDARY.search(content_type)
    if match is None:
        raise ValueError("the multipart body has no boundary")
    boundary = (match.group(1) or match.group(2)).encode("latin-1")
    # Every delimiter but the first follows a CRLF; adding one makes them all alike.
    chunks = (b"\r\n" + body).split(b"\r\n--" + boundary)
    if len(chunks) < 3 or not chunks[-1].startswith(b"--"):
        raise ValueError("the multipart body is incomplete")
    parts: list[FormPart] = []
    for chunk in chunks[1:-1]:
        line_end = chunk.find(b"\r\n")
        if line_end < 0 or chunk[:line_end].strip(b" \t"):
            raise ValueError("a multipart delimiter is malformed")
        head, separator, data = chunk[line_end + 2 :].partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("a multipart part has no header block")
        disposition = ""
        for line in head.split(b"\r\n"):
            key, colon, value = line.partition(b":")
            if not colon:
                raise ValueError("a multipart header is malformed")
            if key.strip().lower() == b"content-disposition":
                disposition = value.decode("utf-8", "replace").strip()
        if not disposition.lower().startswith("form-data"):
            raise ValueError("a multipart part is not form-data")
        # finditer, not findall: an unmatched group is None here, "" with findall.
        params = {
            m.group(1).lower(): m.group(2) if m.group(2) is not None else m.group(3)
            for m in _PARAM.finditer(disposition)
        }
        if not params.get("name"):
            raise ValueError("a multipart part has no name")
        parts.append(FormPart(params["name"], params.get("filename"), data))
        if len(parts) > MAX_PARTS:
            raise ValueError("too many multipart parts")
    return parts


async def _read_body(request: Request) -> bytes:
    """The request body, refused with a 413 as soon as it passes ``MAX_BODY_BYTES``."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(413, "The report is too large. A photo may be up to 5 MB.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(
                413, "The report is too large. A photo may be up to 5 MB."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _invalid(message: str, loc: tuple[str, ...] = ("body",)) -> RequestValidationError:
    """A 422 in FastAPI's usual ``detail`` shape."""
    return RequestValidationError([{"type": "value_error", "loc": loc, "msg": message}])


def _fields(raw: dict[str, Any]) -> ReportFields:
    try:
        return ReportFields.model_validate(raw)
    except ValidationError as error:
        # No input or context: the input could be the whole body.
        detail = error.errors(
            include_url=False, include_context=False, include_input=False
        )
        for item in detail:
            item["loc"] = ("body", *item["loc"])
        raise RequestValidationError(detail) from None


def _from_json(body: bytes) -> tuple[ReportFields, bytes | None]:
    try:
        raw = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid("The body is not valid JSON.") from None
    if not isinstance(raw, dict):
        raise _invalid("The body must be a JSON object.")
    encoded = raw.pop("photo", None)
    if encoded is not None and not isinstance(encoded, str):
        raise _invalid("photo must be base64 text.", ("body", "photo"))
    photo: bytes | None = None
    if encoded:
        if encoded.startswith("data:"):  # a data URL from FileReader.readAsDataURL
            encoded = encoded.partition(",")[2]
        try:
            photo = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise _invalid("photo is not valid base64.", ("body", "photo")) from None
    return _fields(raw), photo


def _from_form(body: bytes, content_type: str) -> tuple[ReportFields, bytes | None]:
    try:
        parts = parse_multipart(body, content_type)
    except ValueError as error:
        raise _invalid(f"The form body could not be read: {error}.") from None
    raw: dict[str, Any] = {}
    photo: bytes | None = None
    seen: set[str] = set()
    for part in parts:
        if part.name in seen:
            raise _invalid(f"{part.name} was sent twice.", ("body", part.name))
        seen.add(part.name)
        if part.name == "photo":
            photo = part.data or None  # an empty file input sends an empty part
            continue
        if part.filename is not None or len(part.data) > MAX_FIELD_BYTES:
            raise _invalid(
                f"{part.name} must be a short text field.", ("body", part.name)
            )
        try:
            raw[part.name] = part.data.decode("utf-8")
        except UnicodeDecodeError:
            raise _invalid(
                f"{part.name} is not UTF-8 text.", ("body", part.name)
            ) from None
    return _fields(raw), photo


def _check_photo_bytes(photo: bytes | None) -> None:
    if photo is None:
        return
    if len(photo) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "The photo is larger than 5 MB.")
    if photo_format(photo) is None:
        raise HTTPException(415, "The photo must be a JPEG or PNG image.")


# -------------------------------------------------------------------- endpoint

_REQUEST_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {"schema": ReportFormBody.model_json_schema()},
        "application/json": {"schema": ReportJsonBody.model_json_schema()},
    },
}


@router.post(
    "/reports",
    response_model=ReportResult,
    status_code=201,
    dependencies=[Depends(public_write_limit)],  # OP-401: per connection, per minute
    responses={
        413: {"description": "The body or the photo is too large (5 MB photo limit)."},
        415: {"description": "Not JSON or multipart, or the photo is not JPEG or PNG."},
        429: {"description": "Too many reports; see the Retry-After header."},
    },
    openapi_extra={"requestBody": _REQUEST_BODY},
)
async def post_report(
    request: Request,
    store: Annotated[ObstructionStore, Depends(get_store)],
    now: Annotated[datetime, Depends(get_now)],
    config: Annotated[ReportsConfig, Depends(get_reports_config)],
    counter: Annotated[ReportCounter, Depends(get_report_counter)],
    checker: Annotated[PhotoChecks | None, Depends(get_photo_checker)],
) -> ReportResult:
    """Report snow, ice or a blocked path at a point in Edmonton, with an optional photo.

    The report becomes a ``community_report`` obstruction straight away and expires
    after the hours in ``config/reports.json``. A photo that the road-condition model
    and Claude both confirm gives medium confidence; anything else gives low, and
    ``photo.checked_by`` names the checks that answered. The photo is never stored.
    """
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in ("application/json", "multipart/form-data"):
        raise HTTPException(415, "Send the report as JSON or multipart/form-data.")
    body = await _read_body(request)
    if media_type == "application/json":
        fields, photo = _from_json(body)
    else:
        fields, photo = _from_form(body, content_type)
    del body  # the photo is the only copy left, and it goes with this request
    _check_photo_bytes(photo)
    try:
        # The photo check may call a model or an API: keep it off the event loop.
        return await run_in_threadpool(
            reports.submit_report,
            fields,
            photo,
            store=store,
            counter=counter,
            config=config,
            checker=checker,
            now=now,
        )
    except RateLimited as limited:
        minutes = max(1, round(limited.retry_after_s / 60))
        message = (
            f"You have sent {config.per_client_per_hour} reports in the last hour. "
            f"Please try again in about {minutes} min."
            if limited.scope == "client"
            else "OpenPath is receiving too many reports right now. Please try again later."
        )
        raise HTTPException(
            429, message, headers={"Retry-After": str(limited.retry_after_s)}
        ) from None
