"""Tests for PC-Logic logical-input synthesis.

PC-Logic (05-201) doesn't enumerate its 6 logical inputs in the
``$1011`` inventory frames — the input bus addresses are computed by
the firmware from the PC-Logic's own bus address plus a slot index.

The library compensates by synthesizing ``category="Button"`` entries
in ``discovered_devices`` for each PC-Logic input, so the regular
``merge_discovered_buttons`` path writes them into the button store
as 2-channel virtual buttons. The HA integration then renders each
as an ``LM-INPUT N`` device under the PC-Logic module.

This file pins:

  * The address-derivation formula (and its inverse).
  * The synthesis path's idempotency and field shape.
  * The provenance fields (``pc_logic_parent_address``,
    ``pc_logic_slot_index``) survive the merge into the button store.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.fileio import merge_discovered_buttons
from nikobus_connect.discovery.mapping import KEY_MAPPING
from nikobus_connect.discovery.protocol import (
    convert_nikobus_address,
    derive_pc_logic_input_physicals,
    pc_logic_address_for_input,
    pc_logic_input_slot_index,
)


# ---------------------------------------------------------------------------
# Address-derivation unit tests
# ---------------------------------------------------------------------------


def test_derive_inputs_for_940c_matches_observed_install():
    """The 2026-05-18 install at PC-Logic 940C captured 12 bus events.
    Their inverse-transform yields 6 physicals: 64A061..64A066. The
    derivation must reproduce that exact list."""

    physicals = derive_pc_logic_input_physicals("940C", 6)
    assert physicals == ["64A061", "64A062", "64A063", "64A064", "64A065", "64A066"]


def test_derived_physicals_produce_observed_bus_addresses():
    """Each input physical, fed through ``convert_nikobus_address``,
    must reproduce one of the 12 bus events the user captured."""

    expected_primary_bus = {
        "64A061": "21814B",
        "64A062": "11814B",
        "64A063": "31814B",
        "64A064": "09814B",
        "64A065": "29814B",
        "64A066": "19814B",
    }
    for phys, expected in expected_primary_bus.items():
        assert convert_nikobus_address(phys) == expected, phys


def test_inverse_pc_logic_address():
    """Given an input physical, recover the parent PC-Logic address."""

    for phys, expected_parent in [
        ("64A061", "940C"),
        ("64A062", "940C"),
        ("64A063", "940C"),
        ("64A064", "940C"),
        ("64A065", "940C"),
        ("64A066", "940C"),
    ]:
        assert pc_logic_address_for_input(phys) == expected_parent, phys


def test_inverse_pc_logic_slot_index():
    for phys, expected_slot in [
        ("64A061", 1),
        ("64A062", 2),
        ("64A063", 3),
        ("64A064", 4),
        ("64A065", 5),
        ("64A066", 6),
    ]:
        assert pc_logic_input_slot_index(phys) == expected_slot, phys


def test_inverse_returns_none_on_non_pc_logic_address():
    """A regular wall-button physical address must not be misclassified
    as a PC-Logic input."""

    # Real wall button from the 2026-05-18 inventory dump:
    assert pc_logic_address_for_input("1843B4") is None
    assert pc_logic_input_slot_index("1843B4") is None


def test_derive_rejects_invalid_address():
    with pytest.raises(ValueError):
        derive_pc_logic_input_physicals("not-hex", 6)


def test_derive_rejects_overflow_address():
    """An address where byteswap*8 overflows 16 bits hasn't been seen
    on validated hardware; refuse rather than guess."""

    # byteswap(9E90) = 0x909E; 0x909E * 8 = 0x484F0 > 0xFFFF
    with pytest.raises(ValueError, match="overflow"):
        derive_pc_logic_input_physicals("9E90", 6)


def test_derive_respects_channel_count():
    """With channel_count=3, only 3 inputs are returned."""

    assert derive_pc_logic_input_physicals("940C", 3) == ["64A061", "64A062", "64A063"]


# ---------------------------------------------------------------------------
# Synthesis path tests
# ---------------------------------------------------------------------------


def _make_discovery(tmp_path) -> NikobusDiscovery:
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
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )


def test_synthesize_adds_six_inputs_for_a_pc_logic_module(tmp_path):
    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "940C": {
            "category": "Module",
            "module_type": "pc_logic",
            "device_type": "08",
            "model": "05-201",
            "address": "940C",
            "channels": 6,
            "channels_count": 6,
        }
    }

    discovery._synthesize_pc_logic_inputs()

    for slot, phys in enumerate(
        ["64A061", "64A062", "64A063", "64A064", "64A065", "64A066"], start=1
    ):
        assert phys in discovery.discovered_devices, phys
        entry = discovery.discovered_devices[phys]
        assert entry["category"] == "Button"
        assert entry["channels"] == 2
        assert entry["pc_logic_parent_address"] == "940C"
        assert entry["pc_logic_slot_index"] == slot
        assert entry["description"] == NikobusDiscovery.PC_LOGIC_INPUT_TYPE


def test_synthesize_does_nothing_when_no_pc_logic(tmp_path):
    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "C9A5": {
            "category": "Module",
            "module_type": "switch_module",
            "channels": 12,
            "channels_count": 12,
        }
    }
    before = dict(discovery.discovered_devices)

    discovery._synthesize_pc_logic_inputs()

    assert discovery.discovered_devices == before


def test_synthesize_doesnt_shadow_existing_button(tmp_path):
    """If a real button at one of the derived addresses already exists
    (vanishingly rare but possible if Niko ever assigns an overlapping
    range), the synthesis must defer to the real entry."""

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "940C": {
            "category": "Module",
            "module_type": "pc_logic",
            "channels": 6,
            "channels_count": 6,
        },
        "64A063": {
            "category": "Button",
            "model": "05-346",
            "channels": 4,
            "device_type": "06",
            "description": "Bus push button, 4 control buttons",
        },
    }

    discovery._synthesize_pc_logic_inputs()

    # Real button entry untouched.
    assert discovery.discovered_devices["64A063"]["model"] == "05-346"
    # Other slots still synthesized.
    for phys in ["64A061", "64A062", "64A064", "64A065", "64A066"]:
        assert discovery.discovered_devices[phys]["category"] == "Button"
        assert discovery.discovered_devices[phys].get("pc_logic_parent_address") == "940C"


def test_synthesize_is_idempotent(tmp_path):
    """Running synthesis twice produces the same result — important
    because discovery may run repeatedly across the integration's
    lifetime."""

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "940C": {
            "category": "Module",
            "module_type": "pc_logic",
            "channels": 6,
            "channels_count": 6,
        }
    }
    discovery._synthesize_pc_logic_inputs()
    snapshot_a = {k: dict(v) for k, v in discovery.discovered_devices.items()}

    discovery._synthesize_pc_logic_inputs()
    snapshot_b = {k: dict(v) for k, v in discovery.discovered_devices.items()}

    assert snapshot_a == snapshot_b


# ---------------------------------------------------------------------------
# End-to-end: synthesis + merge into the button store
# ---------------------------------------------------------------------------


def test_synthesized_input_flows_through_merge_to_button_store(tmp_path):
    """The full happy path: synthesize, then run ``merge_discovered_buttons``,
    confirm the resulting button-store entry carries the provenance
    fields the HA-side renderer needs."""

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "940C": {
            "category": "Module",
            "module_type": "pc_logic",
            "channels": 6,
            "channels_count": 6,
            "address": "940C",
        }
    }
    discovery._synthesize_pc_logic_inputs()

    button_data: dict = {"nikobus_button": {}}
    merge_discovered_buttons(
        button_data,
        discovery.discovered_devices,
        KEY_MAPPING,
        convert_nikobus_address,
    )

    nikobus_buttons = button_data["nikobus_button"]
    for slot, phys in enumerate(
        ["64A061", "64A062", "64A063", "64A064", "64A065", "64A066"], start=1
    ):
        assert phys in nikobus_buttons, phys
        entry = nikobus_buttons[phys]
        assert entry["pc_logic_parent_address"] == "940C"
        assert entry["pc_logic_slot_index"] == slot
        assert entry["channels"] == 2
        # 2-channel button → 2 operation points (1A primary, 1B alias).
        assert set(entry["operation_points"].keys()) == {"1A", "1B"}


def test_merged_button_provenance_survives_re_merge(tmp_path):
    """Re-running discovery (and re-merging) must keep the provenance
    fields populated, not strip them."""

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "940C": {
            "category": "Module",
            "module_type": "pc_logic",
            "channels": 6,
            "channels_count": 6,
            "address": "940C",
        }
    }
    discovery._synthesize_pc_logic_inputs()

    button_data: dict = {"nikobus_button": {}}
    # First merge.
    merge_discovered_buttons(
        button_data,
        discovery.discovered_devices,
        KEY_MAPPING,
        convert_nikobus_address,
    )
    # Second merge with the same discovered_devices — should be idempotent
    # for the provenance fields too.
    merge_discovered_buttons(
        button_data,
        discovery.discovered_devices,
        KEY_MAPPING,
        convert_nikobus_address,
    )

    entry = button_data["nikobus_button"]["64A061"]
    assert entry["pc_logic_parent_address"] == "940C"
    assert entry["pc_logic_slot_index"] == 1
