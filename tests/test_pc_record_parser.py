"""Stage-2a parser tests, pinned against a real Nikobus PC-software
serial trace captured against a PC Link (86F5) on roswennen's install.

The trace gives us 49 register reads across the productive range
0xA3..0xD3 (sub=04). Decoded:

  - 1 all-FF empty pre-table marker (0xA3, body partially empty),
  - 9 module-registry records (0xA4..0xAC) — every byte aligns with
    DEVICE_TYPES and the addresses match the user's actual install,
  - 38 link records (0xAD..0xD2) with byte-0 channel indices, mode
    bytes, flag bytes (0x80 / 0x40 / 0x00), and 3-byte payload
    fields,
  - 1 trailer (0xD3, all-FF body) marking end-of-table.

These tests pin the parser's interpretation. If the layout assumption
is wrong on a different user's hardware, these tests will need to
adapt — and that's the whole point of pinning them.
"""

from __future__ import annotations

import pytest

from nikobus_connect.discovery.pc_record_parser import (
    LinkRecord,
    ModuleRegistryRecord,
    RECORD_HEX_LEN,
    is_empty_record,
    parse_pc_record,
)


# Real bodies from the captured trace. (register_hex, body_hex).
TRACE_REGISTRY = [
    ("A4", "03000000030000006C0E000001000000"),  # dimmer 0E6C
    ("A5", "030000000A000000F586000001000000"),  # PC Link 86F5 (self)
    ("A6", "03000000020000000591000001000000"),  # roller 9105
    ("A7", "03000000020000009483000002000000"),  # roller 8394
    ("A8", "0300000001000000A5C9000001000000"),  # switch C9A5
    ("A9", "03000000080000000C94000001000000"),  # PC Logic 940C
    ("AA", "0300000031000000055B000002000000"),  # compact switch 5B05
    ("AB", "03000000010000000747000003000000"),  # switch 4707
    ("AC", "03000000420000006C96000001000000"),  # feedback 966C
]

TRACE_LINKS = [
    # (reg, body_hex, expected channel_index, mode_byte, flag_byte, payload, slot)
    ("AD", "0400000006000080B443180001000000", 0x04, 0x06, 0x80, "B44318", 0x01),
    ("AE", "040000000C000040801C0D0001000000", 0x04, 0x0C, 0x40, "801C0D", 0x01),
    ("AF", "04000000230000805012200001000000", 0x04, 0x23, 0x80, "501220", 0x01),
    ("B0", "04000000230000801549200004000000", 0x04, 0x23, 0x80, "154920", 0x04),
    ("B1", "050000000600008054C5170002000000", 0x05, 0x06, 0x80, "54C517", 0x02),
    ("B2", "0500000006000080121F1D000F000000", 0x05, 0x06, 0x80, "121F1D", 0x0F),
    ("B6", "090000000C000040C0FE0F0002000000", 0x09, 0x0C, 0x40, "C0FE0F", 0x02),
    ("BB", "0B00000006000000A0FB1C000D000000", 0x0B, 0x06, 0x00, "A0FB1C", 0x0D),
    ("D2", "210000001F000080F6582E0006000000", 0x21, 0x1F, 0x80, "F6582E", 0x06),
]

TRACE_TRAILER = "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"  # reg 0xD3 body

# Address of the user's PC Logic, found in the registry.
PC_LOGIC_ADDRESS = "940C"


# ---------------------------------------------------------------------------
# Empty / sentinel handling
# ---------------------------------------------------------------------------


def test_record_hex_len_is_sixteen_bytes():
    """16 bytes / 32 hex chars per register response — pinned because
    Stage 1's 12-char (6-byte) guess was wrong and a Stage-2 reader
    relying on the right value is the whole point."""

    assert RECORD_HEX_LEN == 32


def test_is_empty_record_recognises_all_ff_chunk():
    assert is_empty_record(TRACE_TRAILER) is True
    assert is_empty_record("ffffffffffffffffffffffffffffffff") is True


def test_is_empty_record_rejects_non_ff_chunks():
    assert is_empty_record(TRACE_REGISTRY[0][1]) is False
    assert is_empty_record(TRACE_LINKS[0][1]) is False
    assert is_empty_record("") is False


def test_parse_pc_record_returns_none_for_empty_chunk():
    assert parse_pc_record(TRACE_TRAILER) is None


