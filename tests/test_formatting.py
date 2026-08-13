"""The format-string rules are the ones in util.js, edge cases included."""

from __future__ import annotations

import pytest

from dcssbot.formatting import (
    clean_line,
    death_report,
    escape_for_code_block,
    escape_markdown,
    strip_format,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain text", "plain text"),
        ("<lightred>ouch</lightred>", "ouch"),
        ("<w>?</w> for help", "? for help"),
        ("<bg:blue>lit</bg:blue>", "lit"),
        # An unknown tag name is not markup; it stays as written.
        ("a <bat> flits past", "a <bat> flits past"),
        # '<<' is the escape for a literal '<'.
        ("HP << 10", "HP < 10"),
        ("<<lightred>", "<lightred>"),
        # A bare '>' or '&' is ordinary text, not an entity.
        ("HP > 10 & rising", "HP > 10 & rising"),
        # Lookup is case-sensitive in util.js, so this is literal text.
        ("<LIGHTRED>shout", "<LIGHTRED>shout"),
        # A tag without its closing bracket is literal too.
        ("<lightred no bracket", "<lightred no bracket"),
        ("", ""),
    ],
)
def test_strip_format(raw: str, expected: str) -> None:
    assert strip_format(raw) == expected


def test_strip_format_handles_unclosed_colour_runs() -> None:
    # crawl's auto-generated format strings routinely never close their tags.
    assert strip_format("<yellow>Welcome, <white>testbot") == "Welcome, testbot"


def test_clean_line_drops_control_characters_and_trailing_space() -> None:
    assert clean_line("You feel \x07odd.   ") == "You feel odd."


def test_clean_line_keeps_tabs_out_of_the_control_sweep() -> None:
    assert clean_line("a\tb") == "a\tb"


def test_escape_for_code_block_breaks_a_fence() -> None:
    escaped = escape_for_code_block("look: ``` end")
    assert "```" not in escaped
    # The visible characters are unchanged; only a zero-width space is added.
    assert escaped.replace("​", "") == "look: ``` end"


def test_escape_markdown_escapes_item_name_punctuation() -> None:
    assert escape_markdown("a +2 long sword of *flaming*").count("\\") == 2


# -- death reports ----------------------------------------------------------
#
# `game_ended`'s `message` is `hiscores_format_single_long(se, true)` — the
# same summary the game-over screen shows. Continuation lines carry the 13
# spaces `_hiscore_newline_string` adds to align them under the high-score
# list's rank column, which is meaningless outside that list.

REAL_DEATH_RECORD = (
    "Bloop the Skirmisher (Minotaur Fighter)\n"
    "             Began as a Minotaur Fighter on Aug 13, 2026.\n"
    "             Slain by a jackal\n"
    "             ... on level 2 of the Dungeon.\n"
    "             The game lasted 00:03:12 (412 turns).\n"
)


def test_death_report_strips_the_high_score_indentation() -> None:
    assert death_report(REAL_DEATH_RECORD) == (
        "Bloop the Skirmisher (Minotaur Fighter)\n"
        "Began as a Minotaur Fighter on Aug 13, 2026.\n"
        "Slain by a jackal\n"
        "... on level 2 of the Dungeon.\n"
        "The game lasted 00:03:12 (412 turns)."
    )


def test_death_report_drops_blank_lines_and_trailing_space() -> None:
    assert death_report("a\n\n   \nb   \n") == "a\nb"


def test_death_report_of_nothing_is_empty() -> None:
    assert death_report("") == ""
    assert death_report("   \n  ") == ""


def test_death_report_strips_colour_markup_and_control_characters() -> None:
    assert death_report("Slain by a <lightred>jackal</lightred>\x07") == (
        "Slain by a jackal"
    )
