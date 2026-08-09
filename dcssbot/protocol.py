"""Framing for the WebTiles websocket protocol.

A frame is either plain UTF-8 JSON or a raw-deflate blob, depending on what
subprotocol the handshake settled on. Either way the JSON is a single object:
usually ``{"msgs": [...]}`` carrying a batch, sometimes a bare message. The
client is expected to unwrap both forms — ``client.js`` does exactly this.

Compression is a stream, not a per-frame affair. The server keeps one
``zlib.compressobj`` for the connection and flushes with ``Z_SYNC_FLUSH``
after each frame, stripping the trailing ``00 00 FF FF``. We put those four
bytes back and feed a single long-lived decompressor, so frames cannot be
decoded out of order or in isolation.
"""

from __future__ import annotations

import json
import zlib
from typing import Any

#: Subprotocol that asks the server to skip compression entirely.
#: ``ws_handler.select_subprotocol`` honours this, which removes a whole class
#: of stream-state bugs. We still support the compressed path as a fallback.
NO_COMPRESSION_SUBPROTOCOL = "no-compression"

_SYNC_FLUSH_TAIL = b"\x00\x00\xff\xff"


class ProtocolError(Exception):
    """A frame could not be decoded."""


class FrameDecoder:
    """Decodes websocket frames into lists of WebTiles messages.

    One instance per connection: it owns the decompression stream state.
    """

    def __init__(self, compressed: bool) -> None:
        self.compressed = compressed
        self._inflater = (
            zlib.decompressobj(-zlib.MAX_WBITS) if compressed else None
        )

    def decode(self, frame: str | bytes) -> list[dict[str, Any]]:
        """Return the messages carried by one frame."""
        return unwrap(self.decode_text(frame))

    def decode_text(self, frame: str | bytes) -> str:
        """Return the JSON text of one frame, decompressing if needed."""
        if isinstance(frame, str):
            return frame
        if self._inflater is None:
            return frame.decode("utf-8")
        try:
            raw = self._inflater.decompress(frame + _SYNC_FLUSH_TAIL)
        except zlib.error as exc:  # pragma: no cover - stream corruption
            raise ProtocolError(f"decompression failed: {exc}") from exc
        return raw.decode("utf-8")


def unwrap(text: str) -> list[dict[str, Any]]:
    """Parse frame JSON into a list of messages.

    Batches arrive as ``{"msgs": [...]}``; a lone message arrives as itself.
    """
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise ProtocolError(f"invalid JSON in frame: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError(f"expected a JSON object, got {type(obj).__name__}")
    batch = obj.get("msgs")
    if batch is None:
        return [obj]
    if not isinstance(batch, list):
        raise ProtocolError("'msgs' was not a list")
    return [m for m in batch if isinstance(m, dict)]


def encode(msg: str, **data: Any) -> str:
    """Build an outgoing message. Mirrors ``comm.send_message``."""
    payload = dict(data)
    payload["msg"] = msg
    return json.dumps(payload)
