"""The command grammar: what Discord messages become keystrokes.

Two ways in.

**A single printable character is sent as itself.** ``.dcss/o`` sends ``o``,
``.dcss/S`` sends ``S``, ``.dcss/#`` sends ``#``. Letters, digits and
punctuation all pass straight through, whether or not they mean anything on
the screen that is currently up.

**Everything a keyboard has but a character does not** — Tab, Escape, the
arrows, Ctrl combinations — needs a name, because there is no character to
type. ``.dcss/tab``, ``.dcss/esc``, ``.dcss/arrowup``, ``.dcss/ctrl f``.

Named words also exist for the common commands (``.dcss/explore`` for ``o``),
purely because they read better in a busy channel. :func:`help_text` lists
each one against the key it sends, so the two forms are never a mystery.

Since a single character is now a valid command, direction names are words:
``.dcss/north``, not ``.dcss/n`` — ``n`` is the character ``n``, which crawl
reads as a move to the south-east.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .gamestate import InputContext
from .keys import (
    CK_DELETE,
    CK_DOWN,
    CK_END,
    CK_HOME,
    CK_INSERT,
    CK_LEFT,
    CK_PGDN,
    CK_PGUP,
    CK_RIGHT,
    CK_UP,
    KEY_BACKSPACE,
    KEY_ENTER,
    KEY_ESCAPE,
    KEY_TAB,
    ctrl,
)


class Kind(Enum):
    """How a command reaches the game."""

    #: Printable characters, written to the game's tty.
    TEXT = "text"
    #: A single keycode over crawl's control socket.
    KEYCODE = "keycode"
    #: An ordered mix of the two.
    SEQUENCE = "sequence"
    #: Handled by the bot itself, never sent to the game.
    META = "meta"
    #: Back out of whatever is on screen until normal play resumes. The keys
    #: depend on what is up at the time, so the session drives it.
    RECOVER = "recover"


#: Kept for the dispatch-time check and for ``DCSS_BLOCK_DANGEROUS_KEYS``.
#: Nothing blocks these by default any more — every printable character is
#: sendable — but the set is still what that option refers to.
DANGEROUS_PLAY_CHARS: frozenset[str] = frozenset("S~&\x11\x18\x13")

#: Every context, i.e. no restriction. Context gating is off by default;
#: these sets only matter when DCSS_ENFORCE_CONTEXT is turned on.
ANY = frozenset(InputContext)
PLAY_ONLY = frozenset({InputContext.PLAY})
ANY_PROMPT = frozenset(
    {
        InputContext.MORE,
        InputContext.YESNO,
        InputContext.PROMPT,
        InputContext.MENU,
        InputContext.TARGET,
        InputContext.VIEW_MAP,
    }
)
ESCAPABLE = ANY_PROMPT | {InputContext.PLAY, InputContext.TEXT_ENTRY}


@dataclass(frozen=True)
class Step:
    """One keystroke within a command."""

    kind: Kind
    text: str = ""
    keycode: int = 0


@dataclass(frozen=True)
class Command:
    """A grammar entry."""

    name: str
    steps: tuple[Step, ...]
    contexts: frozenset[InputContext]
    help: str
    #: The key this sends, written the way a player would say it — "k",
    #: "Tab", "Ctrl-F". Shown by `.dcss/help` so a named command and the
    #: character it stands for are never two separate mysteries.
    key: str = ""
    meta: bool = False
    takes_argument: bool = False


def _text(*chars: str) -> tuple[Step, ...]:
    return tuple(Step(Kind.TEXT, text=c) for c in chars)


def _key(code: int) -> tuple[Step, ...]:
    return (Step(Kind.KEYCODE, keycode=code),)


def _cmd(
    name: str,
    steps: tuple[Step, ...],
    contexts: Iterable[InputContext],
    help_str: str,
    key: str = "",
    *,
    meta: bool = False,
    takes_argument: bool = False,
) -> Command:
    return Command(
        name=name,
        steps=steps,
        contexts=frozenset(contexts),
        help=help_str,
        key=key,
        meta=meta,
        takes_argument=takes_argument,
    )


#: Compass names to crawl's vi-style movement keys.
DIRECTIONS: dict[str, str] = {
    "north": "k",
    "south": "j",
    "east": "l",
    "west": "h",
    "ne": "u",
    "nw": "y",
    "se": "n",
    "sw": "b",
}

_COMMAND_LIST: list[Command] = [
    _cmd(name, _text(char), PLAY_ONLY | {InputContext.TARGET, InputContext.VIEW_MAP},
         f"move {name}", char)
    for name, char in DIRECTIONS.items()
]

_COMMAND_LIST += [
    _cmd("run", (), PLAY_ONLY, "run in a direction, e.g. `run ne`",
         "shifted direction", takes_argument=True),

    # -- keys with no character, which is why they need a name -------------
    _cmd("tab", _key(KEY_TAB), ANY, "auto-fight the nearest monster", "Tab"),
    _cmd("attack", _key(KEY_TAB), ANY, "auto-fight the nearest monster", "Tab"),
    _cmd("esc", _key(KEY_ESCAPE), ESCAPABLE, "cancel / back out", "Escape"),
    _cmd("escape", _key(KEY_ESCAPE), ESCAPABLE, "cancel / back out", "Escape"),
    _cmd("enter", _key(KEY_ENTER), ESCAPABLE, "confirm", "Enter"),
    _cmd("backspace", _key(KEY_BACKSPACE), ANY, "delete a character", "Backspace"),
    _cmd("delete", _key(CK_DELETE), ANY, "delete", "Delete"),
    _cmd("insert", _key(CK_INSERT), ANY, "insert", "Insert"),
    _cmd("space", _text(" "), ANY, "press space", "Space"),
    _cmd("more", _text(" "), ANY, "clear a --more-- prompt", "Space"),
    _cmd("arrowup", _key(CK_UP), ANY, "move the cursor up", "Up arrow"),
    _cmd("arrowdown", _key(CK_DOWN), ANY, "move the cursor down", "Down arrow"),
    _cmd("arrowleft", _key(CK_LEFT), ANY, "move the cursor left", "Left arrow"),
    _cmd("arrowright", _key(CK_RIGHT), ANY, "move the cursor right", "Right arrow"),
    _cmd("pageup", _key(CK_PGUP), ANY, "scroll a page up", "Page Up"),
    _cmd("pagedown", _key(CK_PGDN), ANY, "scroll a page down", "Page Down"),
    _cmd("home", _key(CK_HOME), ANY, "jump to the top", "Home"),
    _cmd("end", _key(CK_END), ANY, "jump to the bottom", "End"),
    _cmd("ctrl", (), ANY, "send a control key, e.g. `ctrl f`",
         "Ctrl-<letter>", takes_argument=True),

    # -- word aliases for common commands ----------------------------------
    _cmd("explore", _text("o"), PLAY_ONLY, "auto-explore", "o"),
    _cmd("wait", _text("s"), PLAY_ONLY, "wait one turn", "s"),
    _cmd("rest", _text("5"), PLAY_ONLY, "rest until healed or interrupted", "5"),
    _cmd("upstairs", _text("<"), PLAY_ONLY, "take stairs up", "<"),
    _cmd("downstairs", _text(">"), PLAY_ONLY, "take stairs down", ">"),
    _cmd("travel", _text("G"), PLAY_ONLY, "open the travel prompt", "G"),
    _cmd("map", _text("X"), PLAY_ONLY, "open the level map", "X"),
    _cmd("pickup", _text(","), PLAY_ONLY, "pick up items here", ","),
    _cmd("inv", _text("i"), PLAY_ONLY, "show inventory", "i"),
    _cmd("wield", _text("w"), PLAY_ONLY, "wield a weapon", "w"),
    _cmd("wear", _text("W"), PLAY_ONLY, "wear armour", "W"),
    _cmd("takeoff", _text("T"), PLAY_ONLY, "take off armour", "T"),
    _cmd("puton", _text("P"), PLAY_ONLY, "put on jewellery", "P"),
    _cmd("remove", _text("R"), PLAY_ONLY, "remove jewellery", "R"),
    _cmd("drop", _text("d"), PLAY_ONLY, "drop an item", "d"),
    _cmd("quaff", _text("q"), PLAY_ONLY, "quaff a potion", "q"),
    _cmd("read", _text("r"), PLAY_ONLY, "read a scroll", "r"),
    _cmd("eat", _text("e"), PLAY_ONLY, "eat", "e"),
    _cmd("fire", _text("f"), PLAY_ONLY, "fire the quivered action", "f"),
    _cmd("quiver", _text("Q"), PLAY_ONLY, "choose what to quiver", "Q"),
    _cmd("evoke", _text("v"), PLAY_ONLY, "evoke an item", "v"),
    _cmd("cast", _text("z"), PLAY_ONLY, "cast a spell", "z"),
    _cmd("spells", _text("I"), PLAY_ONLY, "list known spells", "I"),
    _cmd("memorise", _text("M"), PLAY_ONLY, "memorise a spell", "M"),
    _cmd("abil", _text("a"), PLAY_ONLY, "use an ability", "a"),
    _cmd("pray", _text("p"), PLAY_ONLY, "pray", "p"),
    _cmd("look", _text("x"), PLAY_ONLY, "examine surroundings", "x"),
    _cmd("char", _text("%"), PLAY_ONLY, "character overview", "%"),
    _cmd("skills", _text("m"), PLAY_ONLY, "the skill screen", "m"),
    _cmd("religion", _text("^"), PLAY_ONLY, "religion info", "^"),
    _cmd("resists", _text("A"), PLAY_ONLY, "mutations and resistances", "A"),
    _cmd("yes", _text("y"), ANY, "answer yes", "y"),
    _cmd("no", _text("n"), ANY, "answer no", "n"),

    # -- multi-key and bot commands ----------------------------------------
    _cmd("text", (), ANY, "type a word into a text field, e.g. `text Sigmund`",
         "typed text", takes_argument=True),
    _cmd("neutral", (Step(Kind.RECOVER),), ANY,
         "back out of any menu or prompt until normal play resumes",
         "Escape, repeatedly"),
    _cmd("link", (), ANY, "post the spectate link", meta=True),
    _cmd("status", (), ANY, "post the current status line", meta=True),
    _cmd("help", (), ANY, "list the available commands", meta=True),
]

COMMANDS: dict[str, Command] = {c.name: c for c in _COMMAND_LIST}

#: A single character is sent as itself. ASCII printable, excluding space —
#: `.dcss/space` covers that, since a trailing space is invisible in chat.
_LITERAL_CHAR = re.compile(r"^[\x21-\x7e]$")
#: Text fields get a conservative subset; no control or markup characters.
_TEXT_RE = re.compile(r"^[A-Za-z0-9 _'-]{1,32}$")

#: The pseudo-command a bare character parses into.
LITERAL = Command(
    name="key",
    steps=(),
    contexts=ANY,
    help="send a single key",
    key="the character itself",
)


@dataclass(frozen=True)
class ParsedCommand:
    """A grammar match, ready to be queued."""

    command: Command
    steps: tuple[Step, ...]
    argument: str | None = None

    @property
    def name(self) -> str:
        return self.command.name


class ParseError(Exception):
    """The message looked like a command but was not a valid one."""


def parse(content: str, prefix: str = ".dcss/") -> ParsedCommand | None:
    """Parse one Discord message.

    Returns ``None`` when the message is not addressed to the bot at all, so
    ordinary chat is ignored silently. Raises :class:`ParseError` when the
    prefix matched but the rest did not.
    """
    text = content.strip()
    if not text.lower().startswith(prefix.lower()):
        return None
    rest = text[len(prefix):].strip()
    if not rest:
        raise ParseError("no command given")

    head, _, argument = rest.partition(" ")
    token = head.strip()
    argument = argument.strip()

    # A single printable character is that character, case intact. Checked
    # before the name table so `.dcss/m` is the key `m`, not the word.
    if _LITERAL_CHAR.match(token):
        if argument:
            raise ParseError("a single key takes no argument")
        return ParsedCommand(command=LITERAL, steps=_text(token), argument=token)

    command = COMMANDS.get(token.lower())
    if command is None:
        raise ParseError(f"unknown command `{_sanitise(token)}`")

    if command.takes_argument:
        return _parse_argument(command, argument)
    if argument:
        raise ParseError(f"`{command.name}` takes no argument")
    return ParsedCommand(command=command, steps=command.steps)


def _parse_argument(command: Command, argument: str) -> ParsedCommand:
    if not argument:
        raise ParseError(f"`{command.name}` needs an argument")

    if command.name == "run":
        direction = argument.lower()
        if direction not in DIRECTIONS:
            raise ParseError("run takes a direction: " + ", ".join(DIRECTIONS))
        return ParsedCommand(
            command=command,
            steps=_text(DIRECTIONS[direction].upper()),
            argument=direction,
        )

    if command.name == "ctrl":
        letter = argument.lower()
        if len(letter) != 1 or not letter.isalpha():
            raise ParseError("ctrl takes a single letter, e.g. `ctrl f`")
        return ParsedCommand(
            command=command, steps=_key(ctrl(letter)), argument=letter
        )

    if command.name == "text":
        if not _TEXT_RE.match(argument):
            raise ParseError("text may only contain letters, digits and spaces")
        return ParsedCommand(
            command=command,
            steps=(Step(Kind.TEXT, text=argument),),
            argument=argument,
        )

    raise ParseError(f"`{command.name}` does not take an argument")


def allowed_in(command: Command, context: InputContext, *, enforce: bool = True) -> bool:
    """Whether this command may be sent in this context.

    With ``enforce`` false — the default the bot runs with — everything is
    allowed everywhere: a movement key pressed into an open menu is a menu
    selection, and that is the player's problem, not the bot's.
    """
    if command.meta or not enforce:
        return True
    if context is InputContext.UNKNOWN:
        return False
    return context in command.contexts


def is_safe_to_send(
    steps: Iterable[Step], context: InputContext, *, block_dangerous: bool = False
) -> bool:
    """Whether these keystrokes may be written to the game.

    Off by default: every printable character is sendable, ``S`` included.
    Turn ``DCSS_BLOCK_DANGEROUS_KEYS`` on to keep the run-ending keys out of
    normal play, if one person repeatedly saving-and-exiting becomes a problem.
    """
    if not block_dangerous or context is not InputContext.PLAY:
        return True
    return not any(
        ch in DANGEROUS_PLAY_CHARS
        for step in steps
        if step.kind is Kind.TEXT
        for ch in step.text
    )


def _sanitise(token: str) -> str:
    """Trim and strip formatting from a token before echoing it back."""
    return re.sub(r"[^A-Za-z0-9_-]", "", token)[:20] or "?"


_HELP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Move", ("north", "south", "east", "west", "ne", "nw", "se", "sw", "run")),
    ("Keys with no character", (
        "tab", "esc", "enter", "space", "backspace", "delete",
        "arrowup", "arrowdown", "arrowleft", "arrowright",
        "pageup", "pagedown", "home", "end", "ctrl",
    )),
    ("Common actions", (
        "explore", "wait", "rest", "upstairs", "downstairs", "travel", "map",
        "pickup", "inv", "wield", "wear", "takeoff", "drop", "quaff", "read",
        "eat", "fire", "quiver", "evoke",
    )),
    ("Magic and info", (
        "cast", "spells", "memorise", "abil", "pray",
        "look", "char", "skills", "religion", "resists",
    )),
    ("Prompts", ("yes", "no", "more", "text", "neutral")),
    ("Bot", ("link", "status", "help")),
)


def help_text(prefix: str = ".dcss/") -> str:
    """A listing of every named command against the key it sends."""
    lines = [
        f"**Any single key works on its own**: `{prefix}o` `{prefix}S` `{prefix}5` "
        f"`{prefix}#` — letters, digits and punctuation are sent as typed.",
        f"Keys with no character need a name. Below, `{prefix}name` → key sent.",
        "",
    ]
    for title, names in _HELP_GROUPS:
        entries = []
        for name in names:
            command = COMMANDS.get(name)
            if command is None:
                continue
            if command.meta:
                entries.append(f"`{prefix}{name}`")
            else:
                entries.append(f"`{prefix}{name}` → `{command.key}`")
        if entries:
            lines.append(f"**{title}**: {', '.join(entries)}")
    return "\n".join(lines)
