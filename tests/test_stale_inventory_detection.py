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

    async def get_output_state(addr, group, *, timeout=None):
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

    manifest = await discovery.detect_stale_inventory(retry_delay=0)

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

    async def get_output_state(addr, group, *, timeout=None):
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

    async def get_output_state(addr, group, *, timeout=None):
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

    manifest = await discovery.detect_stale_inventory(retry_delay=0)

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

    async def get_output_state(addr, group, *, timeout=None):
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

    manifest = await discovery.detect_stale_inventory(retry_delay=0)

    assert manifest["absent_modules"] == ["3D28"]
    assert manifest["orphaned_buttons"] == ["3C522A"]


@pytest.mark.asyncio
async def test_detect_stale_inventory_handles_empty_dict_module_data(tmp_path):
    """No probable modules → empty manifest, but no error."""

    async def get_output_state(addr, group, *, timeout=None):
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

    async def get_output_state(addr, group, *, timeout=None):
        raise asyncio.CancelledError()

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {"switch_module": {"8110": {}}}
    discovery = _make_discovery(coord, tmp_path)

    with pytest.raises(asyncio.CancelledError):
        await discovery.detect_stale_inventory()


@pytest.mark.asyncio
async def test_detect_stale_inventory_passes_per_attempt_timeout(tmp_path):
    """The ``timeout`` argument is per-attempt. ``max_attempts=1``
    here so the test asserts a single-attempt absent without sleeping
    between retries; the retry contract has its own dedicated test."""

    async def slow_response(addr, group, *, timeout=None):
        # Simulate a module that takes ~50 ms to ACK. Honour the
        # caller-supplied ``timeout`` like the real
        # ``get_output_state`` would (0.5.20 pushed the deadline
        # inside the function — see Bug-2 note in the docstring).
        await asyncio.wait_for(
            asyncio.sleep(0.05), timeout=timeout if timeout is not None else 15
        )
        return "OK"

    coord = _make_coordinator(get_output_state=slow_response)
    coord.dict_module_data = {"switch_module": {"8110": {}}}
    discovery = _make_discovery(coord, tmp_path)

    # Tight timeout → absent.
    manifest_tight = await discovery.detect_stale_inventory(
        timeout=0.001, max_attempts=1
    )
    assert manifest_tight["absent_modules"] == ["8110"]

    # Generous timeout → present.
    manifest_loose = await discovery.detect_stale_inventory(
        timeout=1.0, max_attempts=1
    )
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

    async def get_output_state(addr, group, *, timeout=None):
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

    manifest = await discovery.detect_stale_inventory(timeout=0.5, retry_delay=0)

    assert manifest["checked"] == ["1CEC", "3D28", "8110"]
    assert manifest["present_modules"] == ["1CEC", "8110"]
    assert manifest["absent_modules"] == ["3D28"]


def test_detect_stale_inventory_defaults_pinned():
    """Pin the five keyword defaults so future regressions fail fast.

    History of changes:
      - 0.5.16: shipped with timeout=0.6, no retries
      - 0.5.17: terminator removed (orthogonal but related)
      - 0.5.18: timeout 0.6 -> 2.0 (fdebrus install report)
      - 0.5.19: max_attempts/retry_delay added; defaults
                max_attempts=3, retry_delay=0.5 after IKIKN field
                report (a real switch_module at 8110 ACKed in
                2.0-3.0 s under post-discovery bus congestion and
                false-negatived at max_attempts=1).
      - 0.5.20: outer_attempts/outer_delay added; defaults
                outer_attempts=1, outer_delay=0.0 to preserve
                pre-0.5.20 single-pass behaviour. Bug-2 fix
                (Nikobus-HA #319 IKIKN trace) made retries actually
                reach the wire; consumers can now opt into outer
                passes to give bus-quiesce time between rounds.
    """

    import inspect

    from nikobus_connect.discovery.discovery import NikobusDiscovery

    sig = inspect.signature(NikobusDiscovery.detect_stale_inventory)
    assert sig.parameters["timeout"].default == 2.0
    assert sig.parameters["max_attempts"].default == 3
    assert sig.parameters["retry_delay"].default == 0.5
    assert sig.parameters["outer_attempts"].default == 1
    assert sig.parameters["outer_delay"].default == 0.0


