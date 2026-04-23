"""Regression tests for module register scan coverage.

Two invariants pinned here:

1. The scan covers the full 0x00..0xFF register range. Legacy code
   started at 0x10, missing 16 low registers that real hardware can
   store link records in.

2. The scan walks **three** memory banks per output module — function
   ``22`` (dimmer) or function ``10`` (switch/roller) at sub-byte ``04``
   for the historic bank, then function ``10`` at sub-byte ``00`` and
   sub-byte ``01`` for the two additional banks revealed by the
   PC-software serial trace. Each bank holds different record types;
   a one-bank scan returns only a fraction of the programmed links.
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


def _capture_scan_calls():
    """Return ``(calls, fake_scan)`` — calls is a list each pass appends to."""

    calls: list[dict] = []

    async def fake_scan(address, base_cmd, command_range, sub_byte="04"):
        calls.append(
            {
                "address": address,
                "base_cmd": base_cmd,
                "command_range": command_range,
                "sub_byte": sub_byte,
            }
        )

    return calls, fake_scan


@pytest.mark.asyncio
async def test_default_scan_range_starts_at_zero_for_output_module(tmp_path):
    """First pass for a switch module covers 0x00..0xFF."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "4707": {
            "address": "4707",
            "category": "Module",
            "model": "05-000-02",
            "channels": 12,
            "device_type": "01",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="switch_module")

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("4707")

    assert calls, "register scan was never invoked"
    first = calls[0]
    scan_range = first["command_range"]
    assert scan_range.start == 0x00
    assert scan_range.stop == 0x100
    assert 0x00 in scan_range and 0x0F in scan_range


@pytest.mark.asyncio
async def test_default_scan_range_starts_at_zero_for_dimmer_module(tmp_path):
    """Same coverage guarantee on the dimmer-module first pass."""

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

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("0E6C")

    first = calls[0]
    scan_range = first["command_range"]
    assert scan_range.start == 0x00
    assert scan_range.stop == 0x100
    # Dimmer pass-1 uses the "22…" function prefix.
    assert first["base_cmd"].startswith("22")


# ---------------------------------------------------------------------------
# Multi-pass scan: pin the three-bank orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_runs_three_passes_per_dimmer_module(tmp_path):
    """A dimmer module is scanned three times — all three passes use
    the dimmer-specific function ``22`` with sub-bytes ``04``, ``00``,
    ``01``. Real-hardware probing showed dimmers silently drop
    function-``10`` reads, so the extra passes must reuse the pass-1
    function code, not switch to ``10``."""

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

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("0E6C")

    # Dimmer: sub=04 (primary, channels 1-6) + sub=01 (secondary,
    # channels 7-12). sub=00 was verified byte-identical to sub=04
    # on real hardware and removed in 0.4.8 to halve scan time.
    assert len(calls) == 2, f"expected 2 passes, got {len(calls)}: {calls}"
    assert calls[0]["base_cmd"] == "226C0E"
    assert calls[0]["sub_byte"] == "04"
    assert calls[1]["base_cmd"] == "226C0E"
    assert calls[1]["sub_byte"] == "01"

    for entry in calls:
        assert entry["address"] == "0E6C"
        assert entry["command_range"].start == 0x00
        assert entry["command_range"].stop == 0x100


@pytest.mark.asyncio
async def test_scan_runs_single_pass_per_switch_module(tmp_path):
    """Switch modules need only the historic sub=04 pass. Real-hardware
    testing showed sub=00 duplicates sub=04 and sub=01 returns reverse-
    link bytes the switch decoder misreads as phantoms (all rejected at
    merge time). 0.4.8 drops both to restore pre-0.4.5 scan time on
    switches while keeping the dimmer bank fix."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "4707": {
            "address": "4707",
            "category": "Module",
            "model": "05-000-02",
            "channels": 12,
            "device_type": "01",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="switch_module")

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("4707")

    assert len(calls) == 1, f"expected 1 pass for switch, got {len(calls)}: {calls}"
    assert calls[0]["base_cmd"] == "100747"
    assert calls[0]["sub_byte"] == "04"


@pytest.mark.asyncio
async def test_scan_runs_single_pass_per_roller_module(tmp_path):
    """Roller modules: single sub=04 pass. No real-hardware trace
    has proven a secondary bank productive, so we mirror switch
    behaviour until one does."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "8394": {
            "address": "8394",
            "category": "Module",
            "model": "05-001-02",
            "channels": 6,
            "device_type": "02",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="roller_module")

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("8394")

    assert len(calls) == 1, f"expected 1 pass for roller, got {len(calls)}: {calls}"
    assert calls[0]["base_cmd"] == "109483"
    assert calls[0]["sub_byte"] == "04"


@pytest.mark.asyncio
async def test_scan_skips_extra_passes_for_non_output_modules(tmp_path):
    """PC link / PC logic / feedback / other modules don't get scanned
    at all (output-only gate runs before scan dispatch); they certainly
    don't get the multi-pass treatment."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "FF00": {
            "address": "FF00",
            "category": "Module",
            "model": "05-200",
            "device_type": "0A",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="pc_link")

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("FF00")

    assert calls == []
