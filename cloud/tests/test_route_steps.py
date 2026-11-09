"""Route steps in plain words: names, turns, start and finish, times, barrier notes.

Every network here is a few hand-placed nodes near Churchill Square, so each
expected sentence can be checked by hand. ``_steps`` is routing's step writer; the
walk legs of walk and LRT trips use the same function (test_lrt covers them).
"""

from __future__ import annotations

import pytest

from cloud.app.network import RawEdge, RawNode, WalkNetwork, export_network
from cloud.app.profiles import DEFAULT_PROFILES_PATH, load_routing_config
from cloud.app.routing import EdgeHazard, Router, _steps
from cloud.app.schemas import Confidence, Effect

LAT0, LNG0 = 53.5440, -113.4900
DLAT, DLNG = 0.0009, 0.0015  # about 100 m north and about 99 m east

STANDARD_SPEED = 1.3  # m/s, config/profiles.json


def at(x: float, y: float) -> tuple[float, float]:
    """(lat, lng) of a point x units east and y units north of the origin."""
    return LAT0 + y * DLAT, LNG0 + x * DLNG


def network(
    places: dict[int, tuple[float, float]], edges: list[RawEdge]
) -> WalkNetwork:
    nodes = [RawNode(osmid, lat, lng) for osmid, (lat, lng) in places.items()]
    return WalkNetwork.from_data(export_network(nodes, edges, source="test"))


def texts(steps) -> list[str]:
    return [s.text for s in steps]


@pytest.fixture(scope="module")
def config():
    return load_routing_config(DEFAULT_PROFILES_PATH)


# ------------------------------------------------------------------ plain names


def test_unnamed_ways_get_plain_names_and_named_ways_keep_theirs() -> None:
    """A chain east: path, sidewalk, laneway, street, then a named street."""
    places = {i: at(i, 0) for i in range(6)}
    edges = [
        RawEdge(0, 1, 100.0, highway="footway", osmid=1),
        RawEdge(1, 2, 100.0, highway="footway", footway="sidewalk", osmid=2),
        RawEdge(2, 3, 100.0, highway="service", osmid=3),
        RawEdge(3, 4, 100.0, highway="residential", osmid=4),
        RawEdge(4, 5, 100.0, highway="residential", name="104 Street NW", osmid=5),
    ]
    net = network(places, edges)
    steps = _steps(net, range(6), range(5), speed_m_s=STANDARD_SPEED)
    assert texts(steps) == [
        "Start on the path heading east for about a minute (100 m).",
        "Continue onto the sidewalk for about a minute (100 m).",
        "Continue onto the laneway for about a minute (100 m).",
        "Continue onto the street for about a minute (100 m).",
        (
            "Continue onto 104 Street NW for about a minute (100 m). "
            "Your destination is ahead."
        ),
    ]
    for raw in ("footway", "service", "residential"):
        assert not any(raw in t for t in texts(steps))


@pytest.mark.parametrize(
    ("highway", "footway", "expected"),
    [
        ("path", None, "the path"),
        ("pedestrian", None, "the pedestrian street"),
        ("living_street", None, "the street"),
        ("primary", None, "the street"),
        ("trunk_link", None, "the street"),
        ("corridor", None, "the indoor corridor"),
        ("cycleway", None, "the shared path"),
        ("track", None, "the trail"),
        ("bus_stop", None, "the path"),  # anything else
        ("footway", "crossing", "the crossing"),
        ("steps", None, "the stairs"),
        ("elevator", None, "the elevator"),
    ],
)
def test_every_highway_type_has_plain_words(
    highway: str, footway: str | None, expected: str
) -> None:
    net = network(
        {0: at(0, 0), 1: at(1, 0)},
        # 20 m: short enough for a crossing to count as one (_CROSSING_MAX_M).
        [RawEdge(0, 1, 20.0, highway=highway, footway=footway, osmid=1)],
    )
    (step,) = _steps(net, [0, 1], [0], speed_m_s=STANDARD_SPEED)
    assert step.text.startswith(f"Start on {expected} heading east")
    assert highway not in step.text.replace(expected, "")


# ----------------------------------------------------------------------- turns


