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
``normalise()`` turns "10220-104 Avenue Northwest" and "10220 104 ave nw" into the
same tokens (``["10220", "104", "ave", "nw"]``): lower case, accents removed,
punctuation to spaces, ordinals stripped ("104th" to "104") and street words and
quadrants shortened through ``WORD_ALIASES``. The same function runs on the
dataset at build time and on every query, so prefix matching works per token.

Ranking
-------
1. A station or place whose name (or alias) starts with the whole query.
2. Addresses: a leading house number, then every remaining token a prefix of a
   token of the street name ("10220 104" finds 104 Avenue NW and 104 Street NW).
3. Stations and places where every query token is a prefix of some token of the
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

# Street words and quadrants, shortened to one spelling. Both the dataset and the
# query go through this table, so "Avenue", "Ave" and "AVE." all match.
WORD_ALIASES: dict[str, str] = {
    "avenue": "ave",
    "av": "ave",
    "street": "st",
    "str": "st",
    "saint": "st",
    "road": "rd",
    "drive": "dr",
    "boulevard": "blvd",
    "boul": "blvd",
    "crescent": "cres",
    "cresc": "cres",
    "cr": "cres",
    "court": "crt",
    "ct": "crt",
    "place": "pl",
    "lane": "ln",
    "point": "pt",
    "pointe": "pt",
    "terrace": "terr",
    "tce": "terr",
    "trail": "trl",
    "tr": "trl",
    "square": "sq",
    "circle": "cir",
    "circ": "cir",
    "centre": "ctr",
    "center": "ctr",
    "gardens": "gdns",
    "crossing": "xing",
    "promenade": "prom",
    "parkway": "pkwy",
    "highway": "hwy",
    "plaza": "plz",
    "village": "vlg",
    "landing": "ldg",
    "green": "grn",
    "grove": "grv",
    "heights": "hts",
    "mount": "mt",
    "fort": "ft",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northwest": "nw",
    "northeast": "ne",
    "southwest": "sw",
    "southeast": "se",
}
QUADRANTS = frozenset({"NW", "NE", "SW", "SE"})

_ORDINAL = re.compile(r"^(\d+)(st|nd|rd|th)$")
_NOT_WORD = re.compile(r"[^a-z0-9 ]+")


# ------------------------------------------------------------------ normalising


def normalise(text: str) -> list[str]:
    """Tokens for matching: lower case, ASCII, no punctuation, aliases applied.

    >>> normalise("10220-104 Avenue Northwest")
    ['10220', '104', 'ave', 'nw']
    >>> normalise("Sir Winston Churchill Sq.")
    ['sir', 'winston', 'churchill', 'sq']
    """
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    )
    ascii_text = ascii_text.replace("'", "")  # king's -> kings, not king s
    tokens: list[str] = []
    for raw in _NOT_WORD.sub(" ", ascii_text).split():
        match = _ORDINAL.match(raw)
        word = match.group(1) if match else raw
        tokens.append(WORD_ALIASES.get(word, word))
    return tokens


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


def parse_station_rows(rows: Iterable[Mapping[str, Any]]) -> list[NamedPlace]:
    """LRT stations and stops (``fhxi-cnhe``): one entry per name, at the mean point."""
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
            )
        )
    return stations


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


def _prefix_owners(
    sorted_tokens: np.ndarray, owners: np.ndarray, prefix: str
) -> set[int]:
    """Ids of the entries that own a token starting with ``prefix`` (two binary searches)."""
    lo = int(np.searchsorted(sorted_tokens, prefix, side="left"))
    hi = int(np.searchsorted(sorted_tokens, prefix + "￿", side="left"))
    return set(owners[lo:hi].tolist())


