"""Constants for the Nikobus integration.

Device-type catalogue and bus protocol constants. The ``Name`` field
on each ``DEVICE_TYPES`` entry mirrors the wording on Niko's official
product pages (https://products.niko.eu/en/article/<MODEL>) so the
HA inventory log line, the device registry entry, and the entity
description all line up with what users see in Niko's catalogue and
in the Nikobus PC software. Sources are noted per-entry.

Routing (which decoder / scan path / HA platform handles each device)
is keyed off the device-type byte via ``_MODULE_TYPE_BY_DEVICE_TYPE``,
NOT off keyword matching against the ``Name`` field. That keeps name
edits free of hidden side-effects.
"""

# =============================================================================
# Discovery
# =============================================================================
DEVICE_TYPES = {
    # ------------------------------------------------------------------
    # Output modules — drive switching / dimming / shutter loads.
    # ------------------------------------------------------------------
    "01": {
        "Category": "Module",
        "Model": "05-000-02",
        "Channels": 12,
        "Name": "Switching module",
    },
    "02": {
        "Category": "Module",
        "Model": "05-001-02",
        "Channels": 6,
        "Name": "Roller shutter module",
    },
    "03": {
        "Category": "Module",
        "Model": "05-007-02",
        "Channels": 12,
        "Name": "Dimmer module",
    },
    "09": {
        "Category": "Module",
        "Model": "05-002-02",
        "Channels": 4,
        "Name": "Compact switch module",
    },
    "31": {
        "Category": "Module",
        "Model": "05-002-02",
        "Channels": 4,
        "Name": "Compact switch module",
    },
    "32": {
        "Category": "Module",
        "Model": "05-008-02",
        "Channels": 4,
        "Name": "Compact dim controller",
    },
    # ------------------------------------------------------------------
    # Controller / system modules — bridge, logic, feedback, audio.
    # ------------------------------------------------------------------
    "08": {
        "Category": "Module",
        "Model": "05-201",
        "Channels": 6,
        "Name": "PC-Logic",
    },
    "0A": {
        "Category": "Module",
        "Model": "05-200",
        "Name": "PC-Link",
    },
    "2B": {
        "Category": "Module",
        "Model": "05-205",
        "Name": "Audio distribution module",
    },
    "37": {
        "Category": "Module",
        "Model": "05-206",
        "Channels": 6,
        "Name": "Modular interface, 6 inputs",
    },
    "42": {
        "Category": "Module",
        "Model": "05-207",
        "Name": "Feedback module",
    },
    # ------------------------------------------------------------------
    # Bus push buttons — Nikobus original (no LEDs).
    # ------------------------------------------------------------------
    "04": {
        "Category": "Button",
        "Model": "05-342",
        "Channels": 2,
        "Name": "Bus push button, 2 control buttons",
    },
    "06": {
        "Category": "Button",
        "Model": "05-346",
        "Channels": 4,
        "Name": "Bus push button, 4 control buttons",
    },
    "0C": {
        "Category": "Button",
        "Model": "05-348",
        "Channels": 4,
        "Name": "Bus push button, 4 control buttons with IR receiver",
    },
    "12": {
        "Category": "Button",
        "Model": "05-349",
        "Channels": 8,
        "Name": "Bus push button, 8 control buttons",
    },
    # ------------------------------------------------------------------
    # Bus push buttons — feedback-LED variants.
    # ------------------------------------------------------------------
    "3F": {
        "Category": "Button",
        "Model": "05-060-02",
        "Channels": 2,
        "Name": "Bus push button, 2 control buttons with two feedback LEDs",
    },
    "40": {
        "Category": "Button",
        "Model": "05-064-02",
        "Channels": 4,
        "Name": "Bus push button, 4 control buttons with four feedback LEDs",
    },
    "41": {
        "Category": "Button",
        "Model": "05-078-02",
        "Channels": 8,
        "Name": "Bus push button, 8 control buttons with eight feedback LEDs",
    },
    # ------------------------------------------------------------------
    # External-contact interfaces.
    # ------------------------------------------------------------------
    "21": {
        "Category": "Button",
        "Model": "05-056",
        "Channels": 2,
        "Name": "Interface for push buttons",
    },
    "22": {
        "Category": "Button",
        "Model": "05-057",
        "Channels": 2,
        "Name": "Interface for switches",
    },
    "43": {
        "Category": "Button",
        "Model": "05-058",
        "Channels": 4,
        "Name": "Universal interface, 4 channels",
    },
    "44": {
        "Category": "Button",
        "Model": "05-058",
        "Channels": 8,
        "Name": "Universal interface, 8 channels",
    },
    # ------------------------------------------------------------------
    # RF transmitters.
    # ------------------------------------------------------------------
    "1F": {
        "Category": "Button",
        "Model": "05-311",
        "Channels": 2,
        "Name": "Mini hand-held RF transmitter, 2 channels",
    },
    "23": {
        "Category": "Button",
        "Model": "05-312",
        "Channels": 4,
        "Name": "Easywave hand-held RF transmitter, 4 channels",
    },
    "25": {
        "Category": "Button",
        "Model": "05-311",
        "Channels": 1,
        "Name": "Mini hand-held RF transmitter, 1 channel",
    },
    "26": {
        "Category": "Button",
        "Model": "05-314",
        "Channels": 4,
        "Name": "RF868 mini transmitter, 4 channels",
    },
    "3D": {
        "Category": "Button",
        "Model": "05-312",
        "Channels": 52,
        "Name": "Easywave RF transmitter, 52 operation points",
    },
    # ------------------------------------------------------------------
    # Sensors.
    # ------------------------------------------------------------------
    "28": {
        "Category": "Button",
        "Model": "05-7X5",
        "Channels": 2,
        "Name": "Motion detector with Nikobus interface",
    },
    # ------------------------------------------------------------------
    # Reserved / not-yet-identified types observed on real hardware.
    #
    # Each of these came from a Nikobus PC-Link inventory dump on a
    # production install and triggers the "Unknown device detected"
    # warning until catalogued. Adding them with ``Category="Reserved"``
    # silences the warning (the category check fires only on the
    # default ``"Unknown"``) and keeps both ``merge_discovered_modules``
    # and ``merge_discovered_buttons`` from acting on them — both gate
    # on ``Category in {"Module", "Button"}``.
    #
    # If you have authoritative info on what any of these are
    # (Nikobus product code, channel count), please open an issue
    # against fdebrus/nikobus-connect with the device-type byte,
    # observed bus addresses, and any model number printed on the
    # physical device.
    # ------------------------------------------------------------------
    "05": {"Category": "Reserved", "Model": "Unknown", "Name": "Reserved 0x05"},
    "14": {"Category": "Reserved", "Model": "Unknown", "Name": "Reserved 0x14"},
    "24": {"Category": "Reserved", "Model": "Unknown", "Name": "Reserved 0x24"},
    "34": {"Category": "Reserved", "Model": "Unknown", "Name": "Reserved 0x34"},
    "46": {"Category": "Reserved", "Model": "Unknown", "Name": "Reserved 0x46"},
    # 0x3B records appear at addresses 3CF000, 3CF010, 3CF020, ... on
    # the same install — a 16-byte stride starting at 3CF000 that's
    # consistent with PC-Logic (05-201) BP-cell directory entries. The
    # records carry routing data, not device identity, so we tag them
    # ``Reserved`` rather than promoting them to ``Module`` and risking
    # downstream code treating them as scannable hardware.
    "3B": {
        "Category": "Reserved",
        "Model": "PC-Logic Cell",
        "Name": "PC-Logic BP Cell (3CF0xx stride)",
    },
}