def star() -> WalkNetwork:
    """West approach A -> C along First Avenue, then one way out of C in each direction.

              N (1, 1)      NE (2, 1)
    A (0, 0) --- C (1, 0) --- E (2, 0)
       B (0, -0.2)  S (1, -1)
    """
    places = {
        1: at(0, 0),  # A
        2: at(1, 0),  # C
        3: at(1, 1),  # N
        4: at(1, -1),  # S
        5: at(2, 0),  # E
        6: at(2, 1),  # NE
        7: at(0, -0.2),  # B, back west and a little south
    }
    edges = [
        RawEdge(1, 2, 100.0, highway="residential", name="First Avenue", osmid=1),
        RawEdge(2, 3, 100.0, highway="residential", name="North Street", osmid=2),
        RawEdge(2, 4, 100.0, highway="residential", name="South Street", osmid=3),
        RawEdge(2, 5, 100.0, highway="residential", name="Second Avenue", osmid=4),
        RawEdge(2, 6, 140.0, highway="residential", name="Diagonal Road", osmid=5),
        RawEdge(2, 7, 100.0, highway="residential", name="Back Lane", osmid=6),
    ]
    return network(places, edges)


@pytest.mark.parametrize(
    ("edge", "node", "expected"),
    [
        (1, 2, "Turn left onto North Street"),  # east then north: 90 degrees left
        (2, 3, "Turn right onto South Street"),  # 90 degrees right
        (3, 4, "Continue onto Second Avenue"),  # straight on
        (4, 5, "Bear left onto Diagonal Road"),  # about 45 degrees left
        (5, 6, "Turn sharply right onto Back Lane"),  # nearly back the way we came
    ],
)
def test_turn_words_follow_the_bearing_change(
    edge: int, node: int, expected: str
) -> None:
    steps = _steps(star(), [0, 1, node], [0, edge], speed_m_s=STANDARD_SPEED)
    assert steps[0].text.startswith("Start on First Avenue heading east")
    assert steps[1].text.startswith(expected + " for about")


def test_a_turn_is_judged_from_the_last_and_first_segments() -> None:
    """A bend inside the previous step counts: a curved road that ends heading north
    then a way east is a right turn, even though the road began heading east."""
    places = {1: at(0, 0), 2: at(1, 1), 3: at(2, 1)}
    curve = [
        (LNG0, LAT0),
        (LNG0 + DLNG, LAT0),  # east first
        (LNG0 + DLNG, LAT0 + DLAT),  # then north
    ]
    edges = [
        RawEdge(
            1, 2, 200.0, highway="residential", name="Bend", osmid=1, geometry=curve
        ),
        RawEdge(2, 3, 100.0, highway="residential", name="Top Avenue", osmid=2),
    ]
    steps = _steps(network(places, edges), [0, 1, 2], [0, 1], speed_m_s=1.3)
    assert steps[0].text.startswith("Start on Bend heading east")
    assert steps[1].text.startswith("Turn right onto Top Avenue")


# ------------------------------------------------------------- start and finish


def test_start_heading_and_destination_side(config) -> None:
    """Jasper Avenue runs east; where the destination sits decides the last words."""
    places = {1: at(0, 0), 2: at(1, 0), 3: at(2, 0)}
    edges = [
        RawEdge(1, 2, 100.0, highway="footway", name="Jasper Avenue NW", osmid=1),
        RawEdge(2, 3, 100.0, highway="footway", name="Jasper Avenue NW", osmid=2),
    ]
    router = Router(network(places, edges), config)
    end_lat, end_lng = at(2, 0)
    north = 20 / 111_000  # about 20 m

    def last_words(
        to_lat: float, to_lng: float, frm: tuple[float, float] | None = None
    ) -> str:
        from_lat, from_lng = frm if frm is not None else at(0, 0)
        route = router.route(from_lat, from_lng, to_lat, to_lng, "standard").routes[0]
        return route.steps[-1].text.rsplit(". ", 1)[-1]

    heading_east = router.route(*at(0, 0), end_lat, end_lng, "standard").routes[0]
    assert heading_east.steps[0].text.startswith(
        "Start on Jasper Avenue NW heading east"
    )
    assert last_words(end_lat, end_lng) == "Your destination is ahead."
    assert last_words(end_lat + north, end_lng) == "Your destination is on your left."
    assert last_words(end_lat - north, end_lng) == "Your destination is on your right."
    # Heading west the sides swap, and the first words say so.
    start_lat, start_lng = at(0, 0)
    west = router.route(end_lat, end_lng, start_lat, start_lng, "standard").routes[0]
    assert west.steps[0].text.startswith("Start on Jasper Avenue NW heading west")
    assert (
        last_words(start_lat + north, start_lng, frm=(end_lat, end_lng))
        == "Your destination is on your right."
    )


def test_eight_compass_points() -> None:
    cases = {
        (0, 1): "north",
        (1, 1): "north-east",
        (1, 0): "east",
        (1, -1): "south-east",
        (0, -1): "south",
        (-1, -1): "south-west",
        (-1, 0): "west",
        (-1, 1): "north-west",
    }
    for (x, y), word in cases.items():
        # DLNG and DLAT are both about 100 m, so a diagonal is about 45 degrees.
        net = network(
            {1: at(0, 0), 2: at(x, y)},
            [RawEdge(1, 2, 100.0, highway="footway", name="Way", osmid=1)],
        )
        (step,) = _steps(net, [0, 1], [0], speed_m_s=STANDARD_SPEED)
        assert step.text.startswith(f"Start on Way heading {word} "), (x, y, step)


