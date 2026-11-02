"""A dimmer's LED key is pressed from the module's own state.

``led_on`` / ``led_off`` press a wall key, and the Feedback Module toggles
that key's LED on every telegram it sees (it keeps the LED state itself;
a set-output never reaches it). A press that was not needed therefore
inverts the LED. Until 0.57.3 the press was gated on the brightness the
host last knew, which may be stale; the channel is now read first, as
for a switch, and the host's value is only the fallback when the module
does not answer.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from nikobus_connect.api import NikobusAPI
from nikobus_connect.exceptions import NikobusTimeoutError

ON = "80" + "00" * 5  # channel 1 at level 0x80, the rest off
OFF = "00" * 6


def _make_api(led_on: str | None = "1A2B3C", led_off: str | None = "1A2B3D", module_state: str | None = OFF):
    """An API over mocks; ``module_state`` is what the module answers a
    state read with (``None``: it does not answer)."""
    command_handler = MagicMock()
    command_handler.queue_command = AsyncMock()
    command_handler.set_output_state = AsyncMock()
    command_handler.get_output_state = AsyncMock(
        return_value=module_state,
        side_effect=None if module_state is not None else NikobusTimeoutError("silent"),
    )
    channel_meta: dict = {}
    if led_on is not None:
        channel_meta["led_on"] = led_on
    if led_off is not None:
        channel_meta["led_off"] = led_off
    module_data = {"dimmer_module": {"ADDR1": {"channels": [channel_meta]}}}
    return NikobusAPI(command_handler, module_data), command_handler


def _led_commands(handler) -> list[str]:
    """The key presses queued: one command carrying the ``#N`` frame
    ``press_repeat`` times (3), collapsed to the frame."""
    out = []
    for call in handler.queue_command.call_args_list:
        cmd = call.args[0]
        if isinstance(cmd, str) and cmd.startswith("#N"):
            frames = cmd.split("\r#N")
            assert len(frames) == 3, cmd
            out.append(frames[0])
    return out


class TestDimmerLedFeedback(unittest.IsolatedAsyncioTestCase):
    async def test_turn_on_presses_when_the_module_says_off(self):
        api, handler = _make_api(module_state=OFF)
        await api.turn_on_light("ADDR1", 1, 80, current_brightness=0)
        self.assertEqual(_led_commands(handler), ["#N1A2B3C\r#E1"])
        handler.get_output_state.assert_awaited_once_with("ADDR1", 1)
        handler.set_output_state.assert_awaited_once()

    async def test_turn_on_does_not_press_when_the_module_says_on(self):
        """The host thinks the light is off (stale); the module knows better."""
        api, handler = _make_api(module_state=ON)
        await api.turn_on_light("ADDR1", 1, 80, current_brightness=0)
        self.assertEqual(_led_commands(handler), [])
        handler.set_output_state.assert_awaited_once()

    async def test_brightness_change_while_on_does_not_press(self):
        api, handler = _make_api(module_state=ON)
        await api.turn_on_light("ADDR1", 1, 40, current_brightness=80)
        self.assertEqual(_led_commands(handler), [])

    async def test_turn_off_presses_when_the_module_says_on(self):
        api, handler = _make_api(module_state=ON)
        await api.turn_off_light("ADDR1", 1, current_brightness=0)  # host stale: it thinks off
        self.assertEqual(_led_commands(handler), ["#N1A2B3D\r#E1"])
        handler.set_output_state.assert_awaited_once()

    async def test_turn_off_does_not_press_when_the_module_says_off(self):
        api, handler = _make_api(module_state=OFF)
        await api.turn_off_light("ADDR1", 1, current_brightness=80)
        self.assertEqual(_led_commands(handler), [])
        handler.set_output_state.assert_awaited_once()

    async def test_unanswered_module_falls_back_to_the_hosts_brightness(self):
        api, handler = _make_api(module_state=None)
        await api.turn_on_light("ADDR1", 1, 80, current_brightness=0)
        await api.turn_off_light("ADDR1", 1, current_brightness=0)
        self.assertEqual(_led_commands(handler), ["#N1A2B3C\r#E1"])  # on pressed, off not

    async def test_no_led_configured_reads_nothing_and_presses_nothing(self):
        api, handler = _make_api(led_on=None, led_off=None)
        await api.turn_on_light("ADDR1", 1, 80, current_brightness=0)
        await api.turn_off_light("ADDR1", 1, current_brightness=80)
        self.assertEqual(_led_commands(handler), [])
        handler.get_output_state.assert_not_awaited()
        self.assertEqual(handler.set_output_state.await_count, 2)


if __name__ == "__main__":
    unittest.main()
