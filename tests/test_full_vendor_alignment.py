"""Full vendor-alignment tests for the 0.16.0 scan plan.

Niko's PC software (COM3 trace 2026-05-08, switch module 3D82) uses
EXACTLY this per-module read sequence:

  sub=00 → 6 regs (0x05..0x09 + 0x3E)  — module header / identity
  sub=01 → 37 regs (0x70..0x93 + 0x96) — link table
  sub=04 → 5 regs (0x65..0x69)         — status / state

Total: 48 register reads per module.

0.16.0 applies this EXACT plan to ALL output modules + PC-Logic
— full vendor alignment, no firmware-specific or install-specific
exceptions. The pre-0.16.0 sub=04 0x00..0x3F sweep is preserved as
an opt-in safety net via ``broad_scan=True``.
"""

from __future__ import annotations

from nikobus_connect.discovery.discovery import (
    _BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE,
    _BROAD_SCAN_LEGACY_REGISTERS,
    _SCAN_REGISTERS_BY_SUB,
    _SCAN_SUBS_BY_MODULE_TYPE,
    _VENDOR_REGISTER_MAP_BY_SUB,
    _VENDOR_REGISTER_MAP_TRACE_SOURCE,
    _scan_registers_for_sub,
    _scan_subs_for_module_type,
    _wire_sub_byte,
)


# ---------------------------------------------------------------------------
# Vendor register map — exact, no approximation
# ---------------------------------------------------------------------------


def test_vendor_register_map_is_pinned_per_sub_byte() -> None:
    """The 48-register-per-module vendor scan is locked in."""
    assert _VENDOR_REGISTER_MAP_BY_SUB == {
        "00": (0x05, 0x06, 0x07, 0x08, 0x09, 0x3E),
        "01": tuple(range(0x70, 0x94)) + (0x96,),
        "04": (0x65, 0x66, 0x67, 0x68, 0x69),
    }
    total = sum(len(regs) for regs in _VENDOR_REGISTER_MAP_BY_SUB.values())
    assert total == 48


def test_vendor_register_map_trace_source_is_documented() -> None:
    """Provenance string anchors the map to a specific capture."""
    assert "2026-05-08" in _VENDOR_REGISTER_MAP_TRACE_SOURCE
    assert "3D82" in _VENDOR_REGISTER_MAP_TRACE_SOURCE
    assert "PC software" in _VENDOR_REGISTER_MAP_TRACE_SOURCE


# ---------------------------------------------------------------------------
# All output modules + PC-Logic share the same plan — no exceptions
# ---------------------------------------------------------------------------


def test_switch_uses_vendor_plan() -> None:
    assert _scan_subs_for_module_type("switch_module") == ("00", "01", "04")


def test_roller_uses_vendor_plan() -> None:
    assert _scan_subs_for_module_type("roller_module") == ("00", "01", "04")


def test_dimmer_uses_vendor_plan_no_firmware_exception() -> None:
    """0.16.0 drops the pre-0.16.0 dimmer-specific full-sweep exception
    (rooted in the 2026-05-04 capture on modules 116D / 0E0A). The
    dimmer now uses the same vendor plan as every other module."""
    assert _scan_subs_for_module_type("dimmer_module") == ("00", "01", "04")


def test_pc_logic_uses_vendor_plan_no_defensive_sweep() -> None:
    """0.16.0 drops PC-Logic's pre-0.16.0 0x00..0xFF defensive sweep.
    Same vendor plan as output modules — full alignment."""
    assert _scan_subs_for_module_type("pc_logic") == ("00", "01", "04")


def test_pc_link_uses_its_own_inventory_scan() -> None:
    """PC-Link has its own scan profile — the controller's module-registry
    band (0xA3..0xFF), not the per-module vendor link-table reads."""
    assert _scan_subs_for_module_type("pc_link") == ("pc_link_inventory",)


def test_output_modules_and_pc_logic_share_same_plan() -> None:
    """Pin the invariant: switch / roller / dimmer / PC-Logic all use
    the same vendor scan plan. That's what 0.16.0 's full alignment
    means — no module-type-specific divergence."""
    plan = ("00", "01", "04")
    for module_type in ("switch_module", "roller_module", "dimmer_module", "pc_logic"):
        assert _scan_subs_for_module_type(module_type) == plan, (
            f"{module_type} drifted from the vendor plan"
        )


def test_modules_without_register_tables_get_empty_plan() -> None:
    """audio / feedback / interface / unknown — no scan plan, no reads."""
    assert _scan_subs_for_module_type("audio_module") == ()
    assert _scan_subs_for_module_type("feedback_module") == ()
    assert _scan_subs_for_module_type("interface_module") == ()
    assert _scan_subs_for_module_type("other_module") == ()
    assert _scan_subs_for_module_type(None) == ()


