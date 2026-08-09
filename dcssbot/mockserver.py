"""A small stand-in for a WebTiles server.

Real crawl is the only authority on the protocol, but it is a poor development
loop: every experiment needs a compiled binary, a webserver and a live game.
This mock implements the handful of messages the bot actually exchanges,
faithfully enough to exercise the login handshake, the game lifecycle, the
message batching and the input path.

It also mirrors the two shapes that trip clients up: frames arrive as
``{"msgs": [...]}`` batches, and compression, when negotiated, is one deflate
stream across the whole connection rather than per frame.

Run it directly::

    python -m dcssbot.mockserver --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import zlib
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection, serve

from .protocol import NO_COMPRESSION_SUBPROTOCOL

log = logging.getLogger(__name__)

DEFAULT_USERNAME = "testbot"
DEFAULT_PASSWORD = "hunter2"
DEFAULT_GAME_ID = "dcss-web-trunk"


@dataclass
class Session:
    """Per-connection state."""

    ws: ServerConnection
    compressor: Any = None
    username: str | None = None
    in_game: bool = False
    #: Every input the client sent, in order, for assertions in tests.
    received_keys: list[dict[str, Any]] = field(default_factory=list)


class MockWebTilesServer:
    """Serves just enough WebTiles to develop against."""

    def __init__(
        self,
        *,
        username: str = DEFAULT_USERNAME,
        password: str = DEFAULT_PASSWORD,
        game_id: str = DEFAULT_GAME_ID,
        ping_interval: float = 30.0,
    ) -> None:
        self.username = username
        self.password = password
        self.game_id = game_id
        self.ping_interval = ping_interval
        self.sessions: list[Session] = []
        self._server: Any = None
        self.port: int = 0

    # -- lifecycle --------------------------------------------------------

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await serve(
            self._handle,
            host,
            port,
            select_subprotocol=_select_subprotocol,
            ping_interval=None,
        )
        self.port = next(iter(self._server.sockets)).getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/socket"

    # -- connection handling ----------------------------------------------

    async def _handle(self, ws: ServerConnection) -> None:
        compressed = ws.protocol.subprotocol != NO_COMPRESSION_SUBPROTOCOL
        session = Session(
            ws=ws,
            compressor=(
                zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -zlib.MAX_WBITS)
                if compressed
                else None
            ),
        )
        self.sessions.append(session)
        pinger = asyncio.create_task(self._ping_loop(session))
        try:
            async for raw in ws:
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                await self._on_message(session, obj)
        except websockets.ConnectionClosed:
            pass
        finally:
            pinger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pinger
            if session in self.sessions:
                self.sessions.remove(session)

    async def _ping_loop(self, session: Session) -> None:
        while True:
            await asyncio.sleep(self.ping_interval)
            with contextlib.suppress(Exception):
                await self.send(session, {"msg": "ping"})

    async def _on_message(self, session: Session, obj: dict[str, Any]) -> None:
        kind = obj.get("msg")

        if kind == "login":
            if obj.get("username") == self.username and obj.get("password") == self.password:
                session.username = self.username
                await self.send(
                    session, {"msg": "login_success", "username": self.username}
                )
                await self.send(session, {"msg": "lobby_clear"})
                await self.send(session, {"msg": "lobby_complete"})
            else:
                await self.send(
                    session, {"msg": "login_fail", "reason": "Wrong password."}
                )

        elif kind == "pong":
            pass

        elif kind == "play":
            if session.username is None:
                await self.send(session, {"msg": "login_required", "game": self.game_id})
                return
            if obj.get("game_id") != self.game_id:
                await self.send(session, {"msg": "go_lobby"})
                return
            session.in_game = True
            await self.send(session, {"msg": "game_started"})
            await self.start_game_messages(session)

        elif kind == "go_lobby":
            session.in_game = False
            await self.send(session, {"msg": "go_lobby"})

        elif kind in ("key", "input", "text_input"):
            session.received_keys.append(obj)
            await self.on_input(session, obj)

    # -- scripted game behaviour (overridable in tests) -------------------

    async def start_game_messages(self, session: Session) -> None:
        await self.send_batch(
            session,
            [
                {"msg": "input_mode", "mode": 1},
                {
                    "msg": "player",
                    "hp": 18,
                    "hp_max": 18,
                    "mp": 3,
                    "mp_max": 3,
                    "turn": 0,
                    "place": "Dungeon",
                    "depth": 1,
                },
                {
                    "msg": "msgs",
                    "messages": [
                        {"text": "Welcome, <yellow>testbot</yellow> the Skirmisher.", "turn": 0},
                        {"text": "Press <w>?</w> for a list of commands.", "turn": 0},
                    ],
                },
            ],
        )

    async def on_input(self, session: Session, obj: dict[str, Any]) -> None:
        """Echo each input back as a log line, so the relay has something to do."""
        if obj.get("msg") == "key":
            label = f"keycode {obj.get('keycode')}"
        else:
            label = "".join(chr(c) for c in obj.get("data", [])) + obj.get("text", "")
        await self.send_batch(
            session,
            [{"msg": "msgs", "messages": [{"text": f"You send {label}.", "turn": 1}]}],
        )

    async def end_game(
        self, session: Session, *, reason: str = "quit", dump: str | None = None
    ) -> None:
        session.in_game = False
        await self.send(
            session,
            {
                "msg": "game_ended",
                "reason": reason,
                "message": "You die...",
                "dump": dump or "http://example.invalid/morgue/testbot.txt",
            },
        )
        await self.send(session, {"msg": "go_lobby"})

    # -- transport --------------------------------------------------------

    async def send(self, session: Session, message: dict[str, Any]) -> None:
        await self.send_batch(session, [message])

    async def send_batch(self, session: Session, messages: list[dict[str, Any]]) -> None:
        payload = json.dumps({"msgs": messages}).encode("utf-8")
        if session.compressor is None:
            await session.ws.send(payload.decode("utf-8"))
            return
        # Same trick as ws_handler: deflate, sync-flush, drop the 00 00 FF FF.
        chunk = session.compressor.compress(payload)
        chunk += session.compressor.flush(zlib.Z_SYNC_FLUSH)
        await session.ws.send(chunk[:-4])

    async def broadcast(self, messages: list[dict[str, Any]]) -> None:
        for session in list(self.sessions):
            with contextlib.suppress(Exception):
                await self.send_batch(session, messages)


def _select_subprotocol(ws: ServerConnection, subprotocols: list[str]) -> str | None:
    if NO_COMPRESSION_SUBPROTOCOL in subprotocols:
        return NO_COMPRESSION_SUBPROTOCOL
    return None


async def _main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = MockWebTilesServer(
        username=args.username, password=args.password, game_id=args.game_id
    )
    port = await server.start(args.host, args.port)
    log.info("mock webtiles server on ws://%s:%d/socket", args.host, port)
    log.info("login as %s / %s, game_id %s", args.username, args.password, args.game_id)
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--game-id", default=DEFAULT_GAME_ID)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
