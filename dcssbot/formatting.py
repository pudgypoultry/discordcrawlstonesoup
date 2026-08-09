"""Turn crawl format strings into text Discord can safely display.

The ``text`` field of every entry in a ``msgs`` message is a crawl *format
string*, not HTML: colour markup looks like ``<lightred>ouch</lightred>``,
``<<`` is the escape for a literal ``<``, and a tag whose name is not a known
colour (``<bat>``) is ordinary text. The rules below mirror
``formatted_string_to_html`` in ``webserver/game_data/static/util.js`` so that
what we relay matches what a browser would show.
"""

from __future__ import annotations

import re

#: Colour names util.js recognises. Lookup is case-sensitive there, so
#: ``<LIGHTRED>`` is literal text rather than markup, and we match that.
COLOURS: frozenset[str] = frozenset(
    {
        "black",
        "blue",
        "green",
        "cyan",
        "red",
        "magenta",
        "brown",
        "lightgrey",
        "lightgray",
        "darkgrey",
        "darkgray",
        "lightblue",
        "lightgreen",
        "lightcyan",
        "lightred",
        "lightmagenta",
        "yellow",
        "h",
        "white",
        "w",
    }
)

# Same shape as the regex in util.js: an optionally-doubled '<' introducing a
# possibly-closing, possibly-'bg:'-prefixed tag name, or a bare '>' or '&'.
_TOKEN = re.compile(r"<?<(/?(?:bg:)?[a-z]*)>?|>|&", re.IGNORECASE)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _is_colour_tag(whole: str, group: str) -> bool:
    """Whether a matched token is colour markup rather than literal text."""
    if whole.startswith("<<") or not whole.endswith(">"):
        return False
    name = group
    if name.startswith("/"):
        name = name[1:]
    if name.startswith("bg:"):
        name = name[3:]
    return name in COLOURS


def strip_format(text: str) -> str:
    """Strip colour markup from a crawl format string, keeping the text."""

    def replace(match: re.Match[str]) -> str:
        whole = match.group(0)
        group = match.group(1) or ""
        if _is_colour_tag(whole, group):
            return ""
        # Not markup. '<<' collapses to one '<'; everything else is literal.
        return whole[1:] if whole.startswith("<<") else whole

    return _TOKEN.sub(replace, text)


def clean_line(text: str) -> str:
    """Format-strip a message and drop anything that would corrupt output."""
    return _CONTROL_CHARS.sub("", strip_format(text)).rstrip()


# A fenced block ends at the first ``` on its own, so a run of three backticks
# in the payload has to be broken up. A zero-width space is invisible in
# Discord and keeps the surrounding text unchanged.
_ZWSP = "​"


def escape_for_code_block(text: str) -> str:
    """Make text safe to place inside a triple-backtick Discord code block."""
    return text.replace("```", f"``{_ZWSP}`")


_MARKDOWN_SPECIALS = re.compile(r"([\\`*_~|>#\-\[\]()])")


def escape_markdown(text: str) -> str:
    """Escape text used outside a code block, e.g. a status line."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text)
