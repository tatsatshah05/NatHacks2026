"""Address and place search (OP-302): ``GET /api/search?q=``.

Why this exists
---------------
People type "10220 104 ave", "churchill", "central station" or "chipotle" instead
of finding a point on the map. AGENTS.md forbids calling an external geocoder at
request time, so the search runs on a prebuilt index of four sources:

- City of Edmonton Parcel Addresses (Socrata ``ut27-nrpn``): every parcel address
  and every suite address that has no parcel of its own.
- LRT stations and stops (Socrata ``fhxi-cnhe``): one entry per station, at the
  mean of its platform points.
- Named places from ``config/places.json`` (arenas, hospitals, malls, campuses),
  each with a recorded coordinate source.
- Points of interest from OpenStreetMap (OP-416): named shops, restaurants,
  clinics, libraries, parks and the like inside the city boundary, read from the
  Overpass API at build time. Each is a ``place`` whose detail says what it is and
  where ("Restaurant, 10220 103 Street NW"), so a chain's locations can be told
  apart. ``parse_poi_elements()`` turns the Overpass elements into rows.

Two halves
----------
1. Build time (``scripts/build_search_index.py``, laptop only): downloads the
   rows (or reads the recorded fixtures), ``build_index()`` normalises and
   deduplicates them, and ``SearchIndex.save()`` writes ``cloud/data/search.npz``,
   which is committed.
2. Run time: ``load_index()`` reads the arrays in well under a second onto
   ``app.state.search``; ``SearchIndex.search()`` answers a query in a few
   milliseconds; the router serves it.

Normalisation
-------------
``words()`` lower-cases the text, removes accents and punctuation, strips ordinals
("104th" to "104") and joins "N.W." into "nw". ``normalise()`` then puts each word
in the dataset's spelling through ``WORD_ALIASES`` ("ave" to "avenue", "northwest"
to "nw"), so "10220-104 Ave N.W." and "10220 104 Avenue Northwest" give the same
tokens. The dataset and the places are indexed in that canonical form. A query
word matches a token when the word, as typed or in its canonical spelling, is a
prefix of the token, so half-typed words work ("10220 104 aven").

Ranking
-------
Tiers, best first. Inside a tier, LRT stations and the config places (group 0)
come before OpenStreetMap points of interest (group 1), then shorter labels first.

0. The whole query starts a name: the label of a station, place or point of
   interest, or a curated alias from ``config/places.json`` ("Churchill Square",
   "U of A"), which is a name people use for the place.
1. The whole query starts an OpenStreetMap alias (``brand``, ``short_name`` or
   ``alt_name``), which is weaker evidence than the name itself.
2. Addresses: a leading house number, then every remaining word a prefix of a
   word of the street name ("10220 104" finds 104 Avenue NW and 104 Street NW).
3. Stations and places where every query word is a prefix of some word of the
   name, an alias or the detail ("churchill" finds Edmonton City Hall through its
   address; "pharmacy" finds every pharmacy through its category).
4. Fuzzy: a misspelt word is replaced by the closest indexed word ("jaspr" to
   "jasper"), first among street, station and config place words, then among the
   words only points of interest use, and tiers 2 and 3 run again.

At most ``MAX_RESULTS`` results come back; a query under ``MIN_QUERY_CHARS``
characters gives an empty list.

The arrays (``search.npz``)
---------------------------
Addresses, sorted by house number then street id:
    ``addr_number`` int32, ``addr_street`` int32 (index into ``streets``),
    ``addr_lat`` / ``addr_lng`` float32 (about 0.4 m precision, enough to start a route)
Tables
    ``streets`` (display labels, "104 Avenue NW"), ``name_label``, ``name_detail``,
    ``name_kind``, ``name_terms`` (label and aliases joined by "|"),
    ``name_group`` int8 (0: station or config place, 1: OpenStreetMap point of
    interest), ``name_lat`` / ``name_lng`` float64,
    ``manifest`` (JSON: counts, sources, build time)
Prebuilt name lookups (``LOOKUP_KEYS``), each a sorted string array and the int32
entry index that owns each string, so loading needs no text normalisation:
    ``name_tok`` / ``name_owner`` (tier 0: canonical labels and curated aliases),
    ``alias_tok`` / ``alias_owner`` (tier 1: canonical OpenStreetMap aliases),
    ``word_tok`` / ``word_owner`` (tiers 3 and 4: every word of label, aliases, detail)
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import re
import time
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

# Bump when the array layout changes, so an old file is refused instead of misread.
# 2 (OP-416): ``name_group`` added for the OpenStreetMap points of interest.
FORMAT_VERSION = 2

DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "search.npz"
DEFAULT_PLACES_PATH = Path(__file__).resolve().parents[2] / "config" / "places.json"

PARCEL_DATASET = "ut27-nrpn"
STATIONS_DATASET = "fhxi-cnhe"
ATTRIBUTION = "City of Edmonton Open Data"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_LICENCE_URL = "https://www.openstreetmap.org/copyright"

MAX_RESULTS = 8
MIN_QUERY_CHARS = 2
MAX_QUERY_CHARS = 200
# Fuzzy matching only for words at least this long, so "st" is never "corrected".
FUZZY_MIN_CHARS = 4
FUZZY_CUTOFF = 0.8

Kind = Literal["address", "station", "place"]

# Ranking groups: on an equal match, a station or a config place (curated, with a
# checked coordinate) comes before an OpenStreetMap point of interest.
GROUP_CURATED = 0
GROUP_POI = 1

# The tiers of ``SearchIndex.search()``; see the module docstring.
TIER_NAME, TIER_ALIAS, TIER_ADDRESS, TIER_WORDS, TIER_FUZZY = range(5)

# Abbreviations -> the word the dataset uses ("AVENUE", "WEST", "NW"). The dataset and
# the places are indexed in this canonical form. A query word matches either as typed
# or through this table, so "ave", "aven" and "avenue" all find AVENUE, while "st"
# still finds "Stony Plain Road" as well as STREET.
WORD_ALIASES: dict[str, str] = {
    "ave": "avenue",
    "av": "avenue",
    "st": "street",
    "str": "street",
    "rd": "road",
    "dr": "drive",
    "blvd": "boulevard",
    "boul": "boulevard",
    "cres": "crescent",
    "cresc": "crescent",
    "cr": "crescent",
    "crt": "court",
    "ct": "court",
    "pl": "place",
    "ln": "lane",
    "pt": "point",
    "terr": "terrace",
    "tce": "terrace",
    "trl": "trail",
    "tr": "trail",
    "sq": "square",
    "cir": "circle",
    "circ": "circle",
    "ctr": "centre",
    "center": "centre",
    "gdns": "gardens",
    "xing": "crossing",
    "prom": "promenade",
    "pkwy": "parkway",
    "hwy": "highway",
    "plz": "plaza",
    "vlg": "village",
    "ldg": "landing",
    "grn": "green",
    "grv": "grove",
    "hts": "heights",
    "mt": "mount",
    "ft": "fort",
    "northwest": "nw",
    "northeast": "ne",
    "southwest": "sw",
    "southeast": "se",
}
QUADRANTS = frozenset({"NW", "NE", "SW", "SE"})

_ORDINAL = re.compile(r"^(\d+)(st|nd|rd|th)$")
_NOT_WORD = re.compile(r"[^a-z0-9 ]+")


# ------------------------------------------------------------------ normalising


def words(text: str) -> list[str]:
    """Words as typed: lower case, ASCII, no punctuation, no ordinal suffixes.

    >>> words("10220-104th Ave. N.W.")
    ['10220', '104', 'ave', 'nw']
    """
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    )
    ascii_text = ascii_text.replace("'", "")  # king's -> kings, not king s
    out: list[str] = []
    for raw in _NOT_WORD.sub(" ", ascii_text).split():
        match = _ORDINAL.match(raw)
        word = match.group(1) if match else raw
        # "N.W." arrives as "n" then "w": join it back into one quadrant.
        if word in {"e", "w"} and out and out[-1] in {"n", "s"}:
            out[-1] += word
            continue
        out.append(word)
    return out


def canonical(word: str) -> str:
    """One word in the dataset's spelling: "ave" -> "avenue", "northwest" -> "nw"."""
    return WORD_ALIASES.get(word, word)


