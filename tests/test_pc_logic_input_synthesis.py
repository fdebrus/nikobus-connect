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


def test_derive_respects_channel_count():
    """With channel_count=3, only 3 inputs are returned."""

    assert derive_pc_logic_input_physicals("940C", 3) == ["64A061", "64A062", "64A063"]


# ---------------------------------------------------------------------------
# Second-install validation: PC-Logic at 0x8DC8
# ---------------------------------------------------------------------------
#
# The 0.8.0/0.9.0 byteswap*8 formula overflows on this address (0xC88D × 8
# = 0x64468 > 0xFFFF) and was rejected with ValueError. The user's
# .migrated v1 file gives us the ground-truth bus addresses to validate
# the rewritten formula against.


def test_derive_inputs_for_8dc8_matches_observed_install():
    """PC-Logic at 0x8DC8 produces physicals 646E41..646E46.
    Sourced from the user's .migrated v1 file (twelve bus events
    decoded inversely)."""

    physicals = derive_pc_logic_input_physicals("8DC8", 6)
    assert physicals == ["646E41", "646E42", "646E43", "646E44", "646E45", "646E46"]


def test_8dc8_physicals_produce_observed_bus_addresses():
    """Each derived input physical for 8DC8, fed through
    ``convert_nikobus_address``, must reproduce the primary
    (1A / Hoog) bus addresses from the user's v1 dump."""

    expected_primary_bus = {
        "646E41": "209D8B",
        "646E42": "109D8B",
        "646E43": "309D8B",
        "646E44": "089D8B",
        "646E45": "289D8B",
        "646E46": "189D8B",
    }
    for phys, expected in expected_primary_bus.items():
        assert convert_nikobus_address(phys) == expected, phys


def test_inverse_pc_logic_address_8dc8():
    """The inverse must work for both validated installs, not just 940C."""

    for phys, expected_parent in [
        ("646E41", "8DC8"),
        ("646E42", "8DC8"),
        ("646E43", "8DC8"),
        ("646E44", "8DC8"),
        ("646E45", "8DC8"),
        ("646E46", "8DC8"),
    ]:
        assert pc_logic_address_for_input(phys) == expected_parent, phys


def test_inverse_pc_logic_slot_index_8dc8():
    for phys, expected_slot in [
        ("646E41", 1),
        ("646E42", 2),
        ("646E43", 3),
        ("646E44", 4),
        ("646E45", 5),
        ("646E46", 6),
    ]:
        assert pc_logic_input_slot_index(phys) == expected_slot, phys


# ---------------------------------------------------------------------------
# Interface-module synthesis (05-206) — OWN address scheme, hardware-
# validated (issue #485)
# ---------------------------------------------------------------------------
#
# The pre-0.34.0 hypothesis (same formula as PC-Logic) was pinned here
# with a "fails loudly if hardware disagrees" test. Hardware disagreed:
# unit 0548's inputs emit 24A806/64A806 (slot 1) and 14A806/54A806
# (slot 2) on the wire — reproduced exactly by
# ``0x180000 + module_addr + slot``, and provably unreachable by the
# pc_logic scheme (exhaustive search over all 2^16 module addresses).
# The PC-Logic scheme itself was re-validated on the SAME install
# (module 8806, physical contact capture) — the two families genuinely
# use different firmware derivations.


def test_synthesize_extends_to_interface_module(tmp_path):
    """A 05-206 interface_module discovery entry triggers the same
    synthesis path as pc_logic — six button entries, each carrying
    provenance tagged with module_type='interface_module' — but with
    the interface-specific address scheme."""

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "6E40": {
            "category": "Module",
            "module_type": "interface_module",
            "model": "05-206",
            "address": "6E40",
            "channels": 6,
            "channels_count": 6,
        }
    }

    discovery._synthesize_pc_logic_inputs()

    expected_physicals = [f"186E4{slot}" for slot in range(1, 7)]
    for slot, phys in enumerate(expected_physicals, start=1):
        assert phys in discovery.discovered_devices, phys
        entry = discovery.discovered_devices[phys]
        assert entry["category"] == "Button"
        assert entry["channels"] == 2
        assert entry["pc_logic_parent_address"] == "6E40"
        assert entry["pc_logic_parent_type"] == "interface_module"
        assert entry["pc_logic_slot_index"] == slot
        assert entry["model"] == "05-206"
        assert entry["description"] == NikobusDiscovery.INTERFACE_MODULE_INPUT_TYPE


