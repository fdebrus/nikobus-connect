"""The decoder tables in ``mapping.py`` against the vendor's own tables.

``tests/fixtures/vendor_param_tables.json`` is extracted from a Nikobus
PC-software project file (``__niko__.mdb``): ``ParamBase`` — the value
lists behind every timer / level parameter — and, per output product and
link mode, the ``LinkModeBase`` row that says which parameter table the
mode's T1 and T2 come from. Every table transcribed into ``mapping.py``
and every mode → parameter dispatch the decoders apply must agree with
it, so a mislabelled value or a mode wired to the wrong table shows up
here rather than in a user's link records.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nikobus_connect.discovery import mapping
from nikobus_connect.discovery.switch_decoder import _timer_value as switch_timer

FIXTURE = Path(__file__).parent / "fixtures" / "vendor_param_tables.json"
VENDOR = json.loads(FIXTURE.read_text())
PARAMS = {int(k): v for k, v in VENDOR["param_base"].items()}
MODES = VENDOR["link_modes"]
DISPLAY = VENDOR["display_en"]  # token → English string of the software's language table

# ParamBase keys (KeyParamBase) of the tables the decoders transcribe.
KP_NONE = 1
KP_SWITCH_LONG_DELAY = 2
KP_SWITCH_SHORT_DELAY = 3
KP_SWITCH_PUSH_TIME = 4
KP_ROLLER_T2 = 5
KP_ROLLER_T3 = 6
KP_DIMMER_AMOUNT = 11
KP_DIMMER_T2 = 12
KP_DIMMER_T1_1 = 13
KP_DIMMER_T1_2 = 14
KP_DIMMER_T1_3 = 15


def _norm(label: str | None) -> str | None:
    """``"0,5 s"`` / ``"0.5s"`` / ``"1 m"`` / ``"1m"`` compare equal."""
    if label is None:
        return None
    return label.replace(",", ".").replace(" ", "").lower()


def _values(kp: int) -> list[str]:
    return PARAMS[kp]["values"]


# --- value tables ---------------------------------------------------------


def test_switch_timer_columns_are_the_vendor_tables() -> None:
    long_delay = [mapping.SWITCH_TIMER_MAPPING[i][0] for i in range(16)]
    short_delay = [mapping.SWITCH_TIMER_MAPPING[i][1] for i in range(16)]
    push_time = [mapping.SWITCH_TIMER_MAPPING[i][2] for i in range(4)]
    assert [_norm(v) for v in long_delay] == [_norm(v) for v in _values(KP_SWITCH_LONG_DELAY)]
    assert [_norm(v) for v in short_delay] == [_norm(v) for v in _values(KP_SWITCH_SHORT_DELAY)]
    assert [_norm(v) for v in push_time] == [_norm(v) for v in _values(KP_SWITCH_PUSH_TIME)]
    assert all(mapping.SWITCH_TIMER_MAPPING[i][2] is None for i in range(4, 16))


def test_roller_operating_time_is_the_vendor_table() -> None:
    vendor = _values(KP_ROLLER_T2)
    assert vendor[0] == "S_DB_PUSHTIME_OFF"  # rendered as "Turned off"
    ours = [mapping.ROLLER_TIMER_MAPPING[i][0] for i in range(16)]
    assert ours[0] == "Turned off"
    assert [_norm(v) for v in ours[1:]] == [_norm(v) for v in vendor[1:]]


def test_roller_paired_times_are_the_vendor_table() -> None:
    ours = [mapping.ROLLER_T3_MAPPING[i] for i in range(16)]
    assert [_norm(v) for v in ours] == [_norm(v) for v in _values(KP_ROLLER_T3)]


def test_dimmer_tables_are_the_vendor_tables() -> None:
    assert [_norm(v) for v in mapping.DIMMER_T2_RAMP] == [_norm(v) for v in _values(KP_DIMMER_T2)]
    assert [_norm(v) for v in mapping.DIMMER_T1_2] == [_norm(v) for v in _values(KP_DIMMER_T1_2)]
    assert [_norm(v) for v in mapping.DIMMER_T1_3] == [_norm(v) for v in _values(KP_DIMMER_T1_3)]
    # Tokens rendered through the software's language table, verbatim.
    assert [DISPLAY[t] for t in _values(KP_DIMMER_T1_1)] == list(mapping.DIMMER_T1_1)
    assert [DISPLAY[t] for t in _values(KP_DIMMER_AMOUNT)] == list(mapping.DIMMER_PRESET_LEVEL)
    assert DISPLAY["S_DB_PUSHTIME_OFF"] == mapping.ROLLER_TIMER_MAPPING[0][0]


def test_mode_codes_match_the_vendor_descriptions() -> None:
    """Every mode label carries the vendor's code, and the vendor has a
    display string for it (wording of our labels is ours, the code is not)."""
    for product, table in (
        ("05-000-02", mapping.SWITCH_MODE_MAPPING),
        ("05-001-02", mapping.ROLLER_MODE_MAPPING),
        ("05-007-02", mapping.DIMMER_MODE_MAPPING),
    ):
        family = {"05-000-02": "SCHAKEL", "05-001-02": "ROLLUIK", "05-007-02": "DIMMER"}[product]
        for label in table.values():
            code = label.split(" ", 1)[0]
            # The software's language table names every mode we decode …
            assert f"S_DB_DESC_{family}_M{int(code[1:])}" in DISPLAY, (
                f"{product} {code}: no display string in the vendor language table"
            )
            # … and this project's LinkModeBase carries the ones its
            # products offer (dimmer M13 / M14 belong to a later dimmer
            # product and are absent from this project database).
            row = MODES.get(f"{product}|{code}")
            if row is not None:
                assert row["description"].startswith("S_DB_DESC_")


# --- mode → parameter dispatch ---------------------------------------------


def _mode_index(table: dict[int, str], mode: str) -> int:
    for index, label in table.items():
        if label.startswith(mode + " "):
            return index
    raise AssertionError(f"mode {mode} not in {table}")


@pytest.mark.parametrize(
    "mode", [k.split("|")[1] for k in MODES if k.startswith("05-000-02|") and k.split("|")[1] != "M13"]
)
def test_switch_mode_reads_the_parameter_the_vendor_assigns(mode: str) -> None:
    """``switch_decoder._timer_value`` picks the column LinkModeBase names."""
    kp = MODES[f"05-000-02|{mode}"]["param1"]
    index = _mode_index(mapping.SWITCH_MODE_MAPPING, mode)
    column = {KP_SWITCH_LONG_DELAY: 0, KP_SWITCH_SHORT_DELAY: 1, KP_SWITCH_PUSH_TIME: 2}.get(kp)
    for nibble in range(16):
        t1, _t2 = switch_timer(index, nibble)
        if column is None:
            assert t1 is None, f"{mode} takes no parameter, got {t1!r}"
        else:
            assert t1 == mapping.SWITCH_TIMER_MAPPING[nibble][column]


@pytest.mark.parametrize("mode", [k.split("|")[1] for k in MODES if k.startswith("05-001-02|")])
def test_roller_mode_reads_the_parameter_the_vendor_assigns(mode: str) -> None:
    from nikobus_connect.discovery.shutter_decoder import _timer_value as roller_timer

    kp = MODES[f"05-001-02|{mode}"]["param1"]
    index = _mode_index(mapping.ROLLER_MODE_MAPPING, mode)
    for nibble in range(16):
        t1, _t2 = roller_timer(nibble, index)
        if kp == KP_ROLLER_T3:
            assert t1 == mapping.ROLLER_T3_MAPPING[nibble]
        elif kp == KP_ROLLER_T2:
            assert t1 == mapping.ROLLER_TIMER_MAPPING[nibble][0]
        else:
            assert t1 is None, f"{mode} takes no parameter, got {t1!r}"


@pytest.mark.parametrize("mode", [k.split("|")[1] for k in MODES if k.startswith("05-007-02|")])
def test_dimmer_mode_reads_the_parameter_the_vendor_assigns(mode: str) -> None:
    row = MODES[f"05-007-02|{mode}"]
    index = _mode_index(mapping.DIMMER_MODE_MAPPING, mode)
    table = mapping.DIMMER_MODE_T1_LOOKUP.get(index)
    expected = {
        KP_DIMMER_T1_1: mapping.DIMMER_T1_1,
        KP_DIMMER_T1_2: mapping.DIMMER_T1_2,
        KP_DIMMER_T1_3: mapping.DIMMER_T1_3,
        KP_DIMMER_AMOUNT: mapping.DIMMER_AMOUNT_PERCENT,
    }.get(row["param1"])
    assert table is expected, f"{mode}: T1 table {table} vs vendor KP {row['param1']}"
    # Every dimmer mode that takes a T2 takes the ramp table; none other.
    assert row["param2"] in (KP_NONE, KP_DIMMER_T2)
