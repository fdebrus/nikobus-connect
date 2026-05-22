"""Resolve 05-312 Easywave 52-key BP-cell links to op-points.

Background. 0.14.0 made the 05-312 materialise correctly in the
button store (one device, 52 op-points with the right bus
addresses). What it did *not* do was teach
``_resolve_operation_point`` how to follow a BP-cell reference
back to one of those op-points.

The BP-cell encoding is different from the broadcast bus
encoding. When a module's link table stores a reference to the
05-312, the ``button_address`` field is ``physical + offset``
with ``offset ∈ [0, 32)`` — not one of the 52 broadcast first
bytes from ``EASYWAVE_52_KEY_MAPPING``. So the existing resolver
paths (direct physical, +1 sibling, ``bus_to_op`` index,
IR-slot) all missed, and the link silently dropped at merge.

0.15.0 adds a deterministic slot decoder::

    offset = button_address - physical_base
        bits 4..3 → channel (1..4)
        bits 2..0 → slot
            0..2 → rocker X-(slot+1)AB
            3    → X-4AB if key=1 else X-5AB
            4    → channel master rocker X-AB
            5    → channel C button X-C
            6,7  → unused

The test fixtures pin every (button, key, label) tuple actually
observed on the diagnostic install, including the 4/5 shared-slot
disambiguation by key bit and the channel master rocker that
drives all five channel-N outputs.
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import (
    _build_bus_to_op_index,
    _build_easywave_52_lookup,
    _build_ir_base_lookup,
    _resolve_easywave_52,
    _resolve_operation_point,
    merge_linked_modules,
)
from nikobus_connect.discovery.fileio import merge_discovered_buttons
from nikobus_connect.discovery.mapping import KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address


# ---------------------------------------------------------------------------
# Fixture: materialise the 05-312 at physical 0E31C0 (the diagnostic install)
# ---------------------------------------------------------------------------


def _materialise_05_312(physical: str = "0E31C0") -> dict:
    """Run ``merge_discovered_buttons`` against a single 05-312 so the
    button store carries the same 52 op-points the live discovery
    would produce. Avoids hand-building the op-point table in the
    test — anything 0.14.0's merge produces will be visible here."""

    button_data: dict = {"nikobus_button": {}}
    discovered = {
        physical: {
            "address": physical,
            "category": "Button",
            "description": "Easywave hand-held RF transmitter, 52 operation points",
            "device_type": "3D",
            "model": "05-312",
            "channels": 52,
            "channels_count": 52,
        }
    }
    merge_discovered_buttons(
        button_data, discovered, KEY_MAPPING, convert_nikobus_address
    )
    return button_data["nikobus_button"]


# Ground-truth (button_address, key, expected op-point label) tuples
# extracted by cross-referencing the Niko PC software export of the
# user's install against BP-cell decodes captured from the live
# discovery scan (modules 8CF5, 8B9C, 9418, C95D, ...). Each row
# resolves to the *A-half* op-point; the B-half is exercised
# separately via the mirror propagation test.
GROUND_TRUTH_05_312 = [
    # Channel 1 (R1: Rolluikmodule on 8CF5, outputs O02..O06)
    ("0E31C1", 1, "1.2A"),
    ("0E31C2", 1, "1.3A"),
    ("0E31C3", 1, "1.4A"),  # slot 3 with key=1 → row 4
    ("0E31C3", 0, "1.5A"),  # slot 3 with key=0 → row 5
    ("0E31C4", 1, "1A"),    # channel-1 master rocker (drives ch1 ×5)
    ("0E31C4", 0, "1A"),    # same master, key=0 is the Stop trigger
    # Channel 2 (continuation on R1 + R2)
    ("0E31C8", 1, "2.1A"),
    ("0E31C9", 1, "2.2A"),
    ("0E31CC", 1, "2A"),    # channel-2 master
    ("0E31CC", 0, "2A"),
    # Channel 3 (D1: Dimcontroller on C95D)
    ("0E31D0", 1, "3.1A"),
    ("0E31D1", 1, "3.2A"),
    # Channel 4 (R3: Rolluikmodule on 9418 + outputs on 8B9C)
    ("0E31D8", 1, "4.1A"),
    ("0E31D9", 1, "4.2A"),
    ("0E31DA", 1, "4.3A"),
    ("0E31DB", 1, "4.4A"),  # slot 3 with key=1 → row 4
    ("0E31DB", 0, "4.5A"),  # slot 3 with key=0 → row 5
    ("0E31DC", 1, "4A"),    # channel-4 master
    ("0E31DC", 0, "4A"),
]


# ---------------------------------------------------------------------------
# Lookup index
# ---------------------------------------------------------------------------


