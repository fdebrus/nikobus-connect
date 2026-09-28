"""Audio Distribution module (05-205) link-table decoder.

The audio module keeps its links in memory bank ``01`` instead of the
output bank ``04``, which is empty on it. The band opens with a two-byte
header — the record count, then ``00`` — followed by that many six-byte
records:

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
from .protocol import _is_all_ff, _safe_int, reverse_hex

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


def split_link_table(stream_hex: str) -> list[str]:
    """Split a bank-01 byte stream into its six-byte records.

    The band starts with filler ``FF`` bytes, then ``<count> 00``, then
    ``count`` records. Returns the records as hex strings; an empty list
    when no header is present (an empty or unreadable band).
    """
    stream = (stream_hex or "").upper()
    index = 0
    while stream.startswith("FF", index):
        index += 2
    if index + 4 > len(stream):
        return []
    count = _safe_int(stream[index : index + 2])
    if not count or stream[index + 2 : index + 4] != "00":
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

    def reset_scan_buffers(self) -> None:
        super().reset_scan_buffers()
        self._header_seen = False
        self._records_left = None

    def analyze_frame_payload(
        self, payload_buffer: str, payload_and_crc: str
    ) -> dict[str, Any] | None:
        payload_and_crc = payload_and_crc.upper()
        if len(payload_and_crc) < 6:
            return None
        data_region = payload_and_crc[:-6]
        trailing_crc = payload_and_crc[-6:]
        stream = (payload_buffer + data_region).upper()

        if not self._header_seen:
            index = 0
            while stream.startswith("FF", index):
                index += 2
            if index + 4 > len(stream):
                # Nothing but filler so far — keep only a possible
                # partial header for the next frame.
                return {
                    "crc": trailing_crc,
                    "payload_region": data_region,
                    "misaligned": False,
                    "chunks": [],
                    "remainder": stream[index:],
                }
            count = _safe_int(stream[index : index + 2])
            if not count or stream[index + 2 : index + 4] != "00":
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

        return {
            "crc": trailing_crc,
            "payload_region": data_region,
            "misaligned": False,
            "chunks": chunks,
            "remainder": stream[idx:] if self._records_left else "",
        }


__all__ = [
    "AUDIO_MODE_NAMES",
    "OBJECT_POWER",
    "AudioDecoder",
    "audio_function_label",
    "audio_mode_number",
    "audio_object_label",
    "decode",
    "split_link_table",
]
