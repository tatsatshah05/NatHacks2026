"""Web Push alerts for saved routes (OP-304).

Three parts, all built by ``start_route_watcher`` at app startup (``main.py``):

``PushSubscriptionStore``
    The ``push_subscription`` table: one row per browser push endpoint, owned by an
    anonymous ``client_id``. No accounts (decided November 7) and no personal data
    beyond the endpoint the browser handed us.

``PushSender``
    Sends one JSON payload to every subscription of a client with ``pywebpush``,
    signed with the VAPID keys from the environment (``VAPID_PUBLIC_KEY``,
    ``VAPID_PRIVATE_KEY``, ``VAPID_SUBJECT``). Without keys it logs once at startup
    and every send is a no-op: the demo must never crash for a missing key. A
    ``404`` or ``410`` from the push service deletes the subscription; any other
    failure counts, and the subscription is dropped after ``MAX_FAILURES``.

``RouteWatcher``
    Rechecks saved routes when hazards change. Every ``interval_s`` a daemon thread
    asks the hazard provider for the current ``HazardMap`` (which also keeps the
    matcher's map fresh without traffic), diffs it with the previous one, and
    rechecks only the saved routes that touch a changed edge (through an
    ``edge -> route ids`` index of each route's current and usual edges), every
    route that is impossible right now, and routes saved since the last pass.
    After ``WARMUP_S`` from startup it rechecks every route once, so a change
    that happened while the API was down or asleep is not missed.
    ``saved_routes.decide`` applies the change rules; a push goes out at most once
    per saved route every ``MIN_PUSH_INTERVAL_S``, and a change that falls inside
    that window stays pending and is sent on a later tick, unless the route is
    back to normal before the browser ever heard of the problem.

    The watcher never blocks ``/api/route`` (its own thread, one small lock) and
    never breaks routing: every failure is logged and the next tick tries again.

Keys: ``python -m cloud.app.push --generate-keys`` prints a fresh VAPID key pair
in the two environment variables' format (docs/DEPLOY.md).
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, col, select

from .routing import HazardMap, Route, Router, profile_weights
from .saved_routes import (
    Notice,
    RouteState,
    SavedRouteRow,
    SavedRouteStore,
    Usual,
    decide,
    edge_index,
    watched_edges,
)

log = logging.getLogger("openpath.push")

MAX_SUBSCRIPTIONS_PER_CLIENT = 5
MAX_FAILURES = 5  # a subscription is dropped after this many failed sends in a row
MIN_PUSH_INTERVAL_S = 120.0  # at most one push per saved route in this window
WATCH_INTERVAL_S = 15.0  # how often the watcher looks at the hazard map
# After a start (or a Render free-plan wake-up) items that expired while the API was
# down are missing until the scheduler fetches again, so the watcher rechecks
# nothing and sends nothing for this long. Derived from the settings, not measured:
# a source's first round takes at most about 47 s (4 attempts with 10 s timeouts and
# 1, 2 and 4 s back-off), then one 30 s map refresh and one 15 s tick.
WARMUP_S = 120.0
PUSH_TTL_S = 15 * 60  # how long a push service keeps an undelivered message
PUSH_TIMEOUT_S = 10.0


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- subscriptions


class LimitReached(Exception):
    """A client already holds as many subscriptions as it may (``429`` at the API)."""


class PushSubscriptionRow(SQLModel, table=True):
    """One browser's push endpoint. ``endpoint`` is unique, so a re-subscribe updates."""

    __tablename__ = "push_subscription"

    id: int | None = Field(default=None, primary_key=True)
    client_id: str = Field(index=True, max_length=64)
    endpoint: str = Field(unique=True, max_length=1024)
    p256dh: str = Field(max_length=256)
    auth: str = Field(max_length=64)
    created_at: datetime
    last_ok_at: datetime | None = None
    failures: int = 0

    def info(self) -> dict[str, Any]:
        """The ``subscription_info`` shape pywebpush expects."""
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }


class PushSubscriptionStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(
            self.engine, tables=[PushSubscriptionRow.__table__]
        )

    def upsert(
        self, client_id: str, endpoint: str, p256dh: str, auth: str, now: datetime
    ) -> tuple[PushSubscriptionRow, bool]:
        """Store or refresh a subscription. Returns the row and whether it is new.

        An endpoint that moves to another ``client_id`` (the browser cleared its
        storage and made a new id) follows the new id. Raises ``LimitReached`` when
        the client already has ``MAX_SUBSCRIPTIONS_PER_CLIENT`` other endpoints.
        """
        now = now.astimezone(UTC)
        with Session(self.engine) as session:
            row = session.exec(
                select(PushSubscriptionRow).where(
                    PushSubscriptionRow.endpoint == endpoint
                )
            ).first()
            created = row is None
            if row is None:
                count = len(
                    session.exec(
                        select(PushSubscriptionRow.id).where(
                            PushSubscriptionRow.client_id == client_id
                        )
                    ).all()
                )
                if count >= MAX_SUBSCRIPTIONS_PER_CLIENT:
                    raise LimitReached(
                        f"A client may hold at most {MAX_SUBSCRIPTIONS_PER_CLIENT} "
                        "push subscriptions."
                    )
                row = PushSubscriptionRow(
                    client_id=client_id,
                    endpoint=endpoint,
                    p256dh=p256dh,
                    auth=auth,
                    created_at=now,
                )
            else:
                row.client_id = client_id
                row.p256dh = p256dh
                row.auth = auth
                row.failures = 0
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            if created:
                log.info("PUSH subscription %s new for client %s", row.id, client_id)
            return row, created

    def for_client(self, client_id: str) -> list[PushSubscriptionRow]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(PushSubscriptionRow)
                .where(PushSubscriptionRow.client_id == client_id)
                .order_by(col(PushSubscriptionRow.id))
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def count(self, client_id: str) -> int:
        return len(self.for_client(client_id))

    def record_success(self, subscription_id: int, now: datetime) -> None:
        with Session(self.engine) as session:
            row = session.get(PushSubscriptionRow, subscription_id)
            if row is None:
                return
            row.last_ok_at = now.astimezone(UTC)
            row.failures = 0
            session.add(row)
            session.commit()

    def record_failure(self, subscription_id: int) -> bool:
        """Count one failed send. Returns True when the subscription was dropped."""
        with Session(self.engine) as session:
            row = session.get(PushSubscriptionRow, subscription_id)
            if row is None:
                return False
            row.failures += 1
            if row.failures >= MAX_FAILURES:
                session.delete(row)
                session.commit()
                log.info(
                    "PUSH subscription %s dropped after %d failures",
                    subscription_id,
                    row.failures,
                )
                return True
            session.add(row)
            session.commit()
            return False

    def delete(self, subscription_id: int) -> None:
        with Session(self.engine) as session:
            row = session.get(PushSubscriptionRow, subscription_id)
            if row is not None:
                session.delete(row)
                session.commit()


# ------------------------------------------------------------------- VAPID keys


@dataclass(frozen=True)
class VapidKeys:
    """The three ``VAPID_*`` variables, in the browser's base64url format."""

    public_key: str
    private_key: str
    subject: str


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_vapid_keys(subject: str = "mailto:team@example.com") -> VapidKeys:
    """A fresh P-256 key pair: the public key as the uncompressed point the browser's
    ``applicationServerKey`` takes, the private key as its raw 32 bytes."""
    vapid = Vapid()
    vapid.generate_keys()
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    public = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return VapidKeys(_b64url(public), _b64url(private), subject)


def public_key_of(private_key: str) -> str:
    """The base64url public key that belongs to a base64url private key."""
    vapid = Vapid.from_string(private_key)
    return _b64url(
        vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )


def vapid_keys_from_env(environ: Mapping[str, str] = os.environ) -> VapidKeys | None:
    """The configured keys, or None (with one log line) when push is not set up.

    A public key that does not belong to the private key is a configuration slip
    that would make every browser reject the subscription, so it is refused here.
    """
    public = (environ.get("VAPID_PUBLIC_KEY") or "").strip()
    private = (environ.get("VAPID_PRIVATE_KEY") or "").strip()
    subject = (environ.get("VAPID_SUBJECT") or "").strip()
    if not public or not private:
        log.warning("PUSH disabled: VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are not set")
        return None
    if not subject:
        log.warning("PUSH disabled: VAPID_SUBJECT (a mailto: address) is not set")
        return None
    try:
        derived = public_key_of(private)
    except Exception:
        log.exception("PUSH disabled: VAPID_PRIVATE_KEY is not a valid key")
        return None
    if derived != public.rstrip("="):
        log.error(
            "PUSH disabled: VAPID_PUBLIC_KEY does not belong to VAPID_PRIVATE_KEY"
        )
        return None
    return VapidKeys(public, private, subject)


