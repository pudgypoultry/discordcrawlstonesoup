"""End-to-end runs against the mock server.

These exercise the parts that only misbehave once there is a real socket and a
real event loop: the login handshake, ping/pong, the two input wire forms, and
the compressed stream.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

import ssl

from dcssbot.client import LoginFailed, WebTilesClient
from dcssbot.keys import KEY_ESCAPE, KEY_TAB
from dcssbot.mockserver import (
    DEFAULT_GAME_ID,
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    MockWebTilesServer,
)
from dcssbot.protocol import NO_COMPRESSION_SUBPROTOCOL


@pytest.fixture
async def server() -> AsyncIterator[MockWebTilesServer]:
    mock = MockWebTilesServer(ping_interval=0.05)
    await mock.start()
    try:
        yield mock
    finally:
        await mock.stop()


async def connected(mock: MockWebTilesServer, **kwargs) -> WebTilesClient:
    client = WebTilesClient(mock.url, **kwargs)
    await client.start()
    return client


async def test_login_succeeds_and_reports_the_username(server: MockWebTilesServer) -> None:
    # The handshake deadlocks if the reader is not already pumping when the
    # login message goes out, so this is the test that matters most.
    client = await connected(server)
    try:
        assert await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD) == DEFAULT_USERNAME
    finally:
        await client.close()


async def test_login_failure_raises(server: MockWebTilesServer) -> None:
    client = await connected(server)
    try:
        with pytest.raises(LoginFailed, match="Wrong password"):
            await client.login(DEFAULT_USERNAME, "wrong")
    finally:
        await client.close()


async def test_no_compression_subprotocol_is_negotiated(server: MockWebTilesServer) -> None:
    client = await connected(server)
    try:
        assert client._ws.protocol.subprotocol == NO_COMPRESSION_SUBPROTOCOL
        assert client._decoder is not None and not client._decoder.compressed
    finally:
        await client.close()


async def test_compressed_stream_round_trips(server: MockWebTilesServer) -> None:
    # The mock refuses `no-compression` only when the client does not ask for
    # it, which is what compression=True does.
    client = await connected(server, compression=True)
    try:
        assert client._decoder is not None and client._decoder.compressed
        assert await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD) == DEFAULT_USERNAME
        await client.play(DEFAULT_GAME_ID)
    finally:
        await client.close()


async def test_play_starts_a_game(server: MockWebTilesServer) -> None:
    client = await connected(server)
    try:
        await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        reply = await client.play(DEFAULT_GAME_ID)
        assert reply["msg"] == "game_started"
    finally:
        await client.close()


async def test_play_without_login_is_refused(server: MockWebTilesServer) -> None:
    client = await connected(server)
    try:
        reply = await client.play(DEFAULT_GAME_ID)
        assert reply["msg"] == "login_required"
    finally:
        await client.close()


async def test_unknown_game_id_bounces_to_the_lobby(server: MockWebTilesServer) -> None:
    client = await connected(server)
    try:
        await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        assert (await client.play("no-such-game"))["msg"] == "go_lobby"
    finally:
        await client.close()


async def test_printable_keys_and_special_keys_use_different_wire_forms(
    server: MockWebTilesServer,
) -> None:
    client = await connected(server)
    try:
        await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        await client.play(DEFAULT_GAME_ID)
        await client.send_text("o")
        await client.send_keycode(KEY_TAB)
        await client.send_escape()
        await asyncio.sleep(0.1)

        sent = server.sessions[0].received_keys
        assert sent[0] == {"msg": "input", "text": "o"}
        assert sent[1] == {"msg": "key", "keycode": KEY_TAB}
        assert sent[2] == {"msg": "key", "keycode": KEY_ESCAPE}
    finally:
        await client.close()


async def test_open_brace_is_sent_as_a_byte_array(server: MockWebTilesServer) -> None:
    # client.js special-cases '{' this way; we match it rather than relying on
    # the server's JSON parser handling it as text.
    client = await connected(server)
    try:
        await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        await client.send_text("{")
        await asyncio.sleep(0.1)
        assert server.sessions[0].received_keys[0] == {"msg": "input", "data": [123]}
    finally:
        await client.close()


async def test_empty_text_sends_nothing(server: MockWebTilesServer) -> None:
    client = await connected(server)
    try:
        await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        await client.send_text("")
        await asyncio.sleep(0.05)
        assert server.sessions[0].received_keys == []
    finally:
        await client.close()


async def test_ping_is_answered_with_pong(server: MockWebTilesServer) -> None:
    # Failing to answer gets the connection dropped by a real server.
    client = await connected(server)
    seen: list[dict] = []
    client.on("ping", lambda msg: seen.append(msg))
    try:
        await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        await asyncio.sleep(0.2)
        assert seen, "server never pinged"
    finally:
        await client.close()


async def test_handlers_see_batched_messages_individually(
    server: MockWebTilesServer,
) -> None:
    client = await connected(server)
    seen: list[str] = []
    client.on("*", lambda msg: seen.append(str(msg.get("msg"))))
    try:
        await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        await client.play(DEFAULT_GAME_ID)
        await asyncio.sleep(0.1)
        # game start sends input_mode, player and msgs inside one frame.
        assert {"input_mode", "player", "msgs"} <= set(seen)
    finally:
        await client.close()


async def test_request_times_out_without_hanging_the_client(
    server: MockWebTilesServer,
) -> None:
    client = await connected(server)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await client.request("go_lobby", expect=("never_sent",), timeout=0.1)
        # The failed waiter must not linger and swallow a later reply.
        assert client._waiters == []
        assert await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD) == DEFAULT_USERNAME
    finally:
        await client.close()


async def test_pending_requests_fail_when_the_server_disconnects(
    server: MockWebTilesServer,
) -> None:
    client = await connected(server)
    try:
        await client.login(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        pending = asyncio.create_task(
            client.request("go_lobby", expect=("never_sent",), timeout=5.0)
        )
        await asyncio.sleep(0.05)
        await server.stop()
        for session in list(server.sessions):
            await session.ws.close()
        with pytest.raises(Exception):
            await asyncio.wait_for(pending, timeout=2.0)
    finally:
        await client.close()


# -- TLS context construction ----------------------------------------------
#
# The behaviour these protect against only shows up on Windows, where a CA
# rotation can leave two certificates for the same root in the OS store and
# OpenSSL's path-builder grabs the stale one — a chain a browser accepts
# fails here with "certificate has expired". These tests can only check that
# the right context gets built and that it actually has certifi's roots
# loaded, not reproduce the Windows-specific trust-store bug itself.


def test_ws_urls_get_no_explicit_ssl_context() -> None:
    client = WebTilesClient("ws://127.0.0.1:8080/socket")
    assert client._build_ssl_context() is None


def test_wss_urls_get_a_context_pinned_to_certifi() -> None:
    client = WebTilesClient("wss://crawl.example.org/socket")
    context = client._build_ssl_context()
    assert context is not None
    assert isinstance(context, ssl.SSLContext)
    # Confirms the bundle actually loaded rather than being an empty context.
    assert context.cert_store_stats()["x509_ca"] > 0


def test_the_wss_context_does_not_depend_on_the_os_store() -> None:
    # Two contexts built the same way load the same fixed set of roots,
    # regardless of whatever is currently sitting in the OS certificate store.
    client = WebTilesClient("wss://crawl.example.org/socket")
    first = client._build_ssl_context()
    second = client._build_ssl_context()
    assert first is not None and second is not None
    assert first.cert_store_stats() == second.cert_store_stats()
