"""Buttons that exist only in programming get an entry of their own.

Two kinds of address turn up in output modules' link tables that no
wall plate can emit:

* the PC-Logic's logic outputs. On a real install (2026-10) PC-Logic
  ``8DC8`` fired ``#N002087`` (on) and ``#N402087`` (off) 0.6 s after
  the key that triggered logic function L01; switch module ``81F6``
  held the link on physical ``684100``, key 0, mode M01 — which the
  merge used to drop as an unknown button, so the frame stayed an
  "unknown press" and the output it switched was never refreshed;
* the Nikobus software's virtual input banks (``3CF000``–``3CFFFF``),
  which programs and scenes fire.

Discovery now synthesizes a plate for each such address from the keys
the output modules reference, and the links merge onto it. Anything
else that is unmatched stays unmatched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.discovery import fileio
from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.fileio import (
    VIRTUAL_BUTTON_MODEL,
    VIRTUAL_BUTTON_TYPE,
    merge_discovered_buttons,
    merge_linked_modules,
)
from nikobus_connect.discovery.mapping import KEY_MAPPING
from nikobus_connect.discovery.protocol import (
    VIRTUAL_CLASS_BANK,
    VIRTUAL_CLASS_PC_LOGIC,
    convert_nikobus_address,
    virtual_button_class,
)

# Peter's install: switch module 81F6, channel 6, driven by PC-Logic L01.
L01_PHYSICAL = "684100"
L01_RECORD = {
    "module_address": "81F6",
    "channel": 6,
    "mode": "M01 (On / off)",
    "t1": "0s",
    "t2": None,
    "payload": "000601" + "002087",
    "button_address": L01_PHYSICAL,
    "record_source": "output_module_table",
}


def _make_discovery(tmp_path, button_data=None) -> NikobusDiscovery:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    return NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=MagicMock(),
        button_data=button_data if button_data is not None else {"nikobus_button": {}},
        on_button_save=None,
    )


def _merge(discovery: NikobusDiscovery, command_mapping: dict) -> dict:
    button_data = discovery._button_data
    merge_discovered_buttons(
        button_data, discovery.discovered_devices, KEY_MAPPING, convert_nikobus_address
    )
    merge_linked_modules(button_data, command_mapping)
    return button_data["nikobus_button"]


# ---------------------------------------------------------------------------
# Address classes
# ---------------------------------------------------------------------------


def test_virtual_classes_cover_pc_logic_and_the_software_banks() -> None:
    assert virtual_button_class("684100") == VIRTUAL_CLASS_PC_LOGIC
    assert virtual_button_class("600000") == VIRTUAL_CLASS_PC_LOGIC
    assert virtual_button_class("6FFFFF") == VIRTUAL_CLASS_PC_LOGIC
    # A PC-Logic input (0x600000 | (addr >> 1) << 4 | slot) is in the class too.
    assert virtual_button_class("64A061") == VIRTUAL_CLASS_PC_LOGIC
    assert virtual_button_class("3CF006") == VIRTUAL_CLASS_BANK
    assert virtual_button_class("3CF040") == VIRTUAL_CLASS_BANK  # the audio bank
    assert virtual_button_class("3CF3F0") == VIRTUAL_CLASS_BANK


def test_wall_buttons_and_junk_are_not_virtual() -> None:
    assert virtual_button_class("2C4E00") is None
    assert virtual_button_class("5FFFFF") is None
    assert virtual_button_class("700000") is None
    assert virtual_button_class("3CEFFF") is None
    assert virtual_button_class("3D0000") is None
    assert virtual_button_class("E00320") is None  # a calendar channel
    assert virtual_button_class("") is None
    assert virtual_button_class("zz") is None


# ---------------------------------------------------------------------------
# Peter's L01: the PC-Logic logic output
# ---------------------------------------------------------------------------


def test_pc_logic_logic_output_gets_an_entry_with_the_bus_frames(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "8DC8": {"category": "Module", "module_type": "pc_logic", "address": "8DC8"}
    }
    command_mapping = {(L01_PHYSICAL, 0, None): [dict(L01_RECORD)]}
    discovery._accumulated_command_mapping = command_mapping
    discovery._accumulated_unmatched = {L01_PHYSICAL}

    assert discovery._synthesize_virtual_buttons_from_unmatched() == 1

    entry = discovery.discovered_devices[L01_PHYSICAL]
    assert entry["category"] == "Button"
    assert entry["type"] == VIRTUAL_BUTTON_TYPE
    assert entry["model"] == VIRTUAL_BUTTON_MODEL
    assert entry["channels"] == 4
    assert entry["virtual_button"] is True
    assert entry["virtual_class"] == VIRTUAL_CLASS_PC_LOGIC
    assert entry["virtual_parent_address"] == "8DC8"
    assert entry["description"] == "PC-Logic virtual output 684100"

    buttons = _merge(discovery, command_mapping)
    stored = buttons[L01_PHYSICAL]
    assert stored["virtual_button"] is True
    assert stored["virtual_class"] == VIRTUAL_CLASS_PC_LOGIC
    assert stored["virtual_parent_address"] == "8DC8"
    op_points = stored["operation_points"]
    assert set(op_points) == {"1A", "1B", "1C", "1D"}
    # The frames the PC-Logic puts on the bus for L01 on / off.
    assert op_points["1C"]["bus_address"] == "002087"
    assert op_points["1D"]["bus_address"] == "402087"
    # Key 0 is 1C; M01 is a two-button mode, so 1D mirrors the link.
    for key in ("1C", "1D"):
        linked = op_points[key]["linked_modules"]
        assert linked[0]["module_address"] == "81F6"
        assert linked[0]["outputs"][0]["channel"] == 6
    assert "linked_modules" not in op_points["1A"] or not op_points["1A"]["linked_modules"]


def test_pc_logic_parent_is_taken_from_the_module_store_when_not_rediscovered(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    discovery._module_data = {
        "nikobus_module": {"8DC8": {"module_type": "pc_logic", "address": "8DC8"}}
    }
    discovery._accumulated_command_mapping = {(L01_PHYSICAL, 0, None): [dict(L01_RECORD)]}
    discovery._accumulated_unmatched = {L01_PHYSICAL}
    discovery._synthesize_virtual_buttons_from_unmatched()
    assert discovery.discovered_devices[L01_PHYSICAL]["virtual_parent_address"] == "8DC8"


def test_two_pc_logics_means_no_parent_guess(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "8DC8": {"category": "Module", "module_type": "pc_logic"},
        "940C": {"category": "Module", "module_type": "pc_logic"},
    }
    discovery._accumulated_command_mapping = {(L01_PHYSICAL, 0, None): [dict(L01_RECORD)]}
    discovery._accumulated_unmatched = {L01_PHYSICAL}
    discovery._synthesize_virtual_buttons_from_unmatched()
    entry = discovery.discovered_devices[L01_PHYSICAL]
    assert entry["virtual_class"] == VIRTUAL_CLASS_PC_LOGIC
    assert "virtual_parent_address" not in entry


# ---------------------------------------------------------------------------
# The software's virtual input banks
# ---------------------------------------------------------------------------


def test_bank_key_gets_an_entry_and_its_links(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    records = {
        ("3CF006", 0, None): [
            {
                "module_address": "4707",
                "channel": 1,
                "mode": "M02 (On)",
                "record_source": "output_module_table",
            }
        ],
        ("3CF006", 1, None): [
            {
                "module_address": "4707",
                "channel": 2,
                "mode": "M03 (Off)",
                "record_source": "output_module_table",
            }
        ],
    }
    discovery._accumulated_command_mapping = records
    discovery._accumulated_unmatched = {"3CF006"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 1
    entry = discovery.discovered_devices["3CF006"]
    assert entry["virtual_class"] == VIRTUAL_CLASS_BANK
    assert entry["channels"] == 4
    assert "virtual_parent_address" not in entry
    assert entry["description"] == "Virtual button 3CF006"

    buttons = _merge(discovery, records)
    op_points = buttons["3CF006"]["operation_points"]
    assert op_points["1C"]["bus_address"] == convert_nikobus_address("3CF006")
    assert op_points["1C"]["linked_modules"][0]["outputs"][0]["channel"] == 1
    assert op_points["1A"]["linked_modules"][0]["outputs"][0]["channel"] == 2
    # Single-key modes are not mirrored onto the pair.
    assert not op_points["1D"].get("linked_modules")
    assert not op_points["1B"].get("linked_modules")


def test_a_key_index_above_three_makes_an_eight_key_plate(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    records = {
        ("3CF010", 5, None): [
            {"module_address": "4707", "channel": 3, "mode": "M02 (On)",
             "record_source": "output_module_table"}
        ]
    }
    discovery._accumulated_command_mapping = records
    discovery._accumulated_unmatched = {"3CF010"}
    discovery._synthesize_virtual_buttons_from_unmatched()
    assert discovery.discovered_devices["3CF010"]["channels"] == 8
    buttons = _merge(discovery, records)
    op_points = buttons["3CF010"]["operation_points"]
    assert len(op_points) == 8
    # Raw key 5 is nibble A, the 1A key of an eight-key plate.
    assert op_points["1A"]["linked_modules"][0]["outputs"][0]["channel"] == 3


# ---------------------------------------------------------------------------
# What is left alone
# ---------------------------------------------------------------------------


def test_unknown_wall_buttons_stay_unmatched(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    records = {
        ("2C4E00", 0, None): [
            {"module_address": "4707", "channel": 1, "mode": "M01 (On / off)",
             "record_source": "output_module_table"}
        ]
    }
    discovery._accumulated_command_mapping = records
    discovery._accumulated_unmatched = {"2C4E00"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert discovery.discovered_devices == {}
    _, links, outputs, unmatched = merge_linked_modules(discovery._button_data, records)
    assert (links, outputs, unmatched) == (0, 0, {"2C4E00"})


def test_registry_only_records_do_not_make_an_entry(tmp_path) -> None:
    """A PC-Link registry row with no output module behind it may be residue."""
    discovery = _make_discovery(tmp_path)
    discovery._accumulated_command_mapping = {
        (L01_PHYSICAL, 0, None): [
            {"module_address": "81F6", "channel": 6, "mode": "M01 (On / off)",
             "record_source": "pc_link_registry"}
        ]
    }
    discovery._accumulated_unmatched = {L01_PHYSICAL}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert discovery.discovered_devices == {}


def test_ir_codes_do_not_make_an_entry(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    discovery._accumulated_command_mapping = {
        ("3CF006", 0, "10B"): [
            {"module_address": "4707", "channel": 1, "mode": "M02 (On)",
             "record_source": "output_module_table"}
        ]
    }
    discovery._accumulated_unmatched = {"3CF006"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0


def test_existing_entries_are_not_shadowed(tmp_path) -> None:
    button_data = {
        "nikobus_button": {
            "3CF006": {"description": "Mine", "channels": 4, "operation_points": {}}
        }
    }
    discovery = _make_discovery(tmp_path, button_data)
    discovery._accumulated_command_mapping = {
        ("3CF006", 0, None): [
            {"module_address": "4707", "channel": 1, "mode": "M02 (On)",
             "record_source": "output_module_table"}
        ]
    }
    discovery._accumulated_unmatched = {"3CF006"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert button_data["nikobus_button"]["3CF006"]["description"] == "Mine"


def test_synthesis_is_idempotent(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {"8DC8": {"category": "Module", "module_type": "pc_logic"}}
    discovery._accumulated_command_mapping = {(L01_PHYSICAL, 0, None): [dict(L01_RECORD)]}
    discovery._accumulated_unmatched = {L01_PHYSICAL}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 1
    snapshot = {k: dict(v) for k, v in discovery.discovered_devices.items()}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert {k: dict(v) for k, v in discovery.discovered_devices.items()} == snapshot


def test_store_constants_are_exported_for_hosts() -> None:
    assert fileio.VIRTUAL_BUTTON_TYPE == "Virtual button"
    assert fileio.VIRTUAL_BUTTON_MODEL == "Virtual"
