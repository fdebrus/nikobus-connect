"""Tests for the cluster-based remote-transmitter synthesis path.

Multi-page Easywave remotes (e.g. 05-312 with 13 scene pages) emit
dozens of distinct bus codes from a single physical handheld. None
of those codes appear in PC-Link inventory — the receiver only
knows the transmitter as a generic enrolled device. When discovery
scans output modules and decodes their BP cells, references to
those bus codes show up as ``unmatched_addresses`` in
``merge_linked_modules``.

This file pins the cluster-detection and synthesis behaviour against
the user's real install — PC-Logic at 940C, interface_module at
6E40, and **52 emitted bus codes from one Easywave remote that all
share the 4-hex suffix ``E31C``**.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.fileio import merge_discovered_buttons
from nikobus_connect.discovery.mapping import KEY_MAPPING
from nikobus_connect.discovery.protocol import convert_nikobus_address


# The 52 bus addresses observed on the user's install — every
# distinct code their multi-page Easywave remote emits. All share
# the trailing 4 hex chars ``E31C``.
REMOTE_E31C_BUS_ADDRESSES = sorted({
    # Ch1: 13 codes (3 base A/B/C + 5 scenes × A/B)
    "88E31C", "C8E31C", "08E31C",
    "80E31C", "C0E31C", "A0E31C", "E0E31C",
    "90E31C", "D0E31C", "B0E31C", "F0E31C",
    "30E31C", "70E31C",
    # Ch2
    "8CE31C", "CCE31C", "0CE31C",
    "84E31C", "C4E31C", "A4E31C", "E4E31C",
    "94E31C", "D4E31C", "B4E31C", "F4E31C",
    "34E31C", "74E31C",
    # Ch3
    "8AE31C", "CAE31C", "0AE31C",
    "82E31C", "C2E31C", "A2E31C", "E2E31C",
    "92E31C", "D2E31C", "B2E31C", "F2E31C",
    "32E31C", "72E31C",
    # Ch4
    "8EE31C", "CEE31C", "0EE31C",
    "86E31C", "C6E31C", "A6E31C", "E6E31C",
    "96E31C", "D6E31C", "B6E31C", "F6E31C",
    "36E31C", "76E31C",
})


def _make_discovery(tmp_path) -> NikobusDiscovery:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    return NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=MagicMock(),
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )


# ---------------------------------------------------------------------------
# Cluster detection
# ---------------------------------------------------------------------------


def test_cluster_below_threshold_is_not_synthesised(tmp_path):
    """A handful of unmatched addresses sharing a suffix shouldn't be
    promoted — likely just coincidence or flash garbage."""

    discovery = _make_discovery(tmp_path)
    discovery._accumulated_unmatched = {
        "8801AB", "C801AB", "0801AB",  # 3 entries — well below threshold
    }
    discovery._synthesize_remote_transmitters_from_unmatched()
    assert discovery.discovered_devices == {}


def test_cluster_at_threshold_is_synthesised(tmp_path):
    """Exactly 8 entries sharing a suffix should trigger synthesis
    (one virtual transmitter + 8 children)."""

    discovery = _make_discovery(tmp_path)
    suffix = "ABCD"
    discovery._accumulated_unmatched = {
        f"{first:02X}{suffix}" for first in range(0x80, 0x88)
    }
    discovery._synthesize_remote_transmitters_from_unmatched()

    # Virtual transmitter parent
    assert "RT-ABCD" in discovery.discovered_devices
    parent = discovery.discovered_devices["RT-ABCD"]
    assert parent["category"] == "Module"
    assert parent["module_type"] == "remote_transmitter"
    assert parent["transmitter_suffix"] == "ABCD"
    assert parent["transmitter_member_count"] == 8

    # 8 child button entries
    children = [
        v for v in discovery.discovered_devices.values()
        if v.get("category") == "Button"
        and v.get("remote_transmitter_address") == "RT-ABCD"
    ]
    assert len(children) == 8


def test_52_member_e31c_cluster_full_install(tmp_path):
    """The user's real install: 52 emitted Easywave codes sharing
    suffix ``E31C`` get synthesised as one transmitter parent + 52
    passthrough children. Each child is keyed in
    ``discovered_devices`` by the observed bus address itself and
    carries the original bus address in
    ``remote_transmitter_bus_address``."""

    discovery = _make_discovery(tmp_path)
    discovery._accumulated_unmatched = set(REMOTE_E31C_BUS_ADDRESSES)
    discovery._synthesize_remote_transmitters_from_unmatched()

    # Single virtual parent
    assert "RT-E31C" in discovery.discovered_devices
    parent = discovery.discovered_devices["RT-E31C"]
    assert parent["transmitter_member_count"] == 52
    assert parent["transmitter_suffix"] == "E31C"

    # 52 children, each keyed by the original bus address.
    children = {
        addr: entry
        for addr, entry in discovery.discovered_devices.items()
        if entry.get("category") == "Button"
        and entry.get("remote_transmitter_address") == "RT-E31C"
    }
    assert len(children) == 52
    assert set(children.keys()) == set(REMOTE_E31C_BUS_ADDRESSES)
    for bus_address, entry in children.items():
        assert entry["channels"] == 1
        assert entry["remote_transmitter_suffix"] == "E31C"
        assert entry["remote_transmitter_bus_address"] == bus_address


def test_synthesised_children_merge_into_button_store_with_original_bus(tmp_path):
    """End-to-end: after synthesis + standard merge, every child
    button-store entry has exactly one op-point ``1A`` whose
    ``bus_address`` matches the original observed bus code."""

    discovery = _make_discovery(tmp_path)
    discovery._accumulated_unmatched = set(REMOTE_E31C_BUS_ADDRESSES)
    discovery._synthesize_remote_transmitters_from_unmatched()

    button_data: dict = {"nikobus_button": {}}
    merge_discovered_buttons(
        button_data,
        discovery.discovered_devices,
        KEY_MAPPING,
        convert_nikobus_address,
    )

    # Collect the resulting op-point bus addresses; must be a
    # permutation of the original 52.
    seen_bus = set()
    for entry in button_data["nikobus_button"].values():
        if entry.get("remote_transmitter_address") != "RT-E31C":
            continue
        op_points = entry.get("operation_points") or {}
        assert set(op_points.keys()) == {"1A"}
        seen_bus.add(op_points["1A"]["bus_address"])

    assert seen_bus == set(REMOTE_E31C_BUS_ADDRESSES)


def test_multiple_independent_clusters_get_separate_transmitters(tmp_path):
    """If two different transmitters happen to be in the install,
    each cluster gets its own synthetic parent."""

    discovery = _make_discovery(tmp_path)
    cluster_a = {f"{first:02X}AAAA" for first in range(0x80, 0x90)}  # 16
    cluster_b = {f"{first:02X}BBBB" for first in range(0x80, 0x8A)}  # 10
    discovery._accumulated_unmatched = cluster_a | cluster_b
    discovery._synthesize_remote_transmitters_from_unmatched()

    assert "RT-AAAA" in discovery.discovered_devices
    assert "RT-BBBB" in discovery.discovered_devices
    assert (
        discovery.discovered_devices["RT-AAAA"]["transmitter_member_count"] == 16
    )
    assert (
        discovery.discovered_devices["RT-BBBB"]["transmitter_member_count"] == 10
    )


def test_synthesis_doesnt_shadow_real_entries(tmp_path):
    """If a real button was already enrolled at one of the cluster
    member addresses (vanishingly unlikely but possible), the
    synthesis must defer to the real entry."""

    discovery = _make_discovery(tmp_path)
    # Pretend the address "80E31C" is actually a discovered wall
    # button — synthesis should leave it alone.
    discovery.discovered_devices = {
        "80E31C": {
            "category": "Button",
            "model": "05-346",
            "channels": 4,
            "address": "80E31C",
            "device_type": "06",
        }
    }
    discovery._accumulated_unmatched = set(REMOTE_E31C_BUS_ADDRESSES)
    discovery._synthesize_remote_transmitters_from_unmatched()

    # Real entry untouched.
    assert discovery.discovered_devices["80E31C"]["model"] == "05-346"
    # Parent + the other 51 children still synthesised.
    assert "RT-E31C" in discovery.discovered_devices
    children = [
        v for v in discovery.discovered_devices.values()
        if v.get("category") == "Button"
        and v.get("remote_transmitter_address") == "RT-E31C"
    ]
    assert len(children) == 51


def test_synthesis_is_idempotent_with_repeated_call(tmp_path):
    """Running the synthesis twice on the same unmatched set must
    produce the same final state — important because discovery can
    run multiple times across the integration's lifetime."""

    discovery = _make_discovery(tmp_path)
    discovery._accumulated_unmatched = set(REMOTE_E31C_BUS_ADDRESSES)
    discovery._synthesize_remote_transmitters_from_unmatched()
    snapshot_a = {k: dict(v) for k, v in discovery.discovered_devices.items()}

    discovery._synthesize_remote_transmitters_from_unmatched()
    snapshot_b = {k: dict(v) for k, v in discovery.discovered_devices.items()}

    assert snapshot_a == snapshot_b
