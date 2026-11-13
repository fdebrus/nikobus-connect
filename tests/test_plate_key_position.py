"""``plate_key_position``: where a key sits on its wall plate.

The plate entries are the ones ``merge_discovered_buttons`` writes for
an inventoried device, so the labels and the stored type or model are
the store's own.
"""

from __future__ import annotations

from typing import Any

import pytest

from nikobus_connect.discovery import plate_key_position
from nikobus_connect.discovery.fileio import merge_discovered_buttons
from nikobus_connect.discovery.mapping import DEVICE_TYPES, KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address

_TWO = {"1A": "upper", "1B": "lower"}
_FOUR = {"1A": "upper_left", "1B": "lower_left", "1C": "upper_right", "1D": "lower_right"}
_EIGHT = {
    "1A": "upper_left",
    "1B": "lower_left",
    "1C": "upper_mid_left",
    "1D": "lower_mid_left",
    "2A": "upper_mid_right",
    "2B": "lower_mid_right",
    "2C": "upper_right",
    "2D": "lower_right",
}


def _entry(device_type: str) -> dict[str, Any]:
    """The store entry of an inventoried device of this type."""
    info = DEVICE_TYPES[device_type]
    button_data: dict[str, Any] = {"nikobus_button": {}}
    discovered = {
        "1809FE": {
            "category": "Button",
            "device_type": device_type,
            "model": info["Model"],
            "channels": info["Channels"],
            "description": info["Name"],
        }
    }
    merge_discovered_buttons(button_data, discovered, KEY_MAPPING, convert_nikobus_address)
    entry: dict[str, Any] = button_data["nikobus_button"]["1809FE"]
    return entry


@pytest.mark.parametrize(
    ("device_type", "positions"),
    [
        pytest.param("04", _TWO, id="05-060"),
        pytest.param("05", _TWO, id="05-061"),
        pytest.param("3F", _TWO, id="05-060-02"),
        pytest.param("10", _TWO, id="4*-082"),
        pytest.param("06", _FOUR, id="05-064"),
        pytest.param("40", _FOUR, id="05-064-02"),
        pytest.param("11", _FOUR, id="4*-084"),
        pytest.param("0C", _FOUR, id="05-09x"),
        pytest.param("12", _EIGHT, id="4*-078"),
        pytest.param("41", _EIGHT, id="05-078-02"),
    ],
)
def test_every_key_of_a_wall_plate_has_its_place(device_type: str, positions: dict[str, str]) -> None:
    entry = _entry(device_type)
    assert {key: plate_key_position(entry, key) for key in entry["operation_points"]} == positions


@pytest.mark.parametrize(
    "device_type",
    [
        pytest.param("21", id="push-button-interface"),
        pytest.param("43", id="universal-interface"),
        pytest.param("23", id="rf-bus-push-button"),
        pytest.param("26", id="rf-mini-transmitter"),
        pytest.param("28", id="motion-detector"),
    ],
)
def test_what_is_not_a_wall_plate_has_no_place(device_type: str) -> None:
    entry = _entry(device_type)
    assert [plate_key_position(entry, key) for key in entry["operation_points"]] == [None] * len(
        entry["operation_points"]
    )


def test_a_store_without_the_device_type_is_known_by_its_model() -> None:
    entry = _entry("06")
    entry.pop("device_type", None)
    assert plate_key_position(entry, "1c") == "upper_right"


@pytest.mark.parametrize(
    ("entry", "key"),
    [
        pytest.param(None, "1A", id="no-entry"),
        pytest.param({"device_type": "06"}, None, id="no-key"),
        pytest.param({"device_type": "06"}, "IR:02A", id="an-ir-code"),
        pytest.param({"device_type": "06"}, "2A", id="a-key-the-plate-does-not-have"),
        pytest.param({"model": "Remote Code"}, "1A", id="a-remote-code-entry"),
    ],
)
def test_no_place_is_invented(entry: dict[str, Any] | None, key: str | None) -> None:
    assert plate_key_position(entry, key) is None
