"""The 340-00112 memory image, as the vendor plugin (Niko_05_010.dll)
writes it — layout pinned from the decompiled plugin, nothing read from
a module."""

from __future__ import annotations

import pytest

from nikobus_connect.rgb_memory import (
    RGB_COLOUR_PATH_BLOCK,
    RGB_CONFIG_BLOCK,
    RGB_D65_XY,
    RGB_IMAGE_BLOCKS,
    RGB_IMAGE_SIZE,
    RGB_LED_PROFILE_BLOCK,
    RGB_LINK_RECORD_COUNT,
    RGB_LINK_RECORD_SIZE,
    RGB_LINK_TABLE_BLOCK,
    RGB_READBACK_LENGTHS,
    RGB_T1_SECONDS,
    RGB_T2_SECONDS,
    RgbLinkRecord,
    address_from_wire,
    decode_rgb_colour_paths,
    decode_rgb_config,
    decode_rgb_link_table,
    is_empty_rgb_link_record,
    param1_is_forced,
    record_address,
    reverse_24,
)


def test_the_image_is_four_contiguous_blocks() -> None:
    assert RGB_IMAGE_BLOCKS[0].start == 0
    for previous, block in zip(RGB_IMAGE_BLOCKS, RGB_IMAGE_BLOCKS[1:]):
        assert block.start == previous.end
    assert RGB_IMAGE_BLOCKS[-1].end == RGB_IMAGE_SIZE == 0x1F20


def test_the_link_table_holds_128_records_of_18_bytes() -> None:
    assert RGB_LINK_TABLE_BLOCK.start == 0x190
    assert RGB_LINK_TABLE_BLOCK.length == RGB_LINK_RECORD_COUNT * RGB_LINK_RECORD_SIZE == 0x900


def test_blocks_map_onto_16_byte_registers() -> None:
    """Block index = byte address / 16, sub-byte high and register low —
    the library's convention for other modules; unverified here."""
    assert RGB_LED_PROFILE_BLOCK.registers[0] == ("00", 0x00)
    assert RGB_LED_PROFILE_BLOCK.registers[-1] == ("00", 0x18)
    assert RGB_LINK_TABLE_BLOCK.registers[0] == ("00", 0x19)
    assert RGB_LINK_TABLE_BLOCK.registers[-1] == ("00", 0xA8)
    assert len(RGB_LINK_TABLE_BLOCK.registers) == 144
    assert RGB_COLOUR_PATH_BLOCK.registers[0] == ("00", 0xA9)
    assert RGB_COLOUR_PATH_BLOCK.registers[-1] == ("01", 0xF0)
    assert RGB_CONFIG_BLOCK.registers == (("01", 0xF1),)


def test_the_vendor_reads_back_everything_but_the_profile_and_the_links() -> None:
    assert RGB_READBACK_LENGTHS["link_table"] == 0
    assert RGB_READBACK_LENGTHS["led_profile"] == 0
    assert RGB_READBACK_LENGTHS["colour_paths"] == 0x1480
    assert RGB_READBACK_LENGTHS["config"] == 16


def test_timer_tables() -> None:
    assert RGB_T1_SECONDS[0] == 10 and RGB_T1_SECONDS[1] == 60 and RGB_T1_SECONDS[-1] == 7200
    assert RGB_T2_SECONDS[0] == 1 and RGB_T2_SECONDS[-1] == 300
    assert len(RGB_T1_SECONDS) == len(RGB_T2_SECONDS) == 16


def test_parameter_1_is_forced_to_15_for_the_plugin_s_modes() -> None:
    assert {m for m in range(32) if param1_is_forced(m)} == {3, 7, 9, 10} | set(range(14, 32))


M19_RECORD = bytes.fromhex("4928D898000F012C" + "FF" * 10)


def test_the_validating_install_s_m19_link() -> None:
    """Nikobus-HA #519: plate 124A36, key 1C (code 0), mode M19 on
    output 1, pressing #N1B1492 on the bus. The record the plugin
    composes for it holds the address in the software's form,
    plate << 2 | key, which bit-reverses to the wire form; parameter 1
    forced to 15; the default parameter 2 (15) as 300 s."""
    record = RgbLinkRecord.from_bytes(M19_RECORD)
    assert record.address == record_address(0x124A36, 0) == 0x4928D8
    assert record.address_hex == "4928D8"
    assert record.wire_address == "1B1492"
    assert (record.mode, record.channel) == (19, 0)
    assert record.mode_label == "M19 (Start/stop scenario)"
    assert record.param1 == 15 and record.param1_seconds is None  # not a timer in mode 19
    assert record.param2_seconds == 300
    assert record.colour_xy is None
    assert not record.has_colour_path


