"""The game side: one long-lived WebTiles session driven by the queue.

Responsibilities, in the order they matter:

1. Stay connected. Servers restart, sockets blip; the loop reconnects with
   backoff and re-enters the game.
2. Keep :class:`~dcssbot.gamestate.GameState` current so the grammar has
   something truthful to gate on.
3. Drain the command queue at a fixed rate, re-checking safety at dispatch
   time rather than trusting the check made when the command was posted.
4. Get unstuck. Random input finds menus and prompts constantly, and a game
   parked in a submenu nobody can name is as dead as a killed character.
5. Start a new character when the old one dies, because permadeath plus
   anarchy input means runs are measured in minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .client import LoginFailed, WebTilesClient, WebTilesError
from .cmdqueue import CommandQueue, QueuedCommand
from .config import Config
from .gamestate import STUCK_CONTEXTS, GameState, InputContext
from .grammar import Kind, Step, allowed_in, is_safe_to_send
from .keys import KEY_ENTER
from .items import ItemMenuError, ItemRow, find_item, parse_item_menu, pick_unknown
from .skills import SkillMenuError, SkillRow, find_skill, parse_skill_menu
from .formatting import death_report
from .msglog import LogLine, MessageLog

log = logging.getLogger(__name__)

#: ``push_ui_layout`` types from ``newgame.cc``. The species/background screen,
#: the weapon screen and the map screen all push ``newgame-choice``, so the
#: layout name alone does not say which one is up; the reroll confirmation gets
#: a layout of its own.
NEWGAME_CHOICE = "newgame-choice"
NEWGAME_CONFIRM = "newgame-random-combo"
NEWGAME_LAYOUTS: frozenset[str] = frozenset({NEWGAME_CHOICE, NEWGAME_CONFIRM})

#: The "press any key" summary crawl shows over a finished character
#: (`end_game` in ``end.cc``). The game process does not exit — and so
#: ``game_ended`` does not arrive — until it is dismissed.
GAME_OVER_LAYOUT = "game-over"

LineSink = Callable[[list[LogLine]], Awaitable[None]]
EventSink = Callable[["GameEvent"], Awaitable[None]]


class MacroError(Exception):
    """A multi-step macro could not be carried out."""


@dataclass(frozen=True)
class GameEvent:
    """Something the Discord side may want to announce."""

    kind: str
    detail: str = ""
    url: str | None = None
    #: For ``game_ended``, crawl's own exit type — "dead", "won", "quit",
    #: "bailed out", "save", "abort", "crash" (`_exit_type_to_string`).
    reason: str = ""


class GameSession:
    """Owns the connection, the game state and the input path."""

    def __init__(
        self,
        config: Config,
        queue: CommandQueue,
        *,
        on_lines: LineSink,
        on_event: EventSink,
    ) -> None:
        self.config = config
        self.queue = queue
        self.on_lines = on_lines
        self.on_event = on_event

        self.state = GameState()
        self.log = MessageLog()
        self.client: WebTilesClient | None = None
        self.username: str | None = None
        #: Why the game side is not up, if it is not. Surfaced by `.dcss/status`
        #: — without it a rejected login is indistinguishable from a game that
        #: simply has not started yet, and the bot looks broken rather than
        #: misconfigured.
        self.last_error: str | None = None
        #: What the session is currently trying to reach, and since when. A
        #: connect that hangs instead of failing fast reports no error at all,
        #: so without this the only thing status could say was "still
        #: connecting" — true, useless, and indistinguishable from a firewall
        #: quietly dropping the packets.
        self.connect_target: str | None = None
        self.connect_started: float | None = None

        self._stop = asyncio.Event()
        self._game_over = asyncio.Event()
        self._last_send = 0.0
        self._newgame_task: asyncio.Task[None] | None = None
        #: Whether chat's keystrokes are allowed through to the game. Held shut
        #: from the moment a character dies until the next one is standing in
        #: the dungeon, because the creation screens are not a menu you can
        #: blunder through: `_prompt_choice` and `_reroll_random` both take
        #: Escape as `game_ended(game_exit::abort)`, so one stray queued key
        #: would end the new run before it began.
        self._accepting_input = asyncio.Event()

    # -- public API -------------------------------------------------------

    @property
    def in_game(self) -> bool:
        return self.state.in_game

    @property
    def accepting_input(self) -> bool:
        """Whether chat's keys are currently reaching the game.

        False while a character is being created — which is a game being in
        progress but not yet playable, a state `in_game` alone cannot express.
        """
        return self._accepting_input.is_set()

    def spectate_url(self) -> str | None:
        if not self.state.in_game:
            return None
        return self.config.spectate_url(self.username or self.config.username)

    def why_not_running(self) -> str:
        """One line explaining why there is no game, for `.dcss/status`."""
        if self.last_error:
            return self.last_error
        if self.connect_started is not None:
            waited = time.monotonic() - self.connect_started
            where = self.connect_target or self.config.websocket_url
            if waited > 25:
                return (
                    f"still trying to reach {where} after {waited:.0f}s — that is "
                    "far longer than a connection should take. Check DCSS_WS_URL, "
                    "that the crawl server is listening on that address, and that "
                    "nothing is silently dropping the connection."
                )
            return f"connecting to {where} ({waited:.0f}s so far)"
        return "the game side has not started connecting yet"

    async def stop(self) -> None:
        self._stop.set()
        if self.client is not None:
            await self.client.close()

    async def run(self) -> None:
        """Connect, play, and reconnect until stopped."""
        log.info("game session starting")
        delay = self.config.reconnect_delay
        first = True
        while not self._stop.is_set():
            try:
                await self._connect_and_play(reconnect=not first)
                delay = self.config.reconnect_delay
            except LoginFailed as exc:
                # Retrying rejected credentials is pointless and, on a public
                # server, a good way to get an account locked. Stop, but leave
                # the reason where `.dcss/status` can find it.
                self.last_error = (
                    f"login rejected for user {self.config.username!r}: {exc}. "
                    "Check DCSS_USERNAME/DCSS_PASSWORD, and that the account "
                    "exists on this server."
                )
                log.error("%s", self.last_error)
                await self.on_event(GameEvent("error", self.last_error))
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("session ended: %s", exc, exc_info=log.isEnabledFor(logging.DEBUG))
            finally:
                first = False

            if self._stop.is_set():
                return
            log.info("reconnecting in %.0fs", delay)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            delay = min(delay * 2, self.config.reconnect_max_delay)

    # -- connection -------------------------------------------------------

    async def _connect_and_play(self, *, reconnect: bool) -> None:
        client = WebTilesClient(
            self.config.websocket_url, compression=self.config.use_compression
        )
        self.client = client
        self._register_handlers(client)
        if reconnect:
            # Older crawl versions replayed the message buffer on re-attach.
            self.log.note_reconnect()

        # Logged before the connect, not after: a connect that hangs rather
        # than failing fast is otherwise completely silent, and the first
        # question is always "which URL is it actually using".
        log.info(
            "connecting to %s as %s (game %s)",
            self.config.websocket_url,
            self.config.username,
            self.config.game_id,
        )
        self.connect_target = self.config.websocket_url
        self.connect_started = time.monotonic()
        await client.start()
        log.info("connected, logging in")
        try:
            self.username = await client.login(self.config.username, self.config.password)
            log.info("logged in as %s", self.username)
            await self._start_game()

            workers = [
                asyncio.create_task(self._drain_queue(), name="queue-drain"),
                asyncio.create_task(self._watchdog(), name="watchdog"),
                asyncio.create_task(client.wait_closed(), name="closed"),
                asyncio.create_task(self._stop.wait(), name="stop"),
            ]
            done, pending = await asyncio.wait(
                workers, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            self._cancel_newgame()
            await client.close()
            self.client = None
            self.state.in_game = False

    async def _start_game(self) -> None:
        assert self.client is not None
        # Shut before `play`, not after: if there is no save the creation
        # screens are already on their way back, and anything still queued from
        # a previous connection would land on them.
        self._accepting_input.clear()
        reply = await self.client.play(self.config.game_id)
        kind = reply.get("msg")
        if kind != "game_started":
            raise WebTilesError(
                f"could not start game {self.config.game_id!r}: server replied "
                f"{kind!r}. Check DCSS_GAME_ID — `probe.py --games` lists the "
                "ids this server offers."
            )
        self.last_error = None
        self.connect_started = None
        self._game_over.clear()
        await self._enter_dungeon()

    async def _enter_dungeon(self) -> None:
        """Answer character creation, announce who turned up, reopen the gate.

        Resuming an existing save shows no creation screens at all, in which
        case this returns almost immediately — but the gate still has to be
        reopened, hence the ``finally``. A creation that times out reopens it
        too: a bot nobody can type at is worse than one parked on a screen
        somebody in chat can work out how to leave.
        """
        try:
            await self._create_character()
        finally:
            self._accepting_input.set()
        await self._announce_game_start()

    def _register_handlers(self, client: WebTilesClient) -> None:
        client.on("*", self._on_message)
        client.on("msgs", self._on_msgs)
        client.on("game_ended", self._on_game_ended)

    # -- incoming ---------------------------------------------------------

    async def _on_message(self, msg: dict[str, Any]) -> None:
        before = self.state.context
        self.state.handle(msg)
        after = self.state.context
        if after is not before:
            log.debug("context %s -> %s", before.value, after.value)

    async def _on_msgs(self, msg: dict[str, Any]) -> None:
        lines = self.log.feed(msg)
        if lines:
            await self.on_lines(lines)

    async def _on_game_ended(self, msg: dict[str, Any]) -> None:
        self._game_over.set()
        # Close the gate here rather than in the restart task, so there is no
        # window between the death and the restart in which queued keys are
        # still being dispatched.
        self._accepting_input.clear()
        dropped = self.queue.clear()
        reason = str(msg.get("reason") or "").strip()
        # `message` is the several-line death record crawl builds for the
        # game-over screen — who the character was, what killed it, where, and
        # how long it lasted. That is the morgue summary; `dump` is the URL of
        # the full morgue file, and only exists if the server templates one.
        report = death_report(str(msg.get("message") or ""))
        dump = _morgue_url(msg.get("dump"))
        log.info("game ended (%s); dropped %d queued commands", reason or "?", dropped)
        await self.on_event(
            GameEvent("game_ended", detail=report, url=dump, reason=reason)
        )
        if self.config.auto_restart:
            self._cancel_newgame()
            self._newgame_task = asyncio.create_task(
                self._restart_after_death(), name="newgame"
            )

    async def _announce_game_start(self) -> None:
        who = self.state.character_description()
        await self.on_event(
            GameEvent(
                "game_started",
                detail=f"Now playing **{who}**." if who else "",
                url=self.spectate_url(),
            )
        )

    # -- outgoing ---------------------------------------------------------

    async def _drain_queue(self) -> None:
        """Dispatch queued commands at the configured rate.

        The wait happens *before* taking the next command, not after. Popping
        first and then sleeping would park a command outside the queue, where
        it is invisible to the queue depth the watchdog reads and where its
        TTL has already been checked against a stale clock.
        """
        while True:
            await self._accepting_input.wait()
            await self._pace()
            item = await self.queue.get()
            if not self._accepting_input.is_set():
                # A character can die while we are blocked waiting for a
                # command, which would otherwise let exactly one keystroke
                # through into the new game's creation screens.
                continue
            await self._dispatch(item)

    async def _pace(self) -> None:
        wait = self.config.command_interval - (time.monotonic() - self._last_send)
        if wait > 0:
            await asyncio.sleep(wait)

    async def _dispatch(self, item: QueuedCommand) -> None:
        context = self.state.context
        command = item.parsed.command
        # A macro drives menus itself, so it has to start from normal play
        # for the same reason a run does. Bare `quaff` is just the key `q` and
        # stays unrestricted.
        is_macro = any(s.kind is Kind.MACRO for s in item.parsed.steps)
        if (command.multi_key or is_macro) and context is not InputContext.PLAY:
            # Runs and modifier combinations only mean anything during ordinary
            # play. Inside a menu a run is nonsense and a control key can do
            # something surprising, so these are held back there whatever
            # DCSS_ENFORCE_CONTEXT says. Single keys are unaffected.
            log.debug("skipping %s: %s is not normal play", item.name, context.value)
            return
        if not allowed_in(command, context, enforce=self.config.enforce_context):
            # Only reachable with DCSS_ENFORCE_CONTEXT on.
            log.debug("skipping %s: context is now %s", item.name, context.value)
            return
        if not is_safe_to_send(
            item.parsed.steps,
            context,
            block_dangerous=self.config.block_dangerous_keys,
        ):
            log.warning("refusing unsafe %s in %s", item.name, context.value)
            return
        for _ in range(item.count):
            await self.send_steps(item.parsed.steps)

    async def send_steps(self, steps: tuple[Step, ...]) -> None:
        """Write one command's keystrokes to the game."""
        client = self.client
        if client is None or not client.connected:
            return
        for step in steps:
            if step.kind is Kind.TEXT:
                await client.send_text(step.text)
            elif step.kind is Kind.KEYCODE:
                await client.send_keycode(step.keycode)
            elif step.kind is Kind.RECOVER:
                await self.escape_to_neutral()
            elif step.kind is Kind.MACRO:
                await self._run_macro(step.text)
        self._last_send = time.monotonic()

    async def _run_macro(self, payload: str) -> None:
        """Run a macro and report the outcome, success or failure.

        Macros take several seconds and a handful of keystrokes, so silence
        about one that did not work would be indistinguishable from a bot that
        has wandered off.
        """
        verb, _, argument = payload.partition(":")
        try:
            if verb == "train":
                detail = await self.train_only(argument)
            elif verb in ("quaff", "read"):
                detail = await self.use_item(verb, argument)
            else:  # pragma: no cover - the grammar cannot produce this
                raise MacroError(f"unknown macro {verb!r}")
        except MacroError as exc:
            log.info("%s macro failed: %s", verb, exc)
            await self.on_event(GameEvent("notice", f"Could not {verb}: {exc}"))
        except Exception:
            log.exception("%s macro blew up", verb)
            await self.on_event(GameEvent("notice", f"The {verb} macro failed."))
        else:
            log.info("%s", detail)
            await self.on_event(GameEvent("notice", detail))

    async def train_only(self, query: str) -> str:
        """Train one skill exclusively and set its target to the next level.

        Drives the skill screen the way a player would: ``m`` to open it,
        Shift plus the skill's key to make it the only thing being trained,
        then ``=``, the key again, the number and Enter.

        The keys are read off the menu rather than hardcoded, because crawl
        assigns them by position — Unarmed Combat is ``b`` in the "useful"
        view and ``f`` in the "all" view, and both shift as a character gains
        skills. The current level comes from the same place, since nothing
        else in the protocol reports it.
        """
        client = self.client
        if client is None or not client.connected:
            raise MacroError("not connected to a game")

        step = self.config.macro_step_delay
        await self._open_skill_menu(step)
        try:
            row = await self._find_skill_row(query, step)
            target = row.next_target

            # Shift plus the key trains this skill and nothing else.
            await client.send_text(row.key.upper())
            await asyncio.sleep(step)

            # The menu re-renders, and the keys can move with it.
            row = await self._find_skill_row(query, step, reopen=False)

            await client.send_text("=")
            await asyncio.sleep(step)
            await client.send_text(row.key)
            await asyncio.sleep(step)
            for digit in str(target):
                await client.send_text(digit)
                await asyncio.sleep(0.15)
            await client.send_keycode(KEY_ENTER)
            await asyncio.sleep(step)
        finally:
            await self.escape_to_neutral()
        self._last_send = time.monotonic()
        return (
            f"Training **{row.name}** only (was {row.level:g}), "
            f"target set to {target}."
        )

    async def use_item(self, verb: str, query: str) -> str:
        """Quaff or read one item, chosen by name or by being unidentified.

        Two keys, not three: `q` opens crawl's own potion list and pressing an
        item's letter drinks it there and then — verified against 0.34.1. The
        letter is read off that list rather than assumed, since inventory
        letters move as items are picked up and used.

        A miss is a complete failure by design: nothing is sent to the game
        beyond opening the menu, and the screen is put back to normal play.
        """
        client = self.client
        if client is None or not client.connected:
            raise MacroError("not connected to a game")

        open_key = "q" if verb == "quaff" else "r"
        noun = "potion" if verb == "quaff" else "scroll"
        step = self.config.macro_step_delay

        if self.state.context is not InputContext.PLAY:
            await self.escape_to_neutral()
        self.state.clear_menu()
        await client.send_text(open_key)

        rows = await self._await_item_rows(step)
        if rows is None:
            # Crawl declines to open the menu at all when you have none, and
            # says so in the log; there is nothing on screen to back out of.
            await self.escape_to_neutral()
            raise MacroError(f"no {noun}s in the inventory")

        try:
            if query.strip().lower() == "unknown":
                row = pick_unknown(rows)
            else:
                row = find_item(rows, query)
        except ItemMenuError as exc:
            await self.escape_to_neutral()
            raise MacroError(str(exc)) from exc

        await client.send_text(row.key)
        await asyncio.sleep(step)
        self._last_send = time.monotonic()
        which = "unidentified " if not row.identified else ""
        return f"{verb.capitalize()}ing the {which}**{row.name}** ({row.key})."

    async def _await_item_rows(self, step: float) -> list[ItemRow] | None:
        """Wait for the item menu, or None if crawl never opened one."""
        deadline = time.monotonic() + max(4.0, step * 8)
        while time.monotonic() < deadline:
            rows = parse_item_menu(self.state.menu_items)
            if rows:
                return rows
            await asyncio.sleep(0.1)
        return None

    async def _open_skill_menu(self, step: float) -> None:
        client = self.client
        assert client is not None
        # Start from normal play: `m` typed into another menu is a selection.
        if self.state.context is not InputContext.PLAY:
            await self.escape_to_neutral()
        self.state.clear_menu()
        await client.send_text("m")
        if not await self._await_skill_rows(step):
            raise MacroError("the skill screen did not open")

    async def _await_skill_rows(self, step: float) -> bool:
        """Wait for the menu to render at least one skill row."""
        deadline = time.monotonic() + max(4.0, step * 8)
        while time.monotonic() < deadline:
            if parse_skill_menu(self.state.menu_lines):
                return True
            await asyncio.sleep(0.1)
        return False

    async def _find_skill_row(
        self, query: str, step: float, *, reopen: bool = True
    ) -> SkillRow:
        """Locate a skill, switching the menu's view if it is not listed here.

        The "useful skills" view hides skills the character has no aptitude
        for, and past twenty-odd skills the keys spill into digits — which the
        target prompt does not accept, since its own footer says ``[a-z]``. In
        either case ``*`` toggles to the other view, where it may have a
        letter.
        """
        client = self.client
        assert client is not None
        if reopen and not await self._await_skill_rows(step):
            raise MacroError("the skill screen did not open")

        for attempt in range(2):
            rows = parse_skill_menu(self.state.menu_lines)
            try:
                row = find_skill(rows, query)
            except SkillMenuError as exc:
                if attempt:
                    raise MacroError(str(exc)) from exc
            else:
                if row.key.isalpha():
                    return row
                if attempt:
                    raise MacroError(
                        f"{row.name} is listed under {row.key!r} here, and the "
                        "target prompt only takes a-z"
                    )
            # Switch between the useful and all views and look again. Waiting
            # for the rows to actually change beats sleeping a fixed step: the
            # redraw is a round trip plus a render, and a step that is merely
            # usually long enough reports "no such skill" for a skill that is
            # right there, just not drawn yet.
            before = _skill_signature(rows)
            await client.send_text("*")
            await self._await_skill_view_change(before, step)
        raise MacroError(f"could not find a skill matching {query!r}")

    async def _await_skill_view_change(
        self, before: tuple[tuple[str, str], ...], step: float
    ) -> None:
        """Wait for the skill screen to redraw into the other view."""
        deadline = time.monotonic() + max(4.0, step * 8)
        while time.monotonic() < deadline:
            rows = parse_skill_menu(self.state.menu_lines)
            if rows and _skill_signature(rows) != before:
                return
            await asyncio.sleep(0.02)

    async def escape_to_neutral(self, max_steps: int = 10) -> bool:
        """Back out of whatever is on screen until normal play resumes.

        One Escape is often not enough — menus nest, and a ``--more--`` ignores
        Escape entirely and wants a space. So this looks at what is actually up
        after each key rather than sending a fixed sequence, and stops as soon
        as the game is taking commands again.
        """
        client = self.client
        if client is None or not client.connected:
            return False
        for _ in range(max_steps):
            context = self.state.context
            if context in (InputContext.PLAY, InputContext.LOBBY):
                return True
            if context is InputContext.MORE:
                # Escape does not clear a --more--; any ordinary key does.
                await client.send_text(" ")
            else:
                # Escape cancels menus, prompts, targeting and text fields, and
                # answers no to a yes/no question.
                await client.send_escape()
            await asyncio.sleep(self.config.neutral_step_delay)
        settled = self.state.context in (InputContext.PLAY, InputContext.LOBBY)
        if not settled:
            log.warning(
                "could not get back to normal play; still in %s",
                self.state.context.value,
            )
        return settled

    # -- resilience -------------------------------------------------------

    async def _watchdog(self) -> None:
        """Escape a context the game has been parked in for too long."""
        # Poll often enough that the escape is not much later than the timeout,
        # but not so often that it spins.
        interval = min(2.0, max(0.2, self.config.stuck_timeout / 4))
        last_escape = 0.0
        while True:
            await asyncio.sleep(interval)
            if not self.state.in_game or self.config.stuck_timeout <= 0:
                continue
            if not self._accepting_input.is_set():
                # Character creation is on screen, and Escape is not a way out
                # of it: both `_prompt_choice` and `_reroll_random` treat it as
                # `game_ended(game_exit::abort)`. The creation loop owns the
                # keyboard until it is finished.
                continue
            if self.state.ui_layout == GAME_OVER_LAYOUT:
                # Not a screen anyone is going to drive, and nothing else
                # happens until it is gone: crawl holds the process open on it,
                # so the death report and the next character both wait on this
                # keypress. Verified against a real server, where leaving it to
                # the ordinary stuck timeout stalled the restart by that long.
                log.info("dismissing the game-over screen")
                await self.escape_to_neutral()
                continue
            if self.state.context not in STUCK_CONTEXTS:
                continue
            if self.state.context_age < self.config.stuck_timeout:
                continue
            if len(self.queue):
                # Someone is still trying to drive it; leave them to it.
                continue
            if time.monotonic() - last_escape < self.config.stuck_timeout:
                # One escape per window: a screen that swallows Escape should
                # not turn the watchdog into a keystroke firehose.
                continue
            last_escape = time.monotonic()
            log.info(
                "stuck in %s for %.0fs, backing out",
                self.state.context.value,
                self.state.context_age,
            )
            await self.escape_to_neutral()

    async def _restart_after_death(self) -> None:
        """Start a fresh character once the previous run is over.

        Permadeath plus open input means this is load-bearing rather than a
        nicety: without it the bot sits in the lobby until someone notices.

        The gate stays shut for the whole of this, the pause included. The new
        game uses the same ``game_id``, so it comes up on the same crawl
        version the previous character was playing.
        """
        try:
            await asyncio.sleep(self.config.restart_delay)
            client = self.client
            if client is None or not client.connected or self._stop.is_set():
                return
            log.info("starting a new game")
            await client.play(self.config.game_id)
            await self._enter_dungeon()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto-restart failed")
            # Whatever went wrong, leaving the keyboard locked would strand the
            # channel with a bot that silently ignores everything.
            self._accepting_input.set()

    async def _create_character(self) -> bool:
        """Answer character creation, whatever screens this version puts up.

        Driven off the layout crawl says is on screen rather than a fixed list
        of keys, because the number of screens varies. ``!`` picks a random
        species and background in one go; crawl then asks whether to keep the
        combination it rolled; and a weapon screen appears only for backgrounds
        that have a weapon choice at all — ``_choose_weapon`` returns early for
        the rest. A fixed sequence therefore either leaves the last screen
        unanswered or spills its surplus keys into the dungeon.

        Nothing here sends Escape. On both creation layouts it is not "cancel
        this screen" but ``game_ended(game_exit::abort)``.
        """
        client = self.client
        if client is None:
            return False

        deadline = time.monotonic() + self.config.newgame_timeout
        # Which push we last answered. Successive screens report the same
        # layout name, so without this the loop cannot tell the weapon screen
        # from the species screen it has just answered, and keys go astray.
        answered: int | None = None
        picked_character = False
        # When the creation screens first went away *and* a character existed.
        # Crawl pops each screen before pushing the next, so the gaps have to
        # be debounced; `character_ready` is what stops the gaps counting as
        # the end of creation in the first place.
        settled_since: float | None = None

        while time.monotonic() < deadline:
            if not client.connected or self._stop.is_set():
                return False

            layout = self.state.ui_layout
            screen = self.state.ui_push_count

            if layout in NEWGAME_LAYOUTS:
                settled_since = None
                if screen != answered:
                    if layout == NEWGAME_CONFIRM:
                        key = self.config.newgame_confirm_key
                    elif not picked_character:
                        key = self.config.newgame_character_key
                        picked_character = True
                    else:
                        key = self.config.newgame_weapon_key
                    log.info("character creation: %s, sending %r", layout, key)
                    await client.send_text(key)
                    answered = screen
            elif self.state.in_game and self.state.character_ready:
                # No creation screen and a character that actually exists. The
                # second half matters: `game_started` arrives when the process
                # starts, but a real 0.34.1 server takes about a second longer
                # to push the species screen, and for that second the state is
                # indistinguishable from being in the dungeon. Concluding from
                # a timer alone raced that gap and lost on a cold start,
                # opening the keyboard onto the species menu.
                #
                # Anything that is not a creation screen — a menu, a prompt, a
                # `--more--` on the welcome message — is the game putting up
                # its own screens, and counts as creation being over. Waiting
                # for normal play specifically would stall until the timeout
                # with the keyboard locked.
                now = time.monotonic()
                if settled_since is None:
                    settled_since = now
                elif now - settled_since >= self.config.newgame_settle:
                    log.info("character is in the dungeon")
                    return True
            else:
                # Still starting up, or a character is being rolled: no
                # keystrokes and no conclusions yet.
                settled_since = None

            await asyncio.sleep(self.config.newgame_poll_interval)

        log.warning(
            "character creation did not finish within %.0fs; last screen was %r",
            self.config.newgame_timeout,
            self.state.ui_layout,
        )
        return False

    def _cancel_newgame(self) -> None:
        if self._newgame_task is not None:
            self._newgame_task.cancel()
            self._newgame_task = None


def _skill_signature(rows: list[SkillRow]) -> tuple[tuple[str, str], ...]:
    """What a skill view looks like, for spotting a redraw into the other one.

    Keys and names together: the two views differ both in which skills they
    list and in the keys they assign, so either changing means the redraw has
    landed.
    """
    return tuple((row.key, row.name) for row in rows)


def _morgue_url(dump: Any) -> str | None:
    """The morgue link from ``game_ended``, if it is actually a link.

    `process_handler.py` builds it as ``morgue_url + filename``, and a server
    whose game config leaves ``morgue_url`` unset can send the string "None"
    glued to the filename instead of a URL — observed on a stock local
    webserver. Posting that to Discord as a link makes the bot look broken and
    tells nobody anything, so anything that is not http(s) is dropped.
    """
    if not isinstance(dump, str):
        return None
    url = dump.strip()
    return url if url.startswith(("http://", "https://")) else None
