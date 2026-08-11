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
``north``, not ``n`` — ``n`` is the character ``n``, which crawl reads as a
move to the south-east.

**Doubling a movement key runs.** ``uu`` is Shift-u, north-east until
something happens. Only the eight vi keys double; doubling anything else would
silently upper-case it, and ``ss`` would be save-and-exit.

**A modifier plus a key** is written as two words: ``ctrl f``, ``shift u``,
``shift arrowup``. Everything that combines keys this way is held back unless
the game is in ordinary play, because a run means nothing in a menu and a
control key there can do something surprising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .gamestate import InputContext
from .keys import (
    CK,
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
    #: Anything that combines keys — a doubled direction, a modifier, a run.
    #: These are only meaningful during ordinary play: inside a menu a run has
    #: no meaning and a control key can do something surprising, so they are
    #: held back there regardless of DCSS_ENFORCE_CONTEXT.
    multi_key: bool = False


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
    multi_key: bool = False,
) -> Command:
    return Command(
        name=name,
        steps=steps,
        contexts=frozenset(contexts),
        help=help_str,
        key=key,
        meta=meta,
        takes_argument=takes_argument,
        multi_key=multi_key,
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
         "shifted direction", takes_argument=True, multi_key=True),

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
         "Ctrl-<key>", takes_argument=True, multi_key=True),
    _cmd("shift", (), ANY, "send a shifted key, e.g. `shift u` to run north-east",
         "Shift-<key>", takes_argument=True, multi_key=True),

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

#: The pseudo-command a doubled direction parses into.
DOUBLED = Command(
    name="run-key",
    steps=(),
    contexts=PLAY_ONLY,
    help="run in a direction",
    key="the shifted character",
    multi_key=True,
)

#: crawl's vi movement keys. Doubling one of these runs in that direction,
#: which is Shift plus the same key. Only these eight: doubling an arbitrary
#: letter would silently upper-case it, and `ss` would be save-and-exit.
RUN_KEYS: frozenset[str] = frozenset("hjklyubn")

#: Named keys that have a Shift- and Ctrl- variant in cio.h.
_MODIFIED_KEYS: dict[str, str] = {
    "arrowup": "UP",
    "arrowdown": "DOWN",
    "arrowleft": "LEFT",
    "arrowright": "RIGHT",
    "insert": "INSERT",
    "home": "HOME",
    "end": "END",
    "pageup": "PGUP",
    "pagedown": "PGDN",
    "tab": "TAB",
}


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


def parse(
    content: str, prefix: str = ".dcss/", *, allow_bare: bool = True
) -> ParsedCommand | None:
    """Parse one Discord message.

    Keys need no prefix: a message that is just ``o`` sends ``o``, and one that
    is just ``north`` sends ``k``. Anything that is not a key is ordinary chat
    and returns ``None`` — silently, because in bare mode most messages are
    conversation and replying to each one would be unbearable.

    The prefix still works for keys, and is *required* for the bot's own
    commands, so nobody fires ``help`` by saying "help" in conversation. A
    prefixed message that does not parse raises :class:`ParseError`, since
    someone who typed the prefix clearly meant to address the bot.
    """
    text = content.strip()

    if text.lower().startswith(prefix.lower()):
        body = text[len(prefix):].strip()
        if not body:
            raise ParseError("no command given")
        return _parse_body(body)

    if not allow_bare or not text:
        return None
    try:
        parsed = _parse_body(text)
    except ParseError:
        return None
    # `help`, `link` and `status` are the bot talking about itself rather than
    # keys going to the game, so they keep the prefix.
    return None if parsed.command.meta else parsed


def _parse_body(body: str) -> ParsedCommand:
    """Turn a command body into keystrokes, or raise."""
    head, _, argument = body.partition(" ")
    token = head.strip()
    argument = argument.strip()

    # A single printable character is that character, case intact. Checked
    # before the name table so `m` is the key `m`, not the word.
    if _LITERAL_CHAR.match(token):
        if argument:
            raise ParseError("a single key takes no argument")
        return ParsedCommand(command=LITERAL, steps=_text(token), argument=token)

    # A doubled movement key runs: `uu` is Shift-u, north-east until something
    # happens. Restricted to the eight vi keys, since doubling anything else
    # would just upper-case it and `ss` would be save-and-exit.
    if len(token) == 2 and token[0] == token[1] and token[0] in RUN_KEYS:
        if argument:
            raise ParseError("a doubled key takes no argument")
        return ParsedCommand(
            command=DOUBLED, steps=_text(token[0].upper()), argument=token
        )

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

    if command.name in ("ctrl", "shift"):
        return _parse_modified(command, argument)

    if command.name == "text":
        if not _TEXT_RE.match(argument):
            raise ParseError("text may only contain letters, digits and spaces")
        return ParsedCommand(
            command=command,
            steps=(Step(Kind.TEXT, text=argument),),
            argument=argument,
        )

    raise ParseError(f"`{command.name}` does not take an argument")


def _parse_modified(command: Command, argument: str) -> ParsedCommand:
    """``ctrl f`` / ``shift u`` — a modifier plus a key that actually has one.

    Only combinations the game can receive are accepted. Shift on a letter is
    just its capital; on an arrow or Tab it is a distinct keycode from cio.h.
    Shift on punctuation is refused, because which symbol that produces is a
    property of the keyboard layout, not of crawl.
    """
    target = argument.strip()
    modifier = command.name

    if len(target) == 1 and target.isalpha():
        if modifier == "ctrl":
            return ParsedCommand(command, _key(ctrl(target)), target.lower())
        return ParsedCommand(command, _text(target.upper()), target.lower())

    named = _MODIFIED_KEYS.get(target.lower())
    if named is not None:
        code = CK.get(f"{modifier.upper()}_{named}")
        if code is not None:
            return ParsedCommand(command, _key(code), target.lower())

    raise ParseError(
        f"{modifier} takes a letter or one of: " + ", ".join(sorted(_MODIFIED_KEYS))
    )


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
    ("Move", ("north", "south", "east", "west", "ne", "nw", "se", "sw")),
    ("Keys with no character", (
        "tab", "esc", "enter", "space", "backspace", "delete",
        "arrowup", "arrowdown", "arrowleft", "arrowright",
        "pageup", "pagedown", "home", "end",
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
    ("Combinations (normal play only)", ("run", "ctrl", "shift")),
    ("Prompts", ("yes", "no", "more", "text", "neutral")),
    ("Bot", ("link", "status", "help")),
)


def help_text(prefix: str = ".dcss/") -> str:
    """A listing of every named command against the key it sends."""
    lines = [
        "**Just type the key.** `o` `S` `5` `#` — letters, digits and "
        "punctuation are sent as typed, no prefix needed.",
        "Keys with no character have a name instead. Below, name → key sent.",
        "**Double a movement key to run**: `hh` `jj` `kk` `ll` `yy` `uu` `bb` "
        "`nn` — `uu` runs north-east.",
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
                entries.append(f"`{name}` → `{command.key}`")
        if entries:
            lines.append(f"**{title}**: {', '.join(entries)}")
    lines.append(
        f"Bot commands keep the prefix, so nobody fires them by chatting: "
        f"`{prefix}help` `{prefix}link` `{prefix}status`. The prefix also still "
        f"works on any key — `{prefix}o` is the same as `o`."
    )
    return "\n".join(lines)
