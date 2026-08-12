"""Reading crawl's item-selection menu.

Unlike the skill screen, this one arrives structured — the ``menu`` message for
``use_item`` carries an ``items`` array with the text, the hotkey and, most
usefully, the stack size::

    {"text": " d - 4 bubbling green potions", "q": 4, "hotkeys": [100], "level": 2}

``level`` 1 is a category heading ("Potions"), 2 an item. ``q`` is the number
carried, which is what "the most duplicates" means for picking an unknown one.

Whether an item is identified is decided by its *name*, which item-name.cc
builds one of two ways: identified potions and scrolls read ``potion of X`` /
``scroll of X``, while unidentified ones are described by appearance —
``bubbling green potion``, ``scroll labelled XYDIOF MEIRA``. So the presence
of "… of " is the whole test, and it is a property of crawl's naming rather
than a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .formatting import clean_line

#: " d - 4 bubbling green potions" → key, count, name.
_ROW = re.compile(r"^\s*(\S)\s+-\s+(.*)$")
#: A leading stack size or article, which is presentation rather than identity.
_LEADING_COUNT = re.compile(r"^(\d+)\s+")
_LEADING_ARTICLE = re.compile(r"^(?:an?|the)\s+", re.IGNORECASE)
#: item-name.cc writes "potion of X" / "scroll of X" only once identified.
_IDENTIFIED = re.compile(r"\b(?:potions?|scrolls?)\s+of\s+", re.IGNORECASE)


@dataclass(frozen=True)
class ItemRow:
    """One selectable item as the menu currently shows it."""

    key: str
    name: str
    quantity: int
    identified: bool


class ItemMenuError(Exception):
    """The item menu did not contain what was asked for."""


def parse_item_menu(items: list[dict]) -> list[ItemRow]:
    """Extract the selectable rows from a ``menu`` message's items."""
    rows: list[ItemRow] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        # level 1 is a category heading with no hotkey of its own.
        hotkeys = entry.get("hotkeys")
        if not isinstance(hotkeys, list) or not hotkeys:
            continue
        match = _ROW.match(clean_line(str(entry.get("text", ""))))
        if not match:
            continue
        key, rest = match.groups()

        counted = _LEADING_COUNT.match(rest)
        if counted:
            rest = rest[counted.end():]
        rest = _LEADING_ARTICLE.sub("", rest).strip()

        quantity = entry.get("q")
        if not isinstance(quantity, int) or quantity < 1:
            quantity = int(counted.group(1)) if counted else 1

        rows.append(
            ItemRow(
                key=key,
                name=rest,
                quantity=quantity,
                identified=bool(_IDENTIFIED.search(rest)),
            )
        )
    return rows


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def find_item(rows: list[ItemRow], query: str) -> ItemRow:
    """Find the one item matching ``query``.

    Matches the identified name ("haste" finds a potion of haste) or the
    appearance of an unidentified one ("bubbling green"). Refuses rather than
    guessing when a fragment fits several, since drinking the wrong potion is
    not a recoverable mistake.
    """
    wanted = _normalise(query)
    if not wanted:
        raise ItemMenuError("no item given")

    exact = [r for r in rows if _normalise(r.name) == wanted]
    if exact:
        return exact[0]

    candidates = [r for r in rows if wanted in _normalise(r.name)]
    if not candidates:
        raise ItemMenuError(f"nothing matching {query!r} is in the inventory")
    if len(candidates) > 1:
        names = ", ".join(sorted(r.name for r in candidates))
        raise ItemMenuError(f"{query!r} could mean any of: {names}")
    return candidates[0]


def pick_unknown(rows: list[ItemRow]) -> ItemRow:
    """Choose an unidentified item: most duplicates first, then alphabetical.

    Drinking from the biggest stack first is the usual way to identify by use
    — a duplicate is the cheapest one to spend — and the alphabetical
    tie-break makes the choice reproducible rather than dependent on menu
    order.
    """
    unknown = [r for r in rows if not r.identified]
    if not unknown:
        raise ItemMenuError("nothing unidentified to try")
    unknown.sort(key=lambda r: (-r.quantity, _normalise(r.name)))
    return unknown[0]
