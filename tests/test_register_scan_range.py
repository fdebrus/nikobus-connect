"""Regression test: the default module register-scan range covers 0x00..0xFF.

Legacy code started at 0x10, which silently skipped 16 registers that
real hardware can store link records in. Confirmed by a user report
where a 4-key button had 1A/1B link records sitting in 0x00..0x0F that
never surfaced through discovery. This test pins the range so the
regression can't come back.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.discovery.discovery import NikobusDiscovery


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    task = MagicMock()
    task.cancel = MagicMock()
    return task


def _make_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = True  # skip the outer "start fresh" branch
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    coord.get_module_channel_count = MagicMock(return_value=12)
    return coord


@pytest.mark.asyncio
async def test_default_scan_range_starts_at_zero_for_output_module(tmp_path):
    """When ``query_module_inventory`` triggers the register scan for a
    switch module, the command_range handed to ``_scan_module_registers``
    starts at 0x00 and covers the full 0x00..0xFF space (256 registers)."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    # A known switch module in the discovered inventory.
    discovery.discovered_devices = {
        "4707": {
            "address": "4707",
            "category": "Module",
            "model": "05-000-02",
            "channels": 12,
            "device_type": "01",
        }
    }
    # Coordinator recognises this address.
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="switch_module")

    captured: dict = {}

    async def fake_scan(address, base_cmd, command_range):
        captured["address"] = address
        captured["base_cmd"] = base_cmd
        captured["command_range"] = command_range

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("4707")

    assert "command_range" in captured, "register scan was never invoked"
    scan_range = captured["command_range"]

    # Full register space, starting at 0x00.
    assert scan_range.start == 0x00
    assert scan_range.stop == 0x100
    assert len(scan_range) == 256

    # Sanity: low registers are included.
    assert 0x00 in scan_range
    assert 0x0F in scan_range


@pytest.mark.asyncio
async def test_default_scan_range_starts_at_zero_for_dimmer_module(tmp_path):
    """Same coverage guarantee on the dimmer-module path (different
    base_command prefix, same register range)."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "0E6C": {
            "address": "0E6C",
            "category": "Module",
            "model": "05-007-02",
            "channels": 12,
            "device_type": "03",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="dimmer_module")

    captured: dict = {}

    async def fake_scan(address, base_cmd, command_range):
        captured["base_cmd"] = base_cmd
        captured["command_range"] = command_range

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("0E6C")

    scan_range = captured["command_range"]
    assert scan_range.start == 0x00
    assert scan_range.stop == 0x100
    # Dimmer uses the "22…" function prefix, not "10…".
    assert captured["base_cmd"].startswith("22")
