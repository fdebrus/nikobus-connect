"""Audio Distribution module (05-205) link-table decoder.

The audio module keeps its links in memory bank ``01`` instead of the
output bank ``04``, which is empty on it. The band opens with a two-byte
header — the record count, little-endian — followed by that many
six-byte records (the vendor plugin writes the count at byte 4998 and
the records from byte 5000, room for 1864 of them; the scan reads the
fixed band first and extends it from the count, see
``AudioDecoder.extension_passes``):

``<bus address (3 bytes)> <function> <object> 01``

The three address bytes are the ``#N`` payload **verbatim**: the frame a
source puts on the bus to drive that function. They must not be passed
through the wall-button address transform, which assumes a keypad's
address layout and returns a different (wrong) plate and key for these
virtual banks — confirmed on the validating install, where the module's
own table and the owner's programmed virtual buttons matched byte for
byte.

The fifth byte names the **object** the function acts on, not a zone:
``0x00``–``0x03`` are zones 1–4 and ``0x08`` is the module's own Power
object (the vendor's object type 127, "Audio Power"). The distinction
matters because the same function byte means different things on the
two: ``0x08`` is M11 "source toggle" on a zone and M01 "power" on the
Power object, which is what the validating install's owner had
programmed as his power key.

Validated against a real 05-205 (2026-09-27, module 8334, 35 records):
32 links covering four zones by eight functions, plus three more from a
second virtual bank. The functions decoded from the bus match the ones
the owner had programmed, and the whole table — every function byte
from ``0x00`` to ``0x1C`` — was confirmed against his project file
(2026-09-28).
"""

from __future__ import annotations

import logging
from typing import Any

from ..coordinator_protocol import CoordinatorProtocol
from .chunk_decoder import BaseChunkingDecoder
from .protocol import _is_all_ff, _safe_int, blocks_to_sections, reverse_hex

_LOGGER = logging.getLogger(__name__)

RECORD_HEX_LEN = 12  # six bytes

# On a **zone** object, function byte + 3 = the vendor's mode number, so
# 0x00 is M03 and 0x0E is M17. Names are the vendor's own (``S_DB_AL_*``
# in its mode table). The Power object numbers its one function
# differently — see ``POWER_MODE``.
AUDIO_MODE_NAMES: dict[int, str] = {
    1: "Power",
    2: "Input 8",
    3: "Source 1",
    4: "Source 2",
    5: "Source 3",
    6: "Source 4",
    7: "Source 5",
    8: "Source 6",
    9: "Source 7",
    10: "Source 8",
    11: "Source toggle",
    12: "Microphone",
    13: "Volume up",
    14: "Volume down",
    15: "On / off",
    16: "On",
    17: "Off",
    18: "Treble up",
    19: "Treble down",
    20: "Bass up",
    21: "Bass down",
    22: "Balance right",
    23: "Balance left",
    24: "Preset 1",
    25: "Preset 2",
    26: "Preset 3",
    27: "Preset 4",
    28: "Preset 5",
    29: "Preset 6",
    30: "Preset 7",
    31: "Preset 8",
}

# The object byte. Zones count from zero; ``0x08`` is not a zone at all
# but the module's own Power object, the vendor's object type 127.
OBJECT_POWER = 0x08
_MAX_ZONE = 0x03
#: The Power object carries one link mode, M01, under function byte 0x08.
POWER_FUNCTION = 0x08
POWER_MODE = 1


def audio_mode_number(function_raw: int, object_raw: int = 0) -> int:
    """The vendor mode number a function byte stands for on an object.

    A zone numbers its modes three above the function byte; the Power
    object's single function (``0x08``) is M01 instead.
    """
    if object_raw == OBJECT_POWER:
        return POWER_MODE if function_raw == POWER_FUNCTION else function_raw + 3
    return function_raw + 3


def audio_function_label(function_raw: int, object_raw: int = 0) -> str:
    """``"M13 (Volume up)"`` for a function byte on an object."""
    mode = audio_mode_number(function_raw, object_raw)
    name = AUDIO_MODE_NAMES.get(mode)
    return f"M{mode:02d} ({name})" if name else f"M{mode:02d}"


def audio_object_label(object_raw: int) -> str:
    """``"Zone 2"``, or ``"Power"`` for the module's own power object."""
    if object_raw == OBJECT_POWER:
        return "Power"
    return f"Zone {object_raw + 1}"


#: Where the vendor plugin (``Niko_05_202``) puts the table: a
#: two-byte little-endian record count at byte 4998, the records from
#: byte 5000, room for 1864 of them. Byte 4998 is sub-byte ``01``
#: register ``0x38``, offset 6 — the head of the band the scan plan
#: reads first.
AUDIO_COUNT_ADDRESS = 4998
AUDIO_TABLE_ADDRESS = 5000
AUDIO_MAX_RECORDS = 1864
#: The last 16-byte block a full table can reach (block ``0x3F3``).
AUDIO_TABLE_LAST_BLOCK = (AUDIO_TABLE_ADDRESS + AUDIO_MAX_RECORDS * 6 - 1) // 16


