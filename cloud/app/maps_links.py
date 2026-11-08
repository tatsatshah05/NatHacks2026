"""Hand-off links to Google Maps and Apple Maps (OP-209).

Why
    OpenPath plans the accessible route; Google Maps and Apple Maps give the
    turn-by-turn experience people already know. The link must carry our detour,
    otherwise the other app simply draws its own shortest route straight through
    the barrier we avoided.

How the detour is carried
    Google Maps accepts up to three ``waypoints`` on a directions link. We place
    them where our route leaves the plain shortest route: for each stretch of
    the route that is not on the plain route, the point halfway along that
    stretch becomes a waypoint, longest stretches first, kept in travel order.
    Apple Maps links do not reliably accept waypoints, so the Apple link is
    start-to-end only and the UI says so (``APPLE_MAPS_NOTE``).

Formats (AGENTS.md, Contracts)
    Google: https://www.google.com/maps/dir/?api=1&origin=LAT,LNG&destination=LAT,LNG
            &travelmode=walking&waypoints=LAT,LNG|LAT,LNG
    Apple:  https://maps.apple.com/?saddr=LAT,LNG&daddr=LAT,LNG&dirflg=w
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

from .network import WalkNetwork

MAX_WAYPOINTS = 3
APPLE_MAPS_NOTE = (
    "Apple Maps may not keep this detour; check the route it shows before you set off."
)

Point = tuple[float, float]  # (lat, lng)


def _fmt(point: Point) -> str:
    lat, lng = point
    return f"{lat:.6f},{lng:.6f}"


def google_maps_url(
    origin: Point, destination: Point, waypoints: Sequence[Point] = ()
) -> str:
    """A Google Maps walking-directions link with up to three waypoints."""
    params = {
        "api": "1",
        "origin": _fmt(origin),
        "destination": _fmt(destination),
        "travelmode": "walking",
    }
    chosen = list(waypoints)[:MAX_WAYPOINTS]
    if chosen:
        params["waypoints"] = "|".join(_fmt(p) for p in chosen)
    # urlencode turns "," into %2C and "|" into %7C; Google accepts both forms.
    return "https://www.google.com/maps/dir/?" + urlencode(params)


def apple_maps_url(origin: Point, destination: Point) -> str:
    """An Apple Maps walking-directions link (start and end only)."""
    return "https://maps.apple.com/?" + urlencode(
        {"saddr": _fmt(origin), "daddr": _fmt(destination), "dirflg": "w"}
    )


def detour_waypoints(
    network: WalkNetwork,
    route_nodes: Sequence[int],
    plain_nodes: Sequence[int] | None,
    max_waypoints: int = MAX_WAYPOINTS,
) -> list[Point]:
    """Points that pin the route's detours: the middle of each stretch off the plain route.

    ``route_nodes`` and ``plain_nodes`` are node indices. When the route is the plain
    route (or there is no plain route to compare with), there is nothing to pin and
    the list is empty. Stretches are ranked by length and the longest ``max_waypoints``
    are kept, then returned in travel order.
    """
    if not plain_nodes or len(route_nodes) < 3:
        return []
    on_plain = set(plain_nodes)
    # Find maximal runs of consecutive route nodes that are not on the plain route.
    stretches: list[tuple[int, int]] = []  # (first index, last index) into route_nodes
    start: int | None = None
    for i, node in enumerate(route_nodes):
        if node not in on_plain:
            if start is None:
                start = i
        elif start is not None:
            stretches.append((start, i - 1))
            start = None
    if start is not None:
        stretches.append((start, len(route_nodes) - 1))
    if not stretches:
        return []

    def length(stretch: tuple[int, int]) -> float:
        a, b = stretch
        # Edge lengths are not needed for a ranking; node count along the stretch is a fair proxy
        # and keeps this independent of edge lookups.
        return float(b - a + 1)

    longest = sorted(stretches, key=length, reverse=True)[:max_waypoints]
    longest.sort()  # travel order
    points: list[Point] = []
    for a, b in longest:
        mid = route_nodes[(a + b) // 2]
        points.append((float(network.node_lat[mid]), float(network.node_lng[mid])))
    return points
