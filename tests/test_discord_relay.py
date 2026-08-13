"""The Discord side's decision-making, without a live gateway connection.

``on_message`` is called directly with stand-ins, so this covers what the bot
accepts, ignores and refuses without needing a token.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest

from dcssbot.cmdqueue import CommandQueue
from dcssbot.config import Config
from dcssbot.discordbot import DiscordRelay, format_game_over
from dcssbot.gamestate import GameState, InputContext, MouseMode
from dcssbot.session import GameEvent

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


async def test_a_single_key_is_queued() -> None:
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(".dcss/o"))
    item = queue.get_nowait()
    assert item is not None
    assert [s.text for s in item.parsed.steps] == ["o"]


async def test_a_named_command_is_queued() -> None:
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(".dcss/explore"))
    assert [item.name for item in queue] == ["explore"]


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


async def test_a_command_wrong_for_the_context_is_still_sent() -> None:
    # Any key, any time. `o` while a menu is open is a menu selection, and
    # that is the channel's problem to sort out — dropping it silently was
    # indistinguishable from the bot being broken.
    relay, queue = make_relay()
    relay.session.state.handle({"msg": "ui-push"})
    message = FakeMessage(".dcss/explore")
    await relay.on_message(message)
    assert [item.name for item in queue] == ["explore"]
    assert message.channel.sent == []


async def test_context_gating_can_be_turned_back_on() -> None:
    relay, queue = make_relay(enforce_context=True)
    relay.session.state.handle({"msg": "ui-push"})
    await relay.on_message(FakeMessage(".dcss/explore"))
    assert len(queue) == 0


async def test_neutral_is_accepted_in_any_context() -> None:
    relay, queue = make_relay()
    relay.session.state.handle({"msg": "ui-push"})
    await relay.on_message(FakeMessage(".dcss/neutral"))
    assert [item.name for item in queue] == ["neutral"]


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
    assert "`explore` → `o`" in channel.sent[0]


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


@pytest.mark.parametrize("content", [".dcss/save", ".dcss/quit", ".dcss/nonsense"])
async def test_unknown_words_queue_nothing(content: str) -> None:
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(content))
    assert len(queue) == 0


@pytest.mark.parametrize("char", ["S", "~", "&", "#"])
async def test_every_printable_character_reaches_the_queue(char: str) -> None:
    # Deliberate: any key at any time. `S` is save-and-exit, and that is the
    # channel's business now, not the bot's.
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(f".dcss/{char}"))
    item = queue.get_nowait()
    assert item is not None
    assert [step.text for step in item.parsed.steps] == [char]


async def test_character_case_survives_the_relay() -> None:
    relay, queue = make_relay()
    await relay.on_message(FakeMessage(".dcss/S"))
    item = queue.get_nowait()
    assert item is not None
    assert [step.text for step in item.parsed.steps] == ["S"]


async def test_a_custom_prefix_is_honoured() -> None:
    relay, queue = make_relay(command_prefix="!crawl ")
    await relay.on_message(FakeMessage("!crawl explore"))
    assert [item.name for item in queue] == ["explore"]
    await relay.on_message(FakeMessage(".dcss/explore"))
    assert len(queue) == 1


async def test_a_dropped_command_says_so_when_no_game_is_running() -> None:
    # Silence here is indistinguishable from a broken bot, which is exactly
    # the confusion this notice exists to remove.
    relay, queue = make_relay()
    relay.session.state.handle({"msg": "game_ended", "reason": "quit"})
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/explore", channel=channel))
    assert len(queue) == 0
    assert "No game is running" in channel.sent[0]


async def test_the_no_game_notice_is_rate_limited() -> None:
    relay, _ = make_relay()
    relay.session.state.handle({"msg": "game_ended", "reason": "quit"})
    channel = FakeChannel()
    for _ in range(5):
        await relay.on_message(FakeMessage(".dcss/explore", channel=channel))
    assert len(channel.sent) == 1


async def test_ordinary_context_churn_stays_silent() -> None:
    # A movement key arriving while a menu is open is normal in an open
    # channel; commenting on it every time would drown the log feed.
    relay, _ = make_relay()
    relay.session.state.handle({"msg": "ui-push"})
    channel = FakeChannel()
    for _ in range(5):
        await relay.on_message(FakeMessage(".dcss/explore", channel=channel))
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


async def test_a_bare_key_is_queued_without_the_prefix() -> None:
    relay, queue = make_relay()
    await relay.on_message(FakeMessage("o"))
    item = queue.get_nowait()
    assert item is not None
    assert [s.text for s in item.parsed.steps] == ["o"]


async def test_bare_chat_is_still_ignored() -> None:
    relay, queue = make_relay()
    message = FakeMessage("that gnoll looks rough")
    await relay.on_message(message)
    assert len(queue) == 0
    assert message.channel.sent == []


async def test_bare_help_does_nothing_but_prefixed_help_works() -> None:
    relay, _ = make_relay()
    channel = FakeChannel()
    await relay.on_message(FakeMessage("help", channel=channel))
    assert channel.sent == []
    await relay.on_message(FakeMessage(".dcss/help", channel=channel))
    assert len(channel.sent) == 1


async def test_requiring_the_prefix_turns_bare_input_off() -> None:
    relay, queue = make_relay(require_prefix=True)
    await relay.on_message(FakeMessage("o"))
    assert len(queue) == 0
    await relay.on_message(FakeMessage(".dcss/o"))
    assert len(queue) == 1


# -- the game-over post -----------------------------------------------------


def test_game_over_post_carries_the_headline_report_and_morgue() -> None:
    post = format_game_over(
        GameEvent(
            "game_ended",
            detail="Bloop the Skirmisher (Minotaur Fighter)\nSlain by a jackal",
            url="https://crawl.example/morgue/bloop.txt",
            reason="dead",
        )
    )
    assert post.startswith("☠️ **The character has died.**")
    # The record keeps its own line breaks, inside a code block so Discord
    # does not read the punctuation in monster names as markup.
    assert "```\nBloop the Skirmisher (Minotaur Fighter)\nSlain by a jackal\n```" in post
    # Angle brackets keep Discord from unfurling a preview of the morgue file.
    assert "Morgue: <https://crawl.example/morgue/bloop.txt>" in post


def test_a_win_is_not_announced_as_a_death() -> None:
    post = format_game_over(GameEvent("game_ended", detail="escaped", reason="won"))
    assert "🏆" in post
    assert "died" not in post


def test_an_unrecognised_exit_reason_still_gets_a_headline() -> None:
    post = format_game_over(GameEvent("game_ended", detail="x", reason="something new"))
    assert post.startswith("**Game over.**")


def test_game_over_post_survives_a_missing_report_and_morgue() -> None:
    # `dump` is only set when the server templates a morgue_url, and a crash
    # can end a game with no record at all.
    assert format_game_over(GameEvent("game_ended", reason="crash")) == (
        "💥 **The game crashed.**"
    )


def test_a_long_report_is_trimmed_to_fit_a_discord_message() -> None:
    post = format_game_over(
        GameEvent("game_ended", detail="x" * 5000, reason="dead")
    )
    assert len(post) < 2000
    assert post.rstrip().endswith("…\n```")


def test_backticks_in_a_report_cannot_break_out_of_the_code_block() -> None:
    post = format_game_over(
        GameEvent("game_ended", detail="killed by ```rm -rf```", reason="dead")
    )
    # Exactly two fences: the ones this function opened and closed.
    assert post.count("```") == 2


async def test_a_post_does_not_wait_forever_for_the_gateway(monkeypatch) -> None:
    # The game announces a new character from its own startup path, so an
    # unbounded wait here would let a token that never connects wedge the game
    # side entirely — with nothing in the log to say why.
    import dcssbot.discordbot as discordbot

    monkeypatch.setattr(discordbot, "READY_TIMEOUT", 0.05)
    relay, _ = make_relay()
    assert not relay._ready.is_set()
    await asyncio.wait_for(relay.post("anyone there?"), timeout=2.0)


async def test_an_announcement_does_not_wait_forever_for_the_gateway(
    monkeypatch,
) -> None:
    import dcssbot.discordbot as discordbot

    monkeypatch.setattr(discordbot, "READY_TIMEOUT", 0.05)
    relay, _ = make_relay()
    assert not relay._ready.is_set()
    await asyncio.wait_for(
        relay.announce(GameEvent("game_ended", reason="dead")), timeout=2.0
    )


async def test_status_explains_that_a_character_is_being_created() -> None:
    # During creation the context is an ordinary MENU, so without this the
    # status is indistinguishable from the game sitting in a menu — and gives
    # no hint that commands are being deliberately held back.
    relay, _ = make_relay()
    relay.session.accepting_input = False
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/status", channel=channel))
    assert "new character" in channel.sent[0]


async def test_status_is_normal_once_input_is_flowing() -> None:
    relay, _ = make_relay()
    relay.session.accepting_input = True
    relay.session.state.handle({"msg": "player", "hp": 9, "hp_max": 18})
    channel = FakeChannel()
    await relay.on_message(FakeMessage(".dcss/status", channel=channel))
    assert "HP 9/18" in channel.sent[0]
