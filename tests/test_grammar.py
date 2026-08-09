"""The grammar is the security boundary for an open channel."""

from __future__ import annotations

import pytest

from dcssbot.gamestate import InputContext
from dcssbot.grammar import (
    COMMANDS,
    DANGEROUS_PLAY_CHARS,
    Kind,
    ParseError,
    allowed_in,
    is_safe_to_send,
    parse,
)
from dcssbot.keys import KEY_ESCAPE, KEY_TAB


def test_non_command_messages_are_ignored_silently() -> None:
    assert parse("just chatting about the run") is None
    assert parse("") is None


def test_prefix_is_case_insensitive() -> None:
    assert parse(".DCSS/o") is not None


def test_literal_character_command() -> None:
    parsed = parse(".dcss/o")
    assert parsed is not None
    assert [(s.kind, s.text) for s in parsed.steps] == [(Kind.TEXT, "o")]


def test_keycode_command() -> None:
    parsed = parse(".dcss/tab")
    assert parsed is not None
    assert parsed.steps[0].kind is Kind.KEYCODE
    assert parsed.steps[0].keycode == KEY_TAB


def test_escape_is_a_keycode_not_a_character() -> None:
    parsed = parse(".dcss/esc")
    assert parsed is not None
    assert parsed.steps[0].keycode == KEY_ESCAPE


def test_direction_names_map_to_vi_keys() -> None:
    assert parse(".dcss/n").steps[0].text == "k"  # type: ignore[union-attr]
    assert parse(".dcss/se").steps[0].text == "n"  # type: ignore[union-attr]


def test_run_takes_a_direction_and_shifts_it() -> None:
    parsed = parse(".dcss/run ne")
    assert parsed is not None
    assert parsed.steps[0].text == "U"


def test_run_rejects_a_non_direction() -> None:
    with pytest.raises(ParseError):
        parse(".dcss/run away")


def test_unknown_token_raises() -> None:
    with pytest.raises(ParseError, match="unknown command"):
        parse(".dcss/notacommand")


def test_bare_prefix_raises() -> None:
    with pytest.raises(ParseError):
        parse(".dcss/")


def test_argument_on_a_command_that_takes_none_raises() -> None:
    with pytest.raises(ParseError, match="takes no argument"):
        parse(".dcss/o extra")


def test_error_message_does_not_echo_markdown() -> None:
    # The unknown token goes back into a Discord message; it must not carry
    # formatting or a mention through with it.
    with pytest.raises(ParseError) as exc:
        parse(".dcss/@everyone**x**")
    assert "@" not in str(exc.value)
    assert "*" not in str(exc.value)


# -- the dangerous keys ---------------------------------------------------


@pytest.mark.parametrize("char", sorted(DANGEROUS_PLAY_CHARS))
def test_no_command_produces_a_dangerous_character(char: str) -> None:
    for command in COMMANDS.values():
        for step in command.steps:
            assert char not in step.text


def test_there_is_no_generic_send_a_key_command() -> None:
    # An allowlist only holds if nothing in it forwards arbitrary input.
    free_form = {name for name, c in COMMANDS.items() if c.takes_argument}
    assert free_form == {"run", "select", "text"}


@pytest.mark.parametrize("payload", ["~", "&", "Sx", "a b", "<", ";"])
def test_select_refuses_anything_but_one_menu_key(payload: str) -> None:
    with pytest.raises(ParseError):
        parse(f".dcss/select {payload}")


def test_select_accepts_a_menu_letter_and_crawl_menu_symbols() -> None:
    assert parse(".dcss/select b").steps[0].text == "b"  # type: ignore[union-attr]
    assert parse(".dcss/select *").steps[0].text == "*"  # type: ignore[union-attr]


def test_select_allows_capital_s_but_only_inside_a_menu() -> None:
    # `S` is an ordinary inventory slot, so refusing it would break menus. It
    # is dangerous only during play, and two independent checks cover that:
    # the context gate, and the dispatch-time character check.
    parsed = parse(".dcss/select S")
    assert parsed is not None
    assert parsed.steps[0].text == "S"
    assert allowed_in(parsed.command, InputContext.MENU)
    assert not allowed_in(parsed.command, InputContext.PLAY)
    assert not is_safe_to_send(parsed.steps, InputContext.PLAY)


@pytest.mark.parametrize("payload", ["~x", "hello&", "a" * 40, "<lightred>"])
def test_text_rejects_markup_and_overlong_input(payload: str) -> None:
    with pytest.raises(ParseError):
        parse(f".dcss/text {payload}")


def test_text_accepts_a_plain_name() -> None:
    parsed = parse(".dcss/text Sigmund the Second")
    assert parsed is not None
    assert parsed.steps[0].text == "Sigmund the Second"


# -- context gating -------------------------------------------------------


def test_play_command_is_not_allowed_in_a_menu() -> None:
    # The whole point of tracking the UI stack: `o` in an open menu is a
    # selection, not auto-explore.
    assert not allowed_in(COMMANDS["o"], InputContext.MENU)
    assert allowed_in(COMMANDS["o"], InputContext.PLAY)


def test_select_is_not_allowed_during_play() -> None:
    assert not allowed_in(COMMANDS["select"], InputContext.PLAY)
    assert allowed_in(COMMANDS["select"], InputContext.MENU)


def test_text_is_only_allowed_in_a_text_field() -> None:
    assert allowed_in(COMMANDS["text"], InputContext.TEXT_ENTRY)
    for context in (InputContext.PLAY, InputContext.MENU, InputContext.MORE):
        assert not allowed_in(COMMANDS["text"], context)


def test_nothing_is_allowed_in_an_unknown_context() -> None:
    assert not allowed_in(COMMANDS["o"], InputContext.UNKNOWN)


def test_meta_commands_work_anywhere() -> None:
    for context in InputContext:
        assert allowed_in(COMMANDS["help"], context)


def test_dispatch_time_check_blocks_dangerous_text_reaching_play() -> None:
    # `text` is gated to TEXT_ENTRY at parse time, but the context can change
    # while the command waits in the queue, so it is re-checked at dispatch.
    parsed = parse(".dcss/text Save me")
    assert parsed is not None
    assert is_safe_to_send(parsed.steps, InputContext.TEXT_ENTRY)
    assert not is_safe_to_send(parsed.steps, InputContext.PLAY)


def test_dispatch_time_check_passes_ordinary_commands() -> None:
    parsed = parse(".dcss/o")
    assert parsed is not None
    assert is_safe_to_send(parsed.steps, InputContext.PLAY)
