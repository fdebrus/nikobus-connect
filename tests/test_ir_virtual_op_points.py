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
    _compute_ir_bus_address,
    find_ir_operation_point,
    find_operation_point,
    merge_linked_modules,
)


def _ir_receiver_store() -> dict:
    """A single IR receiver with the usual 4 wall op-points populated."""

    return {
        "nikobus_button": {
            "0D1C80": {
                "type": "IR Bus push button, 4 control buttons",
                "model": "05-348",
                "channels": 4,
                "description": "IR Bus push button, 4 control buttons #N0D1C80",
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
    # 0.4.3+: IR op-points now carry a computed bus_address so runtime
    # routing via find_operation_point works the same as wall keys.
    # 30A on 0D1C80 -> slot 0D1C9E, key_raw 1 (bank A), encoded 9E4E2C.
    assert ir_op["bus_address"] == "9E4E2C"
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
    # IR op-point carries both ir_code and a computed bus_address
    # (0.4.3+: runtime routing works via find_operation_point).
    assert ir_op["ir_code"] == "30A"
    assert ir_op["bus_address"] == "9E4E2C"


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


def test_find_operation_point_resolves_ir_op_points_by_bus_address():
    """0.4.3+: IR op-points carry a computed ``bus_address`` so the
    standard ``find_operation_point`` resolves them the same way it
    resolves wall keys. That's how HA routes runtime IR presses for
    free, without a second lookup helper."""

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
    hit_wall = find_operation_point(store, "88F3EC")
    assert hit_wall is not None
    assert hit_wall[1] == "1A"

    # IR bus_address resolves to the IR op-point; key_label is the
    # storage key ("IR:30A").
    hit_ir = find_operation_point(store, "9E4E2C")
    assert hit_ir is not None
    receiver, storage_key, op_point = hit_ir
    assert receiver == "0D1C80"
    assert storage_key == f"{IR_OP_POINT_PREFIX}30A"
    assert op_point["ir_code"] == "30A"

    # The IR receiver's own physical address is still not a bus
    # address for any op-point.
    assert find_operation_point(store, "0D1C80") is None


# ---------------------------------------------------------------------------
# bus_address computation (0.4.3+)
# ---------------------------------------------------------------------------


def test_compute_ir_bus_address_matches_captured_trace():
    """The one real-hardware trace we captured: pressing IR code ``10B``
    on receiver ``0D1C80`` emits ``#ND44E2C`` on the wire. Our
    deterministic encoding must produce that exact address.

    Regression anchor for the encoding: convert_nikobus_address on the
    slot + first-nibble shift by KEY_MAPPING_MODULE[4][key_index] where
    key_index = IR bank cycle inverse of the code's trailing letter.
    """

    assert _compute_ir_bus_address("0D1C80", "10B") == "D44E2C"


def test_compute_ir_bus_address_all_banks_on_one_slot_are_distinct():
    """All four banks (C/A/D/B) on the same channel produce four
    distinct 6-hex addresses. This is what lets a runtime ``#N…``
    frame uniquely identify an IR code."""

    addrs = {
        bank: _compute_ir_bus_address("0D1C80", f"17{bank}")
        for bank in "CADB"
    }
    assert len(set(addrs.values())) == 4
    # Sanity-check one pairing from the brute-force enumeration.
    assert addrs["C"] == "224E2C"  # key_raw=0, nibble shift 0
    assert addrs["A"] == "A24E2C"  # key_raw=1, nibble shift 8
    assert addrs["D"] == "624E2C"  # key_raw=2, nibble shift 4
    assert addrs["B"] == "E24E2C"  # key_raw=3, nibble shift C


def test_compute_ir_bus_address_alternate_receiver():
    """The second known IR receiver uses base byte 0xC0."""

    addr = _compute_ir_bus_address("0FFEC0", "05A")
    assert addr is not None
    assert len(addr) == 6


def test_compute_ir_bus_address_bad_inputs_return_none():
    """Malformed inputs must never raise — just return None so
    the merge loop can skip without crashing on junk data."""

    assert _compute_ir_bus_address("", "10B") is None
    assert _compute_ir_bus_address("0D1C80", "") is None
    assert _compute_ir_bus_address(None, "10B") is None  # type: ignore[arg-type]
    assert _compute_ir_bus_address("0D1C80", None) is None  # type: ignore[arg-type]
    assert _compute_ir_bus_address("0D1C80", "XXX") is None  # bad bank
    assert _compute_ir_bus_address("0D1C80", "40A") is None  # channel > 39
    assert _compute_ir_bus_address("0D1C80", "00A") is None  # channel < 1
    assert _compute_ir_bus_address("XYZ", "10B") is None  # bad receiver


def test_compute_ir_bus_address_never_collides_with_wall_keys_same_receiver():
    """On a realistic IR receiver, the wall-key bus addresses and the
    full grid of IR bus addresses must all be unique. If this ever
    fires we'd be misrouting presses between a wall key and an IR
    code."""

    receiver = "0D1C80"
    all_addrs: set[str] = set()

    # Wall-key addresses for this 4-channel IR receiver come from
    # convert_nikobus_address(receiver) + KEY_MAPPING[4] shifts.
    from nikobus_connect.discovery.protocol import convert_nikobus_address
    from nikobus_connect.discovery.mapping import KEY_MAPPING

    converted = convert_nikobus_address(receiver)
    orig_nib = int(converted[0], 16)
    for add_hex in KEY_MAPPING[4].values():
        add = int(add_hex, 16)
        new_nib = (orig_nib + add) & 0xF
        wall_addr = f"{new_nib:X}{converted[1:]}".upper()
        assert wall_addr not in all_addrs
        all_addrs.add(wall_addr)

    # Every IR code (channels 1..39, 4 banks each = 156 codes) on
    # that same receiver.
    for channel in range(1, 40):
        for bank in "CADB":
            code = f"{channel:02d}{bank}"
            ir_addr = _compute_ir_bus_address(receiver, code)
            assert ir_addr is not None
            assert ir_addr not in all_addrs, (
                f"collision: IR code {code} on {receiver} matches an "
                f"existing address in {all_addrs}"
            )
            all_addrs.add(ir_addr)

    # 4 wall + 156 IR = 160 unique 6-hex addresses on one receiver.
    assert len(all_addrs) == 160


def test_ir_op_point_bus_address_survives_rediscovery():
    """bus_address is discovery-owned + deterministic, so it stays
    stable across re-discovery. Confirm it matches the computed value
    even if the user had renamed description in between."""

    store = _ir_receiver_store()
    mapping = {
        ("0D1C80", 3, "10B"): [
            {
                "module_address": "C9A5",
                "channel": 2,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "P",
                "button_address": "0D1C80",
                "ir_code": "10B",
            }
        ]
    }
    merge_linked_modules(store, mapping)

    op_points = store["nikobus_button"]["0D1C80"]["operation_points"]
    ir_key = f"{IR_OP_POINT_PREFIX}10B"
    assert op_points[ir_key]["bus_address"] == "D44E2C"

    # User rename.
    op_points[ir_key]["description"] = "Garage door remote, open"

    # Re-discovery.
    merge_linked_modules(store, mapping)
    assert op_points[ir_key]["description"] == "Garage door remote, open"
    assert op_points[ir_key]["bus_address"] == "D44E2C"


def test_ir_op_point_heals_missing_bus_address_on_rediscovery():
    """0.4.2-era store entries without bus_address must pick one up on
    the next discovery, so callers don't need a separate migration."""

    store = {
        "nikobus_button": {
            "0D1C80": {
                "type": "IR Bus push button, 4 control buttons",
                "model": "05-348",
                "channels": 4,
                "description": "IR Bus push button, 4 control buttons #N0D1C80",
                "operation_points": {
                    "1A": {
                        "bus_address": "88F3EC",
                        "description": "Push button 1A #N88F3EC",
                    },
                    # Legacy 0.4.2-shaped IR op-point: no bus_address.
                    f"{IR_OP_POINT_PREFIX}10B": {
                        "ir_code": "10B",
                        "description": "Garage door remote, open",
                    },
                },
            }
        }
    }

    mapping = {
        ("0D1C80", 3, "10B"): [
            {
                "module_address": "C9A5",
                "channel": 2,
                "mode": "M01 (On / off)",
                "t1": None,
                "t2": None,
                "payload": "P",
                "button_address": "0D1C80",
                "ir_code": "10B",
            }
        ]
    }
    merge_linked_modules(store, mapping)

    ir_op = store["nikobus_button"]["0D1C80"]["operation_points"][
        f"{IR_OP_POINT_PREFIX}10B"
    ]
    assert ir_op["bus_address"] == "D44E2C"
    # User description preserved.
    assert ir_op["description"] == "Garage door remote, open"


# ---------------------------------------------------------------------------
# add_to_command_mapping: mapping-key shape for IR records (0.4.3 bug fix)
# ---------------------------------------------------------------------------


def test_add_to_command_mapping_keys_ir_record_on_receiver_address():
    """Real discovery decoder path: for an IR record, push_button_address
    is the post-shift wire address (e.g. 'D44E2C'), which does NOT
    start with an IR receiver prefix, so the mapping key can't use it
    as the first element without losing the IR route at merge time.
    0.4.3 switches to the receiver's physical base (from the pre-shift
    button_address) whenever the record is IR-flagged.
    """

    from nikobus_connect.discovery.discovery import add_to_command_mapping

    mapping: dict = {}
    # Simulates what a switch/roller/dimmer decoder hands off for an IR
    # record targeting 0D1C80 code 10B (captured-trace fixture).
    decoded = {
        "push_button_address": "D44E2C",  # post-shift wire form
        "button_address": "0D1C8A",  # pre-shift slot, 0D1C80+0x0A (ch10)
        "key_raw": 3,  # bank B
        "channel": 2,
        "M": "M01 (On / off)",
        "T1": None,
        "T2": None,
        "payload": "P",
    }
    ir_receiver_lookup = {"0D1C": 0x80}

    add_to_command_mapping(
        mapping, decoded, "C9A5", ir_receiver_lookup=ir_receiver_lookup
    )

    # Exactly one entry; key's first element MUST be the receiver
    # ('0D1C80'), not the encoded wire address ('D44E2C').
    assert len(mapping) == 1
    (mapping_addr, key_raw, ir_code), outputs = next(iter(mapping.items()))
    assert mapping_addr == "0D1C80"
    assert ir_code == "10B"
    assert key_raw == 3


def test_add_to_command_mapping_keeps_wall_records_on_shifted_address():
    """Wall records are unaffected: their mapping key stays the
    post-shift wire address so ``find_operation_point`` still matches."""

    from nikobus_connect.discovery.discovery import add_to_command_mapping

    mapping: dict = {}
    decoded = {
        "push_button_address": "804E2C",  # wall-key 1A shift of 0D1C80
        "button_address": "0D1C80",
        "key_raw": 1,
        "channel": 2,
        "M": "M01 (On / off)",
        "T1": None,
        "T2": None,
        "payload": "P",
    }
    # Note: even though button_address is an IR receiver base, there's
    # no ir_slot (it IS the base), so split_ir_button_address returns
    # (0D1C80, None, None) -> not flagged as IR -> wall-path preserved.
    ir_receiver_lookup = {"0D1C": 0x80}

    add_to_command_mapping(
        mapping, decoded, "C9A5", ir_receiver_lookup=ir_receiver_lookup
    )

    assert len(mapping) == 1
    (mapping_addr, _key_raw, ir_code), _outputs = next(iter(mapping.items()))
    assert mapping_addr == "804E2C"
    assert ir_code is None