def test_parse_pc_record_returns_none_for_wrong_length():
    assert parse_pc_record("0300000003000000") is None  # too short
    assert parse_pc_record(TRACE_REGISTRY[0][1] + "00") is None  # too long


def test_parse_pc_record_returns_none_for_non_hex():
    assert parse_pc_record("ZZ" * 16) is None


# ---------------------------------------------------------------------------
# Module-registry parsing — all 9 records from the trace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reg_hex,body_hex", TRACE_REGISTRY)
def test_registry_record_parses_to_module_registry_type(reg_hex, body_hex):
    record = parse_pc_record(body_hex)
    assert isinstance(record, ModuleRegistryRecord), (
        f"reg=0x{reg_hex} body={body_hex} should be a registry record"
    )


def test_registry_record_extracts_dimmer_correctly():
    record = parse_pc_record(TRACE_REGISTRY[0][1])
    assert record.device_type == 0x03
    assert record.address == "0E6C"
    assert record.type_slot == 1


def test_registry_record_extracts_pc_logic_correctly():
    """PC Logic at 940C — the device the user's install has and we've
    been chasing for two stages of work. Confirms the parser sees it
    as a known module via the PC Link's registry."""

    record = parse_pc_record(TRACE_REGISTRY[5][1])  # 0xA9
    assert record.device_type == 0x08
    assert record.address == PC_LOGIC_ADDRESS


def test_registry_record_extracts_pc_link_self_reference():
    """Reg 0xA5 holds the PC Link's own address (86F5). The controller
    indexes itself in its registry; the parser must not blow up."""

    record = parse_pc_record(TRACE_REGISTRY[1][1])
    assert record.device_type == 0x0A
    assert record.address == "86F5"


def test_registry_record_address_is_byte_swapped_to_bus_form():
    """Bytes 8-9 are stored little-endian on-wire (``6C 0E``) and need
    to be swapped to the bus-form address (``0E6C``) that matches what
    the discovery inventory phase reports."""

    record = parse_pc_record(TRACE_REGISTRY[0][1])
    # On-wire bytes 8-9 are "6C 0E"; bus-form is "0E6C".
    assert record.address == "0E6C"


def test_registry_record_type_slot_tracks_per_type_instance_count():
    """The 8394 roller is the second roller in the registry → slot=2.
    The 4707 switch is the third switch → slot=3."""

    second_roller = parse_pc_record(TRACE_REGISTRY[3][1])  # 8394
    assert second_roller.address == "8394"
    assert second_roller.type_slot == 2

    third_switch = parse_pc_record(TRACE_REGISTRY[7][1])  # 4707
    assert third_switch.address == "4707"
    assert third_switch.type_slot == 3


def test_registry_records_cover_all_install_modules():
    """The 9 registry records together cover every module in the
    user's nikobus.modules.json. This is the ground-truth check that
    the parser correctly understands the registry encoding for an
    entire install."""

    addresses = {parse_pc_record(body).address for _, body in TRACE_REGISTRY}
    expected = {"0E6C", "86F5", "9105", "8394", "C9A5", "940C", "5B05", "4707", "966C"}
    assert addresses == expected


# ---------------------------------------------------------------------------
# Link-record parsing — sample from the trace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reg_hex,body_hex,channel_idx,mode,flag,payload,slot", TRACE_LINKS
)
def test_link_record_extracts_all_fields(
    reg_hex, body_hex, channel_idx, mode, flag, payload, slot
):
    record = parse_pc_record(body_hex)
    assert isinstance(record, LinkRecord), (
        f"reg=0x{reg_hex} body={body_hex} should be a link record"
    )
    assert record.channel_index == channel_idx
    assert record.mode_byte == mode
    assert record.flag_byte == flag
    assert record.payload_bytes == payload
    assert record.slot == slot


def test_link_record_channel_index_can_repeat_across_records():
    """Multiple buttons can route to the same channel — byte 0 = 0x04
    appears 4 times across regs 0xAD..0xB0. Parser must not deduplicate."""

    indices = [parse_pc_record(body).channel_index for _, body, *_ in TRACE_LINKS[:4]]
    assert indices == [0x04, 0x04, 0x04, 0x04]


def test_link_record_flag_byte_supports_all_three_observed_values():
    """The trace has flag=0x80 (most common), 0x40 (some channels), and
    0x00 (one record). Parser must accept all three without coercion."""

    seen_flags = {parse_pc_record(body).flag_byte for _, body, *_ in TRACE_LINKS}
    assert {0x80, 0x40, 0x00}.issubset(seen_flags)


