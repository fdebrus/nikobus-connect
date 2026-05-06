"""Tests for paired-button link inference (M01 / M02 dimmer modes).

Nikobus dimmer M01 ("Dim on/off (2 buttons)") uses two physical keys per
dimmable output (one ON, one OFF) but the module only stores the link
record on one key. M02 ("Dim on/off (4 buttons)") uses four keys with
the record on the master (1A or 2A). Both require post-scan inference
to populate the peer keys' ``linked_modules``.
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import merge_linked_modules


def _store_with_master_record(
    physical_addr: str,
    channels: int,
    master_key: str,
    bus_addresses: dict[str, str],
    output: dict,
    module_address: str = "0E6C",
) -> dict:
    op_points: dict = {}
    for key, bus in bus_addresses.items():
        op_points[key] = {
            "bus_address": bus,
            "description": f"Push button {key} #N{bus}",
        }
    op_points[master_key]["linked_modules"] = [
        {"module_address": module_address, "outputs": [dict(output)]}
    ]
    return {
        "nikobus_button": {
            physical_addr: {
                "type": f"Button with {channels} Operation Points",
                "model": "05-346",
                "channels": channels,
                "description": f"Button with {channels} Operation Points #N{physical_addr}",
                "operation_points": op_points,
            }
        }
    }


# --- M01 (2 buttons) -----------------------------------------------------


def test_m01_mirrors_record_from_1a_to_1b():
    output = {
        "channel": 2,
        "mode": "M01 (Dim on/off (2 buttons))",
        "t1": None,
        "t2": None,
        "payload": "FFB4001100D00E61",
        "button_address": "1843B4",
    }
    store = _store_with_master_record(
        "1843B4",
        4,
        "1A",
        {"1A": "8B7086", "1B": "CB7086", "1C": "0B7086", "1D": "4B7086"},
        output,
    )

    merge_linked_modules(store, {})  # invokes the post-pass

    op_points = store["nikobus_button"]["1843B4"]["operation_points"]
    # 1A keeps its original record.
    assert op_points["1A"]["linked_modules"][0]["outputs"][0]["channel"] == 2
    # 1B now mirrors 1A.
    assert "linked_modules" in op_points["1B"]
    assert op_points["1B"]["linked_modules"][0]["module_address"] == "0E6C"
    assert op_points["1B"]["linked_modules"][0]["outputs"][0] == output
    # 1C and 1D — not in the 1A↔1B pair — are untouched.
    assert "linked_modules" not in op_points["1C"]
    assert "linked_modules" not in op_points["1D"]


def test_m01_mirrors_in_both_directions():
    """Source on 1B should mirror to 1A; pairing is bidirectional."""

    output = {
        "channel": 5,
        "mode": "M01 (Dim on/off (2 buttons))",
        "t1": None,
        "t2": None,
        "payload": "AAAA",
        "button_address": "1CFBA0",
    }
    store = _store_with_master_record(
        "1CFBA0",
        4,
        "1B",
        {"1A": "8177CE", "1B": "C177CE", "1C": "0177CE", "1D": "4177CE"},
        output,
    )

    merge_linked_modules(store, {})

    op_points = store["nikobus_button"]["1CFBA0"]["operation_points"]
    assert op_points["1A"]["linked_modules"][0]["outputs"][0] == output
    assert op_points["1B"]["linked_modules"][0]["outputs"][0] == output


def test_m01_per_output_filtering_does_not_mirror_other_modes():
    """A 1C op-point with both an M01 (2-button) dimmer record and a
    single-key dimmer record (M06) must mirror only the paired one."""

    paired = {
        "channel": 4,
        "mode": "M01 (Dim on/off (2 buttons))",
        "t1": None,
        "t2": None,
        "payload": "FFB400030050155F",
        "button_address": "17C554",
    }
    not_paired = {
        "channel": 2,
        "mode": "M06 (Off (eventually with operating time))",
        "t1": "1 s",
        "t2": None,
        "payload": "XX",
        "button_address": "17C554",
    }
    store = {
        "nikobus_button": {
            "17C554": {
                "type": "Bus push button, 4 control buttons",
                "model": "05-346",
                "channels": 4,
                "description": "Bus push button, 4 control buttons #N17C554",
                "operation_points": {
                    "1A": {"bus_address": "8AA8FA"},
                    "1B": {"bus_address": "CAA8FA"},
                    "1C": {
                        "bus_address": "0AA8FA",
                        "linked_modules": [
                            {
                                "module_address": "0E6C",
                                "outputs": [not_paired, paired],
                            }
                        ],
                    },
                    "1D": {"bus_address": "4AA8FA"},
                },
            }
        }
    }

    merge_linked_modules(store, {})

    one_d = store["nikobus_button"]["17C554"]["operation_points"]["1D"]
    assert "linked_modules" in one_d
    assert one_d["linked_modules"][0]["outputs"] == [paired]
    # The single-key M06 record did NOT propagate to the peer.
    for output in one_d["linked_modules"][0]["outputs"]:
        assert "M06" not in output["mode"]


def test_roller_m01_mirrors_between_up_and_down_keys():
    """Roller M01 ("Open - stop - close") is a 2-button mode: up-key
    opens, down-key closes, either key stops during movement. The module
    stores one record; the peer key needs inference."""

    # Up key (1A) holds the record; 1B (down key) should receive it.
    output = {
        "channel": 2,
        "mode": "M01 (Open - stop - close)",
        "t1": "50 s",
        "t2": None,
        "payload": "FF12E02C7234",
        "button_address": "0D1C80",
    }
    store = _store_with_master_record(
        "0D1C80",
        4,
        "1A",
        {"1A": "804E2C", "1B": "C04E2C", "1C": "004E2C", "1D": "404E2C"},
        output,
        module_address="9105",
    )

    merge_linked_modules(store, {})

    op_points = store["nikobus_button"]["0D1C80"]["operation_points"]
    assert op_points["1B"]["linked_modules"][0]["outputs"][0] == output
    # 1C/1D are not in the 1A↔1B pair; they stay untouched.
    assert "linked_modules" not in op_points["1C"]
    assert "linked_modules" not in op_points["1D"]


def test_roller_m02_open_only_is_single_key():
    """Roller M02 ("Open") and M03 ("Close") are single-key modes —
    they drive one direction, no pairing."""

    for mode in ["M02 (Open)", "M03 (Close)", "M04 (Stop)"]:
        output = {
            "channel": 3,
            "mode": mode,
            "t1": None, "t2": None, "payload": "X",
            "button_address": "999999",
        }
        store = _store_with_master_record(
            "999999",
            4,
            "1A",
            {"1A": "AAAA", "1B": "BBBB", "1C": "CCCC", "1D": "DDDD"},
            output,
            module_address="9105",
        )
        merge_linked_modules(store, {})
        op_points = store["nikobus_button"]["999999"]["operation_points"]
        for peer in ("1B", "1C", "1D"):
            assert "linked_modules" not in op_points[peer], (
                f"roller {mode!r} should NOT trigger mirroring to {peer}"
            )


def test_m01_mirror_dedupes_against_existing_peer_records():
    """Re-running the pass (or having a pre-existing peer record) must
    not duplicate."""

    output = {
        "channel": 2,
        "mode": "M01 (Dim on/off (2 buttons))",
        "t1": None,
        "t2": None,
        "payload": "AAAA",
        "button_address": "1843B4",
    }
    store = _store_with_master_record(
        "1843B4",
        4,
        "1A",
        {"1A": "8B7086", "1B": "CB7086", "1C": "0B7086", "1D": "4B7086"},
        output,
    )

    merge_linked_modules(store, {})
    merge_linked_modules(store, {})  # idempotent

    one_b_outputs = store["nikobus_button"]["1843B4"]["operation_points"]["1B"][
        "linked_modules"
    ][0]["outputs"]
    assert len(one_b_outputs) == 1


# --- M02 (4 buttons) -----------------------------------------------------


def test_m02_mirrors_from_1a_to_1b_1c_1d():
    output = {
        "channel": 7,
        "mode": "M02 (Dim on/off (4 buttons))",
        "t1": None,
        "t2": None,
        "payload": "BBBB",
        "button_address": "1E0D4A",
    }
    store = _store_with_master_record(
        "1E0D4A",
        4,
        "1A",
        {"1A": "94AC1E", "1B": "D4AC1E", "1C": "14AC1E", "1D": "54AC1E"},
        output,
    )

    merge_linked_modules(store, {})

    op_points = store["nikobus_button"]["1E0D4A"]["operation_points"]
    for key in ("1B", "1C", "1D"):
        assert "linked_modules" in op_points[key], (
            f"M02 should mirror to {key}"
        )
        assert op_points[key]["linked_modules"][0]["outputs"][0] == output


def test_m02_does_not_mirror_from_non_master_key():
    """If the record sits on 1B (not the master), don't infer the group —
    we'd be guessing the role assignment."""

    output = {
        "channel": 1,
        "mode": "M02 (Dim on/off (4 buttons))",
        "t1": None,
        "t2": None,
        "payload": "CCCC",
        "button_address": "1E0D4A",
    }
    store = _store_with_master_record(
        "1E0D4A",
        4,
        "1B",  # non-master
        {"1A": "94AC1E", "1B": "D4AC1E", "1C": "14AC1E", "1D": "54AC1E"},
        output,
    )

    merge_linked_modules(store, {})

    op_points = store["nikobus_button"]["1E0D4A"]["operation_points"]
    # 1B keeps its original; 1A/1C/1D get nothing.
    for key in ("1A", "1C", "1D"):
        assert "linked_modules" not in op_points[key]


