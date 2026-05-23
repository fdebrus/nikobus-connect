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


def test_roller_default_uses_anchored_band() -> None:
    """0.18.0: roller default scans the anchored productive band
    (sub=00 link cluster around 0x90 + master slot at 0xF0, plus
    sub=01 0x00..0x27 state mirror). ~68 reads vs the 251-read full
    profile, validated against two live rollers (9105, 8394)."""
    total = _total_reads("roller_module")
    assert 50 <= total <= 90, total


def test_roller_broad_scan_restores_full_link_table() -> None:
    """``broad_scan=True`` reaches the rest of section 1's link-table
    range, the sec 4 mirror, sec 0/3 sentinels, and the huge sec 2
    variable band."""
    total = _total_reads("roller_module", broad_scan=True)
    # Full (251) + sec 2 (~700 regs) = ~950+. Generous bounds.
    assert total >= 900, total


def test_pc_logic_plan_dispatches_dll_sections() -> None:
    """PC-Logic profile aggregates 4 sub=4 sections from Niko_05_201a.dll
    (offsets 0x42CB, 0x4268, 0x445C, 0x4E20). Total post-dedup is 133."""
    plan = _scan_passes_for_module_type("pc_logic")
    assert all(sub == "04" for sub, _regs in plan), plan
    total = _total_reads("pc_logic")
    assert 100 <= total <= 200, total


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


def test_pc_link_broad_scan_restores_dll_sections() -> None:
    """``broad_scan=True`` re-adds the (likely unused) DLL-derived
    sections plus extends the registry band to 0xFF, for installs that
    want belt-and-braces coverage."""
    plan = _scan_passes_for_module_type("pc_link", broad_scan=True)
    subs_used = {sub for sub, _regs in plan}
    # Broad scan re-introduces sub=02 and sub=03 from the DLL plan.
    assert {"00", "01", "02", "03", "04"}.issubset(subs_used)
    sub4_regs = set()
    for sub, regs in plan:
        if sub == "04":
            sub4_regs.update(regs)
    # Registry band extended to PC-Link's pre-0.16.0 ceiling.
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


def test_switch_default_uses_anchored_band() -> None:
    """0.18.0: switch default scans only the anchored productive band
    (sub=00 link cluster around 0x90, sub=04 legacy cluster around
    0x10). Validated against three switch traces; reduces ~312 → ~82
    reads per switch and eliminates the long-sweep exhaustion mode."""
    plan = _scan_passes_for_module_type("switch_module")
    subs_used = {sub for sub, _regs in plan}
    # sub=01 dropped from default — empirical overlap with sub=04
    # rendered it nearly redundant.
    assert subs_used == {"00", "04"}, subs_used
    total = _total_reads("switch_module")
    assert 60 <= total <= 100, total


def test_switch_broad_scan_restores_full_dll_profile() -> None:
    """``broad_scan=True`` restores the bands dropped from the anchored
    default — sub=01 secondary, sub=00 pre-anchor sweep, sub=04 dead
    tail and status probes — for installs that want full coverage."""
    plan = _scan_passes_for_module_type("switch_module", broad_scan=True)
    subs_used = {sub for sub, _regs in plan}
    assert subs_used == {"00", "01", "04"}, subs_used
    sub4_regs = set()
    for sub, regs in plan:
        if sub == "04":
            sub4_regs.update(regs)
    assert {0x00, 0x10, 0x20, 0x3F}.issubset(sub4_regs)


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