class _TokenIndex:
    """Sorted (token, owner) pairs: every query token must prefix a token of the owner."""

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

    def match_all(self, tokens: Sequence[str]) -> set[int] | None:
        """Owners matching every token; None when there are no tokens to match."""
        result: set[int] | None = None
        for token in tokens:
            found = _prefix_owners(self.tokens, self.owners, token)
            result = found if result is None else result & found
            if not result:
                return set()
        return result

    def has_prefix(self, token: str) -> bool:
        return bool(_prefix_owners(self.tokens, self.owners, token))


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
        self._street_index = _TokenIndex([normalise(s) for s in self.streets])
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

    def search(self, query: str) -> list[SearchResult]:
        """Up to ``MAX_RESULTS`` matches, best first. See the module docstring for the tiers."""
        if len(query.strip()) < MIN_QUERY_CHARS:
            return []
        tokens = normalise(query[:MAX_QUERY_CHARS])
        if not tokens:
            return []
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
                    # Same shape as the address keys: (tier, group, rank, label).
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

        # Tier 0: the whole query is a prefix of a name or alias.
        query_text = " ".join(tokens)
        for i, prefixes in enumerate(self._name_prefixes):
            if any(p.startswith(query_text) for p in prefixes):
                add_name(i, 0)
        # Tier 1: house number plus street.
        scored.extend(self._address_matches(tokens, tier=1))
        # Tier 2: every token is a prefix of a token in the name, alias or detail.
        for i in sorted(self._name_index.match_all(tokens) or ()):
            add_name(i, 2)
        # Tier 3: fuzzy, only when the exact tiers left room.
        if len(scored) < MAX_RESULTS:
            fixed = self._fuzzy_tokens(tokens)
            if fixed != tokens:
                scored.extend(self._address_matches(fixed, tier=3))
                for i in sorted(self._name_index.match_all(fixed) or ()):
                    add_name(i, 3)
        scored.sort(key=lambda entry: entry[0])
        return [result for _, result in scored[:MAX_RESULTS]]

    def _address_matches(
        self, tokens: Sequence[str], *, tier: int
    ) -> list[tuple[tuple[Any, ...], SearchResult]]:
        number, street_tokens = split_house_number(tokens)
        if number is None:
            return []
        lo = int(np.searchsorted(self.addr_number, number, side="left"))
        hi = int(np.searchsorted(self.addr_number, number, side="right"))
        if lo == hi:
            return []
        rows = np.arange(lo, hi)
        street_ids = self._street_index.match_all(street_tokens)
        if street_ids is not None:
            keep = np.isin(
                self.addr_street[rows], np.fromiter(street_ids, dtype=np.int32)
            )
            rows = rows[keep]
        wanted = set(street_tokens)
        out = []
        for row in rows.tolist():
            street = self.streets[self.addr_street[row]]
            exact_hits = len(wanted & set(normalise(street)))
            label = f"{number} {street}"
            out.append(
                (
                    # Exact street-word hits first, then shorter street names.
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

    def _fuzzy_tokens(self, tokens: Sequence[str]) -> list[str]:
        """Replace each unknown word with its closest word in the vocabulary."""
        fixed = []
        for token in tokens:
            if (
                len(token) >= FUZZY_MIN_CHARS
                and token.isalpha()
                and not self._street_index.has_prefix(token)
                and not self._name_index.has_prefix(token)
            ):
                close = difflib.get_close_matches(
                    token, self._vocabulary, n=1, cutoff=FUZZY_CUTOFF
                )
                if close:
                    fixed.append(close[0])
                    continue
            fixed.append(token)
        return fixed


# ------------------------------------------------------------------- building


def build_index(
    addresses: Iterable[AddressRow],
    named: Iterable[NamedPlace],
    *,
    manifest: Mapping[str, Any] | None = None,
) -> SearchIndex:
    """Deduplicate and sort the rows into the arrays ``SearchIndex`` searches.

    One entry per (house number, street): the mean of its parcel points, or of its
    suite points when the address exists only as suites (about 32,000 addresses
    citywide, among them Edmonton City Centre and Stantec Tower).
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
