"""Regressions from the 2026-10-01 decoder and merge-layer audit."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from nikobus_connect.discovery.audio_decoder import AudioDecoder, decode as decode_audio, split_link_table
from nikobus_connect.discovery.discovery import add_to_command_mapping
from nikobus_connect.discovery.fileio import merge_linked_modules
from nikobus_connect.discovery.pc_logic_decoder import decode_input_link
from nikobus_connect.discovery.protocol import get_button_address, normalize_payload, reverse_hex
from nikobus_connect.nkb.parser import parse_nkb


def _record(n: int) -> str:
    return f"{n & 0xFF:02X}83CF{n % 32:02X}{n % 4:02X}01"


# --- audio: a count whose low byte is FF ----------------------------------


@pytest.mark.parametrize("count", [255, 511, 1791])
def test_a_count_with_a_low_byte_of_ff_is_not_eaten_as_filler(count: int) -> None:
    stream = "FF" * 6 + count.to_bytes(2, "little").hex().upper()
    stream += "".join(_record(i) for i in range(count))
    assert len(split_link_table(stream)) == count


def test_the_streaming_decoder_reads_a_255_record_table() -> None:
    decoder = AudioDecoder(None)
    stream = "FF" * 6 + (255).to_bytes(2, "little").hex().upper()
    stream += "".join(_record(i) for i in range(255))
    while len(stream) % 32:
        stream += "FF"
    chunks: list[str] = []
    buffer = ""
    for offset in range(0, len(stream), 32):
        block = 0x138 + offset // 32
        decoder.set_current_block(f"{block >> 8:02X}", block & 0xFF)
        analysis = decoder.analyze_frame_payload(buffer, stream[offset : offset + 32] + "000000")
        assert analysis is not None
        buffer = analysis["remainder"]
        chunks.extend(analysis["chunks"])
    assert chunks == [_record(i) for i in range(255)]


def test_a_record_without_the_validity_byte_is_dropped() -> None:
    class _Ctx:
        module_address = "8334"
        module_channel_count = None
        coordinator = None

    good = reverse_hex("0083CF0A0001")
    bad = reverse_hex("0083CF0A00FF")
    assert decode_audio(good, normalize_payload(good), _Ctx) is not None
    assert decode_audio(bad, normalize_payload(bad), _Ctx) is None


# --- PC-Logic input table: the plate address -------------------------------


def test_the_input_link_names_the_plate_the_way_the_other_decoders_do() -> None:
    decoded = decode_input_link(bytes.fromhex("4928D8") + bytes([0, 3, 0x41]))
    assert decoded is not None
    assert decoded["button_address"] == "124A36"
    assert decoded["button_address"] == get_button_address(reverse_hex("4928D8"))
    assert decoded["wire_address"] == "1B1492"


# --- merge: an audio record on a wall key stays the key's -------------------


def _audio_output(bus_address: str, zone: int) -> dict:
    return {
        "payload": "",
        "bus_address": bus_address,
        "button_address": bus_address,
        "push_button_address": bus_address,
        "key_raw": 0,
        "audio_function": "M16 (On)",
        "audio_function_raw": 0x0D,
        "audio_object": f"Zone {zone}",
        "audio_object_raw": zone - 1,
        "audio_power": False,
        "audio_zone": zone,
        "audio_zone_raw": zone - 1,
        "description": f"Zone {zone} On",
        "channel": zone,
        "M": "M16 (On)",
        "T1": None,
        "T2": None,
        "record_source": "output_module_table",
    }


def test_a_wall_key_driving_a_zone_keeps_the_link_and_gets_no_phantom() -> None:
    # Plate 124A36, four keys; key 1C puts #N1B1492 on the bus.
    button_data = {
        "nikobus_button": {
            "124A36": {
                "address": "124A36",
                "description": "Kitchen plate",
                "channels": 4,
                "operation_points": {
                    "1C": {"bus_address": "1B1492", "description": "Kitchen 1C"},
                },
            }
        }
    }
    mapping: dict = {}
    add_to_command_mapping(mapping, _audio_output("1B1492", 1), "8334")
    # A virtual trigger no plate owns, for contrast.
    add_to_command_mapping(mapping, _audio_output("8083CF", 1), "8334")

    merge_linked_modules(button_data, mapping)
    store = button_data["nikobus_button"]

    assert "1B1492" not in store, "the wall key's frame must not become a phantom trigger"
    linked = store["124A36"]["operation_points"]["1C"].get("linked_modules")
    assert linked, "the wall key carries the audio link"
    assert linked[0]["module_address"] == "8334"
    assert linked[0]["outputs"][0]["audio_zone"] == 1
    assert "8083CF" in store and store["8083CF"]["type"]


# --- .nkb parser: the documented ValueError --------------------------------


def test_parse_nkb_raises_value_error_on_a_file_that_is_not_an_archive(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.nkb"
    bogus.write_bytes(b"this is not a zip file")
    with pytest.raises(ValueError):
        parse_nkb(bogus)


def test_parse_nkb_raises_value_error_on_a_database_the_reader_cannot_read(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # A JET header the reader accepts, with nothing behind it.
        zf.writestr("project.mdb", b"\x00\x01\x00\x00Standard Jet DB\x00" + b"\x00" * 100)
    nkb = tmp_path / "truncated.nkb"
    nkb.write_bytes(buf.getvalue())
    with pytest.raises(ValueError):
        parse_nkb(nkb)
