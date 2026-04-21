"""Tests for the ``on_progress`` callback.

``NikobusDiscovery`` fires progress events at five phase transitions:

    inventory       — PC-Link #A enumeration started
    identity        — per-address device_type queries queued
    register_scan   — per-module (once when scan starts for a module,
                      again after each register read with ``register``
                      populated)
    finalizing      — final sweep / discovery finished

The callback signature accepts a :class:`DiscoveryProgress` snapshot.
Exceptions raised by the callback must be swallowed so a misbehaving
downstream tracker can't abort the scan.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect import const
from nikobus_connect.discovery import (
    DiscoveryProgress,
    PHASE_FINALIZING,
    PHASE_REGISTER_SCAN,
)
from nikobus_connect.discovery.discovery import NikobusDiscovery


# --- fakes (parallel to test_sequential_register_scan) ---


class FakeListener:
    def __init__(self) -> None:
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_response: bool = False


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.on_send = None

    async def send(self, command: str) -> None:
        self.sent.append(command)
        if self.on_send is not None:
            await self.on_send(command)


class FakeCommand:
    def __init__(self) -> None:
        self._listener = FakeListener()
        self._connection = FakeConnection()


class FakeCoordinator:
    def __init__(self) -> None:
        self.nikobus_command = FakeCommand()
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


def _make_discovery(tmp_path, on_progress):
    coord = FakeCoordinator()
    return NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
        on_progress=on_progress,
    )


def _ack_for(command: str) -> str:
    return f"$05{command[3:5]}"


def _register_from_command(command: str) -> int:
    return int(command[9:11], 16)


# --- tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_fires_through_register_scan(tmp_path, monkeypatch):
    """Running ``_scan_module_registers`` then ``_complete_discovery_run``
    emits register_scan (per register) and a finalizing event with the
    expected counters."""

    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    events: list[DiscoveryProgress] = []

    async def on_progress(progress: DiscoveryProgress) -> None:
        events.append(progress)

    discovery = _make_discovery(tmp_path, on_progress)
    discovery._coordinator.discovery_module = True
    # Simulate the ALL path setup.
    discovery._progress_module_total = 1
    discovery._progress_module_index = 1

    async def auto_ack(command: str) -> None:
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)

    discovery._coordinator.nikobus_command._connection.on_send = auto_ack

    await discovery._scan_module_registers("4707", "100747", range(0x10, 0x13))
    await discovery._complete_discovery_run("4707")

    # register_scan events: one per register read, all matching module.
    reg_events = [e for e in events if e.phase == PHASE_REGISTER_SCAN]
    assert len(reg_events) == 3
    assert [e.register for e in reg_events] == [0x10, 0x11, 0x12]
    for e in reg_events:
        assert e.module_address == "4707"
        assert e.module_index == 1
        assert e.module_total == 1
        assert e.register_total == 3  # matches range length

    # Last event is finalizing.
    assert events[-1].phase == PHASE_FINALIZING


@pytest.mark.asyncio
async def test_progress_register_total_drops_on_trailer(tmp_path, monkeypatch):
    """When a ``$18`` trailer short-circuits the loop, ``register_total``
    on the next emit reflects ``registers_sent`` rather than the full
    range length — callers using it for a progress bar see 100% at the
    break, not a truncated percentage."""

    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    events: list[DiscoveryProgress] = []

    async def on_progress(progress: DiscoveryProgress) -> None:
        events.append(progress)

    discovery = _make_discovery(tmp_path, on_progress)
    discovery._coordinator.discovery_module = True
    discovery._progress_module_total = 1
    discovery._progress_module_index = 1

    async def on_send(command: str) -> None:
        reg = _register_from_command(command)
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)
        if reg == 0x12:
            await discovery.parse_module_inventory_response(
                "$18FFFFFFFFFFFFFFBF9558"
            )

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    await discovery._scan_module_registers(
        "4707", "100747", range(0x10, 0x100)
    )
    await discovery._complete_discovery_run("4707")

    # Final finalizing event carries the reduced register_total.
    final = events[-1]
    assert final.phase == PHASE_FINALIZING
    # We sent up to 0x12 inclusive (0x10, 0x11, 0x12) and then
    # trailer-short-circuited on 0x13. register_total drops to sent count.
    assert final.register_total == 3


@pytest.mark.asyncio
async def test_progress_callback_exception_does_not_abort_scan(
    tmp_path, monkeypatch
):
    """A raising callback is logged and swallowed; the scan completes."""

    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    calls = 0

    async def on_progress(progress: DiscoveryProgress) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("downstream tracker blew up")

    discovery = _make_discovery(tmp_path, on_progress)
    discovery._coordinator.discovery_module = True
    discovery._progress_module_total = 1
    discovery._progress_module_index = 1

    async def auto_ack(command: str) -> None:
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)

    discovery._coordinator.nikobus_command._connection.on_send = auto_ack

    # Must run to completion without raising.
    await discovery._scan_module_registers("4707", "100747", range(0x10, 0x12))
    await discovery._complete_discovery_run("4707")

    # At minimum: one per register (2) plus the finalizing event.
    assert calls >= 3


@pytest.mark.asyncio
async def test_progress_callback_accepts_sync_function(tmp_path, monkeypatch):
    """A plain (non-async) callable is invoked directly — downstream
    integrations that just want to write to a local variable shouldn't
    be forced to wrap in an async def."""

    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    events: list[DiscoveryProgress] = []

    def on_progress(progress: DiscoveryProgress) -> None:
        events.append(progress)

    discovery = _make_discovery(tmp_path, on_progress)
    discovery._coordinator.discovery_module = True
    discovery._progress_module_total = 1
    discovery._progress_module_index = 1

    async def auto_ack(command: str) -> None:
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)

    discovery._coordinator.nikobus_command._connection.on_send = auto_ack

    await discovery._scan_module_registers("4707", "100747", range(0x10, 0x12))
    assert any(e.phase == PHASE_REGISTER_SCAN for e in events)


@pytest.mark.asyncio
async def test_progress_with_no_callback_is_silent(tmp_path, monkeypatch):
    """Not supplying ``on_progress`` is valid — the discovery runs
    unchanged."""

    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    discovery = _make_discovery(tmp_path, None)
    discovery._coordinator.discovery_module = True

    async def auto_ack(command: str) -> None:
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)

    discovery._coordinator.nikobus_command._connection.on_send = auto_ack

    # Must not raise.
    await discovery._scan_module_registers("4707", "100747", range(0x10, 0x12))


def test_discovery_progress_dataclass_defaults():
    p = DiscoveryProgress(phase=PHASE_REGISTER_SCAN)
    assert p.phase == "register_scan"
    assert p.module_address is None
    assert p.module_index == 0
    assert p.module_total == 0
    assert p.register is None
    assert p.register_total == 0
    assert p.decoded_records == 0