# ---------------------------------------------------------------------------
# Type discrimination
# ---------------------------------------------------------------------------


def test_byte_zero_zero_three_routes_to_registry_record():
    record = parse_pc_record("03" + "00" * 15)  # bare marker, all-zero else
    assert isinstance(record, ModuleRegistryRecord)
    assert record.device_type == 0x00
    assert record.address == "0000"


def test_byte_zero_non_zero_three_routes_to_link_record():
    record = parse_pc_record("04" + "00" * 15)
    assert isinstance(record, LinkRecord)
    assert record.channel_index == 0x04


def test_byte_zero_zero_routes_to_registry_record_only_when_marker_matches():
    """A record with byte 0 = 0x00 (not 0x03) is a link record with
    channel_index 0, NOT a registry record. The marker is exact."""

    record = parse_pc_record("00" + "00" * 15)
    assert isinstance(record, LinkRecord)
    assert record.channel_index == 0


# ---------------------------------------------------------------------------
# Near-empty chunk rejection (0.5.1)
# ---------------------------------------------------------------------------
#
# 0.5.0 shipped with an empty-detection that required every hex char
# to be 'F'. On fdebrus's own install the PC Logic register scan
# returned a chunk like ``FFFFFFFFFFFFFFFFFFFFFFFFFFFF05FF`` — all
# bytes 0xFF except for one stray 0x05 at byte offset 14, which the
# parser doesn't even extract. The 0.5.0 parser routed it to
# ``_parse_link_record`` and emitted a phantom record with
# ``channel_idx=0xFF mode=0xFF flag=0xFF payload=FFFFFF slot=0xFF``.
# 0.5.1 tightens the link-record check: if every extracted field
# (channel_idx, mode_byte, flag_byte, payload_bytes, slot) is 0xFF,
# the chunk is rejected as a near-empty bus artefact.


def test_near_empty_chunk_with_stray_byte_is_rejected():
    """The exact chunk observed on fdebrus's PC Logic at 940C
    (response_index=198 and 230, log timestamp 22:17:15 on
    2026-05-03). 0.5.0 emitted a phantom link record; 0.5.1 must
    return None."""

    chunk = "FFFFFFFFFFFFFFFFFFFFFFFFFFFF05FF"
    assert parse_pc_record(chunk) is None


def test_all_ff_extracted_fields_with_non_ff_marker_is_rejected():
    """A chunk where byte 0 is 0xFF (so it's not a registry marker)
    AND every other extracted field is 0xFF must be rejected. Byte 0
    being 0xFF is the literal "no record" marker the controller
    emits; a record with that marker carrying only 0xFF data isn't a
    link, it's an empty register that picked up a bit of bus noise."""

    # All fields the parser extracts are 0xFF; bytes the parser
    # ignores (1-3, 5-6, 11, 13, 15) carry junk that shouldn't matter.
    chunk = "FF" * 16  # full all-FF — pure empty marker
    assert parse_pc_record(chunk) is None

    # Same all-FF in extracted fields but with junk in unused byte 14.
    chunk_with_junk_at_14 = "FF" * 14 + "AA" + "FF"
    assert parse_pc_record(chunk_with_junk_at_14) is None

    # Junk in unused byte 13.
    chunk_with_junk_at_13 = "FF" * 13 + "55" + "FF" * 2
    assert parse_pc_record(chunk_with_junk_at_13) is None


def test_link_record_with_real_data_in_one_field_is_accepted():
    """Even a single non-FF in any of the extracted fields qualifies
    the chunk as a real link record. Only the all-FF combination is
    rejected — anything else is kept and surfaced for downstream
    decoding."""

    # Non-FF only in mode_byte (byte 4, chars 8-9).
    chunk_mode_only = "FF" * 4 + "06" + "FF" * 11
    record = parse_pc_record(chunk_mode_only)
    assert isinstance(record, LinkRecord)
    assert record.channel_index == 0xFF
    assert record.mode_byte == 0x06

    # Non-FF only in slot (byte 12, chars 24-25).
    chunk_slot_only = "FF" * 12 + "01" + "FF" * 3
    record = parse_pc_record(chunk_slot_only)
    assert isinstance(record, LinkRecord)
    assert record.slot == 0x01
