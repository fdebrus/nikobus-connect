"""Nikobus Protocol Utilities."""


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


def make_pc_link_command(func: int, addr: str, args: bytes | None = None) -> str:
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