def normalise(text: str) -> list[str]:
    """Canonical tokens, for indexing and for comparing whole addresses.

    >>> normalise("10220-104 Ave Northwest")
    ['10220', '104', 'avenue', 'nw']
    >>> normalise("Sir Winston Churchill Sq.")
    ['sir', 'winston', 'churchill', 'square']
    """
    return [canonical(word) for word in words(text)]


def street_label(raw: str) -> str:
    """Display form of a dataset street name: "104 AVENUE NW" -> "104 Avenue NW"."""
    words = []
    for word in raw.split():
        if word in QUADRANTS or word[:1].isdigit():
            words.append(word)  # keep "NW" and "103A"
        else:
            words.append(word.capitalize())
    return " ".join(words)


def split_house_number(tokens: Sequence[str]) -> tuple[int | None, list[str]]:
    """A leading integer is the house number; the rest describes the street."""
    if tokens and tokens[0].isdigit() and len(tokens[0]) <= 7:
        return int(tokens[0]), list(tokens[1:])
    return None, list(tokens)


# ------------------------------------------------------------------ raw records


@dataclass(frozen=True)
class AddressRow:
    """One parsed Parcel Addresses row (a parcel or a suite)."""

    number: int
    street: str  # as the dataset spells it, upper case: "104 AVENUE NW"
    lat: float
    lng: float
    parcel: bool  # object_type PARCEL; suites only count where no parcel exists


@dataclass(frozen=True)
class NamedPlace:
    """An LRT station, a named destination or an OpenStreetMap point of interest."""

    label: str
    kind: Kind
    lat: float
    lng: float
    detail: str | None = None  # shown under the label; also searchable
    aliases: tuple[str, ...] = ()  # alternative names, prefix-searchable
    group: int = GROUP_CURATED  # GROUP_CURATED or GROUP_POI; breaks ranking ties


