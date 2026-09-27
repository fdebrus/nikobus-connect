"""A module on the simulated bus: memory, outputs, and what a press does."""

from __future__ import annotations

import logging

from .encoding import encode_module, module_status_payload, register_width
from .topology import ModuleSpec

_LOGGER = logging.getLogger(__name__)

#: Family signature a module reports in its status reply.
FAMILY_BYTES = {
    "switch_module": 0x10,
    "roller_module": 0x20,
    "dimmer_module": 0x30,
    "audio_module": 0x2B,
    "pc_link": 0x50,
    "pc_logic": 0x40,
    "feedback_module": 0xA0,
}

STATE_ON = 0xFF
STATE_OFF = 0x00
#: A roller channel holds a direction, not a level.
STATE_OPEN = 0x01
STATE_CLOSE = 0x02
STATE_STOP = 0x00

#: Raw link modes whose press flips the output rather than driving it to
#: a fixed state: M01 "on / off", M05 "impulse", M15 "light scene on /
#: off". Every other switch mode ends with the output on, except M03,
#: which turns it off. The raw value is one less than the vendor's mode
#: number (M01 is stored as 0).
SWITCH_TOGGLE_MODES = frozenset({0, 4, 11})
SWITCH_OFF_MODES = frozenset({2})

#: What a roller link's mode does to the channel. M01 cycles
#: open - stop - close; the others drive one direction, or stop.
ROLLER_OPEN_MODES = frozenset({1, 5})
ROLLER_CLOSE_MODES = frozenset({2, 6})
ROLLER_STOP_MODES = frozenset({3})
ROLLER_CYCLE_MODE = 0
#: The cycle M01 walks, in order.
ROLLER_CYCLE = (STATE_OPEN, STATE_STOP, STATE_CLOSE)


class SimulatedModule:
    """One module: what it stores, what it drives, how it reacts."""

    def __init__(self, spec: ModuleSpec) -> None:
        self.spec = spec
        self.address = spec.address
        self.memory = encode_module(spec)
        #: Hex digits one register read returns — eight bytes on a dimmer.
        self.register_hex = register_width(spec.type)
        self.outputs = bytearray(12)
        #: Where each roller channel stands in the M01 cycle. The output
        #: byte cannot say: "stopped" and "never moved" are both 0x00.
        self._roller_step: dict[int, int] = {}

    # --- what a host can read ------------------------------------------

    @property
    def family_byte(self) -> int:
        return FAMILY_BYTES.get(self.spec.type, 0x00)

    def status_payload(self) -> str:
        return module_status_payload(self.spec, self.family_byte)

    def read_register(self, sub: str, register: int) -> str:
        """One register's worth of bytes; filler where it holds nothing."""
        stored = self.memory.get(sub.upper(), {}).get(register)
        if stored is None:
            return "F" * self.register_hex
        return stored

    def group_state(self, group: int) -> str:
        start = 0 if group == 1 else 6
        return self.outputs[start : start + 6].hex().upper()

    # --- what a host can change -----------------------------------------

    def set_group(self, group: int, values: bytes) -> None:
        start = 0 if group == 1 else 6
        self.outputs[start : start + 6] = values[:6]

    def apply_press(self, bus_address: str) -> bool:
        """Drive the outputs a key press would drive.

        Returns whether anything moved, so the caller can tell a press
        this module cares about from one it does not.
        """
        moved = False
        for link in self.spec.links:
            if link.button.upper() != bus_address.upper():
                continue
            index = link.channel - 1
            if not 0 <= index < len(self.outputs):
                continue
            self.outputs[index] = self._next_output(link.mode, index)
            moved = True
            _LOGGER.debug(
                "module %s channel %d -> 0x%02X (press %s, mode M%02d)",
                self.address,
                link.channel,
                self.outputs[index],
                bus_address,
                link.mode + 1,
            )
        return moved

    def _next_output(self, mode: int, index: int) -> int:
        """What one press in this mode leaves the channel at.

        Modelled at the level a host can observe: a mode that ends with
        the output on ends on, one that toggles toggles. The timing of a
        delayed or timed mode is not simulated — its channel simply
        lands where the mode leaves it.
        """
        current = self.outputs[index]
        if self.spec.type == "roller_module":
            if mode in ROLLER_OPEN_MODES:
                return STATE_OPEN
            if mode in ROLLER_CLOSE_MODES:
                return STATE_CLOSE
            if mode in ROLLER_STOP_MODES:
                return STATE_STOP
            if mode == ROLLER_CYCLE_MODE:
                step = (self._roller_step.get(index, -1) + 1) % len(ROLLER_CYCLE)
                self._roller_step[index] = step
                return ROLLER_CYCLE[step]
            return STATE_OPEN
        if mode in SWITCH_TOGGLE_MODES:
            return STATE_OFF if current else STATE_ON
        if mode in SWITCH_OFF_MODES:
            return STATE_OFF
        return STATE_ON