def test_a_station_can_be_the_destination_of_a_walk_leg() -> None:
    net = network(
        {1: at(0, 0), 2: at(1, 0)},
        [RawEdge(1, 2, 100.0, highway="footway", name="Way", osmid=1)],
    )
    (step,) = _steps(
        net,
        [0, 1],
        [0],
        speed_m_s=1.0,
        destination=at(1, 0.2),
        destination_name="Churchill Station",
    )
    assert step.text.endswith("Churchill Station is on your left.")


# ------------------------------------------------------------------------ time


def test_time_phrasing_for_two_profiles(config) -> None:
    standard = config.profile("standard").speed_m_s  # 1.3 m/s
    walker = config.profile("walker_cane").speed_m_s  # 0.9 m/s
    assert (standard, walker) == (1.3, 0.9)

    def one(length_m: float, speed: float) -> str:
        net = network(
            {1: at(0, 0), 2: at(1, 0)},
            [RawEdge(1, 2, length_m, highway="footway", name="Way", osmid=1)],
        )
        (step,) = _steps(net, [0, 1], [0], speed_m_s=speed)
        return step.text

    # 150 m: 115 s for a standard walker (about 2 minutes), 167 s with a walker or
    # cane (about 3 minutes). The distance is the same for both.
    assert "for about 2 minutes (150 m)" in one(150.0, standard)
    assert "for about 3 minutes (150 m)" in one(150.0, walker)
    # Under 30 s only the distance is said; 0.5 to 1.5 minutes is "about a minute".
    assert "heading east for about 30 m." in one(30.0, standard)
    assert "for about a minute (50 m)" in one(50.0, standard)
    assert "for about a minute (110 m)" in one(110.0, standard)  # 85 s
    assert "for about 2 minutes (120 m)" in one(120.0, standard)  # 92 s, 1.54 min
    # Distances round to 10 m from 10 m up, to the metre below that.
    assert "for about 6 m." in one(6.0, standard)
    assert "(1240 m)" in one(1236.0, standard)


# --------------------------------------------------------------- barrier notes


def avenue_chain() -> WalkNetwork:
    """Avenue A, Street 1, Avenue B: three steps, edges 0, 1 and 2."""
    places = {1: at(0, 0), 2: at(1, 0), 3: at(1, 1), 4: at(2, 1)}
    return network(
        places,
        [
            RawEdge(1, 2, 100.0, highway="footway", name="Avenue A", osmid=1),
            RawEdge(2, 3, 100.0, highway="footway", name="Street 1", osmid=2),
            RawEdge(3, 4, 100.0, highway="footway", name="Avenue B", osmid=3),
        ],
    )


def test_snow_note_appears_on_the_right_step_only() -> None:
    snow = EdgeHazard("community_report:ab12", Effect.SNOW, Confidence.MEDIUM, "Snow")
    steps = _steps(
        avenue_chain(), [0, 1, 2, 3], [0, 1, 2], speed_m_s=1.0, hazards={1: [snow]}
    )
    assert steps[1].text == (
        "Turn left onto Street 1 for about 2 minutes (100 m). "
        "Snow reported here by the community."
    )
    assert "Snow" not in steps[0].text and "Snow" not in steps[2].text
    assert steps[2].text.endswith("Your destination is ahead.")


def test_notes_by_effect_and_source() -> None:
    hazards = {
        0: [EdgeHazard("city_311_snow:7", Effect.SNOW, Confidence.LOW, "Snow")],
        1: [
            EdgeHazard("camera_model:511:1:2", Effect.ICE_RISK, Confidence.MEDIUM, ""),
            EdgeHazard(
                "city_traffic_disruptions:1304",
                Effect.CAUTION,
                Confidence.HIGH,
                "Road partly closed",
            ),
        ],
        2: [
            EdgeHazard(
                "city_trail_closures:461",
                Effect.CAUTION,
                Confidence.HIGH,
                "Trail caution: Gold Bar Park",
            ),
            EdgeHazard("x:untitled", Effect.CAUTION, Confidence.LOW, ""),
        ],
    }
    steps = _steps(
        avenue_chain(), [0, 1, 2, 3], [0, 1, 2], speed_m_s=1.0, hazards=hazards
    )
    assert steps[0].text.endswith("Snow reported here by the City.")
    assert steps[1].text.endswith(
        "Ice risk reported here. Caution: Road partly closed."
    )
    assert "Trail caution: Gold Bar Park. Caution reported here." in steps[2].text
    assert not any("black ice" in t.lower() for t in texts(steps))


