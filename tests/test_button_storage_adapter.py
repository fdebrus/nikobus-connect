"""Tests for the caller-owned button-storage adapter.

These tests verify that ``NikobusDiscovery`` treats the caller-supplied
``button_data`` dict as its live store: it mutates the dict in place, calls
``on_button_save`` after merging, and never touches the legacy
``nikobus_button_config.json`` file.

The storage shape is Option A (physical-button keyed, operation points
nested under each physical entry). See ``merge_discovered_buttons`` docstring
for the canonical layout.
"""

from __future__ import annotations

import asyncio
import builtins
from unittest.mock import AsyncMock, MagicMock

from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.fileio import (
    merge_discovered_buttons,
    merge_linked_modules,
)
from nikobus_connect.discovery.mapping import KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    task = MagicMock()
    task.cancel = MagicMock()
    return task


def _make_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.dict_module_data = {}
    coordinator.discovery_running = False
    coordinator.discovery_module = False
    coordinator.discovery_module_address = None
    coordinator.inventory_query_type = None
    return coordinator


def test_merge_discovered_buttons_produces_physical_keyed_shape():
    """A 4-channel physical button = one top-level entry, 4 op-points."""

    button_data: dict = {"nikobus_button": {}}
    discovered = {
        "0083C6": {
            "category": "Button",
            "description": "Button with 4 Operation Points",
            "model": "05-346",
            "channels": 4,
            "address": "0083C6",
        }
    }

    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )

    buttons = button_data["nikobus_button"]
    assert set(buttons.keys()) == {"0083C6"}

    entry = buttons["0083C6"]
    assert entry["type"] == "Button with 4 Operation Points"
    assert entry["model"] == "05-346"
    assert entry["channels"] == 4
    assert set(entry["operation_points"].keys()) == {"1A", "1B", "1C", "1D"}
    for key_label, op_point in entry["operation_points"].items():
        assert "bus_address" in op_point
        assert len(op_point["bus_address"]) == 6
    # Bus addresses are distinct per operation point.
    bus_addrs = {op["bus_address"] for op in entry["operation_points"].values()}
    assert len(bus_addrs) == 4


def test_merge_discovered_buttons_generates_unique_descriptions():
    """Physical-button description = "{type} #N{physical_addr}"; op-point
    description = "Push button {key} #N{bus_addr}". Every description in
    the store must be globally unique so HA entities don't collide.
    """

    button_data: dict = {"nikobus_button": {}}
    discovered = {
        "0083C6": {
            "category": "Button",
            "description": "Button with 4 Operation Points",
            "model": "05-346",
            "channels": 4,
            "address": "0083C6",
        },
        "17C554": {
            "category": "Button",
            "description": "Button with 4 Operation Points",
            "model": "05-346",
            "channels": 4,
            "address": "17C554",
        },
    }

    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )

    buttons = button_data["nikobus_button"]
    assert buttons["0083C6"]["description"] == "Button with 4 Operation Points #N0083C6"
    assert buttons["17C554"]["description"] == "Button with 4 Operation Points #N17C554"

    # Op-point descriptions follow "Push button {key} #N{bus_addr}".
    for physical_addr, entry in buttons.items():
        for key_label, op_point in entry["operation_points"].items():
            bus_addr = op_point["bus_address"]
            assert (
                op_point["description"]
                == f"Push button {key_label} #N{bus_addr}"
            )

    # Collect every description string — must be globally unique.
    all_descs = [entry["description"] for entry in buttons.values()] + [
        op["description"]
        for entry in buttons.values()
        for op in entry["operation_points"].values()
    ]
    assert len(all_descs) == len(set(all_descs))


def test_merge_discovered_buttons_preserves_user_renamed_descriptions():
    """Custom descriptions survive a re-discovery; auto ones get refreshed."""

    button_data: dict = {"nikobus_button": {}}
    discovered = {
        "0083C6": {
            "category": "Button",
            "description": "Button with 4 Operation Points",
            "model": "05-346",
            "channels": 4,
            "address": "0083C6",
        }
    }
    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )

    # User renames physical button and one op-point in the HA UI.
    button_data["nikobus_button"]["0083C6"]["description"] = "Living room 4-key"
    first_key = next(iter(button_data["nikobus_button"]["0083C6"]["operation_points"]))
    button_data["nikobus_button"]["0083C6"]["operation_points"][first_key][
        "description"
    ] = "Living room ceiling light"

    # Re-discovery must not clobber the renames.
    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )

    assert (
        button_data["nikobus_button"]["0083C6"]["description"] == "Living room 4-key"
    )
    assert (
        button_data["nikobus_button"]["0083C6"]["operation_points"][first_key][
            "description"
        ]
        == "Living room ceiling light"
    )


