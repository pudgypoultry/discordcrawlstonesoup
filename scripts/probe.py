#!/usr/bin/env python3
"""Connect to a WebTiles server, send a key, and print what comes back.

This is milestone one from the plan and the thing to reach for whenever the
bot misbehaves against a real server: it exercises the connection, the login
handshake and the input path with nothing else in the way, and it prints the
raw decoded JSON so you can see what the server actually said.

    # against the bundled mock (start it with `python -m dcssbot.mockserver`)
    python scripts/probe.py --key o

    # against a local build
    python scripts/probe.py --url ws://localhost:8080/socket \\
        --username me --password pw --game-id dcss-web-trunk --key tab

Environment variables (DCSS_WS_URL, DCSS_USERNAME, ...) are used as defaults,
so an already-configured shell needs no flags.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dcssbot.client import WebTilesClient  # noqa: E402
from dcssbot.config import load  # noqa: E402
from dcssbot.formatting import clean_line  # noqa: E402
from dcssbot.gamestate import GameState  # noqa: E402
from dcssbot.keys import NAMED_KEYCODES  # noqa: E402

INTERESTING = {
    "msgs",
    "input_mode",
    "ui_state",
    "ui-push",
    "ui-pop",
    "ui-stack",
    "close_all_menus",
    "game_started",
    "game_ended",
    "go_lobby",
    "login_success",
    "login_fail",
    "player",
}


#: `set_game_links` carries rendered HTML whose hrefs hold the ids that `play`
#: expects. There is no cleaner listing in the protocol.
_PLAY_HREF = re.compile(r"#play-([A-Za-z0-9_.-]+)")


async def list_games(args: argparse.Namespace) -> int:
    """Log in and print the game ids this server offers."""
    client = WebTilesClient(args.url, compression=args.compression)
    seen: list[str] = []
    arrived = asyncio.Event()

    def collect(msg: dict[str, Any]) -> None:
        for game_id in _PLAY_HREF.findall(str(msg.get("content", ""))):
            if game_id not in seen:
                seen.append(game_id)
        arrived.set()

    # Registered before login, because the lobby sends this as part of the
    # login response — a waiter set up afterwards would already have missed it.
    client.on("set_game_links", collect)

    print(f"connecting to {args.url}")
    await client.start()
    try:
        username = await client.login(args.username, args.password)
        print(f"logged in as {username}\n")
        try:
            await asyncio.wait_for(arrived.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            print("server never sent set_game_links")
            return 1
        # A real server fills the list in asynchronously as it collects save
        # info, so give the follow-up renders a moment to land.
        await asyncio.sleep(2.0)
        if not seen:
            print("no games advertised for this account")
            return 1
        print("available game ids (use with DCSS_GAME_ID):")
        for game_id in seen:
            print(f"  {game_id}")
    finally:
        await client.close()
    return 0


async def probe(args: argparse.Namespace) -> int:
    state = GameState()
    client = WebTilesClient(args.url, compression=args.compression)

    def show(msg: dict[str, Any]) -> None:
        kind = msg.get("msg")
        state.handle(msg)
        if args.all or kind in INTERESTING:
            if kind == "msgs" and not args.raw:
                for entry in msg.get("messages", []):
                    text = clean_line(str(entry.get("text", "")))
                    if text:
                        print(f"  LOG | {text}")
                if "more" in msg:
                    print(f"  LOG | (more={msg['more']})")
            else:
                print(f"  {kind} | {json.dumps(msg)[:400]}")
            print(f"      -> context={state.context.value}")

    client.on("*", show)

    print(f"connecting to {args.url}")
    await client.start()
    try:
        print(f"logging in as {args.username}")
        username = await client.login(args.username, args.password)
        print(f"logged in as {username}")

        if args.game_id:
            print(f"starting game {args.game_id}")
            reply = await client.play(args.game_id)
            print(f"play replied {reply.get('msg')}")
            if reply.get("msg") != "game_started":
                return 1

        await asyncio.sleep(args.settle)

        for key in args.key:
            keycode = NAMED_KEYCODES.get(key.lower())
            if keycode is not None:
                print(f"sending keycode {keycode} ({key})")
                await client.send_keycode(keycode)
            else:
                print(f"sending text {key!r}")
                await client.send_text(key)
            await asyncio.sleep(args.settle)

        print(f"watching for {args.watch}s")
        await asyncio.sleep(args.watch)
        print(f"final context: {state.context.value}")
        status = state.status_line()
        if status:
            print(f"status: {status}")
    finally:
        await client.close()
    return 0


def main() -> int:
    defaults = load()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default=defaults.websocket_url)
    parser.add_argument("--username", default=defaults.username or "testbot")
    parser.add_argument("--password", default=defaults.password or "hunter2")
    parser.add_argument(
        "--game-id",
        default=defaults.game_id,
        help="game to start; pass an empty string to stay in the lobby",
    )
    parser.add_argument(
        "--key",
        action="append",
        default=[],
        help="a key to send; repeatable. Names like 'tab' and 'esc' become "
        "keycodes, anything else is sent as text",
    )
    parser.add_argument("--settle", type=float, default=1.0)
    parser.add_argument("--watch", type=float, default=3.0)
    parser.add_argument("--all", action="store_true", help="print every message type")
    parser.add_argument("--raw", action="store_true", help="do not prettify msgs")
    parser.add_argument(
        "--games",
        action="store_true",
        help="log in, print the game ids this server offers, and exit",
    )
    parser.add_argument(
        "--compression",
        action="store_true",
        help="use compressed frames instead of the no-compression subprotocol",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(list_games(args) if args.games else probe(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