@pytest.mark.asyncio
async def test_detect_stale_inventory_retries_slow_module_to_present(tmp_path):
    """IKIKN-fixture pin: a real module whose ACK lands on attempt 2
    must classify as present, not absent.

    Mirrors the 2026-05-10 Nikobus-HA field report: switch module
    8110 (real, physically wired) ACKs in 2.0-3.0 s under bus
    congestion immediately post-discovery. With ``max_attempts=1``
    and ``timeout=2.0`` the probe false-negatived. With three
    attempts at 2 s each, 8110 lands as ``present`` on attempt 2.
    """

    call_counts: dict[str, int] = {}

    async def get_output_state(addr, group, *, timeout=None):
        n = call_counts.get(addr, 0) + 1
        call_counts[addr] = n
        if addr == "8110":
            # Slow real module — first attempt times out, second
            # succeeds.
            if n == 1:
                raise asyncio.TimeoutError()
            return "OK"
        if addr == "1CEC":
            return "OK"
        if addr == "3D28":
            # Residue — never ACKs.
            raise asyncio.TimeoutError()
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {
            "8110": {"address": "8110"},
            "1CEC": {"address": "1CEC"},
            "3D28": {"address": "3D28"},
        },
    }
    discovery = _make_discovery(coord, tmp_path)

    manifest = await discovery.detect_stale_inventory(
        max_attempts=3, retry_delay=0
    )

    assert manifest["present_modules"] == ["1CEC", "8110"]
    assert manifest["absent_modules"] == ["3D28"]
    # 8110 took 2 attempts.
    assert call_counts["8110"] == 2
    # 3D28 exhausted all 3 attempts.
    assert call_counts["3D28"] == 3
    # 1CEC succeeded on first try.
    assert call_counts["1CEC"] == 1


@pytest.mark.asyncio
async def test_detect_stale_inventory_max_attempts_one_preserves_pre_0_5_19_behaviour(
    tmp_path,
):
    """``max_attempts=1`` opts out of retries — single probe, single
    classification. Same contract as 0.5.18."""

    calls: list[str] = []

    async def get_output_state(addr, group, *, timeout=None):
        calls.append(addr)
        if addr == "3D28":
            raise asyncio.TimeoutError()
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {"8110": {}, "3D28": {}},
    }
    discovery = _make_discovery(coord, tmp_path)

    await discovery.detect_stale_inventory(max_attempts=1, retry_delay=0)

    # Each address probed exactly once.
    assert calls.count("8110") == 1
    assert calls.count("3D28") == 1


@pytest.mark.asyncio
async def test_detect_stale_inventory_retry_delay_zero_skips_sleep(tmp_path):
    """``retry_delay=0`` removes the inter-attempt sleep, useful for
    test fixtures that don't want to wait. Attempts still happen in
    sequence, just back-to-back."""

    calls: list[str] = []

    async def get_output_state(addr, group, *, timeout=None):
        calls.append(addr)
        raise asyncio.TimeoutError()

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {"switch_module": {"3D28": {}}}
    discovery = _make_discovery(coord, tmp_path)

    import time

    start = time.monotonic()
    manifest = await discovery.detect_stale_inventory(
        timeout=0.001, max_attempts=3, retry_delay=0
    )
    elapsed = time.monotonic() - start

    assert manifest["absent_modules"] == ["3D28"]
    assert calls.count("3D28") == 3
    # 3 attempts × 0.001 s timeout + 0 s sleep = much less than 1 s.
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_detect_stale_inventory_outer_loop_recovers_module_after_quiesce(tmp_path):
    """0.5.20 outer-loop pin: a module that fails all inner attempts
    on pass 1 but ACKs on pass 2 (e.g. bus quiesced between passes)
    must classify as ``present``.

    Models the IKIKN scenario: bus saturated with post-discovery
    traffic during pass 1, then quiet for pass 2. Without an outer
    loop the module would be marked absent. With
    ``outer_attempts=2`` and ``outer_delay`` (zero here for test
    speed; consumers use 3.0s in production) the module recovers.
    """

    outer_pass_count = {"n": 0}

    async def get_output_state(addr, group, *, timeout=None):
        if addr == "1CEC":
            return "OK"  # always fast
        if addr == "3D28":
            raise asyncio.TimeoutError()  # residue, never ACKs
        if addr == "8110":
            # Real but slow under load: fails all inner attempts on
            # outer pass 1, ACKs immediately on outer pass 2.
            if outer_pass_count["n"] == 0:
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

    # Patch asyncio.sleep so we can observe the outer pause without
    # actually sleeping, and use it to flip outer_pass_count.
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay >= 0.1:
            # This is the outer pause — simulate bus quiescing.
            outer_pass_count["n"] = 1
        await real_sleep(0)

    import unittest.mock as mock

    with mock.patch("asyncio.sleep", new=fake_sleep):
        manifest = await discovery.detect_stale_inventory(
            outer_attempts=2, outer_delay=0.5, retry_delay=0
        )

    assert "1CEC" in manifest["present_modules"]
    assert "8110" in manifest["present_modules"]  # recovered on pass 2
    assert manifest["absent_modules"] == ["3D28"]


