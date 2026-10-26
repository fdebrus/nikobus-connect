"""A key stored by its bus address alone receives its module links.

An install without a PC-Link writes ``nikobus_button_config.json`` from
the keys it can press: each key's bus address, often without the
button's own address, which the module link records name. Those records
used to stay unmatched. The merge now also files a record by the frame
the module listens for — computed from the button address and key index
in the record — which is exactly what a key-only entry is stored under.

Real data: 8-key button BP9 (106218) on a 2026-10 install, its eight key
addresses as the bus shows them, and the records its modules hold (first
row on the sibling address 106219).
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import merge_linked_modules
from nikobus_connect.discovery.protocol import record_bus_address, virtual_record_bus_address

BP9_KEYS = {
    "1A": "A61182", "1B": "E61182", "1C": "261182", "1D": "661182",
    "2A": "861182", "2B": "C61182", "2C": "061182", "2D": "461182",
}
#: (record address, key index) -> (module, channel), as decoded on that install.
BP9_RECORDS = {
    ("106219", 1): ("C7C1", 5),   # 1A
    ("106219", 3): ("C7C1", 6),   # 1B
    ("106219", 0): ("C7C1", 7),   # 1C
    ("106219", 2): ("C95D", 3),   # 1D
    ("106218", 1): ("C95D", 2),   # 2A
    ("106218", 3): ("C7C1", 4),   # 2B
    ("106218", 0): ("C7C1", 11),  # 2C
    ("106218", 2): ("C9D7", 11),  # 2D
}


def _key_only_store(addresses) -> dict:
    """What the manual-config loader makes of entries with address + description only."""
    return {
        "nikobus_button": {
            bus: {
                "type": "Manual button", "channels": 1, "description": f"Taste {bus}",
                "operation_points": {"1A": {"bus_address": bus, "linked_modules": []}},
            }
            for bus in addresses
        }
    }


def _mapping(records) -> dict:
    return {
        (addr, key, None): [{
            "module_address": module, "channel": channel, "mode": "M05 (Impulse)",
            "button_address": addr, "record_source": "output_module_table",
        }]
        for (addr, key), (module, channel) in records.items()
    }


def _links(store, bus) -> set[tuple[str, int]]:
    op = store["nikobus_button"][bus]["operation_points"]["1A"]
    return {(b["module_address"], o["channel"]) for b in op["linked_modules"] for o in b["outputs"]}


def test_the_frame_of_each_bp9_record_is_its_keys_bus_address() -> None:
    labels = {1: "1A", 3: "1B", 0: "1C", 2: "1D"}
    for (addr, key), _ in BP9_RECORDS.items():
        expected = BP9_KEYS[labels[key]] if addr == "106219" else BP9_KEYS["2" + labels[key][1]]
        assert record_bus_address(addr, key) == expected, (addr, key)
    assert virtual_record_bus_address is record_bus_address


def test_key_only_entries_receive_every_link() -> None:
    store = _key_only_store(BP9_KEYS.values())
    _updated, _links_added, outputs, unmatched = merge_linked_modules(store, _mapping(BP9_RECORDS))
    assert unmatched == set()
    assert outputs == 8
    assert _links(store, BP9_KEYS["1A"]) == {("C7C1", 5)}
    assert _links(store, BP9_KEYS["1D"]) == {("C95D", 3)}
    assert _links(store, BP9_KEYS["2C"]) == {("C7C1", 11)}
    assert _links(store, BP9_KEYS["2D"]) == {("C9D7", 11)}


def test_a_key_not_in_the_store_stays_unmatched() -> None:
    store = _key_only_store([BP9_KEYS["1A"]])
    *_rest, unmatched = merge_linked_modules(store, _mapping(BP9_RECORDS))
    assert "106218" in unmatched and "106219" in unmatched
    assert _links(store, BP9_KEYS["1A"]) == {("C7C1", 5)}


def test_a_full_button_entry_still_wins_by_its_own_address() -> None:
    plate = {
        "type": "Push button", "channels": 8,
        "operation_points": {label: {"bus_address": bus, "linked_modules": []} for label, bus in BP9_KEYS.items()},
    }
    store = {"nikobus_button": {"106218": plate}}
    *_rest, unmatched = merge_linked_modules(store, _mapping(BP9_RECORDS))
    assert unmatched == set()
    ops = store["nikobus_button"]["106218"]["operation_points"]
    assert {(b["module_address"], o["channel"]) for b in ops["2D"]["linked_modules"] for o in b["outputs"]} == {("C9D7", 11)}
    assert {(b["module_address"], o["channel"]) for b in ops["1A"]["linked_modules"] for o in b["outputs"]} == {("C7C1", 5)}
