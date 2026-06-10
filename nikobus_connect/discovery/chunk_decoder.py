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
        # Corruption detection for the current module scan, decided once
        # on the module's first frame (see analyze_frame_payload) and
        # re-armed per module via reset_scan_buffers. When set, the
        # module's link table doesn't align with the scanned window
        # (corrupt programming) and its decode is skipped wholesale.
        self._stream_alignment_decided = False
        self._module_misaligned = False

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
        self._module_misaligned = False

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

        # Corruption detection, decided ONCE on the module's first frame.
        # Some link tables start mid-record relative to the scanned
        # register window (production case: module 4707's first frame
        # opened with the 2-byte tail of a record preceding the window).
        # A fixed-stride walk then stays phase-shifted for the whole
        # table: phantom buttons decode "cleanly" while the module's real
        # records are lost. This is exactly what the Nikobus PC software
        # flags as a corrupt module needing reprogramming.
        #
        # We DON'T try to recover such a table — re-aligning a corrupt
        # scan can only ever yield a partial/uncertain picture (the
        # records pushed out of the scan window are simply gone), and
        # the proper fix is reprogramming. Instead we DETECT it (a
        # non-phase-0 byte offset matches the host inventory far better
        # than phase 0, under strict guards), skip the module's link
        # decode entirely so no phantom buttons enter the store, and
        # flag the module so the host can tell the user to reprogram it.
        if not self._stream_alignment_decided and not payload_buffer:
            self._stream_alignment_decided = True
            if self._looks_misaligned(combined_payload):
                self._module_misaligned = True
                _LOGGER.warning(
                    "Module %s link table looks corrupt — its records do "
                    "not align with the scanned register window (the "
                    "Nikobus PC software flags this as a module needing "
                    "reprogramming). Skipping its link decode; reprogram "
                    "the module in the Nikobus PC software to fix it.",
                    self._module_address,
                )

        if self._module_misaligned:
            # Consume the stream so the scan loop still completes, but
            # emit no chunks (no phantom buttons) and carry nothing over.
            return {
                "crc": trailing_crc,
                "payload_region": data_region,
                "chunks": [],
                "remainder": "",
                "misaligned": True,
            }

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
            "misaligned": False,
            "chunks": chunks,
            "remainder": remainder,
        }

    def _looks_misaligned(self, stream: str) -> bool:
        """True when the link table doesn't align with the scanned window.

        Evidence-gated, inventory-based: a non-phase-0 byte offset would
        decode far more host-known button addresses than phase 0 itself.
        That means phase 0 (what we actually decode) is reading records
        mid-stride — i.e. a corrupt table. Only output-module link tables
        (12-hex records, address in the leading 3 bytes) are eligible;
        PC-Link / PC-Logic registry layouts have their own structure.
        Returns False (decode normally) when there's no inventory to
        score against or the evidence is ambiguous.
        """
        if _CHUNK_LENGTHS.get(self.module_type) != 12:
            return False
        coordinator = self._coordinator
        if coordinator is None:
            return False

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
        # A shifted phase decodes >= 2 inventory buttons, strictly more
        # than phase 0, and is the unique maximum → phase 0 is misaligned.
        return (
            best_offset != 0
            and best_score >= 2
            and best_score > baseline
            and list(scores.values()).count(best_score) == 1
        )

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
