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


async def test_drain_queue_prefix_drains_only_matching_commands() -> None:
    """The prefix filter (0.33.0, registry early-stop) drains one
    PC-Link's register reads while leaving unrelated queued commands —
    e.g. a user's light toggle queued mid-scan — in their original
    order."""
    handler = _handler()
    loop = asyncio.get_running_loop()
    inv_fut: asyncio.Future[str] = loop.create_future()
    handler._command_queue.put_nowait(
        {"command": "$1410C798BC04AABBCC", "future": inv_fut}
    )
    handler._command_queue.put_nowait({"command": "$1512340100FFDDEE"})  # user cmd
    handler._command_queue.put_nowait({"command": "$1410C798BD04AABBCC"})
    handler._command_queue.put_nowait({"command": "$14105599A004AABBCC"})  # other addr

    assert handler.drain_queue(prefix="$1410C798") == 2
    assert inv_fut.cancelled()

    remaining = []
    while not handler._command_queue.empty():
        remaining.append(handler._command_queue.get_nowait()["command"])
    assert remaining == ["$1512340100FFDDEE", "$14105599A004AABBCC"]


async def test_drain_queue_without_prefix_still_drains_everything() -> None:
    handler = _handler()
    handler._command_queue.put_nowait({"command": "$1410C798BC04AABBCC"})
    handler._command_queue.put_nowait({"command": "$1512340100FFDDEE"})
    assert handler.drain_queue() == 2
    assert handler._command_queue.empty()
