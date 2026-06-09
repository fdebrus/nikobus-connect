"""Chunk handling for switch, dimmer and roller modules.

Pure offset-0 alignment, the 0.8.0 way: walk the buffered byte stream
one full record at a time (``idx += expected_len``), carry any
sub-record tail across frame boundaries via the caller-managed
``payload_buffer``. Cross-frame splits are reassembled by the
``payload_buffer + data_region`` concatenation in ``analyze_frame_payload``.

The 0.5.5..0.5.24 lineage carried an additional "alternate alignment"
pass per scan (skip 4, skip 8) to recover records that appeared to
sit at non-zero stream offsets on some real-world captures. That
path was reverted: the offsets-other-than-zero observations turned
out to be byte-slop from misaligned windows passing the decoder's
shape checks, not genuine firmware variation. The decoder's
``is_known_button_canonical`` and ``_is_garbage_chunk`` filters were
themselves added to mop up the phantoms that alternate-alignment
produced; both are now bypassed in the decoders (the helpers remain
in ``protocol.py`` for external callers and standalone unit tests).
"""

from __future__ import annotations

import logging
from typing import Any

from ..coordinator_protocol import CoordinatorProtocol
from .base import DecodedCommand
from .protocol import decode_command_payload, reverse_hex

_LOGGER = logging.getLogger(__name__)


_CRC_LEN = 6
_CHUNK_LENGTHS = {
    "switch_module": 12,
    "roller_module": 12,
    "dimmer_module": 16,
    # PC-Link / PC-Logic share a 16-byte (32 hex chars) per-record
    # storage format reverse-engineered from a Nikobus PC-software
    # serial trace on real hardware. Each register read returns one
    # complete record — either a module-registry entry (byte 0 == 0x03)
    # or a button → channel link record (byte 0 != 0x03 / 0xFF). The
    # Stage-1 best-guess of 12 (one BP cell ≈ switch chunk) was wrong;
    # the trace shows a 16-byte stride with no per-cell sub-structure
    # at the chunk layer.
    "pc_link": 32,
    "pc_logic": 32,
}


class BaseChunkingDecoder:
    module_type: str

    def __init__(self, coordinator: CoordinatorProtocol | None, module_type: str) -> None:
        self._coordinator = coordinator
        self.module_type = module_type
        self._module_address: str | None = None
        self._module_channel_count: int | None = None

    def can_handle(self, module_type: str) -> bool:
        return module_type == self.module_type

    def set_module_address(self, module_address: str | None) -> None:
        self._module_address = module_address

    def set_module_channel_count(self, module_channel_count: int | None) -> None:
        self._module_channel_count = module_channel_count

    def reset_scan_buffers(self) -> None:
        """Hook called by the discovery loop at every scan boundary.

        The base chunker holds no per-scan state of its own — records
        pack contiguously across frames and any partial chunk is
        returned to the caller via the ``remainder`` field for the
        caller to thread back as ``payload_buffer`` on the next
        ``analyze_frame_payload`` call. Subclasses (``pc_link_decoder``,
        ``pc_logic_decoder``) override this to clear their own
        per-scan state (registry buffers, etc.) and chain back to
        this no-op via ``super().reset_scan_buffers()``.
        """

    def analyze_frame_payload(self, payload_buffer: str, payload_and_crc: str) -> dict[str, Any] | None:
        payload_and_crc = payload_and_crc.upper()
        if len(payload_and_crc) < _CRC_LEN:
            _LOGGER.debug(
                "Skipped %s record — short payload %s",
                self.module_type,
                payload_and_crc,
            )
            return None

        data_region = payload_and_crc[: len(payload_and_crc) - _CRC_LEN]
        trailing_crc = payload_and_crc[len(payload_and_crc) - _CRC_LEN :]
        combined_payload = (payload_buffer + data_region).upper()

        expected_len = _CHUNK_LENGTHS.get(self.module_type)
        chunks: list[str] = []
        remainder = ""

        if expected_len:
            idx = 0
            while idx + expected_len <= len(combined_payload):
                chunks.append(combined_payload[idx : idx + expected_len])
                idx += expected_len
            remainder = combined_payload[idx:]

        return {
            "crc": trailing_crc,
            "payload_region": data_region,
            "chunks": chunks,
            "remainder": remainder,
        }

    def decode_chunk(self, chunk: str, module_address: str | None = None) -> list[DecodedCommand]:
        decoded = decode_command_payload(
            chunk,
            self.module_type,
            self._coordinator,
            module_address=module_address or self._module_address,
            reverse_before_decode=True,
            raw_chunk_hex=chunk,
            module_channel_count=self._module_channel_count,
        )

        if decoded is None:
            _LOGGER.debug(
                "Decoder returned no record for %s module %s — chunk %s",
                self.module_type,
                module_address or self._module_address,
                chunk,
            )
            return []

        command = DecodedCommand(
            module_type=self.module_type,
            raw_message=chunk,
            prefix_hex=None,
            chunk_hex=chunk,
            payload_hex=reverse_hex(chunk),
            metadata=decoded,
        )
        return [command]

    def decode(self, message: str, module_address: str | None = None) -> list[DecodedCommand]:
        return self.decode_chunk(message.strip().upper(), module_address)


__all__ = ["BaseChunkingDecoder"]
