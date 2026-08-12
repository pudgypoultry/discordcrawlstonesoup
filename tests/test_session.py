"""Session behaviour: dispatch gating, the stuck watchdog, auto-restart."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import AsyncIterator

import pytest

from dcssbot.client import WebTilesClient
from dcssbot.cmdqueue import CommandQueue
from dcssbot.config import Config
from dcssbot.gamestate import InputContext, MouseMode
from dcssbot.grammar import parse
from dcssbot.keys import KEY_ESCAPE
from dcssbot.mockserver import (
    DEFAULT_GAME_ID,
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    MockWebTilesServer,
    Session,
)
from dcssbot.msglog import LogLine
from dcssbot.session import GameEvent, GameSession


class Recorder:
    def __init__(self) -> None:
        self.lines: list[LogLine] = []
        self.events: list[GameEvent] = []

    async def on_lines(self, lines: list[LogLine]) -> None:
        self.lines.extend(lines)

    async def on_event(self, event: GameEvent) -> None:
        self.events.append(event)


def make_config(server: MockWebTilesServer, **overrides) -> Config:
    base = Config(
        websocket_url=server.url,
        site_url="https://crawl.example/",
        username=DEFAULT_USERNAME,
        password=DEFAULT_PASSWORD,
        game_id=DEFAULT_GAME_ID,
        discord_token="x",
        channel_ids=frozenset({1}),
        command_interval=0.0,
        auto_restart=False,
        restart_delay=0.05,
        stuck_timeout=0.2,
        reconnect_delay=0.05,
    )
    return dataclasses.replace(base, **overrides)


@pytest.fixture
async def server() -> AsyncIterator[MockWebTilesServer]:
    mock = MockWebTilesServer(ping_interval=30.0)
    await mock.start()
    try:
        yield mock
    finally:
        await mock.stop()


async def running_session(
    server: MockWebTilesServer, **overrides
) -> tuple[GameSession, CommandQueue, Recorder, asyncio.Task]:
    config = make_config(server, **overrides)
    queue = CommandQueue(max_depth=config.queue_depth, ttl=config.queue_ttl)
    recorder = Recorder()
    session = GameSession(
        config, queue, on_lines=recorder.on_lines, on_event=recorder.on_event
    )
    task = asyncio.create_task(session.run())
    for _ in range(200):
        if session.in_game:
            break
        await asyncio.sleep(0.01)
    assert session.in_game, "session never entered a game"
    return session, queue, recorder, task


async def wait_until(predicate, *, timeout: float = 5.0, what: str = "condition") -> None:
    """Poll until `predicate` holds. Messages are in flight, so nothing that
    depends on the client having *processed* a frame can be asserted directly
    after the server has merely sent it."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


async def shutdown(session: GameSession, task: asyncio.Task) -> None:
    await session.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_session_logs_in_plays_and_relays_the_opening_lines(
    server: MockWebTilesServer,
) -> None:
    session, _, recorder, task = await running_session(server)
    try:
        await asyncio.sleep(0.1)
        texts = [line.text for line in recorder.lines]
        assert "Welcome, testbot the Skirmisher." in texts
        # Colour markup is stripped on the way through.
        assert "Press ? for a list of commands." in texts
        assert any(e.kind == "game_started" for e in recorder.events)
    finally:
        await shutdown(session, task)


async def test_spectate_link_is_built_from_the_logged_in_username(
    server: MockWebTilesServer,
) -> None:
    session, _, _, task = await running_session(server)
    try:
        assert session.spectate_url() == "https://crawl.example/#watch-testbot"
    finally:
        await shutdown(session, task)


