"""PC-Link registry header + diagnostic-echo ramp handling; 05-061 mapping.

Decoded 2026-08-25 from a Nikobus-HA #478 install's full discovery log:

* Registers A0..A3 of the PC-Link hold a *header page*, not records:
  byte-ramp filler (each byte equals its offset — the firmware's
  diagnostic echo) ending in the magic tail ``5E 55 AA AA`` followed by
  a u32 little-endian record count (0x17 = 23 on the reference
  install).
* Registers A4..A4+count-1 hold one 16-byte record per register:
  ``[page u32][device_type u32][addr 3 bytes + pad][Component.Number
  u32]``.
* Reads past the last record wrap back into repeating ramp pages.

Before this fix the parser read byte 7 of a ramp page as a device
type — a 00..0F ramp yields 0x04 (coincidentally the real 05-060
type) at "address" 0A0908, seeding a phantom button, and later ramps
yield types 0x14/0x24/0x34, firing spurious "Unknown device type"
warnings. All four ramp frames CRC-validate: they are genuine stored
content, so the 0.30.2 CRC gate cannot filter them — only content
awareness can.

The same install also proved device type ``0x05`` is the 05-061
(2-button plate WITH feedback LEDs): three type-05 records match the
install's .nkb 05-061 components on both bus address and BP index.
It had sat as Reserved (excluded from the button merge) for years.

Tests below pin:
* the header frame sets the record limit and is not classified;
* ramp frames (clean and corrupted) are skipped — no devices, no
  unknown-type warnings;
* frames past the header's record count are skipped (wrap-around);
* deleted (FFFFFF-address) slots still count toward the record limit;
* type 0x05 classifies as a discovered 05-061 Button;
* installs without a header page behave exactly as before.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nikobus_connect.discovery.discovery import (
    NikobusDiscovery,
    _looks_like_echo_ramp,
    _registry_header_count,
)
from nikobus_connect.discovery.mapping import DEVICE_TYPES


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


# ---------------------------------------------------------------------------
# Frames captured verbatim from the #478 install's debug log (PC-Link
# C798). Layout: "2E" + 2-byte responder + 16 data bytes + 3-byte CRC.
# ---------------------------------------------------------------------------

# Register A3 — header page: ramp prefix 30..37, magic 5E55AAAA,
# count 0x17 = 23 records.
HEADER_FRAME = "2EC79830313233343536375E55AAAA17000000BFD70D"

# Registers A0..A2 / wrap-around region — pure byte-ramp filler.
# Byte 7 of each is what the old parser read as a "device type".
RAMP_FRAME_00 = "2EC798000102030405060708090A0B0C0D0E0F91DABA"  # type 0x04 phantom
RAMP_FRAME_10 = "2EC798101112131415161718191A1B1C1D1E1FD48B92"  # "unknown 14"
RAMP_FRAME_20 = "2EC798202122232425262728292A2B2C2D2E2F1B78EF"  # "unknown 24"
RAMP_FRAME_30 = "2EC798303132333435363738393A3B3C3D3E3F5E292A"

# Wrap-around ramps re-read on later passes come back partially
# unstable — mid-ramp corruption must not defeat the detector.
RAMP_FRAME_CORRUPT_FF = "2EC7983031323334FFFFFFFF393A3B3C3D3E3F2796DC"
RAMP_FRAME_CORRUPT_BYTE = "2EC798303130333435363738393A3B3C3D3E3FA8EBDA"

# Register A5 — real record: switch module B655, type 01, number 1.
RECORD_B655 = "2EC798020000000100000055B600000100000025C4B5"

# Register A9 — real record: 05-061 button plate 3D8F7C, type 05,
# Component.Number 2 (matches the .nkb's "BP 2").
RECORD_05_061 = "2EC79805000000050000007C8F3D0002000000EABC01"

# Register A8 — deleted slot: address FFFFFF, but it still occupies a
# registry slot and must count toward the header's record limit.
RECORD_DELETED = "2EC7980400000006000000FFFFFFFF09000000875297"

# Synthetic header with count = 2, for exercising the past-end guard
# without feeding 23 records. parse_inventory_response does not check
# CRC (the listener's CRC gate runs upstream), so the trailer bytes
# are irrelevant here.
HEADER_FRAME_COUNT_2 = "2EC79830313233343536375E55AAAA02000000000000"


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------


def test_header_count_parses_reference_install():
    data = bytes.fromhex(HEADER_FRAME)[3:19]
    assert _registry_header_count(data) == 23


def test_header_count_absent_on_pure_ramp():
    data = bytes.fromhex(RAMP_FRAME_00)[3:19]
    assert _registry_header_count(data) is None


def test_header_count_rejects_implausible_counts():
    # count 0 and count > 512 are both rejected.
    zero = bytes.fromhex("30313233343536375E55AAAA00000000")
    huge = bytes.fromhex("30313233343536375E55AAAAFFFF0000")
    assert _registry_header_count(zero) is None
    assert _registry_header_count(huge) is None


def test_ramp_detector_hits_all_logged_filler_frames():
    for frame in (
        RAMP_FRAME_00,
        RAMP_FRAME_10,
        RAMP_FRAME_20,
        RAMP_FRAME_30,
        RAMP_FRAME_CORRUPT_FF,
        RAMP_FRAME_CORRUPT_BYTE,
    ):
        data = bytes.fromhex(frame)[3:19]
        assert _looks_like_echo_ramp(data), frame


def test_ramp_detector_passes_real_records():
    for frame in (RECORD_B655, RECORD_05_061, RECORD_DELETED):
        data = bytes.fromhex(frame)[3:19]
        assert not _looks_like_echo_ramp(data), frame


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_type_05_is_the_05_061_button():
    entry = DEVICE_TYPES["05"]
    assert entry["Category"] == "Button"
    assert entry["Model"] == "05-061"
    assert entry["Channels"] == 2


# ---------------------------------------------------------------------------
# parse_inventory_response integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_05_061_record_discovers_button(tmp_path):
    """The real type-05 record decodes into a discovered 05-061 Button
    at bus address 3D8F7C — previously silently dropped (Reserved)."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    result = await discovery.parse_inventory_response(RECORD_05_061)

    assert "3D8F7C" in discovery.discovered_devices
    entry = discovery.discovered_devices["3D8F7C"]
    assert entry["category"] == "Button"
    assert entry["model"] == "05-061"
    assert entry["channels"] == 2
    assert [b["address"] for b in result.buttons] == ["3D8F7C"]


