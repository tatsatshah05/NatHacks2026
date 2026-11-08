"""OP-402: one log line per record, as text or JSON (cloud/app/logging_setup.py)."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator

import pytest

from cloud.app import logging_setup
from cloud.app.logging_setup import (
    JsonFormatter,
    StderrHandler,
    TextFormatter,
    change_fields,
    install,
    log_format,
    log_level,
)

TEXT_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z (?P<level>[A-Z]+) "
    r"(?P<logger>\S+) (?P<message>.*)$"
)


def record(
    message: str,
    *args: object,
    name: str = "openpath.store",
    level: int = logging.INFO,
    exc_info: bool = False,
) -> logging.LogRecord:
    info = None
    if exc_info:
        try:
            raise RuntimeError("database is gone")
        except RuntimeError:
            info = sys.exc_info()
    return logging.LogRecord(name, level, __file__, 1, message, args, info)


@pytest.fixture
def saved_logging() -> Iterator[None]:
    """Put the logging set-up back as it was, whatever a test installs."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    root_level = root.level
    levels = {name: logging.getLogger(name).level for name in logging_setup.APP_LOGGERS}
    uvicorn = {
        name: list(logging.getLogger(name).handlers)
        for name in logging_setup.UVICORN_LOGGERS
    }
    formatters = {
        id(handler): handler.formatter
        for name in logging_setup.UVICORN_LOGGERS
        for handler in logging.getLogger(name).handlers
    }
    installed = logging_setup._installed
    installed_formatter = installed.formatter if installed is not None else None
    yield
    root.handlers[:] = handlers
    root.setLevel(root_level)
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)
    for name, kept in uvicorn.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = kept
        for handler in kept:
            handler.setFormatter(formatters.get(id(handler)))
    logging_setup._installed = installed
    if installed is not None:
        installed.setFormatter(installed_formatter)


# ------------------------------------------------------------------ settings


def test_log_format_defaults_to_text_and_accepts_json() -> None:
    assert log_format(None) == ("text", None)
    assert log_format("") == ("text", None)
    assert log_format(" JSON ") == ("json", None)
    kind, warning = log_format("yaml")
    assert kind == "text"
    assert warning is not None and "'yaml'" in warning


def test_log_level_defaults_to_info_and_never_raises() -> None:
    assert log_level(None) == ("INFO", None)
    assert log_level("warning") == ("WARNING", None)
    assert log_level("WARN") == ("WARNING", None)
    level, warning = log_level("loud")
    assert level == "INFO"
    assert warning is not None and "'loud'" in warning


# ---------------------------------------------------------------- text lines


def test_text_line_is_time_level_logger_message() -> None:
    line = TextFormatter().format(
        record(
            "CHANGE %s new %s %r",
            "city_lrt_devices:1234",
            "elevator_out",
            "Elevator out of service",
        )
    )
    match = TEXT_LINE.match(line)
    assert match is not None, line
    assert match["level"] == "INFO"
    assert match["logger"] == "openpath.store"
    assert match["message"] == (
        "CHANGE city_lrt_devices:1234 new elevator_out 'Elevator out of service'"
    )


def test_text_time_is_utc() -> None:
    item = record("SCHEDULER started sources=%d", 7)
    item.created = 1794168062.5  # 2026-11-08T20:01:02.500Z
    assert TextFormatter().format(item).startswith("2026-11-08T20:01:02.500Z INFO ")


def test_text_message_stays_on_one_line() -> None:
    line = TextFormatter().format(record("first\nsecond\r\nthird"))
    assert "\n" not in line
    assert line.endswith("first\\nsecond\\nthird")


def test_text_traceback_follows_the_line() -> None:
    text = TextFormatter().format(
        record("MATCH failed", level=logging.ERROR, exc_info=True)
    )
    first, rest = text.split("\n", 1)
    assert TEXT_LINE.match(first)
    assert "RuntimeError: database is gone" in rest


# ---------------------------------------------------------------- JSON lines


def test_json_line_has_the_four_keys() -> None:
    item = record(
        "SOURCE %s ok items=%d skipped=%d", "lrt", 3, 0, name="openpath.scheduler"
    )
    item.created = 1794168062.5
    payload = json.loads(JsonFormatter().format(item))
    assert payload == {
        "time": "2026-11-08T20:01:02.500Z",
        "level": "INFO",
        "logger": "openpath.scheduler",
        "message": "SOURCE lrt ok items=3 skipped=0",
    }


def test_json_change_line_adds_event_source_and_id() -> None:
    line = JsonFormatter().format(
        record("CHANGE %s removed", "city_construction:ABC-1")
    )
    payload = json.loads(line)
    assert payload["event"] == "change"
    assert payload["source"] == "city_construction"
    assert payload["id"] == "ABC-1"
    assert payload["message"] == "CHANGE city_construction:ABC-1 removed"


