"""What chat is allowed to send.

Any single printable character is sent as itself; keys with no character need
a name. Nothing is filtered for making sense — sending `o` into an open menu
is allowed, and the game decides what that means.
"""

from __future__ import annotations

import pytest

from dcssbot.gamestate import InputContext
from dcssbot.grammar import (
    COMMANDS,
    DANGEROUS_PLAY_CHARS,
    DIRECTIONS,
    Kind,
    ParseError,
    allowed_in,
    help_text,
    is_safe_to_send,
    parse,
)
from dcssbot.keys import CK, CK_UP, KEY_ESCAPE, KEY_TAB, ctrl


def test_non_command_messages_are_ignored_silently() -> None:
    assert parse("just chatting about the run") is None
    assert parse("") is None


# -- single characters ----------------------------------------------------


@pytest.mark.parametrize("char", ["o", "z", "S", "W", "5", "0", "#", "*", "<", ">", "%", "?"])
def test_any_printable_character_is_sent_as_itself(char: str) -> None:
    parsed = parse(f".dcss/{char}")
    assert parsed is not None
    assert [(s.kind, s.text) for s in parsed.steps] == [(Kind.TEXT, char)]


def test_character_case_is_preserved() -> None:
    # `S` must stay `S`; lowercasing it would turn save-and-exit into "wait".
    assert parse(".dcss/S").steps[0].text == "S"  # type: ignore[union-attr]
    assert parse(".dcss/s").steps[0].text == "s"  # type: ignore[union-attr]


def test_a_single_character_takes_no_argument() -> None:
    with pytest.raises(ParseError):
        parse(".dcss/o explore please")


def test_a_bare_prefix_is_an_error() -> None:
    with pytest.raises(ParseError):
        parse(".dcss/")


# -- named keys -----------------------------------------------------------


def test_keys_without_a_character_need_a_name() -> None:
    assert parse(".dcss/tab").steps[0].keycode == KEY_TAB  # type: ignore[union-attr]
    assert parse(".dcss/esc").steps[0].keycode == KEY_ESCAPE  # type: ignore[union-attr]
    assert parse(".dcss/arrowup").steps[0].keycode == CK_UP  # type: ignore[union-attr]


def test_named_keys_are_case_insensitive() -> None:
    assert parse(".dcss/TAB").steps[0].keycode == KEY_TAB  # type: ignore[union-attr]


def test_ctrl_takes_a_letter() -> None:
    parsed = parse(".dcss/ctrl f")
    assert parsed is not None
    assert parsed.steps[0].keycode == ctrl("f") == 6


@pytest.mark.parametrize("bad", ["ff", "1", ""])
def test_ctrl_rejects_anything_but_one_letter(bad: str) -> None:
    with pytest.raises(ParseError):
        parse(f".dcss/ctrl {bad}".strip())


def test_directions_are_words_because_letters_are_literal() -> None:
    # `.dcss/n` is the character `n`, so north has to spell itself out.
    assert parse(".dcss/north").steps[0].text == "k"  # type: ignore[union-attr]
    assert parse(".dcss/n").steps[0].text == "n"  # type: ignore[union-attr]
    assert parse(".dcss/se").steps[0].text == "n"  # type: ignore[union-attr]


def test_run_shifts_a_direction() -> None:
    assert parse(".dcss/run ne").steps[0].text == "U"  # type: ignore[union-attr]


def test_run_rejects_a_non_direction() -> None:
    with pytest.raises(ParseError):
        parse(".dcss/run away")


def test_unknown_word_raises() -> None:
    with pytest.raises(ParseError, match="unknown command"):
        parse(".dcss/notacommand")


def test_error_message_does_not_echo_markdown() -> None:
    with pytest.raises(ParseError) as exc:
        parse(".dcss/@everyone**x**")
    assert "@" not in str(exc.value)
    assert "*" not in str(exc.value)


def test_neutral_is_a_recovery_step() -> None:
    parsed = parse(".dcss/neutral")
    assert parsed is not None
    assert parsed.steps[0].kind is Kind.RECOVER