def test_the_address_form_matches_both_observed_keys() -> None:
    """The same plate's key 1D (code 2) was seen as #N5B1492."""
    assert f"{reverse_24(record_address(0x124A36, 2)):06X}" == "5B1492"
    assert address_from_wire("#N1B1492") == 0x4928D8
    assert address_from_wire("5B1492") == 0x4928DA
    assert reverse_24(reverse_24(0x123456)) == 0x123456


def test_mode_6_parameter_1_is_a_t1_timer() -> None:
    raw = bytes.fromhex("4928D8" "32" "00B4" "0001" + "FF" * 10)  # mode 6, channel 2
    record = RgbLinkRecord.from_bytes(raw)
    assert (record.mode, record.channel) == (6, 2)
    assert record.param1_seconds == 180 == RGB_T1_SECONDS[3]
    assert record.param2_seconds == 1


def test_a_mode_3_default_colour_decodes_as_d65_white() -> None:
    raw = bytes.fromhex("000001" "18" "000F" "012C" "500D543A" "FFFE" "0000" "FF" "FF")
    record = RgbLinkRecord.from_bytes(raw)
    assert record.mode == 3
    x, y = record.colour_xy or (0.0, 0.0)
    assert round(x, 4) == 0.3127 and round(y, 4) == 0.3290
    assert RGB_D65_XY == (0x500D, 0x543A)
    assert record.level == 0xFFFE and record.param7 == 0


def test_colour_path_index_and_flag() -> None:
    raw = bytes.fromhex("000001" "98" "000F" "012C" "FFFFFFFF" "FFFF" "FFFF" "85" "FF")
    record = RgbLinkRecord.from_bytes(raw)
    assert record.colour_path == 5 and record.colour_path_flag
    assert record.has_colour_path
    with pytest.raises(ValueError):
        RgbLinkRecord.from_bytes(b"\x00" * 5)


def test_an_empty_slot_is_all_ff() -> None:
    assert is_empty_rgb_link_record(b"\xff" * 18)
    assert not is_empty_rgb_link_record(b"\xff" * 17 + b"\x00")


def test_table_decode_skips_empty_slots() -> None:
    second = bytes.fromhex("4928DA68000F012C" + "FF" * 10)  # mode 13, channel 0
    table = M19_RECORD + second + b"\xff" * (RGB_LINK_TABLE_BLOCK.length - 36)
    assert len(table) == RGB_LINK_TABLE_BLOCK.length
    decoded = decode_rgb_link_table(table)
    assert [r.address_hex for r in decoded] == ["4928D8", "4928DA"]
    assert decoded[1].mode == 13
    # a hole in the table is skipped, not a stop
    holed = b"\xff" * 18 + table[18:]
    assert [r.address_hex for r in decode_rgb_link_table(holed)] == ["4928DA"]
    # a trailing partial slot is ignored
    assert decode_rgb_link_table(table[:30]) == (decoded[0],)


def test_config_record() -> None:
    raw = bytes.fromhex("0064" "0064" "FF01" "0001" "0002" "FFFFFFFFFFFF")
    cfg = decode_rgb_config(raw)
    assert cfg.value_10001 == 100 and cfg.value_10002 == 100
    assert cfg.flag_10005 and cfg.not_own_component
    assert cfg.variant == 2 and cfg.raw == raw
    with pytest.raises(ValueError):
        decode_rgb_config(raw[:8])


def test_colour_paths() -> None:
    block = bytearray(b"\xff" * RGB_COLOUR_PATH_BLOCK.length)
    # two points at index 0 and 1
    block[0:10] = bytes.fromhex("500D543A" "0000" "FFFF" "FFFF")
    block[10:20] = bytes.fromhex("12345678" "0010" "8000" "FFFF")
    # path 3: start 0, closed loop, two points
    block[5120 + 12 : 5120 + 16] = bytes.fromhex("0000" "02" "02")
    paths = decode_rgb_colour_paths(block)
    assert len(paths) == 1
    path = paths[0]
    assert path.number == 3 and path.start == 0 and path.point_count == 2
    assert path.closed_loop and not path.jump_points
    assert path.points[0].x == 0x500D and path.points[0].y == 0x543A
    assert path.points[1].cumulative_speed == 0x10 and path.points[1].rel_lumi == 0x8000
    with pytest.raises(ValueError):
        decode_rgb_colour_paths(b"\xff" * 10)
