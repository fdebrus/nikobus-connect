"""Tests for the caller-owned button-storage adapter.

These tests verify that ``NikobusDiscovery`` treats the caller-supplied
``button_data`` dict as its live store: it mutates the dict in place, calls
``on_button_save`` after merging, and never touches the legacy
``nikobus_button_config.json`` file.
"""

from __future__ import annotations

import asyncio
import builtins
from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.fileio import (
    merge_discovered_buttons,
    merge_linked_modules,
)
from nikobus_connect.discovery.mapping import KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address


def _drop_coro(coro):
    """Close a coroutine so it doesn't warn during tests."""
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


def test_merge_discovered_buttons_populates_dict_in_place():
    button_data: dict = {"nikobus_button": {}}
    # 4-channel button discovered at address "0083C6".
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
    assert isinstance(buttons, dict)
    # A 4-channel button creates one entry per operation point (4 entries).
    assert len(buttons) == 4
    for addr, entry in buttons.items():
        assert entry["address"] == addr
        assert "linked_button" in entry
        linked = entry["linked_button"]
        assert len(linked) == 1
        assert linked[0]["address"] == "0083C6"
        assert linked[0]["channels"] == 4
        # No legacy `impacted_module` field on the new shape.
        assert "impacted_module" not in entry


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
    first_snapshot = {k: dict(v) for k, v in button_data["nikobus_button"].items()}
    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )
    assert button_data["nikobus_button"].keys() == first_snapshot.keys()
    for addr, entry in button_data["nikobus_button"].items():
        assert len(entry["linked_button"]) == 1


def test_merge_linked_modules_dedupes_on_dict_store():
    button_data = {
        "nikobus_button": {
            "AB12": {
                "description": "Test",
                "address": "AB12",
                "linked_button": [
                    {
                        "type": "Button",
                        "model": "05-346",
                        "address": "AB12",
                        "channels": 4,
                        "key": "1A",
                    }
                ],
            }
        }
    }
    mapping = {
        ("AB12", 1, None): [
            {
                "module_address": "00F5",
                "channel": 1,
                "mode": "M01",
                "t1": "0s",
                "t2": "0s",
                "payload": "aa",
                "button_address": "AB12",
            }
        ]
    }

    updated, links_added, outputs_added = merge_linked_modules(button_data, mapping)
    assert updated == 1
    assert links_added == 1
    assert outputs_added == 1

    # Second call with the same mapping is a no-op (deduped).
    updated, links_added, outputs_added = merge_linked_modules(button_data, mapping)
    assert (updated, links_added, outputs_added) == (0, 0, 0)

    button = button_data["nikobus_button"]["AB12"]
    assert button["linked_modules"][0]["module_address"] == "00F5"
    assert len(button["linked_modules"][0]["outputs"]) == 1


def test_finalize_inventory_phase_mutates_button_data_and_calls_save(
    tmp_path, monkeypatch
):
    """End-to-end: run _finalize_inventory_phase and verify the adapter wins.

    - ``button_data`` is mutated in place with the discovered button.
    - ``on_button_save`` is awaited at least once.
    - No call to ``open`` references the legacy button config file.
    """

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

    # Seed discovered_devices with a 4-channel button.
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

    # button_data dict mutated in place.
    assert len(button_data["nikobus_button"]) == 4
    for addr, entry in button_data["nikobus_button"].items():
        assert entry["linked_button"][0]["address"] == "0083C6"

    # Save callback awaited.
    assert on_button_save.await_count > 0

    # The library never touched the legacy button config.
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

    # Module config IO still runs; button config never opened.
    assert not any("nikobus_button_config.json" in p for p in opened_paths)
