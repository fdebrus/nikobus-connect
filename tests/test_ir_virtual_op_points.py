"""Tests for IR codes surfacing as virtual op-points on an IR receiver.

IR codes arrive during module-config scans as ``command_mapping`` keys of
shape ``(push_button_address, key_raw, ir_code)``. When ``ir_code`` is
present, the resulting link lands on ``operation_points["IR:{code}"]``
on the physical IR receiver — a sibling of the receiver's wall keys
(``1A``-``1D``) — rather than collapsing onto one of the wall keys.
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import (
    IR_OP_POINT_PREFIX,
    find_ir_operation_point,
    find_operation_point,
    merge_linked_modules,
)


def _ir_receiver_store() -> dict:
    """A single IR receiver with the usual 4 wall op-points populated."""

    return {
        "nikobus_button": {
            "0D1C80": {
                "type": "IR Button with 4 Operation Points",
                "model": "05-348",
                "channels": 4,
                "description": "IR Button with 4 Operation Points #N0D1C80",
                "operation_points": {
                    "1A": {
                        "bus_address": "88F3EC",
                        "description": "Push button 1A #N88F3EC",
                    },
                    "1B": {
                        "bus_address": "C8F3EC",
                        "description": "Push button 1B #NC8F3EC",
                    },
                    "1C": {
                        "bus_address": "08F3EC",
                        "description": "Push button 1C #N08F3EC",
                    },
                    "1D": {
                        "bus_address": "48F3EC",
                        "description": "Push button 1D #N48F3EC",
                    },
                },
            }
        }
    }


def test_ir_code_lands_on_virtual_op_point_not_wall_key():
    """An IR record lands on ``IR:30A``, NOT on the receiver's wall keys."""

    store = _ir_receiver_store()

    # mapping key carries the ir_code in the 3rd slot
    mapping = {
        ("0D1C80", 1, "30A"): [
            {
                "module_address": "C9A5",
                "channel": 6,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "3C1555D863B9",
                "button_address": "0D1C80",
                "ir_code": "30A",
            }
        ]
    }

    merge_linked_modules(store, mapping)

    op_points = store["nikobus_button"]["0D1C80"]["operation_points"]

    # Wall keys are untouched.
    for wall_key in ("1A", "1B", "1C", "1D"):
        assert "linked_modules" not in op_points[wall_key], (
            f"IR record leaked onto wall key {wall_key}"
        )

    # Virtual op-point exists under the prefixed key.
    ir_key = f"{IR_OP_POINT_PREFIX}30A"
    assert ir_key in op_points
    ir_op = op_points[ir_key]
    assert ir_op["ir_code"] == "30A"
    assert ir_op["description"] == "IR code 30A #I30A"
    assert "bus_address" not in ir_op
    assert ir_op["linked_modules"][0]["module_address"] == "C9A5"
    assert ir_op["linked_modules"][0]["outputs"][0]["channel"] == 6


def test_ir_op_point_key_never_collides_with_wall_key():
    """Hostile IR code that looks like a wall key (``1A``) still gets
    prefixed, so the wall op-point is never clobbered."""

    store = _ir_receiver_store()
    mapping = {
        ("0D1C80", 1, "1A"): [
            {
                "module_address": "4707",
                "channel": 1,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "FF13F060BC60",
                "button_address": "0D1C80",
                "ir_code": "1A",
            }
        ]
    }
    merge_linked_modules(store, mapping)

    op_points = store["nikobus_button"]["0D1C80"]["operation_points"]
    # Wall 1A survived untouched.
    assert op_points["1A"]["bus_address"] == "88F3EC"
    assert "linked_modules" not in op_points["1A"]
    # IR 1A got its own prefixed slot.
    assert f"{IR_OP_POINT_PREFIX}1A" in op_points
    assert op_points[f"{IR_OP_POINT_PREFIX}1A"]["ir_code"] == "1A"


def test_ir_op_points_sit_after_wall_keys_in_insertion_order():
    """Dict insertion order puts IR keys after wall keys, matching UI
    expectations — wall first (1A..1D), then IR:30A, IR:4B."""

    store = _ir_receiver_store()
    mapping = {
        ("0D1C80", 1, "30A"): [
            {
                "module_address": "C9A5",
                "channel": 6,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "AA",
                "button_address": "0D1C80",
                "ir_code": "30A",
            }
        ],
        ("0D1C80", 1, "4B"): [
            {
                "module_address": "4707",
                "channel": 3,
                "mode": "M05 (Impulse)",
                "t1": None,
                "t2": None,
                "payload": "BB",
                "button_address": "0D1C80",
                "ir_code": "4B",
            }
        ],
    }
    merge_linked_modules(store, mapping)

    keys = list(store["nikobus_button"]["0D1C80"]["operation_points"].keys())
    assert keys[:4] == ["1A", "1B", "1C", "1D"]
    assert set(keys[4:]) == {f"{IR_OP_POINT_PREFIX}30A", f"{IR_OP_POINT_PREFIX}4B"}