async def test_queued_command_reaches_the_game(server: MockWebTilesServer) -> None:
    session, queue, _, task = await running_session(server)
    try:
        queue.put(parse(".dcss/o"), "alice")  # type: ignore[arg-type]
        await asyncio.sleep(0.2)
        assert {"msg": "input", "text": "o"} in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_a_command_still_lands_if_a_menu_opened_while_it_waited(
    server: MockWebTilesServer,
) -> None:
    # By default nothing is gated on context: `o` arriving while a menu is
    # open is sent, and becomes a menu selection.
    session, queue, _, task = await running_session(server, command_interval=0.3)
    try:
        queue.put(parse(".dcss/o"), "alice")  # type: ignore[arg-type]
        session.state.handle({"msg": "ui-push", "type": "describe-item"})
        assert session.state.context is InputContext.MENU
        await asyncio.sleep(0.6)
        assert {"msg": "input", "text": "o"} in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_context_gating_drops_it_when_enforced(
    server: MockWebTilesServer,
) -> None:
    session, queue, _, task = await running_session(
        server, command_interval=0.3, enforce_context=True
    )
    try:
        queue.put(parse(".dcss/explore"), "alice")  # type: ignore[arg-type]
        session.state.handle({"msg": "ui-push", "type": "describe-item"})
        await asyncio.sleep(0.6)
        assert {"msg": "input", "text": "o"} not in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_dangerous_keys_go_through_by_default(
    server: MockWebTilesServer,
) -> None:
    # Any printable key, including save-and-exit.
    session, queue, _, task = await running_session(server)
    try:
        queue.put(parse(".dcss/S"), "alice")  # type: ignore[arg-type]
        await asyncio.sleep(0.4)
        assert {"msg": "input", "text": "S"} in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_dangerous_keys_can_be_blocked_when_asked(
    server: MockWebTilesServer,
) -> None:
    session, queue, _, task = await running_session(server, block_dangerous_keys=True)
    try:
        queue.put(parse(".dcss/S"), "griefer")  # type: ignore[arg-type]
        await asyncio.sleep(0.4)
        assert {"msg": "input", "text": "S"} not in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_neutral_escapes_a_menu(server: MockWebTilesServer) -> None:
    class PoppingServer(MockWebTilesServer):
        """Pops one menu level per Escape, like a real nested menu."""

        async def on_input(self, session: Session, obj: dict) -> None:
            if obj.get("msg") == "key" and obj.get("keycode") == KEY_ESCAPE:
                await self.send_batch(session, [{"msg": "ui-pop"}])

    mock = PoppingServer(ping_interval=30.0)
    await mock.start()
    session = None
    task = None
    try:
        session, queue, _, task = await running_session(mock, neutral_step_delay=0.05)
        session.state.handle({"msg": "ui-push"})
        session.state.handle({"msg": "ui-push"})
        assert session.state.context is InputContext.MENU

        queue.put(parse(".dcss/neutral"), "alice")  # type: ignore[arg-type]
        await wait_until(
            lambda: session.state.context is InputContext.PLAY,
            what="the menus to close",
        )
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_neutral_clears_a_more_prompt_with_a_space(
    server: MockWebTilesServer,
) -> None:
    # Escape does not dismiss a --more--; only an ordinary key does.
    session, _, _, task = await running_session(server, neutral_step_delay=0.05)
    try:
        session.state.handle({"msg": "msgs", "more": True, "messages": []})
        assert session.state.context is InputContext.MORE
        await session.escape_to_neutral(max_steps=2)
        assert {"msg": "input", "text": " "} in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_neutral_gives_up_rather_than_hammering_keys(
    server: MockWebTilesServer,
) -> None:
    # A screen that swallows everything must not turn this into a key firehose.
    session, _, _, task = await running_session(server, neutral_step_delay=0.01)
    try:
        session.state.handle({"msg": "ui-push"})
        assert not await session.escape_to_neutral(max_steps=3)
        escapes = [
            k for k in server.sessions[0].received_keys
            if k.get("msg") == "key" and k.get("keycode") == KEY_ESCAPE
        ]
        assert len(escapes) == 3
    finally:
        await shutdown(session, task)


