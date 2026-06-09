"""Discovery-audit regressions: an exception escaping a discovery entry
point must not leave the coordinator flags stuck.

Every entry path sets ``coordinator.discovery_running`` (and friends)
early; before the fix, an exception after that point (not connected,
send failure, callback error, cancellation) left the flags True forever
— the host then suppressed polling and rejected every new scan with
"discovery already running". The entry points now reset the discovery
state on the way out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.discovery import NikobusDiscovery


def _drop_coro(coro):
    coro.close()
    return MagicMock()


def _make_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    coord.dict_module_data = {"switch_module": {"4707": {"address": "4707"}}}
    coord.get_module_type = MagicMock(return_value="switch_module")
    coord.get_module_channel_count = MagicMock(return_value=12)
    coord.get_button_channels = MagicMock(return_value=None)
    return coord


def _make_discovery(coord, tmp_path) -> NikobusDiscovery:
    return NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )


async def test_inventory_start_failure_resets_flags(tmp_path) -> None:
    """No command pipeline (not connected) → RuntimeError, flags reset."""
    coord = _make_coordinator()
    coord.nikobus_command = None
    discovery = _make_discovery(coord, tmp_path)

    with pytest.raises(RuntimeError):
        await discovery.start_inventory_discovery()

    assert coord.discovery_running is False
    assert coord.inventory_query_type is None
    assert discovery._inventory_timeout_task is None


async def test_inventory_send_failure_resets_flags(tmp_path) -> None:
    """#A queueing fails (bus dropped mid-call) → flags reset, error raised."""
    coord = _make_coordinator()
    coord.nikobus_command = MagicMock()
    coord.nikobus_command.queue_command = AsyncMock(side_effect=OSError("gone"))
    discovery = _make_discovery(coord, tmp_path)

    with pytest.raises(OSError):
        await discovery.start_inventory_discovery()

    assert coord.discovery_running is False
    assert coord.discovery_module is False


async def test_single_module_scan_failure_resets_flags(tmp_path) -> None:
    """An exception escaping the single-module scan path resets state."""
    coord = _make_coordinator()
    coord.nikobus_command = MagicMock()
    discovery = _make_discovery(coord, tmp_path)
    discovery._scan_module_registers = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await discovery.query_module_inventory("4707")

    assert coord.discovery_running is False
    assert coord.discovery_module is False
    assert coord.discovery_module_address is None


async def test_queue_scan_failure_resets_flags(tmp_path) -> None:
    """An exception mid-queue (scan-all) resets state instead of
    leaving the queue's flags stuck."""
    coord = _make_coordinator()
    coord.nikobus_command = MagicMock()
    discovery = _make_discovery(coord, tmp_path)
    discovery._register_scan_queue = ["4707"]
    discovery._scan_module_registers = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await discovery._start_next_register_scan()

    assert coord.discovery_running is False
    assert coord.discovery_module is False