# ------------------------------------------------------------------- sending


class PushSender:
    """Sends payloads to a client's subscriptions; a no-op without keys."""

    def __init__(
        self,
        subscriptions: PushSubscriptionStore,
        keys: VapidKeys | None,
        *,
        send: Callable[..., Any] = webpush,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.subscriptions = subscriptions
        self.keys = keys
        self.send = send
        self.clock = clock

    @property
    def enabled(self) -> bool:
        return self.keys is not None

    @property
    def public_key(self) -> str | None:
        return self.keys.public_key if self.keys else None

    def send_to_client(self, client_id: str, payload: Mapping[str, Any]) -> int:
        """Deliver ``payload`` to every subscription of a client. Returns the count
        delivered. Never raises: push problems are logged and counted per endpoint."""
        if self.keys is None:
            log.info("PUSH skipped for client %s: push is not configured", client_id)
            return 0
        delivered = 0
        for row in self.subscriptions.for_client(client_id):
            if self._send_one(row, payload):
                delivered += 1
        return delivered

    def _send_one(self, row: PushSubscriptionRow, payload: Mapping[str, Any]) -> bool:
        assert self.keys is not None and row.id is not None
        try:
            self.send(
                subscription_info=row.info(),
                data=json.dumps(payload),
                vapid_private_key=self.keys.private_key,
                vapid_claims={"sub": self.keys.subject},
                ttl=PUSH_TTL_S,
                timeout=PUSH_TIMEOUT_S,
            )
        except WebPushException as error:
            status = error.status_code
            if status in (404, 410):
                self.subscriptions.delete(row.id)
                log.info("PUSH subscription %s gone (%s); removed", row.id, status)
            else:
                dropped = self.subscriptions.record_failure(row.id)
                log.warning(
                    "PUSH to subscription %s failed (%s)%s",
                    row.id,
                    status if status is not None else type(error).__name__,
                    "; dropped" if dropped else "",
                )
            return False
        except Exception:  # a bug in the library or a bad key must not stop the watcher
            log.exception("PUSH to subscription %s failed", row.id)
            self.subscriptions.record_failure(row.id)
            return False
        self.subscriptions.record_success(row.id, self.clock())
        return True


# ------------------------------------------------------------------- messages


def route_url(row: SavedRouteRow) -> str:
    """The planner URL that opens this trip (relative to the web app)."""
    query = urlencode(
        {
            "from": f"{row.from_lat:.6f},{row.from_lng:.6f}",
            "to": f"{row.to_lat:.6f},{row.to_lng:.6f}",
            "profile": row.profile,
            "saved": row.id,
        },
        safe=",",  # lat,lng as /api/route takes it
    )
    return f"/?{query}"


def _minutes(value: float | None) -> str:
    return "?" if value is None else f"{value:.0f}"


def notice_payload(
    row: SavedRouteRow, notice: Notice, profile_label: str
) -> dict[str, Any]:
    """The JSON the service worker shows. ``tag`` replaces an older alert for the
    same route instead of stacking them."""
    name = row.name
    if notice == "impossible":
        title = f"No route to {name}"
        body = (
            f"No {profile_label} route to {name} is possible right now. "
            "Open OpenPath to see the barriers."
        )
    elif notice == "clear":
        title = f"Clear again: {name}"
        body = f"Your usual route to {name} is clear again"
        if row.last_eta_min is not None:
            body += f" (about {_minutes(row.last_eta_min)} min)"
        body += "."
    else:
        title = f"Route changed: {name}"
        body = f"Your usual route to {name} has changed"
        if row.last_eta_min is not None and row.baseline_eta_min is not None:
            # Whole minutes, as shown, so "+2 min" agrees with "3 min" and "5 min".
            extra = round(row.last_eta_min) - round(row.baseline_eta_min)
            body += f"; it now takes about {_minutes(row.last_eta_min)} min"
            if extra >= 1:
                body += f" (+{extra} min)"
        body += ". Open OpenPath for the new route."
    return {
        "title": title,
        "body": body,
        "url": route_url(row),
        "tag": f"route-{row.id}",
        "saved_route_id": row.id,
        "status": notice,
        "eta_min": row.last_eta_min,
    }


def alert_test_payload(client_id: str) -> dict[str, Any]:
    return {
        "title": "OpenPath alerts are on",
        "body": "You will hear from us when a saved route changes.",
        "url": "/alerts",
        "tag": "openpath-test",
        "status": "test",
        "client_id": client_id,
    }


# -------------------------------------------------------------------- watcher


def hazard_key(hazards: Sequence[Any]) -> frozenset[tuple[str, str]]:
    """What routing reads from the hazards on one edge: each item and its effect."""
    return frozenset(
        (hazard.item_id, getattr(hazard.effect, "value", str(hazard.effect)))
        for hazard in hazards
    )


def changed_edges(before: HazardMap | None, after: HazardMap) -> set[int]:
    """Edges whose hazards were added, removed or changed effect between two maps."""
    if (
        before is None or before is after
    ):  # providers return the same map until it changes
        return set()
    changed: set[int] = set()
    for edge in before.keys() | after.keys():
        old, new = before.get(edge), after.get(edge)
        if old is None or new is None:
            if old or new:  # an edge that lost or gained its only hazards
                changed.add(int(edge))
        elif hazard_key(old) != hazard_key(new):
            changed.add(int(edge))
    return changed


class RouteWatcher:
    """Rechecks saved routes when hazards change and pushes when it matters.

    ``hazard_clock`` is the app's clock (frozen in replay mode) and feeds the hazard
    provider and the router; ``clock`` is wall time for rate limits and bookkeeping.
    ``tick()`` is what the thread runs; tests call it directly.
    """

    def __init__(
        self,
        routes: SavedRouteStore,
        router: Router,
        hazards: Callable[[datetime], HazardMap],
        sender: PushSender,
        *,
        hazard_clock: Callable[[], datetime] = utcnow,
        clock: Callable[[], datetime] = utcnow,
        interval_s: float = WATCH_INTERVAL_S,
        min_push_interval_s: float = MIN_PUSH_INTERVAL_S,
        warmup_s: float = WARMUP_S,
    ) -> None:
        self.routes = routes
        self.router = router
        self.hazards = hazards
        self.sender = sender
        self.hazard_clock = hazard_clock
        self.clock = clock
        self.interval_s = interval_s
        self.min_push_interval = timedelta(seconds=min_push_interval_s)
        self.warmup = timedelta(seconds=warmup_s)
        self.started_at: datetime | None = None  # the first tick, for the warm-up
        self.checked_all = False  # the one full recheck after the warm-up is done
        self.previous: HazardMap | None = None
        self.by_edge: dict[int, set[int]] = {}
        self.impossible: set[int] = set()
        self.pending: set[int] = set()  # routes owing a push held by the rate limit
        self.dirty = True  # the index must be rebuilt from the database
        self.ticks = 0
        self.pushes = 0
        self._fresh: set[int] = set()  # routes saved since the last tick
        self._fresh_lock = threading.Lock()  # the API adds to _fresh from its threads
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="route-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # never let the thread die
                log.exception("WATCH tick failed; trying again next time")
            self._stop.wait(self.interval_s)

    def mark_dirty(self, added: int | None = None) -> None:
        """A route was saved (``added``) or deleted: rebuild the edge index on the
        next tick, and recheck a new route once, in case hazards moved between its
        baseline and the watcher's last look."""
        if added is not None:
            with self._fresh_lock:
                self._fresh.add(added)
        self.dirty = True

    def warming(self) -> bool:
        """True during the warm-up after startup; the first call starts it."""
        if self.started_at is None:
            self.started_at = self.clock()
        return self.clock() - self.started_at < self.warmup

    # -- one pass ------------------------------------------------------------

    def tick(self) -> list[int]:
        """One pass: read hazards, recheck the routes that changed edges touch,
        send what is due. Returns the ids of the routes rechecked."""
        with self._lock:
            self.ticks += 1
            now = self.hazard_clock()
            try:
                current = self.hazards(now)
            except Exception:
                log.exception("WATCH could not read hazards; keeping the last map")
                return []
            with self._fresh_lock:
                fresh, self._fresh = self._fresh, set()
            if self.dirty:
                self._rebuild_index()
            changed = changed_edges(self.previous, current)
            self.previous = current
            if self.warming():
                return []
            if not self.checked_all:
                # After a start or a wake-up the stored state may be stale: check
                # every route once.
                self.checked_all = True
                due = {route_id for ids in self.by_edge.values() for route_id in ids}
                due |= self.impossible
            else:
                due = fresh
                if changed:
                    due |= self.impossible
                    for edge in changed:
                        due |= self.by_edge.get(edge, set())
            if changed:
                log.info(
                    "WATCH %d edges changed; %d saved routes to recheck",
                    len(changed),
                    len(due),
                )
            for route_id in sorted(due):
                try:
                    self._recheck(route_id, current, now)
                except Exception:  # one odd route must not stop the others
                    log.exception("WATCH recheck of route %s failed", route_id)
            self._flush_pending()
            return sorted(due)

    def _rebuild_index(self) -> None:
        # Cleared first, so a route saved while this runs marks it dirty again.
        self.dirty = False
        rows = self.routes.all()
        self.by_edge, self.impossible = edge_index(rows)
        self.pending = {row.id for row in rows if row.pending and row.id is not None}

    def _forget(self, route_id: int) -> None:
        for ids in self.by_edge.values():
            ids.discard(route_id)
        self.impossible.discard(route_id)
        self.pending.discard(route_id)

    def _index(self, row: SavedRouteRow) -> None:
        """Keep the in-memory index in step with one rechecked row."""
        assert row.id is not None
        self._forget(row.id)
        for edge in watched_edges(row):
            self.by_edge.setdefault(edge, set()).add(row.id)
        if row.last_status == "impossible":
            self.impossible.add(row.id)
        if row.pending:
            self.pending.add(row.id)

    def best_route(self, row: SavedRouteRow, hazards: HazardMap) -> Route | None:
        result = self.router.route(
            row.from_lat, row.from_lng, row.to_lat, row.to_lng, row.profile, hazards
        )
        return result.routes[0] if result.routes else None

    def usual_route(self, row: SavedRouteRow, hazards: HazardMap) -> Usual | None:
        """The saved route's usual edges under the current hazards, for recovery."""
        if not row.baseline_edge_ids:
            return None
        profile = self.router.config.profile(row.profile)
        weights = profile_weights(self.router.network, profile, hazards)
        used = weights[[int(edge) for edge in row.baseline_edge_ids]]
        if bool((used <= 0).any()):
            return Usual(blocked=True, eta_min=None)
        return Usual(False, round(float(used.sum()) / profile.speed_m_s / 60.0, 1))

    def _recheck(self, route_id: int, hazards: HazardMap, now: datetime) -> None:
        row = self.routes.get(route_id)
        if row is None:  # deleted since the index was built
            self._forget(route_id)
            return
        best = self.best_route(row, hazards)
        decision = decide(RouteState.of(row), best, self.usual_route(row, hazards))
        fields: dict[str, Any] = {
            "last_checked_at": self.clock(),
            "last_status": decision.status,
            "last_eta_min": decision.eta_min,
            "last_edge_ids": list(decision.edge_ids),
        }
        if decision.rebaseline and best is not None:
            fields.update(
                baseline_eta_min=best.eta_min, baseline_edge_ids=list(best.edges)
            )
        if decision.changed:
            log.info(
                "CHANGE saved route %s %s -> %s (%s) eta %s -> %s min",
                row.id,
                row.last_status,
                decision.status,
                decision.notice,
                row.last_eta_min,
                decision.eta_min,
            )
            fields["pending"] = True
        updated = self.routes.update(route_id, **fields)
        if updated is None:
            self._forget(route_id)
            return
        self._index(updated)
        if decision.changed:
            self._notify(updated)

    # -- pushing -------------------------------------------------------------

    def _allowed(self, row: SavedRouteRow) -> bool:
        return (
            row.last_push_at is None
            or self.clock() - row.last_push_at >= self.min_push_interval
        )

    def _notify(self, row: SavedRouteRow) -> bool:
        """Send the push the row owes, if the rate limit allows. Otherwise it stays
        pending for ``_flush_pending``."""
        assert row.id is not None
        if not row.pending:
            self.pending.discard(row.id)
            return False
        if row.last_status == "ok" and row.notified_status == "ok":
            # Back to normal before the browser heard of the problem: nothing to say.
            self.routes.update(row.id, pending=False)
            self.pending.discard(row.id)
            return False
        if not self._allowed(row):
            log.info(
                "PUSH for saved route %s held: one push per %.0f s",
                row.id,
                self.min_push_interval.total_seconds(),
            )
            return False
        notice: Notice = "clear" if row.last_status == "ok" else row.last_status  # type: ignore[assignment]
        payload = notice_payload(row, notice, self._profile_label(row.profile))
        delivered = self.sender.send_to_client(row.client_id, payload)
        self.pushes += 1 if delivered else 0
        self.routes.update(
            row.id,
            pending=False,
            notified_status=row.last_status,
            last_push_at=self.clock(),
        )
        self.pending.discard(row.id)
        log.info(
            "PUSH saved route %s %s to %d subscription%s: %s",
            row.id,
            notice,
            delivered,
            "" if delivered == 1 else "s",
            payload["title"],
        )
        return True

    def _flush_pending(self) -> None:
        """Send the pushes the rate limit held, once their window has passed."""
        for route_id in sorted(self.pending):
            row = self.routes.get(route_id)
            if row is None:
                self._forget(route_id)
            elif self._allowed(row) or not row.pending:
                self._notify(row)

    def _profile_label(self, key: str) -> str:
        try:
            return self.router.config.profile(key).label
        except KeyError:
            return key


# -------------------------------------------------------------------- wiring


@dataclass
class PushServices:
    """What ``main.py`` keeps on ``app.state`` for the API and the watcher."""

    subscriptions: PushSubscriptionStore
    routes: SavedRouteStore
    sender: PushSender
    watcher: RouteWatcher | None


def start_route_watcher(
    engine: Engine,
    router: Router | None,
    hazards: Callable[[datetime], HazardMap] | None,
    *,
    hazard_clock: Callable[[], datetime] = utcnow,
    run: bool = True,
    environ: Mapping[str, str] = os.environ,
) -> PushServices:
    """Create the tables, the sender and (with a router) the watcher; start its
    thread when ``run`` is true (``SCHEDULER=off`` keeps it idle for tests)."""
    subscriptions = PushSubscriptionStore(engine)
    subscriptions.create_tables()
    routes = SavedRouteStore(engine)
    routes.create_tables()
    sender = PushSender(subscriptions, vapid_keys_from_env(environ))
    watcher: RouteWatcher | None = None
    if router is not None:
        watcher = RouteWatcher(
            routes,
            router,
            hazards if hazards is not None else (lambda now: {}),
            sender,
            hazard_clock=hazard_clock,
            warmup_s=WARMUP_S if run else 0.0,
        )
        if run:
            watcher.start()
    return PushServices(subscriptions, routes, sender, watcher)


# ----------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cloud.app.push",
        description="Print a fresh VAPID key pair for Web Push (never commit the values).",
    )
    parser.add_argument(
        "--generate-keys",
        action="store_true",
        help="print VAPID_* lines for the Render dashboard",
    )
    parser.add_argument(
        "--subject",
        default="mailto:team@example.com",
        help="the VAPID_SUBJECT contact address",
    )
    args = parser.parse_args(argv)
    if not args.generate_keys:
        parser.print_help()
        return 2
    keys = generate_vapid_keys(args.subject)
    print(f"VAPID_PUBLIC_KEY={keys.public_key}")
    print(f"VAPID_PRIVATE_KEY={keys.private_key}")
    print(f"VAPID_SUBJECT={keys.subject}")
    print(f"NEXT_PUBLIC_VAPID_PUBLIC_KEY={keys.public_key}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
