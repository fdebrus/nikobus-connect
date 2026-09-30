"""The PC-Logic's own input table.

Layout from the vendor plugin's composer (``Niko_05_200``,
``CalcMemoryMap`` block 2): a two-byte little-endian count at byte
998, six-byte records from byte 1000 — ``[addr 3] [input] [slot]
[mode]`` — room for 1536. No capture of a programmed table exists yet;
these tests pin the decoder to the plugin's writer.
"""

from __future__ import annotations

from nikobus_connect.discovery.pc_logic_decoder import (
    PC_LOGIC_INPUT_COUNT_ADDRESS,
    PC_LOGIC_INPUT_TABLE_ADDRESS,
    PcLogicDecoder,
    decode_input_link,
)

FIRST_BLOCK = PC_LOGIC_INPUT_COUNT_ADDRESS // 16  # 0x3E

# Plate 124A36, key 1C (code 0): the software's record form is the
# plate shifted left by two, 4928D8, and the key's #N frame 1B1492
# (Nikobus-HA #519's validating install).
KEY_1C = bytes.fromhex("4928D8")


def _record(n: int, address: bytes = KEY_1C) -> bytes:
    return address + bytes([n % 12, n % 4, 0x40 | n])


def _image(records: list[bytes], count: int | None = None) -> bytes:
    image = bytearray(b"\xff" * 0x3000)
    count = len(records) if count is None else count
    image[PC_LOGIC_INPUT_COUNT_ADDRESS : PC_LOGIC_INPUT_COUNT_ADDRESS + 2] = count.to_bytes(
        2, "little"
    )
    body = b"".join(records)
    image[PC_LOGIC_INPUT_TABLE_ADDRESS : PC_LOGIC_INPUT_TABLE_ADDRESS + len(body)] = body
    return bytes(image)


def _feed(decoder: PcLogicDecoder, image: bytes, block: int, buffer: str = "") -> dict:
    decoder.set_current_block(f"{block >> 8:02X}", block & 0xFF)
    data = image[block * 16 : (block + 1) * 16].hex().upper()
    analysis = decoder.analyze_frame_payload(buffer, data + "000000")
    assert analysis is not None
    return analysis


# --- one record ------------------------------------------------------------


def test_a_record_decodes_to_the_key_and_its_bus_frame() -> None:
    decoded = decode_input_link(KEY_1C + bytes([2, 5, 0x41]))
    assert decoded is not None
    assert decoded["record_address"] == "4928D8"
    assert decoded["wire_address"] == "1B1492"
    assert decoded["key_code"] == 0
    assert decoded["input_index"] == 2
    assert decoded["slot"] == 5
    assert decoded["mode_raw"] == 0x41
    assert decoded["record_source"] == "pc_logic_input_table"
    assert decoded["button_address"] is not None


def test_erased_and_short_records_are_nothing() -> None:
    assert decode_input_link(b"\xff" * 6) is None
    assert decode_input_link(KEY_1C) is None


# --- the table across blocks -----------------------------------------------


def test_the_fixed_plan_sees_four_records_and_asks_for_the_rest() -> None:
    decoder = PcLogicDecoder(None)
    decoder.set_module_address("940C")
    image = _image([_record(i) for i in range(10)])

    # The plan's sub-00 pass ends at block 0x3F: the count, one record
    # and two bytes of the next in 0x3E, three more records in 0x3F.
    for block in (FIRST_BLOCK, FIRST_BLOCK + 1):
        analysis = _feed(decoder, image, block)
        assert analysis["chunks"] == []
    assert decoder._input_count == 10
    assert len(decoder.input_links) == 4

    # 60 record bytes from byte 1000 end at 1059, block 0x42.
    assert decoder.extension_passes() == (("00", (0x40, 0x41, 0x42)),)
    for block in (0x40, 0x41, 0x42):
        _feed(decoder, image, block)
    assert [link["raw"] for link in decoder.input_links] == [
        _record(i).hex().upper() for i in range(10)
    ]
    assert decoder.extension_passes() == ()

    decoder.reset_scan_buffers()
    assert decoder.input_links == []
    assert len(decoder.input_links_by_module["940C"]) == 10


def test_blocks_outside_the_table_still_reach_the_record_parser() -> None:
    decoder = PcLogicDecoder(None)
    image = _image([_record(0)])
    registry_like = "03000000010000000C9400000100000000"[:32]
    decoder.set_current_block("00", 0x06)
    analysis = decoder.analyze_frame_payload("", registry_like + "000000")
    assert analysis is not None
    assert analysis["chunks"] == [registry_like]
    # Before the count is known only the plan's two blocks are claimed.
    _feed(decoder, image, FIRST_BLOCK)
    decoder.set_current_block("00", 0x50)
    analysis = decoder.analyze_frame_payload("", registry_like + "000000")
    assert analysis is not None
    assert analysis["chunks"] == [registry_like]


def test_a_gap_in_the_table_ends_the_read() -> None:
    decoder = PcLogicDecoder(None)
    image = _image([_record(i) for i in range(10)])
    _feed(decoder, image, FIRST_BLOCK)
    _feed(decoder, image, FIRST_BLOCK + 2)  # 0x3F never answered
    assert len(decoder.input_links) == 1
    assert decoder.extension_passes() == ()


def test_an_erased_count_reads_as_an_empty_table() -> None:
    decoder = PcLogicDecoder(None)
    image = bytes(b"\xff" * 0x3000)
    _feed(decoder, image, FIRST_BLOCK)
    assert decoder._input_count == 0
    assert decoder.input_links == []
    assert decoder.extension_passes() == ()


def test_an_erased_slot_inside_the_count_shortens_the_table() -> None:
    decoder = PcLogicDecoder(None)
    image = _image([_record(0), _record(1)], count=5)
    _feed(decoder, image, FIRST_BLOCK)
    _feed(decoder, image, FIRST_BLOCK + 1)
    assert len(decoder.input_links) == 2
    assert decoder._input_count == 2
    assert decoder.extension_passes() == ()


def test_an_extension_that_brings_nothing_is_not_repeated() -> None:
    decoder = PcLogicDecoder(None)
    image = _image([_record(i) for i in range(10)])
    _feed(decoder, image, FIRST_BLOCK)
    _feed(decoder, image, FIRST_BLOCK + 1)
    assert decoder.extension_passes()
    assert decoder.extension_passes() == ()
