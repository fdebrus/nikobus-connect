"""Tests for the .nkb project-file parser (names + rooms + scenes)."""

from __future__ import annotations

import zipfile
from unittest.mock import patch

import pytest

from nikobus_connect.nkb import (
    CANONICAL_NKB_FILENAME,
    NkbData,
    find_nkb_file,
    mode_code,
    parse_nkb,
)
from nikobus_connect.nkb.parser import _extract_outputs, _fmt_addr


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_fmt_addr_module_is_4hex_button_is_6hex():
    assert _fmt_addr(3692) == "0E6C"      # 0x0E6C, 16-bit module
    assert _fmt_addr(37900) == "940C"
    assert _fmt_addr(1590196) == "1843B4"  # 24-bit button
    assert _fmt_addr(859264) == "0D1C80"


def test_mode_code_extracts_leading_m():
    assert mode_code("M12 (Preset on)") == "M12"
    assert mode_code("M04") == "M04"
    assert mode_code("MCF") is None
    assert mode_code(None) is None


# --------------------------------------------------------------------------- #
# find_nkb_file
# --------------------------------------------------------------------------- #
def test_find_canonical_preferred(tmp_path):
    (tmp_path / CANONICAL_NKB_FILENAME).write_bytes(b"x")
    (tmp_path / "other.nkb").write_bytes(b"x")
    assert find_nkb_file(str(tmp_path)).name == CANONICAL_NKB_FILENAME


def test_find_single_nkb(tmp_path):
    (tmp_path / "waterloo.nkb").write_bytes(b"x")
    assert find_nkb_file(str(tmp_path)).name == "waterloo.nkb"


def test_find_none_when_absent(tmp_path):
    assert find_nkb_file(str(tmp_path)) is None


def test_find_none_when_ambiguous(tmp_path):
    (tmp_path / "a.nkb").write_bytes(b"x")
    (tmp_path / "b.nkb").write_bytes(b"x")
    assert find_nkb_file(str(tmp_path)) is None


# --------------------------------------------------------------------------- #
# parse_nkb — addresses, rooms, scene member sets (parser stubbed)
# --------------------------------------------------------------------------- #
class _FakeParser:
    """Stands in for the vendored AccessParser. A tiny but representative
    install: a dimmer (0E6C/Centrale), a button (1843B4/Living), and a
    'Scene - Test' group whose trigger drives dimmer ch1 in M12."""

    _TABLES = {
        "Component": {
            "KeyComponent": [1, 2, 3, 4],
            "KeyLocation": [10, 11, 99, 10],
            "PhysicalAddress": [3692, 1590196, -1, 0],  # 0E6C, 1843B4, scene, skip
            "StrUserName": ["Dimcontroller", "Entree", "Scene - Test", ""],
            "Number": [1, 7, 1, 1],
        },
        "Location": {
            "KeyLocation": [10, 11, 99],
            "StrUserName": ["Centrale", "Living", "S_DB_GROUPS"],
        },
        "Objecten": {
            "KeyObject": [100, 200, 300],
            "KeyComponent": [1, 2, 3],     # output, trigger-input, group
            "KeyObjectBase": [500, 600, 700],
            "ObjectAddress": [0, 0, 0],
            "PhysicalObjectAddress": [0, 0, 0],
            "StrUserName": ["Appliques Salon", "Input", None],
        },
        "ObjectBase": {
            "KeyObjectBase": [500, 600, 700],
            # output's ObjectAddress=2 (e.g. a roller pair) but Prefix=O02 →
            # channel must come from the prefix (2), not ObjectAddress+1 (3).
            "ObjectAddress": [2, 0, 0],
            "Prefix": ["O02", "1A", "CF"],
            "StrDescription": [None, None, None],
        },
        "LinkModeBase": {
            "KeyLinkMode": [10, 13],
            "StrMode": ["M12", "MCF"],
        },
        "Connection": {
            "KeyConnection": [1, 2],
            # MCF: group(300) -> trigger(200); member: output(100) <- trigger(200)
            "KeyObjectOut": [300, 100],
            "KeyObjectIn": [200, 200],
            "KeyLinkMode": [13, 10],
            "ParamValue1": [-1, 10],
        },
    }

    def __init__(self, _path):
        pass

    def parse_table(self, name):
        return self._TABLES[name]


def _make_nkb_zip(tmp_path):
    nkb = tmp_path / "p.nkb"
    with zipfile.ZipFile(nkb, "w") as zf:
        zf.writestr("__niko__.mdb", b"dummy")
    return nkb


def _parse(tmp_path):
    nkb = _make_nkb_zip(tmp_path)
    with patch(
        "nikobus_connect.nkb._access_parser.AccessParser", _FakeParser
    ):
        return parse_nkb(nkb)


