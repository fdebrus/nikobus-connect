"""Discovery-audit regressions for the fileio merge layer.

1. Provenance upgrade on dedupe hit: a record first scanned via PC-Link /
   PC-Logic registry memory and later confirmed in the output module's
   own link table must be re-tagged ``output_module_table`` — leaving the
   registry tag would make the host's residue classifier flag a
   perfectly-active button as previous-owner residue.
2. Remote-transmitter collision guard: a clustered remote code resolving
   to an address already holding a REAL inventory button must not
   clobber it (channels forced to 1, its 1A op-point rewritten).
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import (
    merge_discovered_buttons,
    merge_linked_modules,
)
from nikobus_connect.discovery.mapping import KEY_MAPPING_MODULE
from nikobus_connect.discovery.protocol import convert_nikobus_address


def _button_store() -> dict:
    return {
        "nikobus_button": {
            "16766C": {
                "type": "Bus push button, 4 control buttons",
                "model": "05-346",
                "channels": 4,
                "description": "Bus push button, 4 control buttons #N16766C",
                "operation_points": {
                    "1A": {
                        "bus_address": "8D9B9A",
                        "description": "Push button 1A #N8D9B9A",
                    },
                },
            },
        }
    }


def _output(record_source: str | None) -> dict:
    out = {
        "module_address": "8110",
        "channel": 1,
        "mode": "M05 (Impulse)",
        "t1": None,
        "t2": None,
        "payload": "030500F0F0E5",
        "button_address": "16766C",
        "ir_button_address": None,
        "ir_code": None,
    }
    if record_source is not None:
        out["record_source"] = record_source
    return out


def _stored_outputs(button_data: dict) -> list[dict]:
    op = button_data["nikobus_button"]["16766C"]["operation_points"]["1A"]
    return op["linked_modules"][0]["outputs"]


def test_dedupe_hit_upgrades_registry_provenance_to_module_table():
    """Registry-first then module-table scan → stored tag upgraded."""
    button_data = _button_store()
    merge_linked_modules(
        button_data, {("8D9B9A", 0, None): [_output("pc_link_registry")]}
    )
    assert _stored_outputs(button_data)[0]["record_source"] == "pc_link_registry"

    merge_linked_modules(
        button_data, {("8D9B9A", 0, None): [_output("output_module_table")]}
    )
    outputs = _stored_outputs(button_data)
    assert len(outputs) == 1  # still deduped — no near-duplicate appended
    assert outputs[0]["record_source"] == "output_module_table"


def test_dedupe_hit_does_not_downgrade_module_table_provenance():
    """Module-table-first then registry scan → authoritative tag kept."""
    button_data = _button_store()
    merge_linked_modules(
        button_data, {("8D9B9A", 0, None): [_output("output_module_table")]}
    )
    merge_linked_modules(
        button_data, {("8D9B9A", 0, None): [_output("pc_link_registry")]}
    )
    outputs = _stored_outputs(button_data)
    assert len(outputs) == 1
    assert outputs[0]["record_source"] == "output_module_table"


def test_remote_code_collision_does_not_clobber_wall_button():
    """A clustered remote resolving onto a real button's address is
    skipped (with a warning) instead of forcing channels=1 and
    rewriting the button's 1A op-point."""
    button_data = _button_store()
    devices = {
        "16766C": {
            "category": "Button",
            "remote_transmitter_bus_address": "ABCDEF",
            "remote_transmitter_address": "16766",
            "remote_transmitter_suffix": "C",
            "description": "Remote page 3",
            "model": "05-312",
            "channels": 1,
        }
    }
    merge_discovered_buttons(
        button_data, devices, KEY_MAPPING_MODULE, convert_nikobus_address
    )

    phys = button_data["nikobus_button"]["16766C"]
    assert phys["channels"] == 4  # untouched
    assert phys["type"] == "Bus push button, 4 control buttons"
    assert "remote_transmitter_bus_address" not in phys
    # 1A op-point keeps its real bus address.
    assert phys["operation_points"]["1A"]["bus_address"] == "8D9B9A"


def test_remote_code_merge_still_works_on_fresh_address():
    """The guard must not break the normal remote-transmitter path."""
    button_data = {"nikobus_button": {}}
    devices = {
        "0FA9C0": {
            "category": "Button",
            "remote_transmitter_bus_address": "ABCDEF",
            "description": "",
            "model": "05-312",
            "channels": 1,
        }
    }
    merge_discovered_buttons(
        button_data, devices, KEY_MAPPING_MODULE, convert_nikobus_address
    )
    phys = button_data["nikobus_button"]["0FA9C0"]
    assert phys["type"] == "Remote Code"
    assert phys["operation_points"]["1A"]["bus_address"] == "ABCDEF"
