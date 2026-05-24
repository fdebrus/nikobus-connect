"""Regression tests for module register scan coverage.

0.17.0: scan plans are derived per-product from each product DLL's
``GetDLLReadInfo`` export, translated to wire reads via
``byte_offset = (sub_byte * 256 + register) * 16``. Each product
(switch/dimmer/roller/pc_logic/pc_link/feedback) has its own profile.

These tests pin the dispatcher contract — the scan loop iterates the
per-product profile and issues the correct (sub_byte, register_range)
passes — without hardcoding every register address (those are pinned
by ``test_progress_vendor_aligned.py``).
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
async def test_switch_dispatches_com_aligned_profile(tmp_path):
    """0.19.0: switch scan walks the PC-software COM-trace band —
    sub=00 only, register range 0x10..0x3F with parser-driven
    early-stop on the FF terminator. Validated against C9A5, 4707,
    5B05 in the 24/05/2026 capture."""

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

    subs = {c["sub_byte"] for c in calls}
    assert subs == {"00"}, subs
    assert all(c["base_cmd"] == "100747" for c in calls)


@pytest.mark.asyncio
async def test_dimmer_dispatches_per_product_profile(tmp_path):
    """0.17.0: dimmer walks the per-product profile (DLL-derived).
    Function code is "22" (dimmer-specific read). Total reads include
    the variable section 3 link table that the original vendor trace
    skipped — that's the fix for the dimmer-records regression."""

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
    # 0.19.0 COM-aligned profile: sub=00 0x20..0x3F + sub=00 0xF8..0xFF
    # + sub=01 0x20..0x2F = ~56 reads, with parser-driven early-stop.
    subs = {c["sub_byte"] for c in calls}
    assert subs == {"00", "01"}, subs
    total_regs = sum(len(c["command_range"]) for c in calls)
    assert 40 <= total_regs <= 80, total_regs


@pytest.mark.asyncio
async def test_roller_dispatches_per_product_profile(tmp_path):
    """0.17.0: roller walks the Niko_05_202.dll-derived profile."""

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

    assert all(c["base_cmd"] == "109483" for c in calls)
    subs = {c["sub_byte"] for c in calls}
    # 0.19.0 COM-aligned: roller uses sub=00 only (PC software trace).
    assert subs == {"00"}
    total_regs = sum(len(c["command_range"]) for c in calls)
    assert 30 <= total_regs <= 80, total_regs


@pytest.mark.asyncio
async def test_feedback_module_is_skipped(tmp_path):
    """0.17.1: feedback_module reverts to NON_OUTPUT_MODULE_TYPES — its
    DLL-derived scan (912 reads) wastes ~45 min on ACK timeouts in the
    real world. Feedback programming lives on source modules' BP cells,
    not in the feedback module's own memory, so there are no records to
    discover here anyway."""

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

    assert calls == [], (
        "feedback module should not be scanned (no link records there)"
    )


@pytest.mark.asyncio
async def test_other_modules_still_skipped(tmp_path):
    """Audio/interface/other modules remain skip-listed — no scan plan
    has been characterised for them."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "ABCD": {
            "address": "ABCD",
            "category": "Module",
            "model": "05-XYZ",
            "device_type": "FF",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="audio_module")

    calls, fake_scan = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("ABCD")

    assert calls == []


@pytest.mark.asyncio
async def test_broad_scan_opt_in_adds_extra_passes(tmp_path):
    """``broad_scan=True`` appends the conditional sections PC software
    skips when its in-memory project cache is primed (dimmer section 7,
    roller section 2 — both 11KB+ blocks)."""

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

    calls_broad, fake_scan_broad = _capture_scan_calls()
    discovery._scan_module_registers = fake_scan_broad
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("0E6C")

    # broad_scan adds dimmer section 7 (offset 0x1962 length 0x2CF2)
    # — wraps across sub=2 and sub=3. Verify those banks are scanned.
    subs = {c["sub_byte"] for c in calls_broad}
    assert "02" in subs or "03" in subs, (
        f"broad_scan should add section 7 reads in sub=2/sub=3, got subs={subs}"
    )


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
