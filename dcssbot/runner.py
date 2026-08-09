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

    discord_task = asyncio.create_task(relay.start(config.discord_token), name="discord")
    # Hold the game connection until Discord is up, so the opening lines of the
    # run are not written into a void.
    await relay.wait_ready()
    session_task = asyncio.create_task(session.run(), name="session")

    tasks = [discord_task, session_task]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
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