async def test_watchdog_escapes_a_menu_nobody_is_driving(
    server: MockWebTilesServer,
) -> None:
    session, _, _, task = await running_session(server, stuck_timeout=0.15)
    try:
        session.state.handle({"msg": "ui-push", "type": "describe-item"})
        await asyncio.sleep(0.8)
        assert {"msg": "key", "keycode": KEY_ESCAPE} in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_watchdog_holds_off_while_commands_are_queued(
    server: MockWebTilesServer,
) -> None:
    # Someone is still trying to drive the menu; do not yank them out of it.
    session, queue, _, task = await running_session(
        server, stuck_timeout=0.15, command_interval=60.0
    )
    try:
        session.state.handle({"msg": "ui-push", "type": "describe-item"})
        queue.put(parse(".dcss/b"), "alice")  # type: ignore[arg-type]
        queue.put(parse(".dcss/c"), "bob")  # type: ignore[arg-type]
        await asyncio.sleep(0.6)
        assert {"msg": "key", "keycode": KEY_ESCAPE} not in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_watchdog_leaves_normal_play_alone(server: MockWebTilesServer) -> None:
    session, _, _, task = await running_session(server, stuck_timeout=0.15)
    try:
        await asyncio.sleep(0.6)
        assert session.state.context is InputContext.PLAY
        assert {"msg": "key", "keycode": KEY_ESCAPE} not in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_game_end_announces_the_morgue_and_empties_the_queue(
    server: MockWebTilesServer,
) -> None:
    session, queue, recorder, task = await running_session(
        server, command_interval=60.0
    )
    try:
        queue.put(parse(".dcss/o"), "alice")  # type: ignore[arg-type]
        await server.end_game(server.sessions[0], dump="https://crawl.example/morgue.txt")
        await asyncio.sleep(0.2)
        ended = [e for e in recorder.events if e.kind == "game_ended"]
        assert ended and ended[0].url == "https://crawl.example/morgue.txt"
        # Commands aimed at a dead character are meaningless.
        assert len(queue) == 0
    finally:
        await shutdown(session, task)


async def test_auto_restart_starts_a_new_game(server: MockWebTilesServer) -> None:
    session, _, recorder, task = await running_session(
        server, auto_restart=True, restart_delay=0.05
    )
    try:
        await server.end_game(server.sessions[0])
        await wait_until(
            lambda: any(e.kind == "game_ended" for e in recorder.events),
            what="the game to end",
        )
        await wait_until(
            lambda: len([e for e in recorder.events if e.kind == "game_started"]) == 2,
            what="a second game to start",
        )
        assert session.in_game
    finally:
        await shutdown(session, task)


async def test_character_creation_keys_only_go_to_an_open_menu(
    server: MockWebTilesServer,
) -> None:
    # Resuming an existing save shows no creation screens. Firing '#' into the
    # dungeon because we assumed one was there would be its own disaster.
    class NoMenuServer(MockWebTilesServer):
        async def start_game_messages(self, session: Session) -> None:
            await self.send_batch(
                session, [{"msg": "input_mode", "mode": MouseMode.COMMAND}]
            )

    mock = NoMenuServer(ping_interval=30.0)
    await mock.start()
    session = None
    task = None
    try:
        session, _, _, task = await running_session(mock, auto_restart=True)
        await mock.end_game(mock.sessions[0])
        await asyncio.sleep(0.4)
        sent_text = [
            k.get("text") for k in mock.sessions[0].received_keys if k.get("msg") == "input"
        ]
        assert "#" not in sent_text
        assert "*" not in sent_text
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_reconnects_after_the_server_drops_the_connection(
    server: MockWebTilesServer,
) -> None:
    session, _, recorder, task = await running_session(server, reconnect_delay=0.05)
    try:
        await server.sessions[0].ws.close()
        await wait_until(lambda: not session.in_game, what="the connection to drop")
        await wait_until(
            lambda: len([e for e in recorder.events if e.kind == "game_started"]) >= 2,
            what="the session to reconnect and replay",
        )
        assert session.in_game
    finally:
        await shutdown(session, task)


async def test_a_doubled_key_runs_during_normal_play(
    server: MockWebTilesServer,
) -> None:
    session, queue, _, task = await running_session(server)
    try:
        queue.put(parse("uu"), "alice")  # type: ignore[arg-type]
        await asyncio.sleep(0.4)
        assert {"msg": "input", "text": "U"} in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_a_menu_blocks_doubled_keys_and_modifiers(
    server: MockWebTilesServer,
) -> None:
    # Running has no meaning in a menu and a control key there can do
    # something surprising, so combinations are held back — whatever
    # DCSS_ENFORCE_CONTEXT says.
    session, queue, _, task = await running_session(server)
    try:
        session.state.handle({"msg": "ui-push", "type": "describe-item"})
        for text in ("uu", "shift u", "ctrl f", "run ne"):
            queue.put(parse(text), "alice")  # type: ignore[arg-type]
        await asyncio.sleep(0.6)
        sent = server.sessions[0].received_keys
        assert {"msg": "input", "text": "U"} not in sent
        assert {"msg": "key", "keycode": 6} not in sent
    finally:
        await shutdown(session, task)


