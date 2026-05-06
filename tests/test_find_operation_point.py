"""Tests for the ``find_operation_point`` public helper."""

from __future__ import annotations

from nikobus_connect.discovery.fileio import find_operation_point


def _sample_store() -> dict:
    """A two-physical-button v2 store; one IR receiver, one 4-op wall unit."""

    return {
        "nikobus_button": {
            "182F18": {
                "type": "Bus push button, 4 control buttons",
                "model": "05-346",
                "channels": 4,
                "description": "Bus push button, 4 control buttons #N182F18",
                "operation_points": {
                    "1A": {
                        "bus_address": "863D06",
                        "description": "Push button 1A #N863D06",
                    },
                    "1B": {
                        "bus_address": "C63D06",
                        "description": "Push button 1B #NC63D06",
                    },
                    "1C": {
                        "bus_address": "063D06",
                        "description": "Push button 1C #N063D06",
                    },
                    "1D": {
                        "bus_address": "463D06",
                        "description": "Push button 1D #N463D06",
                    },
                },
            },
            "0D1C80": {
                "type": "IR Bus push button, 4 control buttons",
                "model": "05-348",
                "channels": 4,
                "description": "IR Bus push button, 4 control buttons #N0D1C80",
                "operation_points": {
                    "1A": {
                        "bus_address": "804E2C",
                        "description": "Push button 1A #N804E2C",
                    }
                },
            },
        }
    }


def test_find_operation_point_returns_physical_key_and_op_point():
    store = _sample_store()
    hit = find_operation_point(store, "063D06")
    assert hit is not None
    physical, key_label, op_point = hit
    assert physical == "182F18"
    assert key_label == "1C"
    assert op_point["bus_address"] == "063D06"


def test_find_operation_point_handles_case_and_whitespace():
    store = _sample_store()
    hit = find_operation_point(store, "  063d06  ")
    assert hit is not None
    assert hit[0] == "182F18"


def test_find_operation_point_returns_none_for_unknown_address():
    store = _sample_store()
    assert find_operation_point(store, "DEADBE") is None


def test_find_operation_point_returns_none_on_empty_store():
    assert find_operation_point({"nikobus_button": {}}, "863D06") is None
    assert find_operation_point({}, "863D06") is None
