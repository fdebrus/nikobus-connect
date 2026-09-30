"""The 340-00112 family's memory image, as the Nikobus PC software
writes it.

The controller answers no register read (see :mod:`nikobus_connect.rgb`),
so nothing here has been read from a module. It comes from the vendor
software's own plugin for this family, ``Niko_05_010.dll`` (version
21.0.0.2, 2007), decompiled in September 2026: the plugin builds the
image the software writes into a 340-00112 / 340-00111 / 340-00113 from
the project database, and this module is that layout, byte for byte.
The plugin contains no bus traffic; that is the main executable's,
and ``nikobus.exe`` 4.3.1 was decompiled next. What it does with this
family (EEPROM types 11, the plinth light, and 12, the controller; 10 is
the wall buttons):

* **Writing.** The software puts the module in a link (programming)
  mode, clears it, writes the image in 16-byte blocks — block index =
  byte address / 16, so the link table is blocks 0x19–0xA8 — keeps the
  module in link mode for the whole write (other families are taken out
  of and back into it between blocks), then compares the CRC16 of the
  whole image with what the module's CRC query answers, still in link
  mode, and leaves link mode. This library carries none of those
  functions: a host reads state and presses keys, and nothing here
  writes to a module.
* **Reading.** Never. The upload routine skips EEPROM types 10, 11 and
  12 outright, before asking the module anything, and the plugin's own
  read-back lengths (:data:`RGB_READBACK_LENGTHS`) would end the loop at
  the first block anyway. That is the installation read captured on a
  real install (Nikobus-HA #519): every other module memory-read, the
  controller only status-polled.

So the one read the software ever makes of this family is the CRC, and
it makes it inside link mode. Whether the controller answers block reads
inside link mode, as it does not outside, is untested, and not a host's
question: entering link mode is a programming command.

The image is 7968 bytes in four blocks (:data:`RGB_IMAGE_BLOCKS`). The
link table is block 1: 128 slots of 18 bytes, an empty slot all
``0xFF``. :class:`RgbLinkRecord` is one slot; :func:`build_rgb_link_record`
applies the plugin's transformations to project values the way it does;
:func:`decode_rgb_link_table` reads a whole block back — which nobody
has managed on the bus yet, so it is there for the day a capture or a
read makes it possible, and to check a captured write against.

The address bytes hold the button address in the form the software
keeps it, not the form the wire carries: the plate's project address
shifted left by two with the key's 3-bit code in the low bits, which is
the bit-reversal of the ``#N`` address the key puts on the bus. The
software's own virtual key press is built the same way — it bit-reverses
``address << 2 | key`` into the ``#N`` frame — and the validating
install confirms it: plate ``124A36`` key 1C (code 0) presses
``#N1B1492`` and key 1D (code 2) ``#N5B1492``; the record for the 1C
link therefore starts ``49 28 D8``. :attr:`RgbLinkRecord.wire_address`
gives the bus form, :func:`address_from_wire` the inverse.

One more thing the executable shows: the software has no direct colour
command either. Its simulation mode drives switch and roller outputs
with the set-output functions (0x15 / 0x16), but a dimmer or colour
output is simulated by pressing the linked key on the bus, held by a
timer — which is what a host does too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from .discovery.mapping import RGB_MODE_NAMES

# --- the image ------------------------------------------------------------


class RgbImageBlock(NamedTuple):
    """One block of the image: where it starts and how long it is."""

    name: str
    start: int
    length: int

    @property
    def end(self) -> int:
        return self.start + self.length

    @property
    def registers(self) -> tuple[tuple[str, int], ...]:
        """The 16-byte blocks that cover it, as ``(sub_byte, register)``
        pairs the way the library addresses other modules' memory (block
        index = byte address / 16; high byte is the sub-byte, low byte
        the register). Whether this controller is addressed that way is
        unverified — it has answered no read at all."""
        first = self.start // RGB_BLOCK_SIZE
        last = (self.end - 1) // RGB_BLOCK_SIZE
        return tuple((f"{i >> 8:02X}", i & 0xFF) for i in range(first, last + 1))


#: The plugin hands the software 16-byte blocks for every range.
RGB_BLOCK_SIZE = 16

#: LED calibration profile, from the software's ``leddata.txt``.
RGB_LED_PROFILE_BLOCK = RgbImageBlock("led_profile", 0x0000, 0x0190)
#: The link table: 128 records of 18 bytes.
RGB_LINK_TABLE_BLOCK = RgbImageBlock("link_table", 0x0190, 0x0900)
#: Colour paths: 512 points of 10 bytes, then 32 path descriptors of 4.
RGB_COLOUR_PATH_BLOCK = RgbImageBlock("colour_paths", 0x0A90, 0x1480)
#: The controller's settings record.
RGB_CONFIG_BLOCK = RgbImageBlock("config", 0x1F10, 0x0010)

RGB_IMAGE_BLOCKS: tuple[RgbImageBlock, ...] = (
    RGB_LED_PROFILE_BLOCK,
    RGB_LINK_TABLE_BLOCK,
    RGB_COLOUR_PATH_BLOCK,
    RGB_CONFIG_BLOCK,
)
RGB_IMAGE_SIZE = 0x1F20

#: How many bytes of each block the plugin tells the software to read
#: back (``GetDLLReadInfo``, fixed-length field): none of the LED
#: profile, none of the link table, all of the colour paths and the
#: settings. The software's upload loop stops at the first block whose
#: lengths are both zero, so with block 0 at zero nothing is read — and
#: the upload routine skips this family before that anyway.
RGB_READBACK_LENGTHS: dict[str, int] = {
    RGB_LED_PROFILE_BLOCK.name: 0,
    RGB_LINK_TABLE_BLOCK.name: 0,
    RGB_COLOUR_PATH_BLOCK.name: RGB_COLOUR_PATH_BLOCK.length,
    RGB_CONFIG_BLOCK.name: RGB_CONFIG_BLOCK.length,
}

# --- the link record ------------------------------------------------------

RGB_LINK_RECORD_SIZE = 18
RGB_LINK_RECORD_COUNT = 128
RGB_EMPTY_LINK_RECORD = b"\xff" * RGB_LINK_RECORD_SIZE

#: Timer table for parameter 1 of modes 6 and 12, in seconds: a project
#: value 0–15 is stored as the table entry, anything else as is.
RGB_T1_SECONDS: tuple[int, ...] = (
    10, 60, 120, 180, 240, 300, 360, 420, 480, 540, 900, 1800, 2700, 3600, 5400, 7200,
)
#: Timer table for parameter 2 of every mode, in seconds, same rule.
RGB_T2_SECONDS: tuple[int, ...] = (
    1, 2, 4, 6, 8, 10, 15, 20, 30, 40, 50, 60, 120, 180, 240, 300,
)
#: The modes whose parameter 1 goes through :data:`RGB_T1_SECONDS`.
RGB_T1_MODES: frozenset[int] = frozenset({6, 12})
#: The modes whose parameter 1 the plugin overrides to 15, whatever the
#: project says: 3, 7, 9, 10 and every mode from 14 up.
_PARAM1_FORCED_LOW: frozenset[int] = frozenset({3, 7, 9, 10})
RGB_PARAM1_DEFAULT = 0
RGB_PARAM2_DEFAULT = 15

#: The colour a mode-3 link stores when the project gives none: CIE xy
#: (0.3127, 0.3290) in 1/65535 — D65 white.
RGB_D65_XY: tuple[int, int] = (0x500D, 0x543A)
#: The level bytes the same default writes.
RGB_DEFAULT_LEVEL = 0xFFFE
#: A 16-bit field the plugin left untouched (the record is pre-filled
#: with ``0xFF``).
RGB_ABSENT = 0xFFFF


def reverse_24(value: int) -> int:
    """Bit-reverse a 24-bit value: the record form of a button address to
    its wire form and back."""
    out = 0
    value &= 0xFFFFFF
    for _ in range(24):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def address_from_wire(wire_hex: str) -> int:
    """The record form of a key's ``#N`` bus address (``"1B1492"``)."""
    return reverse_24(int(wire_hex.replace("#N", ""), 16))


