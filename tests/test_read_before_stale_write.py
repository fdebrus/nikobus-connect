"""A group whose state is not recent is read before it is written.

A switch or dimmer module is written six outputs at a time, from the
host's buffer. On a real install (Nikobus-HA #557) a kitchen light
switched off at the wall was switched back on when, seconds later, an
automation switched on another output of the same group: the host had
not heard of the wall press yet. The queue worker now reads the group
first when its state is older than ``SET_READ_BEFORE_WRITE_S`` or
unknown, and the answer is applied with the pending writes kept on top.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from nikobus_connect import const
from nikobus_connect.command import NikobusCommandHandler
from tests.test_set_coalescing import _Connection, _drain, _group_bytes, _Listener


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.SET_COALESCE_WINDOW", 0.02)
    listener = _Listener()
    connection = _Connection(listener)
    h = NikobusCommandHandler(connection=connection, listener=listener, module_states={})
    h._test_connection = connection  # type: ignore[attr-defined]
    return h


class _WallChanged(_Connection):
    """The module answers a read with channel 1 off: a wall press the host missed."""

    async def send(self, command: str) -> None:
        gid, addr_le = command[3:5], command[5:9]
        if gid in ("12", "17"):
            self.reads.append(command)
            self._listener.response_queue.put_nowait(f"$05{gid}")
            self._listener.response_queue.put_nowait(f"$1C{addr_le}0000{'00' * 6}0000")
            return
        await super().send(command)


async def _run(handler, *requests) -> None:
    for address, channel, value in requests:
        await handler.set_output_state(address, channel, value)
    await handler.start()
    try:
        await _drain(handler)
    finally:
        await handler.stop()


async def test_a_group_never_heard_is_read_first_and_pending_writes_win(handler) -> None:
    # The host believes channel 1 is on (stale); the wall switched it off.
    handler.set_bytearray_state("CF58", 1, 0xFF)
    handler._test_connection.__class__ = _WallChanged
    await _run(handler, ("CF58", 5, 0xFF))
    conn = handler._test_connection
    assert len(conn.reads) == 1 and conn.reads[0].startswith("$1012")
    assert len(conn.sent) == 1
    assert _group_bytes(conn.sent[0]) == "000000" + "00FF00"  # ch 1 off as read, ch 5 on as asked


async def test_a_group_heard_recently_is_not_read(handler) -> None:
    handler._group_known_at[("CF58", 1)] = time.monotonic()
    await _run(handler, ("CF58", 5, 0xFF))
    assert handler._test_connection.reads == []
    assert len(handler._test_connection.sent) == 1


async def test_an_old_state_is_read_again(handler, monkeypatch) -> None:
    monkeypatch.setattr("nikobus_connect.command.SET_READ_BEFORE_WRITE_S", 1.0)
    handler._group_known_at[("CF58", 1)] = time.monotonic() - 5
    await _run(handler, ("CF58", 5, 0xFF))
    assert len(handler._test_connection.reads) == 1


async def test_our_own_acknowledged_write_counts_as_recent(handler) -> None:
    await handler.start()
    try:
        await handler.set_output_state("CF58", 5, 0xFF)
        await _drain(handler)
        await handler.set_output_state("CF58", 3, 0xFF)  # right after: no read
        await _drain(handler)
    finally:
        await handler.stop()
    conn = handler._test_connection
    assert len(conn.reads) == 1 and len(conn.sent) == 2
    assert _group_bytes(conn.sent[1]) == "0000FF00FF00"


async def test_a_failed_read_does_not_stop_the_write(handler, monkeypatch) -> None:
    async def _fail(command, address):
        raise TimeoutError("silent")

    monkeypatch.setattr(handler, "_send_command_get_answer", _fail)
    handler.set_bytearray_state("CF58", 1, 0xFF)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)
    await handler.set_output_state("CF58", 5, 0xFF)
    # The worker's write goes through the same stub and fails too; only the
    # read's failure is under test: the buffer keeps the host's state.
    await handler.start()
    try:
        await asyncio.wait_for(handler._command_queue.join(), 2.0)
    finally:
        await handler.stop()
    assert handler.get_bytearray_group_state("CF58", 1) == bytearray([0xFF, 0, 0, 0, 0xFF, 0])


def test_apply_group_state_keeps_writes_not_yet_made(handler) -> None:
    handler._pending_set_groups["CF58_1"] = {"channels": {5: 0xFF}}
    assert handler.apply_group_state("cf58", 1, "FF0000000000")
    assert handler.get_bytearray_group_state("CF58", 1) == bytearray([0xFF, 0, 0, 0, 0xFF, 0])
    assert handler.group_state_age("CF58", 1) is not None
    assert handler.group_state_age("CF58", 1) < 1.0
    assert not handler.apply_group_state("CF58", 1, "zz")
    assert not handler.apply_group_state("CF58", 1, "FF00")


def test_a_pushed_answer_marks_the_group_recent(handler) -> None:
    assert handler.group_state_age("4707", 2) is None
    handler.resolve_pending_get("4707", 2, "00" * 6)
    assert handler.group_state_age("4707", 2) is not None