def parse_parcel_rows(rows: Iterable[Mapping[str, Any]]) -> list[AddressRow]:
    """Parcel Addresses rows -> ``AddressRow``; a malformed row is skipped and counted."""
    parsed: list[AddressRow] = []
    skipped = 0
    for row in rows:
        try:
            number = int(str(row["house_number"]).strip())
            street = " ".join(str(row["street_name"]).split()).upper()
            lat = float(row["latitude"])
            lng = float(row["longitude"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if not street or not (-90 <= lat <= 90 and -180 <= lng <= 180):
            skipped += 1
            continue
        parcel = str(row.get("object_type", "PARCEL")).upper() != "SUITE"
        parsed.append(AddressRow(number, street, lat, lng, parcel))
    if skipped:
        log.warning("SEARCH %d parcel address rows skipped (missing fields)", skipped)
    return parsed


def parse_station_rows(
    rows: Iterable[Mapping[str, Any]],
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> list[NamedPlace]:
    """LRT stations and stops (``fhxi-cnhe``): one entry per name, at the mean point.

    A station has one row per platform, so its entry sits between them. ``aliases``
    (from ``config/places.json``) adds names people use, such as "Government Centre".
    """
    points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        try:
            name = " ".join(str(row["lrt_stop_description"]).split())
            point = (float(row["latitude"]), float(row["longitude"]))
        except (KeyError, TypeError, ValueError):
            log.warning("SEARCH station row skipped: %r", row)
            continue
        points[name].append(point)  # only after the row parsed, never an empty entry
    stations = []
    for name in sorted(points):
        pts = points[name]
        detail = "LRT stop" if name.lower().endswith("stop") else "LRT station"
        stations.append(
            NamedPlace(
                label=name,
                kind="station",
                lat=sum(p[0] for p in pts) / len(pts),
                lng=sum(p[1] for p in pts) / len(pts),
                detail=detail,
                aliases=tuple((aliases or {}).get(name, ())),
            )
        )
    return stations


def load_station_aliases(path: Path = DEFAULT_PLACES_PATH) -> dict[str, list[str]]:
    """``station_aliases`` from ``config/places.json``: station name -> other names."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: list(names) for name, names in raw.get("station_aliases", {}).items()}


def load_places(path: Path = DEFAULT_PLACES_PATH) -> list[NamedPlace]:
    """``config/places.json``: named destinations with recorded coordinate sources."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    places = []
    for entry in raw["places"]:
        places.append(
            NamedPlace(
                label=entry["name"],
                kind="place",
                lat=float(entry["lat"]),
                lng=float(entry["lng"]),
                detail=entry.get("detail") or entry.get("address"),
                aliases=tuple(entry.get("aliases", ())),
            )
        )
    return places


# ------------------------------------------------------- points of interest (OSM)

# The OpenStreetMap keys that make a feature a point of interest, in the order the
# category is read from them. ``public_transport=station`` is never one: the City's
# LRT list covers stations.
POI_TAGS = ("amenity", "shop", "healthcare", "tourism", "leisure", "office")

# Tag values nobody travels to: parking, street furniture, machines. A feature is
# dropped when none of its POI tags is a destination.
NOT_DESTINATIONS: dict[str, frozenset[str]] = {
    "amenity": frozenset(
        {
            "parking",
            "parking_space",
            "parking_entrance",
            "bicycle_parking",
            "motorcycle_parking",
            "bench",
            "waste_basket",
            "waste_disposal",
            "recycling",
            "vending_machine",
            "atm",
            "drinking_water",
            "toilets",
            "shelter",
            "telephone",
            "post_box",
            "letter_box",
            "charging_station",
            "clock",
            "grit_bin",
            "hunting_stand",
            "loading_dock",
            "bicycle_repair_station",
            "compressed_air",
            "vacuum_cleaner",
            "sanitary_dump_station",
            "waste_transfer_station",
            "lounger",
            "watering_place",
            "give_box",
            "public_bookcase",
            "library_dropoff",
            "trolley_bay",
            "weighbridge",
            "fixme",
            "disused",
            "yes",
            "no",
        }
    ),
    "leisure": frozenset(
        {
            "picnic_table",
            "slipway",
            "firepit",
            "bleachers",
            "outdoor_seating",
            "disused",
            "yes",
            "no",
        }
    ),
    # ``tourism=information`` is a destination only as an office (see is_destination).
    "tourism": frozenset({"camp_pitch", "disused", "yes", "no"}),
    "shop": frozenset({"vacant", "disused", "yes", "no"}),
    "healthcare": frozenset({"disused", "no"}),
    "office": frozenset({"disused", "vacant", "no"}),
}
INFORMATION_OFFICES = frozenset({"office", "visitor_centre"})

# Plain English for a tag, keyed "key=value". Anything else falls back to the value
# with underscores replaced ("ice_cream" -> "Ice cream").
CATEGORY_LABELS: dict[str, str] = {
    "amenity=restaurant": "Restaurant",
    "amenity=cafe": "Cafe",
    "amenity=fast_food": "Fast food",
    "amenity=bar": "Bar",
    "amenity=pub": "Pub",
    "amenity=nightclub": "Nightclub",
    "amenity=food_court": "Food court",
    "amenity=ice_cream": "Ice cream",
    "amenity=pharmacy": "Pharmacy",
    "amenity=clinic": "Clinic",
    "amenity=doctors": "Doctor's office",
    "amenity=dentist": "Dentist",
    "amenity=hospital": "Hospital",
    "amenity=veterinary": "Veterinary clinic",
    "amenity=library": "Library",
    "amenity=school": "School",
    "amenity=kindergarten": "Kindergarten",
    "amenity=childcare": "Child care",
    "amenity=college": "College",
    "amenity=university": "University",
    "amenity=place_of_worship": "Place of worship",
    "amenity=community_centre": "Community centre",
    "amenity=social_facility": "Social facility",
    "amenity=social_centre": "Social centre",
    "amenity=bank": "Bank",
    "amenity=post_office": "Post office",
    "amenity=police": "Police station",
    "amenity=fire_station": "Fire station",
    "amenity=townhall": "City hall",
    "amenity=courthouse": "Courthouse",
    "amenity=fuel": "Gas station",
    "amenity=car_wash": "Car wash",
    "amenity=car_rental": "Car rental",
    "amenity=bus_station": "Bus station",
    "amenity=cinema": "Cinema",
    "amenity=theatre": "Theatre",
    "amenity=arts_centre": "Arts centre",
    "amenity=events_venue": "Events venue",
    "amenity=music_venue": "Music venue",
    "amenity=public_hall": "Public hall",
    "amenity=marketplace": "Market",
    "amenity=studio": "Studio",
    "amenity=grave_yard": "Cemetery",
    "amenity=lounge": "Lounge",
    "amenity=training": "Training centre",
    "shop=supermarket": "Grocery store",
    "shop=convenience": "Convenience store",
    "shop=greengrocer": "Greengrocer",
    "shop=bakery": "Bakery",
    "shop=butcher": "Butcher",
    "shop=deli": "Deli",
    "shop=alcohol": "Liquor store",
    "shop=cannabis": "Cannabis store",
    "shop=clothes": "Clothing store",
    "shop=shoes": "Shoe store",
    "shop=mall": "Shopping mall",
    "shop=department_store": "Department store",
    "shop=variety_store": "Variety store",
    "shop=beauty": "Beauty salon",
    "shop=hairdresser": "Hair salon",
    "shop=mobile_phone": "Mobile phone store",
    "shop=electronics": "Electronics store",
    "shop=computer": "Computer store",
    "shop=books": "Bookstore",
    "shop=sports": "Sports store",
    "shop=furniture": "Furniture store",
    "shop=hardware": "Hardware store",
    "shop=doityourself": "Hardware store",
    "shop=car": "Car dealer",
    "shop=car_repair": "Auto repair",
    "shop=car_parts": "Auto parts store",
    "shop=jewelry": "Jewellery store",
    "shop=optician": "Optician",
    "shop=florist": "Florist",
    "shop=gift": "Gift shop",
    "shop=pet": "Pet store",
    "shop=toys": "Toy store",
    "shop=chemist": "Drugstore",
    "shop=dry_cleaning": "Dry cleaner",
    "shop=laundry": "Laundromat",
    "shop=tailor": "Tailor",
    "shop=storage_rental": "Storage rental",
    "shop=collector": "Collectibles store",
    "shop=repair": "Repair shop",
    "shop=coffee": "Coffee shop",
    "shop=tea": "Tea shop",
    "shop=garden_centre": "Garden centre",
    "shop=stationery": "Stationery store",
    "shop=travel_agency": "Travel agency",
    "shop=bicycle": "Bike shop",
    "shop=music": "Music store",
    "shop=video_games": "Video game store",
    "shop=cosmetics": "Cosmetics store",
    "shop=massage": "Massage",
    "shop=tattoo": "Tattoo studio",
    "shop=second_hand": "Thrift store",
    "shop=charity": "Thrift store",
    "shop=outdoor": "Outdoor store",
    "shop=health_food": "Health food store",
    "shop=nutrition_supplements": "Supplement store",
    "shop=medical_supply": "Medical supply store",
    "shop=general": "General store",
    "shop=houseware": "Houseware store",
    "shop=interior_decoration": "Home decor store",
    "shop=craft": "Craft store",
    "shop=art": "Art store",
    "shop=photo": "Photo store",
    "shop=copyshop": "Copy shop",
    "shop=pawnbroker": "Pawnshop",
    "shop=tobacco": "Tobacco store",
    "shop=e-cigarette": "Vape shop",
    "shop=newsagent": "Newsstand",
    "shop=kiosk": "Kiosk",
    "shop=wine": "Wine store",
    "shop=beverages": "Beverage store",
    "shop=pastry": "Pastry shop",
    "shop=confectionery": "Candy store",
    "shop=chocolate": "Chocolate shop",
    "shop=appliance": "Appliance store",
    "shop=tyres": "Tire shop",
    "shop=motorcycle": "Motorcycle dealer",
    "shop=wholesale": "Wholesale store",
    "shop=frozen_food": "Frozen food store",
    "shop=seafood": "Seafood store",
    "shop=baby_goods": "Baby store",
    "shop=fabric": "Fabric store",
    "shop=funeral_directors": "Funeral home",
    "shop=grocery": "Grocery store",
    "shop=bed": "Bedding store",
    "shop=flooring": "Flooring store",
    "shop=paint": "Paint store",
    "shop=carpet": "Carpet store",
    "shop=kitchen": "Kitchen store",
    "shop=lighting": "Lighting store",
    "shop=games": "Game store",
    "shop=antiques": "Antique store",
    "shop=party": "Party supply store",
    "shop=rental": "Rental shop",
    "shop=electrical": "Electrical supply store",
    "shop=musical_instrument": "Musical instrument store",
    "shop=telecommunication": "Telecom store",
    "shop=hearing_aids": "Hearing aid store",
    "shop=watches": "Watch store",
    "shop=erotic": "Adult store",
    "shop=fishing": "Fishing store",
    "shop=hifi": "Hi-fi store",
    "shop=bag": "Bag store",
    "shop=frame": "Frame shop",
    "shop=leather": "Leather store",
    "shop=boat": "Boat dealer",
    "shop=caravan": "RV dealer",
    "shop=trailer": "Trailer dealer",
    "shop=truck": "Truck dealer",
    "shop=trade": "Trade supplier",
    "shop=money_lender": "Money lender",
    "shop=locksmith": "Locksmith",
    "shop=pet_grooming": "Pet grooming",
    "shop=shoe_repair": "Shoe repair",
    "tourism=hotel": "Hotel",
    "tourism=motel": "Motel",
    "tourism=hostel": "Hostel",
    "tourism=guest_house": "Guest house",
    "tourism=apartment": "Holiday apartment",
    "tourism=museum": "Museum",
    "tourism=gallery": "Gallery",
    "tourism=attraction": "Attraction",
    "tourism=artwork": "Artwork",
    "tourism=viewpoint": "Viewpoint",
    "tourism=zoo": "Zoo",
    "tourism=theme_park": "Theme park",
    "tourism=information": "Visitor centre",
    "tourism=camp_site": "Campground",
    "leisure=park": "Park",
    "leisure=playground": "Playground",
    "leisure=pitch": "Sports field",
    "leisure=sports_centre": "Sports centre",
    "leisure=sports_hall": "Sports hall",
    "leisure=fitness_centre": "Gym",
    "leisure=fitness_station": "Fitness station",
    "leisure=swimming_pool": "Swimming pool",
    "leisure=water_park": "Water park",
    "leisure=stadium": "Stadium",
    "leisure=ice_rink": "Ice rink",
    "leisure=golf_course": "Golf course",
    "leisure=miniature_golf": "Mini golf",
    "leisure=disc_golf_course": "Disc golf course",
    "leisure=dog_park": "Dog park",
    "leisure=garden": "Garden",
    "leisure=nature_reserve": "Nature reserve",
    "leisure=track": "Track",
    "leisure=bowling_alley": "Bowling alley",
    "leisure=dance": "Dance studio",
    "leisure=escape_game": "Escape room",
    "leisure=amusement_arcade": "Arcade",
    "leisure=marina": "Marina",
    "leisure=recreation_ground": "Recreation ground",
    "healthcare=pharmacy": "Pharmacy",
    "healthcare=clinic": "Clinic",
    "healthcare=doctor": "Doctor's office",
    "healthcare=dentist": "Dentist",
    "healthcare=hospital": "Hospital",
    "healthcare=centre": "Health centre",
    "healthcare=physiotherapist": "Physiotherapist",
    "healthcare=optometrist": "Optometrist",
    "healthcare=audiologist": "Audiologist",
    "healthcare=podiatrist": "Podiatrist",
    "healthcare=psychotherapist": "Psychotherapist",
    "healthcare=counselling": "Counselling",
    "healthcare=alternative": "Alternative medicine",
    "healthcare=laboratory": "Medical laboratory",
    "healthcare=sample_collection": "Medical laboratory",
    "healthcare=blood_donation": "Blood donation",
    "healthcare=rehabilitation": "Rehabilitation",
    "healthcare=vaccination_centre": "Vaccination centre",
    "healthcare=hospice": "Hospice",
    "healthcare=yes": "Health care",
    "office=company": "Office",
    "office=yes": "Office",
    "office=commercial": "Office",
    "office=healthcare": "Health care office",
    "office=security": "Security office",
    "office=engineering": "Engineering office",
    "office=government": "Government office",
    "office=lawyer": "Law office",
    "office=insurance": "Insurance office",
    "office=estate_agent": "Real estate office",
    "office=accountant": "Accountant",
    "office=tax_advisor": "Tax advisor",
    "office=financial": "Financial office",
    "office=it": "IT company",
    "office=association": "Association",
    "office=ngo": "Non-profit",
    "office=charity": "Charity",
    "office=foundation": "Foundation",
    "office=educational_institution": "Educational institution",
    "office=employment_agency": "Employment agency",
    "office=newspaper": "Newspaper",
    "office=architect": "Architect",
    "office=engineer": "Engineering office",
    "office=consulting": "Consulting office",
    "office=telecommunication": "Telecom office",
    "office=notary": "Notary",
    "office=religion": "Religious office",
    "office=research": "Research office",
    "office=travel_agent": "Travel agency",
    "office=coworking": "Coworking space",
    "office=diplomatic": "Consulate",
    "office=political_party": "Political party office",
    "office=property_management": "Property management",
    "office=construction_company": "Construction company",
    "office=moving_company": "Moving company",
    "office=energy_supplier": "Energy supplier",
}

# A feature of the same name this close to one already kept is the same place.
DUPLICATE_M = 50.0
# A feature named like a station or a config place (or its alias) this close to it
# duplicates the curated entry, whose coordinate was checked.
CURATED_DUPLICATE_M = 1000.0
# Without addr:* tags, the nearest indexed address this close gives the detail.
NEAREST_ADDRESS_M = 150.0
# A longer name is not a name people type; it also widens the fixed-width arrays.
MAX_LABEL_CHARS = 120
EARTH_RADIUS_M = 6_371_008.8

_HOUSE_NUMBER = re.compile(r"^\d{1,7}[A-Za-z]?$")


def category_label(key: str, value: str) -> str:
    """Plain English for one tag: "amenity", "place_of_worship" -> "Place of worship"."""
    first = value.split(";")[0].strip()
    label = CATEGORY_LABELS.get(f"{key}={first}")
    if label:
        return label
    text = " ".join(first.replace("_", " ").split())
    return text[:1].upper() + text[1:]


def is_destination(key: str, value: str, tags: Mapping[str, str]) -> bool:
    """Whether a POI tag names somewhere to go, not parking or street furniture."""
    first = value.split(";")[0].strip()
    if first in NOT_DESTINATIONS.get(key, frozenset()):
        return False
    if key == "tourism" and first == "information":
        return tags.get("information") in INFORMATION_OFFICES
    return True


def poi_category(tags: Mapping[str, str]) -> str | None:
    """The category label of a feature, or None when no POI tag is a destination."""
    for key in POI_TAGS:
        value = tags.get(key)
        if value and is_destination(key, value, tags):
            return category_label(key, value)
    return None


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular distance between two (lat, lng) points; plenty across a city."""
    lat = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(lat)
    dy = math.radians(b[0] - a[0])
    return EARTH_RADIUS_M * math.hypot(dx, dy)


def _metres(lat: np.ndarray, lng: np.ndarray, lat0_rad: float) -> np.ndarray:
    """(x, y) in metres about latitude ``lat0``, for a k-d tree over one city."""
    x = np.radians(np.asarray(lng, dtype=np.float64)) * math.cos(lat0_rad)
    y = np.radians(np.asarray(lat, dtype=np.float64))
    return np.column_stack([x, y]) * EARTH_RADIUS_M


class _NearestAddress:
    """The nearest indexed address within ``NEAREST_ADDRESS_M`` (scipy k-d tree)."""

    def __init__(self, index: SearchIndex) -> None:
        from scipy.spatial import cKDTree  # build time only; not on the request path

        self.index = index
        self.lat0 = (
            math.radians(float(np.mean(index.addr_lat))) if len(index.addr_lat) else 0.0
        )
        self.tree = cKDTree(_metres(index.addr_lat, index.addr_lng, self.lat0))

    def label(self, lat: float, lng: float) -> str | None:
        point = _metres(np.array([lat]), np.array([lng]), self.lat0)[0]
        found, row = self.tree.query(point, distance_upper_bound=NEAREST_ADDRESS_M)
        if not math.isfinite(found):
            return None
        number = int(self.index.addr_number[row])
        return f"{number} {self.index.streets[int(self.index.addr_street[row])]}"


def _element_point(element: Mapping[str, Any]) -> tuple[float, float] | None:
    """A node's point, or the centre Overpass gives for a way or relation."""
    source = element if element.get("type") == "node" else element.get("center")
    try:
        lat, lng = float(source["lat"]), float(source["lon"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def _poi_aliases(name: str, tags: Mapping[str, str]) -> tuple[str, ...]:
    """``brand`` when it differs from the name, plus ``short_name`` and ``alt_name``."""
    own = normalise(name)
    aliases: list[str] = []
    seen: list[list[str]] = [own]
    for key in ("brand", "short_name", "alt_name"):
        for raw in str(tags.get(key, "")).split(";"):
            alias = " ".join(raw.replace("|", " ").split())
            tokens = normalise(alias)
            if alias and tokens and tokens not in seen:
                seen.append(tokens)
                aliases.append(alias)
    return tuple(aliases)


def _osm_address(tags: Mapping[str, str]) -> str | None:
    """``addr:housenumber addr:street`` as OpenStreetMap spells it, when both exist."""
    number = " ".join(str(tags.get("addr:housenumber", "")).split())
    street = " ".join(str(tags.get("addr:street", "")).split())
    if number and street and _HOUSE_NUMBER.match(number):
        return f"{number} {street}"
    return None


def parse_poi_elements(
    elements: Iterable[Mapping[str, Any]],
    *,
    addresses: SearchIndex | None = None,
    curated: Iterable[NamedPlace] = (),
) -> tuple[list[NamedPlace], dict[str, int]]:
    """Overpass ``out center`` elements -> points of interest, plus what was dropped.

    Kept: a named feature with a destination category (``poi_category``), at the
    node or at the centre Overpass gives for a way or relation. Dropped and counted:
    ``nameless``, ``no_point``, ``not_destination``, ``station``
    (``public_transport=station``: the City's LRT list has them), ``too_long``,
    ``duplicate`` (same name within ``DUPLICATE_M`` of one already kept; features
    with an address, then nodes, are kept first) and ``curated_duplicate`` (named
    like a station or config place, or its alias, within ``CURATED_DUPLICATE_M``).

    ``detail`` is "<Category>, <address>": the ``addr:*`` tags when OpenStreetMap
    has them, else the nearest address in ``addresses`` within ``NEAREST_ADDRESS_M``,
    else the category alone. Aliases come from ``brand``, ``short_name`` and
    ``alt_name``. Every point of interest is ``kind="place"`` in ``GROUP_POI``.
    """
    counts: dict[str, int] = defaultdict(int)
    curated_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for place in curated:
        for term in (place.label, *place.aliases):
            curated_points[" ".join(normalise(term))].append((place.lat, place.lng))
    nearest = _NearestAddress(addresses) if addresses is not None else None

    Candidate = tuple[tuple[int, int], str, str, tuple[float, float], dict[str, str]]
    candidates: list[Candidate] = []
    for element in elements:
        counts["returned"] += 1
        tags = element.get("tags") or {}
        name = " ".join(str(tags.get("name", "")).replace("|", " ").split())
        if not name:
            counts["nameless"] += 1
            continue
        if len(name) > MAX_LABEL_CHARS:
            counts["too_long"] += 1
            continue
        point = _element_point(element)
        if point is None:
            counts["no_point"] += 1
            continue
        if tags.get("public_transport") == "station" or tags.get("railway") in {
            "station",
            "halt",
            "tram_stop",
        }:
            counts["station"] += 1
            continue
        category = poi_category(tags)
        if category is None:
            counts["not_destination"] += 1
            continue
        preference = (
            0 if _osm_address(tags) else 1,
            0 if element.get("type") == "node" else 1,
        )
        candidates.append((preference, name, category, point, dict(tags)))
    candidates.sort(key=lambda c: c[0])

    kept_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    pois: list[NamedPlace] = []
    for _, name, category, point, tags in candidates:
        key = " ".join(normalise(name))
        if any(
            distance_m(point, p) <= CURATED_DUPLICATE_M
            for p in curated_points.get(key, ())
        ):
            counts["curated_duplicate"] += 1
            continue
        if any(distance_m(point, p) <= DUPLICATE_M for p in kept_points[key]):
            counts["duplicate"] += 1
            continue
        kept_points[key].append(point)
        address = _osm_address(tags)
        if address:
            counts["with_osm_address"] += 1
        elif nearest is not None and (address := nearest.label(*point)):
            counts["with_nearest_address"] += 1
        else:
            counts["without_address"] += 1
        pois.append(
            NamedPlace(
                label=name,
                kind="place",
                lat=point[0],
                lng=point[1],
                detail=f"{category}, {address}" if address else category,
                aliases=_poi_aliases(name, tags),
                group=GROUP_POI,
            )
        )
    counts["kept"] = len(pois)
    return pois, dict(counts)


# ------------------------------------------------------------------ the index


class SearchResult(BaseModel):
    """One match of ``GET /api/search``."""

    label: str  # "10220 104 Avenue NW", "Central Station", "Rogers Place"
    detail: str | None  # "LRT station", the place's address; None for an address
    lat: float
    lng: float
    kind: Kind


# One query word's spellings: as typed, and through WORD_ALIASES ({"ave", "avenue"}).
Options = frozenset[str]


def options(word: str) -> Options:
    return frozenset({word, canonical(word)})


def _prefix_owners(
    sorted_tokens: np.ndarray, owners: np.ndarray, prefix: str
) -> set[int]:
    """Ids of the entries that own a token starting with ``prefix`` (two binary searches)."""
    lo = int(np.searchsorted(sorted_tokens, prefix, side="left"))
    hi = int(np.searchsorted(sorted_tokens, prefix + "\uffff", side="left"))
    return set(owners[lo:hi].tolist())


class _TokenIndex:
    """Sorted (token, owner) pairs: every query word must prefix a token of the owner.

    ``build()`` makes the two arrays from token lists; the name indexes are built
    once by ``build_index()`` and stored in ``search.npz``, so loading the index does
    no text normalisation (OP-416: with 9,000 points of interest it took 100 ms).
    """

    def __init__(self, tokens: np.ndarray, owners: np.ndarray) -> None:
        self.tokens = np.asarray(tokens, dtype=str)
        self.owners = np.asarray(owners, dtype=np.int32)
        self._vocabulary: list[str] | None = None

    @classmethod
    def build(cls, token_lists: Sequence[Sequence[str]]) -> _TokenIndex:
        pairs = sorted(
            {
                (token, owner)
                for owner, tokens in enumerate(token_lists)
                for token in tokens
            }
        )
        return cls(
            np.array([p[0] for p in pairs], dtype=str),
            np.array([p[1] for p in pairs], dtype=np.int32),
        )

    @property
    def vocabulary(self) -> list[str]:
        """The distinct alphabetic tokens (for fuzzy matching), built on first use."""
        if self._vocabulary is None:
            self._vocabulary = [
                t for t in np.unique(self.tokens).tolist() if t.isalpha()
            ]
        return self._vocabulary

    def owners_of(self, spellings: Options) -> set[int]:
        found: set[int] = set()
        for spelling in spellings:
            found |= _prefix_owners(self.tokens, self.owners, spelling)
        return found

    def match_all(self, query: Sequence[Options]) -> set[int] | None:
        """Owners matching every query word; None when there are no words to match."""
        result: set[int] | None = None
        for spellings in query:
            found = self.owners_of(spellings)
            result = found if result is None else result & found
            if not result:
                return set()
        return result


# The prebuilt name lookup arrays stored in ``search.npz`` (see ``_TokenIndex``).
LOOKUP_KEYS = (
    "name_tok",
    "name_owner",
    "alias_tok",
    "alias_owner",
    "word_tok",
    "word_owner",
)


class SearchIndex:
    """The prebuilt index: arrays from ``search.npz`` plus the lookup structures."""

    def __init__(
        self,
        *,
        addr_number: np.ndarray,
        addr_street: np.ndarray,
        addr_lat: np.ndarray,
        addr_lng: np.ndarray,
        streets: np.ndarray,
        name_label: np.ndarray,
        name_detail: np.ndarray,
        name_kind: np.ndarray,
        name_terms: np.ndarray,
        name_group: np.ndarray,
        name_lat: np.ndarray,
        name_lng: np.ndarray,
        manifest: dict[str, Any],
        lookup: Mapping[str, np.ndarray] | None = None,
    ) -> None:
        self.addr_number = addr_number
        self.addr_street = addr_street
        self.addr_lat = addr_lat
        self.addr_lng = addr_lng
        self.streets: list[str] = np.asarray(streets, dtype=str).tolist()
        self.name_label: list[str] = np.asarray(name_label, dtype=str).tolist()
        self.name_detail: list[str | None] = [
            s or None for s in np.asarray(name_detail, dtype=str).tolist()
        ]
        self.name_kind: list[str] = np.asarray(name_kind, dtype=str).tolist()
        self.name_terms: list[list[str]] = [
            s.split("|") for s in np.asarray(name_terms, dtype=str).tolist()
        ]
        self.name_group: list[int] = np.asarray(name_group, dtype=np.int8).tolist()
        self.name_lat = name_lat
        self.name_lng = name_lng
        self.manifest = manifest
        # The street lookup is small and is rebuilt at load.
        street_tokens = [normalise(s) for s in self.streets]
        self._street_tokens = [set(tokens) for tokens in street_tokens]
        self._street_index = _TokenIndex.build(street_tokens)
        # The name lookups come prebuilt from the file; ``build_index`` makes them.
        if lookup is None:
            lookup = self._build_lookup()
        self._name_index = _TokenIndex(lookup["name_tok"], lookup["name_owner"])
        self._alias_index = _TokenIndex(lookup["alias_tok"], lookup["alias_owner"])
        self._word_index = _TokenIndex(lookup["word_tok"], lookup["word_owner"])
        self._vocabulary_cache: list[str] | None = None

    def _build_lookup(self) -> dict[str, np.ndarray]:
        """The name lookup arrays from the terms and details (build time only)."""
        # Each entry's terms as canonical token lists: the label first, then aliases.
        term_tokens = [[normalise(term) for term in terms] for terms in self.name_terms]
        # Tier 0: the label of every entry, plus the curated aliases of group 0.
        # Tier 1: the OpenStreetMap aliases of group 1.
        name = _TokenIndex.build(
            [
                [
                    " ".join(t)
                    for t in (tokens if group == GROUP_CURATED else tokens[:1])
                ]
                for tokens, group in zip(term_tokens, self.name_group, strict=True)
            ]
        )
        alias = _TokenIndex.build(
            [
                [" ".join(t) for t in tokens[1:]] if group == GROUP_POI else []
                for tokens, group in zip(term_tokens, self.name_group, strict=True)
            ]
        )
        # Tiers 3 and 4: every word of the label, the aliases and the detail.
        word = _TokenIndex.build(
            [
                [t for tokens in entry for t in tokens]
                + (normalise(detail) if detail else [])
                for entry, detail in zip(term_tokens, self.name_detail, strict=True)
            ]
        )
        return {
            "name_tok": name.tokens,
            "name_owner": name.owners,
            "alias_tok": alias.tokens,
            "alias_owner": alias.owners,
            "word_tok": word.tokens,
            "word_owner": word.owners,
        }

    @property
    def lookup(self) -> dict[str, np.ndarray]:
        """The name lookup arrays, as ``save()`` writes them."""
        return {
            "name_tok": self._name_index.tokens,
            "name_owner": self._name_index.owners,
            "alias_tok": self._alias_index.tokens,
            "alias_owner": self._alias_index.owners,
            "word_tok": self._word_index.tokens,
            "word_owner": self._word_index.owners,
        }

    @property
    def _vocabularies(self) -> tuple[list[str], list[str]]:
        """The words a typo is corrected against, built on the first misspelt query.

        First the words of street names, stations and config places (the whole
        vocabulary before OP-416), then the words only points of interest use. A
        typo is corrected against the first list before the second, so "rogres"
        still becomes "rogers" (Rogers Place) rather than the closer "progress" of
        some shop, while "chipotel" still becomes "chipotle".
        """
        if self._vocabulary_cache is None:
            owners = self._word_index.owners
            poi_entries = np.fromiter(
                (group == GROUP_POI for group in self.name_group),
                dtype=bool,
                count=len(self.name_group),
            )
            from_poi = poi_entries[owners] if len(owners) else np.zeros(0, dtype=bool)
            tokens = self._word_index.tokens
            curated = set(self._street_index.vocabulary) | {
                t for t in tokens[~from_poi].tolist() if t.isalpha()
            }
            poi_only = {t for t in tokens[from_poi].tolist() if t.isalpha()} - curated
            self._vocabulary_cache = (sorted(curated), sorted(poi_only))
        return self._vocabulary_cache

    # ------------------------------------------------------------ persistence

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            format_version=np.array(FORMAT_VERSION, dtype=np.int32),
            addr_number=self.addr_number,
            addr_street=self.addr_street,
            addr_lat=self.addr_lat,
            addr_lng=self.addr_lng,
            streets=np.array(self.streets, dtype=str),
            name_label=np.array(self.name_label, dtype=str),
            name_detail=np.array([d or "" for d in self.name_detail], dtype=str),
            name_kind=np.array(self.name_kind, dtype=str),
            name_terms=np.array(["|".join(t) for t in self.name_terms], dtype=str),
            name_group=np.array(self.name_group, dtype=np.int8),
            name_lat=self.name_lat,
            name_lng=self.name_lng,
            manifest=np.array(json.dumps(self.manifest), dtype=str),
            **self.lookup,
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_INDEX_PATH) -> SearchIndex:
        with np.load(path, allow_pickle=False) as data:
            version = int(data["format_version"])
            if version != FORMAT_VERSION:
                raise ValueError(
                    f"{path} is search index format {version}; this code reads {FORMAT_VERSION}"
                )
            return cls(
                addr_number=data["addr_number"],
                addr_street=data["addr_street"],
                addr_lat=data["addr_lat"],
                addr_lng=data["addr_lng"],
                streets=data["streets"],
                name_label=data["name_label"],
                name_detail=data["name_detail"],
                name_kind=data["name_kind"],
                name_terms=data["name_terms"],
                name_group=data["name_group"],
                name_lat=data["name_lat"],
                name_lng=data["name_lng"],
                manifest=json.loads(str(data["manifest"])),
                lookup={key: data[key] for key in LOOKUP_KEYS},
            )

    # ------------------------------------------------------------------ search

    def __len__(self) -> int:
        return len(self.addr_number) + len(self.name_label)

    def find_address(self, number: int, street: str) -> tuple[float, float] | None:
        """The (lat, lng) of one exact address, in any spelling; None if it is not indexed."""
        wanted = normalise(street)
        lo = int(np.searchsorted(self.addr_number, number, side="left"))
        hi = int(np.searchsorted(self.addr_number, number, side="right"))
        for row in range(lo, hi):
            if normalise(self.streets[self.addr_street[row]]) == wanted:
                return float(self.addr_lat[row]), float(self.addr_lng[row])
        return None

    def search(self, query: str) -> list[SearchResult]:
        """Up to ``MAX_RESULTS`` matches, best first. See the module docstring for the tiers."""
        if len(query.strip()) < MIN_QUERY_CHARS:
            return []
        typed = words(query[:MAX_QUERY_CHARS])
        if not typed:
            return []
        spelt = [options(word) for word in typed]
        # Each entry: (sort key, result). Keys are (tier, group, rank, label): the
        # tier first; within it stations and config places (0), then points of
        # interest (1), then addresses (2); then the rank inside the group.
        scored: list[tuple[tuple[Any, ...], SearchResult]] = []
        seen_names: set[int] = set()

        def add_names(ids: Iterable[int], tier: int) -> None:
            for i in sorted(ids):
                if i in seen_names:
                    continue
                seen_names.add(i)
                label = self.name_label[i]
                scored.append(
                    (
                        (tier, self.name_group[i], len(label), label),
                        SearchResult(
                            label=label,
                            detail=self.name_detail[i],
                            lat=round(float(self.name_lat[i]), 6),
                            lng=round(float(self.name_lng[i]), 6),
                            kind=self.name_kind[i],  # type: ignore[arg-type]
                        ),
                    )
                )

        # Tiers 0 and 1: the whole query starts a name, then an OSM alias. The last
        # word may be half typed, so it also counts as typed ("rogers pl" starts
        # "rogers place").
        canon = [canonical(word) for word in typed]
        heads = frozenset({" ".join(canon), " ".join([*canon[:-1], typed[-1]])})
        add_names(self._name_index.owners_of(heads), TIER_NAME)
        add_names(self._alias_index.owners_of(heads), TIER_ALIAS)
        # Tier 2: house number plus street.
        scored.extend(self._address_matches(spelt, tier=TIER_ADDRESS))
        # Tier 3: every word starts a word of the name, an alias or the detail.
        add_names(self._word_index.match_all(spelt) or (), TIER_WORDS)
        # Tier 4: fuzzy, only when the exact tiers left room.
        if len(scored) < MAX_RESULTS:
            fixed = self._fuzzy(typed, spelt)
            if fixed is not None:
                add_names(self._word_index.match_all(fixed) or (), TIER_FUZZY)
                scored.extend(self._address_matches(fixed, tier=TIER_FUZZY))
        scored.sort(key=lambda entry: entry[0])
        return [result for _, result in scored[:MAX_RESULTS]]

    def _address_matches(
        self, spelt: Sequence[Options], *, tier: int
    ) -> list[tuple[tuple[Any, ...], SearchResult]]:
        first = next(iter(spelt[0])) if len(spelt[0]) == 1 else ""
        if not (first.isdigit() and len(first) <= 7):
            return []
        number = int(first)
        lo = int(np.searchsorted(self.addr_number, number, side="left"))
        hi = int(np.searchsorted(self.addr_number, number, side="right"))
        if lo == hi:
            return []
        rows = np.arange(lo, hi)
        street_query = spelt[1:]
        street_ids = self._street_index.match_all(street_query)
        if street_ids is not None:
            keep = np.isin(
                self.addr_street[rows], np.fromiter(street_ids, dtype=np.int32)
            )
            rows = rows[keep]
        wanted = set().union(*street_query) if street_query else set()
        out = []
        for row in rows.tolist():
            street_id = int(self.addr_street[row])
            street = self.streets[street_id]
            exact_hits = len(wanted & self._street_tokens[street_id])
            label = f"{number} {street}"
            out.append(
                (
                    # Whole street words first ("104" over "104A"), then shorter names.
                    (tier, 2, len(street) - 100 * exact_hits, label),
                    SearchResult(
                        label=label,
                        detail=None,
                        lat=round(float(self.addr_lat[row]), 6),
                        lng=round(float(self.addr_lng[row]), 6),
                        kind="address",
                    ),
                )
            )
        out.sort(key=lambda entry: entry[0])
        return out[:MAX_RESULTS]

    def _fuzzy(
        self, typed: Sequence[str], spelt: Sequence[Options]
    ) -> list[Options] | None:
        """Each unknown word replaced by its closest indexed word; None if none changed.

        The street, station and config place words are tried first, then the words
        only points of interest use (see ``_vocabularies``).
        """
        fixed: list[Options] = []
        changed = False
        for word, spellings in zip(typed, spelt, strict=True):
            unknown = (
                len(word) >= FUZZY_MIN_CHARS
                and word.isalpha()
                and not self._street_index.owners_of(spellings)
                and not self._word_index.owners_of(spellings)
            )
            close: list[str] = []
            if unknown:
                for vocabulary in self._vocabularies:
                    close = difflib.get_close_matches(
                        word, vocabulary, n=1, cutoff=FUZZY_CUTOFF
                    )
                    if close:
                        break
            if close:
                fixed.append(frozenset(close))
                changed = True
            else:
                fixed.append(spellings)
        return fixed if changed else None


# ------------------------------------------------------------------- building


def build_index(
    addresses: Iterable[AddressRow],
    named: Iterable[NamedPlace],
    *,
    manifest: Mapping[str, Any] | None = None,
) -> SearchIndex:
    """Deduplicate and sort the rows into the arrays ``SearchIndex`` searches.

    One entry per (house number, street): the mean of its parcel points, or of its
    suite points when the address exists only as suites. That is 58,064 of the
    346,922 addresses citywide (November 8, 2026), among them Edmonton City Centre
    and Stantec Tower.
    """
    parcel_pts: dict[tuple[int, str], list[tuple[float, float]]] = defaultdict(list)
    suite_pts: dict[tuple[int, str], list[tuple[float, float]]] = defaultdict(list)
    parcel_rows = suite_rows = 0
    for row in addresses:
        key = (row.number, row.street)
        if row.parcel:
            parcel_rows += 1
            parcel_pts[key].append((row.lat, row.lng))
        else:
            suite_rows += 1
            suite_pts[key].append((row.lat, row.lng))
    keys = set(parcel_pts) | set(suite_pts)
    streets = sorted({street for _, street in keys})
    street_id = {street: i for i, street in enumerate(streets)}
    ordered = sorted(keys, key=lambda k: (k[0], street_id[k[1]]))
    numbers = np.empty(len(ordered), dtype=np.int32)
    street_ids = np.empty(len(ordered), dtype=np.int32)
    lats = np.empty(len(ordered), dtype=np.float32)
    lngs = np.empty(len(ordered), dtype=np.float32)
    for i, key in enumerate(ordered):
        pts = parcel_pts.get(key) or suite_pts[key]
        numbers[i] = key[0]
        street_ids[i] = street_id[key[1]]
        lats[i] = sum(p[0] for p in pts) / len(pts)
        lngs[i] = sum(p[1] for p in pts) / len(pts)

    named_list = sorted(named, key=lambda p: (p.kind, p.group, p.label, p.detail or ""))
    counts = {
        "addresses": len(ordered),
        "streets": len(streets),
        "parcel_rows": parcel_rows,
        "suite_rows": suite_rows,
        "stations": sum(1 for p in named_list if p.kind == "station"),
        "places": sum(
            1 for p in named_list if p.kind == "place" and p.group == GROUP_CURATED
        ),
        "points_of_interest": sum(1 for p in named_list if p.group == GROUP_POI),
    }
    full_manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "built_at": datetime.now(UTC).isoformat(),
        "attribution": ATTRIBUTION,
        "osm_attribution": OSM_ATTRIBUTION,
        "sources": {
            "addresses": f"https://data.edmonton.ca/d/{PARCEL_DATASET}",
            "stations": f"https://data.edmonton.ca/d/{STATIONS_DATASET}",
            "places": "config/places.json",
            "points_of_interest": OSM_LICENCE_URL,
        },
        "counts": counts,
        **(dict(manifest) if manifest else {}),
    }
    if len(ordered):
        full_manifest["bbox"] = [
            round(float(lngs.min()), 6),
            round(float(lats.min()), 6),
            round(float(lngs.max()), 6),
            round(float(lats.max()), 6),
        ]
    return SearchIndex(
        addr_number=numbers,
        addr_street=street_ids,
        addr_lat=lats,
        addr_lng=lngs,
        streets=np.array([street_label(s) for s in streets], dtype=str),
        name_label=np.array([p.label for p in named_list], dtype=str),
        name_detail=np.array([p.detail or "" for p in named_list], dtype=str),
        name_kind=np.array([p.kind for p in named_list], dtype=str),
        name_terms=np.array(
            ["|".join((p.label, *p.aliases)) for p in named_list], dtype=str
        ),
        name_group=np.array([p.group for p in named_list], dtype=np.int8),
        name_lat=np.array([p.lat for p in named_list], dtype=np.float64),
        name_lng=np.array([p.lng for p in named_list], dtype=np.float64),
        manifest=full_manifest,
    )


def load_index(path: Path = DEFAULT_INDEX_PATH) -> SearchIndex | None:
    """The committed index, or None (with one log line) when it is missing or unreadable.

    ``/api/search`` then answers 503 instead of the app failing to start.
    """
    if not Path(path).exists():
        log.warning("SEARCH index %s missing; /api/search answers 503", path)
        return None
    try:
        started = time.perf_counter()
        index = SearchIndex.load(path)
    except Exception:
        log.exception("SEARCH index %s unreadable; /api/search answers 503", path)
        return None
    pois = sum(1 for group in index.name_group if group == GROUP_POI)
    log.info(
        "SEARCH index loaded: %d addresses, %d stations and places, "
        "%d points of interest, %.0f ms",
        len(index.addr_number),
        len(index.name_label) - pois,
        pois,
        (time.perf_counter() - started) * 1000,
    )
    return index


# -------------------------------------------------------------------- the API

router = APIRouter(prefix="/api", tags=["search"])


def get_index(request: Request) -> SearchIndex:
    index = getattr(request.app.state, "search", None)
    if index is None:
        raise HTTPException(
            503, "Search is not available: the search index is not loaded."
        )
    return index


@router.get("/search", response_model=list[SearchResult])
def search_endpoint(
    index: Annotated[SearchIndex, Depends(get_index)],
    q: Annotated[
        str,
        Query(
            max_length=MAX_QUERY_CHARS,
            description="An address, LRT station or place name, complete or partial",
            examples=["10220 104 ave", "central station", "rogers"],
        ),
    ] = "",
) -> list[SearchResult]:
    """Up to 8 addresses, LRT stations and places matching the text, best first."""
    return index.search(q)


# --------------------------------------------------------------- command line


def _benchmark_queries(index: SearchIndex, count: int = 200) -> list[str]:
    """Realistic queries drawn from the index itself (deterministic)."""
    import random

    rng = random.Random(302)
    queries: list[str] = []
    n_addr = len(index.addr_number)
    while len(queries) < count * 0.5 and n_addr:
        row = rng.randrange(n_addr)
        street = index.streets[index.addr_street[row]]
        number = int(index.addr_number[row])
        words = street.split()
        form = rng.randrange(4)
        if form == 0:
            queries.append(f"{number} {street}")
        elif form == 1:
            queries.append(f"{number} {words[0]}")
        elif form == 2:
            queries.append(f"{number}-{' '.join(words[:2]).lower()}")
        else:
            queries.append(str(number))
    labels = index.name_label
    while len(queries) < count * 0.85 and labels:
        label = rng.choice(labels)
        cut = rng.randrange(3, max(4, len(label)))
        queries.append(label[:cut].lower())
    while len(queries) < count:
        label = rng.choice(labels) if labels else "jasper avenue"
        chars = list(label.lower())
        pos = rng.randrange(len(chars))
        chars[pos] = rng.choice("abcdefghijklmnopqrstuvwxyz")
        queries.append("".join(chars))
    return queries[:count]


def _main(argv: Sequence[str]) -> int:
    import argparse
    import statistics

    parser = argparse.ArgumentParser(
        description="Query the search index, or time 200 realistic queries."
    )
    parser.add_argument("query", nargs="?", help="text to search for")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument(
        "--bench", action="store_true", help="print p50 and p95 timings"
    )
    args = parser.parse_args(argv)
    started = time.perf_counter()
    index = SearchIndex.load(args.index)
    print(
        f"loaded {args.index}: {len(index.addr_number)} addresses, "
        f"{len(index.name_label)} stations and places in "
        f"{(time.perf_counter() - started) * 1000:.0f} ms"
    )
    if args.query:
        for result in index.search(args.query):
            detail = f"  [{result.detail}]" if result.detail else ""
            print(
                f"  {result.kind:8s} {result.label}{detail}  ({result.lat}, {result.lng})"
            )
    if args.bench:
        queries = _benchmark_queries(index)
        timings_ms = []
        for query in queries:
            t0 = time.perf_counter()
            index.search(query)
            timings_ms.append((time.perf_counter() - t0) * 1000)
        timings_ms.sort()
        p50 = statistics.median(timings_ms)
        p95 = statistics.quantiles(timings_ms, n=100)[94]
        print(
            f"{len(queries)} queries: p50 {p50:.2f} ms, p95 {p95:.2f} ms, "
            f"max {timings_ms[-1]:.2f} ms"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
