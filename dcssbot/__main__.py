"""Entry point: ``python -m dcssbot``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from .config import ConfigError, load
from .runner import run


def main() -> int:
    config = load()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # discord.py's own logging is chatty at INFO and drowns the game log.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    try:
        config.validate()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        print("see README.md for the full list of settings", file=sys.stderr)
        return 2

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