async def test_a_menu_still_accepts_single_keys(
    server: MockWebTilesServer,
) -> None:
    # Only combinations are restricted; ordinary keys are untouched.
    session, queue, _, task = await running_session(server)
    try:
        session.state.handle({"msg": "ui-push", "type": "describe-item"})
        queue.put(parse("b"), "alice")  # type: ignore[arg-type]
        await asyncio.sleep(0.4)
        assert {"msg": "input", "text": "b"} in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


async def test_text_entry_blocks_combinations_too(
    server: MockWebTilesServer,
) -> None:
    session, queue, _, task = await running_session(server)
    try:
        session.state.handle({"msg": "text_cursor", "enabled": True})
        queue.put(parse("uu"), "alice")  # type: ignore[arg-type]
        await asyncio.sleep(0.4)
        assert {"msg": "input", "text": "U"} not in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)


class SkillMenuServer(MockWebTilesServer):
    """A mock that answers `m` with a skill screen and drives it like crawl.

    Only the parts the macro touches: opening, the `*` view toggle, Shift to
    train one skill, and `=` plus a key to set a target.
    """

    USEFUL = {
        "1": "  a + Fighting         2.0   7%     0    b + Dodging          2.0   8%   0",
    }
    ALL = {
        "1": "  a + Fighting         2.0   7%     0    c + Axes             0.0        0",
        "2": "  b + Dodging          2.0   8%     0    0 + Evocations       0.0       +1",
    }

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.showing_all = False
        self.trained: str | None = None
        self.target_for: str | None = None
        self.typed = ""

    async def _render(self, session: Session) -> None:
        await self.send_batch(session, [
            {"msg": "menu", "type": "crt", "tag": "skills"},
            {"msg": "txt", "id": "menu_txt",
             "lines": self.ALL if self.showing_all else self.USEFUL},
        ])

    async def on_input(self, session: Session, obj: dict) -> None:
        text = obj.get("text", "")
        if obj.get("msg") == "key" and obj.get("keycode") == KEY_ESCAPE:
            await self.send_batch(session, [{"msg": "close_all_menus"}])
            return
        if text == "m":
            await self._render(session)
        elif text == "*":
            self.showing_all = not self.showing_all
            await self._render(session)
        elif text.isalpha() and text.isupper():
            self.trained = text.lower()
        elif text == "=":
            self.target_for = ""
        elif self.target_for == "" and text:
            self.target_for = text
        elif text.isdigit():
            self.typed += text


async def test_the_train_macro_drives_the_skill_screen(
    server: MockWebTilesServer,
) -> None:
    mock = SkillMenuServer(ping_interval=30.0)
    await mock.start()
    session = task = None
    try:
        session, queue, recorder, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        detail = await session.train_only("fighting")
        assert "Fighting" in detail and "target set to 3" in detail
        assert mock.trained == "a"       # Shift-a
        assert mock.target_for == "a"    # = then a
        assert mock.typed == "3"         # floor(2.0) + 1
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_the_macro_switches_view_for_a_skill_not_listed(
    server: MockWebTilesServer,
) -> None:
    # Axes only appears in the "all" view, so the macro has to press `*`.
    mock = SkillMenuServer(ping_interval=30.0)
    await mock.start()
    session = task = None
    try:
        session, queue, _, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        detail = await session.train_only("axes")
        assert "Axes" in detail and "target set to 1" in detail
        assert mock.showing_all
        assert mock.trained == "c"
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_the_macro_reports_an_unknown_skill(server: MockWebTilesServer) -> None:
    from dcssbot.session import MacroError

    mock = SkillMenuServer(ping_interval=30.0)
    await mock.start()
    session = task = None
    try:
        session, _, _, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        with pytest.raises(MacroError, match="basketweaving"):
            await session.train_only("basketweaving")
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_a_failed_macro_is_announced_rather_than_silent(
    server: MockWebTilesServer,
) -> None:
    mock = SkillMenuServer(ping_interval=30.0)
    await mock.start()
    session = task = None
    try:
        session, queue, recorder, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        queue.put(parse("train basketweaving"), "alice")  # type: ignore[arg-type]
        await wait_until(
            lambda: any(e.kind == "notice" for e in recorder.events),
            what="the failure notice",
        )
        assert "Could not train" in recorder.events[-1].detail
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


