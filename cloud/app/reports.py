"""Community reports (OP-308): snow, ice or a blocked path, reported by a person.

What this module does
---------------------
- Reads ``config/reports.json``: how long a report of each type stays live and how
  many reports a client may send per hour.
- Turns a validated report into an ``Obstruction`` for the shared store, so routing,
  the map and the route report treat it like any other barrier (source
  ``community_report``, category ``sidewalk``, a Point).
- Decides the report's confidence from the optional photo: ``medium`` when a photo
  checker looked at the photo and it agrees with the reported type, ``low`` in every
  other case (no photo, no checker, an unusable photo, a disagreement).
- Counts reports per client and in total in the database (``community_report_log``),
  so the rate limit survives a restart.

The photo
---------
A photo is bytes in memory for the length of one request. It is classified, its size
and class are logged, and then it is dropped. Nothing here writes it to disk, to the
database or to a log (AGENTS.md, Vision Rules). ``reports_api`` parses the request
body itself for the same reason: Starlette's form parser spools large uploads to a
temporary file.

Photo checkers
--------------
``PhotoChecker`` is a small protocol (``classify(photo) -> label``) so the API can run
with the ONNX road-condition model, with Claude, or with no check at all, and tests
can use a fake. ``build_photo_checker`` picks one from the environment:

- ``MODEL_FILE`` (a local ``.onnx``) or ``MODEL_URL`` (an HTTPS release asset), with
  ``MODEL_LABELS`` (the training ``labels.json``) and ``MODEL_SHA256``; optional
  ``MODEL_MIN_SCORE`` (default 0.5). Needs ``vision/requirements.txt`` installed.
- else ``ANTHROPIC_API_KEY``: ``vision.ai_check.ClaudeCheck`` with ``REPORTS_AI_MODEL``
  (default ``claude-haiku-5-5``).
- else no checker: every report gets ``low`` confidence and the response says so.

Any problem building a checker is logged and leaves the API without one. A report
is never refused because the photo check is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Index, delete, func
from sqlalchemy.engine import Engine
from sqlmodel import Field as Column
from sqlmodel import Session, SQLModel, col, select

from .schemas import (
    BBox,
    Category,
    Confidence,
    Effect,
    Geometry,
    Kind,
    Obstruction,
)
from .store import ObstructionStore

log = logging.getLogger("openpath.reports")

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "reports.json"
SOURCE = "community_report"
ATTRIBUTION = "Community report"

ReportType = Literal["snow", "ice", "blocked"]
REPORT_TYPES: tuple[ReportType, ...] = ("snow", "ice", "blocked")

# The anonymous id the web app keeps on the device (also used by OP-304 for push).
CLIENT_ID_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"
MAX_NOTE_CHARS = 120
MAX_PHOTO_BYTES = 5 * 1024 * 1024

# The same approximate box as vision/cameras.py: Edmonton and a little of its
# neighbours. A report outside it is a mistake (or abuse), not a barrier we route around.
EDMONTON = BBox(min_lng=-113.7136, min_lat=53.3957, max_lng=-113.2714, max_lat=53.7160)

# The classes the road-condition model knows (vision/LABELS.md). Kept here as a plain
# tuple so the API does not import vision/ at start-up; a test checks the two agree.
PHOTO_CLASSES: tuple[str, ...] = (
    "dry",
    "wet",
    "snow_partial",
    "snow_covered",
    "ice",
    "unusable",
)
# What a class means for a report type. Classes missing here confirm nothing.
CLASS_TO_TYPE: dict[str, ReportType] = {
    "snow_partial": "snow",
    "snow_covered": "snow",
    "ice": "ice",
}
CLASS_WORDS: dict[str, str] = {
    "dry": "a dry surface",
    "wet": "a wet surface",
    "snow_partial": "some snow",
    "snow_covered": "snow cover",
    "ice": "ice",
}

RATE_WINDOW = timedelta(hours=1)
LOG_KEEP = timedelta(hours=24)  # rows older than this are pruned on each insert
# Serialises "check the limit, store, count" so parallel requests cannot all pass.
_WRITE_LOCK = threading.Lock()


# ------------------------------------------------------------------------- config


@dataclass(frozen=True)
class ReportsConfig:
    expiry_hours: Mapping[ReportType, float]
    per_client_per_hour: int
    global_per_hour: int


def load_config(path: Path | str = CONFIG_PATH) -> ReportsConfig:
    """Read and check ``config/reports.json``."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    expiry = {key: float(value) for key, value in raw["expiry_hours"].items()}
    missing = [key for key in REPORT_TYPES if key not in expiry]
    if missing:
        raise ValueError(f"reports.json expiry_hours is missing {missing}")
    if any(hours <= 0 for hours in expiry.values()):
        raise ValueError("reports.json expiry_hours must be positive")
    limits = raw["rate_limit"]
    per_client = int(limits["per_client_per_hour"])
    global_limit = int(limits["global_per_hour"])
    if per_client < 1 or global_limit < per_client:
        raise ValueError(
            "reports.json rate_limit must be >= 1 and global >= per client"
        )
    return ReportsConfig(expiry, per_client, global_limit)


