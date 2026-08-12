"""Tracks what the game is currently waiting for.

Gating purely on ``input_mode`` is not enough: crawl reports ``COMMAND`` while
a menu is open, so a movement key queued a second earlier would arrive as a
menu selection. The UI stack (``ui-push`` / ``ui-pop`` / ``ui-stack`` /
``close_all_menus``) has to be tracked alongside it, and the two collapse into
a single :class:`InputContext` that the grammar can gate on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MouseMode:
    """``mouse_mode`` values from ``enums.js`` / ``mouse_control``."""

    NORMAL = 0
    COMMAND = 1
    TARGET = 2
    TARGET_DIR = 3
    TARGET_PATH = 4
    MORE = 5
    MACRO = 6
    PROMPT = 7
    YESNO = 8


class UIState:
    """``WebtilesUIState`` from ``tileweb.h``."""

    NORMAL = 0
    CRT = 1
    VIEW_MAP = 2


class InputContext(Enum):
    """What kind of key the game will accept right now."""

    #: No game running — we are sitting in the lobby.
    LOBBY = "lobby"
    #: Normal play: movement, commands, autoexplore.
    PLAY = "play"
    #: A ``--more--`` prompt is up; any key clears it.
    MORE = "more"
    #: A yes/no confirmation.
    YESNO = "yesno"
    #: A free-form prompt (single key or short answer).
    PROMPT = "prompt"
    #: Targeting a direction or a monster.
    TARGET = "target"
    #: A menu or CRT screen is open; expects a selection.
    MENU = "menu"
    #: A text field has focus.
    TEXT_ENTRY = "text_entry"
    #: The map view.
    VIEW_MAP = "view_map"
    #: We have not seen enough traffic to know.
    UNKNOWN = "unknown"


#: Contexts that are not normal play and that a watchdog should try to escape
#: if the game sits in them with nothing happening.
STUCK_CONTEXTS: frozenset[InputContext] = frozenset(
    {
        InputContext.MORE,
        InputContext.YESNO,
        InputContext.PROMPT,
        InputContext.TARGET,
        InputContext.MENU,
        InputContext.TEXT_ENTRY,
        InputContext.VIEW_MAP,
    }
)


@dataclass
class GameState:
    """Mutable view of the current game, fed by incoming messages."""

    in_game: bool = False
    game_id: str | None = None
    username: str | None = None
    input_mode: int = MouseMode.NORMAL
    ui_state: int = UIState.NORMAL
    ui_stack_depth: int = 0
    more: bool = False
    text_cursor: bool = False
    #: Last ``player`` message fields we care about, for the status line.
    player: dict[str, Any] = field(default_factory=dict)
    #: Tag of the CRT menu currently open, e.g. "skills".
    menu_tag: str | None = None
    #: Rendered lines of that menu, merged from ``txt`` updates. Crawl sends
    #: only the rows that changed, so these accumulate rather than replace.
    menu_lines: dict[str, str] = field(default_factory=dict)
    #: Selectable rows of a structured menu (inventory, `use_item`). Unlike
    #: the CRT screens, these arrive as data rather than rendered text.
    menu_items: list[Any] = field(default_factory=list)
    #: Monotonic time of the last context change.
    context_since: float = field(default_factory=time.monotonic)

    _context: InputContext = InputContext.UNKNOWN

    def __post_init__(self) -> None:
        self._context = self._derive_context()

    # -- message handling -------------------------------------------------

    def handle(self, msg: dict[str, Any]) -> None:
        """Fold one incoming WebTiles message into the state."""
        kind = msg.get("msg")

        if kind == "game_started":
            self.in_game = True
            self._reset_ui()
        elif kind in ("game_ended", "go_lobby"):
            self.in_game = False
            self._reset_ui()
        elif kind == "login_success":
            self.username = msg.get("username") or self.username
        elif kind == "input_mode":
            mode = msg.get("mode")
            if isinstance(mode, int):
                self.input_mode = mode
                # Leaving MORE is reported as a mode change, not a msgs update.
                if mode != MouseMode.MORE:
                    self.more = False
        elif kind == "ui_state":
            state = msg.get("state")
            if isinstance(state, int):
                self.ui_state = state
        elif kind == "ui-push":
            self.ui_stack_depth += 1
        elif kind == "ui-pop":
            self.ui_stack_depth = max(0, self.ui_stack_depth - 1)
        elif kind == "ui-stack":
            items = msg.get("items")
            self.ui_stack_depth = len(items) if isinstance(items, list) else 0
        elif kind == "close_all_menus":
            self.ui_stack_depth = 0
            self.clear_menu()
        elif kind == "menu":
            # A bare `menu` outside a ui-push still means something is open.
            self.ui_stack_depth = max(self.ui_stack_depth, 1)
            self.menu_tag = msg.get("tag") if isinstance(msg.get("tag"), str) else None
            self.menu_lines.clear()
            items = msg.get("items")
            self.menu_items = list(items) if isinstance(items, list) else []
        elif kind == "txt":
            if msg.get("id") == "menu_txt":
                lines = msg.get("lines")
                if isinstance(lines, dict):
                    self.menu_lines.update(
                        {str(k): str(v) for k, v in lines.items()}
                    )
        elif kind == "text_cursor":
            self.text_cursor = bool(msg.get("enabled"))
        elif kind == "msgs":
            if "more" in msg:
                self.more = bool(msg["more"])
        elif kind == "player":
            for key in ("hp", "hp_max", "mp", "mp_max", "turn", "place", "depth",
                        "god", "title", "name", "species", "char", "xl", "status"):
                if key in msg:
                    self.player[key] = msg[key]

        self._refresh_context()

    def clear_menu(self) -> None:
        """Forget the current menu's contents.

        Called before driving a menu, so a macro cannot read the leftovers of
        a screen that has already closed.
        """
        self.menu_tag = None
        self.menu_lines.clear()
        self.menu_items.clear()

    def _reset_ui(self) -> None:
        self.clear_menu()
        self.ui_stack_depth = 0
        self.ui_state = UIState.NORMAL
        self.more = False
        self.text_cursor = False
        self.input_mode = MouseMode.NORMAL
        if not self.in_game:
            self.player.clear()

    # -- derived context --------------------------------------------------

    @property
    def context(self) -> InputContext:
        return self._context

    @property
    def context_age(self) -> float:
        """Seconds since the context last changed."""
        return time.monotonic() - self.context_since

    def _refresh_context(self) -> None:
        new = self._derive_context()
        if new is not self._context:
            self._context = new
            self.context_since = time.monotonic()

    def _derive_context(self) -> InputContext:
        if not self.in_game:
            return InputContext.LOBBY
        # A --more-- swallows the next key wherever it appears, so it wins.
        if self.more or self.input_mode == MouseMode.MORE:
            return InputContext.MORE
        if self.input_mode == MouseMode.YESNO:
            return InputContext.YESNO
        if self.input_mode in (
            MouseMode.TARGET,
            MouseMode.TARGET_DIR,
            MouseMode.TARGET_PATH,
        ):
            return InputContext.TARGET
        # A text cursor means a field is taking typed input, wherever it is.
        # Crawl's message-line prompts — Ctrl-F search, travel destinations —
        # live outside the UI stack and leave input_mode at NORMAL, so
        # requiring an open menu here reported them as ordinary play and let
        # subsequent keys be typed into the prompt instead of played.
        if self.text_cursor:
            return InputContext.TEXT_ENTRY
        if self.ui_state == UIState.VIEW_MAP:
            return InputContext.VIEW_MAP
        if self.ui_stack_depth > 0 or self.ui_state == UIState.CRT:
            return InputContext.MENU
        if self.input_mode == MouseMode.PROMPT:
            return InputContext.PROMPT
        if self.input_mode in (MouseMode.COMMAND, MouseMode.NORMAL):
            return InputContext.PLAY
        return InputContext.UNKNOWN

    # -- presentation -----------------------------------------------------

    def status_line(self) -> str | None:
        """A one-line HP/place summary, or None if we have no player data."""
        p = self.player
        if not p:
            return None
        bits: list[str] = []
        if "hp" in p and "hp_max" in p:
            bits.append(f"HP {p['hp']}/{p['hp_max']}")
        if p.get("mp_max"):
            bits.append(f"MP {p.get('mp', '?')}/{p['mp_max']}")
        if p.get("place"):
            depth = p.get("depth")
            bits.append(f"{p['place']}:{depth}" if depth else str(p["place"]))
        if "turn" in p:
            bits.append(f"T:{p['turn']}")
        return " | ".join(bits) if bits else None