def test_text_accepts_a_plain_name() -> None:
    assert parse(".dcss/text Sigmund the Second").steps[0].text == "Sigmund the Second"  # type: ignore[union-attr]


@pytest.mark.parametrize("payload", ["~x", "hello&", "a" * 40, "<lightred>"])
def test_text_rejects_markup_and_overlong_input(payload: str) -> None:
    with pytest.raises(ParseError):
        parse(f".dcss/text {payload}")


# -- no validity filtering ------------------------------------------------


def test_commands_are_allowed_in_every_context_by_default() -> None:
    for name in ("explore", "quaff", "tab", "north"):
        for context in InputContext:
            assert allowed_in(COMMANDS[name], context, enforce=False)


def test_context_gating_still_works_when_asked_for() -> None:
    assert not allowed_in(COMMANDS["explore"], InputContext.MENU, enforce=True)
    assert allowed_in(COMMANDS["explore"], InputContext.PLAY, enforce=True)


def test_dangerous_keys_pass_by_default() -> None:
    # `S` is a printable character, so it goes through like any other.
    parsed = parse(".dcss/S")
    assert parsed is not None
    assert is_safe_to_send(parsed.steps, InputContext.PLAY)


def test_dangerous_keys_can_be_blocked_during_play() -> None:
    parsed = parse(".dcss/S")
    assert parsed is not None
    assert not is_safe_to_send(parsed.steps, InputContext.PLAY, block_dangerous=True)
    # Inside a menu `S` is just an item slot, so it is never blocked there.
    assert is_safe_to_send(parsed.steps, InputContext.MENU, block_dangerous=True)


@pytest.mark.parametrize("char", sorted(DANGEROUS_PLAY_CHARS - set("\x11\x18\x13")))
def test_every_dangerous_character_is_reachable(char: str) -> None:
    parsed = parse(f".dcss/{char}")
    assert parsed is not None
    assert parsed.steps[0].text == char


# -- help -----------------------------------------------------------------


def test_help_pairs_each_named_command_with_its_key() -> None:
    text = help_text()
    assert "`north` → `k`" in text
    assert "`tab` → `Tab`" in text
    assert "`explore` → `o`" in text
    assert "`quaff` → `q`" in text


def test_help_explains_that_keys_need_no_prefix() -> None:
    assert "Just type the key" in help_text()


def test_help_shows_the_prefix_only_on_bot_commands() -> None:
    text = help_text()
    assert "`.dcss/help`" in text
    assert "`.dcss/north`" not in text


def test_help_honours_a_custom_prefix() -> None:
    assert "`!crawl help`" in help_text("!crawl ")


def test_help_lists_no_key_for_bot_commands() -> None:
    # `.dcss/help` sends no keystroke, so pairing it with one would be a lie.
    assert "`.dcss/help`," in help_text() or "`.dcss/help`" in help_text()
    assert "`.dcss/help` →" not in help_text()


def test_every_named_command_that_sends_keys_declares_one() -> None:
    for command in COMMANDS.values():
        if command.meta:
            continue
        assert command.key, f"{command.name} has no key label for help"


def test_every_direction_is_in_help() -> None:
    text = help_text()
    for name in DIRECTIONS:
        assert f"`{name}` →" in text


def test_help_fits_in_one_discord_message() -> None:
    # Discord hard-caps a message at 2000 characters; help is posted as one.
    assert len(help_text()) < 1900


# -- bare input -----------------------------------------------------------


def test_a_bare_key_needs_no_prefix() -> None:
    assert parse("o").steps[0].text == "o"  # type: ignore[union-attr]
    assert parse("S").steps[0].text == "S"  # type: ignore[union-attr]


def test_a_bare_named_command_needs_no_prefix() -> None:
    assert parse("north").steps[0].text == "k"  # type: ignore[union-attr]
    assert parse("tab").steps[0].keycode == KEY_TAB  # type: ignore[union-attr]
    assert parse("ctrl f").steps[0].keycode == ctrl("f")  # type: ignore[union-attr]


