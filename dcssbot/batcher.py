"""Batches log lines into Discord-sized posts.

A single ``.dcss/o`` can emit thirty lines. Discord allows roughly five
messages per five seconds per channel, so posting per line is not an option.
Lines accumulate and flush on whichever comes first: a quiet gap in the log,
a line count, or approaching the 2000-character message cap.

The batcher owns no I/O — it hands finished blocks to a callback — so its
behaviour is testable without a Discord connection.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from .formatting import escape_for_code_block
from .msglog import LogLine

#: Discord's hard limit is 2000; leave room for the fence and a status line.
DISCORD_LIMIT = 2000

Sink = Callable[[str], Awaitable[None]]


class LogBatcher:
    """Accumulates lines and flushes them as fenced code blocks."""

    def __init__(
        self,
        sink: Sink,
        *,
        idle: float = 1.2,
        max_lines: int = 18,
        max_chars: int = 1800,
    ) -> None:
        self.sink = sink
        self.idle = idle
        self.max_lines = max_lines
        self.max_chars = min(max_chars, DISCORD_LIMIT - 100)
        self._lines: list[str] = []
        self._chars = 0
        self._status: str | None = None
        self._last_add = 0.0
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def set_status(self, status: str | None) -> None:
        """Set the status line prefixed to the next flush."""
        self._status = status

    async def add(self, lines: list[LogLine]) -> None:
        """Queue lines, flushing early if the batch is already full."""
        if not lines:
            return
        for line in lines:
            text = escape_for_code_block(line.text)
            if self._chars + len(text) + 1 > self.max_chars and self._lines:
                await self.flush()
            self._lines.append(text)
            self._chars += len(text) + 1
            if len(self._lines) >= self.max_lines:
                await self.flush()
        self._last_add = time.monotonic()
        self._arm()

    def _arm(self) -> None:
        if self._lines and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._idle_flush(), name="log-flush")

    async def _idle_flush(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.idle)
                if not self._lines:
                    return
                if time.monotonic() - self._last_add >= self.idle:
                    await self.flush()
                    return
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise

    async def flush(self) -> None:
        """Post whatever is buffered, if anything."""
        async with self._lock:
            if not self._lines:
                return
            body = "\n".join(self._lines)
            self._lines = []
            self._chars = 0
            block = f"```\n{body}\n```"
            if self._status:
                block = f"`{self._status}`\n{block}"
            await self.sink(block)

    async def close(self) -> None:
        """Flush and stop the idle timer."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass
            self._task = None
        await self.flush()
