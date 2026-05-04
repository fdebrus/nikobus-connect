"""PC Logic (05-201) decoder — Stage 2a, structured logging.

PC Logic is the Nikobus bus's logic controller (separate from the PC
Link, which is the USB serial bridge). It can host BP grids — virtual
button panels for routing — and stores its programming in register
memory addressable via the same ``$1410<addr>NN04`` read protocol
used for switch / dimmer / roller modules.

Stage 1 (0.4.11): added ``pc_logic`` to the scan queue with a
12-hex-char (6-byte) chunk stride and a logging-only stub decoder
that emitted ``PC-Logic chunk | module=X payload=Y`` per slice.

Stage 1.5 (0.4.13): widened the PC-Logic scan range from the
output-module-tuned 0x00..0x3F to the full 0x00..0xFF.

Stage 2a (0.5.0): a Nikobus PC-software serial trace from a real
install (roswennen, Nikobus-HA#303) showed the on-wire format is a
**16-byte (32 hex chars) per-record** structure shared with PC Link
— not the 6-byte BP-cell stride we guessed in Stage 1. This module
now parses those records via the shared ``pc_record_parser`` and
surfaces them at INFO. PC Logic and PC Link are isomorphic at the
record-storage layer, so they share the parser; only the log-prefix
distinguishes them.

Like PC Link, this stays in visibility-only mode for Stage 2a — no
``DecodedCommand`` is emitted into the merge layer. Stage 2b will
add the resolution of byte-0 channel-indices to concrete
``(module_address, channel)`` pairs once the mapping is validated
across multiple installs.
"""

from __future__ import annotations

import logging
from typing import Any

from .chunk_decoder import BaseChunkingDecoder
from .pc_link_decoder import _known_module_addresses
from .pc_record_parser import (
    LinkRecord,
    ModuleRegistryRecord,
    is_empty_record,
    is_noise_chunk,
    parse_pc_record,
)

_LOGGER = logging.getLogger(__name__)
_LOG_PREFIX = "PC-Logic"


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Module-level decoder hook used by ``decode_command_payload``.

    Returns ``None`` — Stage 2a never produces a record for the merge
    layer. Logs the parsed record at INFO for visibility.
    """

    return _log_record(
        payload_hex,
        getattr(context, "module_address", None),
        coordinator=getattr(context, "coordinator", None),
    )


class PcLogicDecoder(BaseChunkingDecoder):
    """PC-Logic variant of the chunk-based decoder pipeline.

    Same on-wire format as ``PcLinkDecoder``; differs only in the
    log prefix it emits.
    """

    def __init__(self, coordinator):
        super().__init__(coordinator, "pc_logic")

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

    Always returns ``None`` — Stage 2a is visibility-only.
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


__all__ = ["PcLogicDecoder", "decode"]
