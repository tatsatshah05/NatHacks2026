"""``WS /ws``: live updates for an open route (OP-303).

What a client gets
    ``source_status``       after every scheduler run of a source: did it succeed, how
                            many items it holds and how old its data is.
    ``obstruction_changed`` when the set of active obstructions changes: the items
                            added and removed since the last hazard rebuild.
    ``route_changed``       for the route the connection subscribed to, when a
                            hazard rebuild gives it a different set of paths, makes
                            it impossible, or makes it at least 20% slower. The
                            payload carries the new summary and one plain sentence,
                            for example "Construction closure on 102 Avenue now
                            blocks your route; the new route is 3 min longer." The
                            client then fetches ``GET /api/route`` again for the
                            full route.
    ``heartbeat``           every ``HEARTBEAT_S`` seconds, so a dead connection is
                            noticed on both sides.

What a client sends
    ``{"type": "subscribe", "route": {"from": [lat, lng], "to": [lat, lng],
    "profile": "wheelchair"}}`` (one route per connection; a new subscribe replaces
    it), ``{"type": "unsubscribe"}`` and ``{"type": "ping"}``. The server answers
    ``subscribed`` (with the route's current summary, the baseline later changes are
    measured against), ``unsubscribed``, ``pong`` or ``error``.

How it is wired (``main.py``)
    ``attach(app)`` builds the ``LiveHub`` on ``app.state.live``. It registers with
    the scheduler (``add_listener``) for ``source_status`` and with the matcher
    (``HazardProvider.on_rebuild``) for the other two. While at least one client is
    connected, the hub asks the hazard provider for the map every ``TICK_S`` seconds,
    so rebuilds happen even when nobody requests a route. Re-routing runs in a
    worker thread, one re-route per subscription per rebuild, and never on the
    event loop or inside a request.

Safety
    Nothing here can affect ``/api/route``: the hub only reads the providers, every
    send is wrapped, a socket that fails to receive a message is dropped, and a
    failing callback is logged where it is called (matcher, scheduler) without
    reaching them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .camera_ice_routing import CameraIceHazardProvider
from .matching import REFRESH_S, HazardProvider, MatchResult
from .routing import EdgeHazard, HazardMap, Router, RoutingError
from .scheduler import SourceStatus
from .schemas import Effect

log = logging.getLogger("openpath.live")

router = APIRouter(tags=["live"])

HEARTBEAT_S = 25.0  # the server sends a heartbeat this often
TICK_S = (
    REFRESH_S  # while clients are connected, the hazard map is refreshed this often
)
ETA_GROWTH = 0.2  # the same paths, but at least this much slower, counts as a change


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------- messages in


def _check_point(value: tuple[float, float]) -> tuple[float, float]:
    lat, lng = value
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("a point is [lat, lng] in decimal degrees")
    return value


class RouteSpec(BaseModel):
    """The route a connection follows: ``from`` and ``to`` are ``[lat, lng]``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: tuple[float, float] = Field(alias="from")
    to: tuple[float, float]
    profile: str = "standard"

    def key(self) -> RouteKey:
        _check_point(self.from_)
        _check_point(self.to)
        return RouteKey(
            self.from_[0], self.from_[1], self.to[0], self.to[1], self.profile
        )


class SubscribeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["subscribe"]
    route: RouteSpec


class UnsubscribeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["unsubscribe"]


class PingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ping"]


ClientMessage = Annotated[
    SubscribeMessage | UnsubscribeMessage | PingMessage, Field(discriminator="type")
]
_client_message: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# -------------------------------------------------------------- messages out


class ObstructionBrief(BaseModel):
    """What ``obstruction_changed`` says about one item."""

    id: str
    kind: str | None  # None only if the item's record was unavailable
    effect: str
    title: str


class RouteSummary(BaseModel):
    """The best route for a subscription, in a few numbers."""

    possible: bool  # False: no route for this profile between these points
    eta_min: float
    distance_m: float
    reason: str | None  # why the route detours, from the routing engine


class HelloEvent(BaseModel):
    type: Literal["hello"] = "hello"
    heartbeat_s: float
    at: datetime


