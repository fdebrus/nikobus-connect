"""Regressions from the 2026-10-01 command-layer audit.

Three defects, each reproduced against the real handler with a fake
PC-Link: a set-output lost to a state read landing on the shared
buffer, a deduplicated GET whose caller waited out the full timeout,
and a reconnect that left the previous transport open.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import nikobus_connect.connection as connection_module
from nikobus_connect.command import NikobusCommandHandler
from nikobus_connect.connection import NikobusConnect


class _Listener:
    def __init__(self) -> None:
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_response = False
        self._awaited_answer = None

    def set_pending_query_group(self, *_args) -> None:
        pass


class _Connection:
    """Acknowledges every frame; answers a GET with "all outputs off"."""

    def __init__(self, listener: _Listener) -> None:
        self.sent: list[str] = []
        self._listener = listener

    async def send(self, command: str) -> None:
        self.sent.append(command)
        gid = command[3:5]
        addr_le = command[5:9]
        self._listener.response_queue.put_nowait(f"$05{gid}")
        if gid in ("12", "17"):
            self._listener.response_queue.put_nowait(
                "$1C" + addr_le + "00" * 6 + "000000"
            )
        else:
            self._listener.response_queue.put_nowait(f"$0EFF{addr_le}00")


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.SET_COALESCE_WINDOW", 0.02)
    listener = _Listener()
    connection = _Connection(listener)
    shared = {"81F6": bytearray(12)}
    h = NikobusCommandHandler(connection=connection, listener=listener, module_states=shared)
    h._parse_state_from_message = lambda _m, _s: "00" * 6  # type: ignore[method-assign]
    h._test_connection = connection  # type: ignore[attr-defined]
    h._test_shared = shared  # type: ignore[attr-defined]
    return h


async def _drain(handler: NikobusCommandHandler) -> None:
    await asyncio.wait_for(handler._command_queue.join(), 2.0)
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_a_state_read_landing_on_the_buffer_does_not_lose_a_set(handler) -> None:
    """Poll GET in flight, user turns channel 1 on, module answers "all
    off" and the host writes that into the shared buffer: the frame built
    afterwards must still carry channel 1 on."""
    shared = handler._test_shared
    await handler.start()
    try:
        poll = asyncio.create_task(handler.get_output_state("81F6", 1))
        await asyncio.sleep(0.005)
        await handler.set_output_state("81F6", 1, 0xFF)
        assert shared["81F6"][0] == 0xFF
        state = await poll
        shared["81F6"][0:6] = bytes.fromhex(state)  # what the coordinator does
        await _drain(handler)
    finally:
        await handler.stop()
    set_frames = [f for f in handler._test_connection.sent if f[3:5] == "15"]
    assert len(set_frames) == 1
    assert set_frames[0][9:21] == "FF0000000000"


@pytest.mark.asyncio
async def test_a_joiner_write_survives_a_state_read_too(handler) -> None:
    shared = handler._test_shared
    await handler.start()
    try:
        poll = asyncio.create_task(handler.get_output_state("81F6", 1))
        await asyncio.sleep(0.005)
        await handler.set_output_state("81F6", 1, 0xFF)
        await handler.set_output_state("81F6", 3, 0xFF)  # joins the pending write
        state = await poll
        shared["81F6"][0:6] = bytes.fromhex(state)
        await _drain(handler)
    finally:
        await handler.stop()
    set_frames = [f for f in handler._test_connection.sent if f[3:5] == "15"]
    assert [f[9:21] for f in set_frames] == ["FF00FF000000"]


@pytest.mark.asyncio
async def test_a_deduplicated_get_is_answered_by_the_first_exchange(handler) -> None:
    # Both callers queue before the worker runs, so the second GET is
    # the duplicate the queue folds into the first.
    first = asyncio.create_task(handler.get_output_state("81F6", 1))
    await asyncio.sleep(0)
    second = asyncio.create_task(handler.get_output_state("81F6", 1))
    await asyncio.sleep(0)
    assert handler._command_queue.qsize() == 1
    await handler.start()
    try:
        results = await asyncio.wait_for(asyncio.gather(first, second), 2.0)
    finally:
        await handler.stop()
    assert results == ["00" * 6, "00" * 6]
    assert len([f for f in handler._test_connection.sent if f[3:5] == "12"]) == 1
    assert handler._pending_get_futures == {}
    assert handler._queued_get_keys == set()


@pytest.mark.asyncio
async def test_the_feedback_fast_path_answers_every_waiter(handler) -> None:
    loop = asyncio.get_running_loop()
    futures = [loop.create_future(), loop.create_future()]
    handler._pending_get_futures["81F6_1"] = list(futures)
    handler.resolve_pending_get("81f6", 1, "FF" * 6)
    assert [f.result() for f in futures] == ["FF" * 6, "FF" * 6]


@pytest.mark.asyncio
async def test_answer_matching_ignores_the_address_case(handler) -> None:
    await handler.start()
    try:
        state = await asyncio.wait_for(handler.get_output_state("81f6", 1), 2.0)
    finally:
        await handler.stop()
    assert state == "00" * 6


@pytest.mark.asyncio
async def test_connect_closes_the_previous_transport_first(monkeypatch) -> None:
    old_writer = MagicMock()
    old_writer.close = MagicMock()
    old_writer.wait_closed = AsyncMock()
    new_reader, new_writer = MagicMock(), MagicMock()
    opened = AsyncMock(return_value=(new_reader, new_writer))
    monkeypatch.setattr(connection_module.asyncio, "open_connection", opened)

    conn = NikobusConnect("192.0.2.1:9999")
    conn._reader, conn._writer = MagicMock(), old_writer
    conn._is_connected = True
    conn._handshake = AsyncMock()  # type: ignore[method-assign]
    conn._probe = AsyncMock()  # type: ignore[method-assign]

    await conn.connect()

    old_writer.close.assert_called_once()
    old_writer.wait_closed.assert_awaited_once()
    assert conn._writer is new_writer
    assert conn.is_connected