def test_m02_on_8op_unit_uses_row_groups_independently():
    """On an 8-op wall, 1A masters 1A-1D and 2A masters 2A-2D
    independently. Records don't cross rows."""

    row1 = {
        "channel": 3,
        "mode": "M02 (Dim on/off (4 buttons))",
        "t1": None, "t2": None, "payload": "ROW1",
        "button_address": "1DF256",
    }
    row2 = {
        "channel": 4,
        "mode": "M02 (Dim on/off (4 buttons))",
        "t1": None, "t2": None, "payload": "ROW2",
        "button_address": "1DF256",
    }
    store = {
        "nikobus_button": {
            "1DF256": {
                "type": "Bus push button, 8 control buttons",
                "model": "05-349",
                "channels": 8,
                "description": "Bus push button, 8 control buttons #N1DF256",
                "operation_points": {
                    "1A": {
                        "bus_address": "BA93EE",
                        "linked_modules": [
                            {"module_address": "0E6C", "outputs": [dict(row1)]}
                        ],
                    },
                    "1B": {"bus_address": "FA93EE"},
                    "1C": {"bus_address": "3A93EE"},
                    "1D": {"bus_address": "7A93EE"},
                    "2A": {
                        "bus_address": "9A93EE",
                        "linked_modules": [
                            {"module_address": "0E6C", "outputs": [dict(row2)]}
                        ],
                    },
                    "2B": {"bus_address": "DA93EE"},
                    "2C": {"bus_address": "1A93EE"},
                    "2D": {"bus_address": "5A93EE"},
                },
            }
        }
    }

    merge_linked_modules(store, {})

    op_points = store["nikobus_button"]["1DF256"]["operation_points"]
    # Row 1: 1B/1C/1D get the row1 record (not row2).
    for key in ("1B", "1C", "1D"):
        assert op_points[key]["linked_modules"][0]["outputs"][0]["payload"] == "ROW1"
        assert (
            op_points[key]["linked_modules"][0]["outputs"][0]["channel"] == 3
        )
    # Row 2: 2B/2C/2D get the row2 record (not row1).
    for key in ("2B", "2C", "2D"):
        assert op_points[key]["linked_modules"][0]["outputs"][0]["payload"] == "ROW2"
        assert (
            op_points[key]["linked_modules"][0]["outputs"][0]["channel"] == 4
        )


