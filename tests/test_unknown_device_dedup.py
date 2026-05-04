"""Dedupe + cataloguing for "Unknown device detected" warnings.

Pre-0.5.4 behaviour: every inventory record carrying an uncatalogued
device-type byte logged a fresh WARNING, including the
"please open an issue" CTA. On installs with several uncatalogued
types (e.g. 7×0x14 + 7×0x24 + 7×0x34 + 5×0x3B observed in one log),
that meant ~26 duplicate WARNINGs per scan.

0.5.4 fixes both ends of that:

1. Catalogue the types observed in the wild as ``Category="Reserved"``
   so they don't trip the "Unknown" warning at all.
2. For *truly* unknown types (not yet catalogued), warn once per
   device-type byte per session; subsequent occurrences DEBUG.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.mapping import DEVICE_TYPES


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    task = MagicMock()
    task.cancel = MagicMock()
    return task


def _make_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    return coord


def _make_discovery(tmp_path) -> NikobusDiscovery:
    return NikobusDiscovery(
        _make_coordinator(),
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
        module_data={"nikobus_module": {}},
    )


# ---------------------------------------------------------------------------
# Cataloguing: previously-warning types now carry Category="Reserved"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("device_type_hex", ["05", "14", "21", "24", "34", "3B", "46"])
def test_reserved_device_types_are_catalogued(device_type_hex):
    """All seven types observed in the user-attachments log have entries."""

    entry = DEVICE_TYPES.get(device_type_hex)
    assert entry is not None, f"missing catalogue entry for 0x{device_type_hex}"
    assert entry["Category"] == "Reserved"
    # Reserved entries must have a Name so the inventory log line is
    # readable rather than ``Discovered Reserved - None``.
    assert entry.get("Name")


async def test_reserved_category_does_not_trigger_unknown_warning(tmp_path, caplog):
    """A type catalogued as Reserved must not log the "open an issue"
    WARNING — that's the whole point of the cataloguing step."""

    discovery = _make_discovery(tmp_path)

    # Construct a minimal inventory frame whose device-type byte is 0x14.
    # parse_inventory_response indexes payload_bytes[7] for the type; we
    # build the rest with valid structure (header 3 bytes + 4 bytes
    # padding + type at offset 7 + 4 bytes more + 3 bytes for the
    # button-style address slice [11:14]).
    # Frame layout for parse_inventory_response: bytes [0-6] padding,
    # byte [7] device-type, bytes [8-10] padding, bytes [11-13] address
    # (Button-style 3-byte slice). Min frame length is 15 bytes; we pad
    # to 16 for headroom.
    payload_hex = "00" * 7 + "14" + "00" * 3 + "1A1918" + "00" * 2

    caplog.set_level(logging.WARNING, logger="nikobus_connect.discovery.discovery")
    await discovery.parse_inventory_response(payload_hex)

    unknown_warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "Unknown device detected" in rec.message
    ]
    assert unknown_warnings == [], (
        f"Reserved type 0x14 should not trigger Unknown-device warning; "
        f"got {len(unknown_warnings)} warnings"
    )


# ---------------------------------------------------------------------------
# Dedupe: truly-unknown types warn once per session
# ---------------------------------------------------------------------------


async def test_unknown_device_warning_is_deduped(tmp_path, caplog):
    """Same uncatalogued device-type byte seen N times → 1 WARNING + N-1 DEBUG."""

    discovery = _make_discovery(tmp_path)

    # Pick a device-type byte not in DEVICE_TYPES at all. 0xEE is
    # unlikely to ever map to a real type.
    rare_type = "EE"
    assert rare_type not in DEVICE_TYPES, (
        "test assumes 0xEE is uncatalogued; if you've added it, pick another"
    )

    payload_hex = "00" * 7 + rare_type + "00" * 3 + "1A1918" + "00" * 2

    caplog.set_level(logging.DEBUG, logger="nikobus_connect.discovery.discovery")

    # Five identical inventory records for the same uncatalogued type.
    for _ in range(5):
        await discovery.parse_inventory_response(payload_hex)

    warnings_for_type = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING
        and "Unknown device detected" in rec.message
        and f"Type {rare_type}" in rec.message
    ]
    deduped_debugs = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.DEBUG
        and "Unknown device detected (deduped)" in rec.message
        and f"Type {rare_type}" in rec.message
    ]

    assert len(warnings_for_type) == 1, (
        f"expected exactly 1 WARNING for repeated uncatalogued type; "
        f"got {len(warnings_for_type)}"
    )
    assert len(deduped_debugs) == 4, (
        f"expected 4 DEBUG-level deduped messages; got {len(deduped_debugs)}"
    )


async def test_unknown_device_dedup_is_per_type_not_global(tmp_path, caplog):
    """Two different uncatalogued types each warn once — dedupe is keyed
    on the type byte, not collapsed into a single global warning."""

    discovery = _make_discovery(tmp_path)

    types = ("EE", "EF")
    for t in types:
        assert t not in DEVICE_TYPES, f"test assumes 0x{t} is uncatalogued"

    caplog.set_level(logging.WARNING, logger="nikobus_connect.discovery.discovery")

    for t in types:
        payload_hex = "00" * 7 + t + "00" * 3 + "1A1918" + "00" * 2
        await discovery.parse_inventory_response(payload_hex)
        # Repeat each one to verify the second occurrence doesn't warn.
        await discovery.parse_inventory_response(payload_hex)

    warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "Unknown device detected" in rec.message
    ]
    assert len(warnings) == 2, (
        f"expected 1 warning per distinct uncatalogued type (2 total); "
        f"got {len(warnings)}"
    )