# Routing table: device-type byte → ``module_type`` bucket. Decoupled
# from the ``Name`` field so name edits can't accidentally change which
# decoder / scan path / platform handles a device. Only Module-category
# entries appear here; Button-category devices and Reserved entries fall
# through to ``other_module`` below.
_MODULE_TYPE_BY_DEVICE_TYPE: dict[str, str] = {
    "01": "switch_module",
    "02": "roller_module",
    "03": "dimmer_module",
    "08": "pc_logic",
    "09": "switch_module",
    "0A": "pc_link",
    "2B": "audio_module",
    "31": "switch_module",
    "32": "dimmer_module",
    "37": "interface_module",
    "42": "feedback_module",
}


def get_module_type_from_device_type(device_type_hex: str) -> str:
    """Return the module type bucket for a given device type hex code.

    Module-category devices are routed by the static
    ``_MODULE_TYPE_BY_DEVICE_TYPE`` table. Button-category and Reserved
    entries (and unknown bytes) fall through to ``other_module``.
    """

    normalized_type = (device_type_hex or "").strip().upper()
    device_info = DEVICE_TYPES.get(normalized_type, {})
    category = str(device_info.get("Category", "")).lower()

    if category != "module":
        return "other_module"

    return _MODULE_TYPE_BY_DEVICE_TYPE.get(normalized_type, "other_module")


CHANNEL_MAPPING = {
    0: "Channel 1",
    1: "Channel 2",
    2: "Channel 3",
    3: "Channel 4",
    4: "Channel 5",
    5: "Channel 6",
    6: "Channel 7",
    7: "Channel 8",
    8: "Channel 9",
    9: "Channel 10",
    10: "Channel 11",
    11: "Channel 12",
}

