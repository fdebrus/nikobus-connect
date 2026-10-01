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

from ..coordinator_protocol import CoordinatorProtocol
from .base import DecodedCommand
from .chunk_decoder import BaseChunkingDecoder
from .pc_link_decoder import _ScanCounts, _decode_and_log, _emit_scan_summary
from .pc_record_parser import RegistryBuffer
from .protocol import _reverse_24, blocks_to_sections, get_button_address, reverse_hex

_LOGGER = logging.getLogger(__name__)
_LOG_PREFIX = "PC-Logic"


def decode(payload_hex: str, raw_bytes: list[str], context: Any) -> dict[str, Any] | None:
    """Module-level decoder hook used by ``decode_command_payload``.

    One-shot path with no registry buffer plumbed through, so the
    resolver can't run here and no ``DecodedCommand`` can be returned.
    Logs the parsed record at DEBUG for visibility and returns ``None``.
    Class-based scans go through ``PcLogicDecoder`` instead, which
    carries a per-scan registry, emits commands for resolved link
    records, and logs one INFO summary per module.
    """

    _decode_and_log(
        payload_hex,
        getattr(context, "module_address", None),
        coordinator=context.coordinator,
        prefix=_LOG_PREFIX,
        registry=None,
        module_type=None,
        logger=_LOGGER,
    )
    return None


# --- The PC-Logic's own input table ----------------------------------------
#
# Besides the registry-shaped records above, the vendor plugin
# (``Niko_05_200``, ``CalcMemoryMap`` block 2) writes a table of every
# link whose *output* is the PC-Logic itself: the wall keys and other
# transmitters that feed its logic. A two-byte little-endian count at
# byte 998, then from byte 1000 one six-byte record per link, sorted,
# room for 1536:
#
#     [addr 23:16] [addr 15:8] [addr 7:0] [input] [slot] [mode]
#
# The address is the software's form, ``plate << 2 | key_code``, the
# same one the switch records store (link parameter 4 adds 4 to it, the
# key half). Bytes 3–5 come from the link's row in the vendor
# database: an input index written one less than the software counts
# it, a slot within a twelve-wide grid, and a mode byte. This reading
# is from the plugin's composer alone — no capture of a programmed
# table exists yet — so the records are surfaced (``input_links``, the
# scan summary) and not turned into entities.
#
# Byte 998 is sub-byte ``00`` register ``0x3E``, offset 6; the plan's
# sub-``00`` pass ends at ``0x3F``, so the fixed plan sees at most
# three records. ``extension_passes`` asks for the rest once the count
# is known.

PC_LOGIC_INPUT_COUNT_ADDRESS = 998
PC_LOGIC_INPUT_TABLE_ADDRESS = 1000
PC_LOGIC_INPUT_MAX_RECORDS = 1536
_INPUT_RECORD_LEN = 6
_INPUT_FIRST_BLOCK = PC_LOGIC_INPUT_COUNT_ADDRESS // 16  # 0x3E
_INPUT_STREAM_OFFSET = PC_LOGIC_INPUT_COUNT_ADDRESS - _INPUT_FIRST_BLOCK * 16  # 6
_INPUT_TABLE_LAST_BLOCK = (
    PC_LOGIC_INPUT_TABLE_ADDRESS + PC_LOGIC_INPUT_MAX_RECORDS * _INPUT_RECORD_LEN - 1
) // 16


