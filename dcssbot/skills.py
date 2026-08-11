"""Reading crawl's skill menu off the wire.

The skill screen is a CRT menu, so it arrives as a ``txt`` message carrying
rendered lines rather than as structured data::

    {"msg": "txt", "id": "menu_txt", "lines": {
        "1": "  <span class=\\"fg7 bg0\\">a + Fighting  2.0  7%  0</span>...",
    }}

Two facts make parsing this the only workable approach, rather than hardcoding
a skill-to-key table:

* **Hotkeys are positional.** In the "useful skills" view Unarmed Combat is
  ``b``; in the "all skills" view the same skill is ``f``. They also depend on
  which skills the character has, so they differ per character and over time.
* **Levels are only here.** Nothing else in the protocol reports a skill's
  current value, and the target this module exists to compute is derived from
  it.

Hotkeys run past ``z`` into digits once a character has enough skills, and
names arrive HTML-escaped (``Maces &amp; Flails``). Both are handled.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass

#: `a + Fighting         2.0   7%     0` — hotkey, training flag, name, level.
#: The name stops at the first run of two or more spaces, which is what
#: separates it from the level column. Two of these can share a line, since
#: the menu is laid out in two columns.
_ROW = re.compile(
    r"([A-Za-z0-9])\s+([+\-])\s+([A-Za-z][A-Za-z&' ]*?)\s{2,}(\d+\.\d+)"
)

_TAG = re.compile(r"<[^>]*>")


@dataclass(frozen=True)
class SkillRow:
    """One skill as the menu currently shows it."""

    key: str
    name: str
    level: float
    training: bool

    @property
    def next_target(self) -> int:
        """The next whole level up from here.

        3.3 and 3.0 both give 4: the point is to finish the level you are on,
        and a target equal to the current level would be met immediately.
        """
        return math.floor(self.level) + 1


class SkillMenuError(Exception):
    """The skill menu did not contain what was asked for."""


def strip_markup(line: str) -> str:
    """Drop the colour spans and unescape entities from one rendered line."""
    return html.unescape(_TAG.sub("", line))


def parse_skill_menu(lines: dict[str, str]) -> list[SkillRow]:
    """Extract every skill row from a ``menu_txt`` payload."""
    rows: list[SkillRow] = []
    seen: set[str] = set()
    for index in sorted(lines, key=_line_order):
        for match in _ROW.finditer(strip_markup(lines[index])):
            key, flag, name, level = match.groups()
            name = name.strip()
            if name in seen:
                continue
            seen.add(name)
            rows.append(
                SkillRow(key=key, name=name, level=float(level), training=flag == "+")
            )
    return rows


def _line_order(index: str) -> int:
    try:
        return int(index)
    except ValueError:
        return 0


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def find_skill(rows: list[SkillRow], query: str) -> SkillRow:
    """Find the one skill matching ``query``.

    Accepts an exact name, a prefix, or a distinctive fragment — "unarmed"
    and "maces" both work. Raises rather than guessing when a fragment could
    mean several skills, since picking one at random would silently retrain
    the wrong thing.
    """
    wanted = _normalise(query)
    if not wanted:
        raise SkillMenuError("no skill given")

    exact = [r for r in rows if _normalise(r.name) == wanted]
    if exact:
        return exact[0]

    prefix = [r for r in rows if _normalise(r.name).startswith(wanted)]
    candidates = prefix or [r for r in rows if wanted in _normalise(r.name)]

    if not candidates:
        raise SkillMenuError(
            f"no skill matching {query!r} is listed"
        )
    if len(candidates) > 1:
        names = ", ".join(sorted(r.name for r in candidates))
        raise SkillMenuError(f"{query!r} could mean any of: {names}")
    return candidates[0]
