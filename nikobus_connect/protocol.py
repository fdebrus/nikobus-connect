"""Nikobus Protocol Utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final


def int_to_hex(value: int, digits: int) -> str:
    """Convert an integer to a hexadecimal string with a specified number of digits."""
    return f"{value:0{digits}X}"


def calc_crc1(data: str) -> int:
    """Calculate CRC-16/ANSI X3.28 (CRC-16-IBM) for the given data."""
    crc = 0xFFFF
    for byte in bytes.fromhex(data):
        crc ^= (byte << 8)
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if (crc >> 15) & 1 else (crc << 1)
    return crc & 0xFFFF


def calc_crc2(data: str) -> int:
    """Calculate CRC-8 (CRC-8-ATM) for the given data."""
    crc = 0
    for char in data:
        crc ^= ord(char)
        for _ in range(8):
            crc = (crc << 1) ^ 0x99 if (crc & 0xFF) >> 7 else crc << 1
    return crc & 0xFF


def append_crc1(data: str) -> str:
    """Append CRC-16/ANSI X3.28 (CRC-16-IBM) to the given data."""
    return data + int_to_hex(calc_crc1(data), 4)


def append_crc2(data: str) -> str:
    """Append CRC-8 (CRC-8-ATM) to the given data."""
    return data + int_to_hex(calc_crc2(data), 2)


def make_pc_link_command(
    func: int, addr: str, args: bytes | bytearray | None = None
) -> str:
    """Construct a PC link command with the specified function, address, and optional arguments."""
    addr_int = int(addr, 16)
    data = int_to_hex(func, 2) + addr_int.to_bytes(2, byteorder='little').hex().upper()
    if args:
        data += args.hex().upper()
    return append_crc2(f"${int_to_hex(len(data) + 10, 2)}{append_crc1(data)}")


def calculate_group_number(channel: int) -> int:
    """Calculate the group number of a channel."""
    return (channel + 5) // 6


def make_pc_link_inventory_command(payload: str) -> str:
    """Construct a PC-Link inventory command."""
    crc1_result = calc_crc1(payload)
    intermediate_string = f"$14{payload}{crc1_result:04X}"
    crc2_result = calc_crc2(intermediate_string)
    return f"$14{payload}{crc1_result:04X}{crc2_result:02X}"


def _reverse_bits(value: int, width: int) -> int:
    """Reverse the lowest `width` bits of a number."""
    reversed_value = 0
    for _ in range(width):
        reversed_value = (reversed_value << 1) | (value & 1)
        value >>= 1
    return reversed_value


def reverse_24bit_to_hex(n: int) -> str:
    """Convert a decimal number to a 24-bit binary string, reverse it, and return as 6-digit hex."""
    bin_24 = f"{n:024b}"
    reversed_bin = bin_24[::-1]
    reversed_int = int(reversed_bin, 2)
    return format(reversed_int, "06X")


def nikobus_to_button_address(hex_address: str, button: str = "1A") -> str:
    """Convert a 24-bit Nikobus module hex_address into the '#Nxxxxxx' form for the given button."""
    button_map = {
        "1A": 0b101,
        "1B": 0b111,
        "1C": 0b001,
        "1D": 0b011,
        "2A": 0b100,
        "2B": 0b110,
        "2C": 0b000,
        "2D": 0b010,
    }
    if button not in button_map:
        raise ValueError(
            f"Unknown button '{button}'. Must be one of {list(button_map.keys())}."
        )

    original_24 = int(hex_address, 16) & 0xFFFFFF
    shifted_22 = original_24 >> 2
    btn_3bits = button_map[button]
    combined_24 = (btn_3bits << 21) | (shifted_22 & 0x1FFFFF)
    reversed_24 = _reverse_bits(combined_24, 24)
    return "#N" + f"{reversed_24:06X}"


def nikobus_button_to_module(button_hex: str) -> tuple[str, str]:
    """Reverse-engineer a '#Nxxxxxx' button address to the original module address and button label."""
    if not button_hex.startswith("#N") or len(button_hex) != 8:
        raise ValueError(f"'{button_hex}' is not a valid '#Nxxxxxx' format.")

    reversed_hex = button_hex[2:]
    reversed_24 = int(reversed_hex, 16)
    combined_24 = _reverse_bits(reversed_24, 24)
    button_code = (combined_24 >> 21) & 0b111
    shifted_22 = combined_24 & 0x1FFFFF
    original_24 = (shifted_22 << 2) & 0xFFFFFF

    inverse_button_map = {
        0b101: "1A",
        0b111: "1B",
        0b001: "1C",
        0b011: "1D",
        0b100: "2A",
        0b110: "2B",
        0b000: "2C",
        0b010: "2D",
    }
    button_label = inverse_button_map.get(button_code, "UNKNOWN")
    module_hex = f"{original_24:06X}"
    return module_hex, button_label


# ---------------------------------------------------------------------------
# PC-Link maintenance commands: module status, EEPROM CRC, controller clock,
# raw memory blocks. Frame/reply layouts are documented in PROTOCOL.md §3.
# ---------------------------------------------------------------------------

FUNC_READ_BLOCK16: Final[int] = 0x10
"""Read one 16-byte memory block: args = block index (little-endian)."""
FUNC_MODULE_STATUS: Final[int] = 0x11
"""Module status: EEPROM-error flag, type signature and record counts."""
FUNC_MODULE_CRC: Final[int] = 0x13
"""Fetch the CRC16 a module computes over its whole memory image."""
FUNC_GET_TIME: Final[int] = 0x1D
"""Read the PC-Link's date/time."""
FUNC_SET_TIME: Final[int] = 0x1E
"""Write the PC-Link's date/time."""
FUNC_READ_BLOCK8: Final[int] = 0x22
"""Read one 8-byte memory block (dimmer-class modules)."""


