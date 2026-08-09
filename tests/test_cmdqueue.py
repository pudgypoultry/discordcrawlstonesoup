"""Queue behaviour under the burst load an open channel produces."""

from __future__ import annotations

import asyncio

import pytest

from dcssbot.cmdqueue import CommandQueue
from dcssbot.grammar import parse


def cmd(text: str = ".dcss/o"):
    parsed = parse(text)
    assert parsed is not None
    return parsed


def test_fifo_order() -> None:
    queue = CommandQueue()
    queue.put(cmd(".dcss/n"), "alice")
    queue.put(cmd(".dcss/s"), "bob")
    assert queue.get_nowait().name == "n"  # type: ignore[union-attr]
    assert queue.get_nowait().name == "s"  # type: ignore[union-attr]
    assert queue.get_nowait() is None


def test_full_queue_drops_the_newest() -> None:
    # Evicting the oldest would let a burst push out commands that have been
    # waiting, which is worse than refusing the burst.
    queue = CommandQueue(max_depth=2)
    assert queue.put(cmd(".dcss/n"), "alice")
    assert queue.put(cmd(".dcss/s"), "bob")
    assert not queue.put(cmd(".dcss/e"), "carol")
    assert [item.name for item in queue] == ["n", "s"]
    assert queue.stats.dropped_full == 1


def test_expired_commands_are_discarded_on_dequeue() -> None:
    queue = CommandQueue(ttl=0.05)
    queue.put(cmd(".dcss/n"), "alice")
    queue._items[0].queued_at -= 1.0  # pretend it has been waiting
    queue.put(cmd(".dcss/s"), "bob")
    assert queue.get_nowait().name == "s"  # type: ignore[union-attr]
    assert queue.stats.dropped_expired == 1


def test_ttl_of_zero_disables_expiry() -> None:
    queue = CommandQueue(ttl=0)
    queue.put(cmd(".dcss/n"), "alice")
    queue._items[0].queued_at -= 1000.0
    assert queue.get_nowait() is not None


def test_duplicates_are_kept_separate_by_default() -> None:
    # Twelve people posting `.dcss/o` fire twelve auto-explores; that is the
    # Twitch-Plays behaviour and it is the default on purpose.
    queue = CommandQueue()
    for name in ("a", "b", "c"):
        queue.put(cmd(".dcss/o"), name)
    assert len(queue) == 3


def test_duplicates_collapse_when_enabled() -> None:
    queue = CommandQueue(collapse_duplicates=True)
    for name in ("a", "b", "c"):
        queue.put(cmd(".dcss/o"), name)
    assert len(queue) == 1
    assert queue.get_nowait().count == 3  # type: ignore[union-attr]


def test_collapsing_distinguishes_arguments() -> None:
    queue = CommandQueue(collapse_duplicates=True)
    queue.put(cmd(".dcss/run n"), "a")
    queue.put(cmd(".dcss/run s"), "b")
    assert len(queue) == 2


def test_per_user_cooldown_rejects_a_rapid_second_post() -> None:
    queue = CommandQueue(per_user_cooldown=60.0)
    assert queue.put(cmd(".dcss/o"), "alice")
    assert not queue.put(cmd(".dcss/n"), "alice")
    assert queue.put(cmd(".dcss/n"), "bob")
    assert queue.stats.dropped_cooldown == 1


def test_clear_reports_what_it_dropped() -> None:
    queue = CommandQueue()
    queue.put(cmd(".dcss/o"), "alice")
    queue.put(cmd(".dcss/n"), "bob")
    assert queue.clear() == 2
    assert len(queue) == 0


async def test_get_waits_for_a_command() -> None:
    queue = CommandQueue()

    async def put_later() -> None:
        await asyncio.sleep(0.01)
        queue.put(cmd(".dcss/tab"), "alice")

    asyncio.create_task(put_later())
    item = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert item.name == "tab"


async def test_get_skips_expired_and_keeps_waiting() -> None:
    queue = CommandQueue(ttl=0.05)
    queue.put(cmd(".dcss/n"), "alice")
    queue._items[0].queued_at -= 1.0

    async def put_later() -> None:
        await asyncio.sleep(0.01)
        queue.put(cmd(".dcss/tab"), "bob")

    asyncio.create_task(put_later())
    item = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert item.name == "tab"
    assert queue.stats.dropped_expired == 1


async def test_get_blocks_when_empty() -> None:
    queue = CommandQueue()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.05)
