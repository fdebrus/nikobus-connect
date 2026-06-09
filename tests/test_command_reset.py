"""Command-handler ``reset()`` tests — post-reconnect state clearing."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from nikobus_connect.command import NikobusCommandHandler


def _handler() -> NikobusCommandHandler:
    return NikobusCommandHandler(
        connection=MagicMock(), listener=MagicMock(), module_states={}
    )


async def test_reset_drains_queue_and_cancels_futures() -> None:
    handler = _handler()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    done_fut: asyncio.Future[str] = loop.create_future()
    done_fut.set_result("already-done")
    handler._command_queue.put_nowait({"command": "$10120000", "future": fut})
    handler._command_queue.put_nowait({"command": "$1012FFFF", "future": done_fut})
    handler._command_queue.put_nowait({"command": "#N123456"})  # no future
    handler._queued_get_keys.add("0000_1")

    handler.reset()

    assert handler._command_queue.empty()
    assert fut.cancelled()
    assert done_fut.result() == "already-done"  # untouched
    assert handler._queued_get_keys == set()


async def test_drain_queue_returns_discard_count() -> None:
    handler = _handler()
    for i in range(3):
        handler._command_queue.put_nowait({"command": f"cmd{i}"})
    assert handler.drain_queue() == 3
    assert handler.drain_queue() == 0