@dataclass(frozen=True)
class ModuleStatus:
    """Decoded reply to :data:`FUNC_MODULE_STATUS`.

    ``record_count_a`` bounds the primary link table (16-byte blocks
    from the module's first table offset); ``record_count_b`` the
    secondary table on modules that have one (dimmer second bank).
    """

    address: str
    eeprom_error: bool
    type_code: int
    record_count_a: int
    record_count_b: int
    raw: bytes

    @property
    def status_byte(self) -> int:
        return self.raw[2] if len(self.raw) > 2 else 0


def reply_payload(message: str) -> bytes:
    """Return the data bytes of a ``$xx…`` reply frame (CRCs stripped).

    The two hex digits after ``$`` carry ``10 + <hex length of the
    data>``; the data spans ``message[3:3 + n]`` with ``n = length_byte
    - 10`` and is followed by the CRC16 (4 hex) and CRC8 (2 hex).
    """
    try:
        n = int(message[1:3], 16) - 10
        if n <= 0:
            return b""
        return bytes.fromhex(message[3 : 3 + n])
    except (ValueError, IndexError):
        return b""


def wire_address(address: str) -> str:
    """Module address as it appears on the wire (little-endian hex)."""
    return int(address, 16).to_bytes(2, "little").hex().upper()


def parse_module_status(payload: bytes, address: str) -> ModuleStatus:
    """Decode the 7-byte :data:`FUNC_MODULE_STATUS` reply.

    Layout: ``addr_lo addr_hi status type ? count_a count_b`` — the same
    shape as the ``$18`` answer to the ``#A`` broadcast (where byte 3 is
    0x50 for a PC-Link and 0x40 for a PC-Logic).
    """
    if len(payload) < 7:
        raise ValueError(f"module status reply too short: {payload.hex()}")
    return ModuleStatus(
        address=address.upper(),
        eeprom_error=bool(payload[2] & 0x01),
        type_code=payload[3],
        record_count_a=payload[5],
        record_count_b=payload[6],
        raw=bytes(payload),
    )


def parse_module_crc(payload: bytes) -> int:
    """Decode the :data:`FUNC_MODULE_CRC` reply: ``FF lo hi ? ? crc_lo crc_hi``."""
    if len(payload) < 7:
        raise ValueError(f"module CRC reply too short: {payload.hex()}")
    return payload[5] | (payload[6] << 8)


def parse_pc_link_time(payload: bytes) -> datetime:
    """Decode the :data:`FUNC_GET_TIME` reply ``FF lo hi YY MM DD hh mm ss``.

    ``YY`` is the year minus 2000. Raises ``ValueError`` on an invalid
    date (a PC-Link whose clock was never set returns out-of-range
    fields).
    """
    if len(payload) < 9:
        raise ValueError(f"PC-Link time reply too short: {payload.hex()}")
    yy, mo, dd, hh, mi, ss = payload[3:9]
    return datetime(2000 + yy, mo, dd, hh, mi, ss)  # noqa: DTZ001 - controller keeps naive local time


def make_set_time_args(moment: datetime) -> bytes:
    """Argument bytes for :data:`FUNC_SET_TIME`: ``YY MM DD hh mm ss FF``."""
    if not 2000 <= moment.year <= 2255:
        raise ValueError("PC-Link clock supports years 2000..2255")
    return bytes(
        [
            moment.year - 2000,
            moment.month,
            moment.day,
            moment.hour,
            moment.minute,
            moment.second,
            0xFF,
        ]
    )


def make_block_index_args(block: int) -> bytes:
    """Argument bytes for a block read: little-endian 16-bit block index."""
    if not 0 <= block <= 0xFFFF:
        raise ValueError(f"block index out of range: {block}")
    return bytes([block & 0xFF, block >> 8])
