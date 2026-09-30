"""The audio link table past the fixed band.

The vendor plugin (``Niko_05_202``) writes a two-byte little-endian
record count at byte 4998 and the records from byte 5000, with room
for 1864 of them. The scan plan's fixed band (sub ``01``, registers
``0x38``–``0x5F``) holds about 105; the decoder learns the count from
the head of the band and asks for the rest.
"""

from __future__ import annotations

import json
from pathlib import Path

from nikobus_connect.discovery.audio_decoder import (
    AUDIO_MAX_RECORDS,
    AUDIO_TABLE_LAST_BLOCK,
    AudioDecoder,
    split_link_table,
)
from nikobus_connect.discovery.protocol import blocks_to_sections

FIXTURE = Path(__file__).parent / "fixtures" / "audio_module_8334_bank01.json"
REGISTERS: dict[str, str] = json.loads(FIXTURE.read_text())

BAND_FIRST_BLOCK = 0x138  # sub 01, register 0x38
BAND_BLOCKS = 0x60 - 0x38  # the plan's fixed band


def _record(n: int) -> str:
    """A plausible six-byte record: address, function, object, 01."""
    return f"{n & 0xFF:02X}83CF{n % 32:02X}{n % 4:02X}01"


def _table_blocks(count: int, min_blocks: int = BAND_BLOCKS) -> list[str]:
    """The band as 16-byte blocks: filler, the count, ``count`` records."""
    stream = "FF" * 6 + count.to_bytes(2, "little").hex().upper()
    stream += "".join(_record(i) for i in range(count))
    while len(stream) % 32 or len(stream) // 32 < min_blocks:
        stream += "FF"
    return [stream[i : i + 32] for i in range(0, len(stream), 32)]


def _feed(
    decoder: AudioDecoder, blocks: list[str], first_block: int, buffer: str = ""
) -> tuple[list[str], str]:
    chunks: list[str] = []
    for offset, block in enumerate(blocks):
        number = first_block + offset
        decoder.set_current_block(f"{number >> 8:02X}", number & 0xFF)
        analysis = decoder.analyze_frame_payload(buffer, block + "000000")
        assert analysis is not None
        buffer = analysis["remainder"]
        chunks.extend(analysis["chunks"])
    return chunks, buffer


# --- the count is two bytes ------------------------------------------------


def test_the_count_is_little_endian_over_two_bytes() -> None:
    stream = "".join(_table_blocks(300, min_blocks=0))
    assert len(split_link_table(stream)) == 300
    # The validating module's header, 23 00, is still 35.
    assert len(split_link_table("".join(REGISTERS[k] for k in sorted(REGISTERS)))) == 35


def test_erased_zero_and_oversized_counts_are_not_a_header() -> None:
    assert split_link_table("FFFF" + "FFFF" + _record(0)) == []
    assert split_link_table("FFFF" + "0000" + _record(0)) == []
    too_many = (AUDIO_MAX_RECORDS + 1).to_bytes(2, "little").hex().upper()
    assert split_link_table("FFFF" + too_many + _record(0)) == []
    at_capacity = AUDIO_MAX_RECORDS.to_bytes(2, "little").hex().upper()
    assert split_link_table("FFFF" + at_capacity + _record(0)) == [_record(0)]


# --- the extension ---------------------------------------------------------


def test_a_table_that_fits_the_band_needs_no_extension() -> None:
    decoder = AudioDecoder(None)
    blocks = [REGISTERS[k] for k in sorted(REGISTERS)]
    chunks, _ = _feed(decoder, blocks, BAND_FIRST_BLOCK)
    assert len(chunks) == 35
    assert decoder.extension_passes() == ()


def test_the_band_holds_105_records_and_the_rest_is_asked_for() -> None:
    decoder = AudioDecoder(None)
    blocks = _table_blocks(200)
    chunks, buffer = _feed(decoder, blocks[:BAND_BLOCKS], BAND_FIRST_BLOCK)
    # 640 band bytes − 6 filler − 2 count = 632 bytes: 105 records and
    # two bytes of the 106th.
    assert len(chunks) == 105
    assert len(buffer) == 4

    extension = decoder.extension_passes()
    # 95 records left, 2 bytes already in hand: 568 bytes, 36 blocks,
    # plus one of slack, from the block after the band.
    assert extension == (("01", tuple(range(0x60, 0x60 + 37))),)

    rest, _ = _feed(
        decoder, blocks[BAND_BLOCKS : BAND_BLOCKS + 37], BAND_FIRST_BLOCK + BAND_BLOCKS, buffer
    )
    assert len(rest) == 95
    assert chunks + rest == [_record(i) for i in range(200)]
    assert decoder.extension_passes() == ()


def test_an_extension_that_brings_nothing_is_not_repeated() -> None:
    decoder = AudioDecoder(None)
    blocks = _table_blocks(200)
    _feed(decoder, blocks[:BAND_BLOCKS], BAND_FIRST_BLOCK)
    assert decoder.extension_passes()
    # The module went quiet: no frame came back for the extension.
    assert decoder.extension_passes() == ()


def test_the_extension_crosses_sub_bytes_and_stops_at_capacity() -> None:
    decoder = AudioDecoder(None)
    blocks = _table_blocks(AUDIO_MAX_RECORDS)
    _feed(decoder, blocks[:BAND_BLOCKS], BAND_FIRST_BLOCK)
    extension = decoder.extension_passes()
    assert [sub for sub, _regs in extension] == ["01", "02", "03"]
    assert extension[0][1][0] == 0x60
    last_sub, last_regs = extension[-1]
    assert int(last_sub, 16) << 8 | last_regs[-1] == AUDIO_TABLE_LAST_BLOCK


def test_reset_forgets_the_table() -> None:
    decoder = AudioDecoder(None)
    _feed(decoder, _table_blocks(200)[:BAND_BLOCKS], BAND_FIRST_BLOCK)
    decoder.reset_scan_buffers()
    assert decoder.extension_passes() == ()


# --- blocks to sections ----------------------------------------------------


def test_blocks_to_sections_splits_at_sub_byte_boundaries() -> None:
    assert blocks_to_sections(0x1F0, 0x205) == (
        ("01", tuple(range(0xF0, 0x100))),
        ("02", tuple(range(0x00, 0x06))),
    )
    assert blocks_to_sections(0x160, 0x160) == (("01", (0x60,)),)
    assert blocks_to_sections(0x10, 0x0F) == ()
