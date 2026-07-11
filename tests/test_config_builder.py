"""Tests for building HA manual-config payloads from a ``.nkb``."""

from __future__ import annotations

import zipfile
from unittest.mock import patch

from nikobus_connect.nkb import build_config, build_config_from_nkb
from nikobus_connect.nkb.config_builder import NkbConfig


# --------------------------------------------------------------------------- #
# build_config — pure assembly from raw rows
# --------------------------------------------------------------------------- #
def _productbase():
    return [
        {"KeyProductBase": 1, "NikoRefNr": "05-000-02"},   # switch 12ch
        {"KeyProductBase": 2, "NikoRefNr": "05-001-02"},   # roller 6ch
        {"KeyProductBase": 3, "NikoRefNr": "05-008-02"},   # dimmer 4ch
        {"KeyProductBase": 4, "NikoRefNr": "05-064"},      # 4-button
        {"KeyProductBase": 9, "NikoRefNr": "05-201"},      # PC-Logic (skip)
    ]


def _components():
    return [
        {"KeyComponent": 1, "KeyProductBase": 1, "PhysicalAddress": 0xC966,
         "StrUserName": "Switch S1"},
        {"KeyComponent": 2, "KeyProductBase": 2, "PhysicalAddress": 0x9220,
         "StrUserName": "Roller R1"},
        {"KeyComponent": 3, "KeyProductBase": 3, "PhysicalAddress": 0xCABA,
         "StrUserName": "Dimmer D1"},
        {"KeyComponent": 4, "KeyProductBase": 4, "PhysicalAddress": 0x1CB502,
         "StrUserName": "4BP - Kitchen"},
        # A 24-bit input with no name → default "Button …"
        {"KeyComponent": 5, "KeyProductBase": 4, "PhysicalAddress": 0x1CB4F8,
         "StrUserName": ""},
        # scene / skip rows
        {"KeyComponent": 6, "KeyProductBase": 9, "PhysicalAddress": -1,
         "StrUserName": "Scene - X"},
    ]


def _objecten():
    # channel names for the switch (comp 1): ch1 "Hal", ch2 placeholder
    return [
        {"KeyObject": 10, "KeyComponent": 1, "KeyObjectBase": 100,
         "StrUserName": "Hal"},
        {"KeyObject": 11, "KeyComponent": 1, "KeyObjectBase": 101,
         "StrUserName": "Schakeluitgang"},  # placeholder → "Output 2"
    ]


def _objectbase():
    return {
        100: {"KeyObjectBase": 100, "Prefix": "O01"},
        101: {"KeyObjectBase": 101, "Prefix": "O02"},
    }


def test_build_config_modules_and_channels():
    cfg = build_config(_components(), _productbase(), _objecten(), _objectbase())
    assert isinstance(cfg, NkbConfig)
    mc = cfg.module_config
    assert [(m["address"], m["model"], len(m["channels"]))
            for m in mc["switch_module"]] == [("C966", "05-000-02", 12)]
    assert [(m["address"], len(m["channels"])) for m in mc["dimmer_module"]] \
        == [("CABA", 4)]
    assert [(m["address"], len(m["channels"])) for m in mc["roller_module"]] \
        == [("9220", 6)]

    sw = mc["switch_module"][0]["channels"]
    assert sw[0] == {"description": "Hal"}          # real name
    assert sw[1] == {"description": "Output 2"}      # placeholder replaced
    assert "led_on" not in sw[0] and "led_off" not in sw[0]


def test_build_config_roller_has_operation_time():
    cfg = build_config(_components(), _productbase(), _objecten(), _objectbase())
    roller_ch = cfg.module_config["roller_module"][0]["channels"][0]
    assert roller_ch["operation_time"] == "40"


def test_build_config_buttons_named_and_sorted():
    cfg = build_config(_components(), _productbase(), _objecten(), _objectbase())
    btns = cfg.button_config["nikobus_button"]
    assert [b["address"] for b in btns] == ["1CB4F8", "1CB502"]  # sorted
    assert btns[1] == {"address": "1CB502", "description": "4BP - Kitchen"}
    # unnamed input gets a default description
    assert btns[0] == {"address": "1CB4F8", "description": "Button 1CB4F8"}


