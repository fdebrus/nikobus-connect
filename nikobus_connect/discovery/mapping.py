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
        # Same physical product as 0x31 — two device-type bytes for one
        # SKU. Niko 05-002-02 has a single 4-output configuration; the
        # firmware on different revisions reports as 0x09 on some installs
        # and 0x31 on others. Both entries are correct, do not deduplicate.
        "Category": "Module",
        "Model": "05-002-02",
        "Channels": 4,
        "Name": "Compact switch module",
    },
    "31": {
        # Same physical product as 0x09 — see comment there.
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
        # 05-058 push-button mode: 4 inputs → 4 telegrams (one per
        # press). See 0x44 below for the switch-mode variant.
        "Category": "Button",
        "Model": "05-058",
        "Channels": 4,
        "Name": "Universal interface, 4 channels",
    },
    "44": {
        # Same physical product as 0x43 in switch mode: 4 inputs ×
        # 2 state-change telegrams (close + open) = 8 bus channels.
        # Niko 05-058 supports both push-button and switch contacts;
        # the firmware reports 0x43 in push-button mode and 0x44 in
        # switch mode. Niko's 05-057 documentation (the 2-input
        # sibling) confirms each switch contact emits 2 telegrams,
        # which scales to 4×2=8 for the 4-input 05-058.
        "Category": "Button",
        "Model": "05-058",
        "Channels": 8,
        "Name": "Universal interface, 8 channels",
    },
    # ------------------------------------------------------------------
    # RF transmitters.
    # ------------------------------------------------------------------
    "1F": {
        # Single RF-bus push button per Niko's PMNikobus catalogue:
        # "RF-bus push button [...] has two operation areas
        # available. It is finished with a full rocker, either with
        # or without labelling." Battery-powered wall-mounted RF
        # device that pairs with the 05-300 modular RF interface to
        # integrate into Nikobus over 868.3 MHz. Niko sells these as
        # a base radio module + interchangeable face plates rather
        # than under a single SKU; the catalogue doesn't list a
        # specific model number for the radio module itself, so
        # Model stays "Unknown" until someone reads the printed
        # number off a physical device. The previous mapping to
        # 05-311 was wrong — 05-311 is the 1-channel hand-held
        # mini-transmitter, not a wall device.
        "Category": "Button",
        "Model": "Unknown",
        "Channels": 2,
        "Name": "Single RF-bus push button, 2 operation areas",
    },
    "23": {
        # Double RF-bus push button per Niko's PMNikobus catalogue:
        # "RF-bus push button [...] has four operation areas
        # available. It is finished with two half-rockers, either
        # with or without labelling, or with a 3/4 and a 1/4
        # rocker." Battery-powered wall-mounted RF device that
        # pairs with the 05-300 modular RF interface to integrate
        # into Nikobus over 868.3 MHz. Confirmed by fdebrus from
        # physical hardware (devices at addresses 201250 and
        # 204915 on his install). Niko sells these as a base
        # radio module + interchangeable face plates rather than
        # under a single SKU, so Model stays "Unknown" until
        # someone reads the printed number off a physical device.
        # The previous mapping to 05-312 was wrong — 05-312 is
        # the 13-button hand-held Easywave remote, not a wall
        # device.
        "Category": "Button",
        "Model": "Unknown",
        "Channels": 4,
        "Name": "Double RF-bus push button, 4 operation areas",
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
        # 05-312 Easywave hand-held remote control. Niko's product
        # page (https://www.niko.eu/en/article/05-312) describes it
        # as a hand-held with 13 push buttons + 4 channel-selection
        # buttons, controlling up to 52 circuits — this entry maps
        # the 52-circuit firmware-reported population. (An earlier
        # hypothesis paired 0x23 with this entry as two modes of a
        # single 05-312; that was wrong — 0x23 is a wall switch,
        # not a hand-held.)
        "Category": "Button",
        "Model": "05-312",
        "Channels": 52,
        "Name": "Easywave hand-held RF transmitter, 52 operation points",
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
    #
    # Pre-Gen3 PC-Link diagnostic-echo note (0.5.24):
    # ``0x14``, ``0x24``, ``0x34`` used to be Reserved entries here
    # because they showed up in some users' inventory dumps. The
    # 2026-05-15 forensic on a pre-Gen3 PC-Link (Nikobus-HA user
    # report) confirmed these are NOT real device types — they're
    # artifacts of the firmware's "no programming written"
    # diagnostic-echo response, where unprogrammed registers return
    # a sequential identity pattern ``[N, N+1, ..., N+15]``. Byte 7
    # of that response (which our decoder reads as device-type)
    # cycles through ``0x04, 0x14, 0x24, 0x34, ...`` per register.
    # Only ``0x04`` is a real device type (05-342 push button), and
    # only by coincidence. The others were echo-pattern phantoms
    # that we'd kept as Reserved for years to silence the warning.
    # Now removed. ``0x05`` and ``0x46`` stay Reserved pending more
    # evidence — they may or may not be similar artifacts.
    # ------------------------------------------------------------------
    "05": {"Category": "Reserved", "Model": "Unknown", "Name": "Reserved 0x05"},
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

# PC-Logic logical inputs emit two bus events per press at offsets
# computed from ``convert_nikobus_address(physical) + 0`` (primary)
# and ``... + 4`` (alias) — distinct from the standard 2-channel
# layout used by wall-mounted push buttons. Captured from hardware
# on a 940C install: pressing slot 6 fires ``19814B`` and ``59814B``,
# whose first nibbles are 1 (= original_nibble + 0) and 5 (= + 4).
# ``merge_discovered_buttons`` consults this table instead of
# ``KEY_MAPPING`` when the device entry carries the synthesized
# ``pc_logic_parent_address`` provenance.
PC_LOGIC_KEY_MAPPING = {
    2: {"1A": "0", "1B": "4"},
}


# Niko 05-312 Easywave 52-key hand-held remote.
#
# Unlike the 1/2/4/8-channel wall buttons (where each key's bus
# address differs from the physical's ``convert_nikobus_address``
# only in the first NIBBLE), the 05-312's 52 sub-codes differ in the
# full first BYTE. The low nibble of the first byte identifies the
# channel button (1-4) on the remote; the high nibble identifies
# the sub-code within that channel.
#
# Channel low-nibble decoder:
#   Ch1 -> base 8, scene 0
#   Ch2 -> base C, scene 4
#   Ch3 -> base A, scene 2
#   Ch4 -> base E, scene 6
#
# Sub-code high-nibble decoder:
#   base A/B/C   -> 8 / C / 0
#   scene 1 A/B  -> 8 / C
#   scene 2 A/B  -> A / E
#   scene 3 A/B  -> 9 / D
#   scene 4 A/B  -> B / F
#   scene 5 A/B  -> 3 / 7
#
# So e.g. "1.5 B" = (high_nibble for scene 5 B = 7) << 4 | (Ch1 scene
# low = 0) = 0x70. The full bus address is then
# ``first_byte + convert_nikobus_address(physical)[2:]`` — for the
# real-install physical ``0E31C0`` (convert -> ``00E31C``), Ch1.5 B
# emits ``70E31C``, matching the user's .migrated dump exactly.
#
# Validated against a 2026-05-21 install: all 52 emitted bus
# addresses round-trip through this table. Document is the source
# of truth — change only against a fresh capture from real hardware.
EASYWAVE_52_KEY_MAPPING = {
    "1A": "88", "1B": "C8", "1C": "08",
    "1.1A": "80", "1.1B": "C0",
    "1.2A": "A0", "1.2B": "E0",
    "1.3A": "90", "1.3B": "D0",
    "1.4A": "B0", "1.4B": "F0",
    "1.5A": "30", "1.5B": "70",
    "2A": "8C", "2B": "CC", "2C": "0C",
    "2.1A": "84", "2.1B": "C4",
    "2.2A": "A4", "2.2B": "E4",
    "2.3A": "94", "2.3B": "D4",
    "2.4A": "B4", "2.4B": "F4",
    "2.5A": "34", "2.5B": "74",
    "3A": "8A", "3B": "CA", "3C": "0A",
    "3.1A": "82", "3.1B": "C2",
    "3.2A": "A2", "3.2B": "E2",
    "3.3A": "92", "3.3B": "D2",
    "3.4A": "B2", "3.4B": "F2",
    "3.5A": "32", "3.5B": "72",
    "4A": "8E", "4B": "CE", "4C": "0E",
    "4.1A": "86", "4.1B": "C6",
    "4.2A": "A6", "4.2B": "E6",
    "4.3A": "96", "4.3B": "D6",
    "4.4A": "B6", "4.4B": "F6",
    "4.5A": "36", "4.5B": "76",
}


# Channel counts whose key offsets are expressed as a full 2-hex
# first byte (full first-byte replacement on the physical's
# converted address) rather than a single-nibble add. Used by
# ``merge_discovered_buttons`` to dispatch the right derivation
# path. Currently only 05-312 (52 keys); add to this table when
# new multi-key remote variants are catalogued.
KEY_MAPPING_FIRST_BYTE = {
    52: EASYWAVE_52_KEY_MAPPING,
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
    # T1-nibble → operating time per Niko ParamBase ``S_DB_ROLLUIK_T2``.
    # 4-bit nibble, so exactly 16 entries (0..15).
    #
    # The pre-correction table had a duplicate "6 s" entry at index 6 that
    # shifted every subsequent value down by one slot, so a roller with
    # ``t1_raw=14`` showed "50 s" when the configured operating time was
    # actually "60 s", and ``t1_raw=15`` showed "60 s" when the actual
    # value was "90 s". Verified against Niko's product database (the
    # canonical ``S_DB_ROLLUIK_T2`` parameter table in product.mdb).
    0: ["Turned off", None, None],
    1: ["0,4 s (impuls)", None, None],
    2: ["6 s", None, None],
    3: ["8 s", None, None],
    4: ["10 s", None, None],
    5: ["12 s", None, None],
    6: ["14 s", None, None],
    7: ["16 s", None, None],
    8: ["18 s", None, None],
    9: ["20 s", None, None],
    10: ["25 s", None, None],
    11: ["30 s", None, None],
    12: ["40 s", None, None],
    13: ["50 s", None, None],
    14: ["60 s", None, None],
    15: ["90 s", None, None],
}

# Roller M06 ("Open with operating time") and M07 ("Close with operating
# time") use the T1 nibble to encode a PAIRED duration rather than a
# single operating time. Format is ``<long-press-time> / <short-press-time>``.
#
# Per Niko ParamBase ``S_DB_ROLLUIK_T3`` (product.mdb KP=6). Used only for
# mode bytes 0x05 (M06) and 0x06 (M07) — the regular operating-time modes
# (M01/M02/M03/M05) use ``ROLLER_TIMER_MAPPING`` instead.
ROLLER_T3_MAPPING = {
    0x0: "-  / 1s",
    0x1: "-  / 1s",   # canonical Niko table has both slot 0 and slot 1 as
                      # "-  / 1s" — keeping the duplication faithful to the
                      # spec rather than de-duplicating.
    0x2: "-  / 2s",
    0x3: "-  / 3s",
    0x4: "8s / 1s",
    0x5: "8s / 2s",
    0x6: "8s / 3s",
    0x7: "16s / 1s",
    0x8: "16s / 2s",
    0x9: "16s / 3s",
    0xA: "30s / 1s",
    0xB: "30s / 2s",
    0xC: "30s / 3s",
    0xD: "90s / 1s",
    0xE: "90s / 2s",
    0xF: "90s / 3s",
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
    # Indices 10 and 11 had col[2] swapped pre-fix ("1 m" / "90 s"). The
    # Niko ``S_DB_DIMMER_T2`` ramp-time table is monotonically increasing:
    # 30 s, 40 s, 50 s, 1 m, 2 m, ... so slot 10 is "50 s" and slot 11 is
    # "1 m". "90 s" is not a value in the official ramp-time table at all.
    10: ["6,0 V", None, "50 s"],
    11: ["6,5 V", None, "1 m"],
    12: ["7,0 V", None, "2 m"],
    13: ["7,5 V", None, "3 m"],
    14: ["8,0 V", None, "4 m"],
    15: ["8,5 V", None, "5 m"],
    16: ["9,5 V", None, None],
    17: ["10,0 V", None, None],
}

# ---------------------------------------------------------------------------
# Authoritative per-mode T1 lookup tables for the dimmer.
#
# Niko's product database exposes a separate parameter table per dimmer
# mode — there is no "one T1 table per module". The legacy
# ``DIMMER_TIMER_MAPPING`` collapses three of the four real tables into
# one structure with positional columns, which prevents callers from
# resolving the T1 value correctly per mode.
#
# Source: product.mdb ParamBase rows (Niko PC-software master catalogue):
#   KP=11 S_DB_DIMMER_AMOUNT  → preset dim level (M11 / M12)
#   KP=12 S_DB_DIMMER_T2      → T2 ramp/fade time (every dimmer mode)
#   KP=13 S_DB_DIMMER_T1_1    → on/off step config (M01 / M02 / M03)
#   KP=14 S_DB_DIMMER_T1_2    → push time (M05 / M06)
#   KP=15 S_DB_DIMMER_T1_3    → delayed-off duration (M07)
# ---------------------------------------------------------------------------

DIMMER_T1_1 = ("On/off step 0", "On/off step 1", "On/off step 2-F")
DIMMER_T1_2 = ("0 s", "1 s", "2 s", "3 s")
DIMMER_T1_3 = (
    "10 s", "1 m", "2 m", "3 m", "4 m", "5 m", "6 m", "7 m",
    "8 m", "9 m", "15 m", "30 m", "45 m", "60 m", "90 m", "120 m",
)
DIMMER_AMOUNT_PERCENT = (
    "1%", "1.5%", "2%", "2.5%", "3%", "3.5%", "4%", "4.5%",
    "5%", "5.5%", "6%", "6.5%", "7%", "8%", "9%", "10%",
)
DIMMER_T2_RAMP = (
    "1 s", "2 s", "4 s", "6 s", "8 s", "10 s", "15 s", "20 s",
    "30 s", "40 s", "50 s", "1 m", "2 m", "3 m", "4 m", "5 m",
)

# Mode-byte → T1 lookup table dispatch. Modes outside this map (M04, M08,
# M13, M14) use no T1 parameter per Niko's spec — the chunk's T1 nibble
# is ignored for them, so we deliberately return ``None`` rather than
# fabricating a value.
DIMMER_MODE_T1_LOOKUP = {
    0x00: DIMMER_T1_1,         # M01 - Dim on/off (2 buttons)
    0x01: DIMMER_T1_1,         # M02 - Dim on/off (4 buttons)
    0x02: DIMMER_T1_1,         # M03 - Light scene on/off
    0x04: DIMMER_T1_2,         # M05 - On (if necessary with operating time)
    0x05: DIMMER_T1_2,         # M06 - Off (eventually with operating time)
    0x06: DIMMER_T1_3,         # M07 - Delayed off
    0x08: DIMMER_AMOUNT_PERCENT,  # M11 - Preset on/off
    0x09: DIMMER_AMOUNT_PERCENT,  # M12 - Preset on
}
