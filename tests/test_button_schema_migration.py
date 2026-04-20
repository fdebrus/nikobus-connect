"""Tests for v1 (bus-keyed) -> v2 (physical-keyed) button store migration."""

from __future__ import annotations

from nikobus_connect.discovery.fileio import (
    find_operation_point,
    merge_linked_modules,
    migrate_button_data_v1_to_v2,
)


def _sample_v1_store() -> dict:
    """A realistic v1 snippet: one 4-op-point physical button spread across
    four bus-keyed entries, with one of them carrying a linked_modules block.
    """

    return {
        "nikobus_button": {
            "863D06": {
                "description": "Button with 4 Operation Points #N863D06",
                "address": "863D06",
                "linked_button": [
                    {
                        "type": "Button with 4 Operation Points",
                        "model": "05-346",
                        "address": "182F18",
                        "channels": 4,
                        "key": "1A",
                    }
                ],
                "linked_modules": [
                    {
                        "module_address": "C9A5",
                        "outputs": [
                            {
                                "channel": 4,
                                "mode": "M01 (On / off)",
                                "t1": None,
                                "t2": None,
                                "payload": "FF13F060BC60",
                                "button_address": "182F18",
                            }
                        ],
                    }
                ],
            },
            "C63D06": {
                "description": "Button with 4 Operation Points #NC63D06",
                "address": "C63D06",
                "linked_button": [
                    {
                        "type": "Button with 4 Operation Points",
                        "model": "05-346",
                        "address": "182F18",
                        "channels": 4,
                        "key": "1B",
                    }
                ],
            },
            "063D06": {
                "description": "Button with 4 Operation Points #N063D06",
                "address": "063D06",
                "linked_button": [
                    {
                        "type": "Button with 4 Operation Points",
                        "model": "05-346",
                        "address": "182F18",
                        "channels": 4,
                        "key": "1C",
                    }
                ],
            },
            "463D06": {
                "description": "Button with 4 Operation Points #N463D06",
                "address": "463D06",
                "linked_button": [
                    {
                        "type": "Button with 4 Operation Points",
                        "model": "05-346",
                        "address": "182F18",
                        "channels": 4,
                        "key": "1D",
                    }
                ],
            },
        }
    }


def test_migration_collapses_four_bus_entries_into_one_physical():
    store = _sample_v1_store()
    changed = migrate_button_data_v1_to_v2(store)
    assert changed is True

    buttons = store["nikobus_button"]
    # Four old top-level keys collapsed into one physical entry.
    assert set(buttons.keys()) == {"182F18"}

    phys = buttons["182F18"]
    assert phys["type"] == "Button with 4 Operation Points"
    assert phys["model"] == "05-346"
    assert phys["channels"] == 4

    op_points = phys["operation_points"]
    assert set(op_points.keys()) == {"1A", "1B", "1C", "1D"}
    assert op_points["1A"]["bus_address"] == "863D06"
    assert op_points["1B"]["bus_address"] == "C63D06"
    assert op_points["1C"]["bus_address"] == "063D06"
    assert op_points["1D"]["bus_address"] == "463D06"

    # linked_modules attached to the bus entry for key 1A end up under 1A.
    assert "linked_modules" in op_points["1A"]
    assert op_points["1A"]["linked_modules"][0]["module_address"] == "C9A5"
    # Siblings carry no linked_modules.
    for other in ("1B", "1C", "1D"):
        assert "linked_modules" not in op_points[other]


def test_migration_generates_new_format_descriptions():
    """After migration, descriptions follow the new generated format."""

    store = _sample_v1_store()
    migrate_button_data_v1_to_v2(store)

    phys = store["nikobus_button"]["182F18"]
    assert phys["description"] == "Button with 4 Operation Points #N182F18"

    op_points = phys["operation_points"]
    assert op_points["1A"]["description"] == "Push button 1A #N863D06"
    assert op_points["1B"]["description"] == "Push button 1B #NC63D06"
    assert op_points["1C"]["description"] == "Push button 1C #N063D06"
    assert op_points["1D"]["description"] == "Push button 1D #N463D06"


def test_migration_preserves_custom_v1_descriptions():
    """A v1 description that doesn't match the auto pattern survives migration."""

    store = {
        "nikobus_button": {
            "863D06": {
                "description": "Kitchen ceiling light",
                "address": "863D06",
                "linked_button": [
                    {
                        "type": "Button with 4 Operation Points",
                        "model": "05-346",
                        "address": "182F18",
                        "channels": 4,
                        "key": "1A",
                    }
                ],
            }
        }
    }
    migrate_button_data_v1_to_v2(store)

    op_1a = store["nikobus_button"]["182F18"]["operation_points"]["1A"]
    assert op_1a["description"] == "Kitchen ceiling light"


def test_migration_is_noop_on_v2_shape():
    store = {
        "nikobus_button": {
            "182F18": {
                "type": "Button",
                "model": "05-346",
                "channels": 4,
                "operation_points": {"1A": {"bus_address": "863D06"}},
            }
        }
    }
    snapshot = {k: dict(v) for k, v in store["nikobus_button"].items()}
    assert migrate_button_data_v1_to_v2(store) is False
    assert store["nikobus_button"].keys() == snapshot.keys()


def test_find_operation_point_by_bus_address():
    store = _sample_v1_store()
    migrate_button_data_v1_to_v2(store)

    hit = find_operation_point(store, "063D06")
    assert hit is not None
    physical, key_label, op_point = hit
    assert physical == "182F18"
    assert key_label == "1C"
    assert op_point["bus_address"] == "063D06"

    miss = find_operation_point(store, "DEADBE")
    assert miss is None


def test_merge_linked_modules_auto_migrates_v1_stores():
    """Old-shape storage upgrades on first merge call."""

    store = _sample_v1_store()
    mapping = {
        # Press on 063D06 (= physical 182F18 key 1C) should land under that
        # op-point after migration.
        ("063D06", 2, None): [
            {
                "module_address": "5B05",
                "channel": 2,
                "mode": "M03 (Off, with operation time)",
                "t1": "0s",
                "t2": None,
                "payload": "ab",
                "button_address": "182F18",
            }
        ]
    }

    updated, links_added, outputs_added = merge_linked_modules(store, mapping)
    assert (updated, links_added, outputs_added) == (1, 1, 1)

    buttons = store["nikobus_button"]
    # Migration happened: top-level key is physical, not bus.
    assert "182F18" in buttons
    assert "063D06" not in buttons

    op_1c = buttons["182F18"]["operation_points"]["1C"]
    assert op_1c["linked_modules"][0]["module_address"] == "5B05"
