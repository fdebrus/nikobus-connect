"""All-FF terminator stops the PC-Link sub=04 inventory sweep.

Niko's PC software reads PC-Link memory sequentially and stops at the
first 16-byte all-FF response — that's the end-of-active-project
marker. Records past the terminator are either untouched flash (FF)
or residue from a previous install (visible to a brute-force sweep
but invisible to the Niko software's read sequence).

Pre-0.5.13 the library swept the full ``A0..FF`` range and picked up
shadow records from second-hand PC-Links. The fix mirrors Niko's
behaviour: drain the remaining queued inventory reads on the first
all-FF response.

Trace evidence: a real-hardware capture of Niko's PC software's
``Read preview`` operation against fdebrus's install (logged
2024-05-24) shows the sub=04 sweep going A3 → A4 → ... → C2 → C3
and stopping. C3's response is a 22-byte frame whose 16-byte payload
is pure FF: ``2EF586`` ``FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF`` ``CC98D0``.
The software never reads C4..FF.
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
# as the 16-byte record. All-FF in those 16 bytes triggers the
# terminator path.
ALL_FF_INVENTORY_FRAME = "2EF586" + "FF" * 16 + "CC98D0"

# A real registry record from fdebrus's install — 0E6C dimmer (type
# 03). Used as a "non-terminator" frame to confirm we don't drain
# while real records are still arriving.
REAL_REGISTRY_FRAME = "2EF586" + "03000000030000006C0E000001000000" + "F938E8"


@pytest.mark.asyncio
async def test_leading_all_ff_does_not_drain_before_data(tmp_path):
    """Pure all-FF responses BEFORE any real data are leading
    untouched flash, not the terminator. PC-Link memory often has
    A0..A2 (or similar) untouched before the project's actual start
    register; the user-2026-05-07 install is one such case. We must
    NOT treat the first all-FF as the terminator — that's the bug
    fix for 0.5.13 after the initial ship."""

    coord = _make_coordinator()
    coord.nikobus_command.drain_queue = MagicMock(return_value=95)

    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # Three leading all-FF responses (A0..A2 untouched flash).
    for _ in range(3):
        await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)

    # No drain, no terminator flag.
    assert coord.nikobus_command.drain_queue.call_count == 0
    assert discovery._pc_link_inventory_terminator_seen is False
    # Data not seen yet either.
    assert discovery._pc_link_inventory_data_seen is False


@pytest.mark.asyncio
async def test_all_ff_after_data_drains_queue(tmp_path):
    """The first all-FF response AFTER at least one non-all-FF
    response is the terminator. Drains the queue and sets the flag."""

    coord = _make_coordinator()
    coord.nikobus_command.drain_queue = MagicMock(return_value=42)

    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # Real record arrives → ``data_seen`` flips True.
    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    assert discovery._pc_link_inventory_data_seen is True
    assert discovery._pc_link_inventory_terminator_seen is False
    assert coord.nikobus_command.drain_queue.call_count == 0

    # All-FF after data → terminator fires.
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    assert discovery._pc_link_inventory_terminator_seen is True
    coord.nikobus_command.drain_queue.assert_called_once()


@pytest.mark.asyncio
async def test_leading_all_ff_then_data_then_terminator(tmp_path):
    """Realistic install pattern: A0..A2 untouched flash, A3+ real
    records, eventual all-FF terminator. The terminator should fire
    only on the all-FF that appears after records, not on the
    leading flash."""

    coord = _make_coordinator()
    coord.nikobus_command.drain_queue = MagicMock(return_value=10)

    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # Leading untouched flash.
    for _ in range(3):
        await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    assert coord.nikobus_command.drain_queue.call_count == 0

    # Real records.
    for _ in range(5):
        await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)

    # Trailing all-FF → terminator fires.
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    coord.nikobus_command.drain_queue.assert_called_once()


@pytest.mark.asyncio
async def test_subsequent_all_ff_responses_do_not_drain_again(tmp_path):
    """Once the terminator flag is set, additional all-FF responses
    take the legacy "skip and continue" path instead of re-draining
    (drain_queue is already empty; the second call would be a no-op
    but logging-wise we don't want to spam INFO each time)."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # Establish the data-seen state and fire the terminator.
    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    # Repeat all-FFs.
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)

    assert coord.nikobus_command.drain_queue.call_count == 1


