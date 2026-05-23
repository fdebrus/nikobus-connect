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
async def test_vendor_aligned_scan_plan_for_switch_module(tmp_path):
    """0.16.0: switch module's scan = 3 vendor-aligned passes
    (sub=00 6 regs, sub=01 37 regs, sub=04 5 regs) = 48 reads."""

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

    assert len(calls) == 3, f"expected 3 vendor passes, got {calls}"
    assert [c["sub_byte"] for c in calls] == ["00", "01", "04"]
    assert tuple(calls[0]["command_range"]) == (0x05, 0x06, 0x07, 0x08, 0x09, 0x3E)
    assert tuple(calls[1]["command_range"]) == tuple(range(0x70, 0x94)) + (0x96,)
    assert tuple(calls[2]["command_range"]) == (0x65, 0x66, 0x67, 0x68, 0x69)
    total_regs = sum(len(c["command_range"]) for c in calls)
    assert total_regs == 48


@pytest.mark.asyncio
async def test_default_scan_range_starts_at_zero_for_dimmer_module(tmp_path):
    """0.16.0: dimmer follows the same vendor plan as switch/roller —
    full vendor alignment, no firmware-specific exceptions. The dimmer
    keeps its own function code (``22`` vs ``10`` for switch) but the
    register lists per sub-byte are identical to every other output
    module."""

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

    # Dimmer-specific function code "22" on the wire.
    assert all(c["base_cmd"].startswith("22") for c in calls)
    # Vendor plan: 3 passes, exact register lists.
    assert [c["sub_byte"] for c in calls] == ["00", "01", "04"]
    assert tuple(calls[0]["command_range"]) == (0x05, 0x06, 0x07, 0x08, 0x09, 0x3E)


# ---------------------------------------------------------------------------
# Multi-pass scan: pin the three-bank orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dimmer_uses_vendor_plan_no_firmware_exceptions(tmp_path):
    """0.16.0: dimmer follows the same vendor scan plan as every other
    output module. The pre-0.16.0 firmware-specific full-sweep
    exception (2026-05-04 capture on modules 116D + 0E0A) is **gone**
    — full vendor alignment. Users on firmwares that need broader
    scans can opt in via ``broad_scan=True`` on the discovery instance."""

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

    # 3 vendor-aligned passes, 48 total registers, dimmer func code "22".
    assert len(calls) == 3, f"expected 3 vendor passes, got {calls}"
    assert [c["sub_byte"] for c in calls] == ["00", "01", "04"]
    assert all(c["base_cmd"] == "226C0E" for c in calls)
    assert tuple(calls[0]["command_range"]) == (0x05, 0x06, 0x07, 0x08, 0x09, 0x3E)
    assert tuple(calls[1]["command_range"]) == tuple(range(0x70, 0x94)) + (0x96,)
    assert tuple(calls[2]["command_range"]) == (0x65, 0x66, 0x67, 0x68, 0x69)
    total_regs = sum(len(c["command_range"]) for c in calls)
    assert total_regs == 48


@pytest.mark.asyncio
async def test_switch_runs_vendor_3pass_plan(tmp_path):
    """0.16.0: switch follows the vendor-aligned 3-pass plan."""

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

    assert len(calls) == 3, f"expected 3 vendor passes, got {calls}"
    assert [c["sub_byte"] for c in calls] == ["00", "01", "04"]
    assert all(c["base_cmd"] == "100747" for c in calls)


@pytest.mark.asyncio
async def test_roller_runs_vendor_3pass_plan(tmp_path):
    """0.16.0: roller follows the vendor-aligned 3-pass plan."""

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

    assert len(calls) == 3, f"expected 3 vendor passes, got {calls}"
    assert [c["sub_byte"] for c in calls] == ["00", "01", "04"]
    assert all(c["base_cmd"] == "109483" for c in calls)