def test_ir_op_point_variable_length_codes():
    """IR codes can be any short string; the storage shape handles all of them."""

    store = _ir_receiver_store()
    codes = ["A", "30A", "17B2", "12345"]
    mapping = {
        ("0D1C80", 1, code): [
            {
                "module_address": "C9A5",
                "channel": 1,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": f"P{code}",
                "button_address": "0D1C80",
                "ir_code": code,
            }
        ]
        for code in codes
    }
    merge_linked_modules(store, mapping)

    op_points = store["nikobus_button"]["0D1C80"]["operation_points"]
    for code in codes:
        key = f"{IR_OP_POINT_PREFIX}{code}"
        assert key in op_points
        assert op_points[key]["ir_code"] == code


def test_ir_op_point_preserves_user_description_on_rediscovery():
    """User renames ``IR:30A`` — re-discovery must not clobber it."""

    store = _ir_receiver_store()
    mapping = {
        ("0D1C80", 1, "30A"): [
            {
                "module_address": "C9A5",
                "channel": 6,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "P",
                "button_address": "0D1C80",
                "ir_code": "30A",
            }
        ]
    }
    merge_linked_modules(store, mapping)

    op_points = store["nikobus_button"]["0D1C80"]["operation_points"]
    ir_key = f"{IR_OP_POINT_PREFIX}30A"
    op_points[ir_key]["description"] = "Living room remote, power button"

    merge_linked_modules(store, mapping)
    assert (
        op_points[ir_key]["description"]
        == "Living room remote, power button"
    )


def test_ir_op_point_merge_is_idempotent():
    """Second identical merge is a no-op for IR records too."""

    store = _ir_receiver_store()
    mapping = {
        ("0D1C80", 1, "30A"): [
            {
                "module_address": "C9A5",
                "channel": 6,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "P",
                "button_address": "0D1C80",
                "ir_code": "30A",
            }
        ]
    }
    u1, la1, oa1 = merge_linked_modules(store, mapping)
    assert (u1, la1, oa1) == (1, 1, 1)

    u2, la2, oa2 = merge_linked_modules(store, mapping)
    assert (u2, la2, oa2) == (0, 0, 0)


def test_ir_and_wall_coexist_under_same_receiver():
    """Wall-key records and IR-code records both land under the same
    receiver, each on their own op-point."""

    store = _ir_receiver_store()
    mapping = {
        # A wall press on the receiver's 1A (no ir_code).
        ("88F3EC", 1, None): [
            {
                "module_address": "0E6C",
                "channel": 1,
                "mode": "M01 (Dim on/off (2 buttons))",
                "t1": None,
                "t2": None,
                "payload": "FFB4001000087234",
                "button_address": "0D1C80",
            }
        ],
        # An IR press via code 30A.
        ("0D1C80", 1, "30A"): [
            {
                "module_address": "C9A5",
                "channel": 6,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "3C1555D863B9",
                "button_address": "0D1C80",
                "ir_code": "30A",
            }
        ],
    }
    merge_linked_modules(store, mapping)

    op_points = store["nikobus_button"]["0D1C80"]["operation_points"]
    # Wall 1A has the wall link (mirrored to 1B by dimmer-M01 paired
    # inference — that's fine, not our concern here).
    assert op_points["1A"]["linked_modules"][0]["module_address"] == "0E6C"
    # IR 30A has the IR link and nothing else.
    ir_op = op_points[f"{IR_OP_POINT_PREFIX}30A"]
    assert ir_op["linked_modules"][0]["module_address"] == "C9A5"
    # IR op-point carries ir_code, no bus_address.
    assert ir_op["ir_code"] == "30A"
    assert "bus_address" not in ir_op


def test_find_ir_operation_point_lookup():
    """Runtime lookup by receiver + IR code."""

    store = _ir_receiver_store()
    mapping = {
        ("0D1C80", 1, "30A"): [
            {
                "module_address": "C9A5",
                "channel": 6,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "P",
                "button_address": "0D1C80",
                "ir_code": "30A",
            }
        ]
    }
    merge_linked_modules(store, mapping)

    hit = find_ir_operation_point(store, "0D1C80", "30A")
    assert hit is not None
    receiver, storage_key, op_point = hit
    assert receiver == "0D1C80"
    assert storage_key == f"{IR_OP_POINT_PREFIX}30A"
    assert op_point["ir_code"] == "30A"

    # Missing receiver or unknown code -> None.
    assert find_ir_operation_point(store, "DEADBE", "30A") is None
    assert find_ir_operation_point(store, "0D1C80", "ZZZ") is None


def test_find_operation_point_does_not_surface_ir_op_points():
    """``find_operation_point`` resolves by ``bus_address``; IR op-points
    have no bus_address and must not accidentally match."""

    store = _ir_receiver_store()
    mapping = {
        ("0D1C80", 1, "30A"): [
            {
                "module_address": "C9A5",
                "channel": 6,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "P",
                "button_address": "0D1C80",
                "ir_code": "30A",
            }
        ]
    }
    merge_linked_modules(store, mapping)

    # Wall address still resolves to its wall op-point.
    hit = find_operation_point(store, "88F3EC")
    assert hit is not None
    assert hit[1] == "1A"

    # The IR receiver's physical address is not a bus address for any
    # op-point; the lookup stays None.
    assert find_operation_point(store, "0D1C80") is None
