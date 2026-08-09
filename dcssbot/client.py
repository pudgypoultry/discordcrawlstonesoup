"""An asyncio WebTiles client.

This speaks the same JSON protocol as crawl's browser client, which means no
browser and no DOM: the webserver relays each message either to the game's tty
or over crawl's control socket, and we can drive it directly.

The one piece of ordering that is easy to get wrong: the reader task must be
running *before* the login message goes out, or the reply lands with nobody
listening and the handshake deadlocks. :meth:`start` therefore spins up the
pump first and every request registers its future before it writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Iterable

import websockets
from websockets.asyncio.client import ClientConnection, connect

from .keys import KEY_ESCAPE
from .protocol import (
    NO_COMPRESSION_SUBPROTOCOL,
    FrameDecoder,
    ProtocolError,
    encode,
)

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]


class WebTilesError(Exception):
    """A protocol-level failure that is not simply a dropped connection."""


class LoginFailed(WebTilesError):
    """The server rejected our credentials."""


class WebTilesClient:
    """A single connection to a WebTiles server."""

    def __init__(
        self,
        url: str,
        *,
        compression: bool = False,
        open_timeout: float = 20.0,
        request_timeout: float = 30.0,
        ping_interval: float | None = None,
    ) -> None:
        self.url = url
        # Asking for `no-compression` is honoured by ws_handler and removes the
        # stateful inflate stream entirely. The compressed path stays available
        # for servers that predate the subprotocol.
        self.compression = compression
        self.open_timeout = open_timeout
        self.request_timeout = request_timeout
        # crawl sends its own application-level pings; websockets' keepalive is
        # redundant and its default timeout fights with long game pauses.
        self.ping_interval = ping_interval

        self._ws: ClientConnection | None = None
        self._decoder: FrameDecoder | None = None
        self._reader: asyncio.Task[None] | None = None
        self._handlers: dict[str, list[Handler]] = {}
        self._waiters: list[tuple[frozenset[str], asyncio.Future[dict[str, Any]]]] = []
        self._closed = asyncio.Event()
        self.connected = False

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Open the connection and start reading. Does not log in."""
        subprotocols = None if self.compression else [NO_COMPRESSION_SUBPROTOCOL]
        self._ws = await connect(
            self.url,
            subprotocols=subprotocols,  # type: ignore[arg-type]
            open_timeout=self.open_timeout,
            ping_interval=self.ping_interval,
            max_size=2**24,
        )
        negotiated = self._ws.protocol.subprotocol
        compressed = negotiated != NO_COMPRESSION_SUBPROTOCOL
        if compressed and not self.compression:
            log.warning(
                "server did not accept %r; falling back to compressed frames",
                NO_COMPRESSION_SUBPROTOCOL,
            )
        self._decoder = FrameDecoder(compressed=compressed)
        self._closed.clear()
        self.connected = True
        # Start pumping before anything is sent, so replies cannot be missed.
        self._reader = asyncio.create_task(self._pump(), name="webtiles-reader")

    async def close(self) -> None:
        """Close the connection and stop the reader."""
        self.connected = False
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        self._ws = None
        self._fail_waiters(WebTilesError("connection closed"))
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    # -- handlers ---------------------------------------------------------

    def on(self, kind: str, handler: Handler) -> None:
        """Register a handler for one message type. ``*`` sees everything."""
        self._handlers.setdefault(kind, []).append(handler)

    async def _pump(self) -> None:
        assert self._ws is not None and self._decoder is not None
        try:
            async for frame in self._ws:
                try:
                    messages = self._decoder.decode(frame)
                except ProtocolError:
                    log.exception("dropping undecodable frame")
                    continue
                for message in messages:
                    await self._dispatch(message)
        except websockets.ConnectionClosed:
            log.info("websocket closed by server")
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("reader task failed")
        finally:
            self.connected = False
            self._fail_waiters(WebTilesError("connection closed"))
            self._closed.set()

    async def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("msg")
        if kind == "ping":
            await self.send("pong")

        for waiter_kinds, future in list(self._waiters):
            if kind in waiter_kinds and not future.done():
                future.set_result(message)
                self._waiters = [w for w in self._waiters if w[1] is not future]

        for handler in self._handlers.get("*", ()):
            await _maybe_await(handler(message))
        if isinstance(kind, str):
            for handler in self._handlers.get(kind, ()):
                await _maybe_await(handler(message))

    def _fail_waiters(self, exc: Exception) -> None:
        for _, future in self._waiters:
            if not future.done():
                future.set_exception(exc)
        self._waiters.clear()

    # -- sending ----------------------------------------------------------

    async def send(self, msg: str, **data: Any) -> None:
        """Send one message."""
        if self._ws is None:
            raise WebTilesError("not connected")
        await self._ws.send(encode(msg, **data))

    async def send_raw(self, payload: str) -> None:
        if self._ws is None:
            raise WebTilesError("not connected")
        await self._ws.send(payload)

    def _expect(self, kinds: Iterable[str]) -> asyncio.Future[dict[str, Any]]:
        """Register interest in a reply *before* the request is written."""
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiters.append((frozenset(kinds), future))
        return future

    async def request(
        self, msg: str, expect: Iterable[str], *, timeout: float | None = None, **data: Any
    ) -> dict[str, Any]:
        """Send a message and wait for one of ``expect`` to come back."""
        future = self._expect(expect)
        try:
            await self.send(msg, **data)
            return await asyncio.wait_for(
                future, timeout if timeout is not None else self.request_timeout
            )
        except BaseException:
            self._waiters = [w for w in self._waiters if w[1] is not future]
            future.cancel()
            raise

    # -- protocol operations ----------------------------------------------

    async def login(self, username: str, password: str) -> str:
        """Log in, returning the canonical username the server reports."""
        reply = await self.request(
            "login",
            expect=("login_success", "login_fail"),
            username=username,
            password=password,
        )
        if reply.get("msg") == "login_fail":
            raise LoginFailed(reply.get("reason") or "login rejected")
        return str(reply.get("username") or username)

    async def play(self, game_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Start or resume a game, waiting for it to actually come up."""
        return await self.request(
            "play",
            expect=("game_started", "go_lobby", "login_required"),
            timeout=timeout,
            game_id=game_id,
        )

    async def go_lobby(self) -> None:
        await self.send("go_lobby")

    async def watch(self, username: str) -> None:
        await self.send("watch", username=username)

    async def get_rc(self, game_id: str) -> str:
        reply = await self.request("get_rc", expect=("rcfile_contents",), game_id=game_id)
        return str(reply.get("contents", ""))

    async def set_rc(self, game_id: str, contents: str) -> None:
        await self.send("set_rc", game_id=game_id, contents=contents)

    # -- input ------------------------------------------------------------

    async def send_text(self, text: str) -> None:
        """Send printable characters, as the browser client does.

        These go to the game's tty via ``process_handler.handle_input``. The
        ``{`` special case matches ``client.js``, which sends it as a byte
        array rather than as text.
        """
        if not text:
            return
        if text == "{":
            await self.send("input", data=[ord("{")])
        else:
            await self.send("input", text=text)

    async def send_keycode(self, keycode: int) -> None:
        """Send a special key over crawl's control socket."""
        await self.send("key", keycode=keycode)

    async def send_escape(self) -> None:
        await self.send_keycode(KEY_ESCAPE)


async def _maybe_await(result: Awaitable[None] | None) -> None:
    if result is not None:
        await result