def test_merge_discovered_buttons_is_idempotent():
    button_data: dict = {"nikobus_button": {}}
    discovered = {
        "0083C6": {
            "category": "Button",
            "description": "Button with 4 Operation Points",
            "model": "05-346",
            "channels": 4,
            "address": "0083C6",
        }
    }
    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )
    first = {
        "0083C6": dict(button_data["nikobus_button"]["0083C6"]["operation_points"])
    }
    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )
    second = dict(button_data["nikobus_button"]["0083C6"]["operation_points"])
    assert set(second.keys()) == set(first["0083C6"].keys())
    for key_label in second:
        assert second[key_label]["bus_address"] == first["0083C6"][key_label]["bus_address"]


def test_merge_linked_modules_routes_by_bus_address_to_op_point():
    """A command_mapping entry keyed by bus address lands in the right op-point."""

    button_data = {
        "nikobus_button": {
            "182F18": {
                "type": "Button with 4 Operation Points",
                "model": "05-346",
                "channels": 4,
                "description": "Test",
                "operation_points": {
                    "1A": {"bus_address": "863D06"},
                    "1B": {"bus_address": "C63D06"},
                    "1C": {"bus_address": "063D06"},
                    "1D": {"bus_address": "463D06"},
                },
            }
        }
    }

    # Press emitted on bus address 863D06 (= physical 182F18 key 1A).
    mapping = {
        ("863D06", 1, None): [
            {
                "module_address": "C9A5",
                "channel": 4,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "FF13F060BC60",
                "button_address": "182F18",
            }
        ]
    }

    updated, links_added, outputs_added = merge_linked_modules(button_data, mapping)
    assert (updated, links_added, outputs_added) == (1, 1, 1)

    # Second identical call is a no-op.
    updated, links_added, outputs_added = merge_linked_modules(button_data, mapping)
    assert (updated, links_added, outputs_added) == (0, 0, 0)

    # Link landed under the correct operation point.
    op_1a = button_data["nikobus_button"]["182F18"]["operation_points"]["1A"]
    assert op_1a["linked_modules"][0]["module_address"] == "C9A5"
    assert op_1a["linked_modules"][0]["outputs"][0]["channel"] == 4
    # And NOT under the sibling keys.
    for other in ("1B", "1C", "1D"):
        assert "linked_modules" not in button_data["nikobus_button"]["182F18"][
            "operation_points"
        ][other]


def test_finalize_inventory_phase_mutates_button_data_and_calls_save(
    tmp_path, monkeypatch
):
    """End-to-end: finalize inventory, adapter wins, legacy file untouched."""

    button_data: dict = {"nikobus_button": {}}
    on_button_save = AsyncMock()

    coordinator = _make_coordinator()
    discovery = NikobusDiscovery(
        coordinator,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data=button_data,
        on_button_save=on_button_save,
    )

    discovery.discovered_devices = {
        "0083C6": {
            "category": "Button",
            "description": "Button with 4 Operation Points",
            "model": "05-346",
            "channels": 4,
            "address": "0083C6",
            "device_type": "06",
        }
    }
    discovery.discovery_stage = "inventory_identity"

    opened_paths: list[str] = []
    real_open = builtins.open

    def tracking_open(path, *args, **kwargs):
        opened_paths.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracking_open)

    asyncio.run(discovery._finalize_inventory_phase())

    buttons = button_data["nikobus_button"]
    assert set(buttons.keys()) == {"0083C6"}
    assert set(buttons["0083C6"]["operation_points"].keys()) == {
        "1A",
        "1B",
        "1C",
        "1D",
    }
    assert on_button_save.await_count > 0
    assert not any("nikobus_button_config.json" in p for p in opened_paths), (
        f"library opened legacy button config: {opened_paths}"
    )


def test_discovery_without_button_data_skips_button_merge(tmp_path, monkeypatch):
    """When button_data is omitted the library does no button persistence."""

    coordinator = _make_coordinator()
    discovery = NikobusDiscovery(
        coordinator,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
    )
    discovery.discovered_devices = {
        "0083C6": {
            "category": "Button",
            "description": "Button with 4 Operation Points",
            "model": "05-346",
            "channels": 4,
            "address": "0083C6",
            "device_type": "06",
        }
    }
    discovery.discovery_stage = "inventory_identity"

    opened_paths: list[str] = []
    real_open = builtins.open

    def tracking_open(path, *args, **kwargs):
        opened_paths.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracking_open)

    asyncio.run(discovery._finalize_inventory_phase())

    assert not any("nikobus_button_config.json" in p for p in opened_paths)