def test_json_traceback_stays_inside_one_line() -> None:
    line = JsonFormatter().format(
        record("MATCH failed", level=logging.ERROR, exc_info=True)
    )
    assert "\n" not in line
    payload = json.loads(line)
    assert "RuntimeError: database is gone" in payload["exc"]


def test_json_keeps_non_ascii_text() -> None:
    payload = json.loads(JsonFormatter().format(record("CHANGE weather watch -> 0 °C")))
    assert payload["message"] == "CHANGE weather watch -> 0 °C"


@pytest.mark.parametrize(
    ("message", "fields"),
    [
        # cloud/app/store.py
        (
            "CHANGE city_lrt_devices:1234 new elevator_out 'Elevator'",
            {"event": "change", "source": "city_lrt_devices", "id": "1234"},
        ),
        # cloud/app/camera_ice_routing.py: the id itself has colons
        (
            "CHANGE camera_ice:511:12:3 new ice_risk on 2 crossings",
            {"event": "change", "source": "camera_ice", "id": "511:12:3"},
        ),
        # cloud/app/camera_readings.py
        (
            "CHANGE camera 511:12:3 new wet",
            {"event": "change", "source": "camera", "id": "511:12:3"},
        ),
        # cloud/app/push.py
        (
            "CHANGE saved route r42 ok -> changed (detour) eta 5 -> 7 min",
            {"event": "change", "source": "saved_route", "id": "r42"},
        ),
        # cloud/app/weather_service.py
        (
            "CHANGE weather watch inactive->active freezing after rain",
            {"event": "change", "source": "weather_watch"},
        ),
        # vision/cameras.py: no source:id token
        ("CHANGE alberta511_cameras count 0 -> 1", {"event": "change"}),
        ("CHANGE", {}),
        ("CHANGE ", {"event": "change"}),
        ("SOURCE lrt ok items=3", {}),
        ("WARN CHANGE is not first", {}),
    ],
)
def test_change_fields(message: str, fields: dict[str, str]) -> None:
    assert change_fields(message) == fields


# ------------------------------------------------------------------- install


def test_install_once_sets_levels_and_formats(saved_logging: None) -> None:
    root = logging.getLogger()
    first = install("json", None)
    second = install("text", "WARNING")
    assert first is second
    assert root.handlers.count(first) == 1
    assert isinstance(first.formatter, TextFormatter)
    assert logging.getLogger("openpath").level == logging.WARNING
    assert logging.getLogger("cloud").level == logging.WARNING
    install(None, None)
    assert logging.getLogger("openpath.store").getEffectiveLevel() == logging.INFO
    # Library loggers keep the root's level: httpx does not log every request.
    assert logging.getLogger("httpx").getEffectiveLevel() == root.getEffectiveLevel()


def test_install_formats_uvicorn_handlers(saved_logging: None) -> None:
    from uvicorn.config import LOGGING_CONFIG, Config

    async def app(scope: object, receive: object, send: object) -> None:
        return None

    Config(app=app, log_config=LOGGING_CONFIG)  # uvicorn's own set-up, as on Render
    install("json", None)
    handlers = (
        logging.getLogger("uvicorn").handlers
        + logging.getLogger("uvicorn.access").handlers
    )
    assert handlers
    assert all(isinstance(h.formatter, JsonFormatter) for h in handlers)
    # The access log's arguments still format into one message.
    access = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/health", "1.1", 200),
        None,
    )
    payload = json.loads(JsonFormatter().format(access))
    assert payload["message"] == '127.0.0.1:5000 - "GET /health HTTP/1.1" 200'


def test_bad_settings_log_one_warning(
    saved_logging: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="openpath.logging"):
        install("xml", "chatty")
    warnings = [r.getMessage() for r in caplog.records if r.name == "openpath.logging"]
    assert len(warnings) == 2
    assert all(w.startswith("WARN LOG_") for w in warnings)


def test_create_app_installs_the_format(
    saved_logging: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cloud.app.main import create_app

    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    create_app()
    assert isinstance(logging_setup._installed, StderrHandler)
    assert isinstance(logging_setup._installed.formatter, JsonFormatter)
    monkeypatch.setenv("LOG_FORMAT", "text")
    create_app()
    assert isinstance(logging_setup._installed.formatter, TextFormatter)
    assert logging.getLogger().handlers.count(logging_setup._installed) == 1


def test_handler_writes_to_the_current_stderr(
    saved_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = StderrHandler()
    handler.setFormatter(TextFormatter())
    handler.emit(record("CHANGE %s expired", "city_trails:7"))
    line = capsys.readouterr().err.strip()
    match = TEXT_LINE.match(line)
    assert match is not None and match["message"] == "CHANGE city_trails:7 expired"
