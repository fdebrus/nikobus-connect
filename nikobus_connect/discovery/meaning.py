"""What a link does, in words: its mode, and what T1 and T2 are in it.

A link record carries a mode and up to two parameters the vendor calls
T1 and T2, after the two setting wheels of a module. What a wheel sets
depends on the module and on the mode: a delay, a run time, how long
the key has to be held, a preset level, a dimming time. The decoders
label the values (``"2m"``, ``"4,0 V"``); this says what each one is,
so that a host, a person or a language model reading a link does not
need the tables.

Every sentence and every parameter comes from the vendor's manuals:
PMNikobus_EN R07244, chapter 4, "The different modes or functions" of
the switch module, the shutter module and the dimcontroller, and for
the compact dim controller's M13 / M14 its leaflet PM005-00802. Where
they say nothing about a wheel in a mode, the value is left out rather
than given a meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..nkb.parser import mode_code
from .mapping import DIMMER_T1_1

#: How long the key has to be held before the link acts; the manuals'
#: "operation time", meant for master functions.
HOLD_TIME = "hold_time"
OFF_AFTER = "off_after"
ON_AFTER = "on_after"
RUN_TIME = "run_time"
LEVEL = "level"
#: Dimming on and off both take this long.
DIM_TIME = "dim_time"
DIM_ON_TIME = "dim_on_time"
DIM_OFF_TIME = "dim_off_time"

_RUN_AND_HOLD = "run_and_hold"
_DIM_CURVE = "dim_curve"


@dataclass(frozen=True)
class LinkMeaning:
    """One link, explained."""

    #: The vendor's mode code, ``"M06"``.
    mode: str
    #: How many keys of the plate the mode takes: 1, 2 (upper and
    #: lower) or 4. The record does not say which key is which.
    keys: int
    #: One or two sentences on what a press does to the output, with
    #: the link's own values in them.
    summary: str
    #: The link's values under what they are (``{"off_after": "2 min"}``)
    #: instead of under ``t1`` / ``t2``.
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Mode:
    keys: int
    text: str
    t1: str | None = None
    t2: str | None = None


_SWITCH: dict[str, _Mode] = {
    "M01": _Mode(2, "Two keys: the upper one switches the output on, the lower one off."),
    "M02": _Mode(1, "Switches the output on.", HOLD_TIME),
    "M03": _Mode(1, "Switches the output off.", HOLD_TIME),
    "M04": _Mode(1, "The output is on for as long as the key is held, at most 8 s."),
    "M05": _Mode(1, "Each press switches the output over, on or off."),
    "M06": _Mode(1, "Switches the output on at once, and off again after {off_after}.", OFF_AFTER),
    "M07": _Mode(1, "Switches the output on {on_after} after the press.", ON_AFTER),
    "M08": _Mode(1, "Makes the output blink, 1 s on and 1 s off, until a key with mode M03 switches it off."),
    "M11": _Mode(1, "Switches the output on at once, and off again after {off_after}.", OFF_AFTER),
    "M12": _Mode(1, "Switches the output on {on_after} after the press.", ON_AFTER),
    # The manual sets this mode's cycle time with T1 like M06's; the
    # switch decoder labels no T1 for it, so no time is named here.
    "M13": _Mode(2, "Two keys of a sequencer: several outputs are switched one after the other in a time cycle."),
    "M14": _Mode(
        1,
        "Light scene: a short press recalls the state stored for this output, "
        "a press of more than 3 s stores its present state.",
    ),
    "M15": _Mode(
        2,
        "Light scene on two keys: a short press on the upper one recalls the state stored for "
        "this output, a press of more than 3 s stores its present state, the lower one switches it off.",
    ),
}

_DIMMER: dict[str, _Mode] = {
    "M01": _Mode(
        2,
        "Two keys: a short press on the upper one switches the output on at its last level, "
        "on the lower one off; holding either dims up or down.",
        _DIM_CURVE,
        DIM_TIME,
    ),
    "M02": _Mode(
        4,
        "Four keys: a short press on an upper one switches the output on at its last level, "
        "on a lower one off; a long press dims up or down.",
        _DIM_CURVE,
        DIM_TIME,
    ),
    "M03": _Mode(
        4,
        "Light scene on four keys: upper left recalls the level stored for this output, or stores "
        "its present level when held; lower left switches off; the right-hand keys dim the whole "
        "scene up or down.",
        _DIM_CURVE,
        DIM_TIME,
    ),
    "M04": _Mode(
        1,
        "Light scene: a short press recalls the level stored for this output, a long press stores its present level.",
        None,
        DIM_TIME,
    ),
    "M05": _Mode(1, "Switches the output on at its last level.", HOLD_TIME, DIM_TIME),
    "M06": _Mode(1, "Switches the output off.", HOLD_TIME, DIM_TIME),
    "M07": _Mode(
        1,
        "Switches the output on at its last level, and starts dimming it off after {off_after}.",
        OFF_AFTER,
        DIM_TIME,
    ),
    # "The setting wheels T1 and T2 are of no importance here."
    "M08": _Mode(1, "Makes the output blink at a fixed rate, until a key with mode M06 switches it off."),
    "M11": _Mode(
        4,
        "Preset on four keys: upper left sets the output to {level} whatever its last level, "
        "lower left dims it off, holding a right-hand key dims up or down.",
        LEVEL,
        DIM_TIME,
    ),
    "M12": _Mode(1, "Sets the output to {level}, whatever its last level.", LEVEL, DIM_TIME),
    # Compact dim controller only. Its leaflet describes the key, not the wheels.
    "M13": _Mode(
        1, "One key: a short press switches between off and the maximum level, a long press dims up or down."
    ),
    "M14": _Mode(1, "One key, with memory: a short press switches on and off, a long press dims up or down."),
}

_ROLLER: dict[str, _Mode] = {
    "M01": _Mode(
        2,
        "Two keys: the upper one opens the shutter, the lower one closes it; either stops it while it moves.",
        RUN_TIME,
    ),
    "M02": _Mode(1, "Opens the shutter.", RUN_TIME),
    "M03": _Mode(1, "Closes the shutter.", RUN_TIME),
    "M04": _Mode(1, "Stops the shutter when it is moving."),
    "M05": _Mode(
        4,
        "Four keys of an RF transmitter: upper left opens the shutter, lower left closes it, "
        "either right-hand key stops it.",
        RUN_TIME,
    ),
    "M06": _Mode(1, "Opens the shutter.", _RUN_AND_HOLD),
    "M07": _Mode(1, "Closes the shutter.", _RUN_AND_HOLD),
}

_MODES: dict[str, dict[str, _Mode]] = {
    "switch_module": _SWITCH,
    "dimmer_module": _DIMMER,
    "roller_module": _ROLLER,
}

_MISSING: dict[str, str] = {OFF_AFTER: "the set time", ON_AFTER: "the set time", LEVEL: "its preset level"}

_DURATION_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(s|m)", re.IGNORECASE)
_VOLT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*V", re.IGNORECASE)


def _duration(text: str | None) -> tuple[float, str] | None:
    """Seconds, and the value in one spelling: ``"2m"`` -> ``(120.0, "2 min")``."""
    match = _DURATION_RE.match(text or "")
    if match is None:
        return None
    number = float(match.group(1).replace(",", "."))
    minutes = match.group(2).lower() == "m"
    return number * (60 if minutes else 1), f"{number:g} {'min' if minutes else 's'}"


def _level(text: str | None) -> str | None:
    """A preset as a share of full: the manual's example is 5 V for 50 %.

    Seen once on hardware: a dimmer output with an M12 link at 4,0 V
    read back 0x66 of 0xFF, 40 %, after the key's telegram (2026-11-11).
    """
    match = _VOLT_RE.match(text or "")
    if match is None:
        return None
    volts = float(match.group(1).replace(",", "."))
    return f"{volts * 10:g} % ({(text or '').strip()})"


def _squeezed(text: str | None) -> str:
    return "".join((text or "").split()).lower()


def _dimming(t1: str | None, t2: str | None, curve: bool) -> dict[str, str]:
    """T2 is the dimming time; in M01 to M03, T1 says which way it applies.

    Wheel position 0: dimming on takes T2 and dimming off 1 s; position
    1 the other way round; any other position both take T2.
    """
    time = _duration(t2)
    if time is None:
        return {}
    if curve and _squeezed(t1) == _squeezed(DIMMER_T1_1[0]):
        return {DIM_ON_TIME: time[1], DIM_OFF_TIME: "1 s"}
    if curve and _squeezed(t1) == _squeezed(DIMMER_T1_1[1]):
        return {DIM_ON_TIME: "1 s", DIM_OFF_TIME: time[1]}
    return {DIM_TIME: time[1]}


def _parameters(spec: _Mode, t1: str | None, t2: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if spec.t1 in (HOLD_TIME, OFF_AFTER, ON_AFTER):
        time = _duration(t1)
        # A hold time of nothing is no condition at all.
        if time is not None and (spec.t1 != HOLD_TIME or time[0] > 0):
            values[spec.t1] = time[1]
    elif spec.t1 == RUN_TIME:
        time = _duration(t1)
        if time is not None:
            values[RUN_TIME] = time[1]
        elif _squeezed(t1) == "turnedoff":
            values[RUN_TIME] = "switched off"
    elif spec.t1 == _RUN_AND_HOLD:
        # The decoder writes "<run time> / <hold time>", a dash for no run time.
        run, _, hold = (t1 or "").partition("/")
        for name, part in ((RUN_TIME, run), (HOLD_TIME, hold)):
            time = _duration(part)
            if time is not None:
                values[name] = time[1]
    elif spec.t1 == LEVEL:
        level = _level(t1)
        if level is not None:
            values[LEVEL] = level
    if spec.t2 == DIM_TIME:
        values.update(_dimming(t1, t2, spec.t1 == _DIM_CURVE))
    return values


def _summary(spec: _Mode, values: dict[str, str]) -> str:
    parts = [spec.text.format_map({**_MISSING, **values})]
    if HOLD_TIME in values:
        parts.append(f"The key has to be held for {values[HOLD_TIME]} first.")
    if RUN_TIME in values:
        if values[RUN_TIME] == "switched off":
            parts.append("The run time is switched off.")
        else:
            parts.append(f"The motor runs for {values[RUN_TIME]}.")
    if DIM_TIME in values:
        parts.append(f"Dimming takes {values[DIM_TIME]}.")
    if DIM_ON_TIME in values:
        parts.append(f"Dimming on takes {values[DIM_ON_TIME]}, dimming off {values[DIM_OFF_TIME]}.")
    return " ".join(parts)


def describe_link(
    module_type: str | None, mode: str | None, t1: str | None = None, t2: str | None = None
) -> LinkMeaning | None:
    """What a link does, from the fields a decoded link record holds.

    ``module_type`` is the output module's (``"switch_module"``,
    ``"dimmer_module"``, ``"roller_module"``), ``mode`` its label or
    code (``"M06 (Delayed off (up to 2h))"`` or ``"M06"``), ``t1`` and
    ``t2`` the labels the decoder wrote. ``None`` for a module type or
    a mode the manuals do not cover, the colour controller's among them.
    """
    code = mode_code(mode)
    spec = _MODES.get(module_type or "", {}).get(code or "")
    if spec is None or code is None:
        return None
    values = _parameters(spec, t1, t2)
    return LinkMeaning(mode=code, keys=spec.keys, summary=_summary(spec, values), parameters=values)
