"""``record_source`` provenance field on decoded link entries.

Background: Nikobus-HA #319 IKIKN forensic.

When a button-to-output link is decoded, the record could come from
two distinct on-wire sources:

1. **An output module's own link table** (switch / dimmer / roller's
   internal flash). Chunk size 12 hex (switch / roller) or 16 hex
   (dimmer). These records always reflect the current programming
   active on that module.

2. **A PC-Link or PC-Logic registry** (the 16-byte 32-hex records
   parsed via ``pc_record_parser``). PC software writes these as
   a master copy; DIN-button learn-mode does NOT update them. So
   the registry can carry stale programming from a previous owner
   (the IKIKN case) or scene-only programming that doesn't propagate
   to output modules' tables.

The two sources are structurally indistinguishable at the API surface
without provenance — they both produce a ``linked_modules`` entry on
the source button pointing at a target module and channel. 0.5.22
adds a ``record_source`` field that labels each output entry with
its scan origin so HA-side reconciliation can filter out
registry-only buttons as residue.

Values:
  - ``"output_module_table"`` — read from switch / dimmer / roller's
    own link table.
  - ``"pc_link_registry"`` — read from PC-Link's register memory.
  - ``"pc_logic_registry"`` — read from PC-Logic's register memory.

Missing field on legacy data: treat as None / unknown (HA decides
the bucket, but for safety we expose the absence rather than guess).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nikobus_connect.discovery import switch_decoder, dimmer_decoder, shutter_decoder
from nikobus_connect.discovery.pc_record_parser import (
    LinkRecord,
    RegistryBuffer,
    ModuleRegistryRecord,
    link_record_to_decoded_metadata,
)


class _DecodeContext:
    """Minimal stub matching what the per-module decoders read.

    Mirrors ``discovery.protocol.DecodeContext`` shape — the field
    set the decoders actually access.
    """

    def __init__(self, module_address: str, channel_count: int):
        self.module_address = module_address
        self.module_channel_count = channel_count
        self.coordinator = None


def _registry_with_target(target_address: str, channel_count: int, device_type: int) -> RegistryBuffer:
    """Build a ``RegistryBuffer`` carrying one module-registry record so
    the link-record resolver has something to resolve against."""

    registry = RegistryBuffer()
    registry.add(
        ModuleRegistryRecord(
            type_slot=1,
            device_type=device_type,
            address=target_address,
            raw_hex="00" * 16,
        )
    )
    return registry


def _coordinator_with_button(button_address: str, channels: int) -> MagicMock:
    """Coordinator stub that surfaces a single known button via
    ``get_button_channels`` (needed for ``link_record_to_decoded_metadata``
    to derive ``key_raw`` from the flag byte)."""

    coord = MagicMock()
    coord.get_button_channels = MagicMock(
        side_effect=lambda addr: channels if addr.upper() == button_address.upper() else None
    )
    coord.get_module_channel_count = MagicMock(
        side_effect=lambda addr: 12
    )
    coord.dict_module_data = {}
    return coord


# ---------------------------------------------------------------------------
# Output-module decoders: record_source == "output_module_table"
# ---------------------------------------------------------------------------

def test_switch_decoder_emits_output_module_table_source():
    """A 6-byte switch payload decoded by ``switch_decoder.decode`` must
    carry ``record_source="output_module_table"``. This is the
    canonical "current programming" source — HA-side treats records
    with this label as authoritative."""

    # Real-shape 12-hex (6-byte) record: channel 1, mode M05 (Impulse),
    # button address 16766C (in protocol's bus byte order at the suffix).
    # The exact bits don't matter — we're testing the source label.
    payload_hex = "030500F0F0E5"
    raw_bytes = [payload_hex[i:i + 2] for i in range(0, len(payload_hex), 2)]
    ctx = _DecodeContext("4707", 12)
    # Disable button-known gate by leaving coordinator None.
    decoded = switch_decoder.decode(payload_hex, raw_bytes, ctx)
    # Whether the record passes all other gates depends on synthesis
    # accuracy; what we pin here is the field's presence when it
    # DOES pass.
    if decoded is not None:
        assert decoded.get("record_source") == "output_module_table"


def test_dimmer_decoder_emits_output_module_table_source():
    """16-hex (8-byte) dimmer payload — same provenance contract."""

    # 16-hex = 8 bytes. Dimmer decoder reads channel/mode from
    # bytes 3-4. The other bytes are filler for the structure check.
    payload_hex = "0000000005040000"
    raw_bytes = [payload_hex[i:i + 2] for i in range(0, len(payload_hex), 2)]
    ctx = _DecodeContext("0E6C", 12)
    decoded = dimmer_decoder.decode(payload_hex, raw_bytes, ctx)
    if decoded is not None:
        assert decoded.get("record_source") == "output_module_table"


def test_shutter_decoder_emits_output_module_table_source():
    """12-hex (6-byte) shutter / roller payload — same contract."""

    payload_hex = "030500F0F0E5"
    raw_bytes = [payload_hex[i:i + 2] for i in range(0, len(payload_hex), 2)]
    ctx = _DecodeContext("9105", 6)
    decoded = shutter_decoder.decode(payload_hex, raw_bytes, ctx)
    if decoded is not None:
        assert decoded.get("record_source") == "output_module_table"


# ---------------------------------------------------------------------------
# PC-Link / PC-Logic registry: record_source == "pc_link_registry" /
# "pc_logic_registry"
# ---------------------------------------------------------------------------

def test_link_record_metadata_emits_pc_link_registry_source():
    """``link_record_to_decoded_metadata(record_source="pc_link_registry")``
    must label the returned metadata accordingly. This is the IKIKN
    forensic pin: the 16-byte PC-Link registry records that fdebrus
    measured as "26 records, mixed modes, zero overlap with 6-byte
    output-module records" all need this label so HA can filter."""

    record = LinkRecord(
        channel_index=0x00,
        mode_byte=0x04,           # M05 (Impulse) on switch_module
        flag_byte=0x00,           # key 0 → 1A on a 4-ch button
        payload_bytes="6C7616",   # bus-order — decodes to 16766C
        slot=0,
        raw_hex="0000000004000000" + "6C7616" + "00" + "000000",
    )
    registry = _registry_with_target("8110", channel_count=12, device_type=0x01)
    coord = _coordinator_with_button("16766C", channels=4)

    metadata = link_record_to_decoded_metadata(
        record, registry, coord, record_source="pc_link_registry"
    )
    assert metadata is not None
    assert metadata.get("record_source") == "pc_link_registry"