# ------------------------------------------------------------------- report kinds


@dataclass(frozen=True)
class ReportKind:
    kind: Kind
    effect: Effect
    title: str  # the Obstruction title, e.g. "Reported: icy sidewalk"
    noun: str  # for messages: "an icy sidewalk"
    # Model classes that confirm this type. Empty when no class can (a blocked path).
    agreeing_classes: frozenset[str]


KINDS: dict[ReportType, ReportKind] = {
    "snow": ReportKind(
        Kind.SNOW,
        Effect.SNOW,
        "Reported: snow on the sidewalk",
        "snow on the sidewalk",
        frozenset({"snow_partial", "snow_covered"}),
    ),
    "ice": ReportKind(
        Kind.ICE_RISK,
        Effect.ICE_RISK,
        "Reported: icy sidewalk",
        "an icy sidewalk",
        frozenset({"ice"}),
    ),
    "blocked": ReportKind(
        Kind.BLOCKED,
        Effect.BLOCKED,
        "Reported: sidewalk blocked",
        "a blocked sidewalk",
        frozenset(),
    ),
}


# ----------------------------------------------------------------- the report in


class ReportFields(BaseModel):
    """The fields of one report, before the photo. Shared by the JSON and form bodies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(
        pattern=CLIENT_ID_PATTERN,
        description="The device's anonymous id: 16 to 64 letters, digits, _ or -.",
        examples=["3f9c2a7d1e8b4c6f9a0d2e4b6c8f1a3d"],
    )
    type: ReportType
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    note: str | None = Field(
        default=None,
        max_length=MAX_NOTE_CHARS,
        description="Optional plain text shown with the report.",
    )

    @field_validator("note", mode="before")
    @classmethod
    def _clean_note(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            # ValueError on purpose: pydantic turns it into a 422; a TypeError would be a 500.
            raise ValueError("note must be text")  # noqa: TRY004
        if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
            raise ValueError("note must not contain control characters")
        cleaned = " ".join(value.split())
        return cleaned or None

    @model_validator(mode="after")
    def _in_edmonton(self) -> ReportFields:
        inside = (
            EDMONTON.min_lng <= self.lng <= EDMONTON.max_lng
            and EDMONTON.min_lat <= self.lat <= EDMONTON.max_lat
        )
        if not inside:
            raise ValueError("the location must be inside Edmonton")
        return self


class ReportJsonBody(ReportFields):
    """``POST /api/reports`` as JSON: the photo is base64 (a data URL is accepted)."""

    photo: str | None = Field(
        default=None,
        description="Optional JPEG or PNG, base64 encoded (a data: URL is accepted), "
        "at most 5 MB decoded. Classified in memory and never stored.",
    )


class ReportFormBody(ReportFields):
    """``POST /api/reports`` as multipart/form-data: the photo is a file part."""

    photo: bytes | None = Field(
        default=None,
        description="Optional JPEG or PNG file, at most 5 MB. Classified in memory "
        "and never stored.",
    )


# -------------------------------------------------------------------- the photo

PhotoFormat = Literal["jpeg", "png"]


def photo_format(photo: bytes) -> PhotoFormat | None:
    """``jpeg`` or ``png`` from the file's first bytes, else None (not an image we take)."""
    if photo.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if photo.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    return None


