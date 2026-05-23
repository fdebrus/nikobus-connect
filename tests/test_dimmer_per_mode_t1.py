"""Regression tests for the per-dimmer-mode T1 resolution.

Niko's product database has FOUR distinct T1 parameter tables for the
dimmer module, dispatched by mode:

* M01/M02/M03 → DIMMER_T1_1  (3-value on/off step config)
* M05/M06     → DIMMER_T1_2  (4-value push time)
* M07         → DIMMER_T1_3  (16-value delayed-off duration)
* M11/M12     → DIMMER_AMOUNT_PERCENT (16-value preset dim level, as %)

The pre-change decoder used a positional ``DIMMER_TIMER_MAPPING``
that conflated these tables — preset modes returned voltages (the
old table's column 0), and timed modes returned ramp times (column
2) regardless of whether T1 was actually the ramp time. The
authoritative tables come from Niko's product.mdb ParamBase.
"""

from __future__ import annotations

from nikobus_connect.discovery.dimmer_decoder import _timer_value
from nikobus_connect.discovery.mapping import (
    DIMMER_AMOUNT_PERCENT,
    DIMMER_MODE_T1_LOOKUP,
    DIMMER_T1_1,
    DIMMER_T1_2,
    DIMMER_T1_3,
    DIMMER_T2_RAMP,
)


def test_dimmer_t1_step_table_matches_niko() -> None:
    """M01/M02/M03 use the 3-value on/off step table."""
    expected = ("On/off step 0", "On/off step 1", "On/off step 2-F")
    assert DIMMER_T1_1 == expected
    for mode in (0x00, 0x01, 0x02):
        assert DIMMER_MODE_T1_LOOKUP[mode] is DIMMER_T1_1


def test_dimmer_t1_push_table_matches_niko() -> None:
    """M05/M06 use the 4-value push-time table."""
    expected = ("0 s", "1 s", "2 s", "3 s")
    assert DIMMER_T1_2 == expected
    for mode in (0x04, 0x05):
        assert DIMMER_MODE_T1_LOOKUP[mode] is DIMMER_T1_2


def test_dimmer_t1_delayed_off_table_matches_niko() -> None:
    """M07 uses the 16-value delayed-off duration table."""
    assert len(DIMMER_T1_3) == 16
    assert DIMMER_T1_3[0] == "10 s"
    assert DIMMER_T1_3[-1] == "120 m"
    assert DIMMER_MODE_T1_LOOKUP[0x06] is DIMMER_T1_3


def test_dimmer_t1_amount_table_matches_niko_percentages() -> None:
    """M11/M12 use the 16-value dim-amount table (1% to 10%, in 0.5% steps).

    Pre-fix the dimmer decoder reported these as voltages
    ("1,0 V".."10,0 V"). The Niko PC software shows percentages —
    the official ``S_DB_DIMMER_AMOUNT`` table is keyed in percent.
    """
    assert len(DIMMER_AMOUNT_PERCENT) == 16
    assert DIMMER_AMOUNT_PERCENT[0] == "1%"
    assert DIMMER_AMOUNT_PERCENT[1] == "1.5%"
    assert DIMMER_AMOUNT_PERCENT[2] == "2%"
    assert DIMMER_AMOUNT_PERCENT[15] == "10%"
    assert DIMMER_MODE_T1_LOOKUP[0x08] is DIMMER_AMOUNT_PERCENT
    assert DIMMER_MODE_T1_LOOKUP[0x09] is DIMMER_AMOUNT_PERCENT


def test_dimmer_t2_ramp_table_matches_niko() -> None:
    """T2 ramp time uses the strictly monotonic 16-entry table."""
    expected = (
        "1 s", "2 s", "4 s", "6 s", "8 s", "10 s", "15 s", "20 s",
        "30 s", "40 s", "50 s", "1 m", "2 m", "3 m", "4 m", "5 m",
    )
    assert DIMMER_T2_RAMP == expected


def test_timer_value_picks_correct_t1_table_per_mode() -> None:
    """``_timer_value`` dispatches to the right T1 table for each mode."""
    # M01 with nibble 0 → step 0
    assert _timer_value(0x00, 0)[0] == "On/off step 0"
    # M01 with nibble 2 → step 2-F
    assert _timer_value(0x00, 2)[0] == "On/off step 2-F"
    # M05 with nibble 2 → "2 s" push time (NOT "4 s" ramp from old table)
    assert _timer_value(0x04, 2)[0] == "2 s"
    # M07 with nibble 13 → "60 m" (per S_DB_DIMMER_T1_3)
    assert _timer_value(0x06, 13)[0] == "60 m"
    # M11 with nibble 0 → "1%" (NOT "1,0 V" as pre-fix)
    assert _timer_value(0x08, 0)[0] == "1%"
    # M12 with nibble 15 → "10%"
    assert _timer_value(0x09, 15)[0] == "10%"


def test_timer_value_out_of_range_returns_none() -> None:
    """Out-of-range T1 nibble for a mode with a shorter table → None.

    e.g. M05/M06 only have 4 valid push-time slots (0-3). t1_raw=10
    is out of range — pre-fix the old positional table would have
    returned a (wrong) ramp-time value; post-fix it cleanly returns
    None.
    """
    # M05 with nibble 10 → out of range for the 4-value push-time table
    assert _timer_value(0x04, 10) == (None, None)
    # M01 with nibble 5 → out of range for the 3-value step table
    assert _timer_value(0x00, 5) == (None, None)


def test_timer_value_unparameterized_modes_return_none() -> None:
    """Modes that take no T1 parameter (M04/M08/M13/M14) → None."""
    for mode in (0x03, 0x07, 0x0A, 0x0B):
        assert _timer_value(mode, 5) == (None, None)


def test_timer_value_handles_t2_raw() -> None:
    """When t2_raw is provided, T2 is resolved against DIMMER_T2_RAMP."""
    # Mode doesn't matter for T2 — it's the ramp/fade time
    # used by every mode that supports it.
    assert _timer_value(0x00, 0, t2_raw=0)[1] == "1 s"
    assert _timer_value(0x00, 0, t2_raw=10)[1] == "50 s"
    assert _timer_value(0x00, 0, t2_raw=15)[1] == "5 m"


def test_timer_value_no_mode_returns_none() -> None:
    """No mode info → no resolution possible → (None, None)."""
    assert _timer_value(None, 5) == (None, None)
    assert _timer_value(None, None) == (None, None)