def _count_header(stream: str, index: int) -> int | None:
    """The record count at ``stream[index:]``, or ``None`` if no header is there.

    The count is two bytes little-endian (the vendor writes it that
    way; the validating module's ``23 00`` is 35). Zero, an erased
    ``FFFF`` and anything past the table's capacity are not a header.
    """
    low = _safe_int(stream[index : index + 2])
    high = _safe_int(stream[index + 2 : index + 4])
    if low is None or high is None:
        return None
    count = low | high << 8
    if not 0 < count <= AUDIO_MAX_RECORDS:
        return None
    return count


#: Offset of the count within the band's first block: byte 4998 sits at
#: offset 6 of block ``0x138`` (byte 4992), the first block the plan reads.
_HEADER_OFFSET_HEX = (AUDIO_COUNT_ADDRESS - (AUDIO_COUNT_ADDRESS // 16) * 16) * 2


def _header_index(stream: str) -> int | None:
    """Where the two-byte count starts in ``stream``, or ``None``.

    The vendor writes the count at a fixed place, byte 4998, which is
    offset 6 of the first block the plan reads; that position is tried
    first. Only when nothing valid sits there is the leading ``FF``
    filler skipped — and a count whose low byte is ``FF`` (255, 511, …)
    is exactly what a greedy skip would eat, which is why the fixed
    position comes first.
    """
    if (
        len(stream) >= _HEADER_OFFSET_HEX + 4
        and stream[:_HEADER_OFFSET_HEX] == "FF" * (_HEADER_OFFSET_HEX // 2)
        and _count_header(stream, _HEADER_OFFSET_HEX) is not None
    ):
        return _HEADER_OFFSET_HEX
    index = 0
    while stream.startswith("FF", index):
        index += 2
    if index + 4 > len(stream):
        return None
    return index


def split_link_table(stream_hex: str) -> list[str]:
    """Split a bank-01 byte stream into its six-byte records.

    The band starts with filler ``FF`` bytes, then the two-byte record
    count, then ``count`` records. Returns the records as hex strings;
    an empty list when no header is present (an empty or unreadable
    band).
    """
    stream = (stream_hex or "").upper()
    index = _header_index(stream)
    if index is None:
        return []
    count = _count_header(stream, index)
    if count is None:
        return []
    body = stream[index + 4 :]
    records = [
        body[i * RECORD_HEX_LEN : (i + 1) * RECORD_HEX_LEN] for i in range(count)
    ]
    return [rec for rec in records if len(rec) == RECORD_HEX_LEN]


def decode(payload_hex: str, raw_bytes: list[str], context: Any) -> dict[str, Any] | None:
    """Decode one six-byte audio link record.

    ``payload_hex`` arrives byte-reversed (the chunk layer's
    convention), so the address occupies the last three bytes and reads
    back with :func:`reverse_hex`.
    """
    if _is_all_ff(payload_hex, RECORD_HEX_LEN):
        return None
    if len(raw_bytes) != 6:
        _LOGGER.debug(
            "Skipped audio module %s — invalid length, payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    if raw_bytes[0] != "01":
        # The vendor's own upload decoder takes a record only when its
        # sixth byte is 1; anything else is an erased or partial slot.
        _LOGGER.debug(
            "Skipped audio module %s — validity byte %s, payload %s",
            context.module_address,
            raw_bytes[0],
            payload_hex,
        )
        return None
    object_raw = _safe_int(raw_bytes[1])
    function_raw = _safe_int(raw_bytes[2])
    if object_raw is None or function_raw is None:
        return None
    if object_raw > _MAX_ZONE and object_raw != OBJECT_POWER:
        _LOGGER.debug(
            "Skipped audio module %s — object byte 0x%02X out of range, payload %s",
            context.module_address,
            object_raw,
            payload_hex,
        )
        return None

    is_power = object_raw == OBJECT_POWER
    zone = None if is_power else object_raw + 1
    bus_address = reverse_hex(payload_hex[-6:])
    label = audio_function_label(function_raw, object_raw)
    object_label = audio_object_label(object_raw)
    name = AUDIO_MODE_NAMES.get(audio_mode_number(function_raw, object_raw), label)

    decoded = {
        "payload": payload_hex,
        # Verbatim: this IS the #N frame that drives the function.
        "bus_address": bus_address,
        "button_address": bus_address,
        "push_button_address": bus_address,
        # A trigger belongs to no keypad, so it has no key of its own.
        # The merge still needs one: a record without a key never
        # reaches the button store (see ``add_to_command_mapping``), and
        # an audio module's triggers each have their own address, so one
        # key for all of them collides with nothing.
        "key_raw": 0,
        "audio_function": label,
        "audio_function_raw": function_raw,
        "audio_object": object_label,
        "audio_object_raw": object_raw,
        "audio_power": is_power,
        "audio_zone": zone,
        "audio_zone_raw": object_raw,
        "description": f"{object_label} {name}" if not is_power else f"Audio {name}",
        "channel": zone,
        "M": label,
        "T1": None,
        "T2": None,
        "record_source": "output_module_table",
    }

    _LOGGER.debug(
        "Decoded audio module %s — %s %s from #N%s",
        context.module_address,
        object_label,
        label,
        bus_address,
    )
    return decoded


class AudioDecoder(BaseChunkingDecoder):
    """Chunk decoder for the audio module's bank-01 link table.

    Overrides the chunking so the band's ``<count> 00`` header is
    consumed before the six-byte records start; the base class assumes a
    stream that begins on a record boundary.
    """

    def __init__(self, coordinator: CoordinatorProtocol | None) -> None:
        super().__init__(coordinator, "audio_module")
        self._header_seen = False
        self._records_left: int | None = None
        # Block bookkeeping for the count-driven extension: the block
        # the scan is reading now, the last one that answered, how many
        # bytes of a record straddle the frame boundary, and where the
        # previous extension started (so a module that stops answering
        # cannot be asked for the same blocks twice).
        self._current_block: int | None = None
        self._last_data_block: int | None = None
        self._partial_bytes = 0
        self._extension_from: int | None = None

    def reset_scan_buffers(self) -> None:
        super().reset_scan_buffers()
        self._header_seen = False
        self._records_left = None
        self._current_block = None
        self._last_data_block = None
        self._partial_bytes = 0
        self._extension_from = None

    def set_current_block(self, sub_byte: str, register: int) -> None:
        """Called by the scan loop before each register read."""
        try:
            self._current_block = int(sub_byte, 16) << 8 | int(register)
        except (TypeError, ValueError):
            self._current_block = None

    def extension_passes(self) -> tuple[tuple[str, tuple[int, ...]], ...]:
        """Blocks still to read once the fixed band has been consumed.

        The band the plan reads (``0x38``–``0x5F`` of sub-byte ``01``)
        holds about 105 records; the vendor allows 1864. When the count
        at the head of the band says more records follow than the band
        held, this returns the sections that hold the rest — from the
        block after the last one that answered, one block of slack, up
        to the table's capacity — and ``()`` once the table is in, or
        when the module stopped answering.
        """
        if (
            not self._header_seen
            or not self._records_left
            or self._last_data_block is None
        ):
            return ()
        first = self._last_data_block + 1
        if self._extension_from is not None and first <= self._extension_from:
            return ()
        pending = max(self._records_left * 6 - self._partial_bytes, 1)
        blocks = -(-pending // 16) + 1
        last = min(first + blocks - 1, AUDIO_TABLE_LAST_BLOCK)
        if last < first:
            return ()
        self._extension_from = first
        return blocks_to_sections(first, last)

    def analyze_frame_payload(
        self, payload_buffer: str, payload_and_crc: str
    ) -> dict[str, Any] | None:
        payload_and_crc = payload_and_crc.upper()
        if len(payload_and_crc) < 6:
            return None
        data_region = payload_and_crc[:-6]
        trailing_crc = payload_and_crc[-6:]
        stream = (payload_buffer + data_region).upper()
        self._last_data_block = self._current_block

        if not self._header_seen:
            index = _header_index(stream)
            if index is None:
                # Nothing but filler so far — keep what there is for
                # the next frame, the header may straddle the boundary.
                return {
                    "crc": trailing_crc,
                    "payload_region": data_region,
                    "misaligned": False,
                    "chunks": [],
                    "remainder": stream,
                }
            count = _count_header(stream, index)
            if count is None:
                _LOGGER.debug(
                    "Audio module %s — no record-count header in %s",
                    self._module_address,
                    stream[:16],
                )
                return {
                    "crc": trailing_crc,
                    "payload_region": data_region,
                    "misaligned": False,
                    "chunks": [],
                    "remainder": "",
                }
            self._header_seen = True
            self._records_left = count
            stream = stream[index + 4 :]
            _LOGGER.debug(
                "Audio module %s — link table holds %d records",
                self._module_address,
                count,
            )

        chunks: list[str] = []
        idx = 0
        while (
            idx + RECORD_HEX_LEN <= len(stream)
            and (self._records_left is None or self._records_left > 0)
        ):
            chunks.append(stream[idx : idx + RECORD_HEX_LEN])
            idx += RECORD_HEX_LEN
            if self._records_left is not None:
                self._records_left -= 1

        remainder = stream[idx:] if self._records_left else ""
        self._partial_bytes = len(remainder) // 2
        return {
            "crc": trailing_crc,
            "payload_region": data_region,
            "misaligned": False,
            "chunks": chunks,
            "remainder": remainder,
        }


__all__ = [
    "AUDIO_COUNT_ADDRESS",
    "AUDIO_MAX_RECORDS",
    "AUDIO_MODE_NAMES",
    "AUDIO_TABLE_ADDRESS",
    "AUDIO_TABLE_LAST_BLOCK",
    "OBJECT_POWER",
    "AudioDecoder",
    "audio_function_label",
    "audio_mode_number",
    "audio_object_label",
    "decode",
    "split_link_table",
]
