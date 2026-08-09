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


async def test_a_play_command_is_dropped_if_a_menu_opened_while_it_waited(
    server: MockWebTilesServer,
) -> None:
    # The parse-time check said PLAY; by dispatch a menu is open, so `o` would
    # land as a menu selection. It must not be sent.
    session, queue, _, task = await running_session(server, command_interval=0.3)
    try:
        queue.put(parse(".dcss/o"), "alice")  # type: ignore[arg-type]
        session.state.handle({"msg": "ui-push", "type": "describe-item"})
        assert session.state.context is InputContext.MENU
        await asyncio.sleep(0.5)
        assert {"msg": "input", "text": "o"} not in server.sessions[0].received_keys
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
        queue.put(parse(".dcss/select b"), "alice")  # type: ignore[arg-type]
        queue.put(parse(".dcss/select c"), "bob")  # type: ignore[arg-type]
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
