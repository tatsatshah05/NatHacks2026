"""Row-level data quality for the adapters (OP-407).

Every adapter that turns City rows into items owns one ``RowQuality``. During a
fetch it calls ``see(row)`` for each source row, which notes field names the
adapter does not know, and ``reject(error)`` for each row its own validation
turns away. The scheduler reads ``report()`` after a successful fetch and shows
it per source on ``GET /api/status`` (``rejected_rows``, ``unexpected_fields``,
``last_validation_error``), so a feed that changes shape is noticed instead of
silently thinning the map.

Nothing here changes what an adapter returns: a rejected row is still skipped
and never stops the fetch, and an unknown field is information, not an error.
Each adapter logs its own ``SKIP`` line per rejected row, so this module logs
nothing itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# How many unknown field names ``GET /api/status`` lists per source, sorted. A feed
# that grows twenty new columns at once has changed shape; the names past the cap
# add nothing a person needs to read.
UNEXPECTED_FIELDS_MAX = 20
# The first validation error is kept on one line and cut here, so a status
# response never carries a whole City notice.
VALIDATION_ERROR_MAX_CHARS = 200
# Socrata adds system fields (``:id``, ``:updated_at``, ``:@computed_region_...``)
# that no adapter reads. They are never unexpected.
SYSTEM_FIELD_PREFIX = ":"


@dataclass(frozen=True)
class QualityReport:
    """What one fetch found out about its rows. Immutable once read."""

    rejected_rows: int = 0  # rows the adapter's validation turned away this fetch
    unexpected_fields: tuple[str, ...] = ()  # sorted; at most UNEXPECTED_FIELDS_MAX
    last_validation_error: str | None = None  # the first rejection message this fetch


def one_line(value: object, limit: int = VALIDATION_ERROR_MAX_CHARS) -> str:
    """An error message on one line, cut to ``limit`` characters."""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


class RowQuality:
    """Counts rejected rows and unknown field names for one adapter.

    ``start()`` begins a fetch and clears the previous one; an adapter calls it at
    the top of ``fetch`` so the report always describes the latest fetch only.
    """

    def __init__(self, expected_fields: Iterable[str]) -> None:
        self.expected_fields = frozenset(expected_fields)
        self._rejected = 0
        self._unexpected: set[str] = set()
        self._first_error: str | None = None

    def start(self) -> None:
        """Forget the previous fetch."""
        self._rejected = 0
        self._unexpected = set()
        self._first_error = None

    def see(self, row: Mapping[str, Any]) -> None:
        """Note the field names in ``row`` that the adapter does not know."""
        for name in row:
            if (
                isinstance(name, str)
                and name not in self.expected_fields
                and not name.startswith(SYSTEM_FIELD_PREFIX)
            ):
                self._unexpected.add(name)

    def reject(self, error: object) -> None:
        """Count a row the adapter's validation turned away; keep the first message."""
        self._rejected += 1
        if self._first_error is None:
            self._first_error = one_line(error)

    def report(self) -> QualityReport:
        return QualityReport(
            rejected_rows=self._rejected,
            unexpected_fields=tuple(sorted(self._unexpected)[:UNEXPECTED_FIELDS_MAX]),
            last_validation_error=self._first_error,
        )


__all__ = [
    "SYSTEM_FIELD_PREFIX",
    "UNEXPECTED_FIELDS_MAX",
    "VALIDATION_ERROR_MAX_CHARS",
    "QualityReport",
    "RowQuality",
    "one_line",
]
