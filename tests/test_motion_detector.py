"""The 0x28 motion detector (05-7*5 / 430-00500) has four keys.

A user logged two units for three days (Nikobus-HA forum, 2026-10):
each sends its output on / off at key nibbles 8 / C and the light level
crossing its threshold upwards / downwards at E / A. Discovery used to
treat it as a two-key button, so the light pair was "Press from unknown
button" and no link on it could resolve.
"""

from __future__ import annotations

import pytest

from nikobus_connect.discovery.fileio import (
    _build_bus_to_op_index,
    _build_ir_base_lookup,
    _resolve_operation_point,
    merge_discovered_buttons,
)
from nikobus_connect.discovery.mapping import (
    DEVICE_TYPES,
    KEY_MAPPING,
    MOTION_DETECTOR_DEVICE_TYPE,
    MOTION_DETECTOR_KEY_ROLES,
)
from nikobus_connect.discovery.protocol import (
    convert_nikobus_address,
    is_motion_detector,
    motion_detector_bus_addresses,
    record_bus_address,
)

#: The two units from the report: physical -> frames by role.
UNITS = {
    "0986BC": {"on": "8F5864", "off": "CF5864", "light": "EF5864", "dark": "AF5864"},
    "098B48": {"on": "84B464", "off": "C4B464", "light": "E4B464", "dark": "A4B464"},
}


def _discovered(physical: str) -> dict:
    entry = DEVICE_TYPES[MOTION_DETECTOR_DEVICE_TYPE]
    return {
        physical: {
            "category": "Button",
            "device_type": MOTION_DETECTOR_DEVICE_TYPE,
            "model": entry["Model"],
            "channels": entry["Channels"],
            "description": entry["Name"],
        }
    }


def _merged(physical: str, button_data: dict | None = None) -> dict:
    button_data = button_data if button_data is not None else {"nikobus_button": {}}
    merge_discovered_buttons(button_data, _discovered(physical), KEY_MAPPING, convert_nikobus_address)
    return button_data["nikobus_button"][physical]


@pytest.mark.parametrize("physical", sorted(UNITS))
def test_all_four_frames_by_role(physical: str) -> None:
    assert motion_detector_bus_addresses(physical) == UNITS[physical]


@pytest.mark.parametrize("physical", sorted(UNITS))
def test_inventory_creates_four_keys_at_the_frames_the_unit_sends(physical: str) -> None:
    entry = _merged(physical)
    assert entry["channels"] == 4
    assert entry["device_type"] == MOTION_DETECTOR_DEVICE_TYPE
    by_role = {
        MOTION_DETECTOR_KEY_ROLES[label]: op["bus_address"]
        for label, op in entry["operation_points"].items()
    }
    assert by_role == UNITS[physical]


def test_a_store_from_before_gains_the_light_keys() -> None:
    old = {
        "nikobus_button": {
            "0986BC": {
                "type": "Motion detector with Nikobus interface",
                "model": "05-7*5",
                "channels": 2,
                "description": "Landing",
                "operation_points": {
                    "1A": {"bus_address": "8F5864", "description": "on"},
                    "1B": {"bus_address": "CF5864", "description": "off"},
                },
            }
        }
    }
    entry = _merged("0986BC", old)
    assert entry["description"] == "Landing"  # the user's name is kept
    assert entry["operation_points"]["1A"]["description"] == "on"
    assert {op["bus_address"] for op in entry["operation_points"].values()} == set(UNITS["0986BC"].values())


@pytest.mark.parametrize(("key_raw", "role"), [(1, "on"), (3, "off"), (7, "light"), (5, "dark")])
def test_link_records_resolve_to_the_key_whose_frame_they_listen_for(key_raw: int, role: str) -> None:
    buttons = {"0986BC": _merged("0986BC")}
    resolved = _resolve_operation_point(
        "0986BC", key_raw, buttons, _build_bus_to_op_index(buttons), _build_ir_base_lookup(buttons)
    )
    assert resolved is not None
    physical, label, op = resolved
    assert physical == "0986BC"
    assert MOTION_DETECTOR_KEY_ROLES[label] == role
    assert op["bus_address"] == record_bus_address("0986BC", key_raw) == UNITS["0986BC"][role]


@pytest.mark.parametrize("key_raw", [0, 2])
def test_a_key_index_the_unit_never_sends_is_not_put_on_another_key(key_raw: int) -> None:
    buttons = {"0986BC": _merged("0986BC")}
    resolved = _resolve_operation_point(
        "0986BC", key_raw, buttons, _build_bus_to_op_index(buttons), _build_ir_base_lookup(buttons)
    )
    assert resolved is None or resolved[1] not in ("1C", "1D")


def test_recognised_by_type_byte_model_or_name() -> None:
    assert is_motion_detector({"device_type": "28"})
    assert is_motion_detector({"model": "05-7*5"})
    assert is_motion_detector({"type": "Motion detector with Nikobus interface"})
    assert not is_motion_detector({"model": "05-060", "type": "Bus push button, 2 control buttons"})
    assert not is_motion_detector(None)


def test_ordinary_two_key_buttons_are_unchanged() -> None:
    button_data = {"nikobus_button": {}}
    merge_discovered_buttons(
        button_data,
        {"0A0908": {"category": "Button", "device_type": "04", "model": "05-060", "channels": 2,
                    "description": "Bus push button, 2 control buttons"}},
        KEY_MAPPING,
        convert_nikobus_address,
    )
    entry = button_data["nikobus_button"]["0A0908"]
    assert sorted(entry["operation_points"]) == ["1A", "1B"]
    assert "device_type" not in entry
