"""Command-handler hygiene found by the 0.56.1 audit.

* A caller cancelling the future a set returns (``asyncio.wait_for`` on
  a timeout) used to cancel the shared item; the worker skipped it while
  it stayed registered as the group's pending write, and every later
  request for that group joined a frame never sent.
* ``stop()`` cancelled only state-read futures: set and query waiters
  hung until their own timeout, and a later ``start()`` replayed stale
  frames.
* The read made before a stale group write used the full three attempts.
* A ``$1C`` frame pushed for the module's other group just before our ack
  was held and could be returned as our answer.
"""

from __future__ import annotations

import asyncio

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


class _Silent:
    """A connection that never answers anything."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, command: str) -> None:
        self.sent.append(command)


# ---------------------------------------------------------------------------
# A caller's cancel does not kill the group
# ---------------------------------------------------------------------------


async def test_a_cancelled_caller_future_does_not_block_later_writes(handler) -> None:
    await handler.start()
    try:
        first = await handler.set_output_state("81F6", 7, 0xFF)
        first.cancel()  # what asyncio.wait_for does on a timeout
        second = await handler.set_output_state("81F6", 8, 0xFF)
        await _drain(handler)
        assert second.done() and not second.cancelled()
    finally:
        await handler.stop()
    sent = handler._test_connection.sent
    assert len(sent) == 1
    assert _group_bytes(sent[0]) == "FFFF00000000"  # both writes went out
    assert handler._pending_set_groups == {}


async def test_joiners_follow_the_shared_exchange_but_own_their_future(handler) -> None:
    await handler.start()
    try:
        a = await handler.set_output_state("81F6", 7, 0xFF)
        b = await handler.set_output_state("81F6", 8, 0xFF)
        assert a is not b
        await _drain(handler)
    finally:
        await handler.stop()
    assert a.result() == b.result() == ""


async def test_a_cancelled_shared_item_is_unregistered_when_skipped(handler) -> None:
    """The shared future itself only gets cancelled by a drain; if it ever is
    while still queued, the worker must free the group when it skips it."""
    await handler.set_output_state("81F6", 7, 0xFF)
    item = handler._pending_set_groups["81F6_2"]
    item["future"].cancel()
    await handler.start()
    try:
        await asyncio.wait_for(handler._command_queue.join(), 2.0)
        await asyncio.sleep(0.02)
    finally:
        await handler.stop()
    assert handler._pending_set_groups == {}


# ---------------------------------------------------------------------------
# stop() releases every waiter
# ---------------------------------------------------------------------------


async def test_stop_releases_queued_set_and_query_waiters(monkeypatch) -> None:
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)
    listener = _Listener()
    h = NikobusCommandHandler(connection=_Silent(), listener=listener, module_states={})
    set_future = await h.set_output_state("1234", 1, 0xFF)
    query_task = asyncio.ensure_future(h.query(0x11, "1234"))
    await asyncio.sleep(0.01)
    await h.stop()
    await asyncio.sleep(0.01)
    assert set_future.cancelled()
    assert query_task.done() and query_task.cancelled() or isinstance(query_task.exception(), asyncio.CancelledError)
    assert h._pending_set_groups == {} and h._writing_sets == {}
    assert h._command_queue.empty()


async def test_stop_cancels_the_in_flight_exchange(monkeypatch) -> None:
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.SET_COALESCE_WINDOW", 0.0)
    listener = _Listener()
    h = NikobusCommandHandler(connection=_Silent(), listener=listener, module_states={})
    import time
    h._group_known_at[("1234", 1)] = time.monotonic()  # fresh: no pre-read, straight to the write
    await h.start()
    fut = await h.set_output_state("1234", 1, 0xFF)
    await asyncio.sleep(0.1)  # the write is on the wire, waiting for an ack
    await h.stop()
    await asyncio.sleep(0.01)
    assert fut.cancelled()


# ---------------------------------------------------------------------------
# The pre-write read is one attempt
# ---------------------------------------------------------------------------


async def test_the_read_before_a_stale_write_is_a_single_attempt(handler, monkeypatch) -> None:
    seen: list[int] = []
    original = handler._send_command_get_answer

    async def _spy(command, address, attempts=3):
        seen.append(attempts)
        return await original(command, address, attempts=attempts)

    monkeypatch.setattr(handler, "_send_command_get_answer", _spy)
    await handler.set_output_state("81F6", 7, 0xFF)
    await handler.start()
    try:
        await _drain(handler)
    finally:
        await handler.stop()
    assert seen == [1, 3]  # the read before, then the write itself


# ---------------------------------------------------------------------------
# A frame pushed for the other group is not our answer
# ---------------------------------------------------------------------------


@pytest.fixture
def quick(monkeypatch):
    for name in ("COMMAND_ATTEMPT_TIMEOUT", "COMMAND_ANSWER_WAIT_TIMEOUT", "COMMAND_POST_ACK_ANSWER_TIMEOUT"):
        monkeypatch.setattr(f"nikobus_connect.command.{name}", 0.05)


async def _wait(h, listener, frames, **kw):
    for f in frames:
        listener.response_queue.put_nowait(f)
    return await h._wait_for_ack_and_answer_state("$0512", "$1C3412", **kw)


async def test_a_pre_ack_frame_of_the_other_group_is_discarded(quick) -> None:
    listener = _Listener()
    listener._last_query_group = {"1234": 2}  # the Feedback Module just queried group 2
    h = NikobusCommandHandler(connection=_Silent(), listener=listener, module_states={})
    assert await _wait(h, listener, ["$1C341200AABBCCDDEEFF0000", "$0512"]) is None


async def test_a_pre_ack_frame_of_our_group_still_stands_in(quick) -> None:
    listener = _Listener()
    listener._last_query_group = {"1234": 1}
    h = NikobusCommandHandler(connection=_Silent(), listener=listener, module_states={})
    assert await _wait(h, listener, ["$1C341200AABBCCDDEEFF0000", "$0512"]) == "AABBCCDDEEFF"


async def test_a_best_effort_read_never_uses_a_held_frame(quick) -> None:
    listener = _Listener()
    h = NikobusCommandHandler(connection=_Silent(), listener=listener, module_states={})
    assert await _wait(h, listener, ["$1C341200AABBCCDDEEFF0000", "$0512"], use_held=False) is None


async def test_a_short_held_frame_is_no_answer(quick) -> None:
    listener = _Listener()
    h = NikobusCommandHandler(connection=_Silent(), listener=listener, module_states={})
    assert await _wait(h, listener, ["$1C3412", "$0512"]) is None