@pytest.mark.asyncio
async def test_header_frame_is_metadata_not_a_device(tmp_path):
    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    result = await discovery.parse_inventory_response(HEADER_FRAME)

    assert discovery._registry_record_limit == 23
    assert discovery._registry_records_seen == 0
    assert discovery.discovered_devices == {}
    assert not result.buttons and not result.modules


@pytest.mark.asyncio
async def test_ramp_frames_seed_no_phantoms_and_no_warnings(tmp_path):
    """The four logged ramp frames used to produce a phantom "05-060 @
    0A0908" button plus "Unknown device type 14/24/34" warnings. Now:
    nothing discovered, nothing warned."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    for frame in (
        RAMP_FRAME_00,
        RAMP_FRAME_10,
        RAMP_FRAME_20,
        RAMP_FRAME_30,
        RAMP_FRAME_CORRUPT_FF,
        RAMP_FRAME_CORRUPT_BYTE,
    ):
        await discovery.parse_inventory_response(frame)

    assert discovery.discovered_devices == {}
    assert discovery._unknown_device_types_warned == set()


@pytest.mark.asyncio
async def test_full_reference_sequence(tmp_path):
    """Replay the reference install's shape: leading ramp pages, the
    header, real records (module + 05-061 + deleted slot), then
    wrap-around ramps. Exactly the real devices land."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    for frame in (
        RAMP_FRAME_00,
        RAMP_FRAME_10,
        RAMP_FRAME_20,
        HEADER_FRAME,
        RECORD_B655,
        RECORD_05_061,
        RECORD_DELETED,
    ):
        await discovery.parse_inventory_response(frame)

    assert set(discovery.discovered_devices) == {"B655", "3D8F7C"}
    # Live records + the deleted slot all consumed registry slots.
    assert discovery._registry_records_seen == 3


