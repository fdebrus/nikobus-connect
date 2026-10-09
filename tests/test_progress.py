"""The discovery bar: stage weights, counters, partial-run rescaling.

The percentage cases are ported from Nikobus-HA's progress tests, which
pinned the bar's behaviour against real runs: a scan on its own opening
at 0 rather than 30, identity driven by answers rather than queued
reads, and the bar not falling back during the residue probe.
"""

from __future__ import annotations

import pytest

from nikobus_connect.discovery.progress import (
    COARSE_MODULE_SCAN,
    COARSE_PC_LINK,
    SCOPE_FULL,
    SCOPE_INVENTORY,
    SCOPE_MODULE_SCAN,
    STAGE_ERROR,
    STAGE_FINALIZING,
    STAGE_FINISHED,
    STAGE_IDENTITY,
    STAGE_IDLE,
    STAGE_INVENTORY,
    STAGE_PROBING,
    STAGE_REGISTER_SCAN,
    WEIGHT_FINALIZING,
    WEIGHT_IDENTITY,
    WEIGHT_INVENTORY,
    WEIGHT_REGISTER_SCAN,
    ProgressCounters,
    progress_percent,
)


def test_the_weights_fill_the_bar() -> None:
    assert WEIGHT_INVENTORY + WEIGHT_IDENTITY + WEIGHT_REGISTER_SCAN + WEIGHT_FINALIZING == 100


def test_resting_states() -> None:
    assert progress_percent(STAGE_IDLE, ProgressCounters()) == 0.0
    assert progress_percent(STAGE_ERROR, ProgressCounters()) == 0.0
    assert progress_percent(STAGE_FINISHED, ProgressCounters()) == 100.0


def test_inventory_tracks_frames_once_a_total_is_known() -> None:
    assert progress_percent(STAGE_INVENTORY, ProgressCounters()) == 5.0  # midpoint before a total
    assert progress_percent(STAGE_INVENTORY, ProgressCounters(registers_done=46, registers_total=92)) == 5.0
    assert progress_percent(STAGE_INVENTORY, ProgressCounters(registers_done=92, registers_total=92)) == 10.0
    assert progress_percent(STAGE_INVENTORY, ProgressCounters(registers_done=200, registers_total=92)) == 10.0


def test_identity_is_driven_by_answers_not_queued_reads() -> None:
    half = progress_percent(STAGE_IDENTITY, ProgressCounters(identity_responses=48, identity_expected=96))
    assert half == 20.0  # floor 10 + half of 20
    assert progress_percent(STAGE_IDENTITY, ProgressCounters(identity_responses=96, identity_expected=96)) == 30.0


def test_identity_falls_back_to_the_module_estimate_without_an_expected_count() -> None:
    c = ProgressCounters(modules_done=1, modules_total=4, registers_done=24, registers_total=48)
    assert progress_percent(STAGE_IDENTITY, c) == pytest.approx(10 + 20 * 1.5 / 4)


def test_register_scan_is_modules_done_plus_the_current_share() -> None:
    c = ProgressCounters(modules_done=3, modules_total=4, registers_done=24, registers_total=48)
    assert progress_percent(STAGE_REGISTER_SCAN, c) == pytest.approx(30 + 65 * 3.5 / 4, abs=0.05)
    assert progress_percent(STAGE_REGISTER_SCAN, ProgressCounters()) == 30.0


def test_finalizing_and_probing_sit_near_the_end_and_never_fall_back() -> None:
    assert progress_percent(STAGE_FINALIZING, ProgressCounters()) == 97.5
    assert progress_percent(STAGE_PROBING, ProgressCounters()) == 98.8
    assert 95.0 <= progress_percent(STAGE_PROBING, ProgressCounters(), SCOPE_MODULE_SCAN) <= 99.9


def test_a_full_run_is_never_reported_complete_before_it_finishes() -> None:
    c = ProgressCounters(modules_done=4, modules_total=4, registers_done=48, registers_total=48)
    assert progress_percent(STAGE_REGISTER_SCAN, c) == 95.0
    assert progress_percent(STAGE_FINALIZING, c) < 100.0


# --- partial runs span the whole bar ---------------------------------------


def test_a_scan_on_its_own_opens_at_zero_and_reaches_the_top() -> None:
    start = ProgressCounters(modules_done=0, modules_total=4, registers_done=0, registers_total=48)
    assert progress_percent(STAGE_REGISTER_SCAN, start, SCOPE_MODULE_SCAN) == 0.0
    end = ProgressCounters(modules_done=3, modules_total=4, registers_done=48, registers_total=48)
    assert progress_percent(STAGE_REGISTER_SCAN, end, SCOPE_MODULE_SCAN) > 90.0
    assert 95.0 <= progress_percent(STAGE_FINALIZING, end, SCOPE_MODULE_SCAN) <= 99.9


def test_an_overview_on_its_own_spans_the_bar() -> None:
    assert progress_percent(STAGE_IDENTITY, ProgressCounters(identity_responses=96, identity_expected=96), SCOPE_INVENTORY) == 99.9
    mid = progress_percent(STAGE_IDENTITY, ProgressCounters(identity_responses=48, identity_expected=96), SCOPE_INVENTORY)
    assert 60.0 <= mid <= 70.0


def test_a_full_run_keeps_the_stacked_value() -> None:
    c = ProgressCounters(modules_done=0, modules_total=4, registers_done=0, registers_total=48)
    assert progress_percent(STAGE_REGISTER_SCAN, c, SCOPE_FULL) == 30.0


def test_an_unknown_stage_uses_the_coarse_phase() -> None:
    assert progress_percent("whatever", ProgressCounters(), coarse_phase=COARSE_PC_LINK) == 10.0
    assert progress_percent("whatever", ProgressCounters(), coarse_phase=COARSE_MODULE_SCAN) == 40.0
    assert progress_percent("whatever", ProgressCounters()) == 0.0
