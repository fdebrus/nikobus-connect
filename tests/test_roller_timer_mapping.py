"""Regression test for ROLLER_TIMER_MAPPING.

Pre-correction the table had a duplicate ``"6 s"`` at index 6 that
shifted every subsequent slot down by one. A roller with
``t1_raw=14`` (configured operating time "60 s") displayed "50 s" in
HA, and ``t1_raw=15`` ("90 s") displayed "60 s".

The canonical table is Niko's own ``S_DB_ROLLUIK_T2`` parameter table
(extracted from product.mdb, the Niko PC-software master catalogue).
"""

from __future__ import annotations

from nikobus_connect.discovery.mapping import ROLLER_TIMER_MAPPING


# The canonical T1-nibble → operating-time mapping per Niko's
# product database. Pinning the full 16-entry table here so any
# future edit that re-introduces the off-by-one shift fails loudly.
EXPECTED_ROLLER_T1 = {
    0: "Turned off",
    1: "0,4 s (impuls)",
    2: "6 s",
    3: "8 s",
    4: "10 s",
    5: "12 s",
    6: "14 s",   # post-fix (was "6 s" duplicate before)
    7: "16 s",   # post-fix
    8: "18 s",
    9: "20 s",
    10: "25 s",
    11: "30 s",
    12: "40 s",
    13: "50 s",
    14: "60 s",  # was "50 s" before fix
    15: "90 s",  # was "60 s" before fix; index 16 was extra "90 s"
}


def test_roller_timer_mapping_matches_niko_authoritative() -> None:
    """Every slot 0..15 resolves to the Niko-canonical operating time."""
    for nibble in range(16):
        entry = ROLLER_TIMER_MAPPING.get(nibble)
        assert entry is not None, f"missing entry at nibble {nibble}"
        assert entry[0] == EXPECTED_ROLLER_T1[nibble], (
            f"nibble {nibble}: got {entry[0]!r}, expected {EXPECTED_ROLLER_T1[nibble]!r}"
        )


def test_roller_timer_mapping_is_exactly_16_entries() -> None:
    """T1 is a 4-bit nibble — exactly 16 values, no more, no less."""
    assert set(ROLLER_TIMER_MAPPING.keys()) == set(range(16)), (
        "ROLLER_TIMER_MAPPING must contain exactly nibbles 0..15"
    )


def test_no_duplicate_operating_times_in_post_zero_slots() -> None:
    """Slot 6 onwards must not repeat earlier values.

    The pre-fix table had two "6 s" entries (slot 2 and slot 6). The
    Niko spec has no such duplication — every slot from index 1
    onwards is a unique operating time.
    """
    times = [ROLLER_TIMER_MAPPING[n][0] for n in range(1, 16)]
    assert len(times) == len(set(times)), (
        f"duplicate operating times present: {times}"
    )
