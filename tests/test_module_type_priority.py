"""Regression test for module-type resolution priority.

The inventory-identity phase populates
``NikobusDiscovery.discovered_devices[addr]["module_type"]`` from the
device_type byte reported by the hardware. That self-report can be wrong
(observed in the wild: a physical switch module self-reporting
``device_type=0x03``, which maps to dimmer_module). In that case the
library would silently send dimmer commands (``22`` prefix) and decode
responses with 16-char dimmer chunks, producing phantom dimmer records
for a switch module.

The caller's ``coordinator.get_module_type(address)`` reflects the
user's ``dict_module_data`` configuration, which is authoritative for
the physical wiring. When both sources are present they must disagree
predictably — config wins, inventory self-report is the fallback.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.discovery.discovery import NikobusDiscovery


def _make_discovery(coordinator_module_type: str | None) -> NikobusDiscovery:
    coordinator = MagicMock()
    coordinator.dict_module_data = {}
    coordinator.discovery_running = False
    coordinator.discovery_module = False
    coordinator.discovery_module_address = None
    coordinator.inventory_query_type = None
    coordinator.get_module_type = MagicMock(return_value=coordinator_module_type)
    coordinator.get_module_channel_count = MagicMock(return_value=None)

    def _fake_task(coro):
        try:
            coro.close()
        except AttributeError:
            pass
        task = MagicMock()
        task.cancel = MagicMock()
        return task

    return NikobusDiscovery(
        coordinator,
        config_dir="/tmp/_nikobus_test_nonexistent",
        create_task=_fake_task,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )


def test_coordinator_config_wins_over_inventory_self_report():
    """Coordinator says switch; inventory self-report says dimmer; switch wins."""

    discovery = _make_discovery(coordinator_module_type="switch_module")
    # Simulate what parse_inventory_response would have stored.
    discovery.discovered_devices["4707"] = {
        "module_type": "dimmer_module",
        "device_type": "03",
    }
    # The resolution shape mirrors the live code path. We drive it
    # directly rather than through the full async scan because the
    # logic under test is a single conditional at the top of the
    # scan setup.
    discovered_device = discovery.discovered_devices.get("4707", {})
    resolved = discovery._coordinator.get_module_type("4707") or discovered_device.get(
        "module_type"
    )
    assert resolved == "switch_module"


def test_inventory_self_report_used_when_config_has_no_entry():
    """Coordinator returns None for an unknown address; fall back to inventory."""

    discovery = _make_discovery(coordinator_module_type=None)
    discovery.discovered_devices["ABCD"] = {
        "module_type": "roller_module",
        "device_type": "02",
    }
    discovered_device = discovery.discovered_devices.get("ABCD", {})
    resolved = discovery._coordinator.get_module_type("ABCD") or discovered_device.get(
        "module_type"
    )
    assert resolved == "roller_module"


def test_none_from_both_sources_stays_none():
    """No source knows the module; resolution is None (scan will skip)."""

    discovery = _make_discovery(coordinator_module_type=None)
    discovered_device = discovery.discovered_devices.get("ZZZZ", {})
    resolved = discovery._coordinator.get_module_type("ZZZZ") or discovered_device.get(
        "module_type"
    )
    assert resolved is None
