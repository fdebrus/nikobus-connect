"""A host-injected key press is one write, not N queued items.

A real key repeats its telegram while held; modules act on a telegram
seen at least twice. Queued as separate items the repeats leave 150 ms
apart with other commands able to slip in between, which an impulse /
toggle link can count as two presses. ``press_button`` joins the repeats
into a single command.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from nikobus_connect.api import NikobusAPI


def _api(module_data=None) -> tuple[NikobusAPI, MagicMock]:
    handler = MagicMock()
    handler.queue_command = AsyncMock()
    handler.set_bytearray_state = MagicMock()
    return NikobusAPI(handler, module_data or {}), handler


async def test_press_is_one_command_with_three_frames() -> None:
    api, handler = _api()
    await api.press_button("295682")
    handler.queue_command.assert_awaited_once()
    command = handler.queue_command.await_args.args[0]
    assert command == "#N295682\r#E1\r#N295682\r#E1\r#N295682\r#E1"
    assert command.count("#N295682") == 3


async def test_press_repeat_is_honoured_with_a_floor_of_one() -> None:
    api, handler = _api()
    api.press_repeat = 2
    assert api.press_command("295682").count("#N") == 2
    api.press_repeat = 0
    assert api.press_command("295682").count("#N") == 1
    api.press_repeat = "x"  # type: ignore[assignment]
    assert api.press_command("295682").count("#N") == 3


async def test_led_trigger_and_switch_key_use_the_same_burst() -> None:
    module_data = {
        "switch_module": {
            "81F6": {"channels": [{}] * 6 + [{"led_on": "295682", "led_off": "295682"}] + [{}] * 5}
        }
    }
    api, handler = _api(module_data)
    await api.turn_on_switch("81F6", 7)
    command = handler.queue_command.await_args.args[0]
    assert command.count("#N295682") == 3
    # The key is pressed instead of the output being set.
    handler.set_bytearray_state.assert_called_once_with("81F6", 7, 0xFF)


async def test_completion_handler_is_passed_through() -> None:
    api, handler = _api()
    done = AsyncMock()
    await api.press_button("295682", completion_handler=done)
    assert handler.queue_command.await_args.kwargs["completion_handler"] is done
