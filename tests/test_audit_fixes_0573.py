"""Fixes of the third audit (0.57.3), besides the two-button plates.

Inventory: the queued-reads guard runs in both inventory stages (the
normal flow never left ``inventory_addresses``). Command handler: a
read whose first caller gave up is still made when a second caller
joined it. Listener: only the first answer after our ack is ours.
Motion detector: a bad address yields no frames rather than an error.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nikobus_connect import command as cmdmod
from nikobus_connect.discovery import (
    has_per_plate_key_layout,
    is_motion_detector,
    motion_detector_bus_addresses,
    two_button_bus_addresses,
)
from nikobus_connect.listener import NikobusEventListener
from nikobus_connect.nkb import key_layout_for_model
from tests.test_audit_fixes_0563 import _Coord, _discovery, _handler


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["inventory_addresses", "inventory_identity"])
async def test_inventory_window_waits_in_both_stages(tmp_path, stage: str) -> None:
    coordinator = _Coord()
    command = MagicMock()
    command.pending_count = MagicMock(return_value=40)
    coordinator.nikobus_command = command
    discovery = _discovery(tmp_path, coordinator)
    discovery.discovery_stage = stage
    discovery._inventory_addresses = {"86F5"}
    discovery._inventory_identity_queued = {"86F5"}
    rearmed: list[bool] = []
    discovery._schedule_inventory_timeout = lambda: rearmed.append(True)  # type: ignore[method-assign]
    await discovery._finalize_inventory_phase()
    assert rearmed == [True]
    assert discovery.discovery_stage == stage
    command.pending_count.assert_called_with("$1410")


@pytest.mark.asyncio
async def test_a_joined_read_is_made_after_its_first_caller_gave_up(monkeypatch) -> None:
    monkeypatch.setattr(cmdmod, "COMMAND_ACK_WAIT_TIMEOUT", 0.4)
    monkeypatch.setattr(cmdmod, "COMMAND_EXECUTION_DELAY", 0.0)
    handler, _conn, _listener = _handler()
    sent: list[str] = []

    async def exchange(command: str, address: str, attempts: int = 3) -> str:
        sent.append(command)
        if address == "SLOW":
            await asyncio.sleep(0.5)  # a command ahead in the queue
        return "AABBCCDDEEFF"

    handler._send_command_get_answer = exchange  # type: ignore[method-assign]
    await handler.start()
    await handler.queue_command("$1012SLOW", "SLOW", future=asyncio.get_running_loop().create_future())

    async def caller(delay: float) -> str | BaseException:
        await asyncio.sleep(delay)
        try:
            return await handler.get_output_state("C9A5", 1)
        except (TimeoutError, asyncio.CancelledError) as err:
            return err

    first, second = await asyncio.gather(caller(0.0), caller(0.2))
    await handler.stop()
    assert isinstance(first, TimeoutError)  # gave up while the slow command ran
    assert second == "AABBCCDDEEFF"  # joined the same read and was answered from it
    assert sum("A5C9" in c for c in sent) == 1


def _listener():
    seen: list[tuple[int, str]] = []

    async def feedback(group: int, message: str) -> None:
        seen.append((group, message))

    listener = NikobusEventListener(
        MagicMock(), event_callback=lambda _m: None, feedback_callback=feedback, has_feedback_module=True
    )
    listener.validate_crc = lambda _m: True  # type: ignore[method-assign]
    listener.set_pending_query_group("C9A5", 1)
    listener._awaiting_response = True
    listener._awaited_answer = "$1CA5C9"
    return listener, seen


@pytest.mark.asyncio
async def test_only_the_first_answer_after_our_ack_is_ours() -> None:
    listener, seen = _listener()
    ours = "$1CA5C9010101000000A1B2"
    theirs = "$1CA5C9FFFF00000000C3D4"
    await listener._dispatch_message("$0512")  # our ack
    await listener._dispatch_message(ours)
    await listener._dispatch_message("$1017A5C9AABB")  # the Feedback Module asks group 2
    await listener._dispatch_message(theirs)
    assert seen == [(1, ours), (2, theirs)]


def test_a_bad_motion_detector_address_yields_no_frames() -> None:
    assert motion_detector_bus_addresses("0986BG") == {}
    assert motion_detector_bus_addresses("") == {}
    assert motion_detector_bus_addresses("0986BC")["on"] == "8F5864"


def test_the_helpers_a_host_uses_are_exported() -> None:
    assert has_per_plate_key_layout({"model": "05-060", "channels": 2})
    assert two_button_bus_addresses("180C56", "8_C") == {"1A": "9A8C06", "1B": "DA8C06"}
    assert is_motion_detector({"device_type": "28"})
    assert key_layout_for_model("42-082") == {"1A": "0", "1B": "4"}
