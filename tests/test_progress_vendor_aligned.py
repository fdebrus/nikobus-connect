"""Progress-tracking tests for the 0.17.0 per-product scan plan.

The 0.17.0 scan plan reads NON-CONTIGUOUS registers across MULTIPLE
passes per module, with PER-PRODUCT register lists derived from each
module's product DLL (``Niko_05_XXX.dll`` GetDLLReadInfo export).

The progress tracker contract (since 0.16.1):

  - ``registers_sent`` — cumulative count across all passes for the
    current module (resets to 0 on each new module)
  - ``register_total`` — the CUMULATIVE target across all passes for
    the current module
  - ``pass_index`` / ``pass_total`` — 1-based pass position within the plan
  - ``sub_byte`` — the wire sub-byte of the current pass

These tests pin the fields and pin the per-product plan sizes against
the DLL-derived profiles.
"""

from __future__ import annotations

from nikobus_connect.discovery.base import DiscoveryProgress
from nikobus_connect.discovery.discovery import (
    _MODULE_SCAN_PROFILES,
    _scan_passes_for_module_type,
)


# ---------------------------------------------------------------------------
# DiscoveryProgress shape
# ---------------------------------------------------------------------------


def test_discovery_progress_carries_new_vendor_fields() -> None:
    """The dataclass exposes the four progress-tracking fields with
    sensible defaults."""
    p = DiscoveryProgress(phase="register_scan")
    assert p.registers_sent == 0
    assert p.pass_index == 0
    assert p.pass_total == 0
    assert p.sub_byte is None


def test_discovery_progress_accepts_full_payload() -> None:
    """All progress fields can be constructed positionally."""
    p = DiscoveryProgress(
        phase="register_scan",
        module_address="3D82",
        module_index=2,
        module_total=9,
        register=0x70,
        register_total=248,
        registers_sent=7,
        pass_index=2,
        pass_total=8,
        sub_byte="01",
        decoded_records=4,
    )
    assert p.register_total == 248
    assert p.registers_sent == 7
    assert p.pass_index == 2
    assert p.pass_total == 8
    assert p.sub_byte == "01"


# ---------------------------------------------------------------------------
# Per-product plan totals (pinned against DLL-derived profiles)
# ---------------------------------------------------------------------------


def _total_reads(module_type: str, *, broad_scan: bool = False) -> int:
    return sum(len(regs) for _sub, regs in _scan_passes_for_module_type(
        module_type, broad_scan=broad_scan,
    ))


def test_dimmer_plan_includes_link_table_band() -> None:
    """Dimmer scan covers the variable section 3 link table (default
    0xC85 bytes from offset 0x3E3 ≈ 201 register reads at sub=0/1).
    This is the band the original 48-register vendor trace SKIPPED — it
    must be present in 0.17.0."""
    total = _total_reads("dimmer_module")
    # Header (4+1) + config (1) + link table (193 at sub=0 + 7 at sub=1) +
    # secondary (36+1) + status (5). Allow a small tolerance for dedup.
    assert 240 <= total <= 260, total


def test_roller_plan_includes_link_table_band() -> None:
    """Roller's primary link-table section (offset 0x3E8, default 0xE90
    bytes ≈ 234 reads) must be present."""
    total = _total_reads("roller_module")
    assert 240 <= total <= 260, total


def test_pc_logic_plan_dispatches_dll_sections() -> None:
    """PC-Logic profile aggregates 4 sub=4 sections from Niko_05_201a.dll
    (offsets 0x42CB, 0x4268, 0x445C, 0x4E20). Total post-dedup is 133."""
    plan = _scan_passes_for_module_type("pc_logic")
    assert all(sub == "04" for sub, _regs in plan), plan
    total = _total_reads("pc_logic")
    assert 100 <= total <= 200, total


def test_pc_link_plan_uses_dll_sections_not_legacy_band() -> None:
    """0.17.0: PC-Link scan replaced the empirical sub=4 0xA3..0xFF
    sweep with the DLL-derived sections (sub=0 link table, sub=2/3
    banks). Verify we no longer scan sub=4 0xA3..0xFF."""
    plan = _scan_passes_for_module_type("pc_link")
    subs_used = {sub for sub, _regs in plan}
    assert "04" not in subs_used, plan
    # Should include sub=0 (link table band) and sub=2/3 (DLL bands).
    assert "00" in subs_used
    assert "02" in subs_used
    assert "03" in subs_used


def test_feedback_plan_dispatches_dll_sections() -> None:
    """Feedback (Niko_05_207) is now scanned (was skipped pre-0.17.0).
    Profile covers sub=4 0x00..0xFF + sub=5 0x00..0xFF + sub=6/7 bands."""
    plan = _scan_passes_for_module_type("feedback_module")
    assert plan, "feedback profile missing"
    subs_used = {sub for sub, _regs in plan}
    assert "04" in subs_used
    assert "06" in subs_used


def test_switch_plan_includes_legacy_safety_net() -> None:
    """The switch DLL (Niko_05_000_01) returns a magic tuple rather than
    a section list. Pending dynamic decode of the EXE-side dispatch, we
    apply the dimmer/roller pattern (sub=0 link table) PLUS the
    pre-0.16.0 sub=4 0x00..0x3F band as a proven safety net."""
    plan = _scan_passes_for_module_type("switch_module")
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00", "01", "04"}, subs_used
    # Verify the legacy band is present at sub=4
    sub4_regs = set()
    for sub, regs in plan:
        if sub == "04":
            sub4_regs.update(regs)
    assert {0x00, 0x10, 0x20, 0x3F}.issubset(sub4_regs)


def test_all_output_module_types_have_a_profile() -> None:
    """Every module type we discover must have a non-empty scan profile.
    Empty plans silently skip discovery for that family — guard against
    accidental dispatcher-table omissions."""
    for mt in ("switch_module", "roller_module", "dimmer_module",
               "pc_logic", "pc_link", "feedback_module"):
        plan = _scan_passes_for_module_type(mt)
        assert plan, f"empty scan profile for {mt}"
        assert _MODULE_SCAN_PROFILES[mt] == plan
