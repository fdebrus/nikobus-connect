"""How far a discovery run has got, as one number.

Discovery is a pipeline of stages — the PC-Link inventory, the identity
reads of every module, the register scan of each module's link table,
the finalizing merge, and the residue probe that follows — and a host
shows it as a single bar. The arithmetic that turns the stage and its
counters into a percentage is here, with the weights of the stages
and the rescaling for a run that covers only part of the pipeline, so
every host draws the same bar.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import PHASE_FINALIZING, PHASE_IDENTITY, PHASE_INVENTORY, PHASE_REGISTER_SCAN

#: The stages a run passes through, in order, plus the resting states.
#: The four in the middle are the library's own ``PHASE_*`` names.
STAGE_IDLE = "idle"
STAGE_INVENTORY = PHASE_INVENTORY
STAGE_IDENTITY = PHASE_IDENTITY
STAGE_REGISTER_SCAN = PHASE_REGISTER_SCAN
STAGE_FINALIZING = PHASE_FINALIZING
#: The residue probe after finalizing: every module asked whether it
#: is still on the bus, so stale records can be evicted.
STAGE_PROBING = "probing"
STAGE_FINISHED = "finished"
STAGE_ERROR = "error"

#: Share of the bar each stage takes in a full run. They sum to 100.
WEIGHT_INVENTORY = 10
WEIGHT_IDENTITY = 20
WEIGHT_REGISTER_SCAN = 65
WEIGHT_FINALIZING = 5

#: What a run covers: the whole pipeline, the inventory and identity
#: stages only (a project overview), or the register scan only (an
#: existing installation). A partial run is rescaled to span the bar.
SCOPE_FULL = "full"
SCOPE_INVENTORY = "inventory"
SCOPE_MODULE_SCAN = "module_scan"

#: The coarse two-step view some hosts keep beside the stages; used
#: only as a fallback when no stage is known.
COARSE_PC_LINK = "pc_link"
COARSE_MODULE_SCAN = "module_scan"

_OVERVIEW_SPAN = WEIGHT_INVENTORY + WEIGHT_IDENTITY
_SCAN_FLOOR = WEIGHT_INVENTORY + WEIGHT_IDENTITY
_FINAL_FLOOR = WEIGHT_INVENTORY + WEIGHT_IDENTITY + WEIGHT_REGISTER_SCAN


@dataclass(frozen=True)
class ProgressCounters:
    """What a host has counted so far.

    ``registers_done`` / ``registers_total`` are, during the inventory,
    the PC-Link frames received and expected; during a module scan,
    the registers sent and planned for the current module.
    ``modules_done`` / ``modules_total`` count modules in the scan
    queue. ``identity_responses`` / ``identity_expected`` count the
    ``$2E`` answers of the identity stage — the answers, not the queued
    reads, which race ahead in under a second.
    """

    registers_done: int = 0
    registers_total: int = 0
    modules_done: int = 0
    modules_total: int = 0
    identity_responses: int = 0
    identity_expected: int = 0


def _fraction(done: int, total: int) -> float:
    return min(1.0, done / total) if total else 0.0


def _module_fraction(c: ProgressCounters) -> float:
    """Modules finished plus the share of the one in progress."""
    total = c.modules_total or 1
    return min(1.0, (c.modules_done + _fraction(c.registers_done, c.registers_total)) / total)


def progress_percent(
    stage: str,
    counters: ProgressCounters,
    scope: str = SCOPE_FULL,
    *,
    coarse_phase: str | None = None,
) -> float:
    """The bar's value, 0 to 99.9 while running, 100 when finished.

    Stages stack by weight; within a stage the counters give the
    fraction. The inventory tracks frames once a total is known and
    sits at its midpoint before that; identity is response-driven, and
    falls back to the module estimate for a library that does not say
    how many answers to expect; the register scan is modules done plus
    the current one's share; finalizing sits at its midpoint and the
    probe after it at three quarters, so the bar never falls back
    during the seconds the probe takes. A partial run is rescaled so an
    overview or a scan on its own reads 0 to 100. The value is capped
    at 99.9 until the run is finished, and rounded to a tenth so the
    bar is seen to move within a module.
    """
    if stage in (STAGE_IDLE, STAGE_ERROR):
        return 0.0
    if stage == STAGE_FINISHED:
        return 100.0

    c = counters
    if stage == STAGE_INVENTORY:
        floor, weight = 0, WEIGHT_INVENTORY
        frac = _fraction(c.registers_done, c.registers_total) if c.registers_total else 0.5
    elif stage == STAGE_IDENTITY:
        floor, weight = WEIGHT_INVENTORY, WEIGHT_IDENTITY
        frac = (
            _fraction(c.identity_responses, c.identity_expected)
            if c.identity_expected
            else _module_fraction(c)
        )
    elif stage == STAGE_REGISTER_SCAN:
        floor, weight = _SCAN_FLOOR, WEIGHT_REGISTER_SCAN
        frac = _module_fraction(c)
    elif stage == STAGE_FINALIZING:
        floor, weight, frac = _FINAL_FLOOR, WEIGHT_FINALIZING, 0.5
    elif stage == STAGE_PROBING:
        floor, weight, frac = _FINAL_FLOOR, WEIGHT_FINALIZING, 0.75
    else:
        if coarse_phase == COARSE_PC_LINK:
            return 10.0
        if coarse_phase == COARSE_MODULE_SCAN:
            return 40.0
        return 0.0

    raw = floor + frac * weight
    if scope == SCOPE_MODULE_SCAN:
        raw = (raw - _OVERVIEW_SPAN) / (100 - _OVERVIEW_SPAN) * 100
    elif scope == SCOPE_INVENTORY:
        raw = raw / _OVERVIEW_SPAN * 100
    return min(99.9, round(max(0.0, raw), 1))


__all__ = [
    "COARSE_MODULE_SCAN",
    "COARSE_PC_LINK",
    "SCOPE_FULL",
    "SCOPE_INVENTORY",
    "SCOPE_MODULE_SCAN",
    "STAGE_ERROR",
    "STAGE_FINALIZING",
    "STAGE_FINISHED",
    "STAGE_IDENTITY",
    "STAGE_IDLE",
    "STAGE_INVENTORY",
    "STAGE_PROBING",
    "STAGE_REGISTER_SCAN",
    "WEIGHT_FINALIZING",
    "WEIGHT_IDENTITY",
    "WEIGHT_INVENTORY",
    "WEIGHT_REGISTER_SCAN",
    "ProgressCounters",
    "progress_percent",
]
