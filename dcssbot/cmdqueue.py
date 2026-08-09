"""The command queue between Discord and the game.

Open posting means the queue, not the parser, decides whether the game stays
playable. Two properties matter more than throughput:

*Bounded depth.* Twenty people posting in two seconds while we drain at one
command per second puts the game twenty seconds behind. The deque is capped
and drops the *newest* command when full, so a burst cannot push out the
commands already waiting their turn.

*Bounded age.* A movement command that fires fifteen seconds late is not
chaos, it is noise — whatever it was reacting to is long gone. Anything older
than the TTL is discarded as it comes off the queue.

Duplicate collapsing is off by default: twelve people posting ``.dcss/o``
fire twelve auto-explores, which is the Twitch-Plays behaviour. Turning it on
collapses identical commands that arrive within the same drain window.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterator

from .grammar import ParsedCommand


@dataclass
class QueuedCommand:
    """A parsed command with its provenance."""

    parsed: ParsedCommand
    author: str
    queued_at: float = field(default_factory=time.monotonic)
    #: How many identical commands were folded into this one.
    count: int = 1

    @property
    def age(self) -> float:
        return time.monotonic() - self.queued_at

    @property
    def name(self) -> str:
        return self.parsed.name


@dataclass
class QueueStats:
    """Counters for the ``status`` command and for logging."""

    accepted: int = 0
    dropped_full: int = 0
    dropped_expired: int = 0
    dropped_cooldown: int = 0
    collapsed: int = 0


class CommandQueue:
    """A bounded, ageing FIFO with an optional per-user cooldown."""

    def __init__(
        self,
        *,
        max_depth: int = 25,
        ttl: float = 12.0,
        per_user_cooldown: float = 0.0,
        collapse_duplicates: bool = False,
    ) -> None:
        self.max_depth = max_depth
        self.ttl = ttl
        self.per_user_cooldown = per_user_cooldown
        self.collapse_duplicates = collapse_duplicates
        self._items: deque[QueuedCommand] = deque()
        self._last_post: dict[str, float] = {}
        self._wakeup = asyncio.Event()
        self.stats = QueueStats()

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[QueuedCommand]:
        return iter(self._items)

    def put(self, parsed: ParsedCommand, author: str) -> bool:
        """Offer a command. Returns whether it was accepted."""
        now = time.monotonic()

        if self.per_user_cooldown > 0:
            last = self._last_post.get(author)
            if last is not None and now - last < self.per_user_cooldown:
                self.stats.dropped_cooldown += 1
                return False

        if self.collapse_duplicates:
            for item in self._items:
                if item.name == parsed.name and item.parsed.argument == parsed.argument:
                    item.count += 1
                    self.stats.collapsed += 1
                    self._last_post[author] = now
                    self._wakeup.set()
                    return True

        if len(self._items) >= self.max_depth:
            # Drop the newest rather than evicting a command already waiting.
            self.stats.dropped_full += 1
            return False

        self._items.append(QueuedCommand(parsed=parsed, author=author))
        self._last_post[author] = now
        self.stats.accepted += 1
        self._wakeup.set()
        return True

    def get_nowait(self) -> QueuedCommand | None:
        """Pop the oldest command that has not expired."""
        while self._items:
            item = self._items.popleft()
            if self.ttl > 0 and item.age > self.ttl:
                self.stats.dropped_expired += 1
                continue
            return item
        self._wakeup.clear()
        return None

    async def get(self) -> QueuedCommand:
        """Wait for and pop the next live command."""
        while True:
            item = self.get_nowait()
            if item is not None:
                return item
            self._wakeup.clear()
            await self._wakeup.wait()

    def clear(self) -> int:
        """Drop everything queued, e.g. when the game ends. Returns the count."""
        dropped = len(self._items)
        self._items.clear()
        self._wakeup.clear()
        return dropped

    def summary(self) -> str:
        s = self.stats
        return (
            f"queued={len(self._items)} accepted={s.accepted} "
            f"expired={s.dropped_expired} full={s.dropped_full} "
            f"cooldown={s.dropped_cooldown} collapsed={s.collapsed}"
        )
