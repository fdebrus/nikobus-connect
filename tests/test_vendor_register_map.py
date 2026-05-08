"""Pin the vendor-aligned register map against the captured trace.

Source: Niko PC software COM3 trace, 2026-05-08, against switch module
0x3D82, executing "load current installation". Decoded queries:

  $14103D820500…  reg 05 sub 00
  $14103D820600…  reg 06 sub 00
  $14103D820700…  reg 07 sub 00
  $14103D820800…  reg 08 sub 00
  $14103D820900…  reg 09 sub 00
  $14103D823E00…  reg 3E sub 00 (twice, back-to-back)
  $14103D827001 … $14103D829301   regs 70..93 sub 01 (contiguous)
  $14103D829601…  reg 96 sub 01 (skipping 94, 95)
  $14103D826504 … $14103D826904   regs 65..69 sub 04 (contiguous)
  $14103D829601…  reg 96 sub 01 (re-read, end of sequence)

The constants captured below are reference data — they document the
vendor's per-(sub, register) read sequence so future scan-tuning
work can compare ours against vendor-truth without re-decoding the
trace. They're deliberately NOT wired into the scan loop; switching
defaults without staged validation risks silent data loss.
"""

from __future__ import annotations

from nikobus_connect.discovery.discovery import (
    _VENDOR_REGISTER_MAP_BY_SUB,
    _VENDOR_REGISTER_MAP_TRACE_SOURCE,
)


def test_vendor_map_sub_00_matches_trace() -> None:
    # 6 specific regs in 0x05..0x3E, NOT a contiguous sweep. The
    # vendor reads identity / header data here, not link records.
    assert _VENDOR_REGISTER_MAP_BY_SUB["00"] == (
        0x05,
        0x06,
        0x07,
        0x08,
        0x09,
        0x3E,
    )


def test_vendor_map_sub_01_matches_trace() -> None:
    # Contiguous 0x70..0x93 (36 regs) + 0x96 — the vendor deliberately
    # skips 0x94 and 0x95. Reg 0x96 is the link-table checksum, read
    # at both start and end of the readout sequence.
    expected = tuple(range(0x70, 0x94)) + (0x96,)
    assert _VENDOR_REGISTER_MAP_BY_SUB["01"] == expected
    assert len(_VENDOR_REGISTER_MAP_BY_SUB["01"]) == 37


def test_vendor_map_sub_01_skips_0x94_and_0x95() -> None:
    # Pin the skip explicitly — the trace shows the vendor jumping
    # from 0x93 directly to 0x96. If a future trace shows 0x94/0x95
    # being read, this test will flag it for re-mapping.
    sub01 = set(_VENDOR_REGISTER_MAP_BY_SUB["01"])
    assert 0x94 not in sub01
    assert 0x95 not in sub01
    assert 0x96 in sub01


def test_vendor_map_sub_04_matches_trace() -> None:
    # Contiguous 0x65..0x69 — the vendor reads status / state regs
    # in this band, NOT the 0x00..0x3F range our current scan uses
    # for sub=04. This divergence is the most striking finding of
    # the trace and likely explains why our sub=04 / sub=00 reads
    # return identical content (we're reading the same memory under
    # two different sub-byte aliases).
    assert _VENDOR_REGISTER_MAP_BY_SUB["04"] == (
        0x65,
        0x66,
        0x67,
        0x68,
        0x69,
    )


def test_vendor_map_total_reads_per_module() -> None:
    # 6 (sub=00) + 37 (sub=01) + 5 (sub=04) = 48 reads per module.
    # Our current sweep does ~167 (64+64+39). If the totals here
    # change, the docstring on ``_VENDOR_REGISTER_MAP_BY_SUB``
    # should be updated to match.
    total = sum(len(regs) for regs in _VENDOR_REGISTER_MAP_BY_SUB.values())
    assert total == 48


def test_vendor_map_only_three_sub_bytes() -> None:
    # The trace surfaced exactly three sub-bytes (00 / 01 / 04). If
    # a future trace adds e.g. sub=02 or sub=03 reads, this test
    # will fail and force the map to be expanded with attribution.
    assert set(_VENDOR_REGISTER_MAP_BY_SUB.keys()) == {"00", "01", "04"}


def test_vendor_map_registers_within_byte_range() -> None:
    # Defensive: every register byte must fit in a single byte
    # (0x00..0xFF). Catches accidental int promotions.
    for sub_byte, regs in _VENDOR_REGISTER_MAP_BY_SUB.items():
        for reg in regs:
            assert 0x00 <= reg <= 0xFF, (
                f"sub={sub_byte} reg=0x{reg:X} out of byte range"
            )


def test_vendor_map_trace_source_attributed() -> None:
    # Pin the provenance string. If someone replaces the constants
    # with data from a different trace, this test forces them to
    # update the attribution too — which is the contract that lets
    # future readers know which install / date the data came from.
    assert "2026-05-08" in _VENDOR_REGISTER_MAP_TRACE_SOURCE
    assert "3D82" in _VENDOR_REGISTER_MAP_TRACE_SOURCE
    assert "load current installation" in _VENDOR_REGISTER_MAP_TRACE_SOURCE
