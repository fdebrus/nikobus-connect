"""Progress-tracking tests for the 0.16.x vendor-aligned scan plan.

The vendor scan plan reads NON-CONTIGUOUS registers across MULTIPLE
passes per module (sub=00 6 regs, sub=01 37 regs, sub=04 5 regs = 48
total for an output module). Pre-0.16.1 the progress tracker assumed:

  - registers start at 0x10
  - one pass per module
  - ``register_total`` = the length of that pass

None of those held up under the new plan. 0.16.1 adds:

  - ``registers_sent`` — cumulative count across all passes for the
    current module (resets to 0 on each new module)
  - ``register_total`` — now the CUMULATIVE target (e.g. 48 for the
    3-pass vendor plan), not per-pass
  - ``pass_index`` / ``pass_total`` — 1-based pass position within the plan
  - ``sub_byte`` — the wire sub-byte of the current pass

These tests pin those new fields against the actual scan plan.
"""

from __future__ import annotations

from nikobus_connect.discovery.base import DiscoveryProgress
from nikobus_connect.discovery.discovery import (
    _scan_registers_for_sub,
    _scan_subs_for_module_type,
)


# ---------------------------------------------------------------------------
# DiscoveryProgress shape
# ---------------------------------------------------------------------------


def test_discovery_progress_carries_new_vendor_fields() -> None:
    """The dataclass exposes the four new fields with sensible defaults."""
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
        register_total=48,
        registers_sent=7,
        pass_index=2,
        pass_total=3,
        sub_byte="01",
        decoded_records=4,
    )
    assert p.register_total == 48
    assert p.registers_sent == 7
    assert p.pass_index == 2
    assert p.pass_total == 3
    assert p.sub_byte == "01"


# ---------------------------------------------------------------------------
# Plan-level register-target computation
# ---------------------------------------------------------------------------


def test_vendor_plan_target_for_switch_is_48_registers() -> None:
    """Switch module's 3-pass vendor plan totals 48 reads."""
    plan = _scan_subs_for_module_type("switch_module")
    total = sum(len(_scan_registers_for_sub(sub)) for sub in plan)
    assert total == 48


def test_vendor_plan_target_for_dimmer_is_48_registers() -> None:
    """Dimmer (post-0.16.0 vendor alignment) — same 48 as switch."""
    plan = _scan_subs_for_module_type("dimmer_module")
    total = sum(len(_scan_registers_for_sub(sub)) for sub in plan)
    assert total == 48


def test_vendor_plan_target_for_pc_logic_is_48_registers() -> None:
    """PC-Logic shares the vendor plan as of 0.16.0."""
    plan = _scan_subs_for_module_type("pc_logic")
    total = sum(len(_scan_registers_for_sub(sub)) for sub in plan)
    assert total == 48


def test_pc_link_plan_target_is_93_registers() -> None:
    """PC-Link's inventory band is 0xA3..0xFF — 93 registers."""
    plan = _scan_subs_for_module_type("pc_link")
    total = sum(len(_scan_registers_for_sub(sub)) for sub in plan)
    assert total == 93


def test_broad_scan_adds_64_registers_to_output_modules() -> None:
    """``broad_scan=True`` adds the legacy sub=04 0x00..0x3F sweep
    (64 regs) as an extra pass — total goes from 48 to 112."""
    plan = _scan_subs_for_module_type("switch_module", broad_scan=True)
    total = sum(len(_scan_registers_for_sub(sub)) for sub in plan)
    assert total == 48 + 64
    assert total == 112


# ---------------------------------------------------------------------------
# Typical-install math
# ---------------------------------------------------------------------------


def test_typical_install_total_register_target() -> None:
    """Pin the headline number from the changelog: typical install with
    1×PC-Link + 1×PC-Logic + 3×switch + 1×dimmer reads 333 registers
    per discovery sweep on the vendor plan."""
    def plan_total(module_type: str) -> int:
        return sum(
            len(_scan_registers_for_sub(sub))
            for sub in _scan_subs_for_module_type(module_type)
        )

    pc_link = plan_total("pc_link")
    pc_logic = plan_total("pc_logic")
    switch = plan_total("switch_module")
    dimmer = plan_total("dimmer_module")
    total = pc_link + pc_logic + 3 * switch + dimmer
    assert total == 93 + 48 + 3 * 48 + 48
    assert total == 333
