"""The Discord side: read commands, post the log feed.

Everything runs in one asyncio process sharing one event loop with the
WebTiles client, with the command queue as the only thing between them. That
gives serialisation and throttling for free and avoids a second process that
would have to be kept in step with game state.

Note that reading messages the bot was not mentioned in needs the privileged
**Message Content** intent, toggled on in the Discord developer portal. Without
it the bot connects happily and simply never sees anything.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import discord

from .cmdqueue import CommandQueue
from .config import Config
from .gamestate import InputContext
from .grammar import ParseError, allowed_in, help_text, parse
from .session import GameEvent

log = logging.getLogger(__name__)

#: How often the bot will answer `.dcss/help`, which is long and gets spammed.
HELP_COOLDOWN = 30.0
#: How often the bot will explain a parse error, so a typo storm stays quiet.
ERROR_COOLDOWN = 10.0
#: How often the bot will say that there is no game to send commands to.
#: Rarely, because it is a standing condition rather than a per-message fault —
#: but never saying it makes a disconnected game side look like a dead bot.
NO_GAME_COOLDOWN = 60.0


class DiscordRelay(discord.Client):
    """Relays chat commands into the queue and game output back out."""

    def __init__(self, config: Config, queue: CommandQueue) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.queue = queue
        #: Set by the runner once the game session exists.
        self.session: Any = None
        self._channels: list[discord.abc.Messageable] = []
        self._ready = asyncio.Event()
        self._last_help = 0.0
        self._last_error = 0.0
        self._last_no_game = 0.0
        self._start_message: discord.Message | None = None

    # -- lifecycle --------------------------------------------------------

    async def on_ready(self) -> None:
        self._channels = []
        for channel_id in self.config.channel_ids:
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except discord.DiscordException:
                    log.error("cannot see channel %s", channel_id)
                    continue
            if isinstance(channel, discord.abc.Messageable):
                self._channels.append(channel)
            else:
                log.error("channel %s is not a text channel", channel_id)
        log.info("connected as %s, relaying in %d channel(s)", self.user, len(self._channels))
        self._ready.set()

    async def wait_ready(self) -> None:
        await self._ready.wait()

    # -- inbound ----------------------------------------------------------

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id == getattr(self.user, "id", None):
            return
        if message.channel.id not in self.config.channel_ids:
            return

        try:
            parsed = parse(
                message.content,
                self.config.command_prefix,
                allow_bare=not self.config.require_prefix,
            )
        except ParseError as exc:
            await self._reply_error(message, str(exc))
            return
        if parsed is None:
            return

        if parsed.command.meta:
            await self._handle_meta(message, parsed.command.name)
            return

        context = self.session.state.context if self.session else None
        if context is not None and not allowed_in(
            parsed.command, context, enforce=self.config.enforce_context
        ):
            log.debug("dropping %s: not allowed in %s", parsed.name, context.value)
            await self._note_no_game(message, context)
            return
        if context is InputContext.LOBBY:
            # Nothing to send keys to. This is not a judgement about the
            # command, so it is reported rather than silently swallowed.
            await self._note_no_game(message, context)
            return

        author = str(message.author.id)
        if not self.queue.put(parsed, author):
            log.debug("queue refused %s from %s", parsed.name, author)

    async def _handle_meta(self, message: discord.Message, name: str) -> None:
        now = time.monotonic()
        if name == "help":
            if now - self._last_help < HELP_COOLDOWN:
                return
            self._last_help = now
            await message.channel.send(help_text(self.config.command_prefix))
        elif name == "link":
            url = self.session.spectate_url() if self.session else None
            await message.channel.send(
                f"Watch live: {url}" if url else "No game is running right now."
            )
        elif name == "status":
            await message.channel.send(self._status_text())

    def _status_text(self) -> str:
        if self.session is None:
            return "Not connected yet."
        state = self.session.state
        if not state.in_game:
            return f"No game running — {self.session.why_not_running()}"
        status = state.status_line() or "no player data yet"
        return f"`{status}` — waiting on: {state.context.value}, {self.queue.summary()}"

    async def _note_no_game(self, message: discord.Message, context: InputContext) -> None:
        """Say once in a while that there is no game to send commands to.

        Commands dropped for ordinary context churn — a movement key while a
        menu is open — stay silent, because in an open channel that would be
        constant. But a game side that never connected drops *everything*, and
        silence there is indistinguishable from a broken bot.
        """
        if context not in (InputContext.LOBBY, InputContext.UNKNOWN):
            return
        now = time.monotonic()
        if now - self._last_no_game < NO_GAME_COOLDOWN:
            return
        self._last_no_game = now
        try:
            await message.channel.send(
                "No game is running, so commands are being ignored. "
                f"`{self.config.command_prefix}status` has the details."
            )
        except discord.DiscordException:
            log.debug("could not post the no-game notice", exc_info=True)

    async def _reply_error(self, message: discord.Message, text: str) -> None:
        now = time.monotonic()
        if now - self._last_error < ERROR_COOLDOWN:
            return
        self._last_error = now
        with_help = f"{text}. Try `{self.config.command_prefix}help`."
        try:
            await message.channel.send(with_help, delete_after=20)
        except discord.DiscordException:
            log.debug("could not post parse error", exc_info=True)

    # -- outbound ---------------------------------------------------------

    async def post(self, text: str) -> None:
        """Post to every relay channel, tolerating per-channel failures."""
        await self._ready.wait()
        for channel in self._channels:
            try:
                await channel.send(text)
            except discord.DiscordException:
                log.warning("failed to post to %s", getattr(channel, "id", "?"), exc_info=True)

    async def announce(self, event: GameEvent) -> None:
        """Post a lifecycle event."""
        await self._ready.wait()
        if event.kind == "game_started":
            text = f"**New game started.** Watch live: {event.url}" if event.url else "**New game started.**"
            self._start_message = None
            for channel in self._channels:
                try:
                    sent = await channel.send(text)
                    if self._start_message is None:
                        self._start_message = sent
                except discord.DiscordException:
                    log.warning("failed to announce game start", exc_info=True)
            return

        if event.kind == "game_ended":
            parts = ["**Game over.**"]
            if event.detail:
                parts.append(event.detail)
            if event.url:
                parts.append(f"Morgue: <{event.url}>")
            await self.post(" ".join(parts))
            await self._retire_start_message()
            return

        if event.kind == "notice":
            await self.post(event.detail)
            return

        if event.kind == "error":
            await self.post(f"⚠️ {event.detail}")

    async def _retire_start_message(self) -> None:
        """Edit the "watch live" post so its dead link stops inviting clicks."""
        message = self._start_message
        self._start_message = None
        if message is None:
            return
        try:
            await message.edit(content="**Game over** — this run has finished.")
        except discord.DiscordException:
            log.debug("could not edit the start message", exc_info=True)
