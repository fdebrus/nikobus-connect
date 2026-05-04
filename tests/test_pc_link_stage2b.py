"""Stage 2b plumbing tests: registry accumulation across a scan,
flat output-channel map, and byte-0 → (target_module, channel)
resolution.

The resolver runs in production but ships in logging-only mode —
``PcLinkDecoder.decode_chunk`` still returns ``[]`` so the merge
layer doesn't ingest its output. These tests pin the resolver's
behaviour against fdebrus's install (full ``nikobus.modules.json``
plus 9 registry + 9 link records from the trace), the only install
where we have ground-truth channel counts to validate against.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from nikobus_connect.discovery.pc_link_decoder import PcLinkDecoder
from nikobus_connect.discovery.pc_record_parser import (
    OUTPUT_BEARING_DEVICE_TYPES,
    LinkRecord,
    ModuleRegistryRecord,
    RegistryBuffer,
    build_flat_channel_map,
    parse_pc_record,
    resolve_link_target,
)


# fdebrus install — registry order from TRACE_REGISTRY in
# tests/test_pc_record_parser.py, with channel counts confirmed
# against the published nikobus.modules.json.
FDEBRUS_REGISTRY_ORDER = [
    ("0E6C", 0x03, 12),  # dimmer
    ("86F5", 0x0A, 0),   # PC Link self — excluded from flat map
    ("9105", 0x02, 6),   # roller
    ("8394", 0x02, 6),   # roller
    ("C9A5", 0x01, 12),  # switch
    ("940C", 0x08, 0),   # PC Logic — excluded from flat map
    ("5B05", 0x31, 4),   # compact switch
    ("4707", 0x01, 12),  # switch
    ("966C", 0x42, 0),   # feedback — excluded from flat map
]


def _make_fdebrus_coordinator() -> MagicMock:
    """Coordinator stub that mirrors fdebrus's published install."""

    counts = {addr: ch for addr, _type, ch in FDEBRUS_REGISTRY_ORDER}
    coord = MagicMock()
    coord.dict_module_data = {
        "switch_module": {"C9A5": {}, "4707": {}, "5B05": {}},
        "dimmer_module": {"0E6C": {}},
        "roller_module": {"9105": {}, "8394": {}},
        "pc_link": {"86F5": {}},
        "pc_logic": {"940C": {}},
        "feedback_module": {"966C": {}},
    }
    coord.get_module_channel_count = MagicMock(side_effect=lambda addr: counts.get(addr, 0))
    return coord


def _record(address: str, device_type: int, type_slot: int = 1) -> ModuleRegistryRecord:
    return ModuleRegistryRecord(
        raw_hex="03000000{:02X}000000{:02X}{:02X}0000{:02X}000000".format(
            device_type,
            int(address[2:4], 16),
            int(address[0:2], 16),
            type_slot,
        ).upper(),
        device_type=device_type,
        address=address.upper(),
        type_slot=type_slot,
    )


# ---------------------------------------------------------------------------
# RegistryBuffer
# ---------------------------------------------------------------------------


def test_registry_buffer_starts_empty():
    buf = RegistryBuffer()
    assert len(buf) == 0
    assert not buf
    assert buf.records == ()


def test_registry_buffer_appends_in_encounter_order():
    """Encounter order is the contract — link record byte-0 indexing
    depends on it. The buffer must not sort or rearrange."""

    buf = RegistryBuffer()
    buf.add(_record("0E6C", 0x03))
    buf.add(_record("9105", 0x02))
    buf.add(_record("C9A5", 0x01))

    assert [r.address for r in buf.records] == ["0E6C", "9105", "C9A5"]


def test_registry_buffer_dedups_duplicate_address():
    """The PC Link sometimes re-emits the same register — adding a
    duplicate must not double-count the module in the flat map."""

    buf = RegistryBuffer()
    first = _record("0E6C", 0x03, type_slot=1)
    duplicate = _record("0E6C", 0x03, type_slot=1)

    assert buf.add(first) is True
    assert buf.add(duplicate) is False
    assert len(buf) == 1


def test_registry_buffer_dedup_is_case_insensitive():
    buf = RegistryBuffer()
    assert buf.add(_record("0e6c", 0x03)) is True
    assert buf.add(_record("0E6C", 0x03)) is False


