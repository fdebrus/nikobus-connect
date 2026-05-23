"""Regression tests for ``ROLLER_T3_MAPPING`` + decoder integration.

The roller M06 ("Open with operating time") and M07 ("Close with
operating time") modes use a *paired* duration encoding for T1
— a long-press time and a short-press time, formatted as
``"<long> / <short>"``. Pre-existing code returned ``None`` for
T1 in these modes because the regular ``ROLLER_TIMER_MAPPING`` was
used as the single source.

This test pins:
1. The 16-entry ``ROLLER_T3_MAPPING`` table matches Niko's canonical
   ``S_DB_ROLLUIK_T3`` parameter table.
2. The shutter decoder picks ``T3`` for M06/M07 and ``T2`` for
   M01/M02/M03/M05 — i.e. mode-dependent T1 resolution works.
"""

from __future__ import annotations

from nikobus_connect.discovery.mapping import (
    ROLLER_T3_MAPPING,
    ROLLER_TIMER_MAPPING,
)
from nikobus_connect.discovery.shutter_decoder import _timer_value


# Canonical T3 paired-time table per Niko ParamBase KP=6
# (``S_DB_ROLLUIK_T3``). Format: ``<long-press-time> / <short-press-time>``.
EXPECTED_T3 = {
    0x0: "-  / 1s",
    0x1: "-  / 1s",
    0x2: "-  / 2s",
    0x3: "-  / 3s",
    0x4: "8s / 1s",
    0x5: "8s / 2s",
    0x6: "8s / 3s",
    0x7: "16s / 1s",
    0x8: "16s / 2s",
    0x9: "16s / 3s",
    0xA: "30s / 1s",
    0xB: "30s / 2s",
    0xC: "30s / 3s",
    0xD: "90s / 1s",
    0xE: "90s / 2s",
    0xF: "90s / 3s",
}


def test_t3_table_matches_niko_canonical() -> None:
    """All 16 slots of ROLLER_T3_MAPPING match the spec."""
    assert set(ROLLER_T3_MAPPING.keys()) == set(range(16))
    for nibble in range(16):
        assert ROLLER_T3_MAPPING[nibble] == EXPECTED_T3[nibble], (
            f"slot {nibble}: got {ROLLER_T3_MAPPING[nibble]!r}, "
            f"expected {EXPECTED_T3[nibble]!r}"
        )


def test_decoder_uses_t3_for_m06_and_m07() -> None:
    """``_timer_value`` returns the T3 paired-time label for M06/M07."""
    # mode_raw=0x05 → M06; mode_raw=0x06 → M07
    for mode_raw in (0x05, 0x06):
        for nibble, expected in EXPECTED_T3.items():
            t1, t2 = _timer_value(nibble, mode_raw)
            assert t1 == expected, (
                f"mode 0x{mode_raw:02X} nibble {nibble}: "
                f"expected T3 label {expected!r}, got {t1!r}"
            )
            assert t2 is None


def test_decoder_uses_regular_t2_for_other_modes() -> None:
    """M01/M02/M03/M05 still use ROLLER_TIMER_MAPPING (single value)."""
    for mode_raw in (0x00, 0x01, 0x02, 0x04):
        for nibble in (0, 1, 2, 5, 10, 15):
            t1, t2 = _timer_value(nibble, mode_raw)
            expected = ROLLER_TIMER_MAPPING.get(nibble, [None])[0]
            assert t1 == expected, (
                f"mode 0x{mode_raw:02X} nibble {nibble}: "
                f"expected T2 label {expected!r}, got {t1!r}"
            )


def test_decoder_no_mode_arg_keeps_old_behavior() -> None:
    """Calling ``_timer_value(t1_raw)`` without mode_raw uses T2 (back-compat)."""
    for nibble in (0, 1, 5, 10, 15):
        t1, _ = _timer_value(nibble)
        expected = ROLLER_TIMER_MAPPING.get(nibble, [None])[0]
        assert t1 == expected


def test_t3_table_groups_by_long_press_quartet() -> None:
    """T3 slots 4-15 form 4-slot quartets sharing a long-press prefix.

    Pattern: 4-6 share "8s", 7-9 share "16s", 10-12 share "30s",
    13-15 share "90s". This is a structural invariant of Niko's
    encoding scheme — pinning it so a future edit can't break the
    pattern by accident.
    """
    quartets = {
        ("8s", "16s", "30s", "90s")[i]: [ROLLER_T3_MAPPING[4 + i * 3 + j] for j in range(3)]
        for i in range(4)
    }
    for prefix, slots in quartets.items():
        for entry in slots:
            assert entry.startswith(prefix + " /"), (
                f"slot in {prefix!r} quartet should start with that prefix: {entry!r}"
            )
