"""One log format for the API and uvicorn (OP-402).

``create_app()`` calls ``install()`` once. Every record from the app's loggers
(``openpath.*``, ``cloud.*`` and ``vision.*``), from uvicorn and from any library
that warns then comes out as one line:

    2026-11-08T20:01:02.345Z INFO openpath.store CHANGE city_lrt_devices:1234 new elevator_out 'Elevator out of service'

``LOG_FORMAT=json`` writes one JSON object per line instead, for Render's log
search. Its keys are ``time``, ``level``, ``logger`` and ``message``; a ``CHANGE``
line adds ``event`` (``"change"``) and, when the line names them, ``source`` and
``id``; a traceback goes in ``exc``, so a JSON record is always one line.

What is logged does not change here: the messages are the ones the modules write.
Only two things are set besides the format:

- The app's own loggers log at ``LOG_LEVEL`` (default ``INFO``), because every
  ``CHANGE`` line is an INFO record and, with no logging set up, Python printed
  only warnings. ``LOG_LEVEL=WARNING`` gives back exactly the lines the API wrote
  before OP-402.
- Library loggers keep their own levels (the root logger stays at WARNING), so
  for example httpx does not log every request.

A bad ``LOG_FORMAT`` or ``LOG_LEVEL`` never stops the API: it falls back to the
default and logs one warning. With ``uvicorn --reload`` (``make dev``) the reloader
process never imports the app, so its own two start-up lines keep uvicorn's format.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Literal, TextIO

LogFormat = Literal["text", "json"]

# The API's logger namespaces: modules use "openpath.<part>" or their __name__.
# "vision.*" (the camera worker's own loggers) is left alone: the worker sets up its
# logging itself, and the API only borrows vision modules for report photos.
APP_LOGGERS = ("openpath", "cloud")
# uvicorn sets up its own handlers (stderr for "uvicorn", stdout for the access log);
# install() keeps those handlers and gives them this module's format.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
DEFAULT_LEVEL = "INFO"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Multi-word subjects of CHANGE lines that carry no "source:id" token.
_CHANGE_SUBJECTS: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    # (words, source, the next word is the id)
    (("saved", "route"), "saved_route", True),
    (("weather", "watch"), "weather_watch", False),
    (("camera",), "camera", True),
)

_installed: logging.Handler | None = None


def log_format(raw: str | None) -> tuple[LogFormat, str | None]:
    """Read ``LOG_FORMAT``: ``text`` (the default) or ``json``, plus a warning if bad."""
    value = (raw or "").strip().lower()
    if value in ("", "text"):
        return "text", None
    if value == "json":
        return "json", None
    return "text", f"LOG_FORMAT must be 'text' or 'json', not {raw!r}; using text"


def log_level(raw: str | None) -> tuple[str, str | None]:
    """Read ``LOG_LEVEL`` for the app's loggers, plus a warning if it is not a level."""
    value = (raw or "").strip().upper()
    if value == "":
        return DEFAULT_LEVEL, None
    if value == "WARN":
        return "WARNING", None
    if value in LEVELS:
        return value, None
    return DEFAULT_LEVEL, (
        f"LOG_LEVEL must be one of {', '.join(LEVELS)}, not {raw!r}; "
        f"using {DEFAULT_LEVEL}"
    )


def utc_time(record: logging.LogRecord) -> str:
    """The record's time as UTC ISO 8601 with milliseconds, for example ``...45.123Z``."""
    moment = datetime.fromtimestamp(record.created, UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def one_line(text: str) -> str:
    """A message on one line: line breaks become the two characters ``\\n``."""
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def change_fields(message: str) -> dict[str, str]:
    """``event``, ``source`` and ``id`` for a ``CHANGE`` line; empty for other lines.

    The subjects the app writes today:

    - ``CHANGE city_lrt_devices:1234 new ...``: source and id around the first colon
      (obstructions, camera ice risks, ``camera_model:511:12:3`` in the worker);
    - ``CHANGE camera 511:12:3 new wet``: source ``camera``, id the next word;
    - ``CHANGE saved route <id> ok -> changed ...``: source ``saved_route``;
    - ``CHANGE weather watch inactive->active ...``: source ``weather_watch``.

    Anything else still gets ``event``; the full line is always in ``message``.
    """
    if not message.startswith("CHANGE "):
        return {}
    fields = {"event": "change"}
    words = message[len("CHANGE ") :].split()
    if not words:
        return fields
    if ":" in words[0]:
        source, _, item = words[0].partition(":")
        if source:
            fields["source"] = source
        if item:
            fields["id"] = item
        return fields
    for subject, source, has_id in _CHANGE_SUBJECTS:
        if tuple(words[: len(subject)]) == subject:
            fields["source"] = source
            if has_id and len(words) > len(subject):
                fields["id"] = words[len(subject)]
            break
    return fields


class TextFormatter(logging.Formatter):
    """``<UTC ISO time> <LEVEL> <logger> <message>``, then any traceback below it."""

    def format(self, record: logging.LogRecord) -> str:
        line = (
            f"{utc_time(record)} {record.levelname} {record.name} "
            f"{one_line(record.getMessage())}"
        )
        extra = self._exception_text(record)
        return f"{line}\n{extra}" if extra else line

    def _exception_text(self, record: logging.LogRecord) -> str:
        parts: list[str] = []
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            parts.append(record.exc_text)
        if record.stack_info:
            parts.append(self.formatStack(record.stack_info))
        return "\n".join(parts)


class JsonFormatter(logging.Formatter):
    """One JSON object per record, always on one line."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "time": utc_time(record),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        payload.update(change_fields(message))
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exc"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # json.dumps escapes line breaks inside strings, so this is one line.
        return json.dumps(payload, ensure_ascii=False, default=str)


def formatter(kind: LogFormat) -> logging.Formatter:
    return JsonFormatter() if kind == "json" else TextFormatter()


class StderrHandler(logging.StreamHandler[TextIO]):
    """Writes to whatever ``sys.stderr`` is when a record arrives.

    A handler that kept the ``sys.stderr`` of the moment it was made would write to
    a closed stream after pytest swaps its capture streams (pytest imports the app,
    and with it this handler, while it is capturing).
    """

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property  # type: ignore[override]
    def stream(self) -> TextIO:
        return sys.stderr

    @stream.setter
    def stream(self, value: TextIO) -> None:
        pass  # always sys.stderr; see the class docstring


def install(
    log_format_raw: str | None = None, log_level_raw: str | None = None
) -> logging.Handler:
    """Set the format once per process; later calls only update format and level.

    Adds one handler to the root logger, sets the app's loggers to the level, and
    gives uvicorn's existing handlers the same formatter. Returns the root handler.
    """
    global _installed
    kind, format_warning = log_format(log_format_raw)
    level, level_warning = log_level(log_level_raw)
    chosen = formatter(kind)
    root = logging.getLogger()
    if _installed is None or _installed not in root.handlers:
        _installed = StderrHandler()
        root.addHandler(_installed)
    _installed.setFormatter(chosen)
    for name in APP_LOGGERS:
        logging.getLogger(name).setLevel(level)
    for name in UVICORN_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(chosen)
    log = logging.getLogger("openpath.logging")
    for warning in (format_warning, level_warning):
        if warning:
            log.warning("WARN %s", warning)
    return _installed
