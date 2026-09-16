"""Set-output requests for one module group become one frame.

Each light / switch entity used to build its own group write the moment
it was called: six lights of one module were six frames 150 ms apart,
six relay clicks, the last five carrying the bytes of the earlier ones
anyway. A request now updates the state buffer and waits in the queue;
the frame is built from the buffer when the request reaches the head,
after a short window in which requests for the same group join it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nikobus_connect import const
from nikobus_connect.command import NikobusCommandHandler


class _Listener:
    def __init__(self) -> None:
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_response = False
        self._awaited_answer = None

    def set_pending_query_group(self, *_args) -> None:
        pass


class _Connection:
    """Acknowledges and answers every set frame as a PC-Link would."""

    def __init__(self, listener: _Listener) -> None:
        self.sent: list[str] = []
        self._listener = listener

    async def send(self, command: str) -> None:
        self.sent.append(command)
        addr_le = command[5:9]
        self._listener.response_queue.put_nowait(f"$05{command[3:5]}")
        self._listener.response_queue.put_nowait(f"$0EFF{addr_le}00")


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.SET_COALESCE_WINDOW", 0.05)
    listener = _Listener()
    connection = _Connection(listener)
    h = NikobusCommandHandler(connection=connection, listener=listener, module_states={})
    h._test_connection = connection  # type: ignore[attr-defined]
    return h


def _group_bytes(frame: str) -> str:
    # $1E 16 F681 <6 bytes> FF crc16 crc8 -> the six state bytes
    return frame[9:21]


async def _drain(handler: NikobusCommandHandler) -> None:
    await asyncio.wait_for(handler._command_queue.join(), 2.0)
    await asyncio.sleep(0.02)


async def test_six_lights_of_one_group_are_one_frame(handler) -> None:
    done = []
    for channel in range(7, 13):
        await handler.set_output_state(
            "81F6", channel, 0xFF, completion_handler=lambda c=channel: done.append(c)
        )
    assert handler._command_queue.qsize() == 1
    await handler.start()
    try:
        await _drain(handler)
    finally:
        await handler.stop()
    sent = handler._test_connection.sent
    assert len(sent) == 1
    assert sent[0].startswith("$1E16F681")
    assert _group_bytes(sent[0]) == "FF" * 6
    assert sorted(done) == list(range(7, 13))


async def test_mixed_on_and_off_and_other_group_stay_correct(handler) -> None:
    await handler.set_output_state("81F6", 7, 0xFF)
    await handler.set_output_state("81F6", 9, 0x00)
    await handler.set_output_state("81F6", 1, 0xFF)   # group 1: its own frame
    await handler.set_output_state("4707", 7, 0xFF)   # other module: its own frame
    await handler.start()
    try:
        await _drain(handler)
    finally:
        await handler.stop()
    sent = handler._test_connection.sent
    assert len(sent) == 3
    assert _group_bytes(sent[0]) == "FF0000000000"      # 81F6 group 2: ch7 on, ch9 off
    assert sent[1].startswith("$1E15F681") and _group_bytes(sent[1]) == "FF0000000000"
    assert sent[2].startswith("$1E160747")


async def test_request_arriving_in_the_window_joins_the_frame(handler) -> None:
    """Sequential callers: the second request lands while the first waits
    at the head of the queue, and rides along."""
    await handler.start()
    try:
        await handler.set_output_state("81F6", 7, 0xFF)
        await asyncio.sleep(0.01)          # inside the 50 ms window
        await handler.set_output_state("81F6", 8, 0xFF)
        await _drain(handler)
    finally:
        await handler.stop()
    sent = handler._test_connection.sent
    assert len(sent) == 1
    assert _group_bytes(sent[0]) == "FFFF00000000"


async def test_request_after_the_window_is_a_second_frame(handler) -> None:
    """A change made once the frame is built is a later change."""
    await handler.start()
    try:
        await handler.set_output_state("81F6", 7, 0xFF)
        await asyncio.sleep(0.12)          # window over, frame on the wire
        await handler.set_output_state("81F6", 7, 0x00)
        await _drain(handler)
    finally:
        await handler.stop()
    sent = handler._test_connection.sent
    assert len(sent) == 2
    assert _group_bytes(sent[0]) == "FF0000000000"
    assert _group_bytes(sent[1]) == "000000000000"


async def test_drained_request_does_not_swallow_the_next_one(handler) -> None:
    await handler.set_output_state("81F6", 7, 0xFF)
    assert handler.drain_queue() == 1
    assert handler._pending_set_groups == {}
    await handler.set_output_state("81F6", 8, 0xFF)
    assert handler._command_queue.qsize() == 1
    handler.reset()
    assert handler._pending_set_groups == {}


async def test_single_request_is_unchanged(handler) -> None:
    await handler.set_output_state("0E6C", 1, 0x73)
    await handler.start()
    try:
        await _drain(handler)
    finally:
        await handler.stop()
    sent = handler._test_connection.sent
    assert sent == [sent[0]] and sent[0].startswith("$1E156C0E") and _group_bytes(sent[0]) == "730000000000"


def test_module_states_are_shared_with_the_caller() -> None:
    states: dict = {}
    h = NikobusCommandHandler(connection=MagicMock(), listener=MagicMock(), module_states=states)
    h.set_bytearray_state("81F6", 7, 0xFF)
    assert states["81F6"][6] == 0xFF