@pytest.mark.asyncio
async def test_broad_scan_opt_in_adds_legacy_extra_pass(tmp_path):
    """``broad_scan=True`` re-adds the pre-0.16.0 sub=04 0x00..0x3F
    sweep as a 4th pass after the vendor primary trio. Used as a safety
    net for firmwares where the link table doesn't sit in the
    vendor-canonical 0x70..0x96 band (e.g. the 2026-05-04 dimmer
    capture)."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
        broad_scan=True,
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

    # 3 vendor passes + 1 broad-scan extra = 4
    assert len(calls) == 4
    assert [c["sub_byte"] for c in calls] == ["00", "01", "04", "04"]
    # Broad-scan extra reads the legacy 0x00..0x3F band.
    assert tuple(calls[3]["command_range"]) == tuple(range(0x00, 0x40))


@pytest.mark.asyncio
async def test_scan_skips_extra_passes_for_non_output_modules(tmp_path):
    """Feedback / other modules don't get scanned at all (output-only
    gate runs before scan dispatch); they certainly don't get the
    multi-pass treatment.

    PC Link and PC Logic are NOT in this list — Stage 2 added both to
    the scan path so we can read their controller-resident link tables
    (PC Link, validated against a real Nikobus PC-software trace) and
    BP-cell directories (PC Logic, still being characterised). See
    ``test_pc_link_runs_register_scan`` for the inclusion check."""

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
            "model": "05-207",
            "device_type": "42",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="feedback_module")

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("FF00")

    assert calls == []


# ---------------------------------------------------------------------------
# Per-sub register range tuning (0.4.10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dimmer_vendor_scan_total_is_48_registers(tmp_path):
    """0.16.0: dimmer matches the vendor's 48-register-per-module
    plan. The pre-0.16.0 firmware-specific 512-reg full sweep is
    gone — full vendor alignment. ``broad_scan=True`` is the
    safety net for firmwares that need broader reads."""

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

    total_regs = sum(len(c["command_range"]) for c in calls)
    assert total_regs == 48, (
        f"expected 48 total regs (vendor-aligned 3-pass plan), got {total_regs}"
    )


@pytest.mark.asyncio
async def test_switch_vendor_scan_total_is_48_registers(tmp_path):
    """0.16.0: switch's vendor 3-pass plan = 48 total registers
    (6 + 37 + 5). Down from the pre-0.16.0 two-pass 103 (64 + 39)."""

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

    assert len(calls) == 3
    assert len(calls[0]["command_range"]) == 6   # sub=00 header
    assert len(calls[1]["command_range"]) == 37  # sub=01 link table
    assert len(calls[2]["command_range"]) == 5   # sub=04 status
    total_regs = sum(len(c["command_range"]) for c in calls)
    assert total_regs == 48


# ---------------------------------------------------------------------------
# Forensic mode: caller-supplied register_start / register_end / sub_byte
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forensic_mode_uses_caller_supplied_range(tmp_path):
    """When register_start + register_end are provided, the scan walks
    exactly that range with the given sub_byte and skips extra passes."""

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

    await discovery.query_module_inventory(
        "4707",
        register_start=0x70,
        register_end=0x83,
        sub_byte="01",
    )

    # Single pass — extra-pass logic must be skipped in forensic mode.
    assert len(calls) == 1
    call = calls[0]
    assert call["sub_byte"] == "01"
    assert call["command_range"].start == 0x70
    assert call["command_range"].stop == 0x84  # 0x83 + 1


@pytest.mark.asyncio
async def test_forensic_mode_bypasses_non_output_module_guard(tmp_path):
    """A module type the production path declines (e.g. interface_module)
    is still scanned when the caller provides an explicit range — the
    whole point of forensic mode."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "1234": {
            "address": "1234",
            "category": "Module",
            "model": "05-206",
            "channels": 6,
            "device_type": "37",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="interface_module")

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory(
        "1234",
        register_start=0x00,
        register_end=0x0F,
        sub_byte="04",
    )

    # Production path would have skipped this module type entirely;
    # forensic mode must scan.
    assert len(calls) == 1
    assert calls[0]["command_range"].start == 0x00
    assert calls[0]["command_range"].stop == 0x10


@pytest.mark.asyncio
async def test_forensic_mode_defaults_sub_byte_to_04(tmp_path):
    """sub_byte is optional in forensic mode and defaults to '04' when omitted."""

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

    await discovery.query_module_inventory(
        "4707",
        register_start=0x10,
        register_end=0x1F,
    )

    assert len(calls) == 1
    assert calls[0]["sub_byte"] == "04"


@pytest.mark.asyncio
async def test_forensic_mode_rejects_partial_range(tmp_path):
    """Supplying only one of register_start / register_end is a hard error."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    with pytest.raises(ValueError, match="both"):
        await discovery.query_module_inventory("4707", register_start=0x10)

    with pytest.raises(ValueError, match="both"):
        await discovery.query_module_inventory("4707", register_end=0x10)


@pytest.mark.asyncio
async def test_forensic_mode_rejects_inverted_range(tmp_path):
    """register_end must be >= register_start."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    with pytest.raises(ValueError, match=">="):
        await discovery.query_module_inventory(
            "4707", register_start=0x40, register_end=0x10
        )


@pytest.mark.asyncio
async def test_forensic_mode_rejects_with_all_mode(tmp_path):
    """register range overrides require a specific module — ALL mode rejects them."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    with pytest.raises(ValueError, match="ALL mode"):
        await discovery.query_module_inventory(
            "ALL", register_start=0x10, register_end=0x20
        )
