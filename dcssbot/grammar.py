"""The command grammar: what Discord messages become keystrokes.

Anyone in the channel can post, so this is an allowlist, not a blacklist. A
token that is not in :data:`COMMANDS` produces nothing at all — there is no
generic "send this character" escape hatch, which is what keeps ``S``
(save and exit), ``Ctrl-Q``, ``~`` (macro editor) and ``&`` (wizard mode) out
of reach by construction rather than by enumeration.

Two commands do take free text — ``select`` for menu letters and ``text`` for
text fields — so both filter their argument and are restricted to the contexts
where a stray keystroke cannot reach the dungeon. :func:`is_safe_to_send` is a
second, independent check applied at the moment of sending, because the
context can change between queueing and dispatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .gamestate import InputContext
from .keys import (
    CK_DOWN,
    CK_LEFT,
    CK_RIGHT,
    CK_UP,
    KEY_BACKSPACE,
    KEY_ENTER,
    KEY_ESCAPE,
    KEY_TAB,
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


#: Contexts in which ordinary play commands are accepted.
PLAY_ONLY = frozenset({InputContext.PLAY})
#: Contexts where a single key is expected and almost anything is accepted.
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
#: Escape is valid nearly everywhere and is how the watchdog unsticks things.
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
    #: Set for commands the bot handles itself (``link``, ``help``, ``status``).
    meta: bool = False
    #: Set for commands that consume the rest of the message as an argument.
    takes_argument: bool = False


def _text(*chars: str) -> tuple[Step, ...]:
    return tuple(Step(Kind.TEXT, text=c) for c in chars)


def _key(code: int) -> tuple[Step, ...]:
    return (Step(Kind.KEYCODE, keycode=code),)


def _cmd(
    name: str,
    steps: tuple[Step, ...],
    contexts: Iterable[InputContext],
    help_text: str,
    *,
    meta: bool = False,
    takes_argument: bool = False,
) -> Command:
    return Command(
        name=name,
        steps=steps,
        contexts=frozenset(contexts),
        help=help_text,
        meta=meta,
        takes_argument=takes_argument,
    )


# Movement uses compass names rather than crawl's vi keys, because ``n`` means
# both "south-east" and "no" and the ambiguity is not worth the keystroke.
_DIRECTIONS: dict[str, str] = {
    "n": "k",
    "s": "j",
    "e": "l",
    "w": "h",
    "ne": "u",
    "nw": "y",
    "se": "n",
    "sw": "b",
}

_COMMAND_LIST: list[Command] = []

for _name, _keychar in _DIRECTIONS.items():
    _COMMAND_LIST.append(
        _cmd(
            _name,
            _text(_keychar),
            PLAY_ONLY | {InputContext.TARGET, InputContext.VIEW_MAP},
            f"move {_name}",
        )
    )

_COMMAND_LIST += [
    _cmd(
        "run",
        (),
        PLAY_ONLY,
        "run in a direction, e.g. `.dcss/run ne`",
        takes_argument=True,
    ),
    # -- exploration and movement ----------------------------------------
    _cmd("o", _text("o"), PLAY_ONLY, "auto-explore"),
    _cmd("explore", _text("o"), PLAY_ONLY, "auto-explore"),
    _cmd("tab", _key(KEY_TAB), PLAY_ONLY, "auto-fight the nearest monster"),
    _cmd("attack", _key(KEY_TAB), PLAY_ONLY, "auto-fight the nearest monster"),
    _cmd("wait", _text("s"), PLAY_ONLY, "wait one turn"),
    _cmd("rest", _text("5"), PLAY_ONLY, "rest until healed or interrupted"),
    _cmd("up", _text("<"), PLAY_ONLY, "take stairs up"),
    _cmd("down", _text(">"), PLAY_ONLY, "take stairs down"),
    _cmd("travel", _text("G"), PLAY_ONLY, "open the travel prompt"),
    _cmd("map", _text("X"), PLAY_ONLY, "open the level map"),
    # -- items -------------------------------------------------------------
    _cmd("pickup", _text(","), PLAY_ONLY, "pick up items here"),
    _cmd("inv", _text("i"), PLAY_ONLY, "show inventory"),
    _cmd("wield", _text("w"), PLAY_ONLY, "wield a weapon"),
    _cmd("wear", _text("W"), PLAY_ONLY, "wear armour"),
    _cmd("takeoff", _text("T"), PLAY_ONLY, "take off armour"),
    _cmd("puton", _text("P"), PLAY_ONLY, "put on jewellery"),
    _cmd("remove", _text("R"), PLAY_ONLY, "remove jewellery"),
    _cmd("drop", _text("d"), PLAY_ONLY, "drop an item"),
    _cmd("quaff", _text("q"), PLAY_ONLY, "quaff a potion"),
    _cmd("read", _text("r"), PLAY_ONLY, "read a scroll"),
    _cmd("eat", _text("e"), PLAY_ONLY, "eat"),
    _cmd("fire", _text("f"), PLAY_ONLY, "fire the quivered action"),
    _cmd("quiver", _text("Q"), PLAY_ONLY, "choose what to quiver"),
    _cmd("evoke", _text("v"), PLAY_ONLY, "evoke an item"),
    # -- abilities and spells ---------------------------------------------
    _cmd("cast", _text("z"), PLAY_ONLY, "cast a spell"),
    _cmd("spells", _text("I"), PLAY_ONLY, "list known spells"),
    _cmd("memorise", _text("M"), PLAY_ONLY, "memorise a spell"),
    _cmd("abil", _text("a"), PLAY_ONLY, "use an ability"),
    _cmd("pray", _text("p"), PLAY_ONLY, "pray"),
    # -- information -------------------------------------------------------
    _cmd("look", _text("x"), PLAY_ONLY, "examine surroundings"),
    _cmd("char", _text("%"), PLAY_ONLY, "show the character overview"),
    _cmd("skills", _text("m"), PLAY_ONLY, "show the skill screen"),
    _cmd("religion", _text("^"), PLAY_ONLY, "show religion info"),
    _cmd("resists", _text("A"), PLAY_ONLY, "show mutations and resistances"),
    # -- answering prompts and menus ---------------------------------------
    _cmd("esc", _key(KEY_ESCAPE), ESCAPABLE, "press Escape"),
    _cmd("enter", _key(KEY_ENTER), ESCAPABLE, "press Enter"),
    _cmd("space", _text(" "), ANY_PROMPT, "press Space"),
    _cmd("backspace", _key(KEY_BACKSPACE), {InputContext.TEXT_ENTRY}, "delete a character"),
    _cmd("yes", _text("y"), ANY_PROMPT, "answer yes"),
    _cmd("no", _text("n"), ANY_PROMPT, "answer no"),
    _cmd("more", _text(" "), {InputContext.MORE}, "clear a --more-- prompt"),
    _cmd("scrollup", _key(CK_UP), ANY_PROMPT, "scroll up in a menu"),
    _cmd("scrolldown", _key(CK_DOWN), ANY_PROMPT, "scroll down in a menu"),
    _cmd("scrollleft", _key(CK_LEFT), ANY_PROMPT, "move left in a menu"),
    _cmd("scrollright", _key(CK_RIGHT), ANY_PROMPT, "move right in a menu"),
    _cmd(
        "select",
        (),
        {InputContext.MENU, InputContext.PROMPT, InputContext.TARGET},
        "select a menu entry, e.g. `.dcss/select b`",
        takes_argument=True,
    ),
    _cmd(
        "text",
        (),
        {InputContext.TEXT_ENTRY},
        "type into a text field, e.g. `.dcss/text hello`",
        takes_argument=True,
    ),
    # -- bot commands ------------------------------------------------------
    _cmd("link", (), set(InputContext), "post the spectate link", meta=True),
    _cmd("status", (), set(InputContext), "post the current status line", meta=True),
    _cmd("help", (), set(InputContext), "list the available commands", meta=True),
]

#: Digits answer counts, menu pages and character-creation screens.
for _digit in "123456789":
    _COMMAND_LIST.append(
        _cmd(_digit, _text(_digit), ANY_PROMPT, f"press {_digit}")
    )

COMMANDS: dict[str, Command] = {c.name: c for c in _COMMAND_LIST}

#: Characters that must never reach the game while it is accepting normal
#: commands. Nothing in the grammar produces them; this is the backstop for
#: the two argument-taking commands and for any future addition.
DANGEROUS_PLAY_CHARS: frozenset[str] = frozenset("S~&\x11\x18\x13")

#: Menu selections are single alphanumerics plus the handful of punctuation
#: keys crawl's own menus use (``*`` random, ``+`` recommended, ``!`` cycle,
#: ``-`` none, ``?`` help).
_SELECT_RE = re.compile(r"^[A-Za-z0-9*+!?_-]$")
#: Text fields get a conservative subset; no control or markup characters.
_TEXT_RE = re.compile(r"^[A-Za-z0-9 _'-]{1,32}$")


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
    prefix matched but the rest did not — the caller decides whether that is
    worth a reply.
    """
    text = content.strip()
    if not text.lower().startswith(prefix.lower()):
        return None
    rest = text[len(prefix):].strip()
    if not rest:
        raise ParseError("no command given")

    head, _, argument = rest.partition(" ")
    token = head.strip().lower()
    argument = argument.strip()

    command = COMMANDS.get(token)
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
        if direction not in _DIRECTIONS:
            raise ParseError(
                "run takes a direction: " + ", ".join(sorted(_DIRECTIONS))
            )
        return ParsedCommand(
            command=command,
            steps=_text(_DIRECTIONS[direction].upper()),
            argument=direction,
        )

    if command.name == "select":
        if not _SELECT_RE.match(argument):
            raise ParseError("select takes one letter, digit or menu symbol")
        return ParsedCommand(
            command=command, steps=_text(argument), argument=argument
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


def allowed_in(command: Command, context: InputContext) -> bool:
    """Whether the grammar permits this command in this context."""
    if command.meta:
        return True
    if context is InputContext.UNKNOWN:
        # Better to hold a key than to guess wrong about an open menu.
        return False
    return context in command.contexts


def is_safe_to_send(steps: Iterable[Step], context: InputContext) -> bool:
    """Final check, applied at dispatch time rather than at parse time.

    The context can change while a command sits in the queue, so the dangerous
    characters are re-checked against the context that actually applies.
    """
    if context is not InputContext.PLAY:
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


def help_text(prefix: str = ".dcss/") -> str:
    """A grouped listing of every command, for the ``help`` meta-command."""
    groups: list[tuple[str, list[str]]] = [
        ("Move", [f"{prefix}{d}" for d in _DIRECTIONS] + [f"{prefix}run <dir>"]),
        (
            "Act",
            [
                f"{prefix}{n}"
                for n in (
                    "o", "tab", "wait", "rest", "up", "down", "travel", "map",
                )
            ],
        ),
        (
            "Items",
            [
                f"{prefix}{n}"
                for n in (
                    "pickup", "inv", "wield", "wear", "takeoff", "puton",
                    "remove", "drop", "quaff", "read", "eat", "fire", "quiver",
                    "evoke",
                )
            ],
        ),
        (
            "Magic",
            [f"{prefix}{n}" for n in ("cast", "spells", "memorise", "abil", "pray")],
        ),
        (
            "Info",
            [f"{prefix}{n}" for n in ("look", "char", "skills", "religion", "resists")],
        ),
        (
            "Prompts",
            [
                f"{prefix}{n}"
                for n in (
                    "esc", "enter", "space", "yes", "no", "more", "select <x>",
                    "text <words>", "scrollup", "scrolldown", "1"
                )
            ],
        ),
        ("Bot", [f"{prefix}{n}" for n in ("link", "status", "help")]),
    ]
    lines = [f"**{title}**: {', '.join(f'`{c}`' for c in items)}" for title, items in groups]
    return "\n".join(lines)