@pytest.mark.asyncio
async def test_real_records_before_terminator_do_not_drain(tmp_path):
    """A registry record (well-formed, non-FF) does NOT trigger the
    drain. Only the terminator does. Real records flip the
    ``data_seen`` gate so the next all-FF qualifies as terminator."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # Three real records arrive, then the terminator.
    for _ in range(3):
        await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    assert coord.nikobus_command.drain_queue.call_count == 0
    assert discovery._pc_link_inventory_terminator_seen is False
    assert discovery._pc_link_inventory_data_seen is True

    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    assert coord.nikobus_command.drain_queue.call_count == 1
    assert discovery._pc_link_inventory_terminator_seen is True


@pytest.mark.asyncio
async def test_terminator_outside_inventory_phase_does_not_drain(tmp_path):
    """All-FF responses outside the inventory phase (e.g. during
    Stage-2 register scans where modules legitimately return FF for
    unprogrammed registers) must not drain — Stage-2 scans queue
    their own commands and a stray drain would abort the scan."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "register_scan"

    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)

    assert discovery._pc_link_inventory_terminator_seen is False
    assert coord.nikobus_command.drain_queue.call_count == 0


@pytest.mark.asyncio
async def test_terminator_flag_resets_between_scans(tmp_path):
    """A subsequent inventory enumeration must start fresh — both
    the terminator flag and the ``data_seen`` gate are cleared by
    ``reset_state``."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # First scan: data + terminator.
    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    assert discovery._pc_link_inventory_terminator_seen is True
    assert discovery._pc_link_inventory_data_seen is True

    discovery.reset_state(update_flags=False)
    assert discovery._pc_link_inventory_terminator_seen is False
    assert discovery._pc_link_inventory_data_seen is False

    # Second scan: data + terminator again. Drain count should
    # increment.
    discovery.discovery_stage = "inventory_addresses"
    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)
    assert discovery._pc_link_inventory_terminator_seen is True
    assert coord.nikobus_command.drain_queue.call_count == 2


@pytest.mark.asyncio
async def test_drain_call_is_safe_when_command_lacks_drain_queue(tmp_path):
    """Defensive: if ``coordinator.nikobus_command`` doesn't expose
    ``drain_queue`` (older harness, test stub), the terminator path
    must still set the flag without raising."""

    coord = _make_coordinator()
    # Simulate a command object without drain_queue.
    del coord.nikobus_command.drain_queue
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # Establish data_seen state first.
    await discovery.parse_inventory_response(REAL_REGISTRY_FRAME)
    # Should not raise.
    await discovery.parse_inventory_response(ALL_FF_INVENTORY_FRAME)

    assert discovery._pc_link_inventory_terminator_seen is True


@pytest.mark.asyncio
async def test_queue_loop_short_circuits_after_terminator(tmp_path):
    """The for-loop in ``_run_inventory_identity_queries`` checks the
    terminator flag between iterations. If a terminator response
    arrives during queueing (event-loop yield in queue_command), the
    loop bails out without queueing the remaining registers."""

    queued_regs: list[int] = []

    async def fake_queue_command(cmd):
        # Extract the register byte from the command:
        # ``$1410 <bus_addr> <reg> 04 <crc>`` — register is at
        # chars 9..11 of the command string.
        try:
            reg = int(cmd[9:11], 16)
        except ValueError:
            return
        queued_regs.append(reg)
        # After the third register, simulate a terminator response
        # that flips the flag.
        if len(queued_regs) == 3:
            discovery._pc_link_inventory_terminator_seen = True

    coord = _make_coordinator()
    coord.nikobus_command.queue_command = fake_queue_command
    coord.discovery_module = False
    coord.discovery_module_address = None

    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery._run_inventory_identity_queries({"86F5"})

    # Loop should have stopped after 3 — not queued all 96.
    assert len(queued_regs) == 3
    assert queued_regs == [0xA0, 0xA1, 0xA2]
