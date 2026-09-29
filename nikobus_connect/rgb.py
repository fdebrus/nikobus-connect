"""The 340-00112 RGB controller: what its state image says, and what
its keys do.

The controller (device type ``0x46``) answers the ordinary output-state
query and nothing else: no register read, no checksum query, and a
set-output write gets no acknowledgement. It is driven only by the keys
linked to it, and every one of its link modes is a key behaviour (see
``RGB_MODE_NAMES`` in :mod:`nikobus_connect.discovery.mapping`). So a
host reads the state here and, to change it, presses a key — which is
what this module names.

Calibrated on a real controller (Nikobus-HA #519, 2026-09-29): the
colour loop was frozen with the wall key and the state read after each
colour::

    blue         FF 00 FF FF 00 00
    yellow-green FF FF FF 00 00 00
    violet       FF FF FF FF 00 00
    red-pink     FF FF 00 FF 00 00
    off          00 00 00 00 00 00

Byte 1 is on/off, bytes 2–4 are the R, G and B outputs as lit-or-not
flags — only ``00`` and ``FF`` ever appear, so there are no levels —
and bytes 5–6 are always zero. Both output groups return the same
image. Blue at 7 % green reads ``FF`` for green: any level lights the
flag.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .discovery.mapping import RGB_MODE_KEYS, RGB_MODE_NAMES

#: A plate key face: ``A``–``D``, or ``1A``–``2D`` on an eight-key plate.
_KEY_LABEL_RE = re.compile(r"^[12]?([A-D])$")

#: Hex digits in the six-byte state image a state query answers with.
RGB_STATE_HEX = 12


class RgbState(NamedTuple):
    """The controller's state image, decoded."""

    on: bool
    red: bool
    green: bool
    blue: bool

    @property
    def colour_name(self) -> str:
        """``off``, or the colour the lit outputs mix to."""
        if not self.on:
            return "off"
        return _COLOUR_NAMES.get((self.red, self.green, self.blue), "on")

    @property
    def rgb(self) -> tuple[int, int, int]:
        """The flags as an 8-bit RGB triple: ``(255, 0, 255)`` for
        red and blue lit. All zero when off."""
        if not self.on:
            return (0, 0, 0)
        return (255 * self.red, 255 * self.green, 255 * self.blue)


_COLOUR_NAMES: dict[tuple[bool, bool, bool], str] = {
    (True, False, False): "red",
    (False, True, False): "green",
    (False, False, True): "blue",
    (True, True, False): "yellow",
    (True, False, True): "magenta",
    (False, True, True): "cyan",
    (True, True, True): "white",
}

RGB_OFF_IMAGE = "00" * 6


def decode_rgb_state(image: str | bytes | bytearray) -> RgbState:
    """Decode a state image — the twelve hex digits ``get_output_state``
    returns, or the six bytes — into flags.

    A byte is "lit" when it is anything but zero; the controller only
    ever sends ``00`` or ``FF``, and a value in between would still be
    an output that is on. A short or empty image is off.
    """
    if isinstance(image, str):
        text = image.strip()
        if len(text) < RGB_STATE_HEX:
            return RgbState(False, False, False, False)
        try:
            raw = bytes.fromhex(text[:RGB_STATE_HEX])
        except ValueError:
            return RgbState(False, False, False, False)
    else:
        raw = bytes(image[:6])
        if len(raw) < 4:
            return RgbState(False, False, False, False)
    return RgbState(
        on=raw[0] != 0,
        red=raw[1] != 0,
        green=raw[2] != 0,
        blue=raw[3] != 0,
    )


# --- what a key does, per mode --------------------------------------------
#
# The leaflet (PM340-00112) describes every mode by its keys: a 1-key
# mode has one behaviour, a 2-key mode an upper and a lower key, a
# 4-key mode four corners. A host holds a key's label (``1C``) and the
# link's mode number, and needs to know which key switches the light
# on and which off. These are the roles of a short press; a long press
# often does something else (dim, scroll the colour path), which a
# host's tap does not reach.

ROLE_ON = "on"
ROLE_OFF = "off"
ROLE_TOGGLE = "toggle"
ROLE_START_STOP = "start_stop"
ROLE_START = "start"
ROLE_SCENE = "scene"
ROLE_PRESET = "preset"
ROLE_FLASH = "flash"
ROLE_DELAYED_OFF = "delayed_off"
ROLE_DELAYED_RETURN = "delayed_return"
ROLE_COLOUR_NEXT = "colour_next"
ROLE_COLOUR_PREV = "colour_prev"
ROLE_SPEED_UP = "speed_up"
ROLE_SPEED_DOWN = "speed_down"

