"""Connection lifecycle tests — connect/handshake/send/read error paths
and the reconnect-with-backoff primitive.

These cover the previously-untested core transport layer: handshake
failure tears the connection back down, send/read errors disconnect and
raise the typed exceptions, and ``reconnect_with_backoff`` loops with
exponential, capped delays until ``connect()`` succeeds (cancellation
propagating).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nikobus_connect.connection import NikobusConnect
from nikobus_connect.const import COMMANDS_HANDSHAKE
from nikobus_connect.exceptions import (
    NikobusConnectionError,
    NikobusReadError,
    NikobusSendError,
)


def _stream_pair() -> tuple[MagicMock, MagicMock]:
    reader = MagicMock()
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return reader, writer


async def test_tcp_connect_runs_handshake() -> None:
    conn = NikobusConnect("192.168.2.50:9999")
    reader, writer = _stream_pair()
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))) as opener,
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    opener.assert_awaited_once_with("192.168.2.50", 9999)
    assert conn.is_connected
    # One write per handshake command.
    assert writer.write.call_count == len(COMMANDS_HANDSHAKE)


async def test_serial_path_uses_serial_asyncio() -> None:
    conn = NikobusConnect("/dev/ttyUSB0")
    reader, writer = _stream_pair()
    with (
        patch(
            "nikobus_connect.connection.serial_asyncio.open_serial_connection",
            new=AsyncMock(return_value=(reader, writer)),
        ) as opener,
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    assert opener.await_count == 1
    assert opener.await_args.kwargs["url"] == "/dev/ttyUSB0"
    assert conn.is_connected


async def test_handshake_failure_disconnects_and_raises() -> None:
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair()
    writer.write.side_effect = OSError("broken pipe")
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=AsyncMock()),
        pytest.raises(NikobusConnectionError),
    ):
        await conn.connect()
    assert not conn.is_connected


async def test_send_when_disconnected_raises() -> None:
    conn = NikobusConnect("host:1234")
    with pytest.raises(NikobusConnectionError):
        await conn.send("#E1")


async def test_send_error_disconnects() -> None:
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair()
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    writer.write.side_effect = OSError("gone")
    with pytest.raises(NikobusSendError):
        await conn.send("#E1")
    assert not conn.is_connected


async def test_read_incomplete_disconnects_and_raises() -> None:
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair()
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    reader.readuntil = AsyncMock(
        side_effect=asyncio.IncompleteReadError(partial=b"", expected=1)
    )
    with pytest.raises(NikobusReadError):
        await conn.read()
    assert not conn.is_connected


# ---------------------------------------------------------------------------
# reconnect_with_backoff
# ---------------------------------------------------------------------------


async def test_backoff_retries_until_success_with_doubling_delays() -> None:
    conn = NikobusConnect("host:1234")
    conn.connect = AsyncMock(  # type: ignore[method-assign]
        side_effect=[NikobusConnectionError("x"), NikobusConnectionError("x"), None]
    )
    sleeps: list[float] = []
    attempts: list[tuple[int, float]] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    with patch("asyncio.sleep", new=fake_sleep):
        result = await conn.reconnect_with_backoff(
            initial_delay=1.0,
            max_delay=30.0,
            on_attempt=lambda n, d: attempts.append((n, d)),
        )

    assert result == 3
    assert sleeps == [1.0, 2.0]  # doubled after each failure
    assert attempts == [(1, 1.0), (2, 2.0), (3, 4.0)]


async def test_backoff_delay_is_capped() -> None:
    conn = NikobusConnect("host:1234")
    failures = [NikobusConnectionError("x")] * 6 + [None]
    conn.connect = AsyncMock(side_effect=failures)  # type: ignore[method-assign]
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    with patch("asyncio.sleep", new=fake_sleep):
        await conn.reconnect_with_backoff(initial_delay=1.0, max_delay=8.0)

    assert sleeps == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


async def test_backoff_supports_async_on_attempt() -> None:
    conn = NikobusConnect("host:1234")
    conn.connect = AsyncMock(return_value=None)  # type: ignore[method-assign]
    seen: list[int] = []

    async def hook(attempt: int, delay: float) -> None:
        seen.append(attempt)

    await conn.reconnect_with_backoff(on_attempt=hook)
    assert seen == [1]


async def test_backoff_cancellation_propagates() -> None:
    conn = NikobusConnect("host:1234")
    started = asyncio.Event()

    async def hanging_connect() -> None:
        started.set()
        await asyncio.Event().wait()  # block forever

    conn.connect = hanging_connect  # type: ignore[method-assign]
    task = asyncio.create_task(conn.reconnect_with_backoff())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
