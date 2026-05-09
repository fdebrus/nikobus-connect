"""All-FF responses skip-and-continue; PC-Link sweep is full-range.

0.5.13 / 0.5.14 introduced an early-stop "all-FF terminator" that
mirrored Niko's PC software stopping at the first FF response after
real records. Trace evidence at the time was a single contiguous
install (fdebrus, 2024-05-24) where the FF terminator and the
end-of-project happened to coincide.

A different user (issue #319 / 2026-05-09) reported discovery missing
3 of 9 known modules. Their PC-Link's project memory has a legitimate
all-FF gap mid-project — the sub=04 sweep terminates at the gap and
every record past it is dropped. Re-decoding traces and reviewing
fdebrus's intended discovery flow (probe → drop absent modules →
keep buttons with status flags) makes the read-layer terminator
redundant: ``detect_stale_inventory`` (0.5.16) handles residue at the
bus-presence layer, where actual presence — not a register-value
heuristic — distinguishes real modules from previous-install ghosts.

0.5.17 removes the terminator entirely:

- ``parse_inventory_response`` treats all-FF as "no record at this
  slot, skip and continue" (the pre-0.5.13 behaviour).
- The full ``range(0xA0, 0x100)`` sweep always runs.
- ``drain_queue`` is no longer called from the inventory path.

Tests below pin the new contract: any number of all-FF responses,
in any position, never drain the queue and never short-circuit the
sweep.
"""

from __future__ import annotations

from unittest.mock import MagicMock

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
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    coord.get_module_channel_count = MagicMock(return_value=0)
    coord.nikobus_command = MagicMock()
    coord.nikobus_command.drain_queue = MagicMock(return_value=0)
    return coord


def _make_discovery(coord, tmp_path) -> NikobusDiscovery:
    return NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )


# 22-byte payload for an all-FF inventory frame (after the $0510$ ACK
# the listener strips). The library's parse_inventory_response drops
# the first 3 bytes (``2E`` + 2-byte responder) and treats bytes 3..18
# as the 16-byte record. All-FF in those 16 bytes → skip-and-continue.
ALL_FF_INVENTORY_FRAME = "2EF586" + "FF" * 16 + "CC98D0"

# Real registry record from fdebrus's install — 0E6C dimmer (type 03).
REAL_REGISTRY_FRAME = "2EF586" + "03000000030000006C0E000001000000" + "F938E8"

# Second real registry record — different module address so the
# discovered_devices map can pin both decodes independently. C9A5
# switch (type 01) — one of the modules issue #319 reported missing.
REAL_REGISTRY_FRAME_C9A5 = (
    "2EF586" + "0300000001000000A5C9000002000000" + "1234AB"
)


@pytest.mark.asyncio
async def test_leading_all_ff_does_not_drain(tmp_path):
    """Pure all-FF responses BEFORE any real data are skipped, not
    treated as a terminator. PC-Link memory often has untouched flash
    at A0..A2 before the project's actual start register."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    for _ in range(3):
        await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)

    assert coord.nikobus_command.drain_queue.call_count == 0


@pytest.mark.asyncio
async def test_all_ff_after_data_does_not_drain(tmp_path):
    """An all-FF response after real records is no longer treated as
    end-of-project. It's just an empty slot — sweep continues."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)

    assert coord.nikobus_command.drain_queue.call_count == 0


@pytest.mark.asyncio
async def test_record_after_ff_gap_is_decoded(tmp_path):
    """Bug-fix pin for issue #319: a record that arrives AFTER an
    all-FF block is still decoded into ``discovered_devices``.

    Prior to 0.5.17 the all-FF block fired the terminator, drained
    the queue, and the second record was lost. With the terminator
    removed, both records land."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME_C9A5)

    # The second valid record (C9A5) MUST be in the device map.
    addrs = set(discovery.discovered_devices.keys())
    assert "C9A5" in addrs, (
        f"C9A5 should decode after FF gap; got addrs={addrs}"
    )
    # And the queue must not have been drained.
    assert coord.nikobus_command.drain_queue.call_count == 0


@pytest.mark.asyncio
async def test_multiple_consecutive_ff_blocks_do_not_drain(tmp_path):
    """A run of multiple all-FF blocks (e.g. user deleted a contiguous
    range of modules and the slots zero-erased) does not terminate
    the sweep."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    for _ in range(5):
        await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME_C9A5)

    assert coord.nikobus_command.drain_queue.call_count == 0
    assert "C9A5" in discovery.discovered_devices


@pytest.mark.asyncio
async def test_all_ff_outside_inventory_phase_does_not_drain(tmp_path):
    """All-FF responses outside the inventory phase (e.g. during
    Stage-2 register scans where modules legitimately return FF for
    unprogrammed registers) must not drain — they never did, and
    don't now."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "register_scan"

    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)

    assert coord.nikobus_command.drain_queue.call_count == 0


@pytest.mark.asyncio
async def test_drain_queue_never_called_from_inventory_path(tmp_path):
    """Belt-and-braces: across a realistic 20-frame inventory pattern
    (leading FF, real records, gap, more records, trailing FF), the
    drain_queue method is never invoked from the inventory parser."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # Leading flash.
    for _ in range(3):
        await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    # First batch of records.
    for _ in range(5):
        await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    # Mid-project gap.
    for _ in range(2):
        await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    # Second batch of records.
    for _ in range(5):
        await discovery.parse_inventory_response(REAL_REGISTRY_FRAME_C9A5)
    # Trailing FF (rest of A0..FF unprogrammed).
    for _ in range(5):
        await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)

    assert coord.nikobus_command.drain_queue.call_count == 0
    # Both records on either side of the gap landed (0E6C from
    # REAL_REGISTRY_FRAME, C9A5 from REAL_REGISTRY_FRAME_C9A5).
    assert {"0E6C", "C9A5"}.issubset(set(discovery.discovered_devices.keys()))


@pytest.mark.asyncio
async def test_inventory_loop_queues_full_register_range(tmp_path):
    """``_run_inventory_identity_queries`` queues ALL 96 registers
    (A0..FF) regardless of any all-FF responses arriving during
    queueing. The pre-0.5.17 short-circuit is gone."""

    queued_regs: list[int] = []

    async def fake_queue_command(cmd):
        try:
            reg = int(cmd[9:11], 16)
        except ValueError:
            return
        queued_regs.append(reg)

    coord = _make_coordinator()
    coord.nikobus_command.queue_command = fake_queue_command
    coord.discovery_module = False
    coord.discovery_module_address = None

    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery._run_inventory_identity_queries({"86F5"})

    # Full sweep: 96 registers from 0xA0 to 0xFF inclusive.
    assert len(queued_regs) == 96
    assert queued_regs[0] == 0xA0
    assert queued_regs[-1] == 0xFF