def test_blocked_barriers_never_appear_and_one_item_gives_one_note() -> None:
    """A route never uses a blocked edge; if a caller still passes one, nothing is
    said about it. One item spanning two edges of a step is noted once."""
    places = {1: at(0, 0), 2: at(1, 0), 3: at(2, 0)}
    net = network(
        places,
        [
            RawEdge(1, 2, 100.0, highway="footway", name="Avenue A", osmid=1),
            RawEdge(2, 3, 100.0, highway="footway", name="Avenue A", osmid=2),
        ],
    )
    snow = EdgeHazard("community_report:1", Effect.SNOW, Confidence.MEDIUM, "Snow")
    closed = EdgeHazard(
        "city_construction:9", Effect.BLOCKED, Confidence.HIGH, "Closed"
    )
    (step,) = _steps(
        net, [0, 1, 2], [0, 1], speed_m_s=1.0, hazards={0: [snow, closed], 1: [snow]}
    )
    assert step.text.count("Snow reported here") == 1
    assert "Closed" not in step.text


# --------------------------------------------------------------------- merging


def test_merging_keeps_distance_and_edges_exact() -> None:
    places = {1: at(0, 0), 2: at(0.4, 0), 3: at(0.7, 0), 4: at(1, 0), 5: at(2, 0)}
    net = network(
        places,
        [
            RawEdge(1, 2, 33.3, highway="footway", name="Avenue A", osmid=1),
            RawEdge(2, 3, 33.3, highway="footway", name="Avenue A", osmid=2),
            RawEdge(3, 4, 33.4, highway="footway", name="Avenue A", osmid=3),
            RawEdge(4, 5, 100.0, highway="footway", name="Avenue B", osmid=4),
        ],
    )
    steps = _steps(net, range(5), range(4), speed_m_s=1.0)
    assert len(steps) == 2
    assert steps[0].edges == (0, 1, 2) and steps[0].distance_m == pytest.approx(100.0)
    assert steps[0].text.startswith(
        "Start on Avenue A heading east for about 2 minutes (100 m)."
    )
    assert steps[1].edges == (3,) and steps[1].distance_m == 100.0
    # Unnamed ways merge by their plain name too: two sidewalk edges make one step.
    net2 = network(
        {1: at(0, 0), 2: at(1, 0), 3: at(2, 0)},
        [
            RawEdge(1, 2, 100.0, highway="footway", footway="sidewalk", osmid=1),
            RawEdge(2, 3, 100.0, highway="footway", footway="sidewalk", osmid=2),
        ],
    )
    (step,) = _steps(net2, [0, 1, 2], [0, 1], speed_m_s=1.0)
    assert step.edges == (0, 1) and step.text.startswith("Start on the sidewalk")


def test_stairs_elevators_and_crossings_are_announced() -> None:
    places = {
        1: at(0, 0),
        2: at(1, 0),
        3: at(2, 0),
        4: at(3, 0),
        5: at(4, 0),
        6: at(5, 0),
        7: at(6.2, 0),
    }
    net = network(
        places,
        [
            RawEdge(1, 2, 100.0, highway="footway", name="Avenue A", osmid=1),
            RawEdge(2, 3, 20.0, highway="steps", name="Avenue A", osmid=2),  # same name
            RawEdge(3, 4, 10.0, highway="footway", footway="crossing", osmid=3),
            RawEdge(4, 5, 5.0, highway="elevator", osmid=4),
            RawEdge(
                5,
                6,
                10.0,
                highway="footway",
                footway="crossing",
                name="184 Street NW",
                osmid=5,
            ),
            # Unnamed, flagged as a crossing, but 120 m long: a sidewalk merged
            # with its crossings when the network was built, not a crossing.
            RawEdge(
                6,
                7,
                120.0,
                highway="footway",
                footway=["sidewalk", "crossing"],
                osmid=6,
            ),
        ],
    )
    steps = _steps(net, range(7), range(6), speed_m_s=1.0)
    assert [t.split(" for about")[0] for t in texts(steps)] == [
        "Start on Avenue A heading east",
        "Take the stairs on Avenue A",  # stairs never merge into the street's step
        "Cross the street",
        "Take the elevator",
        "Continue onto 184 Street NW",  # a named crossing edge is a merged way
        "Continue onto the sidewalk",  # and so is a long unnamed one
    ]
    assert steps[1].edges == (1,) and steps[1].distance_m == 20.0
    assert "Cross" not in steps[5].text
