"""Two-button wall plates send their keys at nibbles 0 / 4 (#184).

An install with 14 05-060 plates (type 0x04) showed it on the bus and in
the output modules: plate 1772B8 sends 0753BA (upper) / 4753BA (lower),
and the link record its module holds for the upper key,
``FF06F0E0CA5D``, has key index 0. The library generated 8753BA /
C753BA, so presses matched no module and no link landed on a key. The
4*-082 (0x10) was already known to use 0 / 4; the LED variants of the
05-060, 05-061 (0x05) and 05-060-02 (0x3F), are the same plate.
Interfaces keep the generic 8 / C.
"""

from __future__ import annotations

import pytest

from nikobus_connect.discovery.fileio import (
    _build_bus_to_op_index,
    _build_ir_base_lookup,
    _resolve_operation_point,
    merge_discovered_buttons,
)
from nikobus_connect.discovery.mapping import DEVICE_TYPES, KEY_MAPPING
from nikobus_connect.discovery.protocol import (
    convert_nikobus_address,
    get_button_address,
)
from nikobus_connect.nkb.config_builder import build_config
from nikobus_connect.nkb.parser import key_layout_for_model, per_key_bus_address

PLATE = "1772B8"
UPPER, LOWER = "0753BA", "4753BA"


def _merge(device_type: str, physical: str = PLATE, button_data: dict | None = None) -> dict:
    button_data = button_data if button_data is not None else {"nikobus_button": {}}
    entry = DEVICE_TYPES[device_type]
    merge_discovered_buttons(
        button_data,
        {physical: {"category": "Button", "device_type": device_type, "model": entry["Model"],
                    "channels": entry["Channels"], "description": entry["Name"]}},
        KEY_MAPPING,
        convert_nikobus_address,
    )
    return button_data["nikobus_button"][physical]


@pytest.mark.parametrize("device_type", ["04", "05", "10", "3F"])
def test_every_two_button_plate_uses_nibbles_0_and_4(device_type: str) -> None:
    ops = _merge(device_type)["operation_points"]
    assert (ops["1A"]["bus_address"], ops["1B"]["bus_address"]) == (UPPER, LOWER)


@pytest.mark.parametrize(("device_type", "model"), [("21", "05-056"), ("22", "05-057"), ("1F", "05-302")])
def test_interfaces_and_the_rf_push_button_keep_8_and_c(device_type: str, model: str) -> None:
    assert DEVICE_TYPES[device_type]["Model"] == model
    ops = _merge(device_type)["operation_points"]
    assert (ops["1A"]["bus_address"], ops["1B"]["bus_address"]) == ("8753BA", "C753BA")


def test_the_reporters_link_record_lands_on_the_upper_key() -> None:
    # Switch record FF06F0E0CA5D: key index 0, channel 7, M01, button E0CA5D -> 1772B8.
    payload = "FF06F0E0CA5D"
    assert get_button_address(payload[-6:]) == PLATE
    key_raw = int(payload[2], 16)
    assert key_raw == 0
    buttons = {PLATE: _merge("04")}
    resolved = _resolve_operation_point(
        PLATE, key_raw, buttons, _build_bus_to_op_index(buttons), _build_ir_base_lookup(buttons)
    )
    assert resolved is not None
    assert resolved[1] == "1A"
    assert resolved[2]["bus_address"] == UPPER


def test_the_lower_key_record_lands_on_1b() -> None:
    buttons = {PLATE: _merge("04")}
    resolved = _resolve_operation_point(
        PLATE, 2, buttons, _build_bus_to_op_index(buttons), _build_ir_base_lookup(buttons)
    )
    assert resolved is not None and resolved[1] == "1B"


def test_a_store_with_the_old_addresses_is_corrected_on_the_next_inventory() -> None:
    old = {
        "nikobus_button": {
            PLATE: {
                "type": "Bus push button, 2 control buttons",
                "model": "05-060",
                "channels": 2,
                "description": "Hall",
                "operation_points": {
                    "1A": {"bus_address": "8753BA", "description": "Push button 1A #N8753BA"},
                    "1B": {"bus_address": "C753BA", "description": "Lights hall"},
                },
            }
        }
    }
    entry = _merge("04", button_data=old)
    ops = entry["operation_points"]
    assert ops["1A"] == {"bus_address": UPPER, "description": f"Push button 1A #N{UPPER}"}
    assert ops["1B"] == {"bus_address": LOWER, "description": "Lights hall"}  # the user's name stays
    assert entry["description"] == "Hall"
    assert entry["device_type"] == "04"


@pytest.mark.parametrize("model", ["05-060", "05-060-01", "41-072", "05-061", "05-060-02", "42-082"])
def test_a_project_file_plate_is_found_by_its_product_reference(model: str) -> None:
    assert key_layout_for_model(model) == {"1A": "0", "1B": "4"}
    assert per_key_bus_address(PLATE, 2, "1A", model) == UPPER
    assert per_key_bus_address(PLATE, 2, "1B", model) == LOWER


@pytest.mark.parametrize("model", ["05-056", "05-064", "", None])
def test_other_products_keep_the_generic_layout(model: str | None) -> None:
    assert key_layout_for_model(model) is None
    assert per_key_bus_address(PLATE, 2, "1A", model) == "8753BA"


def test_the_manual_config_built_from_a_project_file_uses_0_and_4() -> None:
    comps = [{"KeyComponent": 1, "KeyProductBase": 5, "PhysicalAddress": int(PLATE, 16),
              "StrUserName": "Hall"}]
    productbase = [{"KeyProductBase": 5, "NikoRefNr": "05-060"}]
    objecten = [{"KeyComponent": 1, "KeyObjectBase": k} for k in (10, 11)]
    objectbase = {10: {"KeyObjectBase": 10, "Prefix": "A"}, 11: {"KeyObjectBase": 11, "Prefix": "B"}}
    buttons = build_config(comps, productbase, objecten, objectbase).button_config["nikobus_button"]
    assert {b["linked_button"][0]["key"]: b["address"] for b in buttons} == {"1A": UPPER, "1B": LOWER}
