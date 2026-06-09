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
from .protocol import decode_command_payload, get_button_address, reverse_hex

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
        # Stream-alignment decision for the current module scan. Decided
        # once on the module's first frame (see analyze_frame_payload);
        # re-armed per module via reset_scan_buffers.
        self._stream_alignment_decided = False

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
        self._stream_alignment_decided = False

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

        # Bounded re-alignment, decided ONCE on the module's first frame.
        # Some tables start mid-record relative to the scanned register
        # window (production case: module 4707's first frame opened with
        # the 2-byte tail of a record preceding the window). A fixed-
        # stride walk then stays phase-shifted for the whole table:
        # phantom buttons decode "cleanly" while the module's REAL
        # records are lost. Try each of the 6 byte phases on the first
        # frame and keep the one that maximises button addresses known
        # to the host inventory — with strict guards (unique maximum,
        # >= 2 hits, strictly better than phase 0) so an ambiguous or
        # inventory-less stream keeps today's behaviour. This is the
        # narrow, evidence-gated version of the alt-alignment chunking
        # that was reverted in 0.9.0 for *creating* phantoms: it never
        # re-decides mid-stream and defaults to phase 0.
        if not self._stream_alignment_decided and not payload_buffer:
            self._stream_alignment_decided = True
            offset = self._inventory_alignment_offset(combined_payload)
            if offset:
                # Loud on purpose: a mid-record table almost always means
                # the module's stored programming is damaged — the Nikobus
                # PC software flags exactly this as "corrupted, reprogram".
                # Re-aligning recovers the (intact) links so HA stays
                # usable, but reprogramming the module is the real fix and
                # the user must not be left unaware of it.
                _LOGGER.warning(
                    "Module %s link table is misaligned — its first record "
                    "starts %d hex chars before the scanned window. This "
                    "usually means the module's stored programming is "
                    "corrupt (the Nikobus PC software will flag it for "
                    "reprogramming). Re-aligned the read to recover the "
                    "links; reprogramming the module is the proper fix.",
                    self._module_address,
                    offset,
                )
                combined_payload = combined_payload[offset:]

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

    def _inventory_alignment_offset(self, stream: str) -> int:
        """Pick the chunk-walk phase that matches the host inventory.

        Returns the hex offset (0/2/4/6/8/10) to drop from the stream
        head, or 0 to keep the current behaviour. Only output-module
        link tables (12-hex records, address in the leading 3 bytes)
        are eligible; PC-Link / PC-Logic registry layouts have their
        own structure.
        """
        if _CHUNK_LENGTHS.get(self.module_type) != 12:
            return 0
        coordinator = self._coordinator
        if coordinator is None:
            return 0

        def _hits(offset: int) -> int:
            count = 0
            idx = offset
            while idx + 12 <= len(stream):
                chunk = stream[idx : idx + 12]
                idx += 12
                head = chunk[:6]
                if head in ("FFFFFF", "000000"):
                    continue
                addr = get_button_address(reverse_hex(head))
                if not addr:
                    continue
                try:
                    channels = coordinator.get_button_channels(addr)
                except Exception:  # pragma: no cover - defensive
                    channels = None
                if isinstance(channels, int) and channels > 0:
                    count += 1
            return count

        scores = {offset: _hits(offset) for offset in (0, 2, 4, 6, 8, 10)}
        baseline = scores[0]
        best_offset, best_score = max(
            scores.items(), key=lambda kv: (kv[1], -kv[0])
        )
        if (
            best_offset != 0
            and best_score >= 2
            and best_score > baseline
            and list(scores.values()).count(best_score) == 1
        ):
            return best_offset
        return 0

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
