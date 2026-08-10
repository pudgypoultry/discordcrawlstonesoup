"""Wires the game session, the queue and the Discord client together."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .batcher import LogBatcher
from .cmdqueue import CommandQueue
from .config import Config
from .discordbot import DiscordRelay
from .msglog import LogLine
from .session import GameEvent, GameSession

log = logging.getLogger(__name__)


async def run(config: Config) -> None:
    """Run the bot until cancelled."""
    config.validate()
    # The settings come from the environment, which is easy to get wrong and
    # impossible to see afterwards. Say out loud what we resolved to.
    log.info(
        "game: %s (id %s, user %s) | discord: %d channel(s), prefix %r | "
        "interval %.2fs",
        config.websocket_url,
        config.game_id,
        config.username,
        len(config.channel_ids),
        config.command_prefix,
        config.command_interval,
    )

    queue = CommandQueue(
        max_depth=config.queue_depth,
        ttl=config.queue_ttl,
        per_user_cooldown=config.per_user_cooldown,
        collapse_duplicates=config.collapse_duplicates,
    )
    relay = DiscordRelay(config, queue)
    batcher = LogBatcher(
        relay.post,
        idle=config.flush_idle,
        max_lines=config.flush_max_lines,
        max_chars=config.flush_max_chars,
    )

    async def on_lines(lines: list[LogLine]) -> None:
        if config.show_status_line:
            batcher.set_status(session.state.status_line())
        await batcher.add(lines)

    async def on_event(event: GameEvent) -> None:
        # Anything buffered belongs to the run that just ended.
        await batcher.flush()
        await relay.announce(event)

    session = GameSession(config, queue, on_lines=on_lines, on_event=on_event)
    relay.session = session

    # Both sides start straight away. The game side used to wait for Discord to
    # be ready first, so that opening log lines were not written into a void —
    # but `relay.post` already waits on that itself, so the gate bought nothing
    # and made the game connection depend on the gateway coming up. When it did
    # not, the bot sat there with no game and nothing in the log to say why.
    discord_task = asyncio.create_task(relay.start(config.discord_token), name="discord")
    session_task = asyncio.create_task(session.run(), name="session")
    log.info("started the discord and game session tasks")

    tasks = [discord_task, session_task]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            # Whichever side stops first ends the run; say which, because
            # "the bot exited" on its own explains nothing.
            log.warning("the %s task finished, shutting down", task.get_name())
            exc = task.exception()
            if exc is not None:
                raise exc
    finally:
        await session.stop()
        await batcher.close()
        with contextlib.suppress(Exception):
            await relay.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