@pytest.mark.asyncio
async def test_past_end_guard_skips_wrapped_records(tmp_path):
    """Once ``count`` slots have been parsed, later frames are the
    wrap-around region — skipped even if they'd otherwise classify."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery.parse_inventory_response(HEADER_FRAME_COUNT_2)
    await discovery.parse_inventory_response(RECORD_B655)
    await discovery.parse_inventory_response(RECORD_DELETED)  # slot 2 of 2
    # Past the end: a frame that WOULD decode as a fresh 05-061.
    await discovery.parse_inventory_response(RECORD_05_061)

    assert "3D8F7C" not in discovery.discovered_devices
    assert set(discovery.discovered_devices) == {"B655"}


@pytest.mark.asyncio
async def test_no_header_means_unbounded_sweep_as_before(tmp_path):
    """Installs whose PC-Link has no header page (never observed to
    emit 5E55AAAA) keep the pre-header behaviour: every frame parses,
    no limit applies."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    for _ in range(4):
        await discovery.parse_inventory_response(RECORD_B655)
    await discovery.parse_inventory_response(RECORD_05_061)

    assert discovery._registry_record_limit is None
    assert set(discovery.discovered_devices) == {"B655", "3D8F7C"}


@pytest.mark.asyncio
async def test_all_ff_slots_do_not_count_toward_limit(tmp_path):
    """All-FF frames are 'no record at this slot' (pre-existing skip)
    and must not consume registry slots — only frames that reach the
    record parser count."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    all_ff = "2EC798" + "FF" * 16 + "CC98D0"
    await discovery.parse_inventory_response(HEADER_FRAME_COUNT_2)
    await discovery.parse_inventory_response(all_ff)
    await discovery.parse_inventory_response(all_ff)
    await discovery.parse_inventory_response(RECORD_B655)
    await discovery.parse_inventory_response(RECORD_05_061)

    assert set(discovery.discovered_devices) == {"B655", "3D8F7C"}


@pytest.mark.asyncio
async def test_reset_state_clears_registry_bounds(tmp_path):
    """A new scan starts clean — the previous scan's header count must
    not suppress the next scan's records."""

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery.parse_inventory_response(HEADER_FRAME_COUNT_2)
    await discovery.parse_inventory_response(RECORD_B655)
    await discovery.parse_inventory_response(RECORD_DELETED)

    discovery.reset_state()
    discovery.discovery_stage = "inventory_addresses"

    assert discovery._registry_record_limit is None
    assert discovery._registry_records_seen == 0

    await discovery.parse_inventory_response(RECORD_05_061)
    assert "3D8F7C" in discovery.discovered_devices


# ---------------------------------------------------------------------------
# Early-stop: the header count bounds not just parsing but the sweep itself
#
# Reference-install timing: registers A0..BB (header + 23 records) took
# ~4 s; the remaining 68 filler reads took ~10 s more, plus the 10 s
# inactivity window before finalize — ~20 s of pure wait for 4 s of
# data. Once the last header-declared record is parsed, the sweep drops
# this PC-Link's still-queued reads and shortens the inactivity window.
# Unlike the removed 0.5.13 all-FF terminator, the condition is exact
# (the device's own header count), not a register-value heuristic.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_early_stop_drains_queue_when_last_record_parsed(tmp_path):
    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery.parse_inventory_response(HEADER_FRAME_COUNT_2)
    await discovery.parse_inventory_response(RECORD_B655)
    assert coord.nikobus_command.drain_queue.call_count == 0  # 1 of 2 — keep going
    await discovery.parse_inventory_response(RECORD_05_061)   # 2 of 2 — stop

    coord.nikobus_command.drain_queue.assert_called_once_with(prefix="$1410C798")
    # The last record itself still classified normally.
    assert "3D8F7C" in discovery.discovered_devices


@pytest.mark.asyncio
async def test_early_stop_shortens_inactivity_window(tmp_path):
    from nikobus_connect.discovery.discovery import (
        _INVENTORY_TIMEOUT_SECONDS,
        _REGISTRY_EARLY_STOP_TIMEOUT,
    )

    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    assert discovery._inventory_timeout_seconds == _INVENTORY_TIMEOUT_SECONDS
    await discovery.parse_inventory_response(HEADER_FRAME_COUNT_2)
    await discovery.parse_inventory_response(RECORD_B655)
    assert discovery._inventory_timeout_seconds == _INVENTORY_TIMEOUT_SECONDS
    await discovery.parse_inventory_response(RECORD_05_061)
    assert discovery._inventory_timeout_seconds == _REGISTRY_EARLY_STOP_TIMEOUT

    # A fresh scan gets the full window back.
    discovery.reset_state()
    assert discovery._inventory_timeout_seconds == _INVENTORY_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_no_early_stop_without_header(tmp_path):
    """Units that never emit the 5E55AAAA header keep the full sweep —
    the drain must never fire on record count alone."""
    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    for _ in range(30):
        await discovery.parse_inventory_response(RECORD_B655)

    assert coord.nikobus_command.drain_queue.call_count == 0