class ItemMenuServer(MockWebTilesServer):
    """A mock that answers `q`/`r` with crawl's `use_item` menu."""

    POTIONS = [
        {"text": "Potions", "level": 1},
        {"text": " L - a potion of lignification", "q": 1, "hotkeys": [76], "level": 2},
        {"text": " d - 4 bubbling green potions", "q": 4, "hotkeys": [100], "level": 2},
        {"text": " f - 2 white potions", "q": 2, "hotkeys": [102], "level": 2},
    ]
    SCROLLS = [
        {"text": "Scrolls", "level": 1},
        {"text": " c - 4 scrolls labelled LOUNOCVILOA", "q": 4, "hotkeys": [99], "level": 2},
        {"text": " e - 3 scrolls labelled XYDIOF MEIRA", "q": 3, "hotkeys": [101], "level": 2},
    ]

    def __init__(self, *, potions=True, scrolls=True, **kw) -> None:
        super().__init__(**kw)
        self.has_potions = potions
        self.has_scrolls = scrolls
        self.chosen: str | None = None
        self.menu_open = False

    async def on_input(self, session: Session, obj: dict) -> None:
        text = obj.get("text", "")
        if obj.get("msg") == "key" and obj.get("keycode") == KEY_ESCAPE:
            self.menu_open = False
            await self.send_batch(session, [{"msg": "close_all_menus"}])
            return
        if text == "q" and self.has_potions:
            self.menu_open = True
            await self.send_batch(session, [
                {"msg": "menu", "tag": "use_item", "items": self.POTIONS}])
        elif text == "r" and self.has_scrolls:
            self.menu_open = True
            await self.send_batch(session, [
                {"msg": "menu", "tag": "use_item", "items": self.SCROLLS}])
        elif self.menu_open and text:
            self.chosen = text
            self.menu_open = False
            await self.send_batch(session, [{"msg": "close_all_menus"}])


async def _with_items(**kw):
    mock = ItemMenuServer(ping_interval=30.0, **kw)
    await mock.start()
    return mock


async def test_quaff_by_name_picks_the_right_letter(server: MockWebTilesServer) -> None:
    mock = await _with_items()
    session = task = None
    try:
        session, _, _, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        detail = await session.use_item("quaff", "lignification")
        assert mock.chosen == "L"
        assert "potion of lignification" in detail
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_quaff_unknown_prefers_the_biggest_stack(
    server: MockWebTilesServer,
) -> None:
    mock = await _with_items()
    session = task = None
    try:
        session, _, _, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        detail = await session.use_item("quaff", "unknown")
        assert mock.chosen == "d"          # 4 of them, not the 2 white ones
        assert "unidentified" in detail
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_read_unknown_uses_the_scroll_list(server: MockWebTilesServer) -> None:
    mock = await _with_items()
    session = task = None
    try:
        session, _, _, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        await session.use_item("read", "unknown")
        assert mock.chosen == "c"          # 4 labelled LOUNOCVILOA
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_an_item_not_carried_fails_without_sending_a_key(
    server: MockWebTilesServer,
) -> None:
    from dcssbot.session import MacroError

    mock = await _with_items()
    session = task = None
    try:
        session, _, _, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        with pytest.raises(MacroError, match="curing"):
            await session.use_item("quaff", "curing")
        # Nothing was selected, and the screen was put back.
        assert mock.chosen is None
        await wait_until(
            lambda: session.state.context is InputContext.PLAY,
            what="the menu to close",
        )
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_no_potions_at_all_is_reported(server: MockWebTilesServer) -> None:
    from dcssbot.session import MacroError

    # Crawl declines to open a menu when you carry none, so the macro has to
    # give up on a timeout rather than wait forever.
    mock = await _with_items(potions=False)
    session = task = None
    try:
        session, _, _, task = await running_session(
            mock, macro_step_delay=0.05, neutral_step_delay=0.05
        )
        with pytest.raises(MacroError, match="no potions"):
            await session.use_item("quaff", "unknown")
    finally:
        if session and task:
            await shutdown(session, task)
        await mock.stop()


async def test_bare_quaff_is_still_just_the_key(server: MockWebTilesServer) -> None:
    session, queue, _, task = await running_session(server)
    try:
        queue.put(parse("quaff"), "alice")  # type: ignore[arg-type]
        await asyncio.sleep(0.3)
        assert {"msg": "input", "text": "q"} in server.sessions[0].received_keys
    finally:
        await shutdown(session, task)