class HeartbeatEvent(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    at: datetime


class PongEvent(BaseModel):
    type: Literal["pong"] = "pong"
    at: datetime


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


class SubscribedEvent(BaseModel):
    type: Literal["subscribed"] = "subscribed"
    route: RouteSpec
    summary: RouteSummary
    at: datetime


class UnsubscribedEvent(BaseModel):
    type: Literal["unsubscribed"] = "unsubscribed"


class SourceStatusEvent(BaseModel):
    type: Literal["source_status"] = "source_status"
    source: str
    ok: bool  # this run succeeded
    stale: bool  # the source has failed enough times that its data is suspect
    item_count: int
    age_s: float | None  # seconds since the last success, None before any success
    last_error: str | None
    at: datetime


class ObstructionChangedEvent(BaseModel):
    type: Literal["obstruction_changed"] = "obstruction_changed"
    added: list[ObstructionBrief]
    removed: list[ObstructionBrief]
    at: datetime


class RouteChangedEvent(BaseModel):
    type: Literal["route_changed"] = "route_changed"
    route: RouteSpec
    summary: RouteSummary  # the route now
    previous: RouteSummary  # the route the client last knew about
    change: str  # one sentence, also what the client announces
    at: datetime


Event = (
    HelloEvent
    | HeartbeatEvent
    | PongEvent
    | ErrorEvent
    | SubscribedEvent
    | UnsubscribedEvent
    | SourceStatusEvent
    | ObstructionChangedEvent
    | RouteChangedEvent
)


# ------------------------------------------------------------ route tracking


@dataclass(frozen=True)
class RouteKey:
    from_lat: float
    from_lng: float
    to_lat: float
    to_lng: float
    profile: str

    def spec(self) -> RouteSpec:
        return RouteSpec(
            from_=(self.from_lat, self.from_lng),
            to=(self.to_lat, self.to_lng),
            profile=self.profile,
        )


@dataclass(frozen=True)
class RouteSnapshot:
    """The best route at one moment: what later rebuilds are compared against."""

    edges: frozenset[int] | None  # None when no route is possible
    eta_min: float
    distance_m: float
    reason: str | None
    avoided: tuple[EdgeHazard, ...] = ()

    @classmethod
    def of(cls, router_: Router, key: RouteKey, hazards: HazardMap) -> RouteSnapshot:
        """Route ``key`` on ``hazards``; raises ``RoutingError`` for a bad request."""
        result = router_.route(
            key.from_lat, key.from_lng, key.to_lat, key.to_lng, key.profile, hazards
        )
        if not result.routes:
            return cls(None, 0.0, 0.0, None)
        best = result.routes[0]
        return cls(
            frozenset(best.edges),
            best.eta_min,
            best.distance_m,
            best.reason,
            tuple(best.hazards_avoided),
        )

    def summary(self) -> RouteSummary:
        return RouteSummary(
            possible=self.edges is not None,
            eta_min=self.eta_min,
            distance_m=self.distance_m,
            reason=self.reason,
        )


def route_changed(
    before: RouteSnapshot, after: RouteSnapshot, eta_growth: float = ETA_GROWTH
) -> bool:
    """The rules: different paths, no route any more (or again), or ETA up 20%."""
    if before.edges is None:
        return after.edges is not None
    if after.edges is None or after.edges != before.edges:
        return True
    return after.eta_min > before.eta_min and after.eta_min >= before.eta_min * (
        1 + eta_growth
    )


def _verb(effect: str) -> str:
    return "blocks" if effect == Effect.BLOCKED else "affects"


def _minutes(value: float) -> int:
    """Whole minutes, rounding halves up (ETAs come in tenths, so 2.5 is common)."""
    return int(round(abs(value), 1) + 0.5)  # tenths first: 5.1 - 2.6 is 2.4999...


def _tail(delta_min: float) -> str:
    minutes = _minutes(delta_min)
    if minutes == 0:
        return "the new route takes about the same time"
    return f"the new route is {minutes} min {'longer' if delta_min > 0 else 'shorter'}"


def describe_change(
    before: RouteSnapshot,
    after: RouteSnapshot,
    *,
    profile_label: str,
    hazards: HazardMap,
    added: Sequence[ObstructionBrief] = (),
    removed: Sequence[ObstructionBrief] = (),
) -> str:
    """One plain sentence about what changed and what it costs.

    The cause, in order of preference: an item added since the last rebuild whose
    hazards sit on the route the client knew (``before.edges``); a hazard the new
    route avoids that the old one did not; an item removed since the last rebuild
    that the old route was avoiding; otherwise a generic lead-in.
    """
    if after.edges is None:
        cause = _new_on_route(before, hazards, added) or _newly_avoided(before, after)
        head = (
            f"{cause.title} now {_verb(cause.effect)} your route"
            if cause
            else "A new barrier blocks your route"
        )
        return f"{head}; no route is possible for the {profile_label} profile."
    if before.edges is None:
        return (
            f"A route is possible again; it takes about {_minutes(after.eta_min)} min."
        )
    delta = after.eta_min - before.eta_min
    cause = _new_on_route(before, hazards, added) or _newly_avoided(before, after)
    if cause is not None:
        head = f"{cause.title} now {_verb(cause.effect)} your route"
    else:
        cleared = _cleared(before, after, removed)
        head = (
            f"{cleared.title} has cleared"
            if cleared
            else "Conditions on your route changed"
        )
    return f"{head}; {_tail(delta)}."


def _new_on_route(
    before: RouteSnapshot, hazards: HazardMap, added: Sequence[ObstructionBrief]
) -> ObstructionBrief | None:
    if before.edges is None or not added:
        return None
    on_old_route = {
        hazard.item_id for edge in before.edges for hazard in hazards.get(edge, ())
    }
    for brief in added:
        if brief.id in on_old_route:
            return brief
    return None


def _newly_avoided(
    before: RouteSnapshot, after: RouteSnapshot
) -> ObstructionBrief | None:
    known = {hazard.item_id for hazard in before.avoided}
    for hazard in after.avoided:
        if hazard.item_id not in known:
            return ObstructionBrief(
                id=hazard.item_id,
                kind=None,
                effect=hazard.effect.value,
                title=hazard.title or hazard.effect.value.replace("_", " "),
            )
    return None


def _cleared(
    before: RouteSnapshot, after: RouteSnapshot, removed: Sequence[ObstructionBrief]
) -> ObstructionBrief | None:
    still = {hazard.item_id for hazard in after.avoided}
    was_avoiding = {hazard.item_id for hazard in before.avoided} - still
    for brief in removed:
        if brief.id in was_avoiding:
            return brief
    return None


def active_briefs(result: MatchResult) -> dict[str, ObstructionBrief]:
    """Every active item in a rebuild, by id, from the report index when it exists."""
    briefs: dict[str, ObstructionBrief] = {}
    if result.items is not None:
        for record in result.items.records:
            briefs[record.id] = ObstructionBrief(
                id=record.id,
                kind=record.kind.value if record.kind else None,
                effect=record.effect.value,
                title=record.title,
            )
    # Without an index (its build failed), fall back to what the hazards carry.
    for on_edge in result.hazards.values():
        for hazard in on_edge:
            briefs.setdefault(
                hazard.item_id,
                ObstructionBrief(
                    id=hazard.item_id,
                    kind=None,
                    effect=hazard.effect.value,
                    title=hazard.title,
                ),
            )
    for item_id in result.matches:
        briefs.setdefault(
            item_id,
            ObstructionBrief(id=item_id, kind=None, effect="caution", title=item_id),
        )
    return briefs


def best_route_router(router: Router) -> Router:
    """A router on the same network and profiles that stops at the best route."""
    alternates = router.config.alternates.model_copy(update={"max_routes": 1})
    return Router(
        router.network, router.config.model_copy(update={"alternates": alternates})
    )


# ----------------------------------------------------------------- the hub


class Socket(Protocol):
    """What the hub needs from a connection (a Starlette ``WebSocket`` has both)."""

    async def send_json(self, data: Any) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


@dataclass
class Subscription:
    key: RouteKey
    baseline: RouteSnapshot


@dataclass
class Connection:
    id: int
    socket: Socket
    subscription: Subscription | None = None
    # One message at a time per socket, so a heartbeat never interleaves an event.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LiveHub:
    """Every open ``/ws`` connection, and the fan-out of events to them.

    ``router`` and ``provider`` may be None (a bare checkout without the network);
    then ``subscribe`` answers with an error and only ``source_status`` flows.
    """

    def __init__(
        self,
        *,
        router: Router | None = None,
        provider: Callable[[datetime], HazardMap] | None = None,
        clock: Callable[[], datetime] = utcnow,
        heartbeat_s: float = HEARTBEAT_S,
        tick_s: float = TICK_S,
    ) -> None:
        self.router = router
        # Re-routing needs only the best route, which is the first one the search
        # finds whatever max_routes says, so skip the alternates (about 4x faster).
        self._best = best_route_router(router) if router is not None else None
        self.provider = provider
        self.clock = clock
        self.heartbeat_s = heartbeat_s
        self.tick_s = tick_s
        self.connections: dict[int, Connection] = {}
        self._next_id = 1
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._tick: asyncio.Task[None] | None = None
        self._active: dict[str, ObstructionBrief] | None = None  # the last rebuild
        self._route_lock = threading.Lock()  # one re-route at a time
        self.rebuilds = 0  # rebuilds seen, for tests and logs

    # -- connections -----------------------------------------------------------

    def add(self, socket: Socket) -> Connection:
        """Register an accepted socket. Call on the event loop."""
        self._loop = asyncio.get_running_loop()
        conn = Connection(self._next_id, socket)
        self._next_id += 1
        self.connections[conn.id] = conn
        log.info("LIVE connect id=%d clients=%d", conn.id, len(self.connections))
        if self.provider is not None and (self._tick is None or self._tick.done()):
            self._tick = self._spawn(self._tick_loop())
        return conn

    def drop(self, conn: Connection, reason: str = "closed") -> None:
        if self.connections.pop(conn.id, None) is not None:
            log.info(
                "LIVE drop id=%d reason=%s clients=%d",
                conn.id,
                reason,
                len(self.connections),
            )

    async def send(self, conn: Connection, event: Event) -> bool:
        """Send one event; a socket that fails is dropped. Returns True when sent."""
        if conn.id not in self.connections:
            return False
        try:
            async with conn.lock:
                await conn.socket.send_json(
                    event.model_dump(mode="json", by_alias=True)
                )
        except Exception as error:  # noqa: BLE001 - any failure means a dead socket
            self.drop(conn, f"send failed: {type(error).__name__}")
            return False
        return True

    async def broadcast(self, event: Event) -> int:
        """Send one event to every connection. Returns how many received it."""
        sent = await asyncio.gather(
            *(self.send(conn, event) for conn in list(self.connections.values()))
        )
        return sum(1 for ok in sent if ok)

    def emit(self, event: Event) -> None:
        """Broadcast from any thread; a no-op before the first connection."""
        self._schedule(self.broadcast(event))

    async def drain(self) -> None:
        """Wait for every scheduled broadcast and re-route (tests, shutdown).

        The refresh timer (``_tick``) runs for as long as clients are connected, so
        it is not waited for.
        """
        while pending := [
            task for task in self._tasks if not task.done() and task is not self._tick
        ]:
            await asyncio.gather(*pending, return_exceptions=True)

    # -- scheduling ------------------------------------------------------------

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        loop = self._loop or asyncio.get_running_loop()
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _schedule(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run ``coro`` on the hub's loop from any thread; drop it if there is none."""
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                coro.close()
                return
            self._loop = loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is loop:
                self._spawn(coro)
            else:
                loop.call_soon_threadsafe(self._spawn, coro)
        except RuntimeError:  # the loop is closed: shutting down
            coro.close()

    async def _tick_loop(self) -> None:
        """Keep the hazard map fresh while anyone is connected (see module doc)."""
        while self.connections and self.provider is not None:
            try:
                await asyncio.sleep(self.tick_s)
                if self.connections:
                    await asyncio.to_thread(self.provider, self.clock())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("LIVE hazard tick failed")

    # -- sources ---------------------------------------------------------------

    def on_source_run(self, status: SourceStatus, ok: bool) -> None:
        """``Scheduler.add_listener`` callback: one ``source_status`` per run."""
        now = self.clock()
        age = (
            round((now - status.last_success).total_seconds(), 1)
            if status.last_success is not None
            else None
        )
        self.emit(
            SourceStatusEvent(
                source=status.source,
                ok=ok,
                stale=status.stale,
                item_count=status.item_count,
                age_s=age,
                last_error=status.last_error,
                at=now,
            )
        )

    # -- hazards ---------------------------------------------------------------

    def on_rebuild(self, result: MatchResult) -> None:
        """``HazardProvider.on_rebuild`` callback, in the matcher's thread.

        Diffs the active items against the previous rebuild, then hands the
        broadcast and the re-routes to the event loop (which runs them in a worker
        thread). Cheap, so the matcher's lock is held only a moment longer.
        """
        self.rebuilds += 1
        current = active_briefs(result)
        previous = self._active
        self._active = current
        added: list[ObstructionBrief] = []
        removed: list[ObstructionBrief] = []
        if previous is not None:
            added = [current[i] for i in sorted(current.keys() - previous.keys())]
            removed = [previous[i] for i in sorted(previous.keys() - current.keys())]
        for brief in added:
            log.info("LIVE obstruction added %s %s", brief.id, brief.title)
        for brief in removed:
            log.info("LIVE obstruction removed %s", brief.id)
        if not self.connections:
            return
        self._schedule(self._after_rebuild(added, removed, result.built_at))

    async def _after_rebuild(
        self,
        added: list[ObstructionBrief],
        removed: list[ObstructionBrief],
        built_at: datetime,
    ) -> None:
        if added or removed:
            await self.broadcast(
                ObstructionChangedEvent(added=added, removed=removed, at=built_at)
            )
        subscribed = [c for c in self.connections.values() if c.subscription]
        if not subscribed or self._best is None:
            return
        try:
            events = await asyncio.to_thread(
                self._recheck_all, subscribed, added, removed
            )
        except Exception:
            log.exception("LIVE re-route failed; clients keep their routes")
            return
        for conn, event in events:
            if await self.send(conn, event):
                log.info("LIVE route_changed id=%d %s", conn.id, event.change)

    def hazards(self) -> HazardMap:
        """The current hazard map (camera risks included), or none if that fails.

        Read through the app's provider, as ``/api/route`` does. Right after a
        rebuild the matcher is not due again for ``refresh_s``, so this read does
        not start another rebuild.
        """
        if self.provider is None:
            return {}
        try:
            return self.provider(self.clock())
        except Exception:
            log.exception("LIVE hazard provider failed; routing without hazards")
            return {}

    def _recheck_all(
        self,
        connections: Sequence[Connection],
        added: Sequence[ObstructionBrief],
        removed: Sequence[ObstructionBrief],
    ) -> list[tuple[Connection, RouteChangedEvent]]:
        """One re-route per subscription, in this worker thread."""
        events: list[tuple[Connection, RouteChangedEvent]] = []
        hazards = self.hazards()
        with self._route_lock:
            for conn in connections:
                subscription = conn.subscription
                if subscription is None:
                    continue
                event = self._recheck(subscription, hazards, added, removed)
                if event is not None:
                    events.append((conn, event))
        return events

    def _recheck(
        self,
        subscription: Subscription,
        hazards: HazardMap,
        added: Sequence[ObstructionBrief],
        removed: Sequence[ObstructionBrief],
    ) -> RouteChangedEvent | None:
        assert self._best is not None
        key, before = subscription.key, subscription.baseline
        try:
            after = RouteSnapshot.of(self._best, key, hazards)
        except RoutingError as error:  # the points were fine before: log, keep going
            log.warning("LIVE re-route %s failed: %s", key, error)
            return None
        if not route_changed(before, after):
            return None
        subscription.baseline = after  # announce each change once
        change = describe_change(
            before,
            after,
            profile_label=self._profile_label(key.profile),
            hazards=hazards,
            added=added,
            removed=removed,
        )
        return RouteChangedEvent(
            route=key.spec(),
            summary=after.summary(),
            previous=before.summary(),
            change=change,
            at=self.clock(),
        )

    def _profile_label(self, profile: str) -> str:
        try:
            assert self.router is not None
            return self.router.config.profile(profile).label
        except Exception:  # noqa: BLE001 - a label is cosmetic
            return profile

    # -- subscriptions ----------------------------------------------------------

    def baseline(self, key: RouteKey) -> RouteSnapshot:
        """Route ``key`` now: the baseline later changes are measured against.

        Blocking (Dijkstra); call it in a worker thread. Raises ``RoutingError`` for a
        request routing cannot serve, and ``RuntimeError`` without a router.
        """
        if self._best is None:
            raise RuntimeError(
                "Routing is not available: the walking network is not loaded."
            )
        hazards = self.hazards()
        with self._route_lock:
            return RouteSnapshot.of(self._best, key, hazards)

    async def subscribe(self, conn: Connection, spec: RouteSpec) -> Event:
        """Replace the connection's route; the reply to send back."""
        try:
            key = spec.key()
            snapshot = await asyncio.to_thread(self.baseline, key)
        except (RoutingError, RuntimeError, ValueError) as error:
            return ErrorEvent(message=str(error))
        conn.subscription = Subscription(key, snapshot)
        log.info(
            "LIVE subscribe id=%d %s %s->%s eta=%.1f",
            conn.id,
            key.profile,
            (key.from_lat, key.from_lng),
            (key.to_lat, key.to_lng),
            snapshot.eta_min,
        )
        return SubscribedEvent(
            route=key.spec(), summary=snapshot.summary(), at=self.clock()
        )

    async def handle(self, conn: Connection, text: str) -> Event:
        """Act on one client message; the reply to send back."""
        try:
            message = _client_message.validate_json(text)
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ()))
            return ErrorEvent(message=f"invalid message: {where}: {first['msg']}")
        if isinstance(message, SubscribeMessage):
            return await self.subscribe(conn, message.route)
        if isinstance(message, UnsubscribeMessage):
            conn.subscription = None
            return UnsubscribedEvent()
        return PongEvent(at=self.clock())

    # -- a connection's life ----------------------------------------------------

    async def serve(self, websocket: WebSocket) -> None:
        """Run one accepted connection until it closes or dies."""
        conn = self.add(websocket)
        try:
            await self.send(
                conn, HelloEvent(heartbeat_s=self.heartbeat_s, at=self.clock())
            )
            receive = asyncio.ensure_future(self._receive_loop(conn, websocket))
            beat = asyncio.ensure_future(self._heartbeat_loop(conn))
            done, pending = await asyncio.wait(
                {receive, beat}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                error = None if task.cancelled() else task.exception()
                if error is not None and not isinstance(error, WebSocketDisconnect):
                    log.warning("LIVE id=%d ended: %r", conn.id, error)
        finally:
            self.drop(conn)
            with contextlib.suppress(Exception):  # already closed by the client
                await websocket.close()

    async def _receive_loop(self, conn: Connection, websocket: WebSocket) -> None:
        """Answer the client's messages until it disconnects."""
        try:
            while conn.id in self.connections:
                text = await websocket.receive_text()
                reply = await self.handle(conn, text)
                await self.send(conn, reply)
        except WebSocketDisconnect:
            return

    async def _heartbeat_loop(self, conn: Connection) -> None:
        while conn.id in self.connections:
            await asyncio.sleep(self.heartbeat_s)
            if not await self.send(conn, HeartbeatEvent(at=self.clock())):
                return


# ------------------------------------------------------------------ wiring


def matcher_of(provider: object) -> HazardProvider | None:
    """The OP-206 matcher behind ``app.state.hazard_provider``, if any."""
    if isinstance(provider, CameraIceHazardProvider):
        provider = provider.base
    return provider if isinstance(provider, HazardProvider) else None


def attach(app: Any, **options: Any) -> LiveHub:
    """Build the hub from ``app.state`` and hook it into the scheduler and matcher.

    Called once in the lifespan, after the router, hazard provider and scheduler
    exist. Works with any of them missing: without a scheduler there is no
    ``source_status``, and without a network ``subscribe`` answers with an error.
    """
    state = app.state
    hub = LiveHub(
        router=getattr(state, "router", None),
        provider=getattr(state, "hazard_provider", None),
        clock=getattr(state, "clock", None) or utcnow,
        **options,
    )
    scheduler = getattr(state, "scheduler", None)
    if scheduler is not None:
        scheduler.add_listener(hub.on_source_run)
    matcher = matcher_of(hub.provider)
    if matcher is not None:
        matcher.on_rebuild(hub.on_rebuild)
    return hub


@router.websocket("/ws")
async def live(websocket: WebSocket) -> None:
    """Live updates: ``source_status``, ``obstruction_changed`` and ``route_changed``."""
    hub: LiveHub | None = getattr(websocket.app.state, "live", None)
    await websocket.accept()
    if hub is None:
        await websocket.close(code=1013, reason="live updates are not available")
        return
    await hub.serve(websocket)


__all__ = [
    "ErrorEvent",
    "HeartbeatEvent",
    "HelloEvent",
    "LiveHub",
    "ObstructionBrief",
    "ObstructionChangedEvent",
    "RouteChangedEvent",
    "RouteKey",
    "RouteSnapshot",
    "RouteSpec",
    "RouteSummary",
    "SourceStatusEvent",
    "SubscribedEvent",
    "attach",
    "describe_change",
    "matcher_of",
    "route_changed",
    "router",
]