def record_address(plate_address: int, key_code: int) -> int:
    """The record form of a plate key: the plate's project address shifted
    left by two with the key's 3-bit code in the low bits, as the vendor
    software computes it. Key codes on a four-key plate: 1C 0, 1A 1, 1D
    2, 1B 3; on an eight-key plate 2C 0, 2A 1, 2D 2, 2B 3, 1C 4, 1A 5,
    1D 6, 1B 7."""
    return ((plate_address << 2) | (key_code & 0x07)) & 0xFFFFFF


def param1_is_forced(mode: int) -> bool:
    """Whether the plugin writes 15 as parameter 1 for ``mode``."""
    return mode in _PARAM1_FORCED_LOW or mode >= 14


@dataclass(frozen=True)
class RgbLinkRecord:
    """One 18-byte slot of the link table, fields as stored.

    ``address`` is the 24-bit button address as the plugin writes it (see
    the module docstring for what that is). ``mode`` is the link's mode
    number (M19 → 19), ``channel`` the output. The four parameter fields
    hold the bytes as written; :attr:`param1_seconds` and
    :attr:`param2_seconds` name them when they are timers. ``colour`` is
    the 32-bit CIE xy pair, ``level`` and ``param7`` the two remaining
    16-bit fields, ``colour_path`` the index into the colour-path table
    of block 2 with its flag bit split off.
    """

    address: int
    mode: int
    channel: int
    param1: int = RGB_ABSENT
    param2: int = RGB_ABSENT
    colour: int = 0xFFFFFFFF
    level: int = RGB_ABSENT
    param7: int = RGB_ABSENT
    colour_path: int = 0x7F
    colour_path_flag: bool = True
    filler: int = 0xFF

    @property
    def address_hex(self) -> str:
        return f"{self.address & 0xFFFFFF:06X}"

    @property
    def wire_address(self) -> str:
        """The key's bus address as a ``#N`` frame carries it (``"1B1492"``)."""
        return f"{reverse_24(self.address):06X}"

    @property
    def mode_label(self) -> str:
        name = RGB_MODE_NAMES.get(self.mode)
        return f"M{self.mode:02d} ({name})" if name else f"M{self.mode:02d}"

    @property
    def param1_seconds(self) -> int | None:
        """Parameter 1 as seconds, for the modes that store a timer there."""
        if self.mode in RGB_T1_MODES and self.param1 != RGB_ABSENT:
            return self.param1
        return None

    @property
    def param2_seconds(self) -> int | None:
        """Parameter 2 as seconds; every mode stores a timer there."""
        return None if self.param2 == RGB_ABSENT else self.param2

    @property
    def colour_xy(self) -> tuple[float, float] | None:
        """The stored colour as CIE xy, ``None`` when no colour is stored."""
        if self.colour == 0xFFFFFFFF:
            return None
        return ((self.colour >> 16) / 65535, (self.colour & 0xFFFF) / 65535)

    @property
    def has_colour_path(self) -> bool:
        return self.colour_path != 0x7F or not self.colour_path_flag

    def to_bytes(self) -> bytes:
        out = bytearray(RGB_EMPTY_LINK_RECORD)
        out[0:3] = (self.address & 0xFFFFFF).to_bytes(3, "big")
        out[3] = ((self.mode & 0x1F) << 3) | (self.channel & 0x07)
        out[4:6] = (self.param1 & 0xFFFF).to_bytes(2, "big")
        out[6:8] = (self.param2 & 0xFFFF).to_bytes(2, "big")
        out[8:12] = (self.colour & 0xFFFFFFFF).to_bytes(4, "big")
        out[12:14] = (self.level & 0xFFFF).to_bytes(2, "big")
        out[14:16] = (self.param7 & 0xFFFF).to_bytes(2, "big")
        out[16] = (self.colour_path & 0x7F) | (0x80 if self.colour_path_flag else 0)
        out[17] = self.filler & 0xFF
        return bytes(out)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> RgbLinkRecord:
        raw = bytes(data[:RGB_LINK_RECORD_SIZE])
        if len(raw) != RGB_LINK_RECORD_SIZE:
            raise ValueError(f"an RGB link record is {RGB_LINK_RECORD_SIZE} bytes, got {len(raw)}")
        return cls(
            address=int.from_bytes(raw[0:3], "big"),
            mode=raw[3] >> 3,
            channel=raw[3] & 0x07,
            param1=int.from_bytes(raw[4:6], "big"),
            param2=int.from_bytes(raw[6:8], "big"),
            colour=int.from_bytes(raw[8:12], "big"),
            level=int.from_bytes(raw[12:14], "big"),
            param7=int.from_bytes(raw[14:16], "big"),
            colour_path=raw[16] & 0x7F,
            colour_path_flag=bool(raw[16] & 0x80),
            filler=raw[17],
        )


