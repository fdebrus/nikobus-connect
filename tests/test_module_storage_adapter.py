"""Tests for the caller-owned module store adapter.

Parallel to the button adapter: the library mutates a caller-supplied
dict in place and awaits an ``on_module_save`` callback instead of
writing ``nikobus_module_config.json`` to disk.

Shape (Option 2 — flat dict keyed by physical address)::

    {"nikobus_module": {
        "<address>": {
            "module_type": ...,
            "description": ...,
            "model": ...,
            "channels": [...],
            "discovered_info": {...},
        }
    }}

User-owned fields on each channel (``entity_type``, ``led_on``/``led_off``,
``operation_time_up``/``operation_time_down``, per-channel
``description``) must survive re-discovery unchanged.
"""

from __future__ import annotations

import asyncio
import builtins
from unittest.mock import AsyncMock, MagicMock

from nikobus_connect.discovery import find_module
from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.fileio import merge_discovered_modules


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


def _switch_device(address: str = "C9A5", channels: int = 12) -> dict:
    return {
        "address": address,
        "category": "Module",
        "description": "Switching module",
        "discovered_name": "Switching module",
        "device_type": "01",
        "model": "05-000-02",
        "module_type": "switch_module",
        "channels": channels,
        "channels_count": channels,
    }


def _roller_device(address: str = "9105", channels: int = 6) -> dict:
    return {
        "address": address,
        "category": "Module",
        "description": "Roller shutter module",
        "discovered_name": "Roller shutter module",
        "device_type": "02",
        "model": "05-001-02",
        "module_type": "roller_module",
        "channels": channels,
        "channels_count": channels,
    }


# --- merge semantics ----------------------------------------------------


def test_new_module_gets_auto_description_and_default_channels():
    store: dict = {"nikobus_module": {}}
    discovered = {"C9A5": _switch_device("C9A5", 12)}

    added, updated = merge_discovered_modules(store, discovered)
    assert (added, updated) == (1, 0)

    entry = store["nikobus_module"]["C9A5"]
    assert entry["module_type"] == "switch_module"
    assert entry["description"].startswith("switch_module_s")
    assert entry["model"] == "05-000-02"
    assert len(entry["channels"]) == 12
    # Defaults for an untouched switch_module channel.
    assert entry["channels"][0] == {"description": "not_in_use output_1"}
    # Roller defaults include operation_time_up.
    assert "operation_time_up" not in entry["channels"][0]
    assert entry["discovered_info"] == {
        "name": "Switching module",
        "device_type": "01",
        "channels_count": 12,
    }


def test_roller_module_defaults_include_operation_time_up():
    store: dict = {"nikobus_module": {}}
    discovered = {"9105": _roller_device("9105", 6)}

    merge_discovered_modules(store, discovered)

    entry = store["nikobus_module"]["9105"]
    for ch in entry["channels"]:
        assert ch == {
            "description": f"not_in_use output_{entry['channels'].index(ch) + 1}",
            "operation_time_up": "30",
        }


def test_user_fields_preserved_on_rediscovery():
    """Re-running discovery must NOT clobber any user customization:

    - per-channel ``description`` (renamed from the auto default)
    - ``entity_type`` / ``led_on`` / ``led_off``
    - roller ``operation_time_up`` / ``operation_time_down``
    - module-level ``description`` (e.g. "My switch rack")
    """

    store: dict = {
        "nikobus_module": {
            "C9A5": {
                "module_type": "switch_module",
                "description": "Living room lights",  # user-renamed
                "model": "05-000-02",
                "channels": [
                    {
                        "description": "Salon Appliques",
                        "entity_type": "light",
                    },
                    {"description": "Hall RDC", "led_on": "352A02",
                     "led_off": "352A02", "entity_type": "light"},
                ] + [{"description": f"not_in_use output_{i}"} for i in range(3, 13)],
                "discovered_info": {
                    "name": "Switching module",
                    "device_type": "01",
                    "channels_count": 12,
                },
            }
        }
    }
    before = {
        "module_desc": store["nikobus_module"]["C9A5"]["description"],
        "ch0": dict(store["nikobus_module"]["C9A5"]["channels"][0]),
        "ch1": dict(store["nikobus_module"]["C9A5"]["channels"][1]),
    }

    merge_discovered_modules(store, {"C9A5": _switch_device("C9A5", 12)})

    entry = store["nikobus_module"]["C9A5"]
    assert entry["description"] == before["module_desc"]
    assert entry["channels"][0] == before["ch0"]
    assert entry["channels"][1] == before["ch1"]
    # Discovery-owned fields got refreshed.
    assert entry["discovered_info"]["channels_count"] == 12
    assert entry["model"] == "05-000-02"