def test_parse_addresses_with_rooms(tmp_path):
    data = _parse(tmp_path)
    assert isinstance(data, NkbData)
    assert data.addresses == {
        "0E6C": ("Dimcontroller", "Centrale"),
        "1843B4": ("Entree", "Living"),
    }


def test_parse_extracts_component_numbers(tmp_path):
    """``Component.Number`` — the per-product index the Niko software
    prefixes as ``BP7`` / ``S1`` — is surfaced per address so a consumer
    can render the same index the user sees in the Nikobus application."""
    data = _parse(tmp_path)
    assert data.numbers == {"0E6C": 1, "1843B4": 7}


def test_extract_addresses_empty_name_falls_back_to_room():
    """An installer-unnamed component that IS placed in a room is kept
    (name ``""``, room set) so a consumer can display the room instead;
    a component with neither name nor room is still skipped."""
    from nikobus_connect.nkb.parser import _extract_addresses

    components = [
        {"PhysicalAddress": 3692, "StrUserName": "", "KeyLocation": 10,
         "Number": 3},
        {"PhysicalAddress": 1590196, "StrUserName": "  ", "KeyLocation": None},
        {"PhysicalAddress": -1, "StrUserName": "Scene - X", "KeyLocation": 99},
    ]
    locations = {10: "Cave", 99: "S_DB_GROUPS"}
    addresses, numbers = _extract_addresses(components, locations)
    assert addresses == {"0E6C": ("", "Cave")}
    assert numbers == {"0E6C": 3}


def test_parse_scene_member_set(tmp_path):
    data = _parse(tmp_path)
    assert len(data.scenes) == 1
    sc = data.scenes[0]
    assert sc.name == "Scene - Test"
    # trigger(200) drives output(100)=0E6C in M12; channel 2 from Prefix
    # O02 (NOT 3 from ObjectAddress+1); MCF link excluded.
    assert sc.members == frozenset({("0E6C", 2, "M12")})


def test_parse_extracts_output_channel_names(tmp_path):
    data = _parse(tmp_path)
    # output object (0E6C, Prefix O02) carries the real name "Appliques
    # Salon"; channel 2 from the prefix, not ObjectAddress.
    assert data.outputs == {("0E6C", 2): "Appliques Salon"}


def test_extract_outputs_skips_placeholders(tmp_path):
    comp_by_key = {1: {"PhysicalAddress": 3692}}
    objecten = [
        {"KeyComponent": 1, "KeyObjectBase": 5, "StrUserName": "Switch output"},
        {"KeyComponent": 1, "KeyObjectBase": 6, "StrUserName": "  "},
        {"KeyComponent": 1, "KeyObjectBase": 7, "StrUserName": "Terrasse"},
    ]
    objectbase = {
        5: {"Prefix": "O01"}, 6: {"Prefix": "O03"}, 7: {"Prefix": "O04"},
    }
    assert _extract_outputs(comp_by_key, objecten, objectbase) == {
        ("0E6C", 4): "Terrasse"
    }


def test_parse_raises_without_mdb(tmp_path):
    nkb = tmp_path / "bad.nkb"
    with zipfile.ZipFile(nkb, "w") as zf:
        zf.writestr("notes.txt", b"no mdb")
    with pytest.raises(ValueError):
        parse_nkb(nkb)


# --------------------------------------------------------------------------- #
# parse_nkb extraction hardening (crafted-.nkb defences)
# --------------------------------------------------------------------------- #
def test_parse_nkb_rejects_oversized_mdb(tmp_path, monkeypatch):
    """A decompression-bomb .mdb is rejected by the byte cap, not extracted."""
    from nikobus_connect.nkb import parser

    monkeypatch.setattr(parser, "_MAX_MDB_BYTES", 16)
    bomb = tmp_path / "bomb.nkb"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__niko__.mdb", b"A" * 64)  # 64 decompressed > 16 cap

    with pytest.raises(ValueError, match="safety limit"):
        parse_nkb(str(bomb))


def test_parse_nkb_traversal_member_name_stays_in_tmp(tmp_path):
    """An absolute / `..` .mdb member name cannot redirect the parser read
    outside the temp dir — it's written under a fixed local name."""
    evil = tmp_path / "evil.nkb"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../../../etc/passwd.mdb", b"not-a-real-mdb")

    captured = {}

    class _Sentinel(Exception):
        pass

    def _fake_parser(path):
        captured["path"] = path
        raise _Sentinel()

    with patch(
        "nikobus_connect.nkb._access_parser.AccessParser", _fake_parser
    ):
        with pytest.raises(_Sentinel):
            parse_nkb(str(evil))

    p = captured["path"]
    assert p.endswith("project.mdb")
    assert "etc/passwd" not in p
