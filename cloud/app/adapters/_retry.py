"""Read an HTTP ``Retry-After`` header as a delay in seconds.

Shared by the adapters that are rate-limited by their provider (the LRT feed on
Socrata, Open-Meteo in the weather adapter). The header is either a number of
seconds or an HTTP date; anything else, or a date in the past, is not a delay.
The adapter owns what to do with the delay; this never sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Fetch time must include a timezone")
    return value.astimezone(UTC)


def retry_after_s(value: str | None, now: datetime) -> float | None:
    """Seconds to wait from ``now``, or None when the header is missing or unreadable."""
    if not value:
        return None
    try:
        if value.strip().isdigit():
            return float(value)
        retry_at = parsedate_to_datetime(value)
        return max(0.0, (_utc(retry_at) - _utc(now)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None