# ---------------------------------------------------------------------------
# Register-list lookup returns the EXACT vendor list
# ---------------------------------------------------------------------------


def test_scan_registers_for_sub_returns_exact_vendor_lists() -> None:
    """No approximation to contiguous ``range`` — the exact captured
    tuple, including the non-contiguous 0x96 in sub=01."""
    assert _scan_registers_for_sub("00") == (0x05, 0x06, 0x07, 0x08, 0x09, 0x3E)
    assert _scan_registers_for_sub("01") == tuple(range(0x70, 0x94)) + (0x96,)
    assert _scan_registers_for_sub("04") == (0x65, 0x66, 0x67, 0x68, 0x69)


def test_sub01_keeps_non_contiguous_0x96() -> None:
    """The vendor's sub=01 list deliberately skips 0x94 and 0x95 then
    includes 0x96 — pin the gap so a contiguous-range optimisation
    can't silently widen it."""
    sub01 = _scan_registers_for_sub("01")
    assert 0x93 in sub01
    assert 0x94 not in sub01
    assert 0x95 not in sub01
    assert 0x96 in sub01


def test_pc_link_inventory_scan_is_a3_to_ff() -> None:
    """PC-Link's separate scan profile remains 0xA3..0xFF."""
    assert _scan_registers_for_sub("pc_link_inventory") == tuple(
        range(0xA3, 0x100)
    )


# ---------------------------------------------------------------------------
# Wire-level sub-byte normalisation
# ---------------------------------------------------------------------------


def test_wire_sub_byte_passes_vendor_subs_through() -> None:
    """sub=00 / sub=01 / sub=04 all go on the wire as themselves."""
    assert _wire_sub_byte("00") == "00"
    assert _wire_sub_byte("01") == "01"
    assert _wire_sub_byte("04") == "04"


def test_wire_sub_byte_collapses_synthetic_tokens_to_04() -> None:
    """Plan-time tokens for distinct register lists collapse to sub=04
    on the wire — Niko's read-register command code is the same for
    both the vendor's narrow 0x65..0x69 and the legacy 0x00..0x3F."""
    assert _wire_sub_byte("04_broad") == "04"
    assert _wire_sub_byte("pc_link_inventory") == "04"


# ---------------------------------------------------------------------------
# broad_scan opt-in safety net
# ---------------------------------------------------------------------------


def test_broad_scan_adds_legacy_sub04_pass_to_switch() -> None:
    """``broad_scan=True`` appends the legacy 0x00..0x3F sweep as an
    extra pass after the vendor primary."""
    plan = _scan_subs_for_module_type("switch_module", broad_scan=True)
    assert plan == ("00", "01", "04", "04_broad")


def test_broad_scan_adds_legacy_sub04_pass_to_dimmer() -> None:
    """The same safety net applies to dimmer — and is the only path
    by which a user can restore the pre-0.16.0 dimmer full-sweep
    behaviour for a firmware revision that needs it."""
    plan = _scan_subs_for_module_type("dimmer_module", broad_scan=True)
    assert plan == ("00", "01", "04", "04_broad")


def test_broad_scan_extra_is_legacy_0x00_to_0x3f_range() -> None:
    assert _BROAD_SCAN_LEGACY_REGISTERS == tuple(range(0x00, 0x40))
    assert _SCAN_REGISTERS_BY_SUB["04_broad"] == _BROAD_SCAN_LEGACY_REGISTERS


def test_broad_scan_registered_for_every_scanned_module() -> None:
    """Every module type that has a vendor plan also has a broad-scan
    extra entry — the safety net isn't selectively available."""
    for module_type in _SCAN_SUBS_BY_MODULE_TYPE:
        if module_type == "pc_link":
            # PC-Link's scan is completely separate; broad_scan doesn't
            # apply to it.
            continue
        assert module_type in _BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE, (
            f"{module_type} has a vendor plan but no broad-scan safety net"
        )


def test_broad_scan_preserves_vendor_first_ordering() -> None:
    """The vendor passes run FIRST under ``broad_scan``; the legacy
    sweep is appended as a fallback, not promoted ahead of it."""
    plan = _scan_subs_for_module_type("switch_module", broad_scan=True)
    assert plan[:3] == ("00", "01", "04")  # vendor trio first
    assert plan[3] == "04_broad"  # legacy extra last


def test_broad_scan_is_noop_when_disabled() -> None:
    """``broad_scan=False`` (the default) yields the pure vendor plan."""
    for module_type in ("switch_module", "roller_module", "dimmer_module", "pc_logic"):
        assert _scan_subs_for_module_type(module_type, broad_scan=False) == (
            "00", "01", "04",
        )
