"""Regression tests for light-scene Central Function classification.

The PC software's "MCF (Activate light scene / Central Function)"
connection mode also covers CFs triggered by a real wall button or IR
input (not a ``38xx`` PC-Logic broadcast). For those the source IS a
valid button address, so it never lands in the unmatched accumulator —
but the output modules store the member records in a light-scene /
preset output mode.

``_classify_cf_scenes_from_command_mapping`` detects those: it groups
decoded outputs by their source ``button_address`` and flags any source
with a light-scene / preset member as a Central Function, pulling in all
members that share the source (including roller/switch members stored in
plain Open/Close/On modes).

The member fixtures mirror a real install ("waterloo", Nikobus-HA): CF6
"Scene - Dinner" on IR source ``0D1C9E`` ("30B") with a dimmer preset
member plus a switch and a shutter member in plain M02.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from nikobus_connect.discovery import discovery as discovery_mod
from nikobus_connect.discovery.discovery import (
    CFBroadcast,
    NikobusDiscovery,
    _is_cf_scene_mode,
)


def _bare_discovery() -> NikobusDiscovery:
    """A NikobusDiscovery with just the attributes the classifier reads,
    bypassing __init__ (which needs a live coordinator)."""
    d = NikobusDiscovery.__new__(NikobusDiscovery)
    d._accumulated_command_mapping = {}
    d.discovered_cf_broadcasts = {}
    return d


def _out(button, module, channel, mode, ir_button=None):
    return {
        "button_address": button,
        "ir_button_address": ir_button,
        "module_address": module,
        "channel": channel,
        "mode": mode,
        "t1": None,
        "t2": None,
    }


class TestSceneModeMarker(unittest.TestCase):
    def test_dimmer_light_scene_and_preset_modes_match(self):
        for mode in (
            "M03 (Light scene on/off)",
            "M04 (Light scene on)",
            "M11 (Preset on/off)",
            "M12 (Preset on)",
        ):
            self.assertTrue(_is_cf_scene_mode(mode), mode)

    def test_switch_light_scene_modes_match(self):
        self.assertTrue(_is_cf_scene_mode("M14 (Light scene on)"))
        self.assertTrue(_is_cf_scene_mode("M15 (Light scene on / off)"))

    def test_ordinary_modes_do_not_match(self):
        for mode in (
            "M01 (On / off)",
            "M02 (Open)",
            "M03 (Close)",
            "M03 (Off + Operating time)",
            "M06 (Off + Operating time)",
            "M01 (Dim on/off (2 buttons))",
        ):
            self.assertFalse(_is_cf_scene_mode(mode), mode)

    def test_non_string_is_safe(self):
        self.assertFalse(_is_cf_scene_mode(None))
        self.assertFalse(_is_cf_scene_mode(12))


class TestSceneClassifier(unittest.TestCase):
    def test_cf6_scene_dinner_is_classified_with_all_members(self):
        """A light-scene member on the dimmer pulls the whole source —
        including the switch/shutter members stored in plain M02 — into
        one Central Function."""
        d = _bare_discovery()
        d._accumulated_command_mapping = {
            ("0D1C9E", 3, None): [
                _out("0D1C9E", "0E6C", 1, "M04 (Light scene on)"),
                _out("0D1C9E", "4707", 12, "M02 (On + Operating time)"),
                _out("0D1C9E", "8394", 1, "M02 (Open)"),
            ],
        }

        d._classify_cf_scenes_from_command_mapping()

        self.assertIn("0D1C9E", d.discovered_cf_broadcasts)
        cf = d.discovered_cf_broadcasts["0D1C9E"]
        self.assertIsInstance(cf, CFBroadcast)
        self.assertEqual(cf.pattern, "light_scene")
        members = {(o.module_address, o.channel, o.mode) for o in cf.outputs}
        self.assertEqual(
            members,
            {
                ("0E6C", 1, "M04 (Light scene on)"),
                ("4707", 12, "M02 (On + Operating time)"),
                ("8394", 1, "M02 (Open)"),
            },
        )

    def test_ordinary_multi_output_button_is_not_a_cf(self):
        """A button driving the dimmer + shutters open/close with no
        light-scene/preset member must NOT be flagged — this is the
        'close all shutters' counter-example."""
        d = _bare_discovery()
        d._accumulated_command_mapping = {
            ("1CFBA0", 3, None): [
                _out("1CFBA0", "0E6C", 1, "M06 (Off + Operating time)"),
                _out("1CFBA0", "8394", 1, "M03 (Close)"),
                _out("1CFBA0", "9105", 2, "M03 (Close)"),
            ],
        }

        d._classify_cf_scenes_from_command_mapping()

        self.assertEqual(d.discovered_cf_broadcasts, {})

    def test_members_deduped_across_rescans(self):
        d = _bare_discovery()
        d._accumulated_command_mapping = {
            ("0D1C9E", 1, None): [
                _out("0D1C9E", "0E6C", 1, "M12 (Preset on)"),
                _out("0D1C9E", "0E6C", 1, "M12 (Preset on)"),  # dup
            ],
        }

        d._classify_cf_scenes_from_command_mapping()

        cf = d.discovered_cf_broadcasts["0D1C9E"]
        self.assertEqual(len(cf.outputs), 1)

    def test_existing_38xx_broadcast_is_not_overwritten(self):
        d = _bare_discovery()
        existing = CFBroadcast(bus_address="384102", pattern="switch_pair", outputs=[])
        d.discovered_cf_broadcasts = {"384102": existing}
        # Even if a (hypothetical) scene-mode record shared that address,
        # the unmatched-path classification wins.
        d._accumulated_command_mapping = {
            ("384102", 0, None): [
                _out("384102", "C9A5", 1, "M14 (Light scene on)"),
            ],
        }

        d._classify_cf_scenes_from_command_mapping()

        self.assertIs(d.discovered_cf_broadcasts["384102"], existing)

    def test_ir_records_group_by_channel_slot_not_receiver_base(self):
        """For IR records ``button_address`` is the receiver base (shared
        by every channel + physical button); ``ir_button_address`` is the
        per-channel slot. The CF must key on the slot, and the receiver's
        physical buttons (no IR slot, plain modes) must NOT be merged in
        or flagged."""
        d = _bare_discovery()
        d._accumulated_command_mapping = {
            # IR scene on channel 0D1C9E ("30B")
            ("0D1C80", 3, "30B"): [
                _out("0D1C80", "0E6C", 1, "M12 (Preset on)", ir_button="0D1C9E"),
                _out("0D1C80", "8394", 1, "M02 (Open)", ir_button="0D1C9E"),
            ],
            # physical buttons of the same receiver (no IR slot)
            ("0D1C80", 0, None): [
                _out("0D1C80", "0E6C", 1, "M01 (Dim on/off (2 buttons))"),
                _out("0D1C80", "0E6C", 2, "M01 (Dim on/off (2 buttons))"),
            ],
        }

        d._classify_cf_scenes_from_command_mapping()

        # Only the IR channel slot is a CF; the receiver base is not.
        self.assertEqual(set(d.discovered_cf_broadcasts), {"0D1C9E"})
        cf = d.discovered_cf_broadcasts["0D1C9E"]
        members = {(o.module_address, o.channel, o.mode) for o in cf.outputs}
        self.assertEqual(
            members,
            {("0E6C", 1, "M12 (Preset on)"), ("8394", 1, "M02 (Open)")},
        )
        # The M01 physical-button channels did not leak into the CF.
        self.assertTrue(all(o.mode != "M01 (Dim on/off (2 buttons))" for o in cf.outputs))

    def test_no_mapping_is_safe(self):
        d = _bare_discovery()
        d._classify_cf_scenes_from_command_mapping()
        self.assertEqual(d.discovered_cf_broadcasts, {})


class TestCompleteRunInvokesSceneClassifier(unittest.TestCase):
    """Placement regression: light-scene CFs sit on valid button
    addresses, so an install can finish discovery with an EMPTY
    ``_accumulated_unmatched``. The scene classifier must still run —
    it must NOT be gated behind the unmatched-only block that handles
    38xx broadcasts / RF cluster synthesis."""

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_scene_classified_with_empty_unmatched(self):
        d = NikobusDiscovery.__new__(NikobusDiscovery)
        d._accumulated_command_mapping = {
            ("0D1C9E", 3, None): [
                _out("0D1C9E", "0E6C", 1, "M12 (Preset on)"),
                _out("0D1C9E", "8394", 1, "M02 (Open)"),
            ],
        }
        d._accumulated_unmatched = set()  # <- the case the old guard skipped
        d.discovered_cf_broadcasts = {}
        d._button_data = {}
        d.discovered_devices = {}
        d._coordinator = MagicMock()
        d._cancel_inventory_timeout = MagicMock()
        d._emit_progress = AsyncMock()
        d.reset_state = MagicMock()

        with patch.object(
            discovery_mod, "_notify_discovery_finished", new=AsyncMock()
        ):
            self._run(d._complete_discovery_run(None))

        self.assertIn("0D1C9E", d.discovered_cf_broadcasts)
        self.assertEqual(
            d.discovered_cf_broadcasts["0D1C9E"].pattern, "light_scene"
        )


if __name__ == "__main__":
    unittest.main()