def test_the_prefix_still_works_on_keys() -> None:
    assert parse(".dcss/o").steps[0].text == "o"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "chat",
    [
        "that gnoll looks rough",
        "lol",
        "nice one everyone",
        "gg",
        "",
        "   ",
        "https://example.com/a",
    ],
)
def test_ordinary_chat_is_ignored_without_raising(chat: str) -> None:
    # Bare mode sees every message in the channel, so anything that is not a
    # key has to be dropped silently rather than answered.
    assert parse(chat) is None


def test_bot_commands_still_require_the_prefix() -> None:
    # Otherwise saying "help" in conversation would dump the command list.
    for name in ("help", "link", "status"):
        assert parse(name) is None
        assert parse(f".dcss/{name}") is not None


def test_bare_mode_can_be_turned_off_entirely() -> None:
    assert parse("o", allow_bare=False) is None
    assert parse(".dcss/o", allow_bare=False) is not None


def test_a_prefixed_mistake_still_raises() -> None:
    # Someone who typed the prefix meant to address the bot, so tell them.
    with pytest.raises(ParseError):
        parse(".dcss/nonsense")


def test_a_bare_word_that_is_a_command_with_junk_after_it_is_chat() -> None:
    assert parse("map is confusing") is None
    assert parse("run for your life") is None


# -- runs and modifier combinations ---------------------------------------


@pytest.mark.parametrize(
    ("doubled", "sent"),
    [("hh", "H"), ("jj", "J"), ("kk", "K"), ("ll", "L"),
     ("yy", "Y"), ("uu", "U"), ("bb", "B"), ("nn", "N")],
)
def test_doubling_a_movement_key_runs(doubled: str, sent: str) -> None:
    parsed = parse(doubled)
    assert parsed is not None
    assert [s.text for s in parsed.steps] == [sent]
    assert parsed.command.multi_key


@pytest.mark.parametrize("doubled", ["oo", "ss", "aa", "zz", "ww", "ii", "SS", "55"])
def test_only_movement_keys_double(doubled: str) -> None:
    # `ss` would otherwise become `S`, which saves and exits the game.
    assert parse(doubled) is None


def test_a_doubled_key_takes_no_argument() -> None:
    with pytest.raises(ParseError):
        parse(".dcss/uu north")


def test_shift_on_a_letter_is_its_capital() -> None:
    parsed = parse("shift u")
    assert parsed is not None
    assert [s.text for s in parsed.steps] == ["U"]


def test_shift_and_ctrl_on_named_keys_use_the_cio_codes() -> None:
    assert parse("shift arrowup").steps[0].keycode == CK["SHIFT_UP"]  # type: ignore[union-attr]
    assert parse("shift tab").steps[0].keycode == CK["SHIFT_TAB"]  # type: ignore[union-attr]
    assert parse("ctrl arrowleft").steps[0].keycode == CK["CTRL_LEFT"]  # type: ignore[union-attr]


def test_ctrl_on_a_letter_is_the_control_code() -> None:
    assert parse("ctrl f").steps[0].keycode == ctrl("f")  # type: ignore[union-attr]


@pytest.mark.parametrize("bad", ["shift 5", "shift ,", "ctrl 9", "shift esc", "ctrl ."])
def test_modifiers_refuse_keys_that_have_no_such_combination(bad: str) -> None:
    # Which symbol Shift-5 produces is a keyboard-layout fact, not a crawl one.
    assert parse(bad) is None
    with pytest.raises(ParseError):
        parse(f".dcss/{bad}")


def test_every_combination_form_is_marked_multi_key() -> None:
    for text in ("uu", "shift u", "ctrl f", "run ne"):
        parsed = parse(text)
        assert parsed is not None and parsed.command.multi_key, text


def test_a_plain_key_is_not_multi_key() -> None:
    for text in ("o", "S", "north", "tab"):
        parsed = parse(text)
        assert parsed is not None and not parsed.command.multi_key, text


def test_help_mentions_doubling() -> None:
    assert "Double a movement key to run" in help_text()