# --- Negative coverage ---------------------------------------------------


def test_switch_m01_mirrors_between_on_and_off_keys():
    """Switch ``M01 (On / off)`` is functionally a 2-button pair: 1A
    turns the output on, paired 1B turns it off. The module stores the
    link record on one key only, same implicit-pairing pattern as
    dimmer M01 and roller M01.

    Regression: pre-0.4.1 the switch M01 mode was missing from the
    pair-match set, so the paired key stayed empty after scan even
    though it physically controls the output.
    """

    output = {
        "channel": 7,
        "mode": "M01 (On / off)",
        "t1": None,
        "t2": None,
        "payload": "FF16F0583160",
        "button_address": "180C56",
    }
    store = _store_with_master_record(
        "180C56",
        2,
        "1A",
        {"1A": "9A8C06", "1B": "DA8C06"},
        output,
        module_address="C9A5",
    )

    merge_linked_modules(store, {})

    op_points = store["nikobus_button"]["180C56"]["operation_points"]
    assert op_points["1B"]["linked_modules"][0]["outputs"][0] == output


# --- Negative coverage ---------------------------------------------------


def test_non_paired_modes_never_mirror():
    """Modes that don't represent a paired function must not mirror."""

    for mode in [
        "M02 (On, with operating time)",  # switch — separate key
        "M03 (Off, with operation time)",  # switch — separate key
        "M04 (Pushbutton)",  # switch — momentary single key
        "M02 (Open)",  # roller single-direction
        "M03 (Close)",  # roller single-direction
        "M03 (Light scene on/off)",  # dimmer M03 (explicitly excluded)
        "M06 (Off (eventually with operating time))",  # dimmer M06
        "M14 (Light scene on)",  # switch M14 — scene recall, single key
        "M15 (Light scene on / off)",  # switch M15 (explicitly excluded)
    ]:
        output = {
            "channel": 1,
            "mode": mode,
            "t1": None, "t2": None, "payload": "X",
            "button_address": "111111",
        }
        store = _store_with_master_record(
            "111111",
            4,
            "1A",
            {"1A": "AAAA", "1B": "BBBB", "1C": "CCCC", "1D": "DDDD"},
            output,
        )
        merge_linked_modules(store, {})
        op_points = store["nikobus_button"]["111111"]["operation_points"]
        for peer in ("1B", "1C", "1D"):
            assert "linked_modules" not in op_points[peer], (
                f"mode {mode!r} should NOT trigger mirroring to {peer}"
            )