def test_interface_module_bus_addresses_through_the_merge(tmp_path):
    """End-to-end: interface_module 0x6E40 synthesized and merged must
    listen on the addresses the 05-206 scheme produces. (The previous
    revision of this test pinned the pc_logic-formula prediction with a
    'fails loudly if hardware disagrees' note — hardware disagreed in
    issue #485, which is exactly how the wrong assumption surfaced.)"""

    expected = {
        "186E41": {"1A": "209D86", "1B": "609D86"},
        "186E42": {"1A": "109D86", "1B": "509D86"},
        "186E43": {"1A": "309D86", "1B": "709D86"},
        "186E44": {"1A": "089D86", "1B": "489D86"},
        "186E45": {"1A": "289D86", "1B": "689D86"},
        "186E46": {"1A": "189D86", "1B": "589D86"},
    }

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "6E40": {
            "category": "Module",
            "module_type": "interface_module",
            "channels": 6,
            "channels_count": 6,
            "address": "6E40",
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

    for phys, ops in expected.items():
        entry = button_data["nikobus_button"][phys]
        op_points = entry["operation_points"]
        for key_label, expected_bus in ops.items():
            assert op_points[key_label]["bus_address"] == expected_bus, (
                phys,
                key_label,
            )
        # Provenance must carry through the merge so HA renders these
        # under the interface_module parent device.
        assert entry["pc_logic_parent_address"] == "6E40"
        assert entry["pc_logic_parent_type"] == "interface_module"


def test_synthesize_skips_other_module_types(tmp_path):
    """Switch, dimmer, roller, etc. modules must not trigger
    input-synthesis even if they happen to be in discovered_devices."""

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "C9A5": {
            "category": "Module",
            "module_type": "switch_module",
            "channels": 12,
            "channels_count": 12,
        },
        "8CF5": {
            "category": "Module",
            "module_type": "roller_module",
            "channels": 6,
            "channels_count": 6,
        },
    }
    before = dict(discovery.discovered_devices)
    discovery._synthesize_pc_logic_inputs()
    assert discovery.discovered_devices == before


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


# ---------------------------------------------------------------------------
# Bus-address derivation: PC-Logic-specific key mapping
# ---------------------------------------------------------------------------
#
# Regular 2-channel push buttons use ``KEY_MAPPING[2] = {1A: 8, 1B: C}`` so
# the op-point bus addresses are ``convert(phys) + 8`` / ``... + C``.
# PC-Logic logical inputs use offsets ``+0`` / ``+4`` instead. Hardware-
# captured from a 940C install (2026-05-20): pressing slot 6 emits
# ``19814B`` then ``59814B`` — first nibbles 1 and 5, matching
# ``original_nibble + 0`` and ``original_nibble + 4``.
#
# These tests pin the captured truth so any regression in the
# PC-Logic-specific mapping override gets caught immediately.


def test_pc_logic_synthesized_bus_addresses_match_hardware_940c(tmp_path):
    """Each synthesized input must produce op-point bus addresses
    matching what the hardware actually emits on press."""

    expected = {
        "64A061": {"1A": "21814B", "1B": "61814B"},
        "64A062": {"1A": "11814B", "1B": "51814B"},
        "64A063": {"1A": "31814B", "1B": "71814B"},
        "64A064": {"1A": "09814B", "1B": "49814B"},
        "64A065": {"1A": "29814B", "1B": "69814B"},
        "64A066": {"1A": "19814B", "1B": "59814B"},  # confirmed on hardware
    }

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

    for phys, ops in expected.items():
        entry = button_data["nikobus_button"][phys]
        op_points = entry["operation_points"]
        for key_label, expected_bus in ops.items():
            assert op_points[key_label]["bus_address"] == expected_bus, (
                phys,
                key_label,
            )


