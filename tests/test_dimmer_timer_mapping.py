"""Regression test for ``DIMMER_TIMER_MAPPING`` col[2] (T2 ramp time).

The third column of ``DIMMER_TIMER_MAPPING`` holds the per-mode ramp
time for dimmer modules. Pre-fix, slot 10 contained ``"1 m"`` and
slot 11 contained ``"90 s"`` — i.e. those two values were swapped
relative to Niko's authoritative ``S_DB_DIMMER_T2`` table, which is
strictly monotonic:

    ..., 30 s, 40 s, 50 s, 1 m, 2 m, 3 m, ...

The value ``"90 s"`` is not in the official ramp-time table at all
— it was a leftover from a different parameter table that got
mis-merged into this column.
"""

from __future__ import annotations

from nikobus_connect.discovery.mapping import DIMMER_TIMER_MAPPING


# Canonical T2 ramp-time table per Niko ParamBase ``S_DB_DIMMER_T2``.
# This is the strict 16-entry monotonic sequence used by every dimmer
# mode (M01-M14) for ramp / fade duration.
EXPECTED_DIMMER_T2 = [
    "1 s",
    "2 s",
    "4 s",
    "6 s",
    "8 s",
    "10 s",
    "15 s",
    "20 s",
    "30 s",
    "40 s",
    "50 s",    # slot 10 — was "1 m" before fix
    "1 m",     # slot 11 — was "90 s" before fix
    "2 m",
    "3 m",
    "4 m",
    "5 m",
]


def test_dimmer_t2_col2_matches_niko_canonical() -> None:
    """Every slot 0..15 of DIMMER_TIMER_MAPPING col[2] matches Niko spec."""
    for nibble in range(16):
        entry = DIMMER_TIMER_MAPPING.get(nibble)
        assert entry is not None, f"missing entry at nibble {nibble}"
        assert entry[2] == EXPECTED_DIMMER_T2[nibble], (
            f"slot {nibble}: got col[2]={entry[2]!r}, "
            f"expected {EXPECTED_DIMMER_T2[nibble]!r}"
        )


def test_dimmer_t2_is_monotonic() -> None:
    """Niko's T2 ramp times always increase with nibble value.

    The pre-fix swap broke this invariant — slot 10 was ``"1 m"``
    (= 60 s) but slot 11 was ``"90 s"`` (= 90 s), which fits the
    sequence numerically but not the official table.
    """
    durations_s = [
        _parse_to_seconds(DIMMER_TIMER_MAPPING[n][2]) for n in range(16)
    ]
    for i in range(1, 16):
        assert durations_s[i] > durations_s[i - 1], (
            f"non-monotonic at slot {i}: {durations_s[i-1]} s → {durations_s[i]} s"
        )


def test_dimmer_t2_does_not_contain_90s() -> None:
    """``"90 s"`` is not in the official Niko T2 table.

    Pinning this so the leftover string can't sneak back in via copy/paste.
    """
    col2_values = [DIMMER_TIMER_MAPPING[n][2] for n in range(16)]
    assert "90 s" not in col2_values, (
        f"'90 s' is not a valid Niko T2 ramp value but is in col[2]: {col2_values}"
    )


def _parse_to_seconds(label: str) -> int:
    """Parse a Niko duration label like ``'30 s'`` / ``'1 m'`` → seconds."""
    parts = label.strip().split()
    value = float(parts[0].replace(",", "."))
    unit = parts[1] if len(parts) > 1 else "s"
    if unit == "s":
        return int(value)
    if unit == "m":
        return int(value * 60)
    raise ValueError(f"unhandled unit in label {label!r}")
