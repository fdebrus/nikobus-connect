"""PC Logic (05-201) decoder.

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
— not the 6-byte BP-cell stride we guessed in Stage 1. The decoder
parses those records via the shared ``pc_record_parser`` and
surfaces them at INFO. PC Logic and PC Link are isomorphic at the
record-storage layer, so they share the parser.

Stage 2c (0.5.10): PC Logic and PC Link both ingest into the merge
layer. The decoder now mirrors ``PcLinkDecoder`` exactly: per-scan
``RegistryBuffer``, link-record resolution against the registry-built
flat output map, and ``DecodedCommand`` emission for resolved link
records. The only difference between the two classes is the log
prefix.

The function-level ``decode()`` hook stays return-``None`` because
it's a one-shot path with no registry context — without registry
buffering the resolver can't run.
"""

from __future__ import annotations

import logging
from typing import Any

from .chunk_decoder import BaseChunkingDecoder
from .pc_link_decoder import _decode_and_log, _known_module_addresses
from .pc_record_parser import RegistryBuffer

_LOGGER = logging.getLogger(__name__)
_LOG_PREFIX = "PC-Logic"


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Module-level decoder hook used by ``decode_command_payload``.

    One-shot path with no registry buffer plumbed through, so the
    resolver can't run here and no ``DecodedCommand`` can be returned.
    Logs the parsed record at INFO for visibility and returns ``None``.
    Class-based scans go through ``PcLogicDecoder`` instead, which
    carries a per-scan registry and emits commands for resolved link
    records.
    """

    _decode_and_log(
        payload_hex,
        getattr(context, "module_address", None),
        coordinator=getattr(context, "coordinator", None),
        prefix=_LOG_PREFIX,
        registry=None,
        module_type=None,
        logger=_LOGGER,
    )
    return None


class PcLogicDecoder(BaseChunkingDecoder):
    """PC-Logic variant of the chunk-based decoder pipeline.

    Same on-wire format as ``PcLinkDecoder`` — both share the
    parser, the registry buffer, and the link-target resolver. The
    only difference is the log prefix used for diagnostic lines.
    """

    def __init__(self, coordinator):
        super().__init__(coordinator, "pc_logic")
        self._registry = RegistryBuffer()

    def reset_registry(self) -> None:
        """Clear the registry buffer between scans."""

        self._registry.reset()

    def reset_scan_buffers(self) -> None:
        """Clear per-scan state. Extends the base alt-alignment reset
        with the registry reset so a fresh scan starts with no carried
        registry state."""

        super().reset_scan_buffers()
        self._registry.reset()

    def decode_chunk(self, chunk, module_address=None):
        chunk = chunk.strip().upper()
        addr = module_address or self._module_address
        return _decode_and_log(
            chunk,
            addr,
            coordinator=self._coordinator,
            prefix=_LOG_PREFIX,
            registry=self._registry,
            module_type=self.module_type,
            logger=_LOGGER,
        )


__all__ = ["PcLogicDecoder", "decode"]