def is_empty_rgb_link_record(data: bytes | bytearray) -> bool:
    """An unused slot: eighteen ``0xFF`` bytes."""
    return bytes(data[:RGB_LINK_RECORD_SIZE]) == RGB_EMPTY_LINK_RECORD


def build_rgb_link_record(
    *,
    address: int,
    mode: int,
    channel: int,
    param1: int | None = None,
    param2: int | None = None,
    colour_xy: tuple[int, int] | None = None,
    level: int | None = None,
    param7: int | None = None,
    colour_path: int | None = None,
    colour_path_flag: bool = False,
) -> RgbLinkRecord:
    """Compose a record from project values the way the plugin does.

    ``param1`` and ``param2`` are the project's 0–15 parameter values
    (``None`` for the project's defaults, 0 and 15); parameter 1 is
    overridden to 15 for the modes in :func:`param1_is_forced`, then
    looked up in :data:`RGB_T1_SECONDS` for modes 6 and 12, and
    parameter 2 in :data:`RGB_T2_SECONDS`. ``colour_xy`` is a CIE xy
    pair in 1/65535; a mode-3 link without one gets D65 white and the
    default level, any other mode leaves the colour bytes ``0xFF``.
    ``level`` is the project's raw level value, stored as
    ``((level >> 16) * 2) & 0xFFFF``; ``param7`` is stored as is below
    0x8000 and as ``(value | 0x20000) >> 2`` above. ``colour_path`` is
    the index of the path in block 2's table (0–31).
    """
    p1 = RGB_PARAM1_DEFAULT if param1 is None else param1
    if param1_is_forced(mode):
        p1 = 15
    if mode in RGB_T1_MODES and 0 <= p1 < len(RGB_T1_SECONDS):
        p1 = RGB_T1_SECONDS[p1]
    p2 = RGB_PARAM2_DEFAULT if param2 is None else param2
    if 0 <= p2 < len(RGB_T2_SECONDS):
        p2 = RGB_T2_SECONDS[p2]

    if colour_xy is None and mode == 3:
        colour = (RGB_D65_XY[0] << 16) | RGB_D65_XY[1]
        stored_level = RGB_DEFAULT_LEVEL
        stored_p7 = 0
    else:
        colour = 0xFFFFFFFF if colour_xy is None else ((colour_xy[0] & 0xFFFF) << 16) | (colour_xy[1] & 0xFFFF)
        stored_level = RGB_ABSENT if level is None else ((level >> 16) * 2) & 0xFFFF
        if param7 is None:
            stored_p7 = RGB_ABSENT
        else:
            stored_p7 = ((param7 | 0x20000) >> 2) & 0xFFFF if param7 > 0x7FFF else param7

    return RgbLinkRecord(
        address=address & 0xFFFFFF,
        mode=mode & 0x1F,
        channel=channel & 0x07,
        param1=p1 & 0xFFFF,
        param2=p2 & 0xFFFF,
        colour=colour,
        level=stored_level,
        param7=stored_p7,
        colour_path=0x7F if colour_path is None else colour_path & 0x7F,
        colour_path_flag=True if colour_path is None else colour_path_flag,
    )


