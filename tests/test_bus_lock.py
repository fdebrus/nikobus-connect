"""Bus lock: one request/response exchange on the PC-Link at a time.

The command queue serialises the commands that go through it, but the
discovery register scan writes on the connection directly. Without a
shared lock a coordinator poll (or a user's switch) can go out while a
register read is still waiting for its answer; the two exchanges garble
each other on the bus (issue #502: the reply to register 0x10 arrived
merged with a poll reply, failed its CRC, and the switch module's link
table was discarded as corrupt because it appeared to start at 0x11).

These tests pin both sides of the contract:

1. the queue holds ``bus_lock`` for the whole send + wait of a command;
2. the register scan waits for ``bus_lock`` before sending a read.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nikobus_connect import const
from nikobus_connect.command import NikobusCommandHandler
from nikobus_connect.discovery.discovery import NikobusDiscovery


# --- 1. queued command holds the lock while it waits for its answer ------


async def test_queued_command_holds_bus_lock_until_answered(monkeypatch) -> None:
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    handler = NikobusCommandHandler(
        connection=MagicMock(), listener=MagicMock(), module_states={}
    )
    started = asyncio.Event()
    release = asyncio.Event()
    locked_during_send: list[bool] = []

    async def fake_send_get_answer(command: str, address: str) -> str:
        locked_during_send.append(handler.bus_lock.locked())
        started.set()
        await release.wait()
        return "000000000000"

    handler._send_command_get_answer = fake_send_get_answer  # type: ignore[method-assign]
    await handler.start()
    try:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        handler._command_queue.put_nowait(
            {"command": "$10120747402BFC", "address": "4707", "future": fut}
        )
        await asyncio.wait_for(started.wait(), 1.0)

        # The exchange is in flight: the lock is held and a second
        # bus user cannot get it.
        assert locked_during_send == [True]
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(handler.bus_lock.acquire(), 0.05)

        release.set()
        assert await asyncio.wait_for(fut, 1.0) == "000000000000"
        # Released once the answer is in.
        await asyncio.wait_for(handler.bus_lock.acquire(), 1.0)
        handler.bus_lock.release()
    finally:
        await handler.stop()


# --- 2. register scan waits for the lock before sending ------------------


class _FakeListener:
    def __init__(self) -> None:
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_response = False


class _FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.on_send = None

    async def send(self, command: str) -> None:
        self.sent.append(command)
        if self.on_send is not None:
            await self.on_send(command)


class _FakeCommand:
    def __init__(self) -> None:
        self._listener = _FakeListener()
        self._connection = _FakeConnection()
        self.bus_lock = asyncio.Lock()


class _FakeCoordinator:
    def __init__(self) -> None:
        self.nikobus_command = _FakeCommand()
        self.dict_module_data: dict = {}
        self.discovery_running = False
        self.discovery_module = False
        self.discovery_module_address: str | None = None
        self.inventory_query_type = None


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    task = MagicMock()
    task.cancel = MagicMock()
    return task


async def test_register_scan_waits_for_bus_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    coord = _FakeCoordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )
    coord.discovery_module = True
    command = coord.nikobus_command
    listener = command._listener
    connection = command._connection

    async def ack(cmd: str) -> None:
        listener.response_queue.put_nowait(f"$05{cmd[3:5]}")

    connection.on_send = ack

    # Another bus user (a queued poll) holds the lock: the scan must not
    # send a single register read until it is released.
    await command.bus_lock.acquire()
    scan = asyncio.create_task(
        discovery._scan_module_registers("4707", "100747", range(0x10, 0x13))
    )
    await asyncio.sleep(0.1)
    assert connection.sent == []

    command.bus_lock.release()
    await asyncio.wait_for(scan, 5.0)
    assert len(connection.sent) == 3
    assert not command.bus_lock.locked()
