"""Shared text helpers for adapters that copy free City text into ``detail``.

The Obstruction contract caps ``detail`` at 500 characters, and the scheduler skips
any item that breaks the contract. City notices are free text with no length limit,
so an adapter that copies one into ``detail`` shortens it with ``clip_detail``
instead of letting a long notice drop the whole closure from the map and routing.
The full text stays at the source: every item's ``url`` links to it.
"""

from __future__ import annotations

import unicodedata

# Obstruction.detail max_length in cloud/app/schemas.py (a test checks they agree).
DETAIL_MAX_CHARS = 500
ELLIPSIS = "..."
# Dropped from the end of a shortened text so it reads "word..." not "word,...".
_TRAILING = ",;:."
# Unicode categories of combining accents and enclosing marks, which belong to the
# letter before them.
_MARKS = ("Mn", "Me")


def clip_detail(text: str, limit: int = DETAIL_MAX_CHARS, *, keep: str = "") -> str:
    """Return ``text + keep`` in at most ``limit`` characters, shortening ``text``.

    Text that fits is returned unchanged. Otherwise ``text`` is cut at the last
    space that leaves room, ``...`` marks the cut, and ``keep`` follows whole.
    ``keep`` is a short ending worth keeping, such as the end date of a notice; if
    it would take more than half the space, it is shortened with the text instead.
    A single word longer than the room is cut inside the word, never between a
    letter and its accent. Lengths count characters, as the contract does.
    """
    if limit <= len(ELLIPSIS):
        raise ValueError("The limit must leave room for more than the ellipsis")
    if len(text) + len(keep) <= limit:
        return text + keep
    if len(keep) > (limit - len(ELLIPSIS)) // 2:
        text, keep = text + keep, ""
    room = limit - len(ELLIPSIS) - len(keep)  # len(text) > room, so text[room] exists
    head = text[:room]
    if not text[room].isspace():
        # The cut falls inside a word: end at the last space before it instead.
        space = max((i for i, char in enumerate(head) if char.isspace()), default=0)
        if space:
            head = head[:space]
        else:
            # One long word (a URL, say): cut inside it, but not between a letter
            # and its accent.
            cut = room
            while cut > 1 and unicodedata.category(text[cut]) in _MARKS:
                cut -= 1
            head = text[:cut]
    head = head.rstrip().rstrip(_TRAILING).rstrip() or text[:room]
    return head + ELLIPSIS + keep
