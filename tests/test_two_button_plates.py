"""Two-button plates (05-060, 05-061, 05-060-02) come in two key layouts.

The same type byte and model on two installs, two layouts:

* nikobus-connect #184 (14 plates): plate 1772B8 sends 0753BA / 4753BA,
  and its module's record ``FF06F0E0CA5D`` holds key index 0 — the
  four-button's right-hand pair (nibbles 0 / 4);
* fdebrus: switch module C9A5's record ``FF16F0583160`` for plate 180C56
  holds key index 1, and the plate sends 9A8C06 / DA8C06 — the generic
  layout (nibbles 8 / C). 0.57.1 moved every such plate to 0 / 4 and
  broke this install.

The layout is read per plate from the key indices its link records use.
The 4*-082 (0x10) is always the right pair.
"""

from __future__ import annotations

import pytest

from nikobus_connect.discovery.fileio import (
    has_per_plate_key_layout,
    merge_discovered_buttons,
    merge_linked_modules,
    two_button_bus_addresses,
)
from nikobus_connect.discovery.mapping import DEVICE_TYPES, KEY_MAPPING
from nikobus_connect.discovery.protocol import (
    convert_nikobus_address,
    get_button_address,
)
from nikobus_connect.nkb.config_builder import build_config
from nikobus_connect.nkb.parser import key_layout_for_model, per_key_bus_address

REPORTER = "1772B8"  # right pair: 0753BA / 4753BA
FDEBRUS = "180C56"  # generic: 9A8C06 / DA8C06


def _discovered(physical: str, device_type: str) -> dict:
    spec = DEVICE_TYPES[device_type]
    return {physical: {"category": "Button", "device_type": device_type, "model": spec["Model"],
                       "channels": spec["Channels"], "description": spec["Name"]}}


def _inventory(physical: str, device_type: str = "04", button_data: dict | None = None) -> dict:
    button_data = button_data if button_data is not None else {"nikobus_button": {}}
    merge_discovered_buttons(button_data, _discovered(physical, device_type), KEY_MAPPING, convert_nikobus_address)
    return button_data


def _records(*records: tuple[str, int, str, int]) -> dict:
    return {
        (plate, key, None): [{"module_address": module, "channel": channel, "mode": "M01 (On / off)",
                              "button_address": plate, "record_source": "output_module_table"}]
        for plate, key, module, channel in records
    }


def _keys(button_data: dict, physical: str) -> tuple[str, str]:
    ops = button_data["nikobus_button"][physical]["operation_points"]
    return ops["1A"]["bus_address"], ops["1B"]["bus_address"]


def _links(button_data: dict, physical: str, label: str) -> set[tuple[str, int]]:
    op = button_data["nikobus_button"][physical]["operation_points"][label]
    return {(b["module_address"], o["channel"]) for b in op.get("linked_modules", []) for o in b["outputs"]}


def test_both_reporters_records_decode_to_their_plates() -> None:
    assert (get_button_address("E0CA5D"), int("FF06F0E0CA5D"[2], 16)) == (REPORTER, 0)
    assert (get_button_address("583160"), int("FF16F0583160"[2], 16)) == (FDEBRUS, 1)


@pytest.mark.parametrize("device_type", ["04", "05", "3F"])
def test_an_inventory_starts_a_plate_in_the_generic_layout(device_type: str) -> None:
    data = _inventory(FDEBRUS, device_type)
    assert _keys(data, FDEBRUS) == ("9A8C06", "DA8C06")
    assert data["nikobus_button"][FDEBRUS]["device_type"] == device_type
    assert "key_layout" not in data["nikobus_button"][FDEBRUS]


def test_records_at_key_indices_1_and_3_keep_the_generic_layout() -> None:
    data = _inventory(FDEBRUS)
    merge_linked_modules(data, _records((FDEBRUS, 1, "C9A5", 7), (FDEBRUS, 3, "C9A5", 8)))
    assert _keys(data, FDEBRUS) == ("9A8C06", "DA8C06")
    assert ("C9A5", 7) in _links(data, FDEBRUS, "1A")
    assert ("C9A5", 8) in _links(data, FDEBRUS, "1B")


def test_records_at_key_indices_0_and_2_move_the_plate_to_the_right_pair() -> None:
    data = _inventory(REPORTER)
    assert _keys(data, REPORTER) == ("8753BA", "C753BA")
    merge_linked_modules(data, _records((REPORTER, 0, "7D5F", 7), (REPORTER, 2, "7D5F", 8)))
    entry = data["nikobus_button"][REPORTER]
    assert _keys(data, REPORTER) == ("0753BA", "4753BA")
    assert entry["key_layout"] == "0_4"
    assert ("7D5F", 7) in _links(data, REPORTER, "1A")
    assert ("7D5F", 8) in _links(data, REPORTER, "1B")
    assert entry["operation_points"]["1A"]["description"] == "Push button 1A #N0753BA"


def test_one_record_is_enough() -> None:
    data = _inventory(REPORTER)
    merge_linked_modules(data, _records((REPORTER, 0, "7D5F", 7)))
    assert _keys(data, REPORTER) == ("0753BA", "4753BA")