def test_easywave_52_lookup_window_covers_24_used_slots_per_button():
    """6 of 8 slots in each of 4 channel blocks = 24 used offsets,
    so the lookup carries 24 entries per 52-key button."""
    buttons = _materialise_05_312("0E31C0")
    lookup = _build_easywave_52_lookup(buttons)
    assert len(lookup) == 24
    # Every entry points back to the one physical base.
    assert set(lookup.values()) == {"0E31C0"}


def test_easywave_52_lookup_skips_unused_slots_6_and_7():
    """Slots 6 and 7 of each channel block are unused on the 05-312
    and must not appear in the lookup (otherwise a BP cell carrying
    an unrelated payload could spuriously match the remote)."""
    buttons = _materialise_05_312("0E31C0")
    lookup = _build_easywave_52_lookup(buttons)
    base = int("0E31C0", 16)
    for channel in range(4):
        for slot in (6, 7):
            unused = f"{(base + (channel * 8) + slot) & 0xFFFFFF:06X}"
            assert unused not in lookup, (channel, slot, unused)


def test_easywave_52_lookup_ignores_non_52_channel_buttons():
    """A 4-channel button at an arbitrary physical must not pollute
    the 52-key window index — its addresses live in an unrelated
    encoding space."""
    buttons = {
        "10998B": {"channels": 2, "operation_points": {}},
        "0E31C0": {
            "channels": 52,
            "operation_points": {"1A": {"bus_address": "88E31C"}},
        },
    }
    lookup = _build_easywave_52_lookup(buttons)
    assert set(lookup.values()) == {"0E31C0"}


# ---------------------------------------------------------------------------
# Ground-truth slot decode
# ---------------------------------------------------------------------------


def test_easywave_52_resolver_returns_expected_label_for_each_ground_truth_row():
    """Every (button, key) tuple captured from the live scan must
    resolve to the expected sub-code label."""
    buttons = _materialise_05_312("0E31C0")
    lookup = _build_easywave_52_lookup(buttons)
    for button_addr, key, expected_label in GROUND_TRUTH_05_312:
        resolved = _resolve_easywave_52(button_addr, key, buttons, lookup)
        assert resolved is not None, (button_addr, key, expected_label)
        phys, label, op_point = resolved
        assert phys == "0E31C0"
        assert label == expected_label, (button_addr, key, label, expected_label)
        assert isinstance(op_point, dict)


def test_easywave_52_resolver_handles_offset_zero_as_first_rocker():
    """Offset 0 = channel 1, slot 0 → 1.1A. This was the first
    motivator for putting the 52-key path *before* the direct-
    physical match in ``_resolve_operation_point`` — without that
    ordering, ``buttons.get('0E31C0')`` would hit the physical and
    the generic resolver would then fail since
    ``KEY_MAPPING_MODULE`` has no entry for ``channels=52``."""
    buttons = _materialise_05_312("0E31C0")
    lookup = _build_easywave_52_lookup(buttons)
    resolved = _resolve_easywave_52("0E31C0", 1, buttons, lookup)
    assert resolved is not None
    phys, label, _ = resolved
    assert phys == "0E31C0"
    assert label == "1.1A"


def test_easywave_52_resolver_returns_none_outside_window():
    """Addresses outside ``[physical, physical+32)`` must not match
    even if they share a prefix."""
    buttons = _materialise_05_312("0E31C0")
    lookup = _build_easywave_52_lookup(buttons)
    # 0E31C0 + 32 = 0E31E0 (one past the window)
    assert _resolve_easywave_52("0E31E0", 1, buttons, lookup) is None
    # Completely unrelated address
    assert _resolve_easywave_52("123456", 1, buttons, lookup) is None


def test_easywave_52_resolver_returns_none_for_unused_slots():
    """Even within the 32-byte window, slots 6 and 7 are unused."""
    buttons = _materialise_05_312("0E31C0")
    lookup = _build_easywave_52_lookup(buttons)
    # Channel-1 slot 6 = 0E31C6, slot 7 = 0E31C7
    assert _resolve_easywave_52("0E31C6", 1, buttons, lookup) is None
    assert _resolve_easywave_52("0E31C7", 1, buttons, lookup) is None


# ---------------------------------------------------------------------------
# Universality: works at any physical base
# ---------------------------------------------------------------------------


def test_easywave_52_resolver_works_at_arbitrary_physical_base():
    """The decode reads only ``offset = button - physical``, so a
    second 05-312 at a different physical produces the same labels
    for the same offsets. Confirms the algorithm has no per-install
    constants."""
    buttons = _materialise_05_312("1A2B3C")
    lookup = _build_easywave_52_lookup(buttons)
    base = int("1A2B3C", 16)
    for button_addr, key, expected_label in GROUND_TRUTH_05_312:
        offset = int(button_addr, 16) - int("0E31C0", 16)
        shifted = f"{(base + offset) & 0xFFFFFF:06X}"
        resolved = _resolve_easywave_52(shifted, key, buttons, lookup)
        assert resolved is not None, (shifted, key)
        _, label, _ = resolved
        assert label == expected_label, (shifted, key, label, expected_label)


