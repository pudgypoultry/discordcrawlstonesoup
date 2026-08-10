"""The Discord side's decision-making, without a live gateway connection.

``on_message`` is called directly with stand-ins, so this covers what the bot
accepts, ignores and refuses without needing a token.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from dcssbot.cmdqueue import CommandQueue
from dcssbot.config import Config
from dcssbot.discordbot import DiscordRelay
from dcssbot.gamestate import GameState, InputContext, MouseMode

CHANNEL_ID = 4242


class FakeChannel:
    def __init__(self, channel_id: int = CHANNEL_ID) -> None:
        self.id = channel_id
        self.sent: list[str] = []

    async def send(self, content: str, **kwargs: Any) -> Any:
        self.sent.append(content)
        return None


class FakeAuthor:
    def __init__(self, author_id: int = 1, bot: bool = False) -> None:
        self.id = author_id
        self.bot = bot


class FakeMessage:
    def __init__(
        self,
        content: str,
        *,
        channel: FakeChannel | None = None,
        author: FakeAuthor | None = None,
    ) -> None:
        self.content = content
        self.channel = channel or FakeChannel()
        self.author = author or FakeAuthor()


class FakeSession:
    def __init__(self, context: InputContext = InputContext.PLAY) -> None:
        self.state = GameState()
        self.state.handle({"msg": "game_started"})
        self.state.handle({"msg": "input_mode", "mode": MouseMode.COMMAND})
        if context is InputContext.MENU:
            self.state.handle({"msg": "ui-push"})
        self._url: str | None = "https://crawl.example/#watch-testbot"
        self.last_error: str | None = None
        self._why = "connecting to ws://127.0.0.1:8080/socket (2s so far)"

    def spectate_url(self) -> str | None:
        return self._url

    def why_not_running(self) -> str:
        return self.last_error or self._why


def make_relay(**overrides) -> tuple[DiscordRelay, CommandQueue]:
    config = dataclasses.replace(
        Config(
            discord_token="x",
            username="testbot",
            password="pw",
            channel_ids=frozenset({CHANNEL_ID}),
            site_url="https://crawl.example/",
        ),
        **overrides,
    )
    queue = CommandQueue()
    relay = DiscordRelay(config, queue)
    relay.session = FakeSession()
    return relay, queue


async def test_a_valid_command_is_queued() -> None:
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(".dcss/o"))
    assert [item.name for item in queue] == ["o"]


async def test_ordinary_chat_is_ignored() -> None:
    relay, queue = make_relay()
    message = FakeMessage("that gnoll looks rough")
    await relay.on_message(message)
    assert len(queue) == 0
    assert message.channel.sent == []


async def test_messages_from_other_channels_are_ignored() -> None:
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(".dcss/o", channel=FakeChannel(999)))
    assert len(queue) == 0


async def test_bot_messages_are_ignored() -> None:
    # Otherwise the relay's own log posts could feed back into the queue.
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(".dcss/o", author=FakeAuthor(2, bot=True)))
    assert len(queue) == 0


async def test_a_command_wrong_for_the_context_is_dropped_silently() -> None:
    relay, queue = make_relay()
    relay.session.state.handle({"msg": "ui-push"})
    message = FakeMessage(".dcss/o")
    await relay.on_message(message)
    assert len(queue) == 0
    assert message.channel.sent == []


async def test_an_unknown_command_gets_one_reply_then_goes_quiet() -> None:
    # A typo storm in an open channel must not become a reply storm.
    relay, _ = make_relay()
    channel = FakeChannel()
    for _ in range(5):
        await relay.on_message(FakeMessage(".dcss/nonsense", channel=channel))
    assert len(channel.sent) == 1
    assert "unknown command" in channel.sent[0]


async def test_help_is_rate_limited() -> None:
    relay, _ = make_relay()
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/help", channel=channel))
    await relay.on_message(FakeMessage(".dcss/help", channel=channel))
    assert len(channel.sent) == 1
    assert ".dcss/o" in channel.sent[0]


async def test_link_posts_the_spectate_url() -> None:
    relay, _ = make_relay()
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/link", channel=channel))
    assert channel.sent == ["Watch live: https://crawl.example/#watch-testbot"]


async def test_link_says_so_when_no_game_is_running() -> None:
    relay, _ = make_relay()
    relay.session._url = None
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/link", channel=channel))
    assert "No game is running" in channel.sent[0]


async def test_status_reports_context_and_queue() -> None:
    relay, queue = make_relay()
    relay.session.state.handle({"msg": "player", "hp": 9, "hp_max": 18})
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/status", channel=channel))
    assert "HP 9/18" in channel.sent[0]
    assert "play" in channel.sent[0]


async def test_meta_commands_are_never_queued() -> None:
    relay, queue = make_relay()
    channel = FakeChannel()
    for command in (".dcss/help", ".dcss/link", ".dcss/status"):
        await relay.on_message(FakeMessage(command, channel=channel))
    assert len(queue) == 0


@pytest.mark.parametrize("content", [".dcss/save", ".dcss/quit", ".dcss/~", ".dcss/&"])
async def test_dangerous_looking_tokens_never_queue_anything(content: str) -> None:
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(content))
    assert len(queue) == 0


async def test_case_folding_cannot_produce_a_dangerous_key() -> None:
    # Tokens are matched case-insensitively, so `.dcss/S` resolves to the
    # south movement command. It must send `j`, never a literal `S`.
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(".dcss/S"))
    item = queue.get_nowait()
    assert item is not None
    assert item.name == "s"
    assert [step.text for step in item.parsed.steps] == ["j"]


async def test_a_custom_prefix_is_honoured() -> None:
    relay, queue = make_relay(command_prefix="!crawl ")
    await relay.on_message(FakeMessage("!crawl o"))
    assert [item.name for item in queue] == ["o"]
    await relay.on_message(FakeMessage(".dcss/o"))
    assert len(queue) == 1


async def test_a_dropped_command_says_so_when_no_game_is_running() -> None:
    # Silence here is indistinguishable from a broken bot, which is exactly
    # the confusion this notice exists to remove.
    relay, queue = make_relay()
    relay.session.state.handle({"msg": "game_ended", "reason": "quit"})
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/o", channel=channel))
    assert len(queue) == 0
    assert "No game is running" in channel.sent[0]


async def test_the_no_game_notice_is_rate_limited() -> None:
    relay, _ = make_relay()
    relay.session.state.handle({"msg": "game_ended", "reason": "quit"})
    channel = FakeChannel()
    for _ in range(5):
        await relay.on_message(FakeMessage(".dcss/o", channel=channel))
    assert len(channel.sent) == 1


async def test_ordinary_context_churn_stays_silent() -> None:
    # A movement key arriving while a menu is open is normal in an open
    # channel; commenting on it every time would drown the log feed.
    relay, _ = make_relay()
    relay.session.state.handle({"msg": "ui-push"})
    channel = FakeChannel()
    for _ in range(5):
        await relay.on_message(FakeMessage(".dcss/o", channel=channel))
    assert channel.sent == []


async def test_status_surfaces_why_the_game_side_is_down() -> None:
    # "No game running" alone cannot be acted on: a rejected login looks
    # exactly like a game that has not started yet.
    relay, _ = make_relay()
    relay.session.state.handle({"msg": "game_ended", "reason": "quit"})
    relay.session.last_error = "login rejected for user 'testbot'"
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/status", channel=channel))
    assert "login rejected for user 'testbot'" in channel.sent[0]


async def test_status_without_an_error_says_it_is_still_connecting() -> None:
    relay, _ = make_relay()
    relay.session.state.handle({"msg": "game_ended", "reason": "quit"})
    relay.session.last_error = None
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/status", channel=channel))
    # Names the address it is trying, not a bare "still connecting".
    assert "ws://127.0.0.1:8080/socket" in channel.sent[0]
