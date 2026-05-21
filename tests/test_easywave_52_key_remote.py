"""Tests for the Niko 05-312 Easywave 52-key remote merge path.

The 05-312 is enrolled in PC-Link inventory as device_type 0x3D
with ``channels=52``. Until 0.13.2, ``merge_discovered_buttons``
rejected it because ``_BUTTON_KEYS_BY_CHANNEL_COUNT`` only knew
{1, 2, 4, 8} — the remote silently dropped from the v2 button
store, and any BP-cell reference to its sub-codes hit the
unmatched accumulator with a single physical (degenerate cluster
of size 1, well below the synthesis threshold).

These tests pin the install on which the bug was diagnosed:
PC-Link reported one device at physical ``0E31C0`` with channels
52, and the user's v1 .migrated archive listed all 52 emitted
bus addresses verbatim. The fix in 0.13.2 adds
``KEY_MAPPING_FIRST_BYTE`` with the 52 first-byte offsets and a
``merge_discovered_buttons`` branch that uses full first-byte
replacement (instead of single-nibble add) when the channel count
appears in that table. After the fix, the remote materialises in
the button store as one entry with 52 op-points whose
``bus_address`` values match the v1 dump byte-for-byte.
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import (
    _BUTTON_KEYS_BY_CHANNEL_COUNT,
    merge_discovered_buttons,
)
from nikobus_connect.discovery.mapping import (
    EASYWAVE_52_KEY_MAPPING,
    KEY_MAPPING,
    KEY_MAPPING_FIRST_BYTE,
)
from nikobus_connect.discovery.protocol import convert_nikobus_address


# Ground truth from the user's .migrated v1 file. Physical 0E31C0,
# all 52 bus addresses keyed by their (human-readable) sub-code
# labels. The label format matches the user's original naming so
# the resulting op-points read familiarly in the v2 store.
EASYWAVE_52_EXPECTED_BUSES_FOR_0E31C0 = {
    "1A": "88E31C", "1B": "C8E31C", "1C": "08E31C",
    "1.1A": "80E31C", "1.1B": "C0E31C",
    "1.2A": "A0E31C", "1.2B": "E0E31C",
    "1.3A": "90E31C", "1.3B": "D0E31C",
    "1.4A": "B0E31C", "1.4B": "F0E31C",
    "1.5A": "30E31C", "1.5B": "70E31C",
    "2A": "8CE31C", "2B": "CCE31C", "2C": "0CE31C",
    "2.1A": "84E31C", "2.1B": "C4E31C",
    "2.2A": "A4E31C", "2.2B": "E4E31C",
    "2.3A": "94E31C", "2.3B": "D4E31C",
    "2.4A": "B4E31C", "2.4B": "F4E31C",
    "2.5A": "34E31C", "2.5B": "74E31C",
    "3A": "8AE31C", "3B": "CAE31C", "3C": "0AE31C",
    "3.1A": "82E31C", "3.1B": "C2E31C",
    "3.2A": "A2E31C", "3.2B": "E2E31C",
    "3.3A": "92E31C", "3.3B": "D2E31C",
    "3.4A": "B2E31C", "3.4B": "F2E31C",
    "3.5A": "32E31C", "3.5B": "72E31C",
    "4A": "8EE31C", "4B": "CEE31C", "4C": "0EE31C",
    "4.1A": "86E31C", "4.1B": "C6E31C",
    "4.2A": "A6E31C", "4.2B": "E6E31C",
    "4.3A": "96E31C", "4.3B": "D6E31C",
    "4.4A": "B6E31C", "4.4B": "F6E31C",
    "4.5A": "36E31C", "4.5B": "76E31C",
}


# ---------------------------------------------------------------------------
# Table integrity
# ---------------------------------------------------------------------------


def test_easywave_52_mapping_has_exactly_52_entries():
    assert len(EASYWAVE_52_KEY_MAPPING) == 52


def test_easywave_52_keys_table_has_exactly_52_entries():
    assert len(_BUTTON_KEYS_BY_CHANNEL_COUNT[52]) == 52


def test_easywave_52_keys_and_mapping_agree():
    """The list of keys for channels=52 must exactly match the
    set of labels in the mapping — otherwise some sub-codes go
    silently missing from the op-points."""
    assert set(_BUTTON_KEYS_BY_CHANNEL_COUNT[52]) == set(EASYWAVE_52_KEY_MAPPING.keys())


def test_first_byte_dispatch_table_registers_52():
    """``merge_discovered_buttons`` dispatches on this table to use
    the full first-byte replacement path."""
    assert 52 in KEY_MAPPING_FIRST_BYTE
    assert KEY_MAPPING_FIRST_BYTE[52] is EASYWAVE_52_KEY_MAPPING


def test_every_first_byte_is_two_hex_chars():
    """Sanity: each value is a 2-character hex string. The merge
    path concatenates it with ``converted_address[2:]`` to form a
    6-char bus address."""
    for label, byte_hex in EASYWAVE_52_KEY_MAPPING.items():
        assert len(byte_hex) == 2, (label, byte_hex)
        int(byte_hex, 16)  # raises if not hex


# ---------------------------------------------------------------------------
# End-to-end: merge produces correct bus addresses on the user's install
# ---------------------------------------------------------------------------


def test_05_312_at_0e31c0_merges_with_52_correct_op_points():
    """A 52-channel button at physical ``0E31C0`` (the user's
    install) must materialise in the button store with one entry,
    52 op-points, and each op-point's ``bus_address`` matching the
    v1 ground truth."""

    button_data: dict = {"nikobus_button": {}}
    discovered = {
        "0E31C0": {
            "address": "0E31C0",
            "category": "Button",
            "description": "Easywave hand-held RF transmitter, 52 operation points",
            "device_type": "3D",
            "model": "05-312",
            "channels": 52,
            "channels_count": 52,
        }
    }

    merge_discovered_buttons(
        button_data,
        discovered,
        KEY_MAPPING,
        convert_nikobus_address,
    )

    nikobus_buttons = button_data["nikobus_button"]
    assert "0E31C0" in nikobus_buttons

    entry = nikobus_buttons["0E31C0"]
    assert entry["channels"] == 52
    assert entry["model"] == "05-312"

    op_points = entry["operation_points"]
    # Exactly 52 op-points materialise.
    assert len(op_points) == 52

    # Every label gets the bus address the user observed in v1.
    for label, expected_bus in EASYWAVE_52_EXPECTED_BUSES_FOR_0E31C0.items():
        assert label in op_points, label
        assert op_points[label]["bus_address"] == expected_bus, (
            label,
            op_points[label]["bus_address"],
            expected_bus,
        )


def test_05_312_merge_is_idempotent():
    """Re-running discovery + merge must not change the resulting
    op-points — same physical, same 52 bus addresses."""
    button_data: dict = {"nikobus_button": {}}
    discovered = {
        "0E31C0": {
            "address": "0E31C0",
            "category": "Button",
            "description": "Easywave",
            "device_type": "3D",
            "model": "05-312",
            "channels": 52,
            "channels_count": 52,
        }
    }
    merge_discovered_buttons(button_data, discovered, KEY_MAPPING, convert_nikobus_address)
    snapshot_a = {
        label: dict(op) for label, op in button_data["nikobus_button"]["0E31C0"]["operation_points"].items()
    }
    merge_discovered_buttons(button_data, discovered, KEY_MAPPING, convert_nikobus_address)
    snapshot_b = {
        label: dict(op) for label, op in button_data["nikobus_button"]["0E31C0"]["operation_points"].items()
    }
    assert snapshot_a == snapshot_b


def test_other_physicals_get_different_buses_with_same_offsets():
    """A second 05-312 at a different physical produces the same
    52 first bytes paired with the new physical's converted-address
    suffix. Confirms the offsets are universal across 05-312 units."""

    button_data: dict = {"nikobus_button": {}}
    # Fictional second remote — different physical, same family.
    physical = "1A2B3C"
    discovered = {
        physical: {
            "address": physical,
            "category": "Button",
            "description": "Easywave",
            "device_type": "3D",
            "model": "05-312",
            "channels": 52,
            "channels_count": 52,
        }
    }
    merge_discovered_buttons(button_data, discovered, KEY_MAPPING, convert_nikobus_address)

    converted = convert_nikobus_address(physical)
    suffix = converted[2:]  # the 4 chars that follow the per-key first byte
    op_points = button_data["nikobus_button"][physical]["operation_points"]
    assert len(op_points) == 52

    for label, first_byte_hex in EASYWAVE_52_KEY_MAPPING.items():
        expected = first_byte_hex + suffix
        assert op_points[label]["bus_address"] == expected, (label, expected)


# ---------------------------------------------------------------------------
# Regression: standard 1/2/4/8-channel buttons keep using single-nibble add
# ---------------------------------------------------------------------------


def test_4_channel_button_still_uses_single_nibble_offsets():
    """The 52-key first-byte path must not bleed into the standard
    {1,2,4,8} channel-count merges, which use single-nibble adds."""

    button_data: dict = {"nikobus_button": {}}
    # Real 4-channel push button from the user's prior dumps.
    discovered = {
        "10998B": {
            "address": "10998B",
            "category": "Button",
            "channels": 2,
            "model": "05-060-02",
            "description": "Bus push button, 2 control buttons with two feedback LEDs",
        }
    }
    merge_discovered_buttons(button_data, discovered, KEY_MAPPING, convert_nikobus_address)

    ops = button_data["nikobus_button"]["10998B"]["operation_points"]
    # Verified against the user's earlier v1 dump.
    assert ops["1A"]["bus_address"] == "B46642"
    assert ops["1B"]["bus_address"] == "F46642"
