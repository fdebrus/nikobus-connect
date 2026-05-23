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
    _MODULE_SCAN_PROFILES,
    _scan_passes_for_module_type,
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
    """0.17.0: ``NON_OUTPUT_MODULE_TYPES`` carries the three remaining
    buckets we can't scan (audio/interface/other). ``feedback_module``
    moved OUT — Niko_05_207.dll's GetDLLReadInfo gives it a real profile
    so we now scan it like any other output module."""

    from nikobus_connect.discovery.discovery import NON_OUTPUT_MODULE_TYPES

    assert NON_OUTPUT_MODULE_TYPES == frozenset({
        "other_module",
        "interface_module",
        "audio_module",
    })


# ---------------------------------------------------------------------------
# 0.5.15: catalogue audit — duplicate-Model invariants
#
# Several DEVICE_TYPES entries legitimately share a Model number because
# the same physical Niko product reports different device-type bytes in
# different firmware revisions or operational modes. These tests pin the
# expected invariants so a future "deduplicate" cleanup doesn't silently
# break real installs.
#
# Also pins the 0x1F → Unknown change (Niko 05-311 is 1-channel only;
# 0x1F's previous mapping to 05-311 was incorrect).
# ---------------------------------------------------------------------------


def test_device_type_0x09_and_0x31_share_05_002_02_sku():
    """Two firmware-revision device-type bytes for the same physical
    Niko 05-002-02 compact switch module. Niko's product page describes
    a single 4-output configuration; some installs report 0x09, others
    report 0x31. Both entries must stay, not deduplicate."""

    entry_09 = DEVICE_TYPES["09"]
    entry_31 = DEVICE_TYPES["31"]
    assert entry_09["Model"] == entry_31["Model"] == "05-002-02"
    assert entry_09["Channels"] == entry_31["Channels"] == 4
    assert entry_09["Category"] == entry_31["Category"] == "Module"


def test_device_type_0x23_maps_to_rf_wall_button_4ch():
    """0x23 is the 4-channel RF-bus wall push button.

    Pre-0.16.2: ``Model = "Unknown"`` — fdebrus had confirmed from
    physical hardware (devices at addresses 201250 and 204915 on his
    install) that this was NOT the hand-held 05-312, but the actual
    SKU wasn't determined from Niko's catalogue.

    0.16.2: vendor catalogue (product.mdb KP=53, ``S_DB_RF_WAND_4``)
    pins this device-type byte to ``05-304`` (consumer-facing code
    that matches Niko's product page niko.eu/en/article/05-304), with
    the technical wildcard ``05-303-4*`` and legacy ``410-00002``
    recorded as alternates."""

    entry = DEVICE_TYPES["23"]
    assert entry["Model"] == "05-304"
    assert entry["ModelAlt"] == "05-303-4*"
    assert entry["ModelAltLegacy"] == "410-00002"
    assert entry["Channels"] == 4
    assert entry["Category"] == "Button"
    assert entry["VendorRef"] == "S_DB_RF_WAND_4"


def test_device_type_0x3d_remains_05_312_easywave_hand_held():
    """0x3D maps to 05-312 — Niko's 13-button Easywave hand-held
    that controls up to 52 circuits. This entry pins the 52-circuit
    firmware-reported population. (Earlier the entry was paired with
    0x23 as "two modes of one product"; that pairing was dropped
    when 0x23 was identified as a wall switch.)"""

    entry = DEVICE_TYPES["3D"]
    assert entry["Model"] == "05-312"
    assert entry["Channels"] == 52
    assert entry["Category"] == "Button"


def test_device_type_0x43_and_0x44_share_05_058_sku():
    """Niko 05-058 universal interface: 4 inputs, configurable as
    push buttons (0x43, 4 telegrams) OR switches (0x44, 4 inputs ×
    2 state-change telegrams = 8 channels). Same physical product,
    two firmware-reported modes."""

    entry_43 = DEVICE_TYPES["43"]
    entry_44 = DEVICE_TYPES["44"]
    assert entry_43["Model"] == entry_44["Model"] == "05-058"
    assert entry_43["Channels"] == 4
    assert entry_44["Channels"] == 8


def test_device_type_0x1f_maps_to_rf_wall_button_2ch():
    """0x1F is the 2-channel RF-bus wall push button.

    Pre-0.16.2: ``Model = "Unknown"`` — we had ruled out the wrong
    earlier mapping (to 05-311 hand-held) but hadn't identified the
    correct SKU.

    0.16.2: vendor catalogue (product.mdb KP=52, ``S_DB_RF_WAND_2``)
    pins this device-type byte to ``05-302`` (consumer-facing code
    that matches Niko's product page niko.eu/en/article/05-302), with
    the technical wildcard ``05-301-4*`` and legacy ``410-00001``
    recorded as alternates."""

    entry = DEVICE_TYPES["1F"]
    assert entry["Model"] == "05-302"
    assert entry["ModelAlt"] == "05-301-4*"
    assert entry["ModelAltLegacy"] == "410-00001"
    assert entry["Channels"] == 2
    assert entry["Category"] == "Button"
    assert entry["VendorRef"] == "S_DB_RF_WAND_2"


