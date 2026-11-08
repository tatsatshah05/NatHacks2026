"""Address and place search (OP-302): ``GET /api/search?q=``.

Why this exists
---------------
People type "10220 104 ave", "churchill" or "central station" instead of finding a
point on the map. AGENTS.md forbids calling an external geocoder at request time,
so the search runs on a prebuilt index of three official sources:

- City of Edmonton Parcel Addresses (Socrata ``ut27-nrpn``): every parcel address
  and every suite address that has no parcel of its own.
- LRT stations and stops (Socrata ``fhxi-cnhe``): one entry per station, at the
  mean of its platform points.
- Named places from ``config/places.json`` (arenas, hospitals, malls, campuses),
  each with a recorded coordinate source.

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
1. A station or place whose name (or alias) starts with the whole query.
2. Addresses: a leading house number, then every remaining word a prefix of a
   word of the street name ("10220 104" finds 104 Avenue NW and 104 Street NW).
3. Stations and places where every query word is a prefix of some word of the
   name, an alias or the detail ("churchill" finds Sir Winston Churchill Square).
4. Fuzzy: a misspelt word is replaced by the closest word in the vocabulary
   ("jaspr" to "jasper") and tiers 2 and 3 run again.

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
    ``name_lat`` / ``name_lng`` float64, ``manifest`` (JSON: counts, sources, build time)
"""

from __future__ import annotations

import difflib
import json
import logging
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
FORMAT_VERSION = 1

DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "search.npz"
DEFAULT_PLACES_PATH = Path(__file__).resolve().parents[2] / "config" / "places.json"

PARCEL_DATASET = "ut27-nrpn"
STATIONS_DATASET = "fhxi-cnhe"
ATTRIBUTION = "City of Edmonton Open Data"

MAX_RESULTS = 8
MIN_QUERY_CHARS = 2
MAX_QUERY_CHARS = 200
# Fuzzy matching only for words at least this long, so "st" is never "corrected".
FUZZY_MIN_CHARS = 4
FUZZY_CUTOFF = 0.8

