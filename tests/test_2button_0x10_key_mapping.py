"""Regression tests for the 0x10 (4*-082, 2-button) key mapping.

The 0x10 2-button shares ``channels=2`` with the 05-060 (0x04) and the
05-056 (0x21) interface, but emits its two faces at first-nibble offsets
+0 / +4 (key indices 0 / 2) — the same slots as a 4-button's 1C/1D — NOT
the +8 / +C (key indices 1 / 3) the other 2-channel devices use.

Captured from a real install (fdebrus, 10 units): physical 130078 →
faces 078032 (nibble 0) and 478032 (nibble 4). Before the fix, 0x10
op-points were generated at the wrong addresses (8/C) and its decoded
link records (at key indices 0/2) never resolved, so all 10 two-buttons
showed zero linked_modules.

The fix must NOT disturb the 05-060 / 05-056 2-channel devices, which
legitimately use keys 1/3 → nibbles 8/C.
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import merge_discovered_buttons
from nikobus_connect.discovery.mapping import (
    DEVICE_TYPE_KEY_MAPPING,
    KEY_MAPPING,
    KEY_MAPPING_MODULE,
)
from nikobus_connect.discovery.protocol import (
    convert_nikobus_address,
    get_push_button_address,
)


# --- mapping tables --------------------------------------------------------

def test_device_type_override_table_has_0x10() -> None:
    assert DEVICE_TYPE_KEY_MAPPING["10"] == {"1A": "0", "1B": "4"}


def test_key_mapping_module_2_covers_both_key_families() -> None:
    """Resolver must handle both the 8/C (keys 1/3) and 0/4 (keys 0/2)
    2-channel families — a physical device only emits its own two."""
    assert KEY_MAPPING_MODULE[2] == {0: "0", 1: "8", 2: "4", 3: "C"}


# --- op-point generation ---------------------------------------------------

def test_0x10_op_points_use_nibble_0_and_4() -> None:
    """0x10 op-points must land at nibbles 0/4 (matching the bus), not
    the default 2-channel 8/C."""
    button_data = {"nikobus_button": {}}
    discovered = {
        "130078": {
            "category": "Button",
            "device_type": "10",
            "model": "4*-082",
            "channels": 2,
            "description": "Bus push button, 2 control buttons",
        }
    }
    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )
    ops = button_data["nikobus_button"]["130078"]["operation_points"]
    assert ops["1A"]["bus_address"] == "078032"  # nibble 0
    assert ops["1B"]["bus_address"] == "478032"  # nibble 4


def test_0x04_2button_still_uses_nibble_8_and_C() -> None:
    """The 05-060 (0x04) 2-button must keep the default 8/C layout."""
    button_data = {"nikobus_button": {}}
    discovered = {
        "0A0908": {
            "category": "Button",
            "device_type": "04",
            "model": "05-060",
            "channels": 2,
            "description": "Bus push button, 2 control buttons",
        }
    }
    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )
    ops = button_data["nikobus_button"]["0A0908"]["operation_points"]
    # convert_nikobus_address("0A0908") → first nibble + 8 / + C.
    conv = convert_nikobus_address("0A0908")
    base = int(conv[0], 16)
    assert ops["1A"]["bus_address"][0] == f"{(base + 0x8) & 0xF:X}"
    assert ops["1B"]["bus_address"][0] == f"{(base + 0xC) & 0xF:X}"


# --- link resolution -------------------------------------------------------

def test_0x10_link_keys_0_and_2_resolve() -> None:
    """Decoded 0x10 link records arrive at key indices 0 and 2; they
    must resolve to the same addresses as the 1A/1B op-points."""
    addr0, _ = get_push_button_address(0, "130078", lambda a: 2, convert_nikobus_address)
    addr2, _ = get_push_button_address(2, "130078", lambda a: 2, convert_nikobus_address)
    assert addr0 == "078032"
    assert addr2 == "478032"


def test_interface_link_keys_1_and_3_still_resolve() -> None:
    """The 05-056 interface emits keys 1/3 → nibbles 8/C; must be
    unaffected by the expanded 2-channel resolver map."""
    addr1, _ = get_push_button_address(1, "128ABE", lambda a: 2, convert_nikobus_address)
    addr3, _ = get_push_button_address(3, "128ABE", lambda a: 2, convert_nikobus_address)
    conv = convert_nikobus_address("128ABE")
    base = int(conv[0], 16)
    assert addr1[0] == f"{(base + 0x8) & 0xF:X}"
    assert addr3[0] == f"{(base + 0xC) & 0xF:X}"
