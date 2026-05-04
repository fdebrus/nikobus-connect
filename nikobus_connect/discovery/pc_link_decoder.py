"""PC Link (05-200) decoder — Stage 2a, structured logging.

The PC Link is the bus controller — the device that bridges the
USB-serial port to the Nikobus bus. Its register memory stores both
a module registry (what the install looks like) and the link table
(button → output-channel routing). The Nikobus PC software walks
that memory at every "Read configuration" operation.

Stage 1 left ``pc_link`` excluded from the scan-queue + chunking
layer entirely. Stage 2a includes it in both, parses the 16-byte
records into structured form, and surfaces them at INFO so users can
attach the dump to GitHub issues without enabling component-level
debug.

Stage 2a does NOT yet emit ``DecodedCommand`` outputs into the merge
layer. The byte-0 → ``(target_module, channel)`` mapping is
hypothesised from one user's trace and needs cross-install validation
before we let it populate ``linked_modules``. That's Stage 2b.
"""

from __future__ import annotations

import logging
from typing import Any

from .chunk_decoder import BaseChunkingDecoder
from .pc_record_parser import (
    LinkRecord,
    ModuleRegistryRecord,
    is_empty_record,
    is_noise_chunk,
    parse_pc_record,
)

_LOGGER = logging.getLogger(__name__)
_LOG_PREFIX = "PC-Link"


def _known_module_addresses(coordinator) -> set[str]:
    """Collect bus-form addresses of every module in the live inventory.

    Used by ``parse_pc_record`` to identify registry records by shape
    (Module device-type + known address) when the byte-0 marker varies
    by firmware. Returns an empty set when the coordinator is missing
    or has no inventory yet — the parser falls back to the legacy
    byte-0 == 0x03 fast path in that case.
    """

    if coordinator is None:
        return set()
    buckets = getattr(coordinator, "dict_module_data", None) or {}
    addresses: set[str] = set()
    for module_map in buckets.values():
        if isinstance(module_map, dict):
            addresses.update(addr.upper() for addr in module_map if addr)
    return addresses


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Module-level decoder hook used by ``decode_command_payload``.

    The chunking layer routes through this for any chunk it produces
    when ``module_type=pc_link``. We log a structured INFO line per
    record (or a DEBUG line for empty / unparseable chunks) and return
    ``None`` — the merge layer must not see these records yet.
    """

    return _log_record(
        payload_hex,
        getattr(context, "module_address", None),
        coordinator=getattr(context, "coordinator", None),
    )


class PcLinkDecoder(BaseChunkingDecoder):
    """PC-Link variant of the chunk-based decoder pipeline.

    Overrides ``decode_chunk`` to bypass the switch/dimmer/roller
    ``reverse_before_decode`` flag — PC-Link records are stored in a
    fixed on-wire byte order that the parser consumes directly.
    """

    def __init__(self, coordinator):
        super().__init__(coordinator, "pc_link")

    def decode_chunk(self, chunk, module_address=None):
        chunk = chunk.strip().upper()
        addr = module_address or self._module_address
        _log_record(chunk, addr, coordinator=self._coordinator, prefix=_LOG_PREFIX)
        return []


def _log_record(
    chunk_hex: str,
    module_address: str | None,
    *,
    coordinator=None,
    prefix: str = _LOG_PREFIX,
) -> dict[str, Any] | None:
    """Shared logging helper used by both ``decode()`` and ``decode_chunk``.

    Always returns ``None`` — Stage 2a is visibility-only; no decoded
    records are surfaced to the merge layer.
    """

    chunk_hex = (chunk_hex or "").strip().upper()

    if is_empty_record(chunk_hex):
        _LOGGER.debug(
            "%s empty record | module=%s payload=%s",
            prefix,
            module_address,
            chunk_hex,
        )
        return None

    if is_noise_chunk(chunk_hex):
        _LOGGER.debug(
            "%s noise chunk | module=%s payload=%s",
            prefix,
            module_address,
            chunk_hex,
        )
        return None

    record = parse_pc_record(
        chunk_hex,
        known_module_addresses=_known_module_addresses(coordinator),
    )
    if record is None:
        _LOGGER.debug(
            "%s unparseable chunk | module=%s payload=%s",
            prefix,
            module_address,
            chunk_hex,
        )
        return None

    if isinstance(record, ModuleRegistryRecord):
        _LOGGER.info(
            "%s module-registry record | module=%s device_type=0x%02X "
            "address=%s type_slot=%d raw=%s",
            prefix,
            module_address,
            record.device_type,
            record.address,
            record.type_slot,
            record.raw_hex,
        )
    elif isinstance(record, LinkRecord):
        _LOGGER.info(
            "%s link record | module=%s channel_idx=0x%02X mode=0x%02X "
            "flag=0x%02X payload=%s slot=0x%02X raw=%s",
            prefix,
            module_address,
            record.channel_index,
            record.mode_byte,
            record.flag_byte,
            record.payload_bytes,
            record.slot,
            record.raw_hex,
        )

    return None


__all__ = ["PcLinkDecoder", "decode"]
