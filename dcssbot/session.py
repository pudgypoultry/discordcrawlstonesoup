"""The game side: one long-lived WebTiles session driven by the queue.

Responsibilities, in the order they matter:

1. Stay connected. Servers restart, sockets blip; the loop reconnects with
   backoff and re-enters the game.
2. Keep :class:`~dcssbot.gamestate.GameState` current so the grammar has
   something truthful to gate on.
3. Drain the command queue at a fixed rate, re-checking safety at dispatch
   time rather than trusting the check made when the command was posted.
4. Get unstuck. Random input finds menus and prompts constantly, and a game
   parked in a submenu nobody can name is as dead as a killed character.
5. Start a new character when the old one dies, because permadeath plus
   anarchy input means runs are measured in minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .client import LoginFailed, WebTilesClient, WebTilesError
from .cmdqueue import CommandQueue, QueuedCommand
from .config import Config
from .gamestate import STUCK_CONTEXTS, GameState, InputContext
from .grammar import Kind, Step, is_safe_to_send
from .msglog import LogLine, MessageLog

log = logging.getLogger(__name__)

LineSink = Callable[[list[LogLine]], Awaitable[None]]
EventSink = Callable[["GameEvent"], Awaitable[None]]


@dataclass(frozen=True)
class GameEvent:
    """Something the Discord side may want to announce."""

    kind: str
    detail: str = ""
    url: str | None = None


class GameSession:
    """Owns the connection, the game state and the input path."""

    def __init__(
        self,
        config: Config,
        queue: CommandQueue,
        *,
        on_lines: LineSink,
        on_event: EventSink,
    ) -> None:
        self.config = config
        self.queue = queue
        self.on_lines = on_lines
        self.on_event = on_event

        self.state = GameState()
        self.log = MessageLog()
        self.client: WebTilesClient | None = None
        self.username: str | None = None

        self._stop = asyncio.Event()
        self._game_over = asyncio.Event()
        self._last_send = 0.0
        self._newgame_task: asyncio.Task[None] | None = None

    # -- public API -------------------------------------------------------

    @property
    def in_game(self) -> bool:
        return self.state.in_game

    def spectate_url(self) -> str | None:
        if not self.state.in_game:
            return None
        return self.config.spectate_url(self.username or self.config.username)

    async def stop(self) -> None:
        self._stop.set()
        if self.client is not None:
            await self.client.close()

    async def run(self) -> None:
        """Connect, play, and reconnect until stopped."""
        delay = self.config.reconnect_delay
        first = True
        while not self._stop.is_set():
            try:
                await self._connect_and_play(reconnect=not first)
                delay = self.config.reconnect_delay
            except LoginFailed as exc:
                log.error("login rejected: %s", exc)
                await self.on_event(GameEvent("error", f"login rejected: {exc}"))
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("session ended: %s", exc, exc_info=log.isEnabledFor(logging.DEBUG))
            finally:
                first = False

            if self._stop.is_set():
                return
            log.info("reconnecting in %.0fs", delay)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            delay = min(delay * 2, self.config.reconnect_max_delay)

    # -- connection -------------------------------------------------------

    async def _connect_and_play(self, *, reconnect: bool) -> None:
        client = WebTilesClient(
            self.config.websocket_url, compression=self.config.use_compression
        )
        self.client = client
        self._register_handlers(client)
        if reconnect:
            # Older crawl versions replayed the message buffer on re-attach.
            self.log.note_reconnect()

        # Logged before the connect, not after: a connect that hangs rather
        # than failing fast is otherwise completely silent, and the first
        # question is always "which URL is it actually using".
        log.info(
            "connecting to %s as %s (game %s)",
            self.config.websocket_url,
            self.config.username,
            self.config.game_id,
        )
        await client.start()
        log.info("connected, logging in")
        try:
            self.username = await client.login(self.config.username, self.config.password)
            log.info("logged in as %s", self.username)
            await self._start_game()

            workers = [
                asyncio.create_task(self._drain_queue(), name="queue-drain"),
                asyncio.create_task(self._watchdog(), name="watchdog"),
                asyncio.create_task(client.wait_closed(), name="closed"),
                asyncio.create_task(self._stop.wait(), name="stop"),
            ]
            done, pending = await asyncio.wait(
                workers, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            self._cancel_newgame()
            await client.close()
            self.client = None
            self.state.in_game = False

    async def _start_game(self) -> None:
        assert self.client is not None
        reply = await self.client.play(self.config.game_id)
        kind = reply.get("msg")
        if kind != "game_started":
            raise WebTilesError(f"could not start game: server replied {kind!r}")
        self._game_over.clear()

    def _register_handlers(self, client: WebTilesClient) -> None:
        client.on("*", self._on_message)
        client.on("msgs", self._on_msgs)
        client.on("game_ended", self._on_game_ended)

    # -- incoming ---------------------------------------------------------

    async def _on_message(self, msg: dict[str, Any]) -> None:
        before = self.state.context
        self.state.handle(msg)
        after = self.state.context
        if after is not before:
            log.debug("context %s -> %s", before.value, after.value)
        if msg.get("msg") == "game_started":
            await self._announce_game_start()

    async def _on_msgs(self, msg: dict[str, Any]) -> None:
        lines = self.log.feed(msg)
        if lines:
            await self.on_lines(lines)

    async def _on_game_ended(self, msg: dict[str, Any]) -> None:
        self._game_over.set()
        dropped = self.queue.clear()
        reason = str(msg.get("reason") or "over")
        detail = str(msg.get("message") or "").strip()
        dump = msg.get("dump")
        log.info("game ended (%s); dropped %d queued commands", reason, dropped)
        await self.on_event(
            GameEvent(
                "game_ended",
                detail=detail or f"Game over ({reason}).",
                url=str(dump) if dump else None,
            )
        )
        if self.config.auto_restart:
            self._cancel_newgame()
            self._newgame_task = asyncio.create_task(
                self._restart_after_death(), name="newgame"
            )

    async def _announce_game_start(self) -> None:
        await self.on_event(
            GameEvent("game_started", detail="A new game is running.", url=self.spectate_url())
        )

    # -- outgoing ---------------------------------------------------------

    async def _drain_queue(self) -> None:
        """Dispatch queued commands at the configured rate.

        The wait happens *before* taking the next command, not after. Popping
        first and then sleeping would park a command outside the queue, where
        it is invisible to the queue depth the watchdog reads and where its
        TTL has already been checked against a stale clock.
        """
        while True:
            await self._pace()
            item = await self.queue.get()
            await self._dispatch(item)

    async def _pace(self) -> None:
        wait = self.config.command_interval - (time.monotonic() - self._last_send)
        if wait > 0:
            await asyncio.sleep(wait)

    async def _dispatch(self, item: QueuedCommand) -> None:
        context = self.state.context
        command = item.parsed.command
        if context not in command.contexts:
            # The situation moved on while this waited its turn.
            log.debug("skipping %s: context is now %s", item.name, context.value)
            return
        if not is_safe_to_send(item.parsed.steps, context):
            log.warning("refusing unsafe %s in %s", item.name, context.value)
            return
        for _ in range(item.count):
            await self.send_steps(item.parsed.steps)

    async def send_steps(self, steps: tuple[Step, ...]) -> None:
        """Write one command's keystrokes to the game."""
        client = self.client
        if client is None or not client.connected:
            return
        for step in steps:
            if step.kind is Kind.TEXT:
                await client.send_text(step.text)
            elif step.kind is Kind.KEYCODE:
                await client.send_keycode(step.keycode)
        self._last_send = time.monotonic()

    # -- resilience -------------------------------------------------------

    async def _watchdog(self) -> None:
        """Escape a context the game has been parked in for too long."""
        # Poll often enough that the escape is not much later than the timeout,
        # but not so often that it spins.
        interval = min(2.0, max(0.2, self.config.stuck_timeout / 4))
        last_escape = 0.0
        while True:
            await asyncio.sleep(interval)
            if not self.state.in_game or self.config.stuck_timeout <= 0:
                continue
            if self.state.context not in STUCK_CONTEXTS:
                continue
            if self.state.context_age < self.config.stuck_timeout:
                continue
            if len(self.queue):
                # Someone is still trying to drive it; leave them to it.
                continue
            if time.monotonic() - last_escape < self.config.stuck_timeout:
                # One escape per window: a screen that swallows Escape should
                # not turn the watchdog into a keystroke firehose.
                continue
            last_escape = time.monotonic()
            log.info(
                "stuck in %s for %.0fs, sending Escape",
                self.state.context.value,
                self.state.context_age,
            )
            client = self.client
            if client is not None and client.connected:
                await client.send_escape()
                # Escape alone does not clear a --more--; a space does.
                if self.state.context is InputContext.MORE:
                    await client.send_text(" ")

    async def _restart_after_death(self) -> None:
        """Start a fresh character once the previous run is over.

        Permadeath plus open input means this is load-bearing rather than a
        nicety: without it the bot sits in the lobby until someone notices.
        """
        try:
            await asyncio.sleep(self.config.restart_delay)
            client = self.client
            if client is None or not client.connected or self._stop.is_set():
                return
            log.info("starting a new game")
            await client.play(self.config.game_id)
            await self._answer_newgame_menus()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto-restart failed")

    async def _answer_newgame_menus(self) -> None:
        """Click through character creation.

        Only ever sends while a menu is genuinely open. Resuming an existing
        save shows no creation screens, and firing ``#`` into the dungeon
        because we assumed one was there is exactly the kind of mistake that
        ends a run.
        """
        client = self.client
        if client is None:
            return
        keys = (self.config.newgame_character_key, self.config.newgame_weapon_key)
        deadline = time.monotonic() + 30.0
        sent = 0
        while time.monotonic() < deadline and sent < len(keys):
            await asyncio.sleep(1.0)
            if not client.connected:
                return
            if self.state.context is not InputContext.MENU:
                if self.state.context is InputContext.PLAY and sent:
                    return
                continue
            await client.send_text(keys[sent])
            sent += 1

    def _cancel_newgame(self) -> None:
        if self._newgame_task is not None:
            self._newgame_task.cancel()
            self._newgame_task = None
