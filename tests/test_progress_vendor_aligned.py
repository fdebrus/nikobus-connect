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


def test_dimmer_default_uses_com_aligned_band() -> None:
    """0.19.0: dimmer default scans the PC-software COM-trace bands —
    sub=00 0x20..0x3F (main link table), sub=00 0xF8..0xFF (timer
    config), sub=01 0x20..0x2F (secondary). ~56 reads vs the old
    245-read DLL plan, with parser-driven early-stop trimming further.
    Validated against module 0E6C in the 24/05/2026 capture."""
    total = _total_reads("dimmer_module")
    assert 40 <= total <= 80, total
    plan = _scan_passes_for_module_type("dimmer_module")
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00", "01"}, subs_used


def test_roller_default_uses_com_aligned_band() -> None:
    """0.19.0: roller default uses the PC-software COM-trace band —
    sub=00 0x10..0x3F, parser-driven early-stop on FF terminator.
    Validated against 9105 (stop at 0x1C) and 8394 (stop at 0x16)."""
    total = _total_reads("roller_module")
    assert 30 <= total <= 80, total
    plan = _scan_passes_for_module_type("roller_module")
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00"}, subs_used


def test_roller_broad_scan_widens_to_full_sub00_sweep() -> None:
    """``broad_scan=True`` widens the default sub=00 0x10..0x3F band
    to a full sub=00 0x00..0xFF sweep, so installs with firmware
    variants outside the PC-software-observed band can still be
    diagnosed."""
    total = _total_reads("roller_module", broad_scan=True)
    assert total == 256, total


def test_pc_logic_default_uses_com_aligned_band() -> None:
    """0.19.0: PC-Logic default uses sub=00 / sub=02 / sub=03 per the
    real PC-software COM trace (24/05/2026, module 940C) — NOT sub=04
    as the 0.17.0 DLL-derived plan assumed. The wrong-sub-byte plan
    was why 940C returned 0 decoded records on the 2026-05-23 HA trace."""
    plan = _scan_passes_for_module_type("pc_logic")
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00", "02", "03"}, subs_used
    assert "04" not in subs_used
    total = _total_reads("pc_logic")
    assert 100 <= total <= 160, total


def test_pc_logic_broad_scan_widens_to_full_sweep() -> None:
    """``broad_scan=True`` widens each pc_logic pass to a full
    0x00..0xFF sweep of its sub-byte. Sub-byte set stays the same
    as the COM-aligned default (sub=00/02/03)."""
    plan = _scan_passes_for_module_type("pc_logic", broad_scan=True)
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00", "02", "03"}, subs_used
    total = sum(len(regs) for _sub, regs in plan)
    assert total == 3 * 256, total


def test_pc_link_plan_matches_pc_software_com_trace() -> None:
    """0.18.0: PC-Link scan restored to the empirically-validated band
    captured from a real PC-software COM4 trace (24/05/2024, module
    86F5) — 97 reg-reads across sub=00, sub=01, sub=04. The 0.17.0
    DLL-derived plan (sub=00 long sweep, sub=02, sub=03) never
    touched any of the bands the PC software actually uses; on the
    2026-05-23 HA trace it returned 0 decoded records in 280 reads.
    """
    plan = _scan_passes_for_module_type("pc_link")
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00", "01", "04"}, subs_used
    # Must NOT touch the bogus DLL-section sub-bytes.
    assert "02" not in subs_used
    assert "03" not in subs_used

    sub4_regs = set()
    for sub, regs in plan:
        if sub == "04":
            sub4_regs.update(regs)
    # PC-Link module-registry band (the actual link records).
    assert {0xA3, 0xC0, 0xD3}.issubset(sub4_regs), \
        f"pc_link plan missing module-registry band sub=04 0xA3..0xD3: {sorted(sub4_regs)}"
    # Vendor-aligned status probe.
    assert {0x65, 0x69}.issubset(sub4_regs)

    total = _total_reads("pc_link")
    assert 90 <= total <= 110, total


def test_pc_link_broad_scan_widens_to_full_sweep() -> None:
    """``broad_scan=True`` widens each pc_link pass to a full
    0x00..0xFF sweep of the same sub-bytes the default uses."""
    plan = _scan_passes_for_module_type("pc_link", broad_scan=True)
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00", "01", "04"}, subs_used
    sub4_regs = set()
    for sub, regs in plan:
        if sub == "04":
            sub4_regs.update(regs)
    # Full sweep reaches 0xFF.
    assert 0xFF in sub4_regs


def test_feedback_plan_is_skipped_post_017_1() -> None:
    """0.17.1: feedback_module is in NON_OUTPUT_MODULE_TYPES (reverted
    from 0.17.0). Real-world testing showed the DLL-derived scan wastes
    ~45 min per module on ACK timeouts; feedback programming lives on
    source modules' BP cells anyway."""
    plan = _scan_passes_for_module_type("feedback_module")
    assert plan == (), (
        f"feedback should have no scan plan in 0.17.1+; got {plan}"
    )


def test_switch_default_uses_com_aligned_band() -> None:
    """0.19.0: switch default uses the PC-software COM-trace band —
    sub=00 0x10..0x3F, parser-driven early-stop on the FF terminator.
    Validated against C9A5 (stop at 0x27), 4707 (stop at 0x19), and
    5B05 (stop at 0x17) in the 24/05/2026 full-session trace."""
    plan = _scan_passes_for_module_type("switch_module")
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00"}, subs_used
    total = _total_reads("switch_module")
    assert 40 <= total <= 80, total


def test_switch_broad_scan_widens_to_full_sub00_sweep() -> None:
    """``broad_scan=True`` widens the default sub=00 0x10..0x3F band
    to a full sub=00 0x00..0xFF sweep for diagnostic use on firmware
    variants outside the PC-software-observed band."""
    plan = _scan_passes_for_module_type("switch_module", broad_scan=True)
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00"}, subs_used
    sub0_regs = set()
    for sub, regs in plan:
        if sub == "00":
            sub0_regs.update(regs)
    assert {0x00, 0x10, 0x3F, 0xFF}.issubset(sub0_regs)


def test_all_scanned_module_types_have_a_profile() -> None:
    """Every module type we scan must have a non-empty profile. Empty
    plans silently skip discovery for that family — guard against
    accidental dispatcher-table omissions.

    Note: ``feedback_module`` is intentionally NOT in this list
    (0.17.1 — see ``test_feedback_plan_is_skipped_post_017_1``).
    """
    for mt in ("switch_module", "roller_module", "dimmer_module",
               "pc_logic", "pc_link"):
        plan = _scan_passes_for_module_type(mt)
        assert plan, f"empty scan profile for {mt}"
        assert _MODULE_SCAN_PROFILES[mt] == plan
