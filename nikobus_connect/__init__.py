"""NikobusConnect: asynchronous library for Nikobus."""

from .api import NikobusAPI
from .command import NikobusCommandHandler
from .connection import NikobusConnect
from .exceptions import (
    NikobusConnectionError,
    NikobusDataError,
    NikobusError,
    NikobusReadError,
    NikobusSendError,
    NikobusTimeoutError,
)
from .listener import NikobusEventListener
from .protocol import (
    append_crc1,
    append_crc2,
    calc_crc1,
    calc_crc2,
    calculate_group_number,
    int_to_hex,
    make_pc_link_command,
    make_pc_link_inventory_command,
    nikobus_button_to_module,
    nikobus_to_button_address,
    reverse_24bit_to_hex,
)

__all__ = [
    "NikobusAPI",
    "NikobusCommandHandler",
    "NikobusConnect",
    "NikobusConnectionError",
    "NikobusDataError",
    "NikobusError",
    "NikobusEventListener",
    "NikobusReadError",
    "NikobusSendError",
    "NikobusTimeoutError",
    "append_crc1",
    "append_crc2",
    "calc_crc1",
    "calc_crc2",
    "calculate_group_number",
    "int_to_hex",
    "make_pc_link_command",
    "make_pc_link_inventory_command",
    "nikobus_button_to_module",
    "nikobus_to_button_address",
    "reverse_24bit_to_hex",
]

__version__ = "2.0.0"
