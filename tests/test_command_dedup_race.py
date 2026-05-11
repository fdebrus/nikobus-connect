"""Command pipeline tests — queue dedup vs caller-side timeout.

Bug-2 fix (Nikobus-HA #319, 0.5.20): the IKIKN install trace showed
that ``detect_stale_inventory``'s outer ``asyncio.wait_for(timeout=2.0)``
around ``get_output_state`` raced badly with the command pipeline's
dedup mechanism:

  T=0    detect_stale_inventory → get_output_state("1CEC", group=1)
         → creates F1, queue_command adds "1CEC_1" to dedup set,
           command queued
         → wait_for(F1, timeout=2.0)
  T=2    outer timeout fires, F1 cancelled
         → BUT dedup key still set (cmd never popped — slow probe
           ahead in queue)
  T=2.5  retry → get_output_state again
         → creates F2, queue_command sees "1CEC_1" → SUPPRESS
         → F2 never resolves → false-negative absent verdict

Fix:
  (1) ``get_output_state`` accepts ``timeout`` kwarg; its except
      branch clears the dedup key so retries re-queue cleanly.
  (2) ``_process_commands`` checks ``future.cancelled()`` before
      sending — stale commands are discarded on pop, not wired.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from nikobus_connect.command import NikobusCommandHandler


def test_get_output_state_accepts_timeout_kwarg():
    """API surface: 0.5.20 added a keyword-only ``timeout`` argument.

    Defaults to ``None`` (meaning: use library
    ``COMMAND_ACK_WAIT_TIMEOUT``). Callers can pass any float to
    override.
    """

    sig = inspect.signature(NikobusCommandHandler.get_output_state)
    timeout_param = sig.parameters["timeout"]
    assert timeout_param.default is None
    assert timeout_param.kind == inspect.Parameter.KEYWORD_ONLY


@pytest.mark.asyncio
async def test_get_output_state_clears_dedup_on_timeout(monkeypatch):
    """Bug-2 fix pin: when ``get_output_state``'s wait times out,
    the dedup key MUST be discarded so subsequent calls for the
    same address re-queue cleanly. Pre-0.5.20 the key stayed in
    the set until the original (stale) command was popped, blocking
    retries via suppression."""

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

    # Call with a tight timeout — the future never resolves because
    # nothing processes the queue, so wait_for fires.
    with pytest.raises(asyncio.TimeoutError):
        await cmd.get_output_state("8110", 1, timeout=0.01)

    # Dedup key must be clear so a retry can re-queue.
    assert "8110_1" not in cmd._queued_get_keys


@pytest.mark.asyncio
async def test_process_commands_skips_cancelled_future():
    """Bug-2 fix pin: when a queued command's future is already
    cancelled (caller gave up via outer timeout), the processor
    must skip the wire send and clear the dedup key — not waste
    bus time on a result nobody's waiting for."""

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

    # Enqueue a $1012 command with a pre-cancelled future. The
    # processor should pop, see future cancelled, discard dedup,
    # and skip the wire send.
    loop = asyncio.get_running_loop()
    cancelled_future = loop.create_future()
    cancelled_future.cancel()

    await cmd._command_queue.put(
        {
            "command": "$1012108100AABB",  # gid=12, address=8110
            "address": "8110",
            "future": cancelled_future,
            "completion_handler": None,
        }
    )

    # Run the processor for just long enough to drain the one
    # queued item, then cancel it.
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
