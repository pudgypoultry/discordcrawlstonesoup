"""Configuration, read from the environment.

Everything has a default that works against the bundled mock server, so
``python -m dcssbot.probe``-style experiments need no setup. The values that
must be set for a real run are the Discord token and the WebTiles credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_opt(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_ids(name: str) -> frozenset[int]:
    raw = os.environ.get(name, "")
    ids: set[int] = set()
    for part in raw.replace(",", " ").split():
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"{name} must be a list of IDs, got {part!r}") from exc
    return frozenset(ids)


class ConfigError(Exception):
    """A configuration value is missing or malformed."""


@dataclass(frozen=True)
class Config:
    """Everything the bot needs to run."""

    # -- WebTiles ---------------------------------------------------------
    #: An IPv4 literal rather than ``localhost`` on purpose: Windows resolves
    #: ``localhost`` to ``::1`` first, and a server listening only on IPv4
    #: then looks unreachable for no visible reason.
    websocket_url: str = field(default_factory=lambda: _env("DCSS_WS_URL", "ws://127.0.0.1:8080/socket"))
    #: Base page URL, used to build spectate links (``<base>#watch-<user>``).
    site_url: str = field(default_factory=lambda: _env("DCSS_SITE_URL", "http://127.0.0.1:8080/"))
    username: str = field(default_factory=lambda: _env("DCSS_USERNAME", ""))
    password: str = field(default_factory=lambda: _env("DCSS_PASSWORD", ""))
    game_id: str = field(default_factory=lambda: _env("DCSS_GAME_ID", "dcss-web-trunk"))
    use_compression: bool = field(default_factory=lambda: _env_bool("DCSS_COMPRESSION", False))

    # -- Discord ----------------------------------------------------------
    discord_token: str = field(default_factory=lambda: _env("DISCORD_TOKEN", ""))
    channel_ids: frozenset[int] = field(default_factory=lambda: _env_ids("DISCORD_CHANNEL_IDS"))
    command_prefix: str = field(default_factory=lambda: _env("DCSS_PREFIX", ".dcss/"))

    # -- input pacing -----------------------------------------------------
    #: Community convention caps public-server clients at 10 commands/second.
    #: Anarchy input is far more watchable well below that.
    command_interval: float = field(default_factory=lambda: _env_float("DCSS_COMMAND_INTERVAL", 0.75))
    queue_depth: int = field(default_factory=lambda: _env_int("DCSS_QUEUE_DEPTH", 25))
    queue_ttl: float = field(default_factory=lambda: _env_float("DCSS_QUEUE_TTL", 12.0))
    per_user_cooldown: float = field(default_factory=lambda: _env_float("DCSS_USER_COOLDOWN", 0.0))
    collapse_duplicates: bool = field(default_factory=lambda: _env_bool("DCSS_COLLAPSE_DUPLICATES", False))

    # -- what chat is allowed to send -------------------------------------
    #: Off by default: any command is accepted in any context, wrong ones
    #: included, and the game deals with the consequences. Turn it on to have
    #: the bot drop commands that do not fit the screen that is up — quieter,
    #: but it silently eats input, which is its own kind of confusing.
    enforce_context: bool = field(default_factory=lambda: _env_bool("DCSS_ENFORCE_CONTEXT", False))
    #: Off by default, because every printable character is sendable now and
    #: `S` is a printable character. Turn it on to keep the run-ending keys
    #: (`S` save-and-exit, `~` macros, `&` wizard mode, the Ctrl- codes) out of
    #: normal play, if one person ending every run becomes a problem.
    block_dangerous_keys: bool = field(default_factory=lambda: _env_bool("DCSS_BLOCK_DANGEROUS_KEYS", False))
    #: Keys need no prefix by default: a message that is just `o` sends `o`.
    #: Turn this on to require the prefix on everything, which is the only way
    #: to keep the channel usable for conversation — several commands are
    #: ordinary words (`no`, `yes`, `map`, `read`, `run`), and bare mode fires
    #: them whenever somebody types one.
    require_prefix: bool = field(default_factory=lambda: _env_bool("DCSS_REQUIRE_PREFIX", False))
    #: Pause between the steps of a multi-step macro. Menus need time to
    #: re-render, and a macro that races ahead reads the previous screen.
    macro_step_delay: float = field(default_factory=lambda: _env_float("DCSS_MACRO_STEP_DELAY", 0.6))
    #: Pause between the keys `.dcss/neutral` sends while backing out.
    neutral_step_delay: float = field(default_factory=lambda: _env_float("DCSS_NEUTRAL_STEP_DELAY", 0.4))

    # -- log relay --------------------------------------------------------
    flush_idle: float = field(default_factory=lambda: _env_float("DCSS_FLUSH_IDLE", 1.2))
    flush_max_lines: int = field(default_factory=lambda: _env_int("DCSS_FLUSH_MAX_LINES", 18))
    flush_max_chars: int = field(default_factory=lambda: _env_int("DCSS_FLUSH_MAX_CHARS", 1800))
    show_status_line: bool = field(default_factory=lambda: _env_bool("DCSS_STATUS_LINE", True))

    # -- resilience -------------------------------------------------------
    #: If the game sits in a menu or prompt this long with nothing happening,
    #: send Escape. Random input finds menus constantly and nobody in Discord
    #: necessarily knows how to back out of one.
    stuck_timeout: float = field(default_factory=lambda: _env_float("DCSS_STUCK_TIMEOUT", 45.0))
    auto_restart: bool = field(default_factory=lambda: _env_bool("DCSS_AUTO_RESTART", True))
    restart_delay: float = field(default_factory=lambda: _env_float("DCSS_RESTART_DELAY", 10.0))
    #: newgame.cc: '!' is M_RANDOM_CHAR, a fully random species *and*
    #: background. '#' is M_VIABLE_CHAR, which only draws from combinations the
    #: game recommends — a narrower pool, and not what "random character" means.
    newgame_character_key: str = field(default_factory=lambda: _env("DCSS_NEWGAME_CHAR", "!"))
    #: Answers the "Do you want to play this combination? [Y/n/q]" popup that
    #: `_reroll_random` puts up after a random character. Only 'n', Tab, '!' and
    #: '#' reroll; 'q' and Escape abort the game outright, so this must never be
    #: set to either of those.
    newgame_confirm_key: str = field(default_factory=lambda: _env("DCSS_NEWGAME_CONFIRM", "y"))
    #: Answers the choice screens that follow the character: the weapon screen,
    #: and the map screen on game types that have one. '*' is "random" on all of
    #: them; '+' is random-but-recommended on the weapon screen.
    newgame_weapon_key: str = field(default_factory=lambda: _env("DCSS_NEWGAME_WEAPON", "*"))
    #: How often to look at what character creation is showing. Only one key
    #: is ever sent per screen, so this paces looking rather than typing.
    newgame_poll_interval: float = field(default_factory=lambda: _env_float("DCSS_NEWGAME_POLL", 0.15))
    #: How long the game has to keep looking like ordinary play before creation
    #: is called finished. Crawl pops each screen before pushing the next, and
    #: during that gap the state is identical to being in the dungeon.
    newgame_settle: float = field(default_factory=lambda: _env_float("DCSS_NEWGAME_SETTLE", 1.5))
    #: How long to keep answering creation screens before giving up and letting
    #: chat have the keyboard back.
    newgame_timeout: float = field(default_factory=lambda: _env_float("DCSS_NEWGAME_TIMEOUT", 60.0))
    reconnect_delay: float = field(default_factory=lambda: _env_float("DCSS_RECONNECT_DELAY", 5.0))
    reconnect_max_delay: float = field(default_factory=lambda: _env_float("DCSS_RECONNECT_MAX_DELAY", 120.0))

    log_level: str = field(default_factory=lambda: _env("DCSS_LOG_LEVEL", "INFO"))
    rc_path: str | None = field(default_factory=lambda: _env_opt("DCSS_RC_PATH"))

    def validate(self) -> None:
        """Raise :class:`ConfigError` if a required value is missing."""
        missing = [
            name
            for name, value in (
                ("DISCORD_TOKEN", self.discord_token),
                ("DCSS_USERNAME", self.username),
                ("DCSS_PASSWORD", self.password),
            )
            if not value
        ]
        if missing:
            raise ConfigError("missing required settings: " + ", ".join(missing))
        if not self.channel_ids:
            raise ConfigError("DISCORD_CHANNEL_IDS must list at least one channel")
        # `_reroll_random` in newgame.cc: 'q' (and Escape) end the game before
        # it starts, and 'n'/Tab/'!'/'#' reroll — which would spin until the
        # creation timeout rather than ever accepting a character.
        if self.newgame_confirm_key.lower() in {"q", "n", "\t", "!", "#"}:
            raise ConfigError(
                f"DCSS_NEWGAME_CONFIRM cannot be {self.newgame_confirm_key!r}: "
                "'q' aborts the new game and 'n'/Tab/'!'/'#' reroll it forever"
            )
        if self.command_interval < 0.1:
            raise ConfigError(
                "DCSS_COMMAND_INTERVAL below 0.1s exceeds the 10 commands/second "
                "ceiling public servers ask clients to respect"
            )

    def spectate_url(self, username: str | None = None) -> str:
        """The ``#watch-<user>`` link, as built by ``client.js``'s hash router."""
        who = username or self.username
        base = self.site_url if self.site_url.endswith("/") else self.site_url + "/"
        return f"{base}#watch-{who}"


def load() -> Config:
    return Config()