class PhotoChecker(Protocol):
    """Classifies one photo held in memory. Returns a PHOTO_CLASSES label or ``unknown``."""

    name: str

    def classify(self, photo: bytes) -> str: ...


class ModelPhotoChecker:
    """The ONNX road-condition classifier (``vision/inference.py``) as a PhotoChecker."""

    def __init__(self, classifier: Any) -> None:
        self.classifier = classifier  # vision.inference.Classifier
        self.name = f"onnx:{str(classifier.version)[:12]}"

    def classify(self, photo: bytes) -> str:
        return str(self.classifier.predict(photo).label)


class ClaudePhotoChecker:
    """``vision.ai_check.ClaudeCheck`` as a PhotoChecker. No answer means ``unknown``."""

    def __init__(self, check: Any) -> None:
        self.check = check
        self.name = f"claude:{getattr(check, 'model', 'unknown')}"

    def classify(self, photo: bytes) -> str:
        result = self.check.label(photo)
        return "unknown" if result is None else str(result.label)


def build_photo_checker(
    env: Mapping[str, str] | None = None,
) -> PhotoChecker | None:
    """The checker the environment asks for, or None. Never raises."""
    env = os.environ if env is None else env
    model_file, model_url = env.get("MODEL_FILE"), env.get("MODEL_URL")
    if model_file or model_url:
        try:
            checker = _model_checker(env, model_file, model_url)
        except Exception as error:  # noqa: BLE001 - any failure means "no model check"
            # The type only: the message could repeat a URL or a path.
            log.warning(
                "REPORT photo check: the ONNX model is not available (%s)",
                type(error).__name__,
            )
        else:
            log.info("REPORT photo check: %s", checker.name)
            return checker
    if env.get("ANTHROPIC_API_KEY"):
        try:
            checker = _claude_checker(env)
        except Exception as error:  # noqa: BLE001 - same: fall back to no check
            log.warning(
                "REPORT photo check: Claude is not available (%s)", type(error).__name__
            )
        else:
            log.info("REPORT photo check: %s", checker.name)
            return checker
    log.info("REPORT photo check: none configured; every report gets low confidence")
    return None


def _model_checker(
    env: Mapping[str, str], model_file: str | None, model_url: str | None
) -> PhotoChecker:
    import httpx

    from vision.inference import ModelSpec, OnnxClassifier, download_model

    labels_path, sha256 = env.get("MODEL_LABELS"), env.get("MODEL_SHA256")
    if not labels_path or not sha256:
        raise ValueError("MODEL_LABELS and MODEL_SHA256 are required with a model")
    spec = ModelSpec.from_training_labels(
        json.loads(Path(labels_path).read_text(encoding="utf-8")),
        sha256=sha256,
        min_score=float(env.get("MODEL_MIN_SCORE") or 0.5),
    )
    if model_file:
        model = Path(model_file).read_bytes()
    else:
        with httpx.Client(timeout=30) as client:
            model = download_model(client, str(model_url))
    return ModelPhotoChecker(OnnxClassifier.load(model, spec))


def _claude_checker(env: Mapping[str, str]) -> PhotoChecker:
    from vision.ai_check import ClaudeCheck

    return ClaudePhotoChecker(
        ClaudeCheck(env.get("REPORTS_AI_MODEL") or "claude-haiku-5-5")
    )


PhotoOutcome = Literal[
    "none",  # no photo was sent
    "agrees",  # the photo shows the reported type: medium confidence
    "disagrees",  # the photo shows something else: kept as reported, low confidence
    "unusable",  # the checker could not read the photo
    "unverifiable",  # no class can confirm this type (a blocked path)
    "unavailable",  # no checker on this server, or the check failed
]


class PhotoResult(BaseModel):
    """What happened to the photo, for the response. Never the photo itself."""

    received: bool
    checked: bool  # True when a checker returned a class for the photo
    outcome: PhotoOutcome
    label: str | None  # the class the checker saw, when it ran
    note: str  # one plain sentence for the person