def decode_rgb_link_table(data: bytes | bytearray) -> tuple[RgbLinkRecord, ...]:
    """Every used slot of a link-table block, in table order.

    ``data`` is the block as written (2304 bytes) or any prefix of it;
    a trailing partial slot is ignored, empty slots are skipped.
    """
    raw = bytes(data)
    records: list[RgbLinkRecord] = []
    for slot in range(min(len(raw) // RGB_LINK_RECORD_SIZE, RGB_LINK_RECORD_COUNT)):
        chunk = raw[slot * RGB_LINK_RECORD_SIZE : (slot + 1) * RGB_LINK_RECORD_SIZE]
        if is_empty_rgb_link_record(chunk):
            continue
        records.append(RgbLinkRecord.from_bytes(chunk))
    return tuple(records)


def encode_rgb_link_table(records: tuple[RgbLinkRecord, ...] | list[RgbLinkRecord]) -> bytes:
    """The link-table block the plugin would write for ``records``:
    slots in order, the rest ``0xFF``."""
    if len(records) > RGB_LINK_RECORD_COUNT:
        raise ValueError(f"the link table holds {RGB_LINK_RECORD_COUNT} records, got {len(records)}")
    body = b"".join(record.to_bytes() for record in records)
    return body + b"\xff" * (RGB_LINK_TABLE_BLOCK.length - len(body))


# --- the settings record ----------------------------------------------------


class RgbConfigRecord(NamedTuple):
    """Block 3, the sixteen settings bytes, as stored.

    The two 16-bit values come from the project's parameters 10001 and
    10002 for the component; ``flag_10005`` from parameter 10005;
    ``variant`` is parameter 10003's low two bits; ``not_own_component``
    is the plugin's flag that the address it derived from parameter
    10003 differs from the component's. What the settings mean to the
    controller — the software shows threshold, DMAX, DMIN, stand-alone
    and the LED profile for this record — is not tied to a byte yet.
    """

    value_10001: int
    value_10002: int
    byte4: int
    flag_10005: bool
    byte6: int
    not_own_component: bool
    byte8: int
    variant: int
    raw: bytes


def decode_rgb_config(data: bytes | bytearray) -> RgbConfigRecord:
    raw = bytes(data[: RGB_CONFIG_BLOCK.length])
    if len(raw) != RGB_CONFIG_BLOCK.length:
        raise ValueError(f"the settings record is {RGB_CONFIG_BLOCK.length} bytes, got {len(raw)}")
    return RgbConfigRecord(
        value_10001=int.from_bytes(raw[0:2], "big"),
        value_10002=int.from_bytes(raw[2:4], "big"),
        byte4=raw[4],
        flag_10005=raw[5] != 0,
        byte6=raw[6],
        not_own_component=raw[7] != 0,
        byte8=raw[8],
        variant=raw[9],
        raw=raw,
    )


# --- the colour paths -----------------------------------------------------

RGB_COLOUR_POINT_SIZE = 10
RGB_COLOUR_POINT_COUNT = 512
RGB_COLOUR_PATH_COUNT = 32
_POINTS_LENGTH = RGB_COLOUR_POINT_SIZE * RGB_COLOUR_POINT_COUNT


class RgbColourPoint(NamedTuple):
    """One point of a colour path: CIE xy in 1/65535, the cumulative
    speed value up to this point, and its relative luminance."""

    x: int
    y: int
    cumulative_speed: int
    rel_lumi: int


class RgbColourPath(NamedTuple):
    """One entry of the 32-entry path table with its points."""

    number: int
    start: int
    point_count: int
    jump_points: bool
    closed_loop: bool
    points: tuple[RgbColourPoint, ...]


def decode_rgb_colour_paths(data: bytes | bytearray) -> tuple[RgbColourPath, ...]:
    """The used entries of block 2's path table, each with its points.

    The block is 5120 bytes of points (big-endian words: x, y,
    cumulative speed, relative luminance, ``0xFFFF``) followed by 32
    four-byte descriptors: start point (big-endian 16-bit), a flags byte
    (bit 0 jump points, bit 1 closed loop) and the point count. An
    unused descriptor is all ``0xFF``.
    """
    raw = bytes(data)
    if len(raw) < RGB_COLOUR_PATH_BLOCK.length:
        raise ValueError(f"the colour-path block is {RGB_COLOUR_PATH_BLOCK.length} bytes, got {len(raw)}")
    paths: list[RgbColourPath] = []
    for index in range(RGB_COLOUR_PATH_COUNT):
        entry = raw[_POINTS_LENGTH + 4 * index : _POINTS_LENGTH + 4 * index + 4]
        if entry == b"\xff\xff\xff\xff":
            continue
        start = int.from_bytes(entry[0:2], "big")
        count = entry[3]
        points = []
        for n in range(start, min(start + count, RGB_COLOUR_POINT_COUNT)):
            p = raw[n * RGB_COLOUR_POINT_SIZE : (n + 1) * RGB_COLOUR_POINT_SIZE]
            points.append(
                RgbColourPoint(
                    x=int.from_bytes(p[0:2], "big"),
                    y=int.from_bytes(p[2:4], "big"),
                    cumulative_speed=int.from_bytes(p[4:6], "big"),
                    rel_lumi=int.from_bytes(p[6:8], "big"),
                )
            )
        paths.append(
            RgbColourPath(
                number=index,
                start=start,
                point_count=count,
                jump_points=bool(entry[2] & 0x01),
                closed_loop=bool(entry[2] & 0x02),
                points=tuple(points),
            )
        )
    return tuple(paths)


__all__ = [
    "RGB_ABSENT",
    "RGB_BLOCK_SIZE",
    "RGB_COLOUR_PATH_BLOCK",
    "RGB_CONFIG_BLOCK",
    "RGB_D65_XY",
    "RGB_DEFAULT_LEVEL",
    "RGB_EMPTY_LINK_RECORD",
    "RGB_IMAGE_BLOCKS",
    "RGB_IMAGE_SIZE",
    "RGB_LED_PROFILE_BLOCK",
    "RGB_LINK_RECORD_COUNT",
    "RGB_LINK_RECORD_SIZE",
    "RGB_LINK_TABLE_BLOCK",
    "RGB_READBACK_LENGTHS",
    "RGB_T1_MODES",
    "RGB_T1_SECONDS",
    "RGB_T2_SECONDS",
    "RgbColourPath",
    "RgbColourPoint",
    "RgbConfigRecord",
    "RgbImageBlock",
    "RgbLinkRecord",
    "address_from_wire",
    "build_rgb_link_record",
    "decode_rgb_colour_paths",
    "decode_rgb_config",
    "decode_rgb_link_table",
    "encode_rgb_link_table",
    "is_empty_rgb_link_record",
    "param1_is_forced",
    "record_address",
    "reverse_24",
]