@pytest.mark.asyncio
async def test_no_early_stop_on_all_ff_or_ramp_frames(tmp_path):
    """Skipped frames (empty slots, filler) don't count as records, so
    they can't trigger the stop prematurely."""
    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    all_ff = "2EC798" + "FF" * 16 + "CC98D0"
    await discovery.parse_inventory_response(HEADER_FRAME_COUNT_2)
    await discovery.parse_inventory_response(all_ff)
    await discovery.parse_inventory_response(RAMP_FRAME_00)
    await discovery.parse_inventory_response(RECORD_B655)

    assert coord.nikobus_command.drain_queue.call_count == 0


@pytest.mark.asyncio
async def test_early_stop_fires_once_and_stragglers_stay_skipped(tmp_path):
    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    await discovery.parse_inventory_response(HEADER_FRAME_COUNT_2)
    await discovery.parse_inventory_response(RECORD_B655)
    await discovery.parse_inventory_response(RECORD_DELETED)  # 2 of 2 — stop
    # In-flight stragglers after the drain: still skipped, no re-drain.
    await discovery.parse_inventory_response(RECORD_05_061)
    await discovery.parse_inventory_response(RAMP_FRAME_00)

    assert coord.nikobus_command.drain_queue.call_count == 1
    assert "3D8F7C" not in discovery.discovered_devices


# ---------------------------------------------------------------------------
# Second reference install (fdebrus, 3.11.0 field log, PC-Link F586) —
# same registry contract, different cosmetics:
#
# * the header page is FF-filled before the magic (no byte ramp);
# * count 0x2E = 46, and the records occupied exactly A4..D1 in the
#   field log ("past registry end (46/46)" fired on the D2 read);
# * past-end reads return ECHOES of recent records (D2/D3 repeated the
#   D1 record) and FF pages with single-byte corruption — no ramp
#   pages at all on this unit.
#
# Pins that the header detector keys on the magic, not the ramp
# prefix, and that record-echo wrap frames are handled by the
# past-end guard (the ramp detector rightly ignores them).
# ---------------------------------------------------------------------------

HEADER_FRAME_FF_PREFIX = "2EF586FFFFFFFFFFFFFFFF5E55AAAA2E00000032C88D"
# Register D1 — the 46th and last record on that install (05-302 RF at
# 2E58F6); registers D2/D3 returned this exact frame again (echo wrap).
RECORD_LAST_F586 = "2EF586210000001F000080F6582E00060000009FA654"
# Register A2 — pre-header noise: FF fill with one corrupted byte (BF).
NOISE_FRAME_BF = "2EF586FFFFFFFFFFFFFFFFBFFFFFFFFFFFFFFF3A4806"


def test_header_detector_keys_on_magic_not_ramp_prefix():
    data = bytes.fromhex(HEADER_FRAME_FF_PREFIX)[3:19]
    assert _registry_header_count(data) == 46
    # The noise frame has no magic — never mistaken for a header.
    assert _registry_header_count(bytes.fromhex(NOISE_FRAME_BF)[3:19]) is None


@pytest.mark.asyncio
async def test_ff_prefix_header_bounds_scan_and_echo_wrap_is_skipped(tmp_path):
    """Replay the second install's shape with a shrunken count: header
    (FF-prefix), records, then the last record echoed again (this
    unit's wrap behavior) — the echo must not re-parse or re-drain."""
    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)
    discovery.discovery_stage = "inventory_addresses"

    # Real header declares 46; synthesize count=2 to keep the test small.
    header_count_2 = "2EF586FFFFFFFFFFFFFFFF5E55AAAA02000000000000"
    await discovery.parse_inventory_response(header_count_2)
    await discovery.parse_inventory_response(RECORD_B655)
    await discovery.parse_inventory_response(RECORD_LAST_F586)  # 2/2 — stop
    # Echo wrap: the same last-record frame arrives again (real D2/D3
    # behavior), then an FF page with a corrupted byte.
    await discovery.parse_inventory_response(RECORD_LAST_F586)
    await discovery.parse_inventory_response(NOISE_FRAME_BF)

    assert coord.nikobus_command.drain_queue.call_count == 1
    # Drain was filtered on THIS responder's address (F586, not C798).
    coord.nikobus_command.drain_queue.assert_called_once_with(prefix="$1410F586")
    # The last record itself decoded (05-302 at 2E58F6), echoes didn't
    # add anything new.
    assert "2E58F6" in discovery.discovered_devices
