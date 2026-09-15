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
from nikobus_connect.protocol import append_crc1, append_crc2
from nikobus_connect.exceptions import (
    NikobusConnectionError,
    NikobusReadError,
    NikobusSendError,
)


def _stream_pair(probe_reply: bytes | None = b"$0511\r") -> tuple[MagicMock, MagicMock]:
    """Fake reader / writer. The reader answers the presence probe with
    ``probe_reply`` (``None`` = silence: ``readuntil`` never completes
    inside the probe's timeout)."""
    reader = MagicMock()
    if probe_reply is None:
        async def _silent(_sep: bytes) -> bytes:
            await asyncio.sleep(3600)
            return b""

        reader.readuntil = _silent
    else:
        reader.readuntil = AsyncMock(return_value=probe_reply)
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return reader, writer


def _fast_probe():
    """Shrink the probe timeout so the silent case fails quickly."""
    return patch.multiple(
        "nikobus_connect.connection",
        PRESENCE_PROBE_TIMEOUT=0.05,
        PRESENCE_PROBE_ATTEMPTS=2,
        PRESENCE_PROBE_SETTLE=0.0,
    )


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
    # One write per handshake command, plus the ``#A`` presence probe.
    assert writer.write.call_count == len(COMMANDS_HANDSHAKE) + 1
    assert writer.write.call_args_list[-1].args[0] == b"#A\r"


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


async def test_probe_silence_is_a_warning_not_a_failure(caplog) -> None:
    """An open port with nothing answering still connects — some gateways
    (a PC-Logic) have no known answer to the null-address probe — but the
    verdict is recorded and logged so the integration can surface it."""
    conn = NikobusConnect("/dev/ttyUSB9")
    reader, writer = _stream_pair(probe_reply=None)
    with (
        patch(
            "nikobus_connect.connection.serial_asyncio.open_serial_connection",
            new=AsyncMock(return_value=(reader, writer)),
        ),
        patch("asyncio.sleep", new=AsyncMock()),
        _fast_probe(),
    ):
        await conn.connect()
    assert conn.is_connected
    assert conn.device_answered is False
    assert "no Nikobus device answered" in caplog.text


async def test_probe_accepts_any_nikobus_frame_as_presence() -> None:
    """A gateway that never acks the null address but relays bus traffic
    (a feedback frame here) still counts as present."""
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair(probe_reply=b"$1C6C0E00FF00000000009FE944\r")
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    assert conn.device_answered is True


async def test_probe_ignores_garbage_until_a_real_frame() -> None:
    """Line noise before the first well-formed frame is skipped."""
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair()
    reader.readuntil = AsyncMock(
        side_effect=[b"\xff\xfe\r", b"$1\r", b"$0511\r", TimeoutError()]
    )
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    assert conn.device_answered is True
    # Three reads to the ack, one more looking for the identity frame.
    assert reader.readuntil.await_count == 4


async def test_probe_waits_for_the_interface_to_settle() -> None:
    """The first probe goes out only after the settle pause: a PC-Link
    just reset by ``ATZ`` on a cold start swallows frames sent at once."""
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair()
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=record_sleep),
        patch.multiple("nikobus_connect.connection", PRESENCE_PROBE_SETTLE=1.5),
    ):
        await conn.connect()
    # Handshake spacing (0.2 s each), then the settle pause right before
    # the probe goes out.
    assert sleeps[len(COMMANDS_HANDSHAKE)] == 1.5
    assert sleeps[-1] == 1.5


# --- a silent probe is overturned by the first frame -------------------


def test_mark_device_answered_overturns_a_silent_probe() -> None:
    conn = NikobusConnect("/dev/ttyUSB0")
    conn.device_answered = False
    calls: list[int] = []
    conn.on_device_answered = lambda: calls.append(1)
    conn.mark_device_answered("$0511")
    assert conn.device_answered is True
    assert calls == [1]
    # Idempotent: a second frame does not call back again.
    conn.mark_device_answered("#N123456")
    assert calls == [1]


def test_mark_device_answered_is_a_no_op_unless_the_probe_was_silent() -> None:
    for verdict in (None, True):
        conn = NikobusConnect("/dev/ttyUSB0")
        conn.device_answered = verdict
        conn.on_device_answered = MagicMock()
        conn.mark_device_answered("$0511")
        assert conn.device_answered is verdict
        conn.on_device_answered.assert_not_called()


async def test_mark_device_answered_schedules_an_async_callback() -> None:
    conn = NikobusConnect("/dev/ttyUSB0")
    conn.device_answered = False
    done = asyncio.Event()

    async def callback() -> None:
        done.set()

    conn.on_device_answered = callback
    conn.mark_device_answered("$0511")
    await asyncio.wait_for(done.wait(), 1.0)
    assert conn.device_answered is True


# --- gateway identity ----------------------------------------------------


def _status_frame(address_le: str, family: int) -> bytes:
    """A ``$18`` status frame as the gateway answers the null probe."""
    data = f"{address_le}00{family:02X}0C3FFF"
    return (append_crc2(f"$18{append_crc1(data)}") + "\r").encode()


async def test_probe_records_the_gateway_identity_from_the_status_frame() -> None:
    """``#A`` is answered by the PC-Link's own status frame: presence and
    identity in one read (captured: ``$18F58600500F3FFFAC61FE``)."""
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair()
    reader.readuntil = AsyncMock(side_effect=[_status_frame("F586", 0x50)])
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    assert conn.device_answered is True
    assert (conn.gateway_address, conn.gateway_family) == ("86F5", "pc_link")


async def test_probe_identity_from_a_status_frame_glued_to_the_ack() -> None:
    """A feedback module used as gateway, its status right behind the ack."""
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair()
    glued = b"$0511" + _status_frame("6C96", 0xA0)
    reader.readuntil = AsyncMock(side_effect=[glued])
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    assert (conn.gateway_address, conn.gateway_family) == ("966C", "feedback_module")


async def test_probe_without_identity_frame_leaves_gateway_unknown() -> None:
    conn = NikobusConnect("host:1234")
    reader, writer = _stream_pair()
    reader.readuntil = AsyncMock(side_effect=[b"$0511\r", TimeoutError()])
    with (
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await conn.connect()
    assert conn.device_answered is True
    assert conn.gateway_address is None and conn.gateway_family is None
