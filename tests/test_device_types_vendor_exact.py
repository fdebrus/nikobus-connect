"""Tests pinning every ``DEVICE_TYPES`` entry to Niko's vendor catalogue.

Every entry must carry a ``VendorRef`` field naming the ``S_DB_*``
localization key from product.mdb. ``Model`` must be the current Niko
NikoRefNr (or ``"Unknown"`` when the device-type byte is observed on
real hardware but no vendor SKU is confirmed for it). ``Name`` must
be the English translation of the corresponding ``StrDescription``.

These tests run as a regression guard so a future "let's just rename
this for clarity" edit doesn't silently drift from vendor terminology.
"""

from __future__ import annotations

from nikobus_connect.discovery.mapping import (
    DEVICE_TYPES,
    DIMMER_MODE_MAPPING,
    DIMMER_MODE_VENDOR_REF,
    ROLLER_MODE_MAPPING,
    ROLLER_MODE_VENDOR_REF,
    SWITCH_MODE_MAPPING,
    SWITCH_MODE_VENDOR_REF,
)


# ---------------------------------------------------------------------------
# Provenance: every DEVICE_TYPES entry carries a VendorRef
# ---------------------------------------------------------------------------


def test_every_module_and_button_entry_has_vendor_ref() -> None:
    """Every Module / Button entry must declare its product.mdb origin."""
    for byte, entry in DEVICE_TYPES.items():
        if entry["Category"] not in ("Module", "Button"):
            continue
        assert "VendorRef" in entry, (
            f"device-type 0x{byte} lacks a VendorRef — every catalogued "
            f"entry must reference its product.mdb origin"
        )
        ref = entry["VendorRef"]
        assert ref.startswith("S_DB_"), (
            f"device-type 0x{byte}: VendorRef={ref!r} must be an "
            f"S_DB_* localization key from product.mdb"
        )


def test_vendor_refs_match_product_mdb() -> None:
    """The set of VendorRef values must be a subset of product.mdb's
    StrDescription column. Hard-coding the known refs here so a typo
    in one of the mapping entries fails loudly rather than silently
    drifting to a non-existent key."""
    known_vendor_refs = {
        # Modules (product.mdb ``StrDescription`` column)
        "S_DB_SCHAKEL_MODULE",
        "S_DB_ROLLUIK_MODULE",
        "S_DB_DIM_CONTROLLER_MODULE",
        "S_DB_SCHAKEL_MODULE_TINY",
        "S_DB_DIM_MODULE_TINY",
        "S_DB_LOGIC_FUNCTION",
        "S_DB_PC_LINK_EXTERN",
        "S_DB_AUDIO_MODULE",
        "S_DB_INPUT6",
        "S_DB_PC_FEEDBACK_MODULE",
        # Buttons / inputs
        "S_DB_BUSDRUKKNOP_2",
        "S_DB_BUSDRUKKNOP_2_LED",
        "S_DB_BUSDRUKKNOP_4",
        "S_DB_KNOP_4_IR_UNIQUE",
        "S_DB_KNOP_8_GRAFIET",
        "S_DB_KNOP_2_GRAFIET_FB",
        "S_DB_KNOP_4_GRAFIET_FB",
        "S_DB_KNOP_8_GRAFIET_FB",
        "S_DB_INTERF_DRUKKNOP",
        "S_DB_INTERF_SCHAK",
        # RF transmitters
        "S_DB_RF_WAND_2",
        "S_DB_RF_WAND_4",
        "S_DB_KNOP_1_RF868",
        "S_DB_KNOP_16_RF868_MINI",
        "S_DB_REMOTE4x5CH",
        # Sensors / actors
        "S_DB_ACTOR_SENSOR",
    }
    used = {
        entry["VendorRef"]
        for entry in DEVICE_TYPES.values()
        if entry.get("VendorRef")
    }
    assert used.issubset(known_vendor_refs), (
        f"Unknown VendorRef values found in DEVICE_TYPES: "
        f"{used - known_vendor_refs}"
    )


# ---------------------------------------------------------------------------
# Catalogue audit: every model is the current Niko code
# ---------------------------------------------------------------------------


