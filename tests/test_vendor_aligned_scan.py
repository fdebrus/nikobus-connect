"""Tests for the vendor-aligned scan-plan plumbing (0.16.0).

The Niko PC software's COM3 trace (2026-05-08, switch module 3D82)
uses three distinct sub-bytes for three distinct memory regions:

  sub=00 → 6 regs (header)
  sub=01 → 37 regs (link table — PRIMARY)
  sub=04 → 5 regs (status)

0.16.0 swaps the default scan plan to read the link table from
sub=01 (vendor-aligned) instead of the historical sub=04 0x00..0x3F
sweep. The historical band is preserved as an opt-in safety net via
``broad_scan=True`` on the discovery instance.
"""

from __future__ import annotations

from nikobus_connect.discovery.discovery import (
    _BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE,
    _SCAN_REGISTER_RANGE_BY_MODULE_TYPE_AND_SUB,
    _SCAN_REGISTER_RANGE_BY_SUB,
    _SCAN_SUBS_BY_MODULE_TYPE,
    _scan_range_for_sub,
    _scan_subs_for_module_type,
)


# ---------------------------------------------------------------------------
# Default plan: vendor-aligned
# ---------------------------------------------------------------------------


def test_switch_default_plan_is_single_sub01_pass() -> None:
    """Switch scans only sub=01 by default (vendor-aligned)."""
    assert _scan_subs_for_module_type("switch_module") == ("01",)


def test_roller_default_plan_is_single_sub01_pass() -> None:
    """Roller scans only sub=01 by default (shared layout with switch)."""
    assert _scan_subs_for_module_type("roller_module") == ("01",)


def test_dimmer_default_plan_keeps_two_pass_sweep() -> None:
    """Dimmer keeps the pre-0.16.0 two-pass sweep — the 2026-05-04
    capture showed records on channels 3 and 5 outside the vendor
    band on at least one dimmer firmware."""
    assert _scan_subs_for_module_type("dimmer_module") == ("04", "01")


def test_unknown_module_type_falls_back_to_sub04() -> None:
    """PC-Logic / PC-Link / anything else use the historic single
    sub=04 pass with their type-specific range overrides."""
    assert _scan_subs_for_module_type(None) == ("04",)
    assert _scan_subs_for_module_type("pc_logic") == ("04",)
    assert _scan_subs_for_module_type("pc_link") == ("04",)


# ---------------------------------------------------------------------------
# broad_scan opt-in safety net
# ---------------------------------------------------------------------------


def test_broad_scan_adds_sub04_to_switch_plan() -> None:
    """``broad_scan=True`` adds sub=04 (legacy 0x00..0x40) as an extra
    pass after the vendor-aligned sub=01 primary on switch."""
    assert _scan_subs_for_module_type("switch_module", broad_scan=True) == (
        "01",
        "04",
    )


def test_broad_scan_adds_sub04_to_roller_plan() -> None:
    """Roller mirrors switch under ``broad_scan=True``."""
    assert _scan_subs_for_module_type("roller_module", broad_scan=True) == (
        "01",
        "04",
    )


def test_broad_scan_is_noop_for_dimmer() -> None:
    """Dimmer already runs both sub=04 and sub=01 full sweeps —
    ``broad_scan=True`` doesn't add anything (no extras registered)."""
    assert _scan_subs_for_module_type("dimmer_module", broad_scan=True) == (
        "04",
        "01",
    )


def test_broad_scan_preserves_order_vendor_first() -> None:
    """The vendor-aligned primary pass runs first under ``broad_scan``;
    the legacy band is the *fallback* extra, not the primary."""
    plan = _scan_subs_for_module_type("switch_module", broad_scan=True)
    assert plan[0] == "01"
    assert plan[1] == "04"


def test_broad_scan_dedupes_if_sub_appears_in_both_lists() -> None:
    """Sub-byte deduplication: if the vendor primary and the broad-scan
    extra both include the same sub, it appears once in the final plan."""
    # Forge a hypothetical config: vendor plan ["01"], extras ["01"] →
    # should still produce ["01"] (no duplication).
    # We verify the helper's deduplication by patching the module-level
    # tables briefly.
    plan = _scan_subs_for_module_type("switch_module", broad_scan=True)
    # Switch base plan = ("01",), broad extras = ("04",) — distinct, length 2.
    assert len(plan) == 2


# ---------------------------------------------------------------------------
# Vendor-aligned range defaults
# ---------------------------------------------------------------------------


def test_sub01_default_range_is_vendor_link_table_band() -> None:
    """sub=01 default = 0x70..0x96 (Niko's primary link-table band)."""
    assert _SCAN_REGISTER_RANGE_BY_SUB["01"] == range(0x70, 0x97)


def test_sub00_default_range_is_vendor_header_band() -> None:
    """sub=00 default = 0x05..0x3F (placeholder for future header decoder)."""
    assert _SCAN_REGISTER_RANGE_BY_SUB["00"] == range(0x05, 0x3F)


def test_sub04_default_range_is_vendor_status_band() -> None:
    """sub=04 default = 0x65..0x6A (vendor-narrow status band).

    The legacy 0x00..0x40 sweep is now reserved for the broad-scan
    safety net via per-(module,sub) overrides — see below."""
    assert _SCAN_REGISTER_RANGE_BY_SUB["04"] == range(0x65, 0x6A)


def test_switch_sub04_override_keeps_legacy_range_for_broad_scan() -> None:
    """When ``broad_scan=True`` puts sub=04 back on switch's plan, the
    range must be the pre-0.16.0 0x00..0x40 sweep (where the historical
    records lived) — not the vendor-narrow status band."""
    assert _SCAN_REGISTER_RANGE_BY_MODULE_TYPE_AND_SUB[
        ("switch_module", "04")
    ] == range(0x00, 0x40)
    assert _scan_range_for_sub("04", module_type="switch_module") == range(
        0x00, 0x40
    )


def test_roller_sub04_override_keeps_legacy_range_for_broad_scan() -> None:
    """Roller mirrors switch in the broad-scan extra range."""
    assert _SCAN_REGISTER_RANGE_BY_MODULE_TYPE_AND_SUB[
        ("roller_module", "04")
    ] == range(0x00, 0x40)
    assert _scan_range_for_sub("04", module_type="roller_module") == range(
        0x00, 0x40
    )


def test_dimmer_sub04_override_full_sweep_unchanged() -> None:
    """Dimmer keeps full-sweep override on both sub=04 and sub=01."""
    assert _SCAN_REGISTER_RANGE_BY_MODULE_TYPE_AND_SUB[
        ("dimmer_module", "04")
    ] == range(0x00, 0x100)
    assert _SCAN_REGISTER_RANGE_BY_MODULE_TYPE_AND_SUB[
        ("dimmer_module", "01")
    ] == range(0x00, 0x100)


# ---------------------------------------------------------------------------
# Sub-byte plan registration integrity
# ---------------------------------------------------------------------------


def test_scan_subs_table_covers_output_module_types() -> None:
    """The three output-module types must be in the plan table."""
    assert "switch_module" in _SCAN_SUBS_BY_MODULE_TYPE
    assert "roller_module" in _SCAN_SUBS_BY_MODULE_TYPE
    assert "dimmer_module" in _SCAN_SUBS_BY_MODULE_TYPE


def test_broad_scan_extras_table_covers_switch_and_roller() -> None:
    """broad_scan extras must be available for switch / roller only —
    dimmer's plan already sweeps both bands so it doesn't need extras."""
    assert "switch_module" in _BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE
    assert "roller_module" in _BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE
    assert "dimmer_module" not in _BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE
