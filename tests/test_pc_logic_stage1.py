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


def test_device_type_0x21_is_push_button_interface():
    """05-056 is a 2-input push-button interface — Niko's product page
    (https://products.niko.eu/de-at/article/05-056) describes it as
    "interface for push buttons for connection to the home automation
    system" with 2 inputs. Promoted from ``Reserved`` in 0.5.11 after
    a user install confirmed the device-type byte against the
    printed model number."""

    entry = DEVICE_TYPES["21"]
    assert entry["Model"] == "05-056"
    assert entry["Category"] == "Button"
    assert entry["Channels"] == 2


def test_device_type_0x22_is_switch_interface():
    entry = DEVICE_TYPES["22"]
    assert entry["Model"] == "05-057"
    assert entry["Category"] == "Button"
    # 05-057 is a 2-input external switching contact (two ``IN``
    # terminals on the physical device). Earlier versions had this
    # as 4 — corrected in 0.5.10 against the printed module image.
    assert entry["Channels"] == 2


def test_device_type_0x26_is_rf868_mini_transmitter():
    entry = DEVICE_TYPES["26"]
    assert entry["Model"] == "05-314"
    assert entry["Category"] == "Button"
    assert entry["Channels"] == 4


def test_device_type_0x2b_is_audio_distribution_module():
    entry = DEVICE_TYPES["2B"]
    assert entry["Model"] == "05-205"
    assert entry["Category"] == "Module"
    # 0.5.10: 05-205 lands in its own ``audio_module`` bucket so the
    # integration can platform-route it deliberately. The bucket has
    # no decoder yet — Audio Distribution storage format is
    # unvalidated — but the dedicated bucket means HA-side code can
    # opt in without inheriting the catch-all ``other_module``
    # button-creation behaviour.
    assert get_module_type_from_device_type("2B") == "audio_module"


def test_device_type_0x37_is_modular_interface():
    """05-206 (Modular Interface, 6 inputs) gets the
    ``interface_module`` bucket so HA can render its inputs as a
    distinct entity class. Excluded from the per-module register-scan
    queue — its routing is held by the PC-Logic, not by itself."""

    entry = DEVICE_TYPES["37"]
    assert entry["Model"] == "05-206"
    assert entry["Category"] == "Module"
    assert entry["Channels"] == 6
    assert get_module_type_from_device_type("37") == "interface_module"


def test_audio_and_interface_buckets_are_excluded_from_scan_queue():
    """``NON_OUTPUT_MODULE_TYPES`` carries the four buckets whose
    addresses are kept out of ``query_module_inventory("ALL")``'s
    sequential queue and whose per-module dispatch short-circuits
    before issuing any register reads. Pin the set so neither bucket
    silently leaks into the scan path on a refactor."""

    from nikobus_connect.discovery.discovery import NON_OUTPUT_MODULE_TYPES

    assert NON_OUTPUT_MODULE_TYPES == frozenset({
        "feedback_module",
        "other_module",
        "interface_module",
        "audio_module",
    })


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


def test_scan_range_for_sub_default_is_tuned_for_switch_and_roller():
    """The 0.4.10 tuning of sub=04 to 0x00..0x3F applies to switch and
    roller (multi-firmware verified). Dimmer was reverted in 0.5.7 to
    the pre-0.4.10 full sweep — covered by a dedicated test below.
    The bare-sub default (no ``module_type``) keeps the historical
    tuned range so callers without a known type see the conservative
    behaviour."""

    assert _scan_range_for_sub("04") == range(0x00, 0x40)
    assert _scan_range_for_sub("04", module_type="switch_module") == range(0x00, 0x40)
    assert _scan_range_for_sub("04", module_type="roller_module") == range(0x00, 0x40)


def test_scan_range_for_sub_dimmer_is_full_sweep_per_pass():
    """Dimmer reverts to pre-0.4.10 ``range(0x00, 0x100)`` for both
    sub=04 and sub=01 since 0.5.7. Real-hardware capture (2026-05-04,
    modules 116D + 0E0A) showed the 0.4.10 narrowing dropped link
    records on channels 3 and 5; restoring the full sweep recovers
    them at the cost of ~3 minutes extra per dimmer scan."""

    assert _scan_range_for_sub("04", module_type="dimmer_module") == range(0x00, 0x100)
    assert _scan_range_for_sub("01", module_type="dimmer_module") == range(0x00, 0x100)


def test_scan_range_for_sub_overrides_pc_logic_to_full_sweep():
    assert _scan_range_for_sub("04", module_type="pc_logic") == range(0x00, 0x100)


def test_scan_range_for_sub_pc_logic_override_applies_to_all_subs():
    """The override is per-module-type, not per-sub. Even if a future
    Stage extends PC-Logic with extra sub-bytes, those passes must also
    sweep the full range until we know where the cell content lives."""

    for sub in ("04", "00", "01"):
        assert _scan_range_for_sub(sub, module_type="pc_logic") == range(0x00, 0x100)


def test_scan_range_priority_per_pass_overrides_per_module():
    """The (module_type, sub_byte) override beats the module-type-only
    override. This is what lets us widen one pass on one module type
    without affecting any other (module, sub) combination."""

    # Dimmer has both a per-pass widening AND falls under the per-sub
    # default — per-pass wins for the registered sub-bytes.
    assert _scan_range_for_sub("04", module_type="dimmer_module") == range(0x00, 0x100)
    assert _scan_range_for_sub("01", module_type="dimmer_module") == range(0x00, 0x100)
    # An unregistered sub-byte for dimmer falls through to the
    # per-sub default (no per-module-type-only override exists for
    # dimmer; PC-Logic / PC-Link are the only whole-module overrides).
    assert _scan_range_for_sub("00", module_type="dimmer_module") == range(0x00, 0x40)


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


# ---------------------------------------------------------------------------
# Stage 2c (0.5.10): PC-Logic class decoder emits DecodedCommands for resolved
# link records, mirroring PcLinkDecoder. Function-level ``decode()`` stays
# return-``None`` because it has no registry context.
# ---------------------------------------------------------------------------


def test_device_type_0x08_carries_six_channels():
    """PC Logic (0x08) is the master logic controller; the local-input
    population (LM01..LM06) means the inventory must carry a non-zero
    channel count so HA can surface them."""

    entry = DEVICE_TYPES["08"]
    assert entry["Channels"] == 6


def test_pc_logic_decoder_emits_decoded_command_for_resolved_link_record():
    """PC-Logic Stage 2c parity with PC-Link: a resolved link record
    produces a ``DecodedCommand`` whose metadata carries the resolved
    target module as ``module_address`` (so the merge-layer override
    routes the link to the real output, not the PC-Logic controller)."""

    from nikobus_connect.discovery.pc_logic_decoder import PcLogicDecoder

    coord = MagicMock()
    coord.dict_module_data = {
        "switch_module": {"C9A5": {}, "4707": {}, "5B05": {}},
        "dimmer_module": {"0E6C": {}},
        "roller_module": {"9105": {}, "8394": {}},
        "pc_link": {"86F5": {}},
        "pc_logic": {"940C": {}},
        "feedback_module": {"966C": {}},
    }
    counts = {
        "0E6C": 12, "9105": 6, "8394": 6, "C9A5": 12,
        "5B05": 4, "4707": 12, "86F5": 0, "940C": 0, "966C": 0,
    }
    coord.get_module_channel_count = MagicMock(side_effect=lambda addr: counts.get(addr, 0))
    coord.get_button_channels = MagicMock(side_effect=lambda addr: {
        "1843B4": 4,
    }.get(addr.upper()))

    decoder = PcLogicDecoder(coord)
    decoder.set_module_address("940C")

    # Same registry order as the PC-Link Stage 2b test — PC-Link and
    # PC-Logic share the parser and resolver, so the flat map index
    # 0x21 still resolves to (C9A5, 10).
    registry_chunks = [
        "03000000030000006C0E000001000000",  # 0E6C dimmer
        "030000000A000000F586000001000000",  # 86F5 PC Link self
        "03000000020000000591000001000000",  # 9105 roller
        "03000000020000009483000002000000",  # 8394 roller
        "0300000001000000A5C9000001000000",  # C9A5 switch
        "03000000080000000C94000001000000",  # 940C PC Logic self
        "0300000031000000055B000002000000",  # 5B05 compact switch
        "03000000010000000747000003000000",  # 4707 switch
        "03000000420000006C96000001000000",  # 966C feedback
    ]
    for chunk in registry_chunks:
        decoder.decode_chunk(chunk)

    commands = decoder.decode_chunk("2100000006000080B443180018000000")

    assert len(commands) == 1
    cmd = commands[0]
    assert cmd.module_type == "pc_logic"
    assert cmd.metadata["module_address"] == "C9A5"
    assert cmd.metadata["channel"] == 10
    assert cmd.metadata["M"] == "M07 (Delayed on (long up to 2h))"
    assert cmd.metadata["button_address"] == "1843B4"
    assert cmd.metadata["key_raw"] == 1


def test_pc_logic_decoder_reset_scan_buffers_clears_registry():
    """``reset_scan_buffers`` runs at scan boundaries via the chunker
    base class. PC-Logic must extend it to also clear its registry
    buffer so a fresh scan doesn't carry registry residue from the
    previous one."""

    from nikobus_connect.discovery.pc_logic_decoder import PcLogicDecoder

    coord = _make_coordinator()
    decoder = PcLogicDecoder(coord)
    decoder.decode_chunk("03000000030000006C0E000001000000")
    assert len(decoder._registry) == 1

    decoder.reset_scan_buffers()
    assert len(decoder._registry) == 0
