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
    # Per 0.4.10 per-sub range tuning: sub=04 sweeps 0x00..0x3F
    # (primary forward-link bank). Critically still starts at 0x00
    # to preserve the 0.4.4 regression fix for records in 0x00..0x0F.
    assert scan_range.start == 0x00
    assert scan_range.stop == 0x40
    assert 0x00 in scan_range and 0x0F in scan_range


@pytest.mark.asyncio
async def test_default_scan_range_starts_at_zero_for_dimmer_module(tmp_path):
    """Dimmer first pass still starts at 0x00 to keep the 0.4.4
    regression fix for records in 0x00..0x0F. 0.5.7 widens the stop
    to 0x100 (full sweep) — see the dimmer two-pass test below for
    the upper-bound assertion."""

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
    assert 0x00 in scan_range and 0x0F in scan_range
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

    # Dimmer: sub=04 (primary) + sub=01 (secondary). sub=00 was
    # verified byte-identical to sub=04 on the original-trace firmware
    # and removed in 0.4.8 to halve scan time.
    #
    # 0.4.10 narrowed each pass to the band a single PC-software trace
    # showed productive: sub=04 → 0x00..0x3F (64 regs),
    # sub=01 → 0x70..0x96 (39 regs).
    #
    # 0.5.7 reverts dimmer to the pre-0.4.10 full sweep
    # (range(0x00, 0x100) per pass) because the 2026-05-04 capture
    # showed records on channels 3 and 5 of a 12-channel dimmer falling
    # outside both 0.4.10 bands. Switch and roller stay at the tuned
    # ranges; they have multi-firmware confirmation that the narrowing
    # doesn't drop records there.
    assert len(calls) == 2, f"expected 2 passes, got {len(calls)}: {calls}"

    assert calls[0]["base_cmd"] == "226C0E"
    assert calls[0]["sub_byte"] == "04"
    assert calls[0]["command_range"].start == 0x00
    assert calls[0]["command_range"].stop == 0x100

    assert calls[1]["base_cmd"] == "226C0E"
    assert calls[1]["sub_byte"] == "01"
    assert calls[1]["command_range"].start == 0x00
    assert calls[1]["command_range"].stop == 0x100

    for entry in calls:
        assert entry["address"] == "0E6C"


@pytest.mark.asyncio
async def test_scan_runs_two_passes_per_switch_module(tmp_path):
    """Switch modules get sub=04 + sub=01 since 0.5.5.

    Pre-0.5.5 history: 0.4.8 dropped sub=01 from switch because the
    cross-frame chunker (broken for 32-char switch frames) misread
    every chunk as a phantom record. With the 0.5.5 chunker fix that
    discards register-end padding, sub=01 returns its own productive
    band; the merge-layer ``unknown_button`` / ``unknown_mode`` gates
    filter any genuine config-byte phantoms that survive."""

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

    assert len(calls) == 2, f"expected 2 passes for switch, got {len(calls)}: {calls}"
    assert calls[0]["base_cmd"] == "100747"
    assert calls[0]["sub_byte"] == "04"
    assert calls[1]["base_cmd"] == "100747"
    assert calls[1]["sub_byte"] == "01"
    assert calls[1]["command_range"].start == 0x70
    assert calls[1]["command_range"].stop == 0x97


@pytest.mark.asyncio
async def test_scan_runs_two_passes_per_roller_module(tmp_path):
    """Roller modules get sub=04 + sub=01 since 0.5.5 — same family
    layout as switch (12-char records, 32-char register frames), and
    the same sub=01 productive band surfaces once the chunker stops
    drifting alignment by the per-register padding."""

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

    assert len(calls) == 2, f"expected 2 passes for roller, got {len(calls)}: {calls}"
    assert calls[0]["base_cmd"] == "109483"
    assert calls[0]["sub_byte"] == "04"
    assert calls[1]["base_cmd"] == "109483"
    assert calls[1]["sub_byte"] == "01"
    assert calls[1]["command_range"].start == 0x70
    assert calls[1]["command_range"].stop == 0x97


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
async def test_dimmer_scan_total_registers_full_sweep_per_pass(tmp_path):
    """0.4.10 narrowed dimmer to sub=04 → 0x00..0x3F (64) + sub=01 →
    0x70..0x96 (39) = 103 registers, on the strength of a single
    PC-software trace. 0.5.7 reverts dimmer to the pre-0.4.10 full
    sweep (range(0x00, 0x100) per pass = 512 total) because the
    2026-05-04 capture from a different firmware revision showed
    productive records on channels 3 and 5 falling outside both
    0.4.10 bands. Switch and roller stay at the tuned ranges (their
    multi-firmware traces don't show the same gap)."""

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
    assert total_regs == 2 * 256, (
        f"expected 512 total regs across 2 passes (full sweep per pass), "
        f"got {total_regs}"
    )


@pytest.mark.asyncio
async def test_switch_scan_two_pass_total_is_tuned_not_full_sweep(tmp_path):
    """Switch two-pass total: 64 (sub=04 → 0x00..0x3F) + 39 (sub=01 →
    0x70..0x96) = 103 registers. Pre-0.4.10 was 256 per pass; 0.5.5
    re-enables sub=01 with the same tuned 0x70..0x96 band."""

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

    assert len(calls) == 2
    assert len(calls[0]["command_range"]) == 64
    assert len(calls[1]["command_range"]) == 39
    # Sanity bound: still well under a full 256-per-pass sweep.
    total_regs = sum(len(c["command_range"]) for c in calls)
    assert total_regs < 256
