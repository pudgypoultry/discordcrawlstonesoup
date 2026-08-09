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

# cio.h: CK_DELETE = -255, then a fixed sequence.
CK_DELETE = -255
CK_UP = -254
CK_DOWN = -253
CK_LEFT = -252
CK_RIGHT = -251
CK_INSERT = -250
CK_HOME = -249
CK_END = -248
CK_CLEAR = -247
CK_PGUP = -246
CK_PGDN = -245

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