def test_the_next_inventory_keeps_a_detected_layout() -> None:
    data = _inventory(REPORTER)
    merge_linked_modules(data, _records((REPORTER, 0, "7D5F", 7)))
    _inventory(REPORTER, button_data=data)
    assert _keys(data, REPORTER) == ("0753BA", "4753BA")
    assert data["nikobus_button"][REPORTER]["key_layout"] == "0_4"


def test_records_of_both_families_leave_the_plate_alone() -> None:
    data = _inventory(REPORTER)
    merge_linked_modules(data, _records((REPORTER, 0, "7D5F", 7), (REPORTER, 1, "7D5F", 8)))
    assert _keys(data, REPORTER) == ("8753BA", "C753BA")


def test_a_user_named_key_keeps_its_name_when_it_moves() -> None:
    data = _inventory(REPORTER)
    data["nikobus_button"][REPORTER]["operation_points"]["1B"]["description"] = "Hall lights"
    merge_linked_modules(data, _records((REPORTER, 2, "7D5F", 8)))
    assert data["nikobus_button"][REPORTER]["operation_points"]["1B"]["description"] == "Hall lights"


def _store_written_by_0571(physical: str, with_links: bool) -> dict:
    right = two_button_bus_addresses(physical, "0_4")
    ops = {label: {"bus_address": bus, "description": f"Push button {label} #N{bus}"} for label, bus in right.items()}
    if with_links:
        ops["1A"]["linked_modules"] = [{"module_address": "7D5F", "outputs": [{"channel": 7}]}]
    return {"nikobus_button": {physical: {"type": "Bus push button, 2 control buttons", "model": "05-060",
                                          "device_type": "04", "channels": 2, "operation_points": ops}}}


def test_a_0571_store_whose_right_pair_keys_have_links_stays_there() -> None:
    data = _inventory(REPORTER, button_data=_store_written_by_0571(REPORTER, with_links=True))
    assert _keys(data, REPORTER) == ("0753BA", "4753BA")
    assert data["nikobus_button"][REPORTER]["key_layout"] == "0_4"


def test_a_0571_store_without_links_goes_back_to_the_generic_layout() -> None:
    data = _inventory(FDEBRUS, button_data=_store_written_by_0571(FDEBRUS, with_links=False))
    assert _keys(data, FDEBRUS) == ("9A8C06", "DA8C06")
    assert "key_layout" not in data["nikobus_button"][FDEBRUS]


def test_the_link_scan_alone_repairs_a_0571_store() -> None:
    data = _store_written_by_0571(FDEBRUS, with_links=False)
    merge_linked_modules(data, _records((FDEBRUS, 1, "C9A5", 7)))
    assert _keys(data, FDEBRUS) == ("9A8C06", "DA8C06")
    assert ("C9A5", 7) in _links(data, FDEBRUS, "1A")


def test_the_4x082_is_always_the_right_pair() -> None:
    data = _inventory("130078", "10")
    assert _keys(data, "130078") == ("078032", "478032")
    merge_linked_modules(data, _records(("130078", 1, "C9A5", 1)))  # never moved by records
    assert _keys(data, "130078") == ("078032", "478032")


@pytest.mark.parametrize(("device_type", "model"), [("21", "05-056"), ("22", "05-057"), ("1F", "05-302")])
def test_interfaces_and_the_rf_push_button_are_not_per_plate(device_type: str, model: str) -> None:
    data = _inventory(REPORTER, device_type)
    assert not has_per_plate_key_layout(data["nikobus_button"][REPORTER])
    merge_linked_modules(data, _records((REPORTER, 0, "7D5F", 7)))
    assert _keys(data, REPORTER) == ("8753BA", "C753BA")


@pytest.mark.parametrize("model", ["05-060", "05-060-01", "41-072", "05-061", "05-060-02"])
def test_a_store_entry_is_recognised_by_its_model_without_a_type_byte(model: str) -> None:
    assert has_per_plate_key_layout({"model": model, "channels": 2})
    assert not has_per_plate_key_layout({"model": model, "channels": 4})


@pytest.mark.parametrize(("model", "expected"), [("42-082", "078032"), ("05-060", "878032"), ("05-056", "878032")])
def test_a_project_file_uses_the_fixed_layout_only_for_the_4x082(model: str, expected: str) -> None:
    assert per_key_bus_address("130078", 2, "1A", model) == expected
    assert (key_layout_for_model(model) is not None) == (model == "42-082")


def test_the_manual_config_starts_a_05_060_in_the_generic_layout() -> None:
    comps = [{"KeyComponent": 1, "KeyProductBase": 5, "PhysicalAddress": int(FDEBRUS, 16), "StrUserName": "Vestiaire"}]
    productbase = [{"KeyProductBase": 5, "NikoRefNr": "05-060"}]
    objecten = [{"KeyComponent": 1, "KeyObjectBase": k} for k in (10, 11)]
    objectbase = {10: {"KeyObjectBase": 10, "Prefix": "A"}, 11: {"KeyObjectBase": 11, "Prefix": "B"}}
    buttons = build_config(comps, productbase, objecten, objectbase).button_config["nikobus_button"]
    assert {b["linked_button"][0]["key"]: b["address"] for b in buttons} == {"1A": "9A8C06", "1B": "DA8C06"}