def test_device_types_use_current_niko_codes_not_legacy_aliases() -> None:
    """Pre-0.16.2 had ``05-342`` / ``05-346`` / ``05-348`` / ``05-349``
    on the bus push-button entries — those codes don't exist in
    Niko's current catalogue. Replaced with the canonical product.mdb
    codes (``05-060`` / ``05-064`` / ``05-09x`` / ``4*-078``)."""
    legacy_codes = {"05-342", "05-346", "05-348", "05-349"}
    for byte, entry in DEVICE_TYPES.items():
        model = entry.get("Model", "")
        assert model not in legacy_codes, (
            f"device-type 0x{byte} still uses retired legacy code {model}"
        )


def test_specific_product_mdb_anchors() -> None:
    """A handful of high-confidence anchors that must hold."""
    assert DEVICE_TYPES["01"]["Model"] == "05-000-02"
    assert DEVICE_TYPES["01"]["VendorRef"] == "S_DB_SCHAKEL_MODULE"

    assert DEVICE_TYPES["02"]["Model"] == "05-001-02"
    assert DEVICE_TYPES["02"]["VendorRef"] == "S_DB_ROLLUIK_MODULE"

    assert DEVICE_TYPES["03"]["Model"] == "05-007-02"
    assert DEVICE_TYPES["03"]["VendorRef"] == "S_DB_DIM_CONTROLLER_MODULE"

    assert DEVICE_TYPES["04"]["Model"] == "05-060"
    assert DEVICE_TYPES["04"]["ModelAlt"] == "4*-072"
    assert DEVICE_TYPES["04"]["VendorRef"] == "S_DB_BUSDRUKKNOP_2"

    # 0x05 — the feedback-LED sibling of the 0x04 05-060. Promoted
    # from Reserved after the #478 registry decode matched three
    # type-05 records to the install's .nkb 05-061 components on both
    # address and BP index. ProductBase KeyProductBase=5.
    assert DEVICE_TYPES["05"]["Model"] == "05-061"
    assert DEVICE_TYPES["05"]["Channels"] == 2
    assert DEVICE_TYPES["05"]["VendorRef"] == "S_DB_BUSDRUKKNOP_2_LED"

    assert DEVICE_TYPES["06"]["Model"] == "05-064"
    assert DEVICE_TYPES["06"]["ModelAlt"] == "4*-074"
    assert DEVICE_TYPES["06"]["VendorRef"] == "S_DB_BUSDRUKKNOP_4"

    assert DEVICE_TYPES["08"]["Model"] == "05-201"
    assert DEVICE_TYPES["08"]["VendorRef"] == "S_DB_LOGIC_FUNCTION"

    assert DEVICE_TYPES["0A"]["Model"] == "05-200"
    assert DEVICE_TYPES["0A"]["VendorRef"] == "S_DB_PC_LINK_EXTERN"

    assert DEVICE_TYPES["12"]["Model"] == "4*-078"
    assert DEVICE_TYPES["12"]["VendorRef"] == "S_DB_KNOP_8_GRAFIET"

    # 0x10 / 0x11 — the 2- and 4-control-point siblings of the 0x12
    # 8-button (47-08x series). Catalogued from a real install where the
    # original hardware order was 9× 47-082 / 10× 47-084 / 11× 47-088
    # and PC-Link inventory reported exactly 9× 0x10 and 10× 0x11.
    assert DEVICE_TYPES["10"]["Model"] == "4*-082"
    assert DEVICE_TYPES["10"]["Channels"] == 2
    assert DEVICE_TYPES["10"]["VendorRef"] == "S_DB_BUSDRUKKNOP_2"
    assert DEVICE_TYPES["11"]["Model"] == "4*-084"
    assert DEVICE_TYPES["11"]["Channels"] == 4
    assert DEVICE_TYPES["11"]["VendorRef"] == "S_DB_BUSDRUKKNOP_4"

    # 0x1F / 0x23 — consumer-facing model is primary (Niko's product
    # pages use these codes); the technical wildcard reference from
    # product.mdb (``05-301-4*`` / ``05-303-4*``) sits in ModelAlt.
    assert DEVICE_TYPES["1F"]["Model"] == "05-302"
    assert DEVICE_TYPES["1F"]["ModelAlt"] == "05-301-4*"
    assert DEVICE_TYPES["1F"]["VendorRef"] == "S_DB_RF_WAND_2"

    assert DEVICE_TYPES["23"]["Model"] == "05-304"
    assert DEVICE_TYPES["23"]["ModelAlt"] == "05-303-4*"
    assert DEVICE_TYPES["23"]["VendorRef"] == "S_DB_RF_WAND_4"

    assert DEVICE_TYPES["28"]["Model"] == "05-7*5"
    assert DEVICE_TYPES["28"]["VendorRef"] == "S_DB_ACTOR_SENSOR"

    assert DEVICE_TYPES["3D"]["Model"] == "05-312"
    assert DEVICE_TYPES["3D"]["VendorRef"] == "S_DB_REMOTE4x5CH"

    assert DEVICE_TYPES["43"]["Model"] == "05-058"
    assert DEVICE_TYPES["43"]["VendorRef"] == "S_DB_INTERF_DRUKKNOP"
    assert DEVICE_TYPES["44"]["Model"] == "05-058"
    assert DEVICE_TYPES["44"]["VendorRef"] == "S_DB_INTERF_SCHAK"