def test_build_config_multi_key_button():
    """A 4-button plate (single-key faces A/B/C/D in the .nkb) becomes four
    entries — one per key — that group onto one physical button with
    ``channels=4`` op-points, so the scan can route each key's links."""
    comps = [
        {"KeyComponent": 1, "KeyProductBase": 4, "PhysicalAddress": 0x1CB502,
         "StrUserName": "4BP - Kitchen"},
    ]
    pb = [{"KeyProductBase": 4, "NikoRefNr": "05-064"}]
    # button faces A/B/C/D plus a combo (AB) that must be ignored
    objecten = [
        {"KeyComponent": 1, "KeyObjectBase": k} for k in (10, 11, 12, 13, 14)
    ]
    objectbase = {
        10: {"KeyObjectBase": 10, "Prefix": "A"},
        11: {"KeyObjectBase": 11, "Prefix": "B"},
        12: {"KeyObjectBase": 12, "Prefix": "C"},
        13: {"KeyObjectBase": 13, "Prefix": "D"},
        14: {"KeyObjectBase": 14, "Prefix": "AB"},   # combo — ignored
    }
    cfg = build_config(comps, pb, objecten, objectbase)
    btns = cfg.button_config["nikobus_button"]
    assert len(btns) == 4
    # each entry carries a linked_button with physical + key + channels=4
    keys = sorted(b["linked_button"][0]["key"] for b in btns)
    assert keys == ["1A", "1B", "1C", "1D"]
    for b in btns:
        lb = b["linked_button"][0]
        assert lb["address"] == "1CB502" and lb["channels"] == 4
        assert b["address"] != ""  # per-key bus address
    # no two faces share an address
    assert len({b["address"] for b in btns}) == 4
    # per-key bus addresses match the library's inventory derivation
    # (convert_nikobus_address('1CB502') = '102B4E', + first-nibble offset):
    by_key = {b["linked_button"][0]["key"]: b["address"] for b in btns}
    assert by_key == {
        "1A": "902B4E", "1B": "D02B4E", "1C": "102B4E", "1D": "502B4E",
    }


def test_build_config_two_button_plate():
    comps = [{"KeyComponent": 1, "KeyProductBase": 5, "PhysicalAddress": 0x1CB68C,
              "StrUserName": "2BP - Room"}]
    pb = [{"KeyProductBase": 5, "NikoRefNr": "05-060"}]
    objecten = [{"KeyComponent": 1, "KeyObjectBase": k} for k in (10, 11)]
    objectbase = {10: {"KeyObjectBase": 10, "Prefix": "A"},
                  11: {"KeyObjectBase": 11, "Prefix": "B"}}
    cfg = build_config(comps, pb, objecten, objectbase)
    btns = cfg.button_config["nikobus_button"]
    assert sorted(b["linked_button"][0]["key"] for b in btns) == ["1A", "1B"]
    assert all(b["linked_button"][0]["channels"] == 2 for b in btns)


def test_build_config_single_face_stays_simple():
    """A device with no key faces (or only ``1A``) stays a simple
    address-only entry (the loader makes a 1-channel button)."""
    comps = [{"KeyComponent": 1, "KeyProductBase": 9, "PhysicalAddress": 0x134C67,
              "StrUserName": "Motion sensor"}]
    pb = [{"KeyProductBase": 9, "NikoRefNr": "430-00502"}]
    cfg = build_config(comps, pb, [], {})
    btns = cfg.button_config["nikobus_button"]
    assert btns == [{"address": "134C67", "description": "Motion sensor"}]


def test_build_config_skips_unknown_module_model():
    """A module whose product isn't a supported switch/dimmer/roller (e.g. a
    colour-plinth controller) is skipped, not mis-categorised."""
    comps = [
        {"KeyComponent": 1, "KeyProductBase": 69, "PhysicalAddress": 0x8267,
         "StrUserName": "Kleuren Plint"},
    ]
    pb = [{"KeyProductBase": 69, "NikoRefNr": "340-00111"}]
    cfg = build_config(comps, pb, [], {})
    assert cfg.module_config == {
        "switch_module": [], "dimmer_module": [], "roller_module": []
    }
    assert cfg.button_config["nikobus_button"] == []


# --------------------------------------------------------------------------- #
# build_config_from_nkb — end-to-end through the zip/mdb reader
# --------------------------------------------------------------------------- #
class _FakeParser:
    _TABLES = {
        "Component": {
            "KeyComponent": [1, 4],
            "KeyProductBase": [1, 4],
            "PhysicalAddress": [0xC966, 0x1CB502],
            "StrUserName": ["Switch S1", "4BP - Kitchen"],
        },
        "ProductBase": {
            "KeyProductBase": [1, 4],
            "NikoRefNr": ["05-000-02", "05-064"],
        },
        "Objecten": {
            "KeyObject": [10],
            "KeyComponent": [1],
            "KeyObjectBase": [100],
            "StrUserName": ["Hal"],
        },
        "ObjectBase": {
            "KeyObjectBase": [100],
            "Prefix": ["O01"],
        },
    }

    def __init__(self, _path):
        pass

    def parse_table(self, name):
        return self._TABLES[name]


def test_build_config_from_nkb_end_to_end(tmp_path):
    nkb = tmp_path / "p.nkb"
    with zipfile.ZipFile(nkb, "w") as zf:
        zf.writestr("__niko__.mdb", b"dummy")
    with patch(
        "nikobus_connect.nkb._access_parser.AccessParser", _FakeParser
    ):
        cfg = build_config_from_nkb(nkb)
    assert cfg.module_config["switch_module"][0]["address"] == "C966"
    assert cfg.module_config["switch_module"][0]["channels"][0]["description"] == "Hal"
    assert cfg.button_config["nikobus_button"] == [
        {"address": "1CB502", "description": "4BP - Kitchen"}
    ]
