"""Regression tests for CF activation broadcast classification.

PC-Logic emits Central Function activations on deterministic bus
addresses (``38 41 XX`` for switch-pair CFs, ``38 80 XX`` for
roller-pair CFs). Output modules carry normal link records pointing
back to those addresses; the per-module decoders see them as link
record SOURCES that don't resolve to any inventory button, and the
merge layer adds them to ``_accumulated_unmatched``.

These tests pin the classifier that pulls those CF addresses out of
the unmatched set, attaches their (module, channel, mode) members,
and surfaces them on ``NikobusDiscovery.discovered_cf_broadcasts``.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from nikobus_connect.discovery.discovery import (
    CFBroadcast,
    CFOutputMember,
    NikobusDiscovery,
    _classify_cf_pattern,
)


class TestCFPatternMatcher(unittest.TestCase):
    """``_classify_cf_pattern`` is the only address-shape gate before
    a candidate is consumed from the unmatched set."""

    def test_switch_pair_prefix_is_recognised(self):
        self.assertEqual(_classify_cf_pattern("384102"), "switch_pair")
        self.assertEqual(_classify_cf_pattern("384100"), "switch_pair")
        self.assertEqual(_classify_cf_pattern("3841FF"), "switch_pair")

    def test_roller_pair_prefix_is_recognised(self):
        self.assertEqual(_classify_cf_pattern("3880CA"), "roller_pair")
        self.assertEqual(_classify_cf_pattern("3880C8"), "roller_pair")
        self.assertEqual(_classify_cf_pattern("388000"), "roller_pair")

    def test_case_insensitive(self):
        self.assertEqual(_classify_cf_pattern("3880ca"), "roller_pair")
        self.assertEqual(_classify_cf_pattern("3841ab"), "switch_pair")

    def test_non_cf_addresses_return_none(self):
        # Real wall-button bus address
        self.assertIsNone(_classify_cf_pattern("1C4AA0"))
        # 52-key Easywave remote base
        self.assertIsNone(_classify_cf_pattern("0E31C0"))
        # Same 38xx prefix but wrong second byte
        self.assertIsNone(_classify_cf_pattern("38FFCA"))
        self.assertIsNone(_classify_cf_pattern("3800CA"))
        # Too short / too long
        self.assertIsNone(_classify_cf_pattern("3880C"))
        self.assertIsNone(_classify_cf_pattern("3880CAB"))
        # Non-hex chars
        self.assertIsNone(_classify_cf_pattern("3880GG"))
        # Non-string input
        self.assertIsNone(_classify_cf_pattern(None))


def _make_discovery_stub() -> NikobusDiscovery:
    """Build a NikobusDiscovery instance with only the fields the
    classifier touches. The full constructor needs a real coordinator
    + listener; the classifier only reads ``_accumulated_unmatched``,
    ``_accumulated_command_mapping``, and writes
    ``discovered_cf_broadcasts``, so we can assemble a stub directly."""
    d = NikobusDiscovery.__new__(NikobusDiscovery)
    d._accumulated_unmatched = set()
    d._accumulated_command_mapping = {}
    d.discovered_cf_broadcasts = {}
    return d


class TestCFBroadcastClassifier(unittest.TestCase):
    """End-to-end behaviour of
    ``_classify_cf_broadcasts_from_unmatched``."""

    def test_roller_pair_cf_with_two_modes_is_classified(self):
        """3880CA appears with M02 + M03 link records on roller channel 6
        of module 8CF5 → one CFBroadcast with 2 members."""
        d = _make_discovery_stub()
        d._accumulated_unmatched = {"3880CA"}
        d._accumulated_command_mapping = {
            ("3880CA", 0, None): [
                {"module_address": "8CF5", "channel": 6,
                 "mode": "M02 (Open - stop - close)", "t1": "30 s"},
                {"module_address": "8CF5", "channel": 6,
                 "mode": "M03 (Close)", "t1": "30 s"},
            ],
        }

        d._classify_cf_broadcasts_from_unmatched()

        self.assertIn("3880CA", d.discovered_cf_broadcasts)
        cf = d.discovered_cf_broadcasts["3880CA"]
        self.assertEqual(cf.pattern, "roller_pair")
        self.assertEqual(len(cf.outputs), 2)
        self.assertEqual({(o.module_address, o.channel, o.mode) for o in cf.outputs}, {
            ("8CF5", 6, "M02 (Open - stop - close)"),
            ("8CF5", 6, "M03 (Close)"),
        })
        # Consumed from unmatched.
        self.assertNotIn("3880CA", d._accumulated_unmatched)

    def test_switch_pair_alles_uit_multi_module_is_classified(self):
        """384102 sprays across 4 switch modules + a dimmer → one CFBroadcast
        with all unique (module, channel, mode) members."""
        d = _make_discovery_stub()
        d._accumulated_unmatched = {"384102"}
        d._accumulated_command_mapping = {
            ("384102", 0, None): [
                {"module_address": "81F6", "channel": 1, "mode": "M03"},
                {"module_address": "81F6", "channel": 2, "mode": "M03"},
                {"module_address": "C5C1", "channel": 1, "mode": "M06"},
                {"module_address": "C5C1", "channel": 2, "mode": "M06"},
            ],
        }

        d._classify_cf_broadcasts_from_unmatched()

        cf = d.discovered_cf_broadcasts["384102"]
        self.assertEqual(cf.pattern, "switch_pair")
        self.assertEqual(len(cf.outputs), 4)
        self.assertEqual({o.module_address for o in cf.outputs}, {"81F6", "C5C1"})

    def test_dedups_repeat_outputs_across_rescans(self):
        """Same (module, channel, mode) appearing twice in command_mapping
        (across multi-pass scans) becomes one member."""
        d = _make_discovery_stub()
        d._accumulated_unmatched = {"3880CA"}
        d._accumulated_command_mapping = {
            ("3880CA", 0, None): [
                {"module_address": "8CF5", "channel": 6, "mode": "M02"},
                {"module_address": "8CF5", "channel": 6, "mode": "M02"},
            ],
            ("3880CA", 1, None): [
                {"module_address": "8CF5", "channel": 6, "mode": "M02"},
            ],
        }

        d._classify_cf_broadcasts_from_unmatched()

        cf = d.discovered_cf_broadcasts["3880CA"]
        self.assertEqual(len(cf.outputs), 1)

    def test_non_cf_address_is_left_in_unmatched(self):
        """Real RF transmitter / unknown bus addresses must NOT be classified
        — they belong to the existing remote-transmitter cluster pass."""
        d = _make_discovery_stub()
        d._accumulated_unmatched = {"1C4AA0", "0E31D2"}
        d._accumulated_command_mapping = {
            ("1C4AA0", 0, None): [
                {"module_address": "C7C1", "channel": 1, "mode": "M01"},
            ],
        }

        d._classify_cf_broadcasts_from_unmatched()

        self.assertEqual(d.discovered_cf_broadcasts, {})
        # Originals stay in unmatched.
        self.assertEqual(d._accumulated_unmatched, {"1C4AA0", "0E31D2"})

    def test_cf_pattern_match_without_outputs_does_NOT_consume(self):
        """If an address matches the CF shape but has no link records
        pointing at it (e.g. accumulator artefact, parser glitch),
        leave it in unmatched — being unsafe is worse than being
        unclassified."""
        d = _make_discovery_stub()
        d._accumulated_unmatched = {"3880CA"}
        d._accumulated_command_mapping = {}  # no targets

        d._classify_cf_broadcasts_from_unmatched()

        self.assertEqual(d.discovered_cf_broadcasts, {})
        self.assertIn("3880CA", d._accumulated_unmatched)

    def test_mixed_cf_and_non_cf_unmatched_addresses(self):
        """Real-world case: accumulator has both CF broadcasts and
        unrecognised addresses — classify only the CFs, leave the
        rest for the RT pass."""
        d = _make_discovery_stub()
        d._accumulated_unmatched = {"3880CA", "3880CB", "1C4AA0", "DEAD42"}
        d._accumulated_command_mapping = {
            ("3880CA", 0, None): [
                {"module_address": "8CF5", "channel": 6, "mode": "M02"},
            ],
            ("3880CB", 0, None): [
                {"module_address": "8B9C", "channel": 5, "mode": "M02"},
                {"module_address": "8B9C", "channel": 5, "mode": "M03"},
            ],
            ("1C4AA0", 0, None): [
                {"module_address": "C7C1", "channel": 1, "mode": "M01"},
            ],
        }

        d._classify_cf_broadcasts_from_unmatched()

        self.assertEqual(set(d.discovered_cf_broadcasts), {"3880CA", "3880CB"})
        self.assertEqual(d._accumulated_unmatched, {"1C4AA0", "DEAD42"})

    def test_empty_inputs_are_noop(self):
        d = _make_discovery_stub()
        d._classify_cf_broadcasts_from_unmatched()
        self.assertEqual(d.discovered_cf_broadcasts, {})

    def test_real_user_dataset_jaccard_strong(self):
        """The labelled-dataset analysis showed each `38 80 XX` broadcast
        matching one CF pair (OP+NEER) at 100% Jaccard on (module,
        channel, mode). Replicate one concrete case from the
        Sluiskouter install: 3880CD ↔ Gordijnen 1,2 & 3 keuken
        OP/NEER (8CF5 ch1+2+3 × M02+M03)."""
        d = _make_discovery_stub()
        d._accumulated_unmatched = {"3880CD"}
        d._accumulated_command_mapping = {
            ("3880CD", 0, None): [
                {"module_address": "8CF5", "channel": 1, "mode": "M02 (Open - stop - close)"},
                {"module_address": "8CF5", "channel": 1, "mode": "M03 (Close)"},
                {"module_address": "8CF5", "channel": 2, "mode": "M02 (Open - stop - close)"},
                {"module_address": "8CF5", "channel": 2, "mode": "M03 (Close)"},
                {"module_address": "8CF5", "channel": 3, "mode": "M02 (Open - stop - close)"},
                {"module_address": "8CF5", "channel": 3, "mode": "M03 (Close)"},
            ],
        }

        d._classify_cf_broadcasts_from_unmatched()

        cf = d.discovered_cf_broadcasts["3880CD"]
        self.assertEqual(cf.pattern, "roller_pair")
        self.assertEqual(len(cf.outputs), 6)
        channels = {o.channel for o in cf.outputs}
        self.assertEqual(channels, {1, 2, 3})
        modes = {o.mode.split(" ")[0] for o in cf.outputs}
        self.assertEqual(modes, {"M02", "M03"})


if __name__ == "__main__":
    unittest.main()
