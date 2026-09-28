"""Audio Distribution module (05-205) link table.

Fixture: the bank-01 registers of a real 05-205 (module 8334), captured
2026-09-27 on an installation with four zones. Its owner confirmed every
record against the virtual buttons programmed in the Nikobus software,
so the expectations below are hardware truth, not inference:

    #N8083CF -> Zone 1 On        #N0083CF -> Zone 1 Volume up
    #NC083CF -> Zone 1 Off       #N4083CF -> Zone 1 Volume down
    #NA083CF/#NE083CF/#N2083CF/#N6083CF -> Zone 1 Source 1..4

The eight functions also match, independently, the vendor's own mode
table for the audio object types (function byte + 3 = mode number).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nikobus_connect.discovery.audio_decoder import (
    OBJECT_POWER,
    AudioDecoder,
    audio_function_label,
    audio_mode_number,
    audio_object_label,
    decode,
    split_link_table,
)
from nikobus_connect.discovery.discovery import add_to_command_mapping
from nikobus_connect.discovery.fileio import merge_linked_modules
from nikobus_connect.discovery.protocol import (
    get_button_address,
    normalize_payload,
    reverse_hex,
)

FIXTURE = Path(__file__).parent / "fixtures" / "audio_module_8334_bank01.json"
REGISTERS: dict[str, str] = json.loads(FIXTURE.read_text())
STREAM = "".join(REGISTERS[k] for k in sorted(REGISTERS))


class _Context:
    module_address = "8334"
    module_channel_count = None
    coordinator = None


def _decode_all() -> list[dict]:
    out = []
    for record in split_link_table(STREAM):
        payload = reverse_hex(record)  # the chunk layer hands records byte-reversed
        decoded = decode(payload, normalize_payload(payload), _Context)
        if decoded:
            out.append(decoded)
    return out


# --- the band ------------------------------------------------------------


def test_the_header_gives_the_record_count() -> None:
    records = split_link_table(STREAM)
    assert len(records) == 0x23 == 35
    assert records[0] == "0083CF0A0001"
    assert records[-1] == "F883CF010301"


def test_filler_and_a_missing_header_are_handled() -> None:
    assert split_link_table("FF" * 16) == []
    assert split_link_table("") == []
    # A count byte must be followed by 00 to count as the header.
    assert split_link_table("FFFF" + "23FF" + "0083CF0A0001") == []
    # Leading filler of any length is skipped.
    assert split_link_table("FFFFFF" + "0100" + "0083CF0A0001") == ["0083CF0A0001"]


# --- the records ---------------------------------------------------------


def test_every_record_decodes() -> None:
    assert len(_decode_all()) == 35


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("8083CF", "Zone 1 On"),
        ("C083CF", "Zone 1 Off"),
        ("0083CF", "Zone 1 Volume up"),
        ("4083CF", "Zone 1 Volume down"),
        ("A083CF", "Zone 1 Source 1"),
        ("E083CF", "Zone 1 Source 2"),
        ("2083CF", "Zone 1 Source 3"),
        ("6083CF", "Zone 1 Source 4"),
        ("EE03CF", "Zone 3 On"),
    ],
)
def test_addresses_confirmed_against_the_owners_buttons(address: str, expected: str) -> None:
    hit = [d for d in _decode_all() if d["bus_address"] == address]
    assert len(hit) == 1, address
    assert hit[0]["description"] == expected


def test_four_zones_by_eight_functions_with_no_gaps() -> None:
    grid: dict[str, set[int]] = {}
    for d in _decode_all():
        if d["audio_zone"] is not None:
            grid.setdefault(d["audio_function"], set()).add(d["audio_zone"])
    assert len(grid) == 8
    assert all(zones == {1, 2, 3, 4} for zones in grid.values()), grid
    assert sorted(grid) == [
        "M03 (Source 1)",
        "M04 (Source 2)",
        "M05 (Source 3)",
        "M06 (Source 4)",
        "M13 (Volume up)",
        "M14 (Volume down)",
        "M16 (On)",
        "M17 (Off)",
    ]


def test_the_power_object_is_not_a_zone() -> None:
    """Object byte 0x08 is the module's Power object, not "every zone".

    Both readings put the same two records outside the zone grid, but
    they name them differently, and the owner's project file settles it:
    object type 127 "Audio Power", link mode M01. His own button for the
    first of these is called POWER OFF.
    """
    power = [d for d in _decode_all() if d["audio_power"]]
    assert len(power) == 2
    assert {d["bus_address"] for d in power} == {"8483CF", "6E03CF"}
    assert all(d["audio_zone"] is None for d in power)
    assert all(d["audio_object"] == "Power" for d in power)
    assert all(d["audio_function"] == "M01 (Power)" for d in power)
    assert all(d["description"] == "Audio Power" for d in power)


def test_the_address_is_stored_verbatim_not_transformed() -> None:
    """The three bytes are the #N payload. Passing them through the
    wall-button transform yields a different, wrong address — the bug
    this decoder exists to avoid."""
    record = [d for d in _decode_all() if d["description"] == "Zone 1 On"][0]
    assert record["bus_address"] == "8083CF"
    assert record["push_button_address"] == "8083CF"
    assert get_button_address("8083CF") != "8083CF"


# --- labels --------------------------------------------------------------


def test_function_byte_plus_three_is_the_vendor_mode_on_a_zone() -> None:
    assert audio_function_label(0x00) == "M03 (Source 1)"
    assert audio_function_label(0x0A) == "M13 (Volume up)"
    assert audio_function_label(0x0E) == "M17 (Off)"
    assert audio_function_label(0x08) == "M11 (Source toggle)"


def test_the_same_function_byte_means_power_on_the_power_object() -> None:
    assert audio_function_label(0x08, OBJECT_POWER) == "M01 (Power)"
    assert audio_mode_number(0x08, OBJECT_POWER) == 1
    assert audio_mode_number(0x08) == 11


@pytest.mark.parametrize(
    ("function_raw", "label"),
    [
        # The owner's project file, function byte by function byte.
        (0x00, "M03 (Source 1)"),
        (0x07, "M10 (Source 8)"),
        (0x09, "M12 (Microphone)"),
        (0x0C, "M15 (On / off)"),
        (0x0F, "M18 (Treble up)"),
        (0x10, "M19 (Treble down)"),
        (0x11, "M20 (Bass up)"),
        (0x12, "M21 (Bass down)"),
        (0x13, "M22 (Balance right)"),
        (0x14, "M23 (Balance left)"),
        (0x15, "M24 (Preset 1)"),
        (0x1C, "M31 (Preset 8)"),
    ],
)
def test_the_whole_vendor_function_table(function_raw: int, label: str) -> None:
    assert audio_function_label(function_raw) == label


def test_object_labels() -> None:
    assert audio_object_label(0) == "Zone 1"
    assert audio_object_label(3) == "Zone 4"
    assert audio_object_label(OBJECT_POWER) == "Power"


# --- streaming, as the scan feeds it ------------------------------------


def test_the_decoder_streams_the_table_across_register_frames() -> None:
    """Registers arrive one frame at a time; the header is consumed once
    and the 35 records come out whole, none split across frames."""
    decoder = AudioDecoder(None)
    decoder.set_module_address("8334")
    chunks: list[str] = []
    remainder = ""
    for key in sorted(REGISTERS):
        analysis = decoder.analyze_frame_payload(remainder, REGISTERS[key] + "ABCDEF")
        assert analysis is not None
        chunks.extend(analysis["chunks"])
        remainder = analysis["remainder"]
    assert len(chunks) == 35
    assert chunks[0] == "0083CF0A0001"
    assert chunks[-1] == "F883CF010301"


def test_a_band_without_a_header_yields_nothing() -> None:
    decoder = AudioDecoder(None)
    analysis = decoder.analyze_frame_payload("", "FF" * 16 + "ABCDEF")
    assert analysis is not None and analysis["chunks"] == []


# --- the merge into the button store ------------------------------------


def _command_mapping() -> dict:
    """What discovery makes of the table, through the real call path."""
    mapping: dict = {}
    for decoded in _decode_all():
        add_to_command_mapping(mapping, decoded, "8334", None)
    return mapping


def _merged_store() -> dict:
    button_data: dict = {"nikobus_button": {}}
    merge_linked_modules(button_data, _command_mapping())
    return button_data["nikobus_button"]


def test_a_decoded_trigger_reaches_the_command_mapping() -> None:
    """The step between decoding a record and storing it.

    ``add_to_command_mapping`` drops any record that carries no key, and
    0.39.0's audio records carried none — so all 35 decoded correctly
    and then vanished, and no audio entity was ever built from them
    (Nikobus-HA #310, reported on a real 05-205). A trigger belongs to
    no keypad, so the decoder gives it key 0.
    """
    assert len(_command_mapping()) == 35
    (outputs,) = [v for k, v in _command_mapping().items() if k[0] == "8083CF"]
    assert outputs[0]["audio_function"] == "M16 (On)"
    assert outputs[0]["module_address"] == "8334"


def test_every_trigger_becomes_its_own_entry_none_unmatched() -> None:
    """An audio trigger is a virtual button no plate owns, so the usual
    resolver would drop all 35 links as unmatched."""
    button_data: dict = {"nikobus_button": {}}
    updated, links, outputs, unmatched = merge_linked_modules(
        button_data, _command_mapping()
    )
    assert (updated, links, outputs) == (35, 35, 35)
    assert unmatched == set()
    assert len(button_data["nikobus_button"]) == 35


def test_the_entry_names_the_module_that_owns_the_trigger() -> None:
    """So a host can group the triggers under that module's device."""
    store = _merged_store()
    assert {e["audio_module_address"] for e in store.values()} == {"8334"}


def test_a_power_trigger_is_stored_as_one() -> None:
    entry = _merged_store()["8483CF"]
    assert entry["audio_power"] is True
    assert entry["audio_zone"] is None
    assert entry["audio_function"] == "M01 (Power)"


def test_the_entry_carries_what_a_host_needs_to_build_a_zone() -> None:
    entry = _merged_store()["8083CF"]
    assert entry["type"] == "Audio Trigger"
    assert entry["description"] == "Zone 1 On"
    assert (entry["audio_zone"], entry["audio_function"]) == (1, "M16 (On)")
    op_point = entry["operation_points"]["AUD"]
    assert op_point["bus_address"] == "8083CF"
    block = op_point["linked_modules"][0]
    assert block["module_address"] == "8334"
    assert block["outputs"][0]["mode"] == "M16 (On)"


def test_a_zone_can_be_assembled_from_the_store() -> None:
    """What the integration does: group the triggers of one module by
    zone to build its controls."""
    zone_2 = {
        entry["audio_function"]: address
        for address, entry in _merged_store().items()
        if entry.get("audio_zone") == 2
    }
    assert zone_2 == {
        "M03 (Source 1)": "B083CF",
        "M04 (Source 2)": "F083CF",
        "M05 (Source 3)": "3083CF",
        "M06 (Source 4)": "7083CF",
        "M13 (Volume up)": "1083CF",
        "M14 (Volume down)": "5083CF",
        "M16 (On)": "9083CF",
        "M17 (Off)": "D083CF",
    }
