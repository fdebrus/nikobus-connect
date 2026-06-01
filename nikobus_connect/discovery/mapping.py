"""Constants for the Nikobus integration.

Device-type catalogue and bus protocol constants. **Every Model and Name
in this file traces directly to Niko's own master product database**
(``product.mdb`` shipped with the Nikobus PC software). Each entry
carries a ``VendorRef`` field with the corresponding ``S_DB_*``
localization key from that database, so it's mechanically verifiable
that we haven't drifted from vendor terminology.

The mapping from on-bus device-type byte → product comes from
real-hardware traces (multiple installs); the product.mdb itself
doesn't carry the device-type byte. Where a byte is observed on the
bus but the specific Niko SKU isn't independently confirmed, we mark
Model = "Unknown" and use a plain-English description that mirrors
Niko's category wording.

Routing (which decoder / scan path / HA platform handles each device)
is keyed off the device-type byte via ``_MODULE_TYPE_BY_DEVICE_TYPE``,
NOT off keyword matching against the ``Name`` field. That keeps name
edits free of hidden side-effects.

S_DB_* → English translation policy:
- We translate from the localization key, not from the Belgian/Dutch
  shipping label, so the wording stays consistent across the codebase.
- Where Niko ships an English product-page name on niko.eu, we match
  it (``Switch module`` for 05-000-02, ``Dim controller module`` for
  05-007-02, etc.).
- Where the only available label is the S_DB_* key, we transliterate
  the key (e.g. ``S_DB_KNOP_4_GRAFIET`` → ``Push button 4 (graphite)``).
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
        "Name": "Switch module",
        "VendorRef": "S_DB_SCHAKEL_MODULE",
    },
    "02": {
        "Category": "Module",
        "Model": "05-001-02",
        "Channels": 6,
        "Name": "Roller shutter module",
        "VendorRef": "S_DB_ROLLUIK_MODULE",
    },
    "03": {
        "Category": "Module",
        "Model": "05-007-02",
        "Channels": 12,
        "Name": "Dim controller module",
        "VendorRef": "S_DB_DIM_CONTROLLER_MODULE",
    },
    "09": {
        # Same physical product as 0x31 — two device-type bytes for
        # one SKU. Niko 05-002-02 has a single 4-output configuration;
        # the firmware on different revisions reports as 0x09 on some
        # installs and 0x31 on others. Both entries are correct, do
        # not deduplicate.
        "Category": "Module",
        "Model": "05-002-02",
        "Channels": 4,
        "Name": "Compact switch module",
        "VendorRef": "S_DB_SCHAKEL_MODULE_TINY",
    },
    "31": {
        # Same physical product as 0x09 — see comment there.
        "Category": "Module",
        "Model": "05-002-02",
        "Channels": 4,
        "Name": "Compact switch module",
        "VendorRef": "S_DB_SCHAKEL_MODULE_TINY",
    },
    "32": {
        "Category": "Module",
        "Model": "05-008-02",
        "Channels": 4,
        "Name": "Compact dim controller",
        "VendorRef": "S_DB_DIM_MODULE_TINY",
    },
    # ------------------------------------------------------------------
    # Controller / system modules — bridge, logic, feedback, audio.
    # ------------------------------------------------------------------
    "08": {
        "Category": "Module",
        "Model": "05-201",
        "Channels": 6,
        "Name": "Logic module",
        "VendorRef": "S_DB_LOGIC_FUNCTION",
    },
    "0A": {
        "Category": "Module",
        "Model": "05-200",
        "Name": "PC-Link",
        "VendorRef": "S_DB_PC_LINK_EXTERN",
    },
    "2B": {
        "Category": "Module",
        "Model": "05-205",
        "Name": "Audio distribution module",
        "VendorRef": "S_DB_AUDIO_MODULE",
    },
    "37": {
        "Category": "Module",
        "Model": "05-206",
        "Channels": 6,
        "Name": "Modular interface, 6 inputs",
        "VendorRef": "S_DB_INPUT6",
    },
    "42": {
        "Category": "Module",
        "Model": "05-207",
        "Name": "Feedback module",
        "VendorRef": "S_DB_PC_FEEDBACK_MODULE",
    },
    # ------------------------------------------------------------------
    # Bus push buttons — Nikobus original (no LEDs).
    #
    # 0x04 / 0x06 / 0x0C / 0x12 carry the Niko model numbers from
    # product.mdb (KP=4, KP=6, KP=26, KP=18 respectively). The
    # pre-0.16.2 entries used the 05-342/346/348/349 codes which
    # don't appear in any modern Niko catalogue; those were
    # wholesale/regional aliases that have since been retired.
    # ------------------------------------------------------------------
    "04": {
        "Category": "Button",
        "Model": "05-060",
        "ModelAlt": "4*-072",
        "ModelAltLegacy": "05-060-01",
        "Channels": 2,
        "Name": "Bus push button, 2 control buttons",
        "VendorRef": "S_DB_BUSDRUKKNOP_2",
    },
    "06": {
        "Category": "Button",
        "Model": "05-064",
        "ModelAlt": "4*-074",
        "ModelAltLegacy": "05-064-01",
        "Channels": 4,
        "Name": "Bus push button, 4 control buttons",
        "VendorRef": "S_DB_BUSDRUKKNOP_4",
    },
    "0C": {
        "Category": "Button",
        "Model": "05-09x",
        "ModelAltLegacy": "05-09x-01",
        "Channels": 4,
        "Name": "Push button, 4 control buttons with IR receiver",
        "VendorRef": "S_DB_KNOP_4_IR_UNIQUE",
    },
    "12": {
        "Category": "Button",
        "Model": "4*-078",
        "ModelAltLegacy": "05-078-01",
        "Channels": 8,
        "Name": "Push button, 8 control buttons (graphite)",
        "VendorRef": "S_DB_KNOP_8_GRAFIET",
    },
    # 0x10 / 0x11 — the 2- and 4-control-point siblings of the 0x12
    # 8-button (4*-078). Confirmed from a real install (Nikobus-HA user
    # fdebrus): the original 20-year-old hardware order listed
    # ``47-082`` (2 buttons), ``47-084`` (4 buttons) and ``47-088``
    # (8 buttons), in quantities 9 / 10 / 11. A PC-Link inventory on
    # that install reported exactly 9 devices of device-type 0x10 and
    # 10 of 0x11 (the 8-button 47-088 reports as the already-catalogued
    # 0x12). Per-key decode evidence corroborates the channel counts:
    # 0x10 records only ever decode keys 0/2 (a 2-point button), 0x11
    # decodes keys 0..3 (a 4-point button). Without these entries both
    # variants fell through as ``Category="Unknown"`` and were dropped
    # by ``merge_discovered_buttons``.
    "10": {
        "Category": "Button",
        "Model": "4*-082",
        "Channels": 2,
        "Name": "Bus push button, 2 control buttons",
        "VendorRef": "S_DB_BUSDRUKKNOP_2",
    },
    "11": {
        "Category": "Button",
        "Model": "4*-084",
        "Channels": 4,
        "Name": "Bus push button, 4 control buttons",
        "VendorRef": "S_DB_BUSDRUKKNOP_4",
    },
    # ------------------------------------------------------------------
    # Bus push buttons — feedback-LED variants (graphite series).
    # ------------------------------------------------------------------
    "3F": {
        "Category": "Button",
        "Model": "05-060-02",
        "Channels": 2,
        "Name": "Push button, 2 control buttons with feedback LEDs (graphite)",
        "VendorRef": "S_DB_KNOP_2_GRAFIET_FB",
    },
    "40": {
        "Category": "Button",
        "Model": "05-064-02",
        "Channels": 4,
        "Name": "Push button, 4 control buttons with feedback LEDs (graphite)",
        "VendorRef": "S_DB_KNOP_4_GRAFIET_FB",
    },
    "41": {
        "Category": "Button",
        "Model": "05-078-02",
        "Channels": 8,
        "Name": "Push button, 8 control buttons with feedback LEDs (graphite)",
        "VendorRef": "S_DB_KNOP_8_GRAFIET_FB",
    },
    # ------------------------------------------------------------------
    # External-contact interfaces.
    # ------------------------------------------------------------------
    "21": {
        "Category": "Button",
        "Model": "05-056",
        "Channels": 2,
        "Name": "Push-button interface",
        "VendorRef": "S_DB_INTERF_DRUKKNOP",
    },
    "22": {
        "Category": "Button",
        "Model": "05-057",
        "Channels": 2,
        "Name": "Switch interface",
        "VendorRef": "S_DB_INTERF_SCHAK",
    },
    "43": {
        # 05-058 push-button mode: 4 inputs → 4 telegrams (one per
        # press). See 0x44 below for the switch-mode variant.
        # Niko product.mdb lists 05-058 under TWO products (KP=67 and
        # KP=68) that share the SKU but differ by S_DB description —
        # the same SKU configured as push buttons (this entry) or
        # switches (the 0x44 entry).
        "Category": "Button",
        "Model": "05-058",
        "Channels": 4,
        "Name": "Universal interface, push-button mode",
        "VendorRef": "S_DB_INTERF_DRUKKNOP",
    },
    "44": {
        # Same physical product as 0x43 in switch mode: 4 inputs ×
        # 2 state-change telegrams (close + open) = 8 bus channels.
        "Category": "Button",
        "Model": "05-058",
        "Channels": 8,
        "Name": "Universal interface, switch mode",
        "VendorRef": "S_DB_INTERF_SCHAK",
    },
    # ------------------------------------------------------------------
    # RF transmitters.
    # ------------------------------------------------------------------
    "1F": {
        # 2-channel RF-bus push button per Niko's PMNikobus catalogue.
        # Battery-powered wall-mounted RF device that pairs with the
        # 05-300 modular RF interface.
        #
        # product.mdb (KP=52, ``S_DB_RF_WAND_2``) lists ``05-301-4*`` as
        # the primary code and ``05-302`` as the alt. The wildcard
        # ``05-301-4*`` is Niko's technical programming reference; the
        # plain ``05-302`` is the catalogue number that appears on
        # consumer hardware labels and Niko's product pages. We promote
        # ``05-302`` to ``Model`` (so the HA device list matches what
        # users find on niko.eu/en/article/05-302) and keep the
        # technical reference in ``ModelAlt``.
        "Category": "Button",
        "Model": "05-302",
        "ModelAlt": "05-301-4*",
        "ModelAltLegacy": "410-00001",
        "Channels": 2,
        "Name": "RF-bus push button, 2 channels",
        "VendorRef": "S_DB_RF_WAND_2",
    },
    "23": {
        # 4-channel RF-bus push button per Niko's PMNikobus catalogue.
        # Confirmed by fdebrus from physical hardware (devices at
        # addresses 201250 and 204915 on his install).
        #
        # product.mdb (KP=53, ``S_DB_RF_WAND_4``) lists ``05-303-4*`` as
        # the primary code and ``05-304`` as the alt. Same convention
        # as 0x1F: we promote the consumer-facing ``05-304`` to
        # ``Model`` and keep the wildcard technical code in
        # ``ModelAlt``.
        "Category": "Button",
        "Model": "05-304",
        "ModelAlt": "05-303-4*",
        "ModelAltLegacy": "410-00002",
        "Channels": 4,
        "Name": "RF-bus push button, 4 channels",
        "VendorRef": "S_DB_RF_WAND_4",
    },
    "25": {
        "Category": "Button",
        "Model": "05-311",
        "Channels": 1,
        "Name": "Mini hand-held RF transmitter, 1 channel",
        "VendorRef": "S_DB_KNOP_1_RF868",
    },
    "26": {
        "Category": "Button",
        "Model": "05-314",
        "Channels": 4,
        "Name": "RF868 mini transmitter, 4 channels",
        "VendorRef": "S_DB_KNOP_16_RF868_MINI",
    },
    "3D": {
        # 05-312 Easywave hand-held remote control. Niko's product
        # page (https://www.niko.eu/en/article/05-312) describes it
        # as a hand-held with 13 push buttons + 4 channel-selection
        # buttons, controlling up to 52 circuits — this entry maps
        # the 52-circuit firmware-reported population.
        "Category": "Button",
        "Model": "05-312",
        "Channels": 52,
        "Name": "Easywave hand-held RF transmitter, 52 operation points",
        "VendorRef": "S_DB_REMOTE4x5CH",
    },
    # ------------------------------------------------------------------
    # Sensors / actors.
    # ------------------------------------------------------------------
    "28": {
        # Niko product.mdb (KP=40) lists this SKU as
        # ``S_DB_ACTOR_SENSOR`` — a sensor module with motion-detection
        # capability that exposes a Nikobus interface. NikoRefNr is
        # ``05-7*5`` (a series prefix, not a single SKU); the legacy
        # part number ``430-00500`` is recorded as the alt.
        "Category": "Button",
        "Model": "05-7*5",
        "ModelAltLegacy": "430-00500",
        "Channels": 2,
        "Name": "Motion detector with Nikobus interface",
        "VendorRef": "S_DB_ACTOR_SENSOR",
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


# Device-type-specific op-point key mappings that override the
# channel-count-keyed ``KEY_MAPPING`` default. Needed when two products
# share a channel count but emit their keys at DIFFERENT first-nibble
# offsets.
#
# 0x10 (4*-082, 2 control buttons): hardware capture (fdebrus install,
# 10 units) shows its two faces fire at first-nibble offsets +0 / +4 —
# the same layout as keys 1C/1D of the 4-button — NOT the +8 / +C used
# by the other 2-channel devices (05-060 0x04, 05-056 0x21 interface).
# Confirmed against the user's own legacy descriptions: physical 130078
# → faces 078032 (nibble 0) and 478032 (nibble 4). Without this override
# 0x10 op-points were generated at the wrong addresses (8/C) and its
# link records (decoded at key indices 0/2) never matched.
#
# Keyed by the on-bus device-type byte; ``merge_discovered_buttons``
# consults this before falling back to ``KEY_MAPPING[num_channels]``.
DEVICE_TYPE_KEY_MAPPING = {
    "10": {"1A": "0", "1B": "4"},
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
    # 2-channel devices come in two key-index families that the link
    # resolver must both handle: the 05-060 (0x04) / 05-056 (0x21)
    # interface emit keys 1/3 (nibbles 8/C), while the 4*-082 (0x10)
    # 2-button emits keys 0/2 (nibbles 0/4 — same slots as a 4-button's
    # 1C/1D). The resolver only sees the channel count, not the device
    # type, so this table carries ALL FOUR indices. That is safe because
    # a physical device only ever emits its own two key indices; the
    # extra entries are never hit for a device that doesn't use them.
    2: {0: "0", 1: "8", 2: "4", 3: "C"},
    4: {0: "0", 1: "8", 2: "4", 3: "C"},
    8: {0: "0", 1: "8", 2: "4", 3: "C", 4: "2", 5: "A", 6: "6", 7: "E"},
}

# =============================================================================
# Switch
#
# Mode descriptions mirror Niko's PC-software UI (English localization
# of the ``S_DB_DESC_SCHAKEL_M*`` keys in product.mdb LinkModeBase).
# Slot 0xA actually carries M14 / 0xB carries M15 — the gap from M08
# to M11 (0x07 → 0x08) is intentional: Niko numbered modes
# M01..M08, M11..M15 to leave room for future M09/M10 (which never
# shipped for switch modules; the audio module took those codes).
# =============================================================================
SWITCH_MODE_MAPPING = {
    0: "M01 (On / off)",
    1: "M02 (On + Operating time)",
    2: "M03 (Off + Operating time)",
    3: "M04 (Pushbutton)",
    4: "M05 (Impulse)",
    5: "M06 (Delayed off (up to 2h))",
    6: "M07 (Delayed on (up to 2h))",
    7: "M08 (Flashing)",
    8: "M11 (Delayed off (up to 50s))",
    9: "M12 (Delayed on (up to 50s))",
    10: "M14 (Light scene on)",
    11: "M15 (Light scene on / off)",
}

# Vendor-ref keys for switch modes. Maps LinkID → Niko ``S_DB_DESC_*``
# token (product.mdb LinkModeBase, ``StrDescription`` column).
# Surfaced so a future consumer that wants the raw localization key
# (e.g. for i18n) can look it up without re-parsing the mode string.
SWITCH_MODE_VENDOR_REF = {
    0: "S_DB_DESC_SCHAKEL_M1",
    1: "S_DB_DESC_SCHAKEL_M2",
    2: "S_DB_DESC_SCHAKEL_M3",
    3: "S_DB_DESC_SCHAKEL_M4",
    4: "S_DB_DESC_SCHAKEL_M5",
    5: "S_DB_DESC_SCHAKEL_M6",
    6: "S_DB_DESC_SCHAKEL_M7",
    7: "S_DB_DESC_SCHAKEL_M8",
    8: "S_DB_DESC_SCHAKEL_M11",
    9: "S_DB_DESC_SCHAKEL_M12",
    10: "S_DB_DESC_SCHAKEL_M14",
    11: "S_DB_DESC_SCHAKEL_M15",
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
#
# Mode descriptions mirror Niko's PC-software UI (English localization
# of the ``S_DB_DESC_ROLLUIK_M*`` keys in product.mdb LinkModeBase).
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

ROLLER_MODE_VENDOR_REF = {
    0: "S_DB_DESC_ROLLUIK_M1",
    1: "S_DB_DESC_ROLLUIK_M2",
    2: "S_DB_DESC_ROLLUIK_M3",
    3: "S_DB_DESC_ROLLUIK_M4",
    4: "S_DB_DESC_ROLLUIK_M5",
    5: "S_DB_DESC_ROLLUIK_M6",
    6: "S_DB_DESC_ROLLUIK_M7",
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
#
# Mode descriptions mirror Niko's PC-software UI (English localization
# of the ``S_DB_DESC_DIMMER_M*`` keys in product.mdb LinkModeBase).
# =============================================================================
DIMMER_MODE_MAPPING = {
    0: "M01 (Dim on/off (2 buttons))",
    1: "M02 (Dim on/off (4 buttons))",
    2: "M03 (Light scene on/off)",
    3: "M04 (Light scene on)",
    4: "M05 (On + Operating time)",
    5: "M06 (Off + Operating time)",
    6: "M07 (Delayed off)",
    7: "M08 (Flashing)",
    8: "M11 (Preset on/off)",
    9: "M12 (Preset on)",
    10: "M13 (Dim on/off (1 button))",
    11: "M14 (Dim on/off memory (1 button))",
}

DIMMER_MODE_VENDOR_REF = {
    0: "S_DB_DESC_DIMMER_M1",
    1: "S_DB_DESC_DIMMER_M2",
    2: "S_DB_DESC_DIMMER_M3",
    3: "S_DB_DESC_DIMMER_M4",
    4: "S_DB_DESC_DIMMER_M5",
    5: "S_DB_DESC_DIMMER_M6",
    6: "S_DB_DESC_DIMMER_M7",
    7: "S_DB_DESC_DIMMER_M8",
    8: "S_DB_DESC_DIMMER_M11",
    9: "S_DB_DESC_DIMMER_M12",
    10: "S_DB_DESC_DIMMER_M13",
    11: "S_DB_DESC_DIMMER_M14",
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
