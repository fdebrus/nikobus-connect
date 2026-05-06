"""Regression test for 8-channel `+1`-alias merge resolution.

Background. Link records on dimmer / switch / roller modules encode
the button address as ``physical + 1`` for raw key indices 4..7 of an
8-channel button. The decoder accepts those via
``is_known_button_canonical``'s sibling check (``protocol.py``); the
resulting decoded record carries ``button_address = physical + 1``
because the channel-count lookup for the alias canonical returns
``None``, so ``get_push_button_address`` short-circuits and
``add_to_command_mapping`` falls back to the canonical form.

Pre-0.5.7 ``_resolve_operation_point`` had only two routes:

  1. ``buttons.get(canonical)`` — direct physical match.
  2. ``bus_to_op.get(canonical)`` — bus-address index lookup, with a
     ``bus_addr + 1`` alias for runtime bus presses (NOT for link
     records: the bus alias adds 1 to the *bus* address, not to the
     *physical* address; the two coincide only by accident).

Neither route covered the link-record alias, so a record decoded with
``button=<physical+1>`` failed both lookups and silently dropped at
merge. On the 2026-05-04 install the consequence was button
``1D3252`` (8-ch), whose link records on roller ``5538`` arrived
exclusively via the ``1D3253`` alias (raw key 4-7), staying unlinked
after the scan.

0.5.7 adds path 1a: when path 1 misses and the canonical-1 sibling
exists in ``buttons`` and is an 8-channel button, fold the alias back
to the physical and resolve against its op-points. 4-channel and
2-channel buttons are NOT affected — their link records never use
``physical + 1``.
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import (
    _build_bus_to_op_index,
    _build_ir_base_lookup,
    _resolve_operation_point,
)


def _eight_channel_button() -> dict:
    """Minimal button-store fixture for an 8-channel button at 1D3252.

    Only the fields ``_resolve_operation_point`` reads are populated.
    ``operation_points`` keys 1A..2D follow the standard 8-ch layout;
    bus_addresses are nibble-shifted variants of the physical address.
    """

    return {
        "1D3252": {
            "type": "Bus push button, 8 control buttons",
            "model": "05-349",
            "channels": 8,
            "operation_points": {
                "1A": {"bus_address": "B2932E"},
                "1B": {"bus_address": "F2932E"},
                "1C": {"bus_address": "32932E"},
                "1D": {"bus_address": "72932E"},
                "2A": {"bus_address": "92932E"},
                "2B": {"bus_address": "D2932E"},
                "2C": {"bus_address": "12932E"},
                "2D": {"bus_address": "52932E"},
            },
        }
    }


def test_canonical_plus_one_resolves_to_8ch_physical_button():
    """The +1 alias of an 8-ch physical address must resolve back to
    the physical button. ``1D3253`` decoded from a roller scan is the
    raw form for keys 4-7 of physical button ``1D3252``."""

    buttons = _eight_channel_button()
    bus_to_op = _build_bus_to_op_index(buttons)
    ir_base = _build_ir_base_lookup(buttons)

    # KEY_MAPPING_MODULE[8] is {0:"0", 1:"8", 2:"4", 3:"C",
    #                          4:"2", 5:"A", 6:"6", 7:"E"}
    # KEY_MAPPING[8] inverse: 1A↔A, 1B↔E, 1C↔2, 1D↔6, 2A↔8, 2B↔C, 2C↔0, 2D↔4
    # _key_raw_to_label(8, key_raw) finds the label whose KEY_MAPPING
    # entry matches KEY_MAPPING_MODULE[8][key_raw].
    expected_labels = {0: "2C", 1: "2A", 2: "2D", 3: "2B",
                       4: "1C", 5: "1A", 6: "1D", 7: "1B"}

    for key_raw, expected_label in expected_labels.items():
        resolved = _resolve_operation_point(
            "1D3253", key_raw, buttons, bus_to_op, ir_base
        )
        assert resolved is not None, (
            f"key_raw={key_raw}: +1 alias 1D3253 must fold back to physical 1D3252"
        )
        phys_addr, key_label, op_point = resolved
        assert phys_addr == "1D3252"
        assert key_label == expected_label
        assert op_point is buttons["1D3252"]["operation_points"][expected_label]


def test_4ch_button_plus_one_does_not_fold():
    """4-channel buttons never use the ``physical + 1`` link-record
    encoding (only 8-ch keys 4-7 do). The +1 of a 4-ch physical must
    NOT be folded back — that would invent ghost links."""

    buttons = {
        "182F18": {
            "type": "Bus push button, 4 control buttons",
            "model": "05-346",
            "channels": 4,
            "operation_points": {
                "1A": {"bus_address": "A1F018"},
                "1B": {"bus_address": "E1F018"},
                "1C": {"bus_address": "21F018"},
                "1D": {"bus_address": "61F018"},
            },
        }
    }
    bus_to_op = _build_bus_to_op_index(buttons)
    ir_base = _build_ir_base_lookup(buttons)

    # 182F19 = 182F18 + 1. For a 4-channel button this is junk; the
    # resolver must not fold it back.
    for key_raw in range(4):
        assert (
            _resolve_operation_point(
                "182F19", key_raw, buttons, bus_to_op, ir_base
            )
            is None
        ), f"4-ch +1 alias must NOT resolve (key_raw={key_raw})"


def test_2ch_button_plus_one_does_not_fold():
    """Same guarantee for 2-channel buttons. Their physical address
    space doesn't reserve an alias slot at ``+1``."""

    buttons = {
        "3CDE66": {
            "type": "Bus push button, 2 control buttons",
            "model": "05-342",
            "channels": 2,
            "operation_points": {
                "1A": {"bus_address": "999ECF"},
                "1B": {"bus_address": "D99ECF"},
            },
        }
    }
    bus_to_op = _build_bus_to_op_index(buttons)
    ir_base = _build_ir_base_lookup(buttons)

    for key_raw in (1, 3):  # only valid 2-ch key_raws
        assert (
            _resolve_operation_point(
                "3CDE67", key_raw, buttons, bus_to_op, ir_base
            )
            is None
        )


def test_direct_physical_match_takes_precedence_over_alias_fallback():
    """When a button is keyed at the *exact* canonical address, the
    direct path-1 match wins. The +1 fallback only fires after path 1
    misses, so we never silently mis-route a record to the wrong
    physical button."""

    # Two 8-channel buttons one apart: 1D3252 and 1D3253.
    # A record with canonical 1D3253 must resolve to 1D3253 (direct),
    # NOT fold back to 1D3252 (would only happen if 1D3253 weren't
    # itself a registered button).
    buttons = _eight_channel_button()
    buttons["1D3253"] = {
        "type": "Bus push button, 8 control buttons",
        "model": "05-349",
        "channels": 8,
        "operation_points": {
            "1A": {"bus_address": "B29333"},
            "2A": {"bus_address": "929333"},
        },
    }
    bus_to_op = _build_bus_to_op_index(buttons)
    ir_base = _build_ir_base_lookup(buttons)

    resolved = _resolve_operation_point(
        "1D3253", 1, buttons, bus_to_op, ir_base
    )
    assert resolved is not None
    phys_addr, key_label, _op = resolved
    assert phys_addr == "1D3253", (
        "direct match must win over +1 alias fallback"
    )
    assert key_label == "2A"