def test_pc_logic_synthesized_bus_addresses_match_hardware_8dc8(tmp_path):
    """Second-install pin: PC-Logic at 0x8DC8 from the user's
    .migrated v1 file. The 0.8.0/0.9.0 byteswap*8 formula refused
    to derive this install (overflow); the rewritten formula
    handles it cleanly. Bus addresses sourced verbatim from the
    user's v1 records (Hoog = 1A primary, Laag = 1B alias)."""

    expected = {
        "646E41": {"1A": "209D8B", "1B": "609D8B"},
        "646E42": {"1A": "109D8B", "1B": "509D8B"},
        "646E43": {"1A": "309D8B", "1B": "709D8B"},
        "646E44": {"1A": "089D8B", "1B": "489D8B"},
        "646E45": {"1A": "289D8B", "1B": "689D8B"},
        "646E46": {"1A": "189D8B", "1B": "589D8B"},
    }

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "8DC8": {
            "category": "Module",
            "module_type": "pc_logic",
            "channels": 6,
            "channels_count": 6,
            "address": "8DC8",
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

    for phys, ops in expected.items():
        entry = button_data["nikobus_button"][phys]
        op_points = entry["operation_points"]
        for key_label, expected_bus in ops.items():
            assert op_points[key_label]["bus_address"] == expected_bus, (
                phys,
                key_label,
            )


def test_regular_2channel_button_still_uses_standard_offsets(tmp_path):
    """A real 2-channel push button (no PC-Logic provenance) must still
    use the standard +8/+C offsets — the PC-Logic override must not
    bleed into ordinary buttons."""

    discovery = _make_discovery(tmp_path)
    discovery.discovered_devices = {
        "10998B": {
            "category": "Button",
            "address": "10998B",
            "channels": 2,
            "model": "05-060-02",
            "description": "Bus push button, 2 control buttons",
        }
    }

    button_data: dict = {"nikobus_button": {}}
    merge_discovered_buttons(
        button_data,
        discovery.discovered_devices,
        KEY_MAPPING,
        convert_nikobus_address,
    )

    entry = button_data["nikobus_button"]["10998B"]
    ops = entry["operation_points"]
    # original_nibble for 10998B is 3 (convert -> 346642); +8 -> B,
    # +C -> F. From the real-install dump shared by the user.
    assert ops["1A"]["bus_address"] == "B46642"
    assert ops["1B"]["bus_address"] == "F46642"


# ---------------------------------------------------------------------------
# Issue #485 hardware anchors — interface_module 0548 + pc_logic 8806
#
# All wire addresses below were captured from PHYSICAL contact toggles
# on the reporter's install (window sensor / terminal short), not from
# HA-side presses — the first true inbound validation for the 05-206.
# ---------------------------------------------------------------------------


def test_issue_485_interface_module_0548_matches_captured_frames():
    """Unit 0548's slot-1 pair (24A806/64A806, window contact) and
    slot-2 pair (14A806/54A806, requested confirmation capture) must
    both be reproduced by derivation + conversion + key offsets."""

    physicals = derive_pc_logic_input_physicals(
        "0548", 6, module_type="interface_module"
    )
    assert physicals == [
        "180549", "18054A", "18054B", "18054C", "18054D", "18054E",
    ]

    def pair(phys):
        c = convert_nikobus_address(phys)
        n = int(c[0], 16)
        return {f"{n:X}{c[1:]}", f"{(n + 4) & 0xF:X}{c[1:]}"}

    assert pair("180549") == {"24A806", "64A806"}  # slot 1, captured
    assert pair("18054A") == {"14A806", "54A806"}  # slot 2, captured


def test_issue_485_pc_logic_8806_still_uses_classic_scheme():
    """The SAME install's Logic Module capture emitted 13008B/53008B —
    the classic pc_logic scheme's slot-2 pair — proving the two
    module families use different derivations (not a firmware
    generation split)."""

    physicals = derive_pc_logic_input_physicals("8806", 6)
    assert physicals[1] == "644032"

    c = convert_nikobus_address("644032")
    n = int(c[0], 16)
    assert {f"{n:X}{c[1:]}", f"{(n + 4) & 0xF:X}{c[1:]}"} == {
        "13008B", "53008B",
    }


def test_synthesis_purges_pre_0_34_interface_entries(tmp_path):
    """Stores written before 0.34.0 hold interface inputs at
    pc_logic-formula addresses (602A41.. for module 0548). Re-running
    synthesis must drop them so the corrected entries replace them
    instead of coexisting as dead devices — while leaving real wall
    buttons and correctly-addressed pc_logic entries untouched."""

    discovery = _make_discovery(tmp_path)
    buttons_store = discovery._button_data["nikobus_button"]
    # Stale pre-0.34.0 synthesized interface entry (issue #485's store):
    buttons_store["602A41"] = {
        "description": "Modular Interface Input #N602A41",
        "pc_logic_parent_address": "0548",
        "pc_logic_parent_type": "interface_module",
        "pc_logic_slot_index": 1,
    }
    # Correct pc_logic entry for another module — must survive:
    buttons_store["644031"] = {
        "description": "PC-Logic Logical Input #N644031",
        "pc_logic_parent_address": "8806",
        "pc_logic_parent_type": "pc_logic",
        "pc_logic_slot_index": 1,
    }
    # Real wall button — no provenance, must survive:
    buttons_store["1843B4"] = {
        "description": "Bus push button, 4 control buttons #N1843B4",
    }

    discovery.discovered_devices = {
        "0548": {
            "category": "Module",
            "module_type": "interface_module",
            "model": "05-206",
            "address": "0548",
            "channels": 6,
            "channels_count": 6,
        },
        "8806": {
            "category": "Module",
            "module_type": "pc_logic",
            "model": "05-201",
            "address": "8806",
            "channels": 6,
            "channels_count": 6,
        },
    }
    discovery._synthesize_pc_logic_inputs()

    assert "602A41" not in buttons_store          # stale scheme purged
    assert "644031" in buttons_store              # correct pc_logic kept
    assert "1843B4" in buttons_store              # real button kept
    # Corrected interface physicals synthesized for the merge to land:
    assert "180549" in discovery.discovered_devices
    assert "18054A" in discovery.discovered_devices
