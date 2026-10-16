"""A switch channel driven by its feedback key is read, pressed, and checked.

A feedback key keeps a plate's LED in step with the output, which a
set-output does not, but it is usually a toggle: pressed blindly it can
switch the wrong way (``turn_off`` on an output that is already off),
and a lost press goes unnoticed until the next poll. Reported on a real
install (Nikobus-HA, 2026-10): terrace strip C9D7 channel 11, toggle key
``461182``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.api import NikobusAPI

KEY = "461182"
GROUP2_OFF = "00" * 6
GROUP2_CH11_ON = "00" * 4 + "FF" + "00"  # channel 11 = group 2, fifth byte


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    monkeypatch.setattr("nikobus_connect.api.FEEDBACK_KEY_SETTLE_S", 0)


def _api(reads, *, led_on=KEY, led_off=KEY) -> tuple[NikobusAPI, MagicMock]:
    channel = {}
    if led_on:
        channel["led_on"] = led_on
    if led_off:
        channel["led_off"] = led_off
    module_data = {"switch_module": {"C9D7": {"channels": [{}] * 10 + [channel, {}]}}}
    handler = MagicMock()

    async def _queue(command, *args, completion_handler=None, **kwargs):
        if completion_handler is not None:
            await completion_handler()

    handler.queue_command = AsyncMock(side_effect=_queue)
    handler.get_output_state = AsyncMock(side_effect=list(reads))
    handler.set_output_state = AsyncMock()
    handler.set_bytearray_state = MagicMock()
    return NikobusAPI(handler, module_data), handler


def _pressed(handler) -> int:
    return sum(1 for c in handler.queue_command.await_args_list if f"#N{KEY}" in c.args[0])


async def test_turn_off_presses_the_key_and_checks_it() -> None:
    api, handler = _api([GROUP2_CH11_ON, GROUP2_OFF])
    assert await api.turn_off_switch("C9D7", 11) is None  # signature unchanged
    assert _pressed(handler) == 1
    handler.set_output_state.assert_not_called()
    assert [c.args for c in handler.get_output_state.await_args_list] == [("C9D7", 2), ("C9D7", 2)]
    handler.set_bytearray_state.assert_any_call("C9D7", 11, 0x00)


async def test_turn_off_on_an_output_already_off_does_not_press() -> None:
    api, handler = _api([GROUP2_OFF])
    await api.turn_off_switch("C9D7", 11)
    assert _pressed(handler) == 0
    handler.set_output_state.assert_not_called()


async def test_turn_on_on_an_output_already_on_does_not_press() -> None:
    api, handler = _api([GROUP2_CH11_ON])
    await api.turn_on_switch("C9D7", 11)
    assert _pressed(handler) == 0


async def test_a_lost_press_is_corrected_with_a_set_output() -> None:
    api, handler = _api([GROUP2_CH11_ON, GROUP2_CH11_ON])
    await api.turn_off_switch("C9D7", 11)
    assert _pressed(handler) == 1
    handler.set_output_state.assert_awaited_once()
    assert handler.set_output_state.await_args.args == ("C9D7", 11, 0x00)


async def test_an_unanswered_read_sets_the_output_without_pressing() -> None:
    api, handler = _api([TimeoutError()])
    await api.turn_off_switch("C9D7", 11)
    assert _pressed(handler) == 0
    handler.set_output_state.assert_awaited_once()


async def test_toggle_reads_the_module_not_the_hosts_idea() -> None:
    api, handler = _api([GROUP2_CH11_ON, GROUP2_OFF])
    assert await api.toggle_switch("C9D7", 11) is False
    assert _pressed(handler) == 1
    handler.set_output_state.assert_not_called()


async def test_toggle_with_an_unanswered_read_presses_a_toggle_key_blind() -> None:
    api, handler = _api([TimeoutError()])
    assert await api.toggle_switch("C9D7", 11) is None
    assert _pressed(handler) == 1


async def test_toggle_without_a_key_sets_the_other_state() -> None:
    api, handler = _api([GROUP2_OFF], led_on=None, led_off=None)
    assert await api.toggle_switch("C9D7", 11) is True
    assert _pressed(handler) == 0
    handler.set_output_state.assert_awaited_once()
    assert handler.set_output_state.await_args.args == ("C9D7", 11, 0xFF)


async def test_a_channel_without_a_key_is_set_as_before() -> None:
    api, handler = _api([], led_on=None, led_off=None)
    await api.turn_off_switch("C9D7", 11)
    handler.get_output_state.assert_not_called()
    handler.set_output_state.assert_awaited_once()


def test_has_feedback_key() -> None:
    api, _ = _api([])
    assert api.has_feedback_key("switch_module", "C9D7", 11)
    assert not api.has_feedback_key("switch_module", "C9D7", 10)
    assert not api.has_feedback_key("switch_module", "0000", 1)