def check_photo(
    photo: bytes | None, report_type: ReportType, checker: PhotoChecker | None
) -> tuple[Confidence, PhotoResult]:
    """Apply the confidence rules: medium only when a checked photo agrees."""
    kind = KINDS[report_type]
    if photo is None:
        return Confidence.LOW, PhotoResult(
            received=False,
            checked=False,
            outcome="none",
            label=None,
            note="No photo was sent, so the report has low confidence.",
        )
    discarded = "The photo was discarded."
    if checker is None:
        return Confidence.LOW, PhotoResult(
            received=True,
            checked=False,
            outcome="unavailable",
            label=None,
            note=f"This server cannot check photos, so the report has low confidence. {discarded}",
        )
    try:
        label = checker.classify(photo)
    except Exception:  # a failing checker must not refuse the report
        log.exception("REPORT photo check failed (%s)", checker.name)
        return Confidence.LOW, PhotoResult(
            received=True,
            checked=False,
            outcome="unavailable",
            label=None,
            note=f"The photo check was unavailable, so the report has low confidence. {discarded}",
        )
    if label not in PHOTO_CLASSES or label == "unusable":
        return Confidence.LOW, PhotoResult(
            received=True,
            checked=False,
            outcome="unusable",
            label=None,
            note=f"The photo could not be read, so the report has low confidence. {discarded}",
        )
    if not kind.agreeing_classes:
        return Confidence.LOW, PhotoResult(
            received=True,
            checked=True,
            outcome="unverifiable",
            label=label,
            note=f"A photo check cannot confirm {kind.noun}, so the report has low confidence. {discarded}",
        )
    if label in kind.agreeing_classes:
        return Confidence.MEDIUM, PhotoResult(
            received=True,
            checked=True,
            outcome="agrees",
            label=label,
            note=f"The photo was checked and shows {CLASS_WORDS[label]}, so the report has medium confidence. {discarded}",
        )
    return Confidence.LOW, PhotoResult(
        received=True,
        checked=True,
        outcome="disagrees",
        label=label,
        note=(
            f"The photo was checked and shows {CLASS_WORDS[label]}, not {kind.noun}. "
            f"Your report is kept as {kind.noun} with low confidence. {discarded}"
        ),
    )


# ------------------------------------------------------------------ rate limiting


class ReportLogRow(SQLModel, table=True):
    """One row per accepted report: who (anonymous id) and when, for the rate limit."""

    __tablename__ = "community_report_log"
    __table_args__ = (
        Index("ix_community_report_log_client", "client_id", "created_at"),
        Index("ix_community_report_log_created", "created_at"),
    )

    id: int | None = Column(default=None, primary_key=True)
    client_id: str = Column(max_length=64)
    report_id: str = Column(max_length=270)
    created_at: datetime


class RateLimited(Exception):
    """Too many reports from one client, or too many in total."""

    def __init__(self, scope: Literal["client", "global"], retry_after_s: int) -> None:
        super().__init__(scope)
        self.scope = scope
        self.retry_after_s = retry_after_s


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; everything stored is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ReportCounter:
    """Counts accepted reports in the database, so limits hold across restarts.

    Uses its own clock (the real time by default), not the app's replay clock: a
    frozen demo clock would otherwise lock a client out for good after five reports.
    """

    def __init__(
        self, engine: Engine, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.engine = engine
        self.clock = clock or (lambda: datetime.now(UTC))

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine, tables=[ReportLogRow.__table__])

    def check(self, client_id: str, config: ReportsConfig) -> None:
        """Raise ``RateLimited`` when this client, or everyone, is over the hourly limit."""
        now = self.clock().astimezone(UTC)
        cutoff = now - RATE_WINDOW
        recent = col(ReportLogRow.created_at) > cutoff
        mine = col(ReportLogRow.client_id) == client_id
        with Session(self.engine) as session:
            if self._count(session, recent, mine) >= config.per_client_per_hour:
                raise RateLimited("client", self._wait_s(session, now, recent, mine))
            if self._count(session, recent) >= config.global_per_hour:
                raise RateLimited("global", self._wait_s(session, now, recent))

    @staticmethod
    def _count(session: Session, *where: Any) -> int:
        return session.exec(
            select(func.count()).select_from(ReportLogRow).where(*where)
        ).one()

    @staticmethod
    def _wait_s(session: Session, now: datetime, *where: Any) -> int:
        """Seconds until the oldest counted report leaves the one-hour window."""
        oldest = session.exec(
            select(func.min(ReportLogRow.created_at)).where(*where)
        ).one()
        wait = (_aware(oldest) + RATE_WINDOW - now) if oldest else RATE_WINDOW
        return max(1, int(wait.total_seconds()) + 1)

    def record(self, client_id: str, report_id: str) -> None:
        """Log one accepted report and prune rows older than ``LOG_KEEP``."""
        now = self.clock().astimezone(UTC)
        with Session(self.engine) as session:
            session.add(
                ReportLogRow(client_id=client_id, report_id=report_id, created_at=now)
            )
            session.exec(  # type: ignore[call-overload]
                delete(ReportLogRow).where(
                    col(ReportLogRow.created_at) < now - LOG_KEEP
                )
            )
            session.commit()

    def count(self, since: datetime, client_id: str | None = None) -> int:
        """Reports logged after ``since`` (for tests and tools)."""
        where = [col(ReportLogRow.created_at) > since.astimezone(UTC)]
        if client_id is not None:
            where.append(col(ReportLogRow.client_id) == client_id)
        with Session(self.engine) as session:
            return self._count(session, *where)


