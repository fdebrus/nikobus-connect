"""Progress-tracking tests for PHASE_INVENTORY and PHASE_IDENTITY.

Pre-0.16.3 the library didn't emit accurate ``register_total`` /
``registers_sent`` during inventory and identity phases, so HA's
fallback kicked in:

  - PHASE_INVENTORY → bar shows ``0 / 240`` (fallback)
  - PHASE_IDENTITY  → bar stays at 0 then jumps because no per-
    register progress was emitted; the actual scan reads 96 registers
    per address but the UI shows ``X / 240``

0.16.3 fixes both: PHASE_INVENTORY surfaces as a single unit of
work (the ``#A`` broadcast), PHASE_IDENTITY emits a 96-register
target per address with per-register increments.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.discovery.base import (
    DiscoveryProgress,
    PHASE_IDENTITY,
    PHASE_INVENTORY,
)
from nikobus_connect.discovery.discovery import NikobusDiscovery


def _make_discovery(tmp_path, on_progress):
    coord = MagicMock()
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.discovery_running = False
    coord.inventory_query_type = None
    coord.nikobus_command = MagicMock()
    coord.nikobus_command.queue_command = AsyncMock()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=lambda coro: coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
        on_progress=on_progress,
    )
    discovery._schedule_inventory_timeout = MagicMock()
    return discovery


# ---------------------------------------------------------------------------
# Identity phase emits per-register progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identity_phase_register_total_is_96(tmp_path) -> None:
    """Per-address identity probe scans 0xA0..0xFF = 96 regs."""
    events: list[DiscoveryProgress] = []

    async def on_progress(p: DiscoveryProgress) -> None:
        events.append(p)

    discovery = _make_discovery(tmp_path, on_progress)
    await discovery._run_inventory_identity_queries({"4707"})

    identity_events = [e for e in events if e.phase == PHASE_IDENTITY]
    assert identity_events, "no PHASE_IDENTITY events emitted"
    for event in identity_events:
        assert event.register_total == 96, (
            f"expected register_total=96 per address, got {event.register_total}"
        )


@pytest.mark.asyncio
async def test_identity_phase_increments_registers_sent_per_register(tmp_path) -> None:
    """Identity scan emits per-register progress so the bar advances."""
    events: list[DiscoveryProgress] = []

    async def on_progress(p: DiscoveryProgress) -> None:
        events.append(p)

    discovery = _make_discovery(tmp_path, on_progress)
    await discovery._run_inventory_identity_queries({"4707"})

    per_register_events = [
        e for e in events
        if e.phase == PHASE_IDENTITY and e.register is not None
    ]
    # 96 registers per address × 1 address = 96 per-register events.
    assert len(per_register_events) == 96
    # registers_sent climbs from 1 to 96.
    counts = [e.registers_sent for e in per_register_events]
    assert counts[0] == 1
    assert counts[-1] == 96
    # Strictly monotonic per address.
    for prev, cur in zip(counts, counts[1:]):
        assert cur == prev + 1


@pytest.mark.asyncio
async def test_identity_phase_resets_counter_per_address(tmp_path) -> None:
    """Each address starts its 96-register scan from registers_sent=1,
    not from a cumulative count carried over from the previous address."""
    events: list[DiscoveryProgress] = []

    async def on_progress(p: DiscoveryProgress) -> None:
        events.append(p)

    discovery = _make_discovery(tmp_path, on_progress)
    await discovery._run_inventory_identity_queries({"4707", "8394"})

    per_register_events = [
        e for e in events
        if e.phase == PHASE_IDENTITY and e.register is not None
    ]
    # 96 events per address × 2 addresses = 192
    assert len(per_register_events) == 192
    # First address: registers_sent 1..96; second address: also 1..96 (reset).
    first_address_events = per_register_events[:96]
    second_address_events = per_register_events[96:]
    assert first_address_events[-1].registers_sent == 96
    assert second_address_events[0].registers_sent == 1
    assert second_address_events[-1].registers_sent == 96


@pytest.mark.asyncio
async def test_identity_phase_surfaces_module_index_and_total(tmp_path) -> None:
    """``module_index`` / ``module_total`` track per-address progress
    across the identity-phase queue, so the UI can show "module 2/3"."""
    events: list[DiscoveryProgress] = []

    async def on_progress(p: DiscoveryProgress) -> None:
        events.append(p)

    discovery = _make_discovery(tmp_path, on_progress)
    await discovery._run_inventory_identity_queries(
        {"4707", "8394", "C7C1"}
    )

    identity_events = [
        e for e in events
        if e.phase == PHASE_IDENTITY and e.register is not None
    ]
    # module_total = 3 throughout the identity phase.
    for event in identity_events:
        assert event.module_total == 3
    # module_index climbs as we move to the next address.
    indexes_seen = {e.module_index for e in identity_events}
    assert indexes_seen == {1, 2, 3}


# ---------------------------------------------------------------------------
# Inventory phase surfaces a single unit of work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_phase_register_total_is_one(tmp_path) -> None:
    """The ``#A`` broadcast is one operation, not a register sweep —
    surface it as a single unit so HA doesn't fall back to ``0/240``.

    Pre-0.16.3 the library left ``register_total=0`` during
    PHASE_INVENTORY and HA fell back to its 240-register safety
    value. Result: a stuck-looking ``0 / 240`` bar throughout the
    inventory phase.
    """
    events: list[DiscoveryProgress] = []

    async def on_progress(p: DiscoveryProgress) -> None:
        events.append(p)

    discovery = _make_discovery(tmp_path, on_progress)
    await discovery.start_inventory_discovery()

    inventory_events = [e for e in events if e.phase == PHASE_INVENTORY]
    assert inventory_events, "no PHASE_INVENTORY event emitted"
    last = inventory_events[-1]
    assert last.register_total == 1
    # The command is on the wire by the time we emit → registers_sent = 1.
    assert last.registers_sent == 1
