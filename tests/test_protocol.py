"""Framing, including the compressed path that is easy to get wrong."""

from __future__ import annotations

import json
import zlib

import pytest

from dcssbot.protocol import FrameDecoder, ProtocolError, encode, unwrap


def test_unwrap_batch() -> None:
    text = json.dumps({"msgs": [{"msg": "ping"}, {"msg": "msgs", "messages": []}]})
    assert [m["msg"] for m in unwrap(text)] == ["ping", "msgs"]


def test_unwrap_bare_message() -> None:
    # client.js treats a message without a `msgs` key as a batch of one.
    assert unwrap(json.dumps({"msg": "ping"})) == [{"msg": "ping"}]


def test_unwrap_rejects_non_objects() -> None:
    with pytest.raises(ProtocolError):
        unwrap("[1, 2, 3]")


def test_unwrap_rejects_invalid_json() -> None:
    with pytest.raises(ProtocolError):
        unwrap("{not json")


def test_encode_puts_the_msg_key_in() -> None:
    assert json.loads(encode("play", game_id="trunk")) == {
        "msg": "play",
        "game_id": "trunk",
    }


class _ServerSideCompressor:
    """Compresses exactly as ws_handler._encode_for_send does."""

    def __init__(self) -> None:
        self._obj = zlib.compressobj(
            zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -zlib.MAX_WBITS
        )

    def frame(self, payload: dict) -> bytes:
        raw = json.dumps(payload).encode("utf-8")
        out = self._obj.compress(raw) + self._obj.flush(zlib.Z_SYNC_FLUSH)
        return out[:-4]  # the server strips the 00 00 FF FF tail


def test_compressed_frames_decode_across_a_stream() -> None:
    # The decompressor is stateful for the whole connection: later frames are
    # only decodable after the earlier ones have gone through it.
    server = _ServerSideCompressor()
    decoder = FrameDecoder(compressed=True)

    first = decoder.decode(server.frame({"msgs": [{"msg": "ping"}]}))
    second = decoder.decode(
        server.frame({"msgs": [{"msg": "msgs", "messages": [{"text": "hi"}]}]})
    )

    assert first == [{"msg": "ping"}]
    assert second[0]["messages"][0]["text"] == "hi"


def test_compressed_decoder_rejects_a_frame_from_another_stream() -> None:
    decoder = FrameDecoder(compressed=True)
    other = _ServerSideCompressor()
    other.frame({"msgs": []})  # advance the other stream past its header
    with pytest.raises(ProtocolError):
        decoder.decode(other.frame({"msgs": [{"msg": "ping"}]}))


def test_plain_decoder_passes_text_through() -> None:
    decoder = FrameDecoder(compressed=False)
    assert decoder.decode('{"msgs":[{"msg":"pong"}]}') == [{"msg": "pong"}]
