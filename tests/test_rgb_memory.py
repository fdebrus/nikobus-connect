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
    build_rgb_link_record,
    decode_rgb_colour_paths,
    decode_rgb_config,
    decode_rgb_link_table,
    encode_rgb_link_table,
    is_empty_rgb_link_record,
    param1_is_forced,
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


def test_the_validating_install_s_m19_link() -> None:
    """Nikobus-HA #519: plate 124A36, key 1C at key index 0, mode M19
    on output 1. The plugin stores the plate's physical address, forces
    parameter 1 to 15, and the default parameter 2 (15) becomes 300 s."""
    record = build_rgb_link_record(address=0x124A36, mode=19, channel=0)
    assert record.to_bytes().hex().upper() == "124A369800" "0F012C" + "FF" * 10
    assert record.address_hex == "124A36"
    assert record.mode_label == "M19 (Start/stop scenario)"
    assert record.param1_seconds is None  # 15 is not a timer in mode 19
    assert record.param2_seconds == 300
    assert record.colour_xy is None
    assert not record.has_colour_path


def test_mode_6_parameter_1_goes_through_t1() -> None:
    record = build_rgb_link_record(address=0x124A36, mode=6, channel=2, param1=3, param2=0)
    assert record.to_bytes()[3] == (6 << 3) | 2
    assert record.param1_seconds == 180
    assert record.param2_seconds == 1


def test_mode_3_without_a_colour_stores_d65_white() -> None:
    record = build_rgb_link_record(address=0x1, mode=3, channel=0)
    raw = record.to_bytes()
    assert raw[8:12].hex().upper() == "500D543A"
    assert raw[12:14].hex().upper() == "FFFE"
    assert raw[14:16] == b"\x00\x00"
    x, y = record.colour_xy or (0.0, 0.0)
    assert round(x, 4) == 0.3127 and round(y, 4) == 0.3290
    assert RGB_D65_XY == (0x500D, 0x543A)


def test_level_and_param7_transformations() -> None:
    record = build_rgb_link_record(
        address=0x1, mode=16, channel=0, colour_xy=(0x1234, 0x5678), level=0x00050000, param7=0x9000
    )
    raw = record.to_bytes()
    assert raw[8:12].hex().upper() == "12345678"
    assert int.from_bytes(raw[12:14], "big") == 10
    assert int.from_bytes(raw[14:16], "big") == ((0x9000 | 0x20000) >> 2) & 0xFFFF
    small = build_rgb_link_record(address=0x1, mode=16, channel=0, param7=0x1234)
    assert small.param7 == 0x1234


def test_colour_path_index_and_flag() -> None:
    record = build_rgb_link_record(address=0x1, mode=19, channel=0, colour_path=5, colour_path_flag=True)
    assert record.to_bytes()[16] == 0x85
    assert record.has_colour_path
    back = RgbLinkRecord.from_bytes(record.to_bytes())
    assert back.colour_path == 5 and back.colour_path_flag


def test_record_round_trips() -> None:
    record = build_rgb_link_record(
        address=0xABCDEF, mode=17, channel=7, param1=2, param2=9, colour_xy=(1, 2), level=0x00030000
    )
    assert RgbLinkRecord.from_bytes(record.to_bytes()) == record
    with pytest.raises(ValueError):
        RgbLinkRecord.from_bytes(b"\x00" * 5)


def test_an_empty_slot_is_all_ff() -> None:
    assert is_empty_rgb_link_record(b"\xff" * 18)
    assert not is_empty_rgb_link_record(b"\xff" * 17 + b"\x00")


def test_table_round_trips_and_skips_empty_slots() -> None:
    first = build_rgb_link_record(address=0x124A36, mode=19, channel=0)
    second = build_rgb_link_record(address=0x124A36, mode=13, channel=0)
    table = encode_rgb_link_table([first, second])
    assert len(table) == RGB_LINK_TABLE_BLOCK.length
    assert table[36:] == b"\xff" * (RGB_LINK_TABLE_BLOCK.length - 36)
    assert decode_rgb_link_table(table) == (first, second)
    # a hole in the table is skipped, not a stop
    holed = bytearray(table)
    holed[0:18] = b"\xff" * 18
    assert decode_rgb_link_table(holed) == (second,)
    with pytest.raises(ValueError):
        encode_rgb_link_table([first] * (RGB_LINK_RECORD_COUNT + 1))


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