# ---------------------------------------------------------------------------
# Mode tables carry vendor-ref tables of identical shape
# ---------------------------------------------------------------------------


def test_switch_mode_vendor_ref_matches_mapping_keys() -> None:
    """Every byte in SWITCH_MODE_MAPPING has a parallel VendorRef."""
    assert set(SWITCH_MODE_MAPPING.keys()) == set(SWITCH_MODE_VENDOR_REF.keys())
    for byte, ref in SWITCH_MODE_VENDOR_REF.items():
        assert ref.startswith("S_DB_DESC_SCHAKEL_M"), (
            f"switch mode 0x{byte:02X}: VendorRef={ref!r} must be a "
            f"S_DB_DESC_SCHAKEL_M* localization key"
        )


def test_roller_mode_vendor_ref_matches_mapping_keys() -> None:
    """Every byte in ROLLER_MODE_MAPPING has a parallel VendorRef."""
    assert set(ROLLER_MODE_MAPPING.keys()) == set(ROLLER_MODE_VENDOR_REF.keys())
    for byte, ref in ROLLER_MODE_VENDOR_REF.items():
        assert ref.startswith("S_DB_DESC_ROLLUIK_M"), (
            f"roller mode 0x{byte:02X}: VendorRef={ref!r} must be a "
            f"S_DB_DESC_ROLLUIK_M* localization key"
        )


def test_dimmer_mode_vendor_ref_matches_mapping_keys() -> None:
    """Every byte in DIMMER_MODE_MAPPING has a parallel VendorRef."""
    assert set(DIMMER_MODE_MAPPING.keys()) == set(DIMMER_MODE_VENDOR_REF.keys())
    for byte, ref in DIMMER_MODE_VENDOR_REF.items():
        assert ref.startswith("S_DB_DESC_DIMMER_M"), (
            f"dimmer mode 0x{byte:02X}: VendorRef={ref!r} must be a "
            f"S_DB_DESC_DIMMER_M* localization key"
        )


def test_switch_mode_m07_uses_vendor_wording_not_legacy_paren() -> None:
    """Vendor's English for M07 is 'Delayed on (up to 2h)' — not the
    legacy 'Delayed on (long up to 2h)' wording. Pin so a copy-paste
    of the legacy string can't reappear."""
    assert SWITCH_MODE_MAPPING[6] == "M07 (Delayed on (up to 2h))"
    # Pre-0.16.2 wording must not return.
    assert "long up to" not in SWITCH_MODE_MAPPING[6]


def test_switch_mode_m11_uses_vendor_short_form() -> None:
    """Niko UI: 'Delayed off (up to 50s)'. Pre-0.16.2 was the
    transliterated 'Delayed off (short up to 50sec.)'."""
    assert SWITCH_MODE_MAPPING[8] == "M11 (Delayed off (up to 50s))"
    assert "short" not in SWITCH_MODE_MAPPING[8]
    assert "sec." not in SWITCH_MODE_MAPPING[8]


def test_dimmer_mode_m13_m14_use_vendor_button_wording() -> None:
    """Niko UI: '(1 button)'. Pre-0.16.2 used the
    transliterated '(1key)'."""
    assert DIMMER_MODE_MAPPING[10] == "M13 (Dim on/off (1 button))"
    assert DIMMER_MODE_MAPPING[11] == "M14 (Dim on/off memory (1 button))"
    assert "1key" not in DIMMER_MODE_MAPPING[10]
    assert "1key" not in DIMMER_MODE_MAPPING[11]
