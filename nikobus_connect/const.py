"""Nikobus protocol constants."""

from typing import Final

# Handshake sequence to initialize the PC-Link interface
COMMANDS_HANDSHAKE: Final[list[str]] = [
    "++++",
    "ATH0",
    "ATZ",
    "$10110000B8CF9D",
    "#L0",
    "#E0",
    "#L0",
    "#E1",
]
EXPECTED_HANDSHAKE_RESPONSE: Final[str] = "$0511"
HANDSHAKE_TIMEOUT: Final[int] = 60

# Command execution timing
COMMAND_EXECUTION_DELAY: Final[float] = 0.15
COMMAND_ACK_WAIT_TIMEOUT: Final[int] = 15
COMMAND_ANSWER_WAIT_TIMEOUT: Final[int] = 5
COMMAND_POST_ACK_ANSWER_TIMEOUT: Final[float] = 1.5
MAX_ATTEMPTS: Final[int] = 3

# Message prefixes and markers
BUTTON_COMMAND_PREFIX: Final[str] = "#N"
COMMAND_PROCESSED: Final[tuple[str, str]] = ("$0515", "$0516")
FEEDBACK_REFRESH_COMMAND: Final[tuple[str, str]] = ("$1012", "$1017")
FEEDBACK_MODULE_ANSWER: Final[str] = "$1C"
MANUAL_REFRESH_COMMAND: Final[tuple[str, str]] = ("$0512", "$0517")
CONTROLLER_ADDRESS: Final[str] = "$18"

# Discovery constants
DEVICE_ADDRESS_INVENTORY: Final[str] = "$18"
DEVICE_INVENTORY_ANSWER: Final[tuple[str, str]] = ("$2E", "$1E")
