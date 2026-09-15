"""PC-Link calendar channels in output-module link records.

The Nikobus software gives the PC-Link 100 calendar channels (CH001 …
CH100, halves A and B) at bus addresses E00320 + 4(n-1) (+2 for B),
which calendar programs and scenes fire like buttons. A link record
carries them bit-reversed like any button address; the wall-button
transform folds distinct channels together, so the decoders keep the
canonical address and the merge files the link under a synthesized
PC-Link entry instead of dropping it as an unknown button.
"""

from __future__ import annotations

from types import SimpleNamespace

from nikobus_connect.discovery import fileio
from nikobus_connect.discovery.protocol import calendar_channel_from_link
from nikobus_connect.discovery.switch_decoder import decode as decode_switch


def test_calendar_channel_addresses_decode_to_the_software_labels() -> None:
    # Entries of a real PC-Link channel table (three-byte bus form).
    assert calendar_channel_from_link("04C007") == ("CH001A", "E00320")
    assert calendar_channel_from_link("24C007") == ("CH002A", "E00324")
    assert calendar_channel_from_link("002007") == ("CH057A", "E00400")
    assert calendar_channel_from_link("352007") == ("CH100A", "E004AC")
    # B half: bus form of E00322.
    assert calendar_channel_from_link("44C007") == ("CH001B", "E00322")


def test_wall_buttons_and_out_of_range_addresses_are_not_calendar_channels() -> None:
    assert calendar_channel_from_link("2C4E00") is None  # a real wall button
    assert calendar_channel_from_link("FFFFFF") is None
    assert calendar_channel_from_link("032007") is None  # E004C0, past CH100
    assert calendar_channel_from_link("zz") is None


def test_switch_record_pointing_to_a_calendar_channel_keeps_it_unique() -> None:
    context = SimpleNamespace(module_address="4707", module_channel_count=12, coordinator=None)
    payload = "00" + "00" + "01" + "04C007"  # key 0, channel 1, mode M01, CH001A
    decoded = decode_switch(payload, [payload[i : i + 2] for i in range(0, 12, 2)], context)
    assert decoded is not None
    assert decoded["calendar_channel"] == "CH001A"
    assert decoded["button_address"] == "E00320"
    # CH017A would fold onto the same wall-button address; it stays distinct.
    other = "00" + "00" + "01" + "06C007"
    decoded2 = decode_switch(other, [other[i : i + 2] for i in range(0, 12, 2)], context)
    assert decoded2["calendar_channel"] == "CH017A"
    assert decoded2["button_address"] == "E00360"
    assert decoded2["button_address"] != decoded["button_address"]


def test_merge_files_a_calendar_link_under_a_synthesized_pc_link_entry() -> None:
    button_data = {"nikobus_button": {}}
    command_mapping = {
        ("E00320", 0, None): [
            {
                "module_address": "4707",
                "channel": 1,
                "mode": "M01 (On / off)",
                "t1": "0s",
                "t2": None,
                "payload": "00000104C007",
                "button_address": "E00320",
                "calendar_channel": "CH001A",
                "record_source": "output_module_table",
            }
        ]
    }
    updated, links, outputs, unmatched = fileio.merge_linked_modules(button_data, command_mapping)
    assert (updated, links, outputs, unmatched) == (1, 1, 1, set())
    entry = button_data["nikobus_button"]["E00320"]
    assert entry["calendar_channel"] == "CH001A"
    assert entry["type"] == fileio.CALENDAR_CHANNEL_TYPE
    op_point = entry["operation_points"]["CAL"]
    assert op_point["bus_address"] == "E00320"
    assert op_point["linked_modules"][0]["module_address"] == "4707"
    assert op_point["linked_modules"][0]["outputs"][0]["channel"] == 1
    # Idempotent on a second merge.
    assert fileio.merge_linked_modules(button_data, command_mapping)[1:3] == (0, 0)


def test_unknown_wall_button_links_are_still_dropped() -> None:
    """The calendar branch does not catch ordinary unmatched buttons."""
    button_data = {"nikobus_button": {}}
    command_mapping = {("2C4E00", 0, None): [{"module_address": "4707", "channel": 1, "mode": "M01"}]}
    updated, links, outputs, unmatched = fileio.merge_linked_modules(button_data, command_mapping)
    assert (updated, links, outputs) == (0, 0, 0)
    assert unmatched == {"2C4E00"}
    assert button_data["nikobus_button"] == {}