KEY_MAPPING = {
    1: {"1A": "8"},
    2: {"1A": "8", "1B": "C"},
    4: {"1A": "8", "1B": "C", "1C": "0", "1D": "4"},
    8: {
        "1A": "A",
        "1B": "E",
        "1C": "2",
        "1D": "6",
        "2A": "8",
        "2B": "C",
        "2C": "0",
        "2D": "4",
    },
}

KEY_MAPPING_MODULE = {
    1: {1: "8"},
    2: {1: "8", 3: "C"},
    4: {0: "0", 1: "8", 2: "4", 3: "C"},
    8: {0: "0", 1: "8", 2: "4", 3: "C", 4: "2", 5: "A", 6: "6", 7: "E"},
}

# =============================================================================
# Switch
# =============================================================================
SWITCH_MODE_MAPPING = {
    0: "M01 (On / off)",
    1: "M02 (On, with operating time)",
    2: "M03 (Off, with operation time)",
    3: "M04 (Pushbutton)",
    4: "M05 (Impulse)",
    5: "M06 (Delayed off (long up to 2h))",
    6: "M07 (Delayed on (long up to 2h))",
    7: "M08 (Flashing)",
    8: "M11 (Delayed off (short up to 50sec.))",
    9: "M12 (Delayed on (short up to 50sec.))",
    10: "M14 (Light scene on)",
    11: "M15 (Light scene on / off)",
}

SWITCH_TIMER_MAPPING = {
    0: ["10s", "0.5s", "0s"],
    1: ["1m", "1s", "1s"],
    2: ["2m", "2s", "2s"],
    3: ["3m", "3s", "3s"],
    4: ["4m", "4s", None],
    5: ["5m", "5s", None],
    6: ["6m", "6s", None],
    7: ["7m", "7s", None],
    8: ["8m", "8s", None],
    9: ["9m", "9s", None],
    10: ["15m", "15s", None],
    11: ["30m", "20s", None],
    12: ["45m", "25s", None],
    13: ["60m", "30s", None],
    14: ["90m", "40s", None],
    15: ["120m", "50s", None],
}

# =============================================================================
# Roller
# =============================================================================
ROLLER_MODE_MAPPING = {
    0: "M01 (Open - stop - close)",
    1: "M02 (Open)",
    2: "M03 (Close)",
    3: "M04 (Stop)",
    4: "M05 (Interface- and RF-control)",
    5: "M06 (Open with operating time)",
    6: "M07 (Close with operating time)",
}

ROLLER_TIMER_MAPPING = {
    0: ["Turned off", None, None],
    1: ["0,4 s (impuls)", None, None],
    2: ["6 s", None, None],
    3: ["8 s", None, None],
    4: ["10 s", None, None],
    5: ["12 s", None, None],
    6: ["6 s", None, None],
    7: ["14 s", None, None],
    8: ["16 s", None, None],
    9: ["18 s", None, None],
    10: ["20 s", None, None],
    11: ["25 s", None, None],
    12: ["30 s", None, None],
    13: ["40 s", None, None],
    14: ["50 s", None, None],
    15: ["60 s", None, None],
    16: ["90 s", None, None],
}

# =============================================================================
# Dimmer
# =============================================================================
DIMMER_MODE_MAPPING = {
    0: "M01 (Dim on/off (2 buttons))",
    1: "M02 (Dim on/off (4 buttons))",
    2: "M03 (Light scene on/off)",
    3: "M04 (Light scene on)",
    4: "M05 (On (if necessary with operating time))",
    5: "M06 (Off (eventually with operating time))",
    6: "M07 (Delayed off)",
    7: "M08 (Flashing)",
    8: "M11 (Preset on/off)",
    9: "M12 (Preset on)",
    10: "M13 (Dim on/off (1key))",
    11: "M14 (Dim on/off memory (1key))",
}

DIMMER_TIMER_MAPPING = {
    0: ["1,0 V", "T2=Dimming time on; Dimming time off=1s", "1 s"],
    1: ["1,5 V", "T2=Dimming time off; Dimming time on=1s", "2 s"],
    2: ["2,0 V", "T2=Dimming time off; Dimming time on", "4 s"],
    3: ["2,5 V", None, "6 s"],
    4: ["3,0 V", None, "8 s"],
    5: ["3,0 V", None, "10 s"],
    6: ["4,0 V", None, "15 s"],
    7: ["4,5 V", None, "20 s"],
    8: ["5,0 V", None, "30 s"],
    9: ["5,5 V", None, "40 s"],
    10: ["6,0 V", None, "1 m"],
    11: ["6,5 V", None, "90 s"],
    12: ["7,0 V", None, "2 m"],
    13: ["7,5 V", None, "3 m"],
    14: ["8,0 V", None, "4 m"],
    15: ["8,5 V", None, "5 m"],
    16: ["9,5 V", None, None],
    17: ["10,0 V", None, None],
}