def test_roller_user_timing_preserved():
    store: dict = {
        "nikobus_module": {
            "9105": {
                "module_type": "roller_module",
                "description": "Ground floor shutters",
                "model": "05-001-02",
                "channels": [
                    {"description": "Salon Volet Terrasse",
                     "operation_time_up": "45",
                     "operation_time_down": "43"},
                ] + [{"description": f"not_in_use output_{i}",
                      "operation_time_up": "30"} for i in range(2, 7)],
                "discovered_info": {
                    "name": "Roller shutter module",
                    "device_type": "02",
                    "channels_count": 6,
                },
            }
        }
    }

    merge_discovered_modules(store, {"9105": _roller_device("9105", 6)})

    entry = store["nikobus_module"]["9105"]
    assert entry["channels"][0]["operation_time_up"] == "45"
    assert entry["channels"][0]["operation_time_down"] == "43"
    assert entry["channels"][0]["description"] == "Salon Volet Terrasse"


def test_model_refreshes_when_hardware_reports_different():
    """If the module now self-reports a different model than stored,
    refresh — user shouldn't be stuck with stale hardware metadata."""

    store = {
        "nikobus_module": {
            "C9A5": {
                "module_type": "switch_module",
                "description": "switch_module_s1",
                "model": "",  # blank
                "channels": [{"description": f"c{i}"} for i in range(12)],
                "discovered_info": {},
            }
        }
    }
    merge_discovered_modules(store, {"C9A5": _switch_device("C9A5", 12)})
    assert store["nikobus_module"]["C9A5"]["model"] == "05-000-02"


def test_non_module_devices_skipped():
    """Buttons and unknown-category devices in discovered_devices must
    not pollute the module store."""

    store: dict = {"nikobus_module": {}}
    discovered = {
        "1843B4": {
            "address": "1843B4",
            "category": "Button",
            "description": "Bus push button, 4 control buttons",
            "model": "05-346",
        },
        "C9A5": _switch_device("C9A5", 12),
    }

    added, updated = merge_discovered_modules(store, discovered)
    assert (added, updated) == (1, 0)
    assert set(store["nikobus_module"].keys()) == {"C9A5"}


def test_auto_description_is_unique_per_module_type():
    store: dict = {"nikobus_module": {}}
    discovered = {
        "C9A5": _switch_device("C9A5"),
        "4707": _switch_device("4707"),
        "5B05": _switch_device("5B05"),
    }
    merge_discovered_modules(store, discovered)

    descriptions = {
        e["description"] for e in store["nikobus_module"].values()
    }
    assert descriptions == {
        "switch_module_s1",
        "switch_module_s2",
        "switch_module_s3",
    }


# --- find_module --------------------------------------------------------


def test_find_module_returns_normalized_tuple():
    store: dict = {"nikobus_module": {}}
    merge_discovered_modules(store, {"C9A5": _switch_device("C9A5")})

    hit = find_module(store, "c9a5")
    assert hit is not None
    addr, entry = hit
    assert addr == "C9A5"
    assert entry["module_type"] == "switch_module"


def test_find_module_returns_none_on_unknown_address():
    store: dict = {"nikobus_module": {}}
    assert find_module(store, "DEADBE") is None
    assert find_module({}, "C9A5") is None
    assert find_module(None, "C9A5") is None  # type: ignore[arg-type]


# --- NikobusDiscovery integration --------------------------------------


def test_finalize_inventory_phase_mutates_module_data_and_calls_save(
    tmp_path, monkeypatch
):
    module_data: dict = {"nikobus_module": {}}
    on_module_save = AsyncMock()

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
        module_data=module_data,
        on_module_save=on_module_save,
    )
    discovery.discovered_devices = {
        "C9A5": _switch_device("C9A5", 12),
    }
    discovery.discovery_stage = "inventory_identity"

    opened: list[str] = []
    real_open = builtins.open

    def tracking_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracking_open)

    asyncio.run(discovery._finalize_inventory_phase())

    # Module store mutated in place.
    assert "C9A5" in module_data["nikobus_module"]
    assert module_data["nikobus_module"]["C9A5"]["module_type"] == "switch_module"
    # Save callback awaited.
    assert on_module_save.await_count > 0
    # Legacy nikobus_module_config.json must never be touched.
    assert not any("nikobus_module_config.json" in p for p in opened), (
        f"library opened legacy module config: {opened}"
    )


def test_discovery_without_module_data_skips_module_persistence(
    tmp_path, monkeypatch
):
    """Omitting module_data + on_module_save leaves the discovery silent
    on modules — integration might only want button persistence."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
    )
    discovery.discovered_devices = {
        "C9A5": _switch_device("C9A5", 12),
    }
    discovery.discovery_stage = "inventory_identity"

    opened: list[str] = []
    real_open = builtins.open

    def tracking_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracking_open)

    asyncio.run(discovery._finalize_inventory_phase())

    # No module file written; no exception raised.
    assert not any("nikobus_module_config.json" in p for p in opened)
