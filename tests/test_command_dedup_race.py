"""Command pipeline tests — queue dedup vs caller-side cancellation.

Background: Nikobus-HA #319 (IKIKN install) surfaced a race between
caller-side cancellation and the command queue's dedup mechanism.
Pre-0.5.21 ``detect_stale_inventory`` wrapped each probe in
``asyncio.wait_for(timeout=2.0)``; when that outer cap fired, the
future was cancelled but the dedup key stayed in the set until the
original command was popped — blocking retries via suppression.

0.5.21 fixed this two ways:
1. ``detect_stale_inventory`` no longer wraps in ``asyncio.wait_for``;
   the command pipeline's natural retry budget (``MAX_ATTEMPTS=3``
   × per-attempt timeout) drives the wallclock budget, eliminating
   the outer race entirely.
2. ``get_output_state`` still clears the dedup key on
   ``CancelledError`` / ``TimeoutError`` and ``_process_commands``
   still skips cancelled futures on pop — these handle the case
   where a higher-level cancellation (e.g., task cancelled,
   integration shutdown) interrupts a probe in flight.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from nikobus_connect.command import NikobusCommandHandler


def test_get_output_state_signature_is_positional():
    """0.5.21 stripped the ``timeout`` kwarg from ``get_output_state``.

    The function now waits ``COMMAND_ACK_WAIT_TIMEOUT`` for the
    response future, trusting the command pipeline's internal retry
    budget. Callers wanting a tighter probe-style deadline should
    not wrap this call in ``asyncio.wait_for`` (that pattern was
    the root cause of the Nikobus-HA #319 IKIKN regression).
    """

    sig = inspect.signature(NikobusCommandHandler.get_output_state)
    params = sig.parameters
    assert "timeout" not in params
    # Positional: self, address, group.
    assert list(params.keys()) == ["self", "address", "group"]


@pytest.mark.asyncio
async def test_get_output_state_clears_dedup_on_cancellation():
    """When the caller's task is cancelled mid-probe (not via outer
    timeout — the kwarg is gone — but via a higher-level cancel),
    the dedup key must be cleared so a re-issued probe can re-queue.
    """

    class _StubConnection:
        async def send(self, _cmd):
            pass

    class _StubListener:
        def set_pending_query_group(self, *_):
            pass

    cmd = NikobusCommandHandler.__new__(NikobusCommandHandler)
    cmd._command_queue = asyncio.Queue()
    cmd._queued_get_keys = set()
    cmd._pending_get_futures = {}
    cmd._connection = _StubConnection()
    cmd._listener = _StubListener()
    cmd._module_states = {}
    cmd._running = False

    async def call_and_cancel():
        return await cmd.get_output_state("8110", 1)

    task = asyncio.create_task(call_and_cancel())
    # Give the task a tick to add the dedup key, then cancel.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Dedup key cleared so a retry can re-queue.
    assert "8110_1" not in cmd._queued_get_keys


@pytest.mark.asyncio
async def test_process_commands_skips_cancelled_future():
    """When a queued command's future is already cancelled (caller
    gave up via task cancellation), the processor must skip the
    wire send and clear the dedup key."""

    sends_attempted: list[str] = []

    class _StubConnection:
        async def send(self, command):
            sends_attempted.append(command)

    class _StubListener:
        def set_pending_query_group(self, *_):
            pass

    cmd = NikobusCommandHandler.__new__(NikobusCommandHandler)
    cmd._command_queue = asyncio.Queue()
    cmd._queued_get_keys = {"8110_1"}
    cmd._pending_get_futures = {}
    cmd._connection = _StubConnection()
    cmd._listener = _StubListener()
    cmd._module_states = {}
    cmd._running = True

    loop = asyncio.get_running_loop()
    cancelled_future = loop.create_future()
    cancelled_future.cancel()

    await cmd._command_queue.put(
        {
            "command": "$1012108100AABB",
            "address": "8110",
            "future": cancelled_future,
            "completion_handler": None,
        }
    )

    processor = asyncio.create_task(cmd._process_commands())
    await cmd._command_queue.join()
    processor.cancel()
    try:
        await processor
    except asyncio.CancelledError:
        pass

    # Wire send never happened.
    assert sends_attempted == []
    # Dedup key cleared so a future call can re-queue.
    assert "8110_1" not in cmd._queued_get_keys
