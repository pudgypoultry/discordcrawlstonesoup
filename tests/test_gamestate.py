"""Context tracking — the thing that keeps keys out of the wrong screen."""

from __future__ import annotations

from dcssbot.gamestate import GameState, InputContext, MouseMode, UIState


def make_playing() -> GameState:
    state = GameState()
    state.handle({"msg": "game_started"})
    state.handle({"msg": "input_mode", "mode": MouseMode.COMMAND})
    return state


def test_starts_in_the_lobby() -> None:
    assert GameState().context is InputContext.LOBBY


def test_game_started_reaches_play() -> None:
    assert make_playing().context is InputContext.PLAY


def test_menu_wins_over_command_input_mode() -> None:
    # This is the case that motivates tracking the UI stack at all: crawl keeps
    # reporting COMMAND while a menu is open.
    state = make_playing()
    state.handle({"msg": "ui-push", "type": "describe-item"})
    assert state.input_mode == MouseMode.COMMAND
    assert state.context is InputContext.MENU


def test_ui_pop_returns_to_play() -> None:
    state = make_playing()
    state.handle({"msg": "ui-push", "type": "describe-item"})
    state.handle({"msg": "ui-pop"})
    assert state.context is InputContext.PLAY


def test_ui_stack_replaces_the_depth_wholesale() -> None:
    state = make_playing()
    state.handle({"msg": "ui-push"})
    state.handle({"msg": "ui-push"})
    state.handle({"msg": "ui-stack", "items": [{"msg": "menu"}]})
    assert state.ui_stack_depth == 1
    state.handle({"msg": "ui-stack", "items": []})
    assert state.context is InputContext.PLAY


def test_close_all_menus_clears_the_stack() -> None:
    state = make_playing()
    state.handle({"msg": "ui-push"})
    state.handle({"msg": "ui-push"})
    state.handle({"msg": "close_all_menus"})
    assert state.context is InputContext.PLAY


def test_more_prompt_beats_everything() -> None:
    state = make_playing()
    state.handle({"msg": "ui-push"})
    state.handle({"msg": "msgs", "more": True, "messages": []})
    assert state.context is InputContext.MORE


def test_more_clears_on_the_next_input_mode_change() -> None:
    state = make_playing()
    state.handle({"msg": "msgs", "more": True, "messages": []})
    state.handle({"msg": "input_mode", "mode": MouseMode.COMMAND})
    assert state.context is InputContext.PLAY


def test_yesno_and_targeting_modes() -> None:
    state = make_playing()
    state.handle({"msg": "input_mode", "mode": MouseMode.YESNO})
    assert state.context is InputContext.YESNO
    state.handle({"msg": "input_mode", "mode": MouseMode.TARGET_DIR})
    assert state.context is InputContext.TARGET


def test_text_cursor_in_a_menu_is_text_entry() -> None:
    state = make_playing()
    state.handle({"msg": "ui-push", "type": "name-entry"})
    state.handle({"msg": "text_cursor", "enabled": True})
    assert state.context is InputContext.TEXT_ENTRY


def test_text_cursor_during_play_is_not_text_entry() -> None:
    # The message pane shows a cursor during ordinary play; that is not a form.
    state = make_playing()
    state.handle({"msg": "text_cursor", "enabled": True})
    assert state.context is InputContext.PLAY


def test_view_map_is_its_own_context() -> None:
    state = make_playing()
    state.handle({"msg": "ui_state", "state": UIState.VIEW_MAP})
    assert state.context is InputContext.VIEW_MAP


def test_game_ended_returns_to_lobby_and_clears_ui() -> None:
    state = make_playing()
    state.handle({"msg": "ui-push"})
    state.handle({"msg": "game_ended", "reason": "dead"})
    assert state.context is InputContext.LOBBY
    assert state.ui_stack_depth == 0
    assert state.player == {}


def test_context_age_resets_only_on_a_change() -> None:
    state = make_playing()
    state.handle({"msg": "ui-push"})
    age_at_push = state.context_age
    state.handle({"msg": "player", "hp": 10, "hp_max": 12})
    assert state.context is InputContext.MENU
    assert state.context_age >= age_at_push


def test_status_line_reads_from_player_messages() -> None:
    state = make_playing()
    state.handle(
        {
            "msg": "player",
            "hp": 12,
            "hp_max": 18,
            "mp": 1,
            "mp_max": 4,
            "turn": 340,
            "place": "Dungeon",
            "depth": 3,
        }
    )
    assert state.status_line() == "HP 12/18 | MP 1/4 | Dungeon:3 | T:340"


def test_status_line_is_none_without_player_data() -> None:
    assert GameState().status_line() is None