# ---------------------------------------------------------------------------
# Integration: full merge_linked_modules round-trip
# ---------------------------------------------------------------------------


def _command_mapping_from_ground_truth() -> dict:
    """Build a fake ``command_mapping`` mimicking the shape
    ``merge_linked_modules`` consumes — one entry per (button, key)
    pair, each linked to a synthetic ``MOD1`` output."""
    mapping = {}
    for idx, (button_addr, key, _label) in enumerate(GROUND_TRUTH_05_312):
        mapping[(button_addr, key, None)] = [
            {
                "module_address": "MOD1",
                "channel": idx + 1,
                "mode": "M01 (Open - stop - close)",
                "t1": None,
                "t2": None,
                "payload": None,
                "button_address": button_addr,
            }
        ]
    return mapping


def test_merge_linked_modules_routes_05_312_bp_cells_to_op_points():
    """End-to-end: every ground-truth BP cell ends up on the
    expected op-point's ``linked_modules``."""
    button_data: dict = {"nikobus_button": _materialise_05_312("0E31C0")}
    command_mapping = _command_mapping_from_ground_truth()

    updated, links_added, outputs_added, unmatched = merge_linked_modules(
        button_data, command_mapping
    )

    assert unmatched == set(), unmatched
    op_points = button_data["nikobus_button"]["0E31C0"]["operation_points"]

    for _button_addr, _key, expected_label in GROUND_TRUTH_05_312:
        op_point = op_points.get(expected_label)
        assert isinstance(op_point, dict), expected_label
        linked = op_point.get("linked_modules") or []
        assert any(
            isinstance(block, dict) and block.get("module_address") == "MOD1"
            for block in linked
        ), (expected_label, linked)


def test_merge_linked_modules_mirrors_a_to_b_for_paired_rocker_modes():
    """M01 (Open-stop-close) on a paired rocker mirrors the link
    from the A half to the B half. Pre-0.15.0 the X.YA ↔ X.YB
    pairs weren't in ``_TWO_BUTTON_PAIRS`` so the mirror skipped
    them; the extension lets the user see both halves of the
    rocker as linked in the button file."""
    button_data: dict = {"nikobus_button": _materialise_05_312("0E31C0")}
    command_mapping = _command_mapping_from_ground_truth()
    merge_linked_modules(button_data, command_mapping)

    op_points = button_data["nikobus_button"]["0E31C0"]["operation_points"]

    # Pick a representative sub-rocker (1-2AB → 1.2A; mirror to 1.2B)
    # and a channel master (1-AB → 1A; mirror to 1B).
    for a_label, b_label in [
        ("1.2A", "1.2B"),
        ("1.5A", "1.5B"),
        ("2.1A", "2.1B"),
        ("4.4A", "4.4B"),
        ("4.5A", "4.5B"),
        ("1A", "1B"),
        ("2A", "2B"),
        ("4A", "4B"),
    ]:
        b_op = op_points.get(b_label)
        assert isinstance(b_op, dict), b_label
        b_linked = b_op.get("linked_modules") or []
        assert any(
            isinstance(block, dict) and block.get("module_address") == "MOD1"
            for block in b_linked
        ), (a_label, b_label, b_linked)


# ---------------------------------------------------------------------------
# Isolation: 4-channel buttons must be unaffected
# ---------------------------------------------------------------------------


def test_4_channel_button_link_resolution_unaffected_by_52_key_path():
    """A 4-channel wall button's link still resolves through the
    normal physical-match / bus-address paths. The 52-key lookup
    is empty when no 52-channel button is in the store."""
    buttons = {
        "10998B": {
            "type": "Bus push button, 2 control buttons",
            "model": "05-060-02",
            "channels": 2,
            "operation_points": {
                "1A": {"bus_address": "B46642"},
                "1B": {"bus_address": "F46642"},
            },
        }
    }
    bus_to_op = _build_bus_to_op_index(buttons)
    ir_base = _build_ir_base_lookup(buttons)
    ew52 = _build_easywave_52_lookup(buttons)
    assert ew52 == {}

    # Direct physical match still works.
    resolved = _resolve_operation_point("10998B", 1, buttons, bus_to_op, ir_base, ew52)
    assert resolved is not None
    phys, label, _ = resolved
    assert phys == "10998B"
    assert label == "1A"