def test_device_type_0x25_remains_correct_05_311_1ch():
    """Sanity check: 0x25 is the genuine 1-channel mini hand-held
    05-311 per Niko's product page. The audit didn't touch this
    entry; pin it so it can't drift."""

    entry = DEVICE_TYPES["25"]
    assert entry["Model"] == "05-311"
    assert entry["Channels"] == 1
    assert entry["Category"] == "Button"


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
    # 0.17.0: PC-Logic follows its DLL-derived profile — all sub=4
    # (Niko_05_201a.dll has 4 sections, all in sub=4 0x26..0xF3).
    assert all(c["sub_byte"] == "04" for c in scan_calls), scan_calls
    assert all(c["base_cmd"].startswith("10") for c in scan_calls)


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


def test_pc_logic_profile_is_dll_derived() -> None:
    """0.17.0: PC-Logic scan plan is derived from Niko_05_201a.dll's
    GetDLLReadInfo export. Four sub=4 sections at:
        offset 0x42CB len 0x0001
        offset 0x4268 len 0x0104
        offset 0x445C len 0x060E
        offset 0x4E20 len 0x0118

    All at sub=4. The total register count below is post-dedup; if
    the DLL sections change, regenerate via _MODULE_SCAN_PROFILES.
    """
    plan = _scan_passes_for_module_type("pc_logic")
    # All passes must be sub=04
    for sub, _regs in plan:
        assert sub == "04", f"unexpected sub {sub} in PC-Logic plan"
    # Total reads is the sum of decoded section lengths in registers.
    total_regs = sum(len(regs) for _sub, regs in plan)
    assert total_regs > 100, f"PC-Logic plan suspiciously thin: {total_regs} reads"


def test_per_product_profiles_cover_all_output_modules() -> None:
    """Every output module type + PC-Logic + PC-Link + feedback has a
    DLL-derived scan profile. No empty plans (which would silently skip
    discovery for that family)."""
    for mt in ("switch_module", "roller_module", "dimmer_module",
               "pc_logic", "pc_link", "feedback_module"):
        plan = _scan_passes_for_module_type(mt)
        assert plan, f"missing scan profile for {mt}"
        assert _MODULE_SCAN_PROFILES[mt] == plan


@pytest.mark.asyncio
async def test_pc_logic_register_scan_drives_dll_profile(tmp_path):
    """0.17.0: PC-Logic scan dispatches the Niko_05_201a.dll-derived
    profile (4 sub=4 sections). The scan loop iterates each pass."""

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
        scan_calls.append({"sub_byte": sub_byte, "range": tuple(command_range)})

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("80D9")

    # PC-Logic profile: all passes are sub=04 (DLL has no other banks).
    assert scan_calls, "no scan calls issued"
    assert {c["sub_byte"] for c in scan_calls} == {"04"}
    # Profile sections come from Niko_05_201a.dll GetDLLReadInfo
    # (offset 0x42CB, 0x4268, 0x445C, 0x4E20). The merged plan must
    # cover at least 100 distinct register reads.
    total_regs = sum(len(c["range"]) for c in scan_calls)
    assert total_regs >= 100, total_regs


@pytest.mark.asyncio
async def test_switch_register_scan_drives_dll_profile(tmp_path):
    """0.17.0: switch scan dispatches the per-product profile derived
    from Niko_05_000_01.dll. The plan includes the legacy sub=4
    0x00..0x3F safety net (still a hypothesis pending real switch trace)."""

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
        scan_calls.append({"sub_byte": sub_byte, "range": tuple(command_range)})

    discovery._scan_module_registers = fake_scan
    discovery._finalize_discovery = AsyncMock()

    await discovery.query_module_inventory("4707")

    # Switch profile uses multiple sub-bytes (00, 01, 04) — link table
    # band at sub=0 0x3E..0xFF, secondary at sub=1 0x70..0x96, legacy
    # safety net at sub=4 0x00..0x3F, status at sub=4 0x65..0x69.
    subs = {c["sub_byte"] for c in scan_calls}
    assert subs == {"00", "01", "04"}, subs
    # The pre-0.16.0 legacy band must be present (proven to find records).
    sub4_regs = set()
    for c in scan_calls:
        if c["sub_byte"] == "04":
            sub4_regs.update(c["range"])
    assert {0x00, 0x10, 0x20, 0x3F}.issubset(sub4_regs), \
        "switch profile missing legacy sub=4 0x00..0x3F safety net"


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
    assert cmd.metadata["M"] == "M07 (Delayed on (up to 2h))"
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
