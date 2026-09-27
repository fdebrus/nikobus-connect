"""The gateway: what comes back for every frame a host sends.

The quirks here are the reason the simulator exists. They are the ones
that unit tests cannot see and that cost real releases:

* an acknowledgement is **withheld** until the gateway has something
  else to send, so a command that produces no reply is only
  acknowledged in front of the next one;
* the gateway **never relays the host's own press**, so a host gets no
  bus event for a frame it sent itself;
* a key held down puts its address on the bus again and again, so a
  module counts a burst of one address as **one** press — which is what
  lets a host send a press several times over for reliability without an
  impulse link reading it as "on, then off again";
* a state answer carries no group, only the six bytes of the group that
  was asked for;
* a register read answers in the width the module's family uses — the
  dimmer's own read function (``0x22``) returns eight bytes where every
  other family returns sixteen.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from nikobus_connect.protocol import calc_crc1, calc_crc2

from .module import SimulatedModule
from .topology import Installation

_LOGGER = logging.getLogger(__name__)

#: Interface lines a host sends that produce nothing.
_INTERFACE_PREFIXES = ("++++", "ATH0", "ATZ", "#L", "#E")

#: Module status (0x11): family, state and the record counts.
FUNC_STATUS = "11"
#: Reading an output group (0x12 / 0x17) — the answer names no group.
_GET_OUTPUT_GROUPS = {"12": 1, "17": 2}
#: Writing an output group (0x15 / 0x16).
_SET_OUTPUT_GROUPS = {"15": 1, "16": 2}
#: Reading a register: 0x10 returns sixteen bytes, 0x22 the dimmer's eight.
_READ_FUNCTIONS = ("10", "22")
#: Every function the gateway answers on a module's behalf.
_MODULE_FUNCTIONS = (
    FUNC_STATUS,
    *_GET_OUTPUT_GROUPS,
    *_SET_OUTPUT_GROUPS,
    *_READ_FUNCTIONS,
)

#: How long one key's address keeps counting as the same press. A held
#: key repeats on the bus at roughly this rate, so a module that treated
#: every frame as a press would toggle an impulse link continuously.
PRESS_DEBOUNCE = 0.3


def frame(payload_hex: str) -> str:
    """Wrap a payload as a bus frame: length, payload, both checksums.

    The length byte is the payload's hex length plus ten, which is what
    the four digits of the module's CRC-16 and the two of the PC-Link's
    CRC-8 add to it.
    """
    body = f"${len(payload_hex) + 10:02X}{payload_hex}{calc_crc1(payload_hex):04X}"
    return f"{body}{calc_crc2(body):02X}"


def short_frame(payload_hex: str) -> str:
    """A frame the module stamps no CRC-16 on: only the PC-Link's CRC-8.

    The answer to an output write is the one of these the host waits
    for, and its length byte counts the two digits of the CRC-8 alone
    (``FF`` + address + ``00`` -> ``$0E``).
    """
    body = f"${len(payload_hex) + 6:02X}{payload_hex}"
    return f"{body}{calc_crc2(body):02X}"


def wire_address(address: str) -> str:
    """A module address as it travels: low byte first."""
    return f"{address[2:]}{address[:2]}".upper()


class Gateway:
    """Answers frames on behalf of an installation."""

    def __init__(
        self,
        installation: Installation,
        *,
        debounce: float = PRESS_DEBOUNCE,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.installation = installation
        self.modules = {
            spec.address: SimulatedModule(spec) for spec in installation.modules
        }
        #: Acknowledgements waiting for something to travel with.
        self._pending_acks: list[str] = []
        self._debounce = debounce
        self._now = now
        #: When each key's address was last seen, for the debounce.
        self._last_press: dict[str, float] = {}

    # --- helpers --------------------------------------------------------

    def _module(self, wire: str) -> SimulatedModule | None:
        address = f"{wire[2:4]}{wire[0:2]}".upper()
        return self.modules.get(address)

    def _flush(self, *replies: str) -> list[str]:
        """Release the held acknowledgements in front of a reply."""
        out = self._pending_acks + [r for r in replies if r]
        self._pending_acks = []
        return out

    def _answer(self, function: str, module: SimulatedModule, payload: str) -> str:
        """The frame a module sends back for one of the functions it serves."""
        wire = payload[:4]
        if function == FUNC_STATUS:
            return frame(f"{wire}{module.status_payload()}")
        if function in _GET_OUTPUT_GROUPS:
            group = _GET_OUTPUT_GROUPS[function]
            return frame(f"{wire}00{module.group_state(group)}")
        if function in _SET_OUTPUT_GROUPS:
            module.set_group(_SET_OUTPUT_GROUPS[function], bytes.fromhex(payload[4:16]))
            return short_frame(f"FF{wire}00")
        register = int(payload[4:6], 16)
        return frame(f"{wire}{module.read_register(payload[6:8], register)}")

    def _is_repeat(self, address: str) -> bool:
        """Whether this frame is the key from a moment ago, still down."""
        moment = self._now()
        last = self._last_press.get(address)
        self._last_press[address] = moment
        return last is not None and moment - last < self._debounce

    def _identity(self) -> str:
        address = self.installation.gateway_address
        family = {"pc_link": 0x50, "pc_logic": 0x40}.get(
            self.installation.gateway_family, 0x50
        )
        return frame(f"{wire_address(address)}00{family:02X}0F3FFF")

    # --- the bus --------------------------------------------------------

    def handle(self, line: str) -> list[str]:
        """Everything the gateway puts on the wire for one host line."""
        line = line.strip()
        if not line:
            return []

        if line.startswith(_INTERFACE_PREFIXES):
            return []

        if line.startswith("#A"):
            # The identity broadcast is answered by the gateway itself.
            return self._flush(self._identity())

        if line.startswith("#N"):
            # A press from the host. The gateway does not relay it back;
            # the modules act on it, and the host learns the new state
            # only by asking.
            address = line[2:8].upper()
            if self._is_repeat(address):
                _LOGGER.debug("press %s is the same key still down, ignored", address)
                return []
            for module in self.modules.values():
                module.apply_press(address)
            return []

        if not line.startswith("$") or len(line) < 5:
            return []

        function = line[3:5]
        payload = line[5:-6]
        wire = payload[:4]
        ack = f"$05{function}"

        if function in _MODULE_FUNCTIONS:
            target = self._module(wire)
            if target is None:
                # Nothing answers for an address that is not on the bus,
                # and the acknowledgement waits with the rest.
                self._pending_acks.append(ack)
                return []
            return self._flush(ack, self._answer(function, target, payload))

        # Anything else is acknowledged, and the acknowledgement waits.
        self._pending_acks.append(ack)
        _LOGGER.debug("unhandled function %s in %s", function, line)
        return []
