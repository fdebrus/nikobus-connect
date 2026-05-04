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

# Module register scan (sequential send-and-wait). Each register read is
# sent one at a time; the scan loop waits for the ACK, then up to
# DATA_TIMEOUT for the matching data frame. An empty register legitimately
# produces no data frame — DATA_TIMEOUT expiring there is not an error.
# A "$18FFFF…" trailer frame short-circuits the remaining reads.
#
# Timeouts are generous: real-hardware ACKs land 300–700 ms after the send,
# with the first register hitting the top of that range because the module
# wakes up on the initial command. Erring on the slow side trades a few
# hundred ms per empty register for alignment correctness.
MODULE_SCAN_ACK_TIMEOUT: Final[float] = 1.5
MODULE_SCAN_DATA_TIMEOUT: Final[float] = 0.5
MODULE_SCAN_RETRY_LIMIT: Final[int] = 1
MODULE_SCAN_TRAILER_PREFIX: Final[str] = "$18"

# Multi-pass scan: if this many registers in a row fail to get any
# ACK, assume the module doesn't accept this function+sub combination
# and abort the pass early. Without this, a non-responding module
# wastes ~256 * (ACK timeout * retries) ≈ 13 minutes per pass.
#
# Raised from 5 → 16 in 0.5.4 after a real-hardware report on
# fdebrus/nikobus-connect#? where 4 switch modules + 1 dimmer aborted
# at register 0x04..0x05 every time. Those firmwares silently ignore
# function-10 / function-22 reads in the 0x00..0x04 dead zone but
# respond fine from 0x05+. The PC-software trace also starts at 0x05
# (sweeps 0x05..0x3E). 16 buys enough headroom to power past that
# leading dead zone while still aborting unproductive passes within
# ~30 s instead of the full ~3 min.
MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT: Final[int] = 16

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
