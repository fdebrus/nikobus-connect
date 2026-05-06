"""Bus-presence cross-check for stale inventory entries.

Background: a user with a second-hand PC-Link sees records from the
previous owner's installation in their inventory dump. Niko's PC
software writes new programming on top of old, but unused register
slots aren't auto-zeroed, so any module / button records the new
install doesn't overwrite stay present in PC-Link flash.

``detect_stale_inventory`` probes each output-bearing module address
on the live bus via ``$1012<addr>`` and classifies them as present /
absent. Buttons are flagged as orphaned when their entire
``linked_modules`` set sits inside the absent set.

Tests below pin the contract against a synthetic version of the
real-world second-hand-PC-Link install: switch module 8110 + compact
switch 1CEC are present (the user kept the physical hardware),
compact switch 3D28 is absent (previous owner's module), and the
26 buttons in the 3Bxx-3Exx range whose ``linked_modules`` point only
at 3D28 cascade-flag as orphaned.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.discovery.discovery import NikobusDiscovery


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    task = MagicMock()
    task.cancel = MagicMock()
    return task


def _make_coordinator(*, get_output_state) -> MagicMock:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    coord.get_module_channel_count = MagicMock(return_value=12)
    coord.nikobus_command = MagicMock()
    coord.nikobus_command.get_output_state = AsyncMock(
        side_effect=get_output_state
    )
    return coord


def _make_discovery(coord, tmp_path, *, button_data=None) -> NikobusDiscovery:
    return NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data=button_data if button_data is not None else {"nikobus_button": {}},
        on_button_save=None,
    )


@pytest.mark.asyncio
async def test_detect_stale_inventory_returns_empty_when_no_command(tmp_path):
    """Defensive: a coordinator without ``nikobus_command`` (e.g. a
    bare-metal harness) gets an empty manifest plus a WARNING log,
    not an exception."""

    coord = MagicMock()
    coord.dict_module_data = {"switch_module": {"8110": {}}}
    coord.nikobus_command = None
    discovery = _make_discovery(coord, tmp_path)

    manifest = await discovery.detect_stale_inventory()

    assert manifest == {
        "checked": [],
        "present_modules": [],
        "absent_modules": [],
        "orphaned_buttons": [],
    }


@pytest.mark.asyncio
async def test_detect_stale_inventory_classifies_present_and_absent(tmp_path):
    """Three switch modules, one of which times out — manifest should
    list two present, one absent."""

    async def get_output_state(addr, group):
        if addr == "3D28":
            raise asyncio.TimeoutError()
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {"8110": {"address": "8110"}, "3D28": {"address": "3D28"}},
        "dimmer_module": {},
        "roller_module": {},
    }
    coord.dict_module_data["switch_module"]["1CEC"] = {"address": "1CEC"}
    discovery = _make_discovery(coord, tmp_path)

    manifest = await discovery.detect_stale_inventory()

    assert manifest["checked"] == ["1CEC", "3D28", "8110"]
    assert manifest["present_modules"] == ["1CEC", "8110"]
    assert manifest["absent_modules"] == ["3D28"]
    assert manifest["orphaned_buttons"] == []


@pytest.mark.asyncio
async def test_detect_stale_inventory_skips_non_output_module_types(tmp_path):
    """PC-Link / PC-Logic / feedback / audio / interface modules
    aren't in ``_BUS_PROBE_MODULE_TYPES`` — probing would either
    target the bridge itself (PC-Link) or a module class that
    doesn't respond uniformly to ``$1012``. None of them go into
    ``checked``."""

    probed: list[str] = []

    async def get_output_state(addr, group):
        probed.append(addr)
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {"8110": {}},
        "pc_link": {"823D": {}},
        "pc_logic": {"940C": {}},
        "feedback_module": {"966C": {}},
        "audio_module": {"8334": {}},
        "interface_module": {"5278": {}},
    }
    discovery = _make_discovery(coord, tmp_path)

    manifest = await discovery.detect_stale_inventory()

    assert probed == ["8110"]
    assert manifest["checked"] == ["8110"]
    assert "823D" not in manifest["checked"]
    assert "940C" not in manifest["checked"]


@pytest.mark.asyncio
async def test_detect_stale_inventory_flags_orphaned_buttons(tmp_path):
    """Cascade case: a button whose ``linked_modules`` block points
    only at an absent module is flagged as orphaned."""

    async def get_output_state(addr, group):
        if addr == "3D28":
            raise asyncio.TimeoutError()
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {"8110": {}, "3D28": {}},
    }
    button_data = {
        "nikobus_button": {
            # Linked only to absent module 3D28 → orphaned.
            "3C522A": {
                "operation_points": {
                    "1A": {
                        "linked_modules": [
                            {"module_address": "3D28", "outputs": []},
                        ],
                    },
                },
            },
            # Linked only to present module 8110 → not orphaned.
            "16766C": {
                "operation_points": {
                    "1A": {
                        "linked_modules": [
                            {"module_address": "8110", "outputs": []},
                        ],
                    },
                },
            },
            # Mixed: one absent + one present → NOT orphaned (still
            # drives something real).
            "1676A0": {
                "operation_points": {
                    "1A": {
                        "linked_modules": [
                            {"module_address": "3D28", "outputs": []},
                            {"module_address": "8110", "outputs": []},
                        ],
                    },
                },
            },
            # No links at all → NOT orphaned (might just be undecoded).
            "16E368": {
                "operation_points": {
                    "1A": {"linked_modules": []},
                },
            },
        }
    }
    discovery = _make_discovery(coord, tmp_path, button_data=button_data)

    manifest = await discovery.detect_stale_inventory()

    assert manifest["absent_modules"] == ["3D28"]
    assert manifest["orphaned_buttons"] == ["3C522A"]
    assert "16766C" not in manifest["orphaned_buttons"]
    assert "1676A0" not in manifest["orphaned_buttons"]
    assert "16E368" not in manifest["orphaned_buttons"]


@pytest.mark.asyncio
async def test_detect_stale_inventory_orphaned_address_uppercased(tmp_path):
    """Address comparisons are case-insensitive; orphaned addresses
    are returned in upper-case for consistency with the rest of the
    discovery payload."""

    async def get_output_state(addr, group):
        raise asyncio.TimeoutError()

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {"switch_module": {"3d28": {}}}
    button_data = {
        "nikobus_button": {
            "3c522a": {
                "operation_points": {
                    "1A": {
                        "linked_modules": [
                            {"module_address": "3d28", "outputs": []},
                        ],
                    },
                },
            },
        }
    }
    discovery = _make_discovery(coord, tmp_path, button_data=button_data)

    manifest = await discovery.detect_stale_inventory()

    assert manifest["absent_modules"] == ["3D28"]
    assert manifest["orphaned_buttons"] == ["3C522A"]


@pytest.mark.asyncio
async def test_detect_stale_inventory_handles_empty_dict_module_data(tmp_path):
    """No probable modules → empty manifest, but no error."""

    async def get_output_state(addr, group):
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {}
    discovery = _make_discovery(coord, tmp_path)

    manifest = await discovery.detect_stale_inventory()

    assert manifest == {
        "checked": [],
        "present_modules": [],
        "absent_modules": [],
        "orphaned_buttons": [],
    }


@pytest.mark.asyncio
async def test_detect_stale_inventory_propagates_cancellation(tmp_path):
    """``asyncio.CancelledError`` is propagated, not swallowed —
    otherwise a cancelled discovery task would silently consume the
    cancellation and finish the probe loop."""

    async def get_output_state(addr, group):
        raise asyncio.CancelledError()

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {"switch_module": {"8110": {}}}
    discovery = _make_discovery(coord, tmp_path)

    with pytest.raises(asyncio.CancelledError):
        await discovery.detect_stale_inventory()


@pytest.mark.asyncio
async def test_detect_stale_inventory_passes_per_probe_timeout(tmp_path):
    """The ``timeout`` argument is per-probe (so a 6-module probe with
    timeout=0.5 takes at most 3 s if every probe times out, not
    longer). Verifies the timeout reaches ``asyncio.wait_for``."""

    timeouts_seen: list[float | None] = []

    async def slow_response(addr, group):
        # Simulate a module that takes too long — wait long enough
        # for a tight ``timeout`` to fire but short enough that a
        # generous one would succeed.
        await asyncio.sleep(0.05)
        return "OK"

    coord = _make_coordinator(get_output_state=slow_response)
    coord.dict_module_data = {"switch_module": {"8110": {}}}
    discovery = _make_discovery(coord, tmp_path)

    # Tight timeout → absent.
    manifest_tight = await discovery.detect_stale_inventory(timeout=0.001)
    assert manifest_tight["absent_modules"] == ["8110"]

    # Generous timeout → present.
    manifest_loose = await discovery.detect_stale_inventory(timeout=1.0)
    assert manifest_loose["present_modules"] == ["8110"]


@pytest.mark.asyncio
async def test_detect_stale_inventory_real_world_secondhand_install(tmp_path):
    """Pin the manifest against the second-hand-PC-Link install
    captured in user log (https://github.com/user-attachments/files/
    27457361/log-2.txt). The user's current install has switch
    module 8110 + compact switch 1CEC; the previous owner's module
    3D28 is in the dump but doesn't respond on the bus.

    This is the canonical use case: the manifest should list
    [8110, 1CEC] as present, [3D28] as absent. Even without
    populated ``linked_modules`` data on the buttons, the absent-
    module classification alone is enough for the caller to start
    cleanup."""

    async def get_output_state(addr, group):
        if addr == "3D28":
            raise asyncio.TimeoutError()
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {
            "8110": {"address": "8110", "channels_count": 12},
            "1CEC": {"address": "1CEC", "channels_count": 4},
            "3D28": {"address": "3D28", "channels_count": 4},
        },
    }
    discovery = _make_discovery(coord, tmp_path)

    manifest = await discovery.detect_stale_inventory(timeout=0.5)

    assert manifest["checked"] == ["1CEC", "3D28", "8110"]
    assert manifest["present_modules"] == ["1CEC", "8110"]
    assert manifest["absent_modules"] == ["3D28"]
