"""Bus-presence cross-check for stale inventory entries.

Background: a user with a second-hand PC-Link sees records from the
previous owner's installation in their inventory dump. Niko's PC
software writes new programming on top of old, but unused register
slots aren't auto-zeroed, so any module / button records the new
install doesn't overwrite stay present in PC-Link flash.

``detect_stale_inventory`` probes each output-bearing module address
on the live bus via ``$1012<addr>`` (through ``get_output_state``)
and classifies them as present / absent. Buttons are flagged as
orphaned when their entire ``linked_modules`` set sits inside the
absent set.

The probe loop relies on the command pipeline's own retry budget
(``MAX_ATTEMPTS=3`` × ~5 s per attempt) — see 0.5.21 simplification
note in ``detect_stale_inventory``'s docstring.
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
    """Three switch modules, one of which fails — manifest should
    list two present, one absent.

    Absent is simulated by raising NikobusTimeoutError-equivalent
    (any non-CancelledError exception). The command pipeline's
    internal retries are inside ``get_output_state``; here we mock
    them away and treat the function's return-or-raise as the
    classification signal."""

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
async def test_detect_stale_inventory_no_starvation_on_slow_first_module(tmp_path):
    """0.5.21 architectural pin: probes run serially in queue order.
    A slow / absent module ahead in the queue does NOT cause
    subsequent modules to be skipped or false-negatived. Each
    module is probed once per outer pass; the command pipeline's
    own retry budget handles transient ACK delays.

    Mirrors the IKIKN scenario (Nikobus-HA #319) where pre-0.5.21
    code wrapped each probe in ``asyncio.wait_for(timeout=2.0)``,
    racing the command pipeline's 3-attempt × 5 s = 15 s natural
    budget. Real module 8110 was starved while 3D28 (absent)
    hogged the processor."""

    call_order: list[str] = []

    async def get_output_state(addr, group):
        call_order.append(addr)
        if addr == "3D28":
            # Residue — simulates the command pipeline exhausting
            # its 3 inner attempts.
            raise asyncio.TimeoutError()
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {
            "1CEC": {"address": "1CEC"},
            "3D28": {"address": "3D28"},
            "8110": {"address": "8110"},
        },
    }
    discovery = _make_discovery(coord, tmp_path)

    manifest = await discovery.detect_stale_inventory()

    # Every module probed exactly once — no false-positives from
    # starvation, no false-negatives from racing the queue.
    assert call_order == ["1CEC", "3D28", "8110"]
    assert manifest["present_modules"] == ["1CEC", "8110"]
    assert manifest["absent_modules"] == ["3D28"]


@pytest.mark.asyncio
async def test_detect_stale_inventory_real_world_secondhand_install(tmp_path):
    """Pin the manifest against the second-hand-PC-Link install
    captured in user log (Nikobus-HA #319). Current install has
    switch module 8110 + compact switch 1CEC; previous owner's
    module 3D28 is in the dump but doesn't respond on the bus.

    Manifest must list [8110, 1CEC] as present, [3D28] as absent."""

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

    manifest = await discovery.detect_stale_inventory()

    assert manifest["checked"] == ["1CEC", "3D28", "8110"]
    assert manifest["present_modules"] == ["1CEC", "8110"]
    assert manifest["absent_modules"] == ["3D28"]


def test_detect_stale_inventory_signature():
    """Pin the public signature.

    0.5.21 simplified the API: removed ``timeout``, ``max_attempts``,
    ``retry_delay`` (the command pipeline's MAX_ATTEMPTS=3 × per-attempt
    timeout IS the retry budget). Kept ``outer_attempts`` /
    ``outer_delay`` for opt-in bus-quiesce passes.
    """

    import inspect

    from nikobus_connect.discovery.discovery import NikobusDiscovery

    sig = inspect.signature(NikobusDiscovery.detect_stale_inventory)
    params = sig.parameters

    # New surface:
    assert params["outer_attempts"].default == 1
    assert params["outer_delay"].default == 0.0
    # Removed surface (0.5.21):
    assert "timeout" not in params
    assert "max_attempts" not in params
    assert "retry_delay" not in params


@pytest.mark.asyncio
async def test_detect_stale_inventory_outer_loop_recovers_after_quiesce(tmp_path):
    """``outer_attempts > 1`` re-probes any module not yet
    classified ``present``. Modules already classified ``present``
    are skipped on subsequent passes to save wire traffic."""

    pass_seen: dict[str, int] = {"n": 0}
    call_counts: dict[str, int] = {}

    async def get_output_state(addr, group):
        call_counts[addr] = call_counts.get(addr, 0) + 1
        if addr == "1CEC":
            return "OK"
        if addr == "3D28":
            raise asyncio.TimeoutError()
        if addr == "8110":
            # Real but slow under load — fails on pass 1, ACKs on
            # pass 2 (after the bus quiesces during outer_delay).
            if pass_seen["n"] == 0:
                raise asyncio.TimeoutError()
            return "OK"
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {
            "1CEC": {"address": "1CEC"},
            "3D28": {"address": "3D28"},
            "8110": {"address": "8110"},
        },
    }
    discovery = _make_discovery(coord, tmp_path)

    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        # Treat any non-trivial sleep as the outer-pass bus-quiesce
        # signal — flip the simulated bus state to "quiet".
        if delay >= 0.1:
            pass_seen["n"] = 1
        await real_sleep(0)

    import unittest.mock as mock

    with mock.patch("asyncio.sleep", new=fake_sleep):
        manifest = await discovery.detect_stale_inventory(
            outer_attempts=2, outer_delay=0.5
        )

    assert "1CEC" in manifest["present_modules"]
    assert "8110" in manifest["present_modules"]  # recovered on pass 2
    assert manifest["absent_modules"] == ["3D28"]
    # 1CEC ACK'd on pass 1, not re-probed.
    assert call_counts["1CEC"] == 1
    # 3D28 probed each pass.
    assert call_counts["3D28"] == 2
    # 8110 probed both passes (failed pass 1, ACK pass 2).
    assert call_counts["8110"] == 2


@pytest.mark.asyncio
async def test_detect_stale_inventory_outer_default_is_single_pass(tmp_path):
    """Default ``outer_attempts=1, outer_delay=0.0`` runs exactly
    one full sweep — same wire-send count as a hand-rolled
    ``for addr in modules: await get_output_state(...)`` loop."""

    call_counts: dict[str, int] = {}

    async def get_output_state(addr, group):
        call_counts[addr] = call_counts.get(addr, 0) + 1
        raise asyncio.TimeoutError()

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {"A": {}, "B": {}, "C": {}},
    }
    discovery = _make_discovery(coord, tmp_path)

    await discovery.detect_stale_inventory()

    assert call_counts == {"A": 1, "B": 1, "C": 1}
