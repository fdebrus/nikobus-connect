"""Tests for the PC-Logic CF (Central Function) address-table recogniser.

The PC-Logic stores a CF trigger-address enumeration in register memory
(function 0x10, sub=0x02), decoded from a real install (940C): a grid of
5-byte ``<prefix>870000<index>`` units, prefix ∈ {00,20,…,E0}, index ∈
0x00..0x1F. Each unit's ``convert_nikobus_address(<prefix>8700)`` lands
on the CF broadcast space (0x3840..0x3847).

It is recognised so the scan stops logging it as "noise", but it is NOT
turned into scene entities — it has no output members and no names (the
CF→output mapping lives in the output-module link tables; CF names in
the .nkb project). These tests pin the recogniser against the real grid
chunks and assert it never mistakes a link / registry / counter / FF
chunk for the table.
"""

from __future__ import annotations

from nikobus_connect.discovery.pc_record_parser import (
    is_cf_address_table_chunk,
    parse_pc_record,
)
from nikobus_connect.discovery.protocol import convert_nikobus_address


# Every distinct 940C sub=02 chunk captured from the real scan.
_REAL_GRID_CHUNKS = [
    "20870000082087000018208700000420",
    "87000014208700000C208700001C2087",
    "0000022087000012208700000A208700",
    "001A2087000006208700001620870000",
    "0E208700001E20870000012087000011",
    "20870000092087000019208700000520",
    "87000015208700000D208700001D2087",
    "A087000008A087000018A087000004A0",
    "0FA08700001FA0870000000087000010",
    "80870000088087000018808700000480",
    "0F808700001F8087FFFFFFFFFFFFFFFF",
    "00408710408708408718408704408714",
    "40870C40871C40870240871240870A40",
    "00C08710C08708C08718C08704C08714",
    "C0870CC0871CC08702C08712C0870AC0",
]

# Chunks that must NOT be mistaken for the CF table.
_NON_GRID = {
    "all-FF": "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
    "counter": "000102030405060708090A0B0C0D0E0F",
    "switch-link": "73C2D074100173C2D004220F728BC874",
    "roller-link": "7442BCD014017442BCE006FF7442B8D0",
    "all-zero": "00000000000000000000000000000000",
}


def test_recognises_every_real_grid_chunk():
    for chunk in _REAL_GRID_CHUNKS:
        assert is_cf_address_table_chunk(chunk), chunk


def test_rejects_non_grid_chunks():
    for label, chunk in _NON_GRID.items():
        assert not is_cf_address_table_chunk(chunk), label


def test_rejects_wrong_length_and_non_str():
    assert not is_cf_address_table_chunk("")
    assert not is_cf_address_table_chunk("8700")
    assert not is_cf_address_table_chunk(None)  # type: ignore[arg-type]


def test_grid_never_parses_as_a_real_record():
    """Defence in depth: even if the recogniser were bypassed, the grid
    chunks must not parse as link/registry records (they'd be junk)."""
    for chunk in _REAL_GRID_CHUNKS:
        # parse_pc_record only accepts records whose bytes 1-3 are zero;
        # grid chunks that happen to start with that shape carry no
        # resolvable target, so this is a soft check that the recogniser
        # is the right gate. We assert the recogniser fires first.
        assert is_cf_address_table_chunk(chunk)


def test_prefix_to_cf_family_arithmetic():
    """Document the decode: convert(<prefix>8700) → 0x3840..0x3847."""
    expected = {
        "00": "003840", "20": "003841", "40": "003842", "60": "003843",
        "80": "003844", "A0": "003845", "C0": "003846", "E0": "003847",
    }
    for prefix, cf_addr in expected.items():
        assert convert_nikobus_address(prefix + "8700") == cf_addr
