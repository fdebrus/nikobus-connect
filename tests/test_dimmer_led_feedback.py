"""Regression tests for dimmer LED-feedback trigger gating.

Nikobus ``led_on`` / ``led_off`` are bus-address broadcasts that
simulate a wall-button press. The receiving button toggles its LED on
press, so firing the trigger when nothing transitioned (e.g. brightness
change while already on) flips the LED into the wrong state.

These tests pin the corrected logic: triggers fire only on real
off ↔ on transitions, never on brightness-only changes.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from nikobus_connect.api import NikobusAPI


def _make_api(led_on: str | None = "1A2B3C", led_off: str | None = "1A2B3D"):
    """Build an API instance backed by mocks so we can assert which bus
    commands were queued."""
    command_handler = MagicMock()
    command_handler.queue_command = AsyncMock()
    command_handler.set_output_state = AsyncMock()

    channel_meta: dict = {}
    if led_on is not None:
        channel_meta["led_on"] = led_on
    if led_off is not None:
        channel_meta["led_off"] = led_off

    module_data = {
        "dimmer_module": {
            "ADDR1": {"channels": [channel_meta]}
        }
    }
    return NikobusAPI(command_handler, module_data), command_handler


def _led_commands(handler) -> list[str]:
    """Return the LED-trigger broadcasts queued during the call."""
    return [
        call.args[0]
        for call in handler.queue_command.call_args_list
        if isinstance(call.args[0], str) and call.args[0].startswith("#N")
    ]


class TestDimmerLedFeedback(unittest.IsolatedAsyncioTestCase):
    async def test_turn_on_from_off_fires_led_on(self):
        api, handler = _make_api()
        await api.turn_on_light("ADDR1", 1, 80, current_brightness=0)
        self.assertEqual(_led_commands(handler), ["#N1A2B3C\r#E1"])
        handler.set_output_state.assert_awaited_once()

    async def test_turn_on_brightness_change_does_NOT_fire_led_on(self):
        """80 → 40 while already on must not toggle the LED."""
        api, handler = _make_api()
        await api.turn_on_light("ADDR1", 1, 40, current_brightness=80)
        self.assertEqual(_led_commands(handler), [])
        handler.set_output_state.assert_awaited_once()

    async def test_turn_on_brightness_increase_does_NOT_fire_led_on(self):
        """40 → 80 while already on must not toggle the LED."""
        api, handler = _make_api()
        await api.turn_on_light("ADDR1", 1, 80, current_brightness=40)
        self.assertEqual(_led_commands(handler), [])

    async def test_turn_off_from_on_fires_led_off(self):
        api, handler = _make_api()
        await api.turn_off_light("ADDR1", 1, current_brightness=80)
        self.assertEqual(_led_commands(handler), ["#N1A2B3D\r#E1"])
        handler.set_output_state.assert_awaited_once()

    async def test_turn_off_when_already_off_does_NOT_fire_led_off(self):
        """Calling off on a light that's already off must not toggle the LED."""
        api, handler = _make_api()
        await api.turn_off_light("ADDR1", 1, current_brightness=0)
        self.assertEqual(_led_commands(handler), [])
        handler.set_output_state.assert_awaited_once()

    async def test_turn_off_legacy_default_still_fires(self):
        """Backward-compat: callers that don't pass current_brightness
        get the legacy ``always fire`` behaviour (default kwarg = 1)."""
        api, handler = _make_api()
        await api.turn_off_light("ADDR1", 1)
        self.assertEqual(_led_commands(handler), ["#N1A2B3D\r#E1"])

    async def test_turn_on_legacy_default_still_fires(self):
        """Backward-compat: legacy callers (no current_brightness)
        keep the existing "fire-on-every-call" behaviour."""
        api, handler = _make_api()
        await api.turn_on_light("ADDR1", 1, 80)
        self.assertEqual(_led_commands(handler), ["#N1A2B3C\r#E1"])

    async def test_no_led_configured_is_a_clean_noop(self):
        api, handler = _make_api(led_on=None, led_off=None)
        await api.turn_on_light("ADDR1", 1, 80, current_brightness=0)
        await api.turn_off_light("ADDR1", 1, current_brightness=80)
        self.assertEqual(_led_commands(handler), [])
        self.assertEqual(handler.set_output_state.await_count, 2)


if __name__ == "__main__":
    unittest.main()