Kind = Literal["address", "station", "place"]

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
    """An LRT station or a named destination."""

    label: str
    kind: Kind
    lat: float
    lng: float
    detail: str | None = None  # shown under the label; also searchable
    aliases: tuple[str, ...] = ()  # alternative names, prefix-searchable


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
            points[name].append((float(row["latitude"]), float(row["longitude"])))
        except (KeyError, TypeError, ValueError):
            log.warning("SEARCH station row skipped: %r", row)
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
    """Sorted (token, owner) pairs: every query word must prefix a token of the owner."""

    def __init__(self, token_lists: Sequence[Sequence[str]]) -> None:
        pairs = sorted(
            {
                (token, owner)
                for owner, tokens in enumerate(token_lists)
                for token in tokens
            }
        )
        self.tokens = np.array([p[0] for p in pairs], dtype=str)
        self.owners = np.array([p[1] for p in pairs], dtype=np.int32)
        self.vocabulary = sorted({p[0] for p in pairs if p[0].isalpha()})

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
        name_lat: np.ndarray,
        name_lng: np.ndarray,
        manifest: dict[str, Any],
    ) -> None:
        self.addr_number = addr_number
        self.addr_street = addr_street
        self.addr_lat = addr_lat
        self.addr_lng = addr_lng
        self.streets = [str(s) for s in streets]
        self.name_label = [str(s) for s in name_label]
        self.name_detail = [str(s) or None for s in name_detail]
        self.name_kind = [str(s) for s in name_kind]
        self.name_terms = [str(s).split("|") for s in name_terms]
        self.name_lat = name_lat
        self.name_lng = name_lng
        self.manifest = manifest
        # Lookup structures, rebuilt at load (a few milliseconds).
        street_tokens = [normalise(s) for s in self.streets]
        self._street_tokens = [set(tokens) for tokens in street_tokens]
        self._street_index = _TokenIndex(street_tokens)
        self._name_prefixes = [
            [" ".join(normalise(term)) for term in terms] for terms in self.name_terms
        ]
        self._name_index = _TokenIndex(
            [
                [t for term in terms for t in normalise(term)]
                + (normalise(detail) if detail else [])
                for terms, detail in zip(self.name_terms, self.name_detail, strict=True)
            ]
        )
        self._vocabulary = sorted(
            set(self._street_index.vocabulary) | set(self._name_index.vocabulary)
        )

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
            name_lat=self.name_lat,
            name_lng=self.name_lng,
            manifest=np.array(json.dumps(self.manifest), dtype=str),
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
                name_lat=data["name_lat"],
                name_lng=data["name_lng"],
                manifest=json.loads(str(data["manifest"])),
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
        # Each entry: (sort key, result). Keys are (tier, group, rank, label): tier
        # first, names before addresses within a tier, then the rank inside the group.
        scored: list[tuple[tuple[Any, ...], SearchResult]] = []
        seen_names: set[int] = set()

        def add_name(i: int, tier: int) -> None:
            if i in seen_names:
                return
            seen_names.add(i)
            label = self.name_label[i]
            scored.append(
                (
                    (tier, 0, len(label), label),
                    SearchResult(
                        label=label,
                        detail=self.name_detail[i],
                        lat=round(float(self.name_lat[i]), 6),
                        lng=round(float(self.name_lng[i]), 6),
                        kind=self.name_kind[i],  # type: ignore[arg-type]
                    ),
                )
            )

        # Tier 0: the whole query starts a name or alias. The last word may be
        # half typed, so it also counts as typed ("rogers pl" starts "rogers place").
        canon = [canonical(word) for word in typed]
        heads = {" ".join(canon), " ".join([*canon[:-1], typed[-1]])}
        for i, prefixes in enumerate(self._name_prefixes):
            if any(p.startswith(head) for p in prefixes for head in heads):
                add_name(i, 0)
        # Tier 1: house number plus street.
        scored.extend(self._address_matches(spelt, tier=1))
        # Tier 2: every word starts a word of the name, an alias or the detail.
        for i in sorted(self._name_index.match_all(spelt) or ()):
            add_name(i, 2)
        # Tier 3: fuzzy, only when the exact tiers left room.
        if len(scored) < MAX_RESULTS:
            fixed = self._fuzzy(typed, spelt)
            if fixed is not None:
                scored.extend(self._address_matches(fixed, tier=3))
                for i in sorted(self._name_index.match_all(fixed) or ()):
                    add_name(i, 3)
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
                    (tier, 1, len(street) - 100 * exact_hits, label),
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
        """Each unknown word replaced by its closest indexed word; None if none changed."""
        fixed: list[Options] = []
        changed = False
        for word, spellings in zip(typed, spelt, strict=True):
            unknown = (
                len(word) >= FUZZY_MIN_CHARS
                and word.isalpha()
                and not self._street_index.owners_of(spellings)
                and not self._name_index.owners_of(spellings)
            )
            close = (
                difflib.get_close_matches(
                    word, self._vocabulary, n=1, cutoff=FUZZY_CUTOFF
                )
                if unknown
                else []
            )
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

    named_list = sorted(named, key=lambda p: (p.kind, p.label))
    counts = {
        "addresses": len(ordered),
        "streets": len(streets),
        "parcel_rows": parcel_rows,
        "suite_rows": suite_rows,
        "stations": sum(1 for p in named_list if p.kind == "station"),
        "places": sum(1 for p in named_list if p.kind == "place"),
    }
    full_manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "built_at": datetime.now(UTC).isoformat(),
        "attribution": ATTRIBUTION,
        "sources": {
            "addresses": f"https://data.edmonton.ca/d/{PARCEL_DATASET}",
            "stations": f"https://data.edmonton.ca/d/{STATIONS_DATASET}",
            "places": "config/places.json",
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
    log.info(
        "SEARCH index loaded: %d addresses, %d stations and places, %.0f ms",
        len(index.addr_number),
        len(index.name_label),
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
            print(f"  {result.kind:8s} {result.label}  ({result.lat}, {result.lng})")
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
