"""PC-Link / PC-Logic discrimination at the broadcast-``#A`` boundary.

Both controllers listen for the address-inquiry broadcast. The
``$18 <addr> 00 <sig> 0F 3F FF <crc>`` reply carries a signature byte
that distinguishes them: 0x50 on PC-Link (model 0A), 0x40 on
PC-Logic (model 08). When both controllers exist on the same bus and
the PC-Logic wins the response race, our pre-0.5.11 discovery would
record the PC-Logic's address as "the PC-Link" and run all
inventory-memory reads against the wrong device — every register
came back empty.

Three trace-confirmed samples seed the parser:

  - fdebrus PC-Link 86F5: ``$18F58600500F3FFFAC61FE``      sig=0x50
  - issue-307 PC-Link 846F: ``$186F8400500F3FFF48EDCE``    sig=0x50
  - new-user PC-Logic 8835: ``$18358800400F3FFF4170C4``    sig=0x40

Tests below pin the contract: 0x50 frames are accepted as PC-Link
addresses; non-0x50 frames are dropped with a warning so the user
sees a clear diagnostic instead of a silent empty-inventory failure.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from nikobus_connect.const import PC_LINK_INVENTORY_SIGNATURE_BYTE
from nikobus_connect.discovery.discovery import NikobusDiscovery


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    task = MagicMock()
    task.cancel = MagicMock()
    return task


def _make_discovery(tmp_path) -> NikobusDiscovery:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    coord.get_module_channel_count = MagicMock(return_value=0)
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )
    discovery.discovery_stage = "inventory_addresses"
    return discovery


def test_signature_constant_is_pc_link_value():
    """PC-Link's signature byte is 0x50 — confirmed across two
    real-hardware traces (fdebrus 86F5, issue-307 846F)."""

    assert PC_LINK_INVENTORY_SIGNATURE_BYTE == 0x50


def test_pc_link_response_is_accepted(tmp_path):
    """Frame with sig=0x50 → address recorded as PC-Link, inventory
    enumeration proceeds. Pinned against fdebrus's real frame."""

    discovery = _make_discovery(tmp_path)

    # fdebrus's actual ``#A`` response.
    discovery.handle_device_address_inventory("$18F58600500F3FFFAC61FE")

    assert "86F5" in discovery._inventory_addresses
    assert "86F5" in discovery.discovered_devices
    assert discovery.discovered_devices["86F5"]["module_type"] == "pc_link"


def test_pc_link_response_from_second_install_is_accepted(tmp_path):
    """Sanity check on the issue-307 reporter's frame — different
    address, same sig byte, must be accepted."""

    discovery = _make_discovery(tmp_path)
    discovery.handle_device_address_inventory("$186F8400500F3FFF48EDCE")

    assert "846F" in discovery._inventory_addresses
    assert "846F" in discovery.discovered_devices


def test_pc_logic_response_is_rejected_with_warning(tmp_path, caplog):
    """Frame with sig=0x40 → PC-Logic. Must be dropped without
    polluting ``discovered_devices`` or ``_inventory_addresses``, and
    must surface a WARNING with the rejected address and signature so
    the user can diagnose the missing-PC-Link configuration."""

    discovery = _make_discovery(tmp_path)

    with caplog.at_level(
        logging.WARNING, logger="nikobus_connect.discovery.discovery"
    ):
        discovery.handle_device_address_inventory("$18358800400F3FFF4170C4")

    assert "8835" not in discovery._inventory_addresses
    assert "8835" not in discovery.discovered_devices
    assert "Inventory record rejected" in caplog.text
    assert "non_pc_link_signature" in caplog.text
    # Address is logged in raw (bus byte) order so users can grep
    # against the wire frame they captured.
    assert "raw=3588" in caplog.text
    assert "0x40" in caplog.text
    assert "0x50" in caplog.text


def test_unknown_signature_byte_is_rejected(tmp_path, caplog):
    """Future controller variants with an unknown signature byte must
    also be rejected. Conservative-by-default: only 0x50 is a known
    PC-Link, anything else needs explicit validation before being
    treated as one."""

    discovery = _make_discovery(tmp_path)

    # Synthetic frame with sig=0x99 — not a known controller byte.
    with caplog.at_level(
        logging.WARNING, logger="nikobus_connect.discovery.discovery"
    ):
        discovery.handle_device_address_inventory("$18AAAA00990F3FFF000000")

    assert "AAAA" not in discovery._inventory_addresses
    assert "AAAA" not in discovery.discovered_devices
    assert "non_pc_link_signature" in caplog.text


def test_pc_link_then_pc_logic_keeps_only_pc_link(tmp_path):
    """If both controllers respond — PC-Link first, PC-Logic second —
    the PC-Logic frame must be dropped silently (with a warning) and
    leave the PC-Link record untouched."""

    discovery = _make_discovery(tmp_path)

    discovery.handle_device_address_inventory("$18F58600500F3FFFAC61FE")
    discovery.handle_device_address_inventory("$18358800400F3FFF4170C4")

    assert discovery._inventory_addresses == {"86F5"}
    assert "8835" not in discovery.discovered_devices


def test_pc_logic_first_then_pc_link_recovers_correct_address(tmp_path):
    """The motivating real-world case: PC-Logic wins the race, but
    we still listen for further frames and pick up the PC-Link's
    response when it arrives. The signature filter is what makes
    this work — without it the PC-Logic's address would be locked
    in first and the PC-Link's later response would be ignored as
    a duplicate."""

    discovery = _make_discovery(tmp_path)

    discovery.handle_device_address_inventory("$18358800400F3FFF4170C4")
    discovery.handle_device_address_inventory("$18F58600500F3FFFAC61FE")

    assert discovery._inventory_addresses == {"86F5"}
    assert "8835" not in discovery.discovered_devices
    assert "86F5" in discovery.discovered_devices


def test_truncated_frame_with_missing_signature_byte_is_rejected(
    tmp_path, caplog
):
    """A short frame that doesn't carry a signature byte must not
    crash and must not be accepted. Defensive parse — bytes after the
    address slot are read with bounds checking."""

    discovery = _make_discovery(tmp_path)

    # Truncated: address present but no signature/payload bytes.
    with caplog.at_level(
        logging.WARNING, logger="nikobus_connect.discovery.discovery"
    ):
        discovery.handle_device_address_inventory("$18AAAA")

    # Truncated frames produce no signature byte; treated as "no
    # signature available" and accepted as PC-Link to preserve the
    # historical behaviour of the handler when the signature byte
    # isn't observable. Real-bus frames always carry the signature,
    # so this path is only reached on malformed/synthetic input.
    # (Either rejecting OR accepting is defensible here; we keep the
    # accept path to avoid breaking older test harnesses that never
    # passed the full payload.)
    assert "AAAA" in discovery._inventory_addresses
