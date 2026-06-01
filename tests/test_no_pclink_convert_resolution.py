"""Regression tests for the no-PC-Link convert() resolver fallback.

PC-Link keys the button store by the RAW canonical address the register
scan decodes, so link records resolve via a direct match. A store built
from manual config files (no PC-Link) keys each button by its
``convert_nikobus_address`` form instead, so the scan's raw addresses
(e.g. ``1CC09C``) miss every direct/bus lookup even though the button is
present under its converted key (``0E40CE``).

Verified on a real no-PC-Link install (Jan Sennesael): every decoded
scan address convert()s exactly onto a store physical key, yet links
stayed unmerged (``active=0, legacy_undecoded=23``) until the convert()
fallback bridged the two address representations.
"""

from __future__ import annotations

from nikobus_connect.discovery.fileio import (
    _build_bus_to_op_index,
    _build_ir_base_lookup,
    _resolve_operation_point,
)
from nikobus_connect.discovery.protocol import convert_nikobus_address


def _store() -> dict:
    """Slice of the real no-PC-Link store (manual_config_consolidated).

    Keyed by the ``convert_nikobus_address`` form, NOT the raw canonical
    address the scan decodes.
    """
    return {
        # 8-control-point (Keuken): convert(1CC09C) == 0E40CE.
        "0E40CE": {
            "channels": 8,
            "operation_points": {
                "1A": {"bus_address": "AE40CE"},
                "2A": {"bus_address": "8E40CE"},
                "2C": {"bus_address": "0E40CE"},
                "1B": {"bus_address": "EE40CE"},
                "1D": {"bus_address": "6E40CE"},
                "2B": {"bus_address": "CE40CE"},
                "2D": {"bus_address": "4E40CE"},
                "1C": {"bus_address": "2E40CE"},
            },
        },
        # 4-control-point (Buro_deur): convert(1CE8E2) == 11C5CE.
        "11C5CE": {
            "channels": 4,
            "operation_points": {
                "1A": {"bus_address": "91C5CE"},
                "1B": {"bus_address": "D1C5CE"},
                "1C": {"bus_address": "11C5CE"},
                "1D": {"bus_address": "51C5CE"},
            },
        },
        # 2-control-point (Overloop_bk): convert(1CA146) == 18A14E.
        "18A14E": {
            "channels": 2,
            "operation_points": {
                "1A": {"bus_address": "98A14E"},
                "1B": {"bus_address": "D8A14E"},
            },
        },
    }


def test_convert_fallback_4ch():
    store = _store()
    bus_to_op = _build_bus_to_op_index(store)
    ir = _build_ir_base_lookup(store)
    assert convert_nikobus_address("1CE8E2") == "11C5CE"
    phys, label, _op = _resolve_operation_point("1CE8E2", 1, store, bus_to_op, ir)
    assert phys == "11C5CE"
    assert label == "1A"


def test_convert_fallback_2ch():
    store = _store()
    bus_to_op = _build_bus_to_op_index(store)
    ir = _build_ir_base_lookup(store)
    phys, label, _op = _resolve_operation_point("1CA146", 1, store, bus_to_op, ir)
    assert phys == "18A14E"
    assert label == "1A"


def test_convert_fallback_8ch_both_halves():
    """The 8-button's 2X half resolves via convert() of the even base;
    its 1X half via convert() of the +1 sibling."""
    store = _store()
    bus_to_op = _build_bus_to_op_index(store)
    ir = _build_ir_base_lookup(store)

    # 2X half — even base 1CC09C.
    phys, label, _op = _resolve_operation_point("1CC09C", 0, store, bus_to_op, ir)
    assert phys == "0E40CE"
    assert label == "2C"

    # 1X half — +1 sibling 1CC09D, normalised into the 1X range.
    phys, label, _op = _resolve_operation_point("1CC09D", 0, store, bus_to_op, ir)
    assert phys == "0E40CE"
    assert label == "1C"


def test_convert_fallback_is_last_resort():
    """When a button IS keyed by its raw address (PC-Link store), the
    direct path-1 match wins and convert() is never consulted — so the
    fallback can't mis-route a PC-Link install."""
    store = {
        "1CE8E2": {  # keyed by RAW address, as a PC-Link inventory would
            "channels": 4,
            "operation_points": {
                "1A": {"bus_address": "B2932E"},
                "1B": {"bus_address": "F2932E"},
                "1C": {"bus_address": "32932E"},
                "1D": {"bus_address": "72932E"},
            },
        }
    }
    bus_to_op = _build_bus_to_op_index(store)
    ir = _build_ir_base_lookup(store)
    phys, _label, _op = _resolve_operation_point("1CE8E2", 0, store, bus_to_op, ir)
    assert phys == "1CE8E2"  # direct match, not the converted form
