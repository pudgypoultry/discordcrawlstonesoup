"""Turns ``msgs`` messages into a stream of plain log lines.

Two fields on a ``msgs`` message adjust what the client already has on screen,
and they do not mean the same thing:

``old_msgs``
    The first N entries of ``messages`` repeat lines the client has already
    displayed — it drops that many and re-adds the whole array. We skip them.

``rollback``
    N previously-sent lines are being *retracted* (erased temporary messages),
    and ``messages`` holds unrelated new content. Discord has no equivalent, so
    the retraction is counted for diagnostics and the new lines are emitted.

Current crawl does not replay the message buffer when a spectator attaches —
``_send_everything`` calls ``webtiles_send_messages``, which only sends what is
still unsent. Older versions were less tidy, so a short overlap check runs on
the first batch after a reconnect and only there; blanket de-duplication would
eat legitimately repeated lines like "You hit the rat."
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from .formatting import clean_line


@dataclass(frozen=True)
class LogLine:
    """One line of crawl output."""

    text: str
    turn: int | None = None
    channel: int | None = None


class MessageLog:
    """Accumulates ``msgs`` payloads into de-duplicated log lines."""

    def __init__(self, history: int = 60, drop_channels: Iterable[int] = ()) -> None:
        self._recent: deque[str] = deque(maxlen=history)
        self._suppress_overlap = False
        self._drop_channels = frozenset(drop_channels)
        self.rolled_back = 0

    def note_reconnect(self) -> None:
        """Arm overlap suppression for the next batch only."""
        self._suppress_overlap = True

    def feed(self, msg: dict[str, Any]) -> list[LogLine]:
        """Extract the genuinely new lines from one ``msgs`` message."""
        entries = msg.get("messages")
        if not isinstance(entries, list):
            return []

        rollback = _as_count(msg.get("rollback"))
        self.rolled_back += rollback

        skip = _as_count(msg.get("old_msgs"))
        candidates = entries[skip:]

        lines: list[LogLine] = []
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            channel = entry.get("channel")
            if isinstance(channel, int) and channel in self._drop_channels:
                continue
            text = clean_line(str(entry.get("text", "")))
            if not text:
                continue
            lines.append(
                LogLine(
                    text=text,
                    turn=entry.get("turn") if isinstance(entry.get("turn"), int) else None,
                    channel=channel if isinstance(channel, int) else None,
                )
            )

        if self._suppress_overlap:
            self._suppress_overlap = False
            lines = self._drop_replayed_prefix(lines)

        for line in lines:
            self._recent.append(line.text)
        return lines

    def _drop_replayed_prefix(self, lines: list[LogLine]) -> list[LogLine]:
        """Drop a leading run that exactly repeats the tail we already have.

        Only used immediately after reconnecting. Finds the longest prefix of
        ``lines`` that matches the end of the recent history and removes it.
        """
        if not lines or not self._recent:
            return lines
        history = list(self._recent)
        longest = min(len(lines), len(history))
        for size in range(longest, 0, -1):
            if history[-size:] == [line.text for line in lines[:size]]:
                return lines[size:]
        return lines


def _as_count(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0