def _input_table_last_block(count: int | None) -> int:
    """The last block the table reaches for ``count`` records.

    Before the count is known the answer is the plan's own last block,
    ``0x3F``, so nothing outside the fixed band is claimed on a guess.
    """
    if count is None:
        return _INPUT_FIRST_BLOCK + 1
    if count <= 0:
        return _INPUT_FIRST_BLOCK
    end = PC_LOGIC_INPUT_TABLE_ADDRESS + count * _INPUT_RECORD_LEN - 1
    return min(end // 16, _INPUT_TABLE_LAST_BLOCK)


def decode_input_link(record: bytes) -> dict[str, Any] | None:
    """Decode one six-byte record of the PC-Logic's input table.

    ``None`` for an erased slot (all ``FF``) or a short record. The
    address is reported three ways: as stored, as the plate address
    the library files buttons under (``button_address``), and as the
    ``#N`` frame the key puts on the bus (``wire_address``).
    ``get_button_address`` takes the three address bytes in the order
    the other decoders hand them, which is reversed (the chunk layer
    byte-reverses a record before decoding), so the stored big-endian
    address is reversed first.
    """
    if len(record) != _INPUT_RECORD_LEN or all(b == 0xFF for b in record):
        return None
    address = int.from_bytes(record[:3], "big")
    stored = f"{address:06X}"
    return {
        "record_address": stored,
        "button_address": get_button_address(reverse_hex(stored)),
        "wire_address": f"{_reverse_24(address):06X}",
        "key_code": address & 0x07,
        "input_index": record[3],
        "slot": record[4],
        "mode_raw": record[5],
        "raw": record.hex().upper(),
        "record_source": "pc_logic_input_table",
    }


class PcLogicDecoder(BaseChunkingDecoder):
    """PC-Logic variant of the chunk-based decoder pipeline.

    Same on-wire format as ``PcLinkDecoder`` — both share the
    parser, the registry buffer, and the link-target resolver. The
    only difference is the log prefix used for diagnostic lines.

    On top of that, the blocks that hold the PC-Logic's own input
    table (see the note above) are taken out of the 16-byte record
    stream and decoded as six-byte records; the result is kept in
    ``input_links`` for the module being scanned and, once its scan
    ends, in ``input_links_by_module``.
    """

    def __init__(self, coordinator: CoordinatorProtocol | None) -> None:
        super().__init__(coordinator, "pc_logic")
        self._registry = RegistryBuffer()
        self._scan_counts = _ScanCounts()
        self._current_block: int | None = None
        self._input_stream = bytearray()
        self._input_count: int | None = None
        self._input_broken = False
        self._extension_from: int | None = None
        self.input_links: list[dict[str, Any]] = []
        self.input_links_by_module: dict[str, list[dict[str, Any]]] = {}

    def reset_registry(self) -> None:
        """Clear the registry buffer between scans."""

        self._registry.reset()

    def reset_scan_buffers(self) -> None:
        """Clear per-scan state and emit the per-module scan summary.

        Extends the base alt-alignment reset with the registry reset so
        a fresh scan starts with no carried registry state, and emits
        the single INFO summary for the module that just finished.
        """

        super().reset_scan_buffers()
        if self._input_count is not None and self._module_address:
            self.input_links_by_module[self._module_address] = list(self.input_links)
            _LOGGER.info(
                "%s scan of module %s — input table holds %d link(s), %d read",
                _LOG_PREFIX,
                self._module_address,
                self._input_count,
                len(self.input_links),
            )
        _emit_scan_summary(
            _LOG_PREFIX, self._module_address, self._scan_counts, _LOGGER
        )
        self._registry.reset()
        self._current_block = None
        self._input_stream = bytearray()
        self._input_count = None
        self._input_broken = False
        self._extension_from = None
        self.input_links = []

    def set_current_block(self, sub_byte: str, register: int) -> None:
        """Called by the scan loop before each register read."""
        try:
            self._current_block = int(sub_byte, 16) << 8 | int(register)
        except (TypeError, ValueError):
            self._current_block = None

    def extension_passes(self) -> tuple[tuple[str, tuple[int, ...]], ...]:
        """Blocks of the input table the fixed plan did not cover.

        ``()`` until the count is known, once every record is in, when
        a block in the middle went unanswered (the table is read in
        order, so a gap ends it), or when the previous extension
        brought nothing new.
        """
        count = self._input_count
        if count is None or self._input_broken or len(self.input_links) >= count:
            return ()
        have = len(self._input_stream)
        first = _INPUT_FIRST_BLOCK + have // 16
        if self._extension_from is not None and first <= self._extension_from:
            return ()
        last = _input_table_last_block(count)
        if last < first:
            return ()
        self._extension_from = first
        return blocks_to_sections(first, last)

    def analyze_frame_payload(
        self, payload_buffer: str, payload_and_crc: str
    ) -> dict[str, Any] | None:
        block = self._current_block
        if block is not None and _INPUT_FIRST_BLOCK <= block <= _input_table_last_block(
            self._input_count
        ):
            payload_and_crc = payload_and_crc.upper()
            if len(payload_and_crc) < 6:
                return None
            data_region = payload_and_crc[:-6]
            self._absorb_input_block(block, data_region)
            return {
                "crc": payload_and_crc[-6:],
                "payload_region": data_region,
                "misaligned": False,
                "chunks": [],
                "remainder": payload_buffer,
            }
        return super().analyze_frame_payload(payload_buffer, payload_and_crc)

    def _absorb_input_block(self, block: int, data_hex: str) -> None:
        """Append one block of the input table and decode what is complete."""
        if self._input_broken:
            return
        expected = _INPUT_FIRST_BLOCK + len(self._input_stream) // 16
        if block != expected:
            _LOGGER.debug(
                "%s module %s — input table block 0x%03X arrived, 0x%03X expected; "
                "stopping the table read",
                _LOG_PREFIX,
                self._module_address,
                block,
                expected,
            )
            self._input_broken = True
            return
        try:
            self._input_stream += bytes.fromhex(data_hex)
        except ValueError:
            self._input_broken = True
            return

        if self._input_count is None:
            if len(self._input_stream) < _INPUT_STREAM_OFFSET + 2:
                return
            count = int.from_bytes(
                self._input_stream[_INPUT_STREAM_OFFSET : _INPUT_STREAM_OFFSET + 2],
                "little",
            )
            if count > PC_LOGIC_INPUT_MAX_RECORDS:
                # Erased memory (FFFF) or not a table: read nothing more.
                count = 0
            self._input_count = count
            _LOGGER.debug(
                "%s module %s — input table holds %d link(s)",
                _LOG_PREFIX,
                self._module_address,
                count,
            )

        start = _INPUT_STREAM_OFFSET + 2
        while len(self.input_links) < self._input_count:
            index = len(self.input_links)
            begin = start + index * _INPUT_RECORD_LEN
            end = begin + _INPUT_RECORD_LEN
            if end > len(self._input_stream):
                break
            decoded = decode_input_link(bytes(self._input_stream[begin:end]))
            if decoded is None:
                # An erased slot inside the counted range: the table is
                # shorter than its count says. Stop here.
                self._input_count = index
                break
            self.input_links.append(decoded)
            _LOGGER.debug(
                "%s module %s — input link %d: key %s (#N%s) -> input %d slot %d mode 0x%02X",
                _LOG_PREFIX,
                self._module_address,
                index,
                decoded["button_address"],
                decoded["wire_address"],
                decoded["input_index"],
                decoded["slot"],
                decoded["mode_raw"],
            )

    def decode_chunk(
        self, chunk: str, module_address: str | None = None
    ) -> list[DecodedCommand]:
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
            counts=self._scan_counts,
        )


__all__ = [
    "PC_LOGIC_INPUT_COUNT_ADDRESS",
    "PC_LOGIC_INPUT_MAX_RECORDS",
    "PC_LOGIC_INPUT_TABLE_ADDRESS",
    "PcLogicDecoder",
    "decode",
    "decode_input_link",
]
