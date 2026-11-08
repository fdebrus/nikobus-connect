"""The 0x3C RF box (05-315, 4 channels) is inventoried with four keys.

Nikobus-HA #575 (2026-11-08): the PC-Link inventory reported type 0x3C
at 1EE2A9 and the integration dropped it as an unknown type, so its
presses #N2551DE and #N6551DE arrived as "Press from unknown button".
product.mdb names key 60 the 4-channel RF box; the two presses are keys
1C and 1D of 1EE2A9 under the default 4-key layout.
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import (
    find_operation_point,
    merge_discovered_buttons,
)
from nikobus_connect.discovery.mapping import DEVICE_TYPES, KEY_MAPPING
from nikobus_connect.discovery.protocol import (
    classify_device_type,
    convert_nikobus_address,
)

UNIT = "1EE2A9"
OBSERVED = {"1C": "2551DE", "1D": "6551DE"}


def _inventoried(type_byte: str) -> dict:
    """What the inventory files for a registry record of ``type_byte``."""
    info = classify_device_type(type_byte, DEVICE_TYPES)
    return {
        UNIT: {
            "category": info["Category"],
            "device_type": type_byte.upper(),
            "model": info["Model"],
            "channels": info["Channels"],
            "description": info["Name"],
        }
    }


def test_type_3c_is_a_four_channel_rf_box() -> None:
    info = classify_device_type("3c", DEVICE_TYPES)
    assert (info["Category"], info["Model"], info["Channels"]) == ("Button", "05-315", 4)
    # The integration files a type with "RF" and no "interface" in its
    # name under the remotes class.
    assert "rf" in info["Name"].lower()
    assert "interface" not in info["Name"].lower()


def test_inventory_creates_four_keys_including_the_observed_presses() -> None:
    data: dict = {"nikobus_button": {}}
    merge_discovered_buttons(data, _inventoried("3C"), KEY_MAPPING, convert_nikobus_address)
    entry = data["nikobus_button"][UNIT]
    frames = {label: op["bus_address"] for label, op in entry["operation_points"].items()}
    assert frames == {"1A": "A551DE", "1B": "E551DE", "1C": "2551DE", "1D": "6551DE"}
    for label, frame in OBSERVED.items():
        assert find_operation_point(data, frame) == (UNIT, label, entry["operation_points"][label])


def test_the_two_channel_rf_box_stays_uncatalogued() -> None:
    assert classify_device_type("38", DEVICE_TYPES)["Category"] == "Unknown"