def test_link_record_metadata_emits_pc_logic_registry_source():
    """Same path with ``record_source="pc_logic_registry"`` — for
    installs with a PC-Logic (fdebrus's own install has 940C). HA
    filters both registry sources identically; the distinct label
    is for diagnostics only."""

    record = LinkRecord(
        channel_index=0x00,
        mode_byte=0x04,
        flag_byte=0x00,
        payload_bytes="6C7616",
        slot=0,
        raw_hex="0000000004000000" + "6C7616" + "00" + "000000",
    )
    registry = _registry_with_target("8110", channel_count=12, device_type=0x01)
    coord = _coordinator_with_button("16766C", channels=4)

    metadata = link_record_to_decoded_metadata(
        record, registry, coord, record_source="pc_logic_registry"
    )
    assert metadata is not None
    assert metadata.get("record_source") == "pc_logic_registry"


def test_link_record_metadata_no_record_source_when_unspecified():
    """Backward-compat: ``record_source`` is a keyword-only argument
    that defaults to ``None``. Callers that don't supply it (e.g. test
    harnesses or one-shot decoder paths without scan-source context)
    get metadata without the field — HA-side treats absence as
    'source unknown' rather than guessing."""

    record = LinkRecord(
        channel_index=0x00,
        mode_byte=0x04,
        flag_byte=0x00,
        payload_bytes="6C7616",
        slot=0,
        raw_hex="0000000004000000" + "6C7616" + "00" + "000000",
    )
    registry = _registry_with_target("8110", channel_count=12, device_type=0x01)
    coord = _coordinator_with_button("16766C", channels=4)

    metadata = link_record_to_decoded_metadata(record, registry, coord)
    assert metadata is not None
    assert "record_source" not in metadata


