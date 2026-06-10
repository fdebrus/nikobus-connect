"""Regression: a near-all-FF inventory slot must not become a phantom
device.

Real PC-Link inventory frame from the fdebrus install (deterministic
across two scans, June 2026): a slot whose address normalises to
``3FFFFF`` (low 20 bits all set) carries a type byte 0x23 that happens
to classify as a 05-304 RF push button. The old guard only skipped
``FFxxxx`` high-byte filler, so this slot leaked through and created a
phantom 4-key RF button (keys 3FFFFF / 7FFFFF / BFFFFF / FFFFFF, the
last being the universal empty-slot value) that decodes no links and
shows up as legacy_undecoded forever.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nikobus_connect.discovery import NikobusDiscovery


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    return MagicMock()


def _make_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    coord.get_module_channel_count = MagicMock(return_value=0)
    coord.get_button_channels = MagicMock(return_value=None)
    coord.nikobus_command = MagicMock()
    coord.nikobus_command.drain_queue = MagicMock(return_value=0)
    return coord


def _make_discovery(coord, tmp_path) -> NikobusDiscovery:
    return NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )


# The exact phantom inventory frame from the production log
# ($2E + responder F586 + 16-byte record + CRC). The record's address
# bytes are FFFF3F -> normalises to 3FFFFF; type byte 0x23 (05-304).
PHANTOM_FRAME = "2EF5860E00000023000080FFFF3F0005000000AE9EEE"


@pytest.mark.asyncio
async def test_near_all_ff_slot_is_not_classified_as_a_device(tmp_path):
    coord = _make_coordinator()
    discovery = _make_discovery(coord, tmp_path)

    await discovery.parse_inventory_response(PHANTOM_FRAME)

    assert "3FFFFF" not in discovery.discovered_devices, (
        "near-all-FF filler slot was classified as a phantom RF button"
    )
    # And nothing else snuck in from the filler frame.
    assert discovery.discovered_devices == {}
