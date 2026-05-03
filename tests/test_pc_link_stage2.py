"""Stage-2a integration tests: PC Link is in the scan queue and runs
through the register-scan engine, with structured logging firing on
real trace bytes.

These pin behaviour roswennen's Nikobus-HA#303 install needs:
controller memory (the PC Link's link table) gets read on every
"Scan all module links" run, not just the modules in the output-only
output-module set.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.pc_link_decoder import PcLinkDecoder, decode
from nikobus_connect.discovery.protocol import decode_command_payload


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
    coord.discovery_module = True
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    coord.get_module_channel_count = MagicMock(return_value=0)
    return coord


# ---------------------------------------------------------------------------
# Scan-queue inclusion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc_link_module_is_included_in_scan_all_queue(tmp_path):
    """``query_module_inventory("ALL")`` must enqueue PC-Link addresses.

    Stage 1 dropped only ``pc_logic`` from the exclusion. Stage 2a
    drops ``pc_link`` too, because the PC software's serial trace
    showed the link table lives in the PC Link's register memory.
    """

    coord = _make_coordinator()
    coord.dict_module_data = {
        "switch_module": {"4707": {"address": "4707"}},
        "pc_link": {"86F5": {"address": "86F5"}},
        "pc_logic": {"940C": {"address": "940C"}},
    }
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery._start_next_register_scan = AsyncMock()

    await discovery.query_module_inventory("ALL")

    queued = discovery._register_scan_queue
    assert "86F5" in queued, "PC-Link address was filtered out of the queue"
    assert "940C" in queued, "regression: PC-Logic dropped from queue"
    assert "4707" in queued, "regression: switch module dropped from queue"


@pytest.mark.asyncio
async def test_pc_link_module_runs_register_scan(tmp_path):
    """A PC-Link module reaching ``query_module_inventory(addr)`` must
    invoke ``_scan_module_registers`` rather than short-circuiting via
    the non-output-module skip path."""

    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "86F5": {
            "address": "86F5",
            "category": "Module",
            "model": "05-200",
            "device_type": "0A",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="pc_link")

    scan_calls: list[dict] = []

    async def fake_scan(address, base_cmd, command_range, sub_byte="04"):
        scan_calls.append(
            {
                "address": address,
                "base_cmd": base_cmd,
                "command_range": command_range,
                "sub_byte": sub_byte,
            }
        )

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("86F5")

    assert scan_calls, "PC-Link module was skipped instead of scanned"
    assert scan_calls[0]["sub_byte"] == "04"
    assert scan_calls[0]["base_cmd"].startswith("10"), (
        "PC-Link uses function 10 (it's not a dimmer)"
    )
    assert scan_calls[0]["command_range"] == range(0xA3, 0x100), (
        "PC-Link scan range must be tuned to the productive band "
        "0xA3..0xFF observed in the Nikobus PC-software trace. The "
        "0.5.0 full-sweep was observed to abort at register 0x04 in "
        "real installs because PC Link doesn't respond to reads in "
        "0x00..0x07, tripping the consecutive-give-up early-stop."
    )


# ---------------------------------------------------------------------------
# Decoder registration
# ---------------------------------------------------------------------------


def test_pc_link_decoder_is_registered_on_discovery(tmp_path):
    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    pc_link_decoders = [
        d for d in discovery._decoders if isinstance(d, PcLinkDecoder)
    ]
    assert len(pc_link_decoders) == 1, "PcLinkDecoder is not registered"
    assert pc_link_decoders[0].can_handle("pc_link")


# ---------------------------------------------------------------------------
# Decoder logging behaviour
# ---------------------------------------------------------------------------


def test_pc_link_decoder_logs_registry_record_at_info(caplog):
    context = MagicMock()
    context.module_address = "86F5"
    # 940C registry entry from the trace (PC Logic seen by the PC Link).
    chunk = "03000000080000000C94000001000000"

    with caplog.at_level(logging.INFO, logger="nikobus_connect.discovery.pc_link_decoder"):
        result = decode(chunk, [], context)

    assert result is None
    log_text = caplog.text
    assert "PC-Link module-registry record" in log_text
    assert "86F5" in log_text
    assert "address=940C" in log_text
    assert "device_type=0x08" in log_text


def test_pc_link_decoder_logs_link_record_at_info(caplog):
    context = MagicMock()
    context.module_address = "86F5"
    # First link record from roswennen's trace, reg=0xAD.
    chunk = "0400000006000080B443180001000000"

    with caplog.at_level(logging.INFO, logger="nikobus_connect.discovery.pc_link_decoder"):
        result = decode(chunk, [], context)

    assert result is None
    log_text = caplog.text
    assert "PC-Link link record" in log_text
    assert "channel_idx=0x04" in log_text
    assert "mode=0x06" in log_text
    assert "flag=0x80" in log_text
    assert "payload=B44318" in log_text


def test_pc_link_decoder_returns_none_for_any_chunk():
    """Stage 2a contract: decoder is visibility-only. Even a fully
    parseable record returns ``None`` so the merge layer never sees
    PC-Link-derived records until Stage 2b validates the byte-0 →
    ``(module, channel)`` mapping."""

    context = MagicMock()
    context.module_address = "86F5"

    parseable_registry = "03000000030000006C0E000001000000"
    parseable_link = "0400000006000080B443180001000000"
    empty = "FF" * 16

    assert decode(parseable_registry, [], context) is None
    assert decode(parseable_link, [], context) is None
    assert decode(empty, [], context) is None


def test_decode_command_payload_routes_pc_link_to_decoder(caplog):
    """``decode_command_payload(module_type='pc_link', ...)`` must
    dispatch through the new ``pc_link_decoder`` branch added to the
    routing table in ``protocol.py``."""

    coord = MagicMock()
    coord.get_module_channel_count = MagicMock(return_value=0)
    chunk = "0400000006000080B443180001000000"

    with caplog.at_level(logging.INFO, logger="nikobus_connect.discovery.pc_link_decoder"):
        result = decode_command_payload(
            chunk, "pc_link", coord, module_address="86F5"
        )

    assert result is None
    assert "PC-Link link record" in caplog.text
