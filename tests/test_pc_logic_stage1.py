"""Stage-1 PC-Logic instrumentation contract.

These tests pin the 0.4.11 behaviour: PC-Logic flows through the
register-scan engine and produces logged chunk dumps without
attempting to decode them. The actual byte decoder lands in Stage 2;
these tests exist to make sure we don't accidentally regress the
queue inclusion or the stub-decoder wiring before then.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.discovery.chunk_decoder import _CHUNK_LENGTHS
from nikobus_connect.discovery.discovery import (
    NikobusDiscovery,
    _scan_range_for_sub,
)
from nikobus_connect.discovery.mapping import (
    DEVICE_TYPES,
    get_module_type_from_device_type,
)
from nikobus_connect.discovery.pc_logic_decoder import PcLogicDecoder, decode
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
# DEVICE_TYPES additions (0x22, 0x26, 0x2B)
# ---------------------------------------------------------------------------


def test_device_type_0x22_is_switch_interface():
    entry = DEVICE_TYPES["22"]
    assert entry["Model"] == "05-057"
    assert entry["Category"] == "Button"


def test_device_type_0x26_is_rf868_mini_transmitter():
    entry = DEVICE_TYPES["26"]
    assert entry["Model"] == "05-314"
    assert entry["Category"] == "Button"
    assert entry["Channels"] == 4


def test_device_type_0x2b_is_audio_distribution_module():
    entry = DEVICE_TYPES["2B"]
    assert entry["Model"] == "05-205"
    assert entry["Category"] == "Module"
    # No dedicated decoder yet — falls through to other_module.
    assert get_module_type_from_device_type("2B") == "other_module"


# ---------------------------------------------------------------------------
# PC-Logic register scan inclusion
# ---------------------------------------------------------------------------


def test_get_module_type_pc_logic_resolves_correctly():
    # PC-Logic is at device type 0x08; verify the resolver still
    # buckets it as ``pc_logic`` after the changes around the
    # exclusion sets.
    assert get_module_type_from_device_type("08") == "pc_logic"


@pytest.mark.asyncio
async def test_pc_logic_module_is_included_in_scan_all_queue(tmp_path):
    """``query_module_inventory("ALL")`` must enqueue PC-Logic addresses
    so the scan engine walks 05-201 register memory."""

    coord = _make_coordinator()
    coord.dict_module_data = {
        "switch_module": {"4707": {"address": "4707"}},
        "pc_logic": {"80D9": {"address": "80D9"}},
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
    assert "80D9" in queued, "PC-Logic address was filtered out of the queue"
    assert "4707" in queued, "regression: switch module dropped from queue"


@pytest.mark.asyncio
async def test_pc_logic_module_runs_register_scan(tmp_path):
    """A PC-Logic module reaching ``query_module_inventory(addr)`` must
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
        "80D9": {
            "address": "80D9",
            "category": "Module",
            "model": "05-201",
            "device_type": "08",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="pc_logic")

    scan_calls: list[dict] = []

    async def fake_scan(address, base_cmd, command_range, sub_byte="04"):
        scan_calls.append(
            {
                "address": address,
                "base_cmd": base_cmd,
                "sub_byte": sub_byte,
            }
        )

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("80D9")

    assert scan_calls, "PC-Logic module was skipped instead of scanned"
    # Pass 1 uses the standard sub=04 with the function-10 prefix
    # (PC-Logic is not a dimmer).
    assert scan_calls[0]["sub_byte"] == "04"
    assert scan_calls[0]["base_cmd"].startswith("10")


# ---------------------------------------------------------------------------
# Stub decoder contract
# ---------------------------------------------------------------------------


def test_pc_logic_decoder_is_registered_on_discovery(tmp_path):
    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    pc_logic_decoders = [
        d for d in discovery._decoders if isinstance(d, PcLogicDecoder)
    ]
    assert len(pc_logic_decoders) == 1, "PcLogicDecoder is not registered"
    assert pc_logic_decoders[0].can_handle("pc_logic")


def test_pc_logic_decoder_returns_none_for_any_chunk():
    """Stage 2a contract preserved from Stage 1: the decoder is a
    visibility-only path. Even when fed a parseable record it returns
    ``None`` so the merge layer never sees PC-Logic-derived records
    until Stage 2b lands."""

    context = MagicMock()
    context.module_address = "80D9"

    # A 32-hex-char registry record, parseable by the Stage 2a parser.
    parseable = "03000000080000000C94000001000000"
    assert decode(parseable, [], context) is None

    # An empty chunk.
    assert decode("FF" * 16, [], context) is None

    # A wrong-length chunk that the parser rejects.
    assert decode("CAFEBABE1234", [], context) is None


def test_pc_logic_decoder_logs_registry_record_at_info(caplog):
    """Stage 2a logs structured records at INFO so users can attach
    the dump without enabling component-level debug. A registry record
    must surface its decoded device_type / address / type_slot."""

    context = MagicMock()
    context.module_address = "80D9"
    # 940C registry record from roswennen's trace.
    chunk = "03000000080000000C94000001000000"

    with caplog.at_level(logging.INFO, logger="nikobus_connect.discovery.pc_logic_decoder"):
        result = decode(chunk, [], context)

    assert result is None
    log_text = caplog.text
    assert "PC-Logic module-registry record" in log_text
    assert "80D9" in log_text
    assert "address=940C" in log_text
    assert "device_type=0x08" in log_text


def test_decode_command_payload_routes_pc_logic_to_decoder(caplog):
    """The dispatch table in ``discovery/protocol.py`` must route
    ``module_type=pc_logic`` to ``pc_logic_decoder`` so the structured
    log fires when the chunking layer hands it a 16-byte record."""

    coord = MagicMock()
    coord.get_module_channel_count = MagicMock(return_value=0)

    # Link record from roswennen's trace, sent without the chunking
    # layer's reverse-before-decode flag (the PC decoders parse on-wire
    # bytes directly).
    chunk = "0400000006000080B443180001000000"

    with caplog.at_level(logging.INFO, logger="nikobus_connect.discovery.pc_logic_decoder"):
        result = decode_command_payload(
            chunk,
            "pc_logic",
            coord,
            module_address="80D9",
        )

    assert result is None
    assert "PC-Logic link record" in caplog.text


def test_pc_logic_chunk_length_is_sixteen_byte_record_stride():
    """Stage 2a (0.5.0) corrects the Stage-1 guess: a Nikobus
    PC-software serial trace shows the on-wire stride is 32 hex chars
    (16 bytes per record), not 12. The 12-char value was guessed from
    BP-cell screenshots; the trace from real hardware contradicted it,
    so the constant moved. PC Link uses the same stride."""

    assert _CHUNK_LENGTHS["pc_logic"] == 32
    assert _CHUNK_LENGTHS["pc_link"] == 32


# ---------------------------------------------------------------------------
# Stage 1.5: PC-Logic full-range scan override
# ---------------------------------------------------------------------------
#
# Stage 1's 64-register dump (sub=04 → 0x00..0x3F) returned a 4×16 cell
# directory plus a long stretch of all-FF on roswennen's 80D9. The
# productive output-module band ends at 0x3F, but PC-Logic is not an
# output module — its memory layout is unmapped. Override the primary
# pass to the full 0x00..0xFF range for ``pc_logic`` only so we can
# observe whether cell content lives past the directory. Other module
# types must keep their tuned range.


def test_scan_range_for_sub_default_is_tuned_for_output_modules():
    assert _scan_range_for_sub("04") == range(0x00, 0x40)
    assert _scan_range_for_sub("04", module_type="switch_module") == range(0x00, 0x40)
    assert _scan_range_for_sub("04", module_type="dimmer_module") == range(0x00, 0x40)
    assert _scan_range_for_sub("04", module_type="roller_module") == range(0x00, 0x40)


def test_scan_range_for_sub_overrides_pc_logic_to_full_sweep():
    assert _scan_range_for_sub("04", module_type="pc_logic") == range(0x00, 0x100)


def test_scan_range_for_sub_pc_logic_override_applies_to_all_subs():
    """The override is per-module-type, not per-sub. Even if a future
    Stage extends PC-Logic with extra sub-bytes, those passes must also
    sweep the full range until we know where the cell content lives."""

    for sub in ("04", "00", "01"):
        assert _scan_range_for_sub(sub, module_type="pc_logic") == range(0x00, 0x100)


@pytest.mark.asyncio
async def test_pc_logic_register_scan_uses_full_range(tmp_path):
    coord = _make_coordinator()
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "80D9": {
            "address": "80D9",
            "category": "Module",
            "model": "05-201",
            "device_type": "08",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="pc_logic")

    scan_calls: list[dict] = []

    async def fake_scan(address, base_cmd, command_range, sub_byte="04"):
        scan_calls.append(
            {
                "address": address,
                "command_range": command_range,
                "sub_byte": sub_byte,
            }
        )

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("80D9")

    assert len(scan_calls) == 1, "Stage 1.5 still expects a single sub=04 pass"
    assert scan_calls[0]["sub_byte"] == "04"
    assert scan_calls[0]["command_range"] == range(0x00, 0x100), (
        "PC-Logic must sweep the full 0..255 register space, not the "
        "output-module tuned 0..63 band."
    )


@pytest.mark.asyncio
async def test_switch_register_scan_range_unaffected_by_pc_logic_override(tmp_path):
    """Regression guard: the per-module-type override must not leak
    into switch/dimmer/roller scans. Their tuned 0x00..0x3F range is
    a deliberate optimisation tied to the productive band of the
    output-module link table."""

    coord = _make_coordinator()
    coord.get_module_channel_count = MagicMock(return_value=12)
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )

    discovery.discovered_devices = {
        "4707": {
            "address": "4707",
            "category": "Module",
            "model": "05-000-02",
            "channels": 12,
            "device_type": "01",
        }
    }
    discovery._is_known_module_address = MagicMock(return_value=True)
    discovery._resolve_module_type = MagicMock(return_value="switch_module")

    scan_calls: list[dict] = []

    async def fake_scan(address, base_cmd, command_range, sub_byte="04"):
        scan_calls.append(
            {"command_range": command_range, "sub_byte": sub_byte}
        )

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("4707")

    assert scan_calls
    assert scan_calls[0]["command_range"] == range(0x00, 0x40)
