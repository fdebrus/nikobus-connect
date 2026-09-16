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

# Presence probe run after the handshake: a status query to the null
# address, which the PC-Link (or a feedback module used as gateway)
# acknowledges with ``$0511`` and nothing else. The interface holds that
# acknowledgement until the next ``$`` frame reaches it, so the probe is
# sent twice per attempt and the first reply is read after the second.
# The ``#A`` identity broadcast: the gateway answers it with its own
# ``$18`` status frame within ~30 ms (PC-Link ``$18 F586 00 50 …``, a
# PC-Logic likewise). A status query to the null address, the previous
# probe, only earns an acknowledgement — and the PC-Link holds acks back
# until it has a reply or a relayed bus frame to send, so on a quiet bus
# that probe never got an answer.
PRESENCE_PROBE_COMMAND: Final[str] = "#A"
PRESENCE_PROBE_TIMEOUT: Final[float] = 3.0
PRESENCE_PROBE_ATTEMPTS: Final[int] = 3
# Pause between the end of the handshake and the first probe. A PC-Link
# just reset by ``ATZ`` on a cold start (host reboot, USB re-enumeration)
# swallows frames sent straight away; the vendor software waits about a
# second there too.
PRESENCE_PROBE_SETTLE: Final[float] = 1.0
# After the probe is acknowledged, how long to keep reading for the
# gateway's own ``$18`` status frame (its address and family).
PRESENCE_PROBE_IDENTITY_WAIT: Final[float] = 0.5

# Family signature (byte 3 of a ``$18`` status reply) per device class.
# Output modules: switch 0x10 (compact switch unit 0x90), roller 0x20,
# dimmer 0x30. Gateways / system modules: PC-Link 0x50, PC-Logic 0x40,
# Feedback Module 0xA0.
FAMILY_SIGNATURES: Final[dict[str, tuple[int, ...]]] = {
    "switch_module": (0x10, 0x90),
    "roller_module": (0x20,),
    "dimmer_module": (0x30,),
    "pc_link": (0x50,),
    "pc_logic": (0x40,),
    "feedback_module": (0xA0,),
}

# Command execution timing
COMMAND_EXECUTION_DELAY: Final[float] = 0.15
# A set-output request waits this long at the head of the queue before
# its frame is built from the state buffer, so requests for the same
# module group that arrive together (six lights of one module switched
# by one service call, or by consecutive script actions) become one
# frame instead of one each.
SET_COALESCE_WINDOW: Final[float] = 0.05
# How many times a host-injected key press (``#N``) is repeated. A real
# key repeats its telegram while held and modules act on a telegram seen
# at least twice: "2 to register, 3 to be sure". The repeats go out in
# one write, back to back — spaced repeats can be counted as separate
# presses by an impulse/toggle link.
DEFAULT_PRESS_REPEAT: Final[int] = 3
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

# Module-status query (function 0x11) issued before a register scan to
# size the read exactly. The query runs through the command queue with
# its own ack/answer retries; this is the outer budget before discovery
# gives up on it and falls back to the fixed scan band.
MODULE_STATUS_TIMEOUT: Final[float] = 20.0

# Multi-pass scan: if this many registers in a row fail to get any
# ACK, assume the module doesn't accept this function+sub combination
# and abort the pass early. Without this, a non-responding module
# wastes ~256 * (ACK timeout * retries) ≈ 13 minutes per pass.
#
# Raised from 5 → 16 in 0.5.4 after a real-hardware report where 4
# switch modules + 1 dimmer aborted at register 0x04..0x05 every time.
# Those firmwares silently ignore function-10 / function-22 reads in
# the 0x00..0x04 dead zone but respond fine from 0x05+. 16 buys enough
# headroom to power past that leading dead zone.
#
# Lowered from 16 → 8 in 0.17.1 because the 0.17.0 per-product profiles
# have variable-length sections (~200 reads each) where firmwares that
# silently drop reads anywhere in the section waste minutes per module.
# 8 still covers the leading dead zone but aborts unresponsive passes
# within ~24 s instead of ~48 s.
MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT: Final[int] = 8

# Per-module register scan: number of CONSECUTIVE all-FF ("empty") data
# registers that must be seen before the scan concludes end-of-table and
# short-circuits the current pass. Set to 1 historically (stop on the
# first empty register), which dropped every link record sitting after a
# mid-table FF gap — e.g. a deleted-slot gap, or a central function whose
# records are written past such a gap. Real installs do have these gaps
# (the PC-Link inventory scan already tolerates them), so a single empty
# register is no longer treated as the end; only a run of consecutive
# empties is. Bounded above by the per-module scan band, so a clean
# end-of-table still stops within a few extra reads.
MODULE_SCAN_FF_TERMINATOR_STREAK_LIMIT: Final[int] = 3

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

# Signature byte that distinguishes PC-Link from PC-Logic in the
# response to the broadcast ``#A`` ("address inquiry") command.
#
# Both controllers listen to ``#A`` and reply with a ``$18 <addr>
# 00 <sig> 0F 3F FF <crc>`` frame. Byte 4 of the payload (``<sig>``)
# is ``0x50`` on PC-Link (model 0A) and ``0x40`` on PC-Logic
# (model 08), confirmed across three real installs:
#
#   - fdebrus PC-Link 86F5: ``$18F586 00 50 0F3FFF AC61FE``
#   - issue-307 PC-Link 846F: ``$186F84 00 50 0F3FFF 48EDCE``
#   - new-user PC-Logic 8835: ``$183588 00 40 0F3FFF 4170C4``
#
# Without this filter our discovery accepts whichever controller
# answered first as "the PC-Link" — when both controllers exist on
# the bus and the PC-Logic wins the response race, all subsequent
# inventory reads go to the wrong device and come back empty.
PC_LINK_INVENTORY_SIGNATURE_BYTE: Final[int] = 0x50