@pytest.mark.asyncio
async def test_detect_stale_inventory_outer_loop_skips_already_present(tmp_path):
    """Modules classified ``present`` on an earlier outer pass are
    not re-probed on subsequent passes. Saves wire traffic and
    keeps the wallclock budget bounded."""

    call_counts: dict[str, int] = {}

    async def get_output_state(addr, group, *, timeout=None):
        n = call_counts.get(addr, 0) + 1
        call_counts[addr] = n
        if addr == "1CEC":
            return "OK"  # always ACK on first try
        if addr == "3D28":
            raise asyncio.TimeoutError()  # never ACK
        return "OK"

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {
        "switch_module": {"1CEC": {}, "3D28": {}},
    }
    discovery = _make_discovery(coord, tmp_path)

    await discovery.detect_stale_inventory(
        outer_attempts=3, outer_delay=0, max_attempts=1, retry_delay=0
    )

    # 1CEC ACK'd on pass 1, never re-probed.
    assert call_counts["1CEC"] == 1
    # 3D28 probed every pass (3 outer × 1 inner = 3 calls).
    assert call_counts["3D28"] == 3


@pytest.mark.asyncio
async def test_detect_stale_inventory_outer_attempts_default_matches_pre_0_5_20(tmp_path):
    """Backward-compat pin: with default ``outer_attempts=1`` the
    probe runs exactly one full pass. Same wire-send count as 0.5.19."""

    call_counts: dict[str, int] = {}

    async def get_output_state(addr, group, *, timeout=None):
        call_counts[addr] = call_counts.get(addr, 0) + 1
        raise asyncio.TimeoutError()

    coord = _make_coordinator(get_output_state=get_output_state)
    coord.dict_module_data = {"switch_module": {"3D28": {}}}
    discovery = _make_discovery(coord, tmp_path)

    await discovery.detect_stale_inventory(retry_delay=0)

    # Default outer_attempts=1 × default max_attempts=3 = 3 calls.
    assert call_counts["3D28"] == 3


def test_complete_discovery_run_callback_signature_compat():
    """Pin the backward-compat callback signature detection.

    Pre-0.5.20: ``on_discovery_finished`` was no-arg.
    0.5.20+:    ``on_discovery_finished(discovered_devices, inventory_query_type)``
                or accepts ``**kwargs``.

    The library inspects the callback signature and calls
    accordingly — old no-arg callbacks still work.
    """

    import asyncio
    import inspect

    from nikobus_connect.discovery.discovery import _notify_discovery_finished

    # Stand-in for the discovery object — only on_discovery_finished
    # is accessed.
    class _DummyDiscovery:
        pass

    received: dict = {}

    async def new_style_cb(*, discovered_devices, inventory_query_type):
        received["devices"] = discovered_devices
        received["query_type"] = inventory_query_type

    discovery_new = _DummyDiscovery()
    discovery_new.on_discovery_finished = new_style_cb

    asyncio.run(
        _notify_discovery_finished(
            discovery_new,
            discovered_devices={"8110": {}},
            inventory_query_type="PC_LINK",
        )
    )
    assert received == {
        "devices": {"8110": {}},
        "query_type": "PC_LINK",
    }

    # Old no-arg callback — must still be invoked.
    old_called = {"n": 0}

    async def old_style_cb():
        old_called["n"] += 1

    discovery_old = _DummyDiscovery()
    discovery_old.on_discovery_finished = old_style_cb

    asyncio.run(
        _notify_discovery_finished(
            discovery_old,
            discovered_devices={"x": 1},
            inventory_query_type="PC_LINK",
        )
    )
    assert old_called["n"] == 1

    # **kwargs callback — receives both.
    kwargs_received: dict = {}

    async def kwargs_style_cb(**kwargs):
        kwargs_received.update(kwargs)

    discovery_kwargs = _DummyDiscovery()
    discovery_kwargs.on_discovery_finished = kwargs_style_cb

    asyncio.run(
        _notify_discovery_finished(
            discovery_kwargs,
            discovered_devices={"y": 2},
            inventory_query_type="MODULE",
        )
    )
    assert kwargs_received == {
        "discovered_devices": {"y": 2},
        "inventory_query_type": "MODULE",
    }