def test_registry_buffer_reset_clears():
    buf = RegistryBuffer()
    buf.add(_record("0E6C", 0x03))
    buf.add(_record("9105", 0x02))
    buf.reset()

    assert len(buf) == 0
    # Reset must also clear the seen-address set so the same record
    # can be re-added on a subsequent scan.
    assert buf.add(_record("0E6C", 0x03)) is True


# ---------------------------------------------------------------------------
# OUTPUT_BEARING_DEVICE_TYPES
# ---------------------------------------------------------------------------


def test_output_bearing_types_include_switches_dimmers_rollers():
    """Switches (01, 09, 31), dimmers (03, 32), rollers (02) drive
    loads — they must be in the output-bearing set."""

    assert 0x01 in OUTPUT_BEARING_DEVICE_TYPES  # switch
    assert 0x02 in OUTPUT_BEARING_DEVICE_TYPES  # roller
    assert 0x03 in OUTPUT_BEARING_DEVICE_TYPES  # dimmer
    assert 0x09 in OUTPUT_BEARING_DEVICE_TYPES  # compact switch
    assert 0x31 in OUTPUT_BEARING_DEVICE_TYPES  # compact switch variant
    assert 0x32 in OUTPUT_BEARING_DEVICE_TYPES  # compact dim


def test_output_bearing_types_exclude_controllers_and_input_modules():
    """PC Link, PC Logic, feedback, audio dist, and modular interface
    inputs don't drive outputs and must be excluded from the flat
    channel map."""

    assert 0x08 not in OUTPUT_BEARING_DEVICE_TYPES  # PC Logic
    assert 0x0A not in OUTPUT_BEARING_DEVICE_TYPES  # PC Link
    assert 0x2B not in OUTPUT_BEARING_DEVICE_TYPES  # Audio Distribution
    assert 0x37 not in OUTPUT_BEARING_DEVICE_TYPES  # Modular Interface inputs
    assert 0x42 not in OUTPUT_BEARING_DEVICE_TYPES  # Feedback Module


# ---------------------------------------------------------------------------
# build_flat_channel_map
# ---------------------------------------------------------------------------


def test_build_flat_channel_map_for_fdebrus_install():
    """The 9-module registry from the trace must yield a 52-channel
    flat map: 0E6C(12) + 9105(6) + 8394(6) + C9A5(12) + 5B05(4) +
    4707(12). The three excluded modules (PC Link self, PC Logic,
    feedback) contribute zero entries."""

    buf = RegistryBuffer()
    for addr, dtype, _ch in FDEBRUS_REGISTRY_ORDER:
        buf.add(_record(addr, dtype))

    coord = _make_fdebrus_coordinator()
    flat = build_flat_channel_map(buf, coord)

    assert len(flat) == 12 + 6 + 6 + 12 + 4 + 12  # = 52
    assert flat[0] == ("0E6C", 1)
    assert flat[11] == ("0E6C", 12)
    assert flat[12] == ("9105", 1)
    assert flat[17] == ("9105", 6)
    assert flat[18] == ("8394", 1)
    assert flat[23] == ("8394", 6)
    assert flat[24] == ("C9A5", 1)
    assert flat[35] == ("C9A5", 12)
    assert flat[36] == ("5B05", 1)
    assert flat[39] == ("5B05", 4)
    assert flat[40] == ("4707", 1)
    assert flat[51] == ("4707", 12)


def test_build_flat_channel_map_skips_non_output_device_types():
    """A registry containing only non-output modules must produce
    an empty flat map regardless of how many entries it has."""

    buf = RegistryBuffer()
    # PC Link self, PC Logic, feedback, audio dist, modular interface.
    buf.add(_record("86F5", 0x0A))
    buf.add(_record("940C", 0x08))
    buf.add(_record("966C", 0x42))
    buf.add(_record("8334", 0x2B))
    buf.add(_record("5278", 0x37))

    coord = _make_fdebrus_coordinator()
    # Coordinator returns 0 for unknown addresses; even if it didn't,
    # the device-type filter alone should drop these.
    coord.get_module_channel_count = MagicMock(return_value=6)

    assert build_flat_channel_map(buf, coord) == []


def test_build_flat_channel_map_skips_modules_with_zero_channel_count():
    """If the coordinator can't size a module (returns 0 / None),
    that module is silently skipped — its entries can't be safely
    placed in the flat map."""

    buf = RegistryBuffer()
    buf.add(_record("0E6C", 0x03))
    buf.add(_record("9105", 0x02))

    coord = MagicMock()
    coord.get_module_channel_count = MagicMock(side_effect=lambda addr: {
        "0E6C": 0,  # coordinator can't size — skipped
        "9105": 6,
    }.get(addr, 0))

    flat = build_flat_channel_map(buf, coord)
    assert flat == [
        ("9105", 1), ("9105", 2), ("9105", 3),
        ("9105", 4), ("9105", 5), ("9105", 6),
    ]


