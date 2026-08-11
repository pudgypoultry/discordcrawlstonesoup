"""Keycodes understood by the crawl binary.

Two different wire forms carry input to a game (see ``client.py``):

* printable text goes to the game's tty as ``{"msg": "input", "text": ...}``
* everything else goes over crawl's control socket as
  ``{"msg": "key", "keycode": N}``

The numbers below are the ones crawl's own webtiles client sends. Negative
values come from ``CK_*`` in ``cio.h``; that enum starts at -255 and must not
be rearranged, so the block is written out here in the same order.
"""

from __future__ import annotations

# cio.h: CK_DELETE = -255, then a sequence its own comment says must not be
# rearranged. Enumerated rather than written out as literals so the plain and
# modified variants cannot drift apart from a miscount.
_CK_SEQUENCE = (
    "DELETE",
    "UP", "DOWN", "LEFT", "RIGHT",
    "INSERT",
    "HOME", "END", "CLEAR",
    "PGUP", "PGDN",
    "TAB_PLACEHOLDER",          # unused; present only as an offset
    "SHIFT_UP", "SHIFT_DOWN", "SHIFT_LEFT", "SHIFT_RIGHT",
    "SHIFT_INSERT",
    "SHIFT_HOME", "SHIFT_END", "SHIFT_CLEAR",
    "SHIFT_PGUP", "SHIFT_PGDN",
    "SHIFT_TAB",
    "CTRL_UP", "CTRL_DOWN", "CTRL_LEFT", "CTRL_RIGHT",
    "CTRL_INSERT",
    "CTRL_HOME", "CTRL_END", "CTRL_CLEAR",
    "CTRL_PGUP", "CTRL_PGDN",
    "CTRL_TAB",
)

CK: dict[str, int] = {name: -255 + i for i, name in enumerate(_CK_SEQUENCE)}

CK_DELETE = CK["DELETE"]
CK_UP = CK["UP"]
CK_DOWN = CK["DOWN"]
CK_LEFT = CK["LEFT"]
CK_RIGHT = CK["RIGHT"]
CK_INSERT = CK["INSERT"]
CK_HOME = CK["HOME"]
CK_END = CK["END"]
CK_CLEAR = CK["CLEAR"]
CK_PGUP = CK["PGUP"]
CK_PGDN = CK["PGDN"]

# ASCII control keys. crawl reads these as plain small integers.
KEY_BACKSPACE = 8
KEY_TAB = 9
KEY_ENTER = 13
KEY_ESCAPE = 27


def ctrl(letter: str) -> int:
    """Keycode for Ctrl-<letter>, matching the CONTROL macro in defines.h."""
    if len(letter) != 1 or not letter.isalpha():
        raise ValueError(f"ctrl() needs a single letter, got {letter!r}")
    return ord(letter.upper()) - ord("A") + 1


#: Keycodes reachable through the command grammar, by token name.
NAMED_KEYCODES: dict[str, int] = {
    "tab": KEY_TAB,
    "esc": KEY_ESCAPE,
    "escape": KEY_ESCAPE,
    "enter": KEY_ENTER,
    "return": KEY_ENTER,
    "backspace": KEY_BACKSPACE,
}