POSITION_SINGLE = "single"
POSITION_UPPER = "upper"
POSITION_LOWER = "lower"
POSITION_UPPER_LEFT = "upper_left"
POSITION_LOWER_LEFT = "lower_left"
POSITION_UPPER_RIGHT = "upper_right"
POSITION_LOWER_RIGHT = "lower_right"

RGB_MODE_KEY_ROLES: dict[int, dict[str, str]] = {
    1: {POSITION_UPPER: ROLE_ON, POSITION_LOWER: ROLE_OFF},
    4: {POSITION_SINGLE: ROLE_SCENE},
    5: {POSITION_SINGLE: ROLE_ON},
    6: {POSITION_SINGLE: ROLE_OFF},
    7: {POSITION_SINGLE: ROLE_DELAYED_OFF},
    8: {POSITION_SINGLE: ROLE_FLASH},
    12: {POSITION_SINGLE: ROLE_PRESET},
    13: {POSITION_SINGLE: ROLE_TOGGLE},
    15: {POSITION_SINGLE: ROLE_DELAYED_RETURN},
    16: {
        POSITION_UPPER_LEFT: ROLE_COLOUR_NEXT,
        POSITION_LOWER_LEFT: ROLE_COLOUR_PREV,
        POSITION_UPPER_RIGHT: ROLE_ON,
        POSITION_LOWER_RIGHT: ROLE_OFF,
    },
    17: {POSITION_UPPER: ROLE_ON, POSITION_LOWER: ROLE_OFF},
    18: {
        POSITION_UPPER_LEFT: ROLE_START_STOP,
        POSITION_LOWER_LEFT: ROLE_OFF,
        POSITION_UPPER_RIGHT: ROLE_SPEED_UP,
        POSITION_LOWER_RIGHT: ROLE_SPEED_DOWN,
    },
    19: {POSITION_UPPER: ROLE_START_STOP, POSITION_LOWER: ROLE_OFF},
    20: {POSITION_SINGLE: ROLE_START},
    21: {POSITION_SINGLE: ROLE_TOGGLE},
}

#: Roles a host may press to switch the light on, best first. A toggle
#: only when the light is off; a scene or preset recalls a stored
#: colour; a delayed-off key lights it for its programmed time.
RGB_ON_ROLES: tuple[str, ...] = (
    ROLE_ON,
    ROLE_TOGGLE,
    ROLE_START_STOP,
    ROLE_START,
    ROLE_PRESET,
    ROLE_SCENE,
    ROLE_DELAYED_OFF,
)

#: Roles a host may press to switch the light off, best first. A toggle
#: only when the light is on.
RGB_OFF_ROLES: tuple[str, ...] = (ROLE_OFF, ROLE_TOGGLE)


def rgb_key_position(mode_number: int, key_label: str) -> str | None:
    """Which of a mode's keys a plate key is, from its label.

    Labels are the plate's key faces, ``A``–``D`` or ``1A``–``2D``: A
    and C are the upper keys of their column, B and D the lower; on a
    four-key mode A is upper-left, B lower-left, C upper-right, D
    lower-right. A one-key mode has one position whatever the key.
    ``None`` for a mode off the table or a label that is not a key.
    """
    keys = RGB_MODE_KEYS.get(mode_number)
    if keys is None:
        return None
    match = _KEY_LABEL_RE.match((key_label or "").strip().upper())
    if match is None:
        return None
    letter = match.group(1)
    if keys == 1:
        return POSITION_SINGLE
    if keys == 2:
        return POSITION_UPPER if letter in "AC" else POSITION_LOWER
    return {
        "A": POSITION_UPPER_LEFT,
        "B": POSITION_LOWER_LEFT,
        "C": POSITION_UPPER_RIGHT,
        "D": POSITION_LOWER_RIGHT,
    }[letter]


def rgb_key_role(mode_number: int, key_label: str) -> str | None:
    """What a short press of plate key ``key_label`` does in a link of
    mode ``mode_number``: ``"on"``, ``"off"``, ``"start_stop"``, …"""
    position = rgb_key_position(mode_number, key_label)
    if position is None:
        return None
    return RGB_MODE_KEY_ROLES.get(mode_number, {}).get(position)


def rgb_mode_label(mode_number: int) -> str:
    """``"M19 (Start/stop scenario)"`` — re-exported for hosts."""
    name = RGB_MODE_NAMES.get(mode_number)
    return f"M{mode_number:02d} ({name})" if name else f"M{mode_number:02d}"


__all__ = [
    "RGB_MODE_KEY_ROLES",
    "RGB_OFF_IMAGE",
    "RGB_OFF_ROLES",
    "RGB_ON_ROLES",
    "RGB_STATE_HEX",
    "RgbState",
    "decode_rgb_state",
    "rgb_key_position",
    "rgb_key_role",
    "rgb_mode_label",
]