def test_build_flat_channel_map_handles_missing_coordinator():
    """A ``None`` coordinator returns an empty map without raising."""

    buf = RegistryBuffer()
    buf.add(_record("0E6C", 0x03))
    assert build_flat_channel_map(buf, None) == []


def test_build_flat_channel_map_handles_missing_get_count_method():
    """A coordinator without ``get_module_channel_count`` returns an
    empty map without raising — the lookup is treated as failed."""

    buf = RegistryBuffer()
    buf.add(_record("0E6C", 0x03))

    coord = object()  # bare object — no method
    assert build_flat_channel_map(buf, coord) == []


# ---------------------------------------------------------------------------
# resolve_link_target
# ---------------------------------------------------------------------------


def test_resolve_link_target_pins_known_traces_to_expected_pairs():
    """Channel indices observed in fdebrus's link records should
    resolve to ``(target_module, channel)`` pairs that match the
    flat-map slot for that index. These are the assertions the
    Stage 2b hypothesis lives or dies on; if the byte-0 →
    flat-channel-index mapping is wrong, these break."""

    buf = RegistryBuffer()
    for addr, dtype, _ch in FDEBRUS_REGISTRY_ORDER:
        buf.add(_record(addr, dtype))

    coord = _make_fdebrus_coordinator()

    # Each (channel_idx, expected_module, expected_channel) pinned
    # against the flat map order computed in
    # test_build_flat_channel_map_for_fdebrus_install.
    cases = [
        (0x04, "0E6C", 5),
        (0x05, "0E6C", 6),
        (0x09, "0E6C", 10),
        (0x0B, "0E6C", 12),
        (0x0C, "9105", 1),
        (0x11, "9105", 6),
        (0x12, "8394", 1),
        (0x18, "C9A5", 1),
        (0x21, "C9A5", 10),
        (0x24, "5B05", 1),
        (0x28, "4707", 1),
        (0x33, "4707", 12),
    ]
    for idx, expected_addr, expected_ch in cases:
        target = resolve_link_target(idx, buf, coord)
        assert target == (expected_addr, expected_ch), (
            f"channel_idx=0x{idx:02X} resolved to {target}, "
            f"expected ({expected_addr}, {expected_ch})"
        )


def test_resolve_link_target_returns_none_out_of_range():
    """Indices past the flat-map length resolve to ``None``. The
    flat map is 52 entries for fdebrus; idx 52 is the first
    out-of-range value."""

    buf = RegistryBuffer()
    for addr, dtype, _ch in FDEBRUS_REGISTRY_ORDER:
        buf.add(_record(addr, dtype))

    coord = _make_fdebrus_coordinator()
    assert resolve_link_target(52, buf, coord) is None
    assert resolve_link_target(0xFF, buf, coord) is None


def test_resolve_link_target_returns_none_for_negative_index():
    buf = RegistryBuffer()
    buf.add(_record("0E6C", 0x03))
    coord = _make_fdebrus_coordinator()

    assert resolve_link_target(-1, buf, coord) is None


def test_resolve_link_target_returns_none_for_empty_registry():
    """Without registry data, no resolution is possible — early link
    records (before the registry is populated) return ``None``."""

    buf = RegistryBuffer()
    coord = _make_fdebrus_coordinator()

    assert resolve_link_target(0x04, buf, coord) is None


def test_resolve_link_target_returns_none_when_registry_only_has_excluded_modules():
    """Even with registry entries, if none are output-bearing
    (PC Link self, PC Logic, feedback, etc.), the flat map is
    empty and resolution fails."""

    buf = RegistryBuffer()
    buf.add(_record("86F5", 0x0A))  # PC Link self
    buf.add(_record("940C", 0x08))  # PC Logic
    buf.add(_record("966C", 0x42))  # feedback

    coord = _make_fdebrus_coordinator()
    assert resolve_link_target(0x00, buf, coord) is None


# ---------------------------------------------------------------------------
# PcLinkDecoder integration: registry accumulation + link-target log
# ---------------------------------------------------------------------------