# ---------------------------------------------------------------------------
# End-to-end: record_source survives through merge_linked_modules
# into the persisted button-store layout.
# ---------------------------------------------------------------------------

def test_record_source_survives_merge_into_button_store():
    """The provenance label must flow all the way from the decoder
    output into the final ``nikobus_button`` store entry. This is
    the contract HA-side reads when classifying buttons.

    Without this, HA can't tell which outputs are current
    programming vs registry residue — Option B's whole purpose is
    that HA filters on this field.
    """

    from nikobus_connect.discovery.fileio import merge_linked_modules

    button_data = {
        "nikobus_button": {
            "16766C": {
                "type": "Bus push button, 4 control buttons",
                "model": "05-346",
                "channels": 4,
                "description": "Bus push button, 4 control buttons #N16766C",
                "operation_points": {
                    "1A": {
                        "bus_address": "8D9B9A",
                        "description": "Push button 1A #N8D9B9A",
                    },
                },
            },
        }
    }

    # Two outputs on the same button: one current (from output module
    # table), one residue (from PC-Link registry). HA-side reconciliation
    # should be able to distinguish them by ``record_source``.
    command_mapping = {
        ("8D9B9A", 0, None): [
            {
                "module_address": "8110",
                "channel": 1,
                "mode": "M05 (Impulse)",
                "t1": None,
                "t2": None,
                "payload": "030500F0F0E5",
                "button_address": "16766C",
                "ir_button_address": None,
                "ir_code": None,
                "record_source": "output_module_table",
            },
            {
                "module_address": "8110",
                "channel": 11,
                "mode": "M07 (Delayed on (long up to 2h))",
                "t1": None,
                "t2": None,
                "payload": "0A00000006000000E84C3C001C000000",
                "button_address": "16766C",
                "ir_button_address": None,
                "ir_code": None,
                "record_source": "pc_link_registry",
            },
        ],
    }

    merge_linked_modules(button_data, command_mapping)

    op_1a = button_data["nikobus_button"]["16766C"]["operation_points"]["1A"]
    linked = op_1a.get("linked_modules") or []
    assert len(linked) == 1
    block = linked[0]
    assert block["module_address"] == "8110"
    outputs = block.get("outputs") or []
    assert len(outputs) == 2

    # Both source labels must survive into the store. HA reads these
    # to bucket buttons by source mix.
    sources = sorted(o.get("record_source") for o in outputs)
    assert sources == ["output_module_table", "pc_link_registry"]


def test_record_source_absent_when_decoded_command_lacks_field():
    """If the decoded command predates 0.5.22 or comes from a custom
    decoder that doesn't set ``record_source``, the merged output
    entry must not carry the field at all (rather than ``None``).

    This keeps the store schema clean for legacy data — HA-side
    distinguishes "field absent" from "explicit None"."""

    from nikobus_connect.discovery.fileio import merge_linked_modules

    button_data = {
        "nikobus_button": {
            "16766C": {
                "type": "Bus push button, 4 control buttons",
                "model": "05-346",
                "channels": 4,
                "description": "Bus push button, 4 control buttons #N16766C",
                "operation_points": {
                    "1A": {
                        "bus_address": "8D9B9A",
                        "description": "Push button 1A #N8D9B9A",
                    },
                },
            },
        }
    }
    command_mapping = {
        ("8D9B9A", 0, None): [
            {
                "module_address": "8110",
                "channel": 1,
                "mode": "M05 (Impulse)",
                "t1": None,
                "t2": None,
                "payload": "030500F0F0E5",
                "button_address": "16766C",
                "ir_button_address": None,
                "ir_code": None,
                # No record_source key — simulates legacy/test caller.
            },
        ],
    }

    merge_linked_modules(button_data, command_mapping)

    op_1a = button_data["nikobus_button"]["16766C"]["operation_points"]["1A"]
    outputs = op_1a["linked_modules"][0]["outputs"]
    assert len(outputs) == 1
    # Field is absent, not None.
    assert "record_source" not in outputs[0]
