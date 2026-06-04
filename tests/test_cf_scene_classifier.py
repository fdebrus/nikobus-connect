"""Regression tests for light-scene Central Function classification.

The PC software's "MCF (Activate light scene / Central Function)" mode
covers CFs triggered by a real wall button or IR code (not a ``38xx``
PC-Logic broadcast). ``_classify_cf_scenes_from_command_mapping`` detects
these from the **merged button store**: it walks each button / IR
op-point and, if any of the op-point's ``linked_modules`` members uses a
light-scene / preset mode, emits a CF keyed on the op-point's own
``bus_address`` — the keyed wire address the bus actually emits (and that
activating the scene must broadcast).

Keying on the op-point ``bus_address`` is what makes per-key / per-IR-code
scenes split correctly: IR ``30A`` -> ``9E4E2C`` and ``30B`` -> ``DE4E2C``
are distinct op-points, so they become distinct scenes instead of
collapsing under the shared receiver base (``0D1C80``).
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


def _op(bus_address, *outputs):
    """Build an op-point dict. ``outputs``: ``(module, channel, mode)``."""
    by_mod: dict[str, list] = {}
    for mod, ch, mode in outputs:
        by_mod.setdefault(mod, []).append(
            {"channel": ch, "mode": mode, "t1": None, "t2": None}
        )
    return {
        "bus_address": bus_address,
        "linked_modules": [
            {"module_address": m, "outputs": o} for m, o in by_mod.items()
        ],
    }


def _bare_discovery(op_points=None) -> NikobusDiscovery:
    """A NikobusDiscovery carrying just a one-button store, bypassing
    __init__ (which needs a live coordinator)."""
    d = NikobusDiscovery.__new__(NikobusDiscovery)
    d._button_data = {
        "nikobus_button": {"0D1C80": {"operation_points": dict(op_points or {})}}
    }
    d.discovered_cf_broadcasts = {}
    return d


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
    def test_scene_keyed_on_wire_address_with_all_members(self):
        """An op-point with a light-scene member becomes a CF keyed on its
        bus_address, pulling in its switch/shutter members too."""
        d = _bare_discovery({
            "IR:30B": _op(
                "DE4E2C",
                ("0E6C", 1, "M04 (Light scene on)"),
                ("4707", 12, "M02 (On + Operating time)"),
                ("8394", 1, "M02 (Open)"),
            )
        })
        d._classify_cf_scenes_from_command_mapping()

        self.assertEqual(set(d.discovered_cf_broadcasts), {"DE4E2C"})
        cf = d.discovered_cf_broadcasts["DE4E2C"]
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

    def test_ir_codes_split_per_wire_address(self):
        """The headline fix: two IR codes on the same slot/receiver are
        distinct op-points → distinct scenes on their own wire addresses,
        not one merged receiver-base mega-CF."""
        d = _bare_discovery({
            "IR:30A": _op(
                "9E4E2C",
                ("0E6C", 1, "M12 (Preset on)"),
                ("0E6C", 2, "M06 (Off + Operating time)"),
            ),
            "IR:30B": _op(
                "DE4E2C",
                ("0E6C", 1, "M04 (Light scene on)"),
                ("8394", 1, "M02 (Open)"),
            ),
        })
        d._classify_cf_scenes_from_command_mapping()

        self.assertEqual(set(d.discovered_cf_broadcasts), {"9E4E2C", "DE4E2C"})
        a = {(o.module_address, o.channel, o.mode)
             for o in d.discovered_cf_broadcasts["9E4E2C"].outputs}
        b = {(o.module_address, o.channel, o.mode)
             for o in d.discovered_cf_broadcasts["DE4E2C"].outputs}
        self.assertEqual(
            a,
            {("0E6C", 1, "M12 (Preset on)"),
             ("0E6C", 2, "M06 (Off + Operating time)")},
        )
        self.assertEqual(
            b,
            {("0E6C", 1, "M04 (Light scene on)"), ("8394", 1, "M02 (Open)")},
        )

    def test_plain_roller_ir_code_is_not_a_scene(self):
        """A receiver with a scene code AND a plain roller code surfaces
        only the scene — the mega-merge is gone."""
        d = _bare_discovery({
            "IR:30A": _op("9E4E2C", ("0E6C", 1, "M12 (Preset on)")),
            "IR:14A": _op("9C4E2C", ("9105", 5, "M01 (Open - stop - close)")),
        })
        d._classify_cf_scenes_from_command_mapping()
        self.assertEqual(set(d.discovered_cf_broadcasts), {"9E4E2C"})

    def test_ordinary_button_is_not_a_cf(self):
        d = _bare_discovery({
            "1A": _op("8B7086", ("0E6C", 2, "M01 (Dim on/off (2 buttons))")),
        })
        d._classify_cf_scenes_from_command_mapping()
        self.assertEqual(d.discovered_cf_broadcasts, {})

    def test_existing_38xx_broadcast_is_not_overwritten(self):
        d = _bare_discovery({
            "IR:30B": _op("384102", ("C9A5", 1, "M14 (Light scene on)")),
        })
        existing = CFBroadcast(bus_address="384102", pattern="switch_pair", outputs=[])
        d.discovered_cf_broadcasts = {"384102": existing}
        d._classify_cf_scenes_from_command_mapping()
        self.assertIs(d.discovered_cf_broadcasts["384102"], existing)

    def test_members_deduped(self):
        d = _bare_discovery({
            "IR:30B": {
                "bus_address": "DE4E2C",
                "linked_modules": [{
                    "module_address": "0E6C",
                    "outputs": [
                        {"channel": 1, "mode": "M12 (Preset on)"},
                        {"channel": 1, "mode": "M12 (Preset on)"},  # dup
                    ],
                }],
            }
        })
        d._classify_cf_scenes_from_command_mapping()
        self.assertEqual(len(d.discovered_cf_broadcasts["DE4E2C"].outputs), 1)

    def test_op_point_without_bus_address_is_skipped(self):
        d = _bare_discovery({
            "1A": {"linked_modules": [
                {"module_address": "0E6C",
                 "outputs": [{"channel": 1, "mode": "M12 (Preset on)"}]}
            ]}  # no bus_address
        })
        d._classify_cf_scenes_from_command_mapping()
        self.assertEqual(d.discovered_cf_broadcasts, {})

    def test_empty_or_missing_button_data_is_safe(self):
        d = _bare_discovery({})
        d._classify_cf_scenes_from_command_mapping()
        self.assertEqual(d.discovered_cf_broadcasts, {})

        d2 = NikobusDiscovery.__new__(NikobusDiscovery)
        d2._button_data = None
        d2.discovered_cf_broadcasts = {}
        d2._classify_cf_scenes_from_command_mapping()
        self.assertEqual(d2.discovered_cf_broadcasts, {})


class TestCompleteRunInvokesSceneClassifier(unittest.TestCase):
    """Placement regression: light-scene CFs sit on real op-points, so an
    install can finish discovery with an EMPTY ``_accumulated_unmatched``.
    The classifier must still run — not be gated behind the unmatched-only
    block that handles 38xx broadcasts / RF cluster synthesis."""

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_scene_classified_with_empty_unmatched(self):
        d = NikobusDiscovery.__new__(NikobusDiscovery)
        d._button_data = {
            "nikobus_button": {
                "0D1C80": {
                    "operation_points": {
                        "IR:30B": _op(
                            "DE4E2C",
                            ("0E6C", 1, "M04 (Light scene on)"),
                            ("8394", 1, "M02 (Open)"),
                        )
                    }
                }
            }
        }
        d._accumulated_unmatched = set()  # the case the old guard skipped
        d.discovered_cf_broadcasts = {}
        d.discovered_devices = {}
        d._coordinator = MagicMock()
        d._cancel_inventory_timeout = MagicMock()
        d._emit_progress = AsyncMock()
        d.reset_state = MagicMock()

        with patch.object(
            discovery_mod, "_notify_discovery_finished", new=AsyncMock()
        ):
            self._run(d._complete_discovery_run(None))

        self.assertIn("DE4E2C", d.discovered_cf_broadcasts)
        self.assertEqual(
            d.discovered_cf_broadcasts["DE4E2C"].pattern, "light_scene"
        )


if __name__ == "__main__":
    unittest.main()