# ------------------------------------------------------------------- the result


class ReportResult(BaseModel):
    """The ``POST /api/reports`` response: the stored item's identity and what happened."""

    id: str  # the Obstruction id, "community_report:<ref>"
    type: ReportType
    kind: Kind
    effect: Effect
    confidence: Confidence
    title: str
    observed_at: datetime
    expires_at: datetime
    expires_in_hours: float
    photo: PhotoResult
    message: str  # one or two plain sentences for the person who reported


def _hours_text(hours: float) -> str:
    whole = f"{hours:g}"
    return f"{whole} hour" if hours == 1 else f"{whole} hours"


def build_obstruction(
    fields: ReportFields,
    confidence: Confidence,
    now: datetime,
    config: ReportsConfig,
    source_ref: str | None = None,
) -> Obstruction:
    """The contract record for one report. ``source_ref`` is random unless given."""
    kind = KINDS[fields.type]
    return Obstruction(
        source=SOURCE,
        source_ref=source_ref or secrets.token_hex(8),
        category=Category.SIDEWALK,
        kind=kind.kind,
        effect=kind.effect,
        title=kind.title,
        detail=fields.note,
        geometry=Geometry(type="Point", coordinates=[fields.lng, fields.lat]),
        confidence=confidence,
        starts_at=None,
        ends_at=None,
        observed_at=now,
        expires_at=now + timedelta(hours=config.expiry_hours[fields.type]),
        url=None,
        attribution=ATTRIBUTION,
    )


def submit_report(
    fields: ReportFields,
    photo: bytes | None,
    *,
    store: ObstructionStore,
    counter: ReportCounter,
    config: ReportsConfig,
    checker: PhotoChecker | None,
    now: datetime,
) -> ReportResult:
    """Rate-limit, check the photo, store the obstruction, log one line. Drops the photo.

    ``now`` is the app clock (frozen in a replay demo) and stamps the obstruction;
    the counter keeps its own real clock. Raises ``RateLimited``; the API turns it
    into a 429.
    """
    counter.check(fields.client_id, config)  # a quick refusal before the photo check
    confidence, result = check_photo(photo, fields.type, checker)
    item = build_obstruction(fields, confidence, now, config)
    with _WRITE_LOCK:
        # Again, under the lock: parallel requests from one client must not all pass.
        counter.check(fields.client_id, config)
        store.upsert([item], now)
        counter.record(fields.client_id, item.id)
    photo_text = (
        "none"
        if photo is None
        else f"{len(photo)} bytes class={result.label or result.outcome}"
    )
    # Size and class only: never the photo, the note or the client id.
    log.info(
        "REPORT %s %s at %.5f,%.5f confidence=%s photo=%s",
        item.id,
        item.kind.value,
        fields.lat,
        fields.lng,
        confidence.value,
        photo_text,
    )
    hours = config.expiry_hours[fields.type]
    return ReportResult(
        id=item.id,
        type=fields.type,
        kind=item.kind,
        effect=item.effect,
        confidence=confidence,
        title=item.title,
        observed_at=item.observed_at,
        expires_at=item.expires_at,
        expires_in_hours=hours,
        photo=result,
        message=f"Thanks. Your report is live for the next {_hours_text(hours)}. {result.note}",
    )
