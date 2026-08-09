"""Batching, which is what keeps the log relay inside Discord's rate limits."""

from __future__ import annotations

import asyncio

from dcssbot.batcher import LogBatcher
from dcssbot.msglog import LogLine


def lines(*texts: str) -> list[LogLine]:
    return [LogLine(text=t) for t in texts]


class Collector:
    def __init__(self) -> None:
        self.posts: list[str] = []

    async def __call__(self, text: str) -> None:
        self.posts.append(text)


async def test_lines_are_held_until_a_flush() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=10.0, max_lines=100)
    await batcher.add(lines("You see a rat."))
    assert sink.posts == []
    await batcher.flush()
    assert sink.posts == ["```\nYou see a rat.\n```"]


async def test_flush_on_line_count() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=10.0, max_lines=3)
    await batcher.add(lines("a", "b", "c", "d"))
    assert len(sink.posts) == 1
    assert sink.posts[0] == "```\na\nb\nc\n```"
    await batcher.flush()
    assert sink.posts[1] == "```\nd\n```"


async def test_flush_before_exceeding_the_character_cap() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=10.0, max_lines=100, max_chars=40)
    await batcher.add(lines("x" * 30, "y" * 30))
    assert len(sink.posts) == 1
    assert "x" * 30 in sink.posts[0]
    assert "y" not in sink.posts[0]


async def test_posts_stay_under_the_discord_limit() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=10.0, max_lines=500, max_chars=10_000)
    await batcher.add(lines(*[f"line {i} " + "z" * 60 for i in range(200)]))
    await batcher.flush()
    assert sink.posts
    assert all(len(post) <= 2000 for post in sink.posts)


async def test_idle_flush_fires_on_its_own() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=0.05, max_lines=100)
    await batcher.add(lines("quiet"))
    await asyncio.sleep(0.2)
    assert sink.posts == ["```\nquiet\n```"]
    await batcher.close()


async def test_status_line_is_prefixed_outside_the_code_block() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=10.0)
    batcher.set_status("HP 12/18 | D:2")
    await batcher.add(lines("You are hit."))
    await batcher.flush()
    assert sink.posts[0].startswith("`HP 12/18 | D:2`\n```")


async def test_a_fence_in_the_payload_cannot_break_out_of_the_block() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=10.0)
    await batcher.add(lines("you read ``` aloud"))
    await batcher.flush()
    # Exactly two fences: the opening and the closing one.
    assert sink.posts[0].count("```") == 2


async def test_flushing_an_empty_batch_posts_nothing() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=10.0)
    await batcher.flush()
    await batcher.add([])
    await batcher.flush()
    assert sink.posts == []


async def test_close_flushes_what_is_left() -> None:
    sink = Collector()
    batcher = LogBatcher(sink, idle=100.0)
    await batcher.add(lines("last words"))
    await batcher.close()
    assert sink.posts == ["```\nlast words\n```"]
