"""In-process rate limits for the API's write routes (OP-401).

Every public write is open to anyone who knows the URL: ``POST /api/reports``,
``POST /api/push/subscribe``, ``POST /api/push/test``, ``POST /api/saved-routes`` and
``DELETE /api/saved-routes/{id}``. ``RateLimiter`` caps how many of them one client
may send per minute, on top of the caps those routes already keep per ``client_id``
(5 reports an hour, 10 saved routes, 5 push subscriptions): those are keyed by an id
the browser chooses, this one by where the request came from. The keyed routes
(``POST /api/ingest/camera`` with ``X-Worker-Key``) keep their key and get a limit
``KEYED_FACTOR`` times looser, checked after the key, so a request without the key
never uses up the worker's share. GET routes are never limited.

The client is the first address in ``X-Forwarded-For`` when the header is present
(Render's proxy puts the caller there), else the socket address. A caller can forge
that header, so this guards against mistakes and casual floods; it is not a security
boundary (docs/SECURITY.md).

Fixed windows: at most ``limit`` requests per client in each 60 s window, counted in
memory and never persisted (the API is one process on Render, so one counter). A
refused request gets ``429`` with ``Retry-After`` (seconds until the window ends) and
one plain sentence. ``RATE_LIMIT_PER_MIN`` sets the public limit (default 30);
``0`` turns both limits off, which the tests do.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

log = logging.getLogger("openpath.rate_limit")

DEFAULT_PER_MIN = 30
KEYED_FACTOR = 10  # keyed routes (the vision worker) may send this many times more
WINDOW_S = 60.0
MAX_CLIENTS = 10_000  # windows kept in memory before the oldest are dropped

Scope = Literal["public", "keyed"]

# Write routes that keep their own limits instead of the per-connection one, and why.
# ``cloud/tests/test_rate_limit.py`` refuses any other write route without a limit.
# OP-418: the camera guide sends one frame every 2 to 3 seconds, so the 30-a-minute
# connection window would stop it alone, and everyone behind one venue NAT shares one
# connection key; ``guide.GuideLimits`` counts 30 a minute per ``client_id`` and 600 in
# total instead. A Report from the same connection keeps its own window this way.
OWN_LIMIT_ROUTES: frozenset[str] = frozenset({"/api/guide/frame"})


def limit_per_min(raw: str | None) -> int:
    """Read ``RATE_LIMIT_PER_MIN``: whole requests per client per minute.

    Unset or empty means the default; ``0`` disables the limits. A value that is not
    a whole number, or is negative, keeps the default and logs a warning, so a typo in
    a dashboard can neither stop the API nor open it up.
    """
    if raw is None or not raw.strip():
        return DEFAULT_PER_MIN
    try:
        value = int(raw.strip())
    except ValueError:
        value = -1
    if value < 0:
        log.warning(
            "WARN RATE_LIMIT_PER_MIN=%r is not a whole number; using %d",
            raw,
            DEFAULT_PER_MIN,
        )
        return DEFAULT_PER_MIN
    return value


@dataclass(frozen=True)
class Decision:
    """What the limiter decided for one request."""

    allowed: bool
    retry_after_s: int  # seconds until this client's window ends; 0 when allowed
    remaining: int  # requests left in the window after this one
    first_refusal: bool = False  # the first refused request of this window (log it)


@dataclass
class _Window:
    started_at: float
    count: int = 0
    refused: bool = False


class RateLimiter:
    """At most ``limit`` requests per key in each fixed window of ``window_s`` seconds.

    ``limit`` 0 disables it. Thread-safe: sync routes run in a thread pool.
    """

    def __init__(
        self,
        limit: int,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_CLIENTS,
    ) -> None:
        if limit < 0:
            raise ValueError("the limit must be 0 (off) or a positive number")
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self.max_keys = max_keys
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def tracked(self) -> int:
        """How many clients have a window in memory right now."""
        return len(self._windows)

    def check(self, key: str) -> Decision:
        """Count one request for ``key`` and say whether it may go ahead."""
        if not self.enabled:
            return Decision(True, 0, 0)
        now = self.clock()
        with self._lock:
            window = self._windows.get(key)
            if window is None or now - window.started_at >= self.window_s:
                if window is None and len(self._windows) >= self.max_keys:
                    self._prune(now)
                window = _Window(now)
                self._windows[key] = window
            if window.count >= self.limit:
                retry = max(1, math.ceil(window.started_at + self.window_s - now))
                first = not window.refused
                window.refused = True
                return Decision(False, retry, 0, first)
            window.count += 1
            return Decision(True, 0, self.limit - window.count)

    def _prune(self, now: float) -> None:
        """Drop ended windows; if the table is still full, the oldest ones too."""
        for key in [
            key
            for key, window in self._windows.items()
            if now - window.started_at >= self.window_s
        ]:
            del self._windows[key]
        excess = len(self._windows) - self.max_keys + 1
        if excess > 0:
            oldest = sorted(self._windows, key=lambda k: self._windows[k].started_at)
            for key in oldest[:excess]:
                del self._windows[key]


@dataclass(frozen=True)
class RateLimits:
    """The two limiters an app holds on ``app.state.rate_limits``."""

    public: RateLimiter
    keyed: RateLimiter

    @classmethod
    def from_env(cls, raw: str | None) -> RateLimits:
        per_min = limit_per_min(raw)
        return cls(RateLimiter(per_min), RateLimiter(per_min * KEYED_FACTOR))


def client_key(request: Request) -> str:
    """Who sent the request: the first ``X-Forwarded-For`` hop, else the socket."""
    first = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if first:
        return first
    return request.client.host if request.client is not None else "unknown"


def _enforce(request: Request, scope: Scope) -> None:
    limits: RateLimits | None = getattr(request.app.state, "rate_limits", None)
    if limits is None:
        return  # an app built without limits (the small test apps) never blocks
    limiter = limits.public if scope == "public" else limits.keyed
    decision = limiter.check(client_key(request))
    if decision.allowed:
        return
    if decision.first_refusal:
        log.warning(
            "RATE_LIMIT %s %s %s over %d per minute",
            scope,
            request.method,
            request.url.path,
            limiter.limit,
        )
    seconds = decision.retry_after_s
    unit = "second" if seconds == 1 else "seconds"
    raise HTTPException(
        429,
        "Too many requests from your connection in the last minute. "
        f"Please wait {seconds} {unit} and try again.",
        headers={"Retry-After": str(seconds)},
    )


def public_write_limit(request: Request) -> None:
    """Dependency for a write anyone may send."""
    _enforce(request, "public")


def keyed_write_limit(request: Request) -> None:
    """Dependency for a write that needs a key. List it after the key check."""
    _enforce(request, "keyed")