def test_pc_link_decoder_accumulates_registry_across_chunks():
    """A scan feeds chunks one-by-one; the decoder's per-instance
    registry must keep records across calls."""

    coord = _make_fdebrus_coordinator()
    decoder = PcLinkDecoder(coord)

    # Three registry chunks from the fdebrus trace.
    decoder.decode_chunk("03000000030000006C0E000001000000")  # 0E6C dimmer
    decoder.decode_chunk("030000000A000000F586000001000000")  # 86F5 PC Link
    decoder.decode_chunk("03000000020000000591000001000000")  # 9105 roller

    addresses = [r.address for r in decoder._registry.records]
    assert addresses == ["0E6C", "86F5", "9105"]


def test_pc_link_decoder_logs_resolved_target_for_link_record(caplog):
    """After a link record is parsed and the registry is populated,
    the decoder logs an INFO ``link target`` line carrying the
    resolved ``(target, channel)`` pair from the flat map."""

    coord = _make_fdebrus_coordinator()
    decoder = PcLinkDecoder(coord)
    decoder.set_module_address("86F5")

    # Feed the full registry first so the flat map is complete.
    registry_chunks = [
        "03000000030000006C0E000001000000",  # 0E6C dimmer
        "030000000A000000F586000001000000",  # 86F5 PC Link self
        "03000000020000000591000001000000",  # 9105 roller
        "03000000020000009483000002000000",  # 8394 roller
        "0300000001000000A5C9000001000000",  # C9A5 switch
        "03000000080000000C94000001000000",  # 940C PC Logic
        "0300000031000000055B000002000000",  # 5B05 compact switch
        "03000000010000000747000003000000",  # 4707 switch
        "03000000420000006C96000001000000",  # 966C feedback
    ]
    for chunk in registry_chunks:
        decoder.decode_chunk(chunk)

    # Then a link record with channel_idx=0x21 (33 dec). On the
    # fdebrus flat map, idx 33 → (C9A5, 10).
    with caplog.at_level(logging.INFO, logger="nikobus_connect.discovery.pc_link_decoder"):
        decoder.decode_chunk("210000001F000080F6582E0006000000")

    assert "PC-Link link target" in caplog.text
    assert "channel_idx=0x21" in caplog.text
    assert "resolved=C9A5" in caplog.text
    assert "ch=10" in caplog.text


def test_pc_link_decoder_logs_unresolved_at_debug_when_registry_incomplete(caplog):
    """A link record whose channel_idx exceeds the current flat-map
    length (e.g. registry not yet seen, or idx beyond all output
    channels) is logged at DEBUG, not INFO. Keeps the INFO stream
    clean for users."""

    coord = _make_fdebrus_coordinator()
    decoder = PcLinkDecoder(coord)
    decoder.set_module_address("86F5")

    # No registry chunks fed — flat map is empty.
    with caplog.at_level(logging.DEBUG, logger="nikobus_connect.discovery.pc_link_decoder"):
        decoder.decode_chunk("0400000006000080B443180001000000")

    assert "PC-Link link target" in caplog.text
    assert "resolved=None" in caplog.text
    # And the INFO-level "link record" line is still emitted...
    assert "PC-Link link record" in caplog.text
    # ...but the resolution itself is at DEBUG.
    debug_lines = [
        rec for rec in caplog.records
        if "link target" in rec.message and rec.levelno == logging.DEBUG
    ]
    assert len(debug_lines) == 1


def test_pc_link_decoder_decode_chunk_still_returns_empty_list():
    """Stage 2b ships in logging-only mode: the resolver runs and
    logs targets, but ``decode_chunk`` keeps returning ``[]`` so
    the merge layer doesn't ingest PC-Link link records. This
    contract stays until the byte-0 → channel-index hypothesis is
    cross-validated against a button-press → output ground truth."""

    coord = _make_fdebrus_coordinator()
    decoder = PcLinkDecoder(coord)
    decoder.set_module_address("86F5")

    # A real registry record and a real link record from the trace.
    assert decoder.decode_chunk("03000000030000006C0E000001000000") == []
    assert decoder.decode_chunk("0400000006000080B443180001000000") == []


def test_pc_link_decoder_reset_registry_clears_buffer_between_scans():
    """``reset_registry`` is the public hook a discovery loop uses
    to clear state at the start of each PC-Link scan."""

    coord = _make_fdebrus_coordinator()
    decoder = PcLinkDecoder(coord)
    decoder.decode_chunk("03000000030000006C0E000001000000")
    assert len(decoder._registry) == 1

    decoder.reset_registry()
    assert len(decoder._registry) == 0
