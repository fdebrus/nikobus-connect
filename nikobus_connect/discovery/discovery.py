import asyncio
import inspect
import json
import logging
import os
from datetime import datetime, timezone

from .base import (
    DecodedCommand,
    DiscoveryProgress,
    InventoryQueryType,
    InventoryResult,
    PHASE_FINALIZING,
    PHASE_IDENTITY,
    PHASE_INVENTORY,
    PHASE_REGISTER_SCAN,
)
from .dimmer_decoder import DimmerDecoder
from .pc_link_decoder import PcLinkDecoder
from .pc_logic_decoder import PcLogicDecoder
from .shutter_decoder import ShutterDecoder
from .switch_decoder import SwitchDecoder
from .mapping import (
    DEVICE_TYPES,
    KEY_MAPPING,
    KEY_MAPPING_MODULE,
    get_module_type_from_device_type,
)
from .protocol import (
    classify_device_type,
    convert_nikobus_address,
    derive_pc_logic_input_physicals,
    reverse_hex,
)
from ..const import (
    COMMAND_EXECUTION_DELAY,
    DEVICE_ADDRESS_INVENTORY,
    DEVICE_INVENTORY_ANSWER,
    MODULE_SCAN_ACK_TIMEOUT,
    MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT,
    MODULE_SCAN_DATA_TIMEOUT,
    MODULE_SCAN_RETRY_LIMIT,
    MODULE_SCAN_TRAILER_PREFIX,
    PC_LINK_INVENTORY_SIGNATURE_BYTE,
)
from .fileio import (
    merge_discovered_buttons,
    merge_discovered_modules,
    merge_linked_modules,
)
from ..protocol import make_pc_link_inventory_command

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IR channel decoding
# ---------------------------------------------------------------------------
# IR receivers use bus addresses where the last byte increments from a base.
# E.g. base 0D1C80 → slots 0D1C81..0D1CBF; base 0FFEC0 → slots 0FFEC1..
# Channel number = slot_byte - base_byte  (range 01-39).
# Bank (A/B/C/D) is determined by the key index on the button:
#   4-ch buttons: key 0→C, 1→A, 2→D, 3→B  (labels 1C, 1A, 1D, 1B)
#   8-ch buttons: keys 0-3 = group 2 (C,A,D,B), keys 4-7 = group 1 (C,A,D,B)
# The C,A,D,B pattern repeats every 4 keys, so bank = map[key % 4].
_IR_BANK_CYCLE = ("C", "A", "D", "B")
_IR_MAX_CHANNEL = 39

# ---------------------------------------------------------------------------
# Per-product DLL-derived scan profiles
# ---------------------------------------------------------------------------
#
# Niko's PC software ("nikobus.exe") drives per-module reads via a plugin
# architecture: ``CalcMemMap.dll`` loads each product DLL (Niko_05_XXX.dll)
# and calls its ``GetDLLReadInfo`` export to learn how to scan that family.
# Each call returns one section, described as (byte_offset, length, ...).
# PC software iterates section indexes 0, 1, 2, ... until the DLL returns
# length 0.
#
# Memory-to-bus mapping (derived empirically by matching the captured
# 0x3D82 trace against the dimmer DLL's section list — see CHANGELOG):
#
#   byte_offset = (sub_byte * 256 + register) * 16
#
# Each register read returns 16 bytes (one BP cell). To cover a section
# from ``offset`` of ``length`` bytes, we read registers
# ``floor(offset/16)..ceil((offset+length)/16)`` at sub-byte
# ``offset // 4096`` (with spillover into the next sub when the section
# crosses a 4096-byte bank boundary).
#
# Each product DLL was disassembled and decoded into ``ScanSection``
# tuples below. ``broad_scan=True`` opts into the heavy sections that
# PC software conditionally skips when CalcMemoryMap has primed a
# project-level cache (default lengths up to ~11KB).
#
# Source DLLs analysed (Nikobus PC software install, 2026-05-23):
#   Niko_05_000_01.dll  (12-channel switch)
#   Niko_05_007.dll     (older switch variant)
#   Niko_05_010.dll     (older switch variant)
#   Niko_05_100.dll     (dimmer)
#   Niko_05_200.dll     (PC-Link)
#   Niko_05_201a.dll    (PC-Logic)
#   Niko_05_202.dll     (roller)
#   Niko_05_207.dll     (feedback)

# Each profile entry: (sub_byte_hex, register_tuple).
# Iterated in order during the per-module scan.
ScanSection = tuple[str, tuple[int, ...]]


def _regs_for_bytes(offset: int, length: int) -> tuple[ScanSection, ...]:
    """Translate a (byte_offset, length) section into one or more
    (sub_byte, register_tuple) pairs, splitting at 4096-byte bank
    boundaries.

    Each register read returns 16 bytes (one BP cell), so a section
    of ``length`` bytes starting at ``offset`` requires
    ``ceil((offset+length)/16) - floor(offset/16)`` reads, distributed
    across sub-bytes ``offset // 4096`` and any banks the section
    crosses into.
    """

    if length <= 0:
        return ()
    first_reg = offset // 16
    last_reg = (offset + length - 1) // 16
    result: list[ScanSection] = []
    cur_sub = first_reg // 256
    cur_start = first_reg % 256
    cur_end = first_reg % 256
    for reg in range(first_reg + 1, last_reg + 1):
        sub = reg // 256
        if sub != cur_sub:
            result.append((f"{cur_sub:02X}", tuple(range(cur_start, cur_end + 1))))
            cur_sub = sub
            cur_start = reg % 256
        cur_end = reg % 256
    result.append((f"{cur_sub:02X}", tuple(range(cur_start, cur_end + 1))))
    return tuple(result)


def _merge_passes(passes: tuple[ScanSection, ...]) -> tuple[ScanSection, ...]:
    """Deduplicate and merge per-sub register sets while preserving order.

    Re-reads of the same (sub, reg) are collapsed — the scan loop
    doesn't gain anything by re-issuing identical commands. Multiple
    sections that target the same sub-byte are kept as separate passes
    (so the progress UI shows each section's discovery boundary).
    """

    merged: list[ScanSection] = []
    seen: dict[str, set[int]] = {}
    for sub, regs in passes:
        bucket = seen.setdefault(sub, set())
        new_regs = tuple(r for r in regs if r not in bucket)
        if not new_regs:
            continue
        bucket.update(new_regs)
        merged.append((sub, new_regs))
    return tuple(merged)


# --- DIMMER (Niko_05_100) ---
# GetDLLReadInfo enumerates 8 sections (decoded from the case dispatch
# at 0x100022f5):
#   sec 0: offset=0x005A length=0x002E (header)
#   sec 1: offset=0x0096 length=0x0004
#   sec 2: offset=0x03E3 length=0x0002 (link-table config)
#   sec 3: offset=0x03E3 length=0xC85 default (variable — LINK TABLE)
#   sec 4: offset=0x170C length=0x0231 (secondary table)
#   sec 5: offset=0x1960 length=0x0003
#   sec 6: offset=0x4650 length=0x0041 (status)
#   sec 7: offset=0x1962 length=0x2CF2 default (variable, project-only)
#
# The 0x3D82 trace captured PC software doing sections 0,1,2,4,5,6 (48 reads)
# — sections 3 and 7 were conditionally skipped because CalcMemoryMap had
# primed the project flag. For bus discovery (no project file) we MUST
# read section 3 (the actual link table). Section 7 is left to broad_scan.
_DIMMER_PROFILE_DEFAULT: tuple[ScanSection, ...] = _merge_passes((
    *_regs_for_bytes(0x005A, 0x002E),   # section 0
    *_regs_for_bytes(0x0096, 0x0004),   # section 1
    *_regs_for_bytes(0x03E3, 0x0002),   # section 2 (config)
    *_regs_for_bytes(0x03E3, 0x0C85),   # section 3 (variable link table)
    *_regs_for_bytes(0x170C, 0x0231),   # section 4
    *_regs_for_bytes(0x1960, 0x0003),   # section 5
    *_regs_for_bytes(0x4650, 0x0041),   # section 6
))

# Section 7 (huge — 11506 bytes wrapping through sub=2 and sub=3). Skipped
# unless broad_scan=True.
_DIMMER_PROFILE_BROAD_EXTRA: tuple[ScanSection, ...] = _regs_for_bytes(0x1962, 0x2CF2)

# COM-trace-aligned dimmer profile — 0.19.0. Captured against 0E6C
# (Niko 05-007-02) on 24/05/2026: the PC software reads three bands
# at function=0x22:
#   sub=00 0x20..0x3F (main link table; stops at 0x3F when full)
#   sub=00 0xF8..0xFF (timer/config band; stops at 0xFF terminator)
#   sub=01 0x20..0x2F (secondary link band; stopped at 0x2A on 0E6C)
# Dimmer chunks are 8 bytes (not 16) so the early-stop tolerates a
# 3-byte trailing FF terminator instead of 6.
_DIMMER_PROFILE_COM_ALIGNED: tuple[ScanSection, ...] = (
    ("00", tuple(range(0x20, 0x40))),
    ("00", tuple(range(0xF8, 0x100))),
    ("01", tuple(range(0x20, 0x30))),
)

# --- ROLLER (Niko_05_202) ---
# 5 sections from dispatch table at 0x10003ACC:
#   sec 0: offset=0x03E6 length=0x0002 (config)
#   sec 1: offset=0x03E8 length=0xE90 default (variable LINK TABLE)
#   sec 2: offset=0x1388 length=0x2BB0 default (variable, project-only)
#   sec 3: offset=0x1386 length=0x0002 (re-read)
#   sec 4: offset=0x0064 length=0x00F1 default
_ROLLER_PROFILE_FULL: tuple[ScanSection, ...] = _merge_passes((
    *_regs_for_bytes(0x0064, 0x00F1),   # section 4 (16 reads)
    *_regs_for_bytes(0x03E6, 0x0002),   # section 0
    *_regs_for_bytes(0x03E8, 0x0E90),   # section 1 (variable, ~234 reads)
    *_regs_for_bytes(0x1386, 0x0002),   # section 3
))

# Anchored productive band — 0.18.0. Two live-roller traces on
# 2026-05-23 (modules 9105, 8394) showed link records cluster tightly
# in sub=00 reg 0x90..0x9C plus a lone "master" slot at 0xF0; the
# sub=01 0x00..0x27 band holds the per-channel state mirror. Sec 4
# returned only records that also appear in the sub=01 mirror —
# dropped. Sec 0 and sec 3 are 1-reg config sentinels that yielded
# zero records on both modules.
#
# Range is padded around the observed cluster (0x8E..0xA8 instead of
# 0x90..0x9C) to leave headroom for installs with more programming.
# Reduces default scan from 251 to ~53 reg-reads per roller (~4.7×).
_ROLLER_PROFILE_ANCHORED: tuple[ScanSection, ...] = _merge_passes((
    ("00", tuple(range(0x8E, 0xA9)) + (0xF0,)),
    ("01", tuple(range(0x00, 0x28))),
))

# Sections trimmed from the anchored profile, restorable via
# ``broad_scan=True`` — covers the sec 4 mirror, sec 0/3 sentinels,
# the rest of sec 1's link-table range, and the huge sec 2 band.
_ROLLER_PROFILE_BROAD_EXTRA: tuple[ScanSection, ...] = _merge_passes((
    *_regs_for_bytes(0x0064, 0x00F1),
    *_regs_for_bytes(0x03E6, 0x0002),
    ("00", tuple(range(0x3F, 0x8E)) + tuple(range(0xA9, 0xF0)) + tuple(range(0xF1, 0x100))),
    ("01", tuple(range(0x28, 0x40))),
    *_regs_for_bytes(0x1386, 0x0002),
    *_regs_for_bytes(0x1388, 0x2BB0),
))

# COM-trace-aligned roller profile — 0.19.0. Same band as switches
# per the PC software trace: sub=00 starting at 0x10, parser-driven
# early-stop on the trailing-FF terminator. Observed on 9105 (5/6
# programmed channels, 13 reads to 0x1C) and 8394 (3/6 programmed,
# 7 reads to 0x16).
_ROLLER_PROFILE_COM_ALIGNED: tuple[ScanSection, ...] = (
    ("00", tuple(range(0x10, 0x40))),
)

# --- PC-LOGIC (Niko_05_201a) ---
# 4 sections at sub=4 (dispatch table at 0x10001A80):
#   sec 0: offset=0x42CB length=0x0001
#   sec 1: offset=0x4268 length=0x0104 default
#   sec 2: offset=0x445C length=0x060E default
#   sec 3: offset=0x4E20 length=0x0118 default
_PC_LOGIC_PROFILE_FULL: tuple[ScanSection, ...] = _merge_passes((
    *_regs_for_bytes(0x42CB, 0x0001),
    *_regs_for_bytes(0x4268, 0x0104),
    *_regs_for_bytes(0x445C, 0x060E),
    *_regs_for_bytes(0x4E20, 0x0118),
))

# COM-trace-aligned PC-Logic profile — 0.19.0. CRITICAL FIX: the
# previous DLL-derived plan scanned sub=04 (because the DLL section
# offsets land in the 0x4000+ range, mapping to sub=04 in the
# byte-offset-to-(sub,reg) translation). But the PC software COM7
# trace shows PC-Logic uses sub=00, sub=02, sub=03 — NOT sub=04.
# This explains why module 940C returned 0 decoded records on the
# 2026-05-23 HA trace: every read was sent to the wrong sub-byte.
#
# Per the 24/05/2026 trace (module 940C):
#   sub=00 0x06..0x1E + 0x3E       (main records, ~25 reads)
#   sub=02 0xAF..0xEE              (secondary band, ~40 reads)
#   sub=03 0xE8..0xF4              (tertiary band, 13 reads)
# Parser-driven early-stop handles the per-pass terminator.
_PC_LOGIC_PROFILE_COM_ALIGNED: tuple[ScanSection, ...] = (
    ("00", tuple(range(0x06, 0x40))),
    ("02", tuple(range(0xAF, 0xEF))),
    ("03", tuple(range(0xE8, 0xF5))),
)

# --- PC-LINK (Niko_05_200) ---
# 0.18.0: validated against a real PC-software COM4 trace
# (24/05/2024 16:25:43) reading from address F586 (module 86F5).
# The trace settles the question that 0.17.0 explicitly flagged
# (see git history): the Niko_05_200.dll "sections" describe the
# HOST's project-file layout, not bus reads.
#
# PC software reads (97 reg-reads, 3 sub-bytes):
#   sub=00 0x05..0x09, 0x3E         (6  — vendor-aligned header)
#   sub=01 0x70..0x93, 0x96         (37 — vendor-aligned secondary)
#   sub=04 0x65..0x69                (5  — vendor-aligned status)
#   sub=04 0xA3..0xD3                (49 — PC-Link MODULE REGISTRY)
#
# The 0xA3..0xD3 module-registry band is the same band pre-0.16.0
# used, and the same band 0.16.0's vendor-aligned plan extended to
# 0xA3..0xFF. The 0.17.0 DLL-derived plan (sub=00 long sweep + sub=02
# + sub=03, total 280 reads) **never touches the bands the PC software
# actually uses** — on the 2026-05-23 HA trace of 86F5, the 0.17.0
# plan returned 0 decoded records across 280 reads. This profile fixes
# that regression.
_PC_LINK_PROFILE_DEFAULT: tuple[ScanSection, ...] = _merge_passes((
    ("00", tuple(range(0x05, 0x0A)) + (0x3E,)),
    ("01", tuple(range(0x70, 0x94)) + (0x96,)),
    ("04", tuple(range(0x65, 0x6A)) + tuple(range(0xA3, 0xD4))),
))

# The 0.17.0 DLL-derived sections, preserved for broad_scan=True
# coverage in case future firmware variants expose any of them on
# the bus. The 24/05/2024 COM trace shows the PC software never
# touches these regions on Niko_05_200.
_PC_LINK_PROFILE_BROAD_EXTRA: tuple[ScanSection, ...] = _merge_passes((
    *_regs_for_bytes(0x0063, 0x0181),
    *_regs_for_bytes(0x03E6, 0x0002),
    *_regs_for_bytes(0x03E6, 0x0C82),
    *_regs_for_bytes(0x2AF8, 0x0280),
    *_regs_for_bytes(0x2EDE, 0x0002),
    *_regs_for_bytes(0x3E80, 0x00C0),
    # Also stretch the registry band to PC-Link's pre-0.16.0 ceiling.
    ("04", tuple(range(0xD4, 0x100))),
))

# --- FEEDBACK (Niko_05_207) ---
# DLL describes 5 sections totaling ~912 reads, but real-world testing
# (2026-05-23) showed feedback modules don't respond to function-0x10
# reads at any of those addresses — every read ACK-times-out, and the
# scan wastes ~45 min per module to no benefit.
#
# Feedback module programming lives on SOURCE modules' BP cells (per
# Niko docs), not in the feedback module's own memory. The DLL bands
# we extracted are display/state config, not link records.
#
# Restored to NON_OUTPUT_MODULE_TYPES below. Profile left as data
# constant in case future hardware variants need it.
_FEEDBACK_PROFILE_DEFAULT: tuple[ScanSection, ...] = _merge_passes((
    *_regs_for_bytes(0x4000, 0x2000),
    *_regs_for_bytes(0x6000, 0x0100),
    *_regs_for_bytes(0x6100, 0x0100),
    *_regs_for_bytes(0x6200, 0x1700),
))

# --- SWITCH ---
# Niko_05_000_01.dll's GetDLLReadInfo returns a magic tuple
# (offset=0x100, recsize=6, recs_per_unit=0x10, length=0) — NOT a
# section enumeration. The EXE-side dispatch is too tangled to decode
# confidently without dynamic analysis (the function at 0x40efa4 dispatches
# the actual reads from a project-driven buffer).
#
# Pragmatic approach: apply the dimmer/roller pattern (link table at
# sub=0 reg=0x3E variable, header at 0x05..0x09, secondary at
# sub=1 0x70..0x96, status at sub=4 0x65..0x69) PLUS the pre-0.16.0
# legacy sub=4 0x00..0x3F band as a proven safety net.
_SWITCH_PROFILE_FULL: tuple[ScanSection, ...] = _merge_passes((
    # Header (dimmer-style)
    ("00", tuple(range(0x05, 0x0A))),
    # Link-table band (dimmer-style; hypothesis)
    ("00", tuple(range(0x3E, 0x100))),
    ("01", tuple(range(0x00, 0x07))),
    # Pre-0.16.0 vendor-aligned secondary band
    ("01", tuple(range(0x70, 0x94)) + (0x96,)),
    # Pre-0.16.0 sub=4 link records band — proven safety net
    ("04", tuple(range(0x00, 0x40))),
    # Vendor status band
    ("04", tuple(range(0x65, 0x6A))),
))

# Anchored productive band — 0.18.0. Three switch-scan traces on
# 2026-05-23 (modules C9A5 full, 4707 ×2) validated the DLL magic
# tuple (recsize=6 bytes, recs_per_unit=0x10) and pinned down where
# records actually live:
#
#   sub=00 anchor at reg=0x8F-0x90, cluster extends to 0x9A (4707) or
#       0xA7 (C9A5) — modes M01..M15 mixed.
#   sub=04 anchor at reg=0x10, cluster extends to 0x16 (4707) or
#       0x27 (C9A5) with a deterministic gap at 0x14. 4707 also has
#       a lone record at 0x00 — preserved by starting the band at 0x00.
#
# Bands dropped from default:
#   - sub=00 0x05..0x09 (header probe) — 0 records on 3/3 traces.
#   - sub=00 0x3E..0x8E (~80 dead regs before the anchor) — 0/3 traces.
#       Also implicated in module-exhaustion failure (4707 log 2/2:
#       pass aborted at 0xD9 after 156 reads, downstream passes timed
#       out completely). Cutting this band removes the failure mode.
#   - sub=01 entirely — secondary band on C9A5 returned 12 records,
#       9 of which duplicate sub=04 content. Net 3 unique records
#       not worth the 44 register reads.
#   - sub=04 0x28..0x3F (24 dead regs past anchor cluster) — 0/3.
#   - sub=04 0x65..0x69 (status probe) — 0/3 traces.
#
# Range padded around the observed clusters (sub=00 0x8E..0xAF,
# sub=04 0x00..0x2F) to leave headroom for installs with more
# programming. Reduces default scan from 312 to ~82 reg-reads per
# switch (~3.8×) and eliminates the long-sweep exhaustion mode.
_SWITCH_PROFILE_ANCHORED: tuple[ScanSection, ...] = _merge_passes((
    ("00", tuple(range(0x8E, 0xB0))),
    ("04", tuple(range(0x00, 0x30))),
))

# COM-trace-aligned switch profile — 0.19.0. The Nikobus PC software
# COM7 capture (24/05/2026 full session) showed that across three
# switches (C9A5 12-ch, 4707 12-ch, 5B05 4-ch compact) the PC reads
# ONLY at sub=00 starting at register 0x10, with the scan length
# varying by programming density (5B05 stopped at 0x17, 4707 at
# 0x1B, C9A5 at 0x27). The terminator is a chunk whose trailing
# 6 bytes are 0xFF — detected by the parser-driven early-stop in
# ``_scan_module_registers`` so we don't read past the table end.
#
# Upper bound of 0x40 is a safety ceiling; the early-stop almost
# always fires well before that. This profile is therefore "scan
# the band, parser tells us when to stop" — same model the PC
# software uses.
_SWITCH_PROFILE_COM_ALIGNED: tuple[ScanSection, ...] = (
    ("00", tuple(range(0x10, 0x40))),
)

# Bands trimmed from the anchored profile, restorable via
# ``broad_scan=True``. Reconstitutes the full _SWITCH_PROFILE_FULL
# plan when set, modulo the deduplicated overlap with the anchored
# core.
_SWITCH_PROFILE_BROAD_EXTRA: tuple[ScanSection, ...] = _merge_passes((
    ("00", tuple(range(0x05, 0x0A))),
    ("00", tuple(range(0x3E, 0x8E)) + tuple(range(0xB0, 0x100))),
    ("01", tuple(range(0x00, 0x07))),
    ("01", tuple(range(0x70, 0x94)) + (0x96,)),
    ("04", tuple(range(0x30, 0x40))),
    ("04", tuple(range(0x65, 0x6A))),
))


# Final per-module-type scan plan. Keys are the canonical module types
# from ``get_module_type_from_device_type``.
#
# Note: ``feedback_module`` is NOT in this map (0.17.1). Real-world
# testing showed feedback modules don't respond to function-0x10 reads
# at any of the DLL-derived sections — the scan wasted ~45 min per
# module for zero records. Feedback programming lives on source
# modules' BP cells. The profile constant ``_FEEDBACK_PROFILE_DEFAULT``
# is preserved as a data constant for future use if firmware variants
# differ.
_MODULE_SCAN_PROFILES: dict[str, tuple[ScanSection, ...]] = {
    "switch_module":   _SWITCH_PROFILE_COM_ALIGNED,
    "roller_module":   _ROLLER_PROFILE_COM_ALIGNED,
    "dimmer_module":   _DIMMER_PROFILE_COM_ALIGNED,
    "pc_logic":        _PC_LOGIC_PROFILE_COM_ALIGNED,
    "pc_link":         _PC_LINK_PROFILE_DEFAULT,
}

# Broad-scan extras. Re-add the bands trimmed from the COM-trace
# default — the 0.18.0 anchored ranges (for switch/roller), the full
# DLL-derived plans (for dimmer/pc_logic/pc_link), and the huge
# variable sections (for dimmer/roller).
_MODULE_SCAN_PROFILES_BROAD_EXTRA: dict[str, tuple[ScanSection, ...]] = {
    "switch_module":   _merge_passes((*_SWITCH_PROFILE_ANCHORED, *_SWITCH_PROFILE_BROAD_EXTRA)),
    "roller_module":   _merge_passes((*_ROLLER_PROFILE_ANCHORED, *_ROLLER_PROFILE_BROAD_EXTRA)),
    "dimmer_module":   _merge_passes((*_DIMMER_PROFILE_DEFAULT, *_DIMMER_PROFILE_BROAD_EXTRA)),
    "pc_logic":        _PC_LOGIC_PROFILE_FULL,
    "pc_link":         _PC_LINK_PROFILE_BROAD_EXTRA,
}

# Conservative fallback when a caller hands us a sub-byte the plan
# didn't cover (keeps forensic mode probeable without a silent skip).
_DEFAULT_SCAN_REGISTERS: tuple[int, ...] = tuple(range(0x00, 0x100))

# Parser-driven early-stop — 0.19.0. The Nikobus PC software COM7
# trace (24/05/2026) shows the PC stops a register-scan pass after
# reading a register whose response payload ends with N trailing 0xFF
# bytes — the "no further records" terminator. N depends on chunk size:
#
#   switch/roller (6-byte records, 12-hex chunks): tail of 6 bytes
#       (12 hex chars). The PC stopped at C9A5 reg=0x27 (tail=6),
#       4707 reg=0x1B (tail=13), 5B05 reg=0x17 (tail=15), 8394
#       reg=0x16 (tail=16), 9105 reg=0x1C (tail=11).
#   dimmer (8-byte chunks): tail of 3 bytes (6 hex chars). Smaller
#       because the 8-byte chunk has less room for both a partial
#       record and a terminator. PC stopped at 0E6C 0x3F (8/8 FF),
#       0x2A (8/8 FF), 0xFF (last 4 bytes FF).
#   pc_link / pc_logic (16-byte chunks, 32-hex): same 6-byte tail
#       rule — the 940C 0x1E stop had 12 trailing FF.
#
# When the terminator fires, the current pass aborts and the scanner
# moves to the next pass / next module. Existing chunks-already-read
# are still decoded; this is "stop AFTER processing this register",
# not "discard this register". Matches the PC software's behaviour
# byte-for-byte.
_FF_TERMINATOR_TAIL_HEX: dict[str, int] = {
    "switch_module": 12,
    "roller_module": 12,
    "pc_link":       12,
    "pc_logic":      12,
    "dimmer_module":  6,
}

# Module-type buckets whose per-module register scan is short-circuited.
# 0.17.1: ``feedback_module`` restored to this set after real-world
# testing showed feedback modules don't respond to the DLL-derived
# scan plan (~45 min wasted per module on ACK timeouts). Feedback
# programming lives on source modules' BP cells, not in the feedback
# module's own memory — there are no link records here to discover.
#
# - ``feedback_module`` (0x42, 05-207): doesn't respond to function-0x10
#   reads on its memory-map sections. Its programming surface is the
#   BP-cell tables on the source switch/dimmer/roller modules.
# - ``other_module``: catch-all bucket — primarily Button-class devices
#   (4-OP / 2-OP / RF / IR / Motion / Feedback Button) that carry no
#   register memory, plus any Module-category device whose name fails
#   to match a more specific keyword in
#   ``get_module_type_from_device_type``.
# - ``interface_module`` (0x37, 05-206): Modular Interface, 6 inputs.
#   The inputs feed the PC-Logic for routing — the interface itself
#   has no BP-cell table to scan. If a future capture proves
#   otherwise, drop the bucket from this set and add a decoder.
# - ``audio_module`` (0x2B, 05-205): Audio Distribution. No
#   button-link routing surface today; left as visibility-only until
#   a real install validates the storage format.
NON_OUTPUT_MODULE_TYPES: frozenset[str] = frozenset({
    "feedback_module",
    "other_module",
    "interface_module",
    "audio_module",
})

def _scan_passes_for_module_type(
    module_type: str | None, *, broad_scan: bool = False
) -> tuple[ScanSection, ...]:
    """Return the ordered per-module scan plan as (sub_byte, registers).

    Each entry is one "pass" — a contiguous register range read against
    a single sub-byte. The list is derived from the product DLL's
    ``GetDLLReadInfo`` export (see ``_MODULE_SCAN_PROFILES``).

    ``broad_scan=True`` appends the conditional sections that PC software
    skips when its in-memory project cache is primed (typically large
    bands the vendor only reads on full project upload). For your install
    these are usually empty — but the per-register give-up logic stops
    each pass early when responses are flash filler.

    Module types not in the plan (audio, interface, other) return ``()``.
    """

    plan = _MODULE_SCAN_PROFILES.get(module_type) if module_type else None
    if not plan:
        return ()
    if broad_scan:
        extras = _MODULE_SCAN_PROFILES_BROAD_EXTRA.get(module_type, ())
        if extras:
            plan = _merge_passes((*plan, *extras))
    return plan


def _wire_sub_byte(sub_byte: str) -> str:
    """Map a plan-time sub-byte token to its on-the-wire form.

    The bus protocol's "read register" command accepts sub-bytes 0x00..0xFF
    directly. Plan-time tokens are uppercase hex strings ("00", "01", "02",
    "03", "04", "06"), matching the wire byte one-for-one. This helper
    exists as a hook for any future synthetic tokens the plan needs.
    """

    return sub_byte


def decode_ir_channel(ir_slot_addr: str | None, key_raw: int | None, ir_base_byte: int = 0x80) -> str | None:
    """Derive the IR channel label from a bus slot address and key index.

    Parameters
    ----------
    ir_slot_addr : str
        The 6-char IR slot address (e.g. "0D1C91").
    key_raw : int
        The raw key index (0-7).
    ir_base_byte : int
        The base byte of the IR receiver (default 0x80).  Channel is
        derived as ``slot_byte - ir_base_byte``.

    Returns the label (e.g. "17A") or None for non-IR / out-of-range addresses.
    """
    if not ir_slot_addr or key_raw is None:
        return None

    a = ir_slot_addr.strip().upper()
    if len(a) != 6:
        return None

    try:
        slot_byte = int(a[-2:], 16)
    except ValueError:
        return None

    channel = slot_byte - ir_base_byte
    if channel < 1 or channel > _IR_MAX_CHANNEL:
        return None

    if not isinstance(key_raw, int) or key_raw < 0 or key_raw > 7:
        return None

    bank = _IR_BANK_CYCLE[key_raw % 4]
    return f"{channel:02d}{bank}"


def build_ir_receiver_lookup(buttons) -> dict[str, int]:
    """Build a mapping of 4-char IR address prefixes to their base byte.

    Operates on the Option-A physical-keyed button store. ``buttons`` may
    be the ``nikobus_button`` dict itself (physical_address -> entry) or
    any iterable of ``(physical_address, entry)`` pairs.

    Returns e.g. {"0D1C": 0x80, "0FFE": 0xC0}.
    """
    if isinstance(buttons, dict):
        items = buttons.items()
    else:
        items = buttons

    lookup: dict[str, int] = {}
    for physical_addr, button in items:
        if not isinstance(button, dict):
            continue
        if "IR" not in (button.get("type") or ""):
            continue
        addr = (physical_addr or "").strip().upper()
        if len(addr) != 6:
            continue
        try:
            prefix = addr[:4]
            base_byte = int(addr[-2:], 16)
            lookup.setdefault(prefix, base_byte)
        except ValueError:
            continue
    return lookup


def split_ir_button_address(
    addr: str | None,
    ir_receiver_lookup: dict[str, int] | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Nikobus IR receiver: physical device is XXXX{base}, IR slots are XXXX{base+1}..
    Returns (physical_addr, ir_slot_addr, ir_slot_byte_hex).
    Non-IR addresses return (addr, None, None).

    Parameters
    ----------
    addr : str
        The 6-char address to classify.
    ir_receiver_lookup : dict
        Mapping of 4-char prefix → base byte, built by build_ir_receiver_lookup().
        Falls back to legacy {"0D1C": 0x80} when None.
    """
    if not addr:
        return None, None, None

    a = addr.strip().upper()
    if len(a) != 6:
        return a, None, None

    if ir_receiver_lookup is None:
        ir_receiver_lookup = {"0D1C": 0x80}

    prefix = a[:4]
    if prefix not in ir_receiver_lookup:
        return a, None, None

    base_byte = ir_receiver_lookup[prefix]
    physical = f"{prefix}{base_byte:02X}"
    if a == physical:
        return physical, None, None

    return physical, a, a[-2:]


def add_to_command_mapping(command_mapping, decoded_command, module_address, ir_receiver_lookup=None):
    """Store decoded command information, allowing one-to-many button mappings."""
    push_button_address = decoded_command.get("push_button_address")

    # Fall back to physical device address when push_button_address could not
    # be resolved (e.g. coordinator doesn't know the button's channel count).
    # fileio._rebuild_address_lookup() maps physical addresses via
    # linked_button[].address, so the match will still succeed.
    if push_button_address is None:
        push_button_address = decoded_command.get("button_address")

    # Accept legacy/new decoder fields
    key_raw = decoded_command.get("key_raw")
    if key_raw is None:
        key_raw = decoded_command.get("key")  # <-- IMPORTANT fallback

    if push_button_address is None or key_raw is None:
        return

    # Normalize key to a stable string/int (depending on what your decoders use)
    if isinstance(key_raw, str):
        key_raw = key_raw.strip()
        if key_raw.isdigit():
            key_raw = int(key_raw)

    physical_push, ir_push_addr, ir_push_slot = split_ir_button_address(push_button_address, ir_receiver_lookup)

    button_address = decoded_command.get("button_address")
    physical_btn, ir_btn_addr, ir_btn_slot = split_ir_button_address(button_address, ir_receiver_lookup)

    # Derive IR channel label (e.g. "17A") from the bus slot address + key.
    ir_slot_addr = ir_btn_addr or ir_push_addr
    ir_base_byte = 0x80
    if ir_slot_addr and ir_receiver_lookup:
        prefix = ir_slot_addr[:4].upper()
        ir_base_byte = ir_receiver_lookup.get(prefix, 0x80)
    ir_channel = decode_ir_channel(ir_slot_addr, key_raw, ir_base_byte) if ir_slot_addr else None

    # Mapping key: prefer logical IR channel label; fall back to raw slot byte.
    ir_key = ir_channel or ir_btn_slot or ir_push_slot

    # For IR records the nibble-shifted wire address (e.g. "D44E2C" for
    # receiver 0D1C80 + code 10B) doesn't start with an IR receiver prefix,
    # so split_ir_button_address leaves physical_push as the shifted form.
    # Use the IR receiver's physical base instead so the merge-time
    # resolver can locate the receiver and attach the link to an
    # IR:{code} op-point. physical_btn is that base when button_address
    # is the pre-shift slot address (always the case for IR records).
    if ir_key and physical_btn:
        mapping_address = physical_btn
    else:
        mapping_address = physical_push
    mapping_key = (mapping_address, key_raw, ir_key)
    outputs = command_mapping.setdefault(mapping_key, [])

    channel_number = decoded_command.get("channel")

    # PC-Link / PC-Logic decoders set ``module_address`` in the
    # decoded metadata to the **resolved target** module — not the
    # controller they were scanned from. Honour that override so the
    # link lands on the real output module. Switch/dimmer/roller
    # decoders never set this field, so the positional argument (the
    # module being scanned) is used in those cases.
    target_module_address = (
        decoded_command.get("module_address") or module_address
    )

    output_definition = {
        "module_address": target_module_address,
        "channel": channel_number,
        "mode": decoded_command.get("M"),
        "t1": decoded_command.get("T1"),
        "t2": decoded_command.get("T2"),
        "payload": decoded_command.get("payload"),

        # button addresses
        "button_address": physical_btn or physical_push or button_address,
        "ir_button_address": ir_btn_addr or ir_push_addr,

        # IR channel label (e.g. "17A", "30B") derived from slot address + key.
        "ir_code": ir_channel or ir_btn_slot or ir_push_slot,

        # 0.5.22: scan-source provenance. ``output_module_table`` for
        # records read from a switch / dimmer / roller's own link
        # table (current programming). ``pc_link_registry`` or
        # ``pc_logic_registry`` for records read from PC-Link or
        # PC-Logic register memory (may be stale residue from a
        # previous install, or scene-only programming the output
        # modules don't carry). HA-side reconciliation filters on
        # this to avoid treating residue as active programming.
        # See Nikobus-HA #319 for the IKIKN forensic.
        "record_source": decoded_command.get("record_source"),
    }

    dedupe_key = (
        output_definition["module_address"],
        output_definition["channel"],
        output_definition["mode"],
        output_definition["t1"],
        output_definition["t2"],
        output_definition.get("ir_code"),
        output_definition.get("ir_button_address"),
    )

    existing_keys = {
        (
            entry.get("module_address"),
            entry.get("channel"),
            entry.get("mode"),
            entry.get("t1"),
            entry.get("t2"),
            entry.get("ir_code"),
            entry.get("ir_button_address"),
        )
        for entry in outputs
    }

    if dedupe_key not in existing_keys:
        outputs.append(output_definition)


async def _notify_discovery_finished(
    discovery,
    *,
    discovered_devices: dict | None = None,
    inventory_query_type=None,
) -> None:
    """Call the discovery finished callback when available.

    0.5.20: ``discovered_devices`` and ``inventory_query_type`` are
    passed as keyword arguments so consumers don't have to read
    mutable instance state (which the library may have cleared
    before the callback fired in 0.5.19 and earlier — see
    Nikobus-HA #319).

    Backward-compat: if the consumer's callback predates 0.5.20 and
    has a no-arg signature (``async def cb()``), it's called with
    no args. Callbacks that accept ``**kwargs`` or explicitly name
    the new parameters receive them.
    """

    callback = getattr(discovery, "on_discovery_finished", None)
    if not callback:
        return

    try:
        sig = inspect.signature(callback)
    except (TypeError, ValueError):
        # Some callables (built-ins, partials with quirks) can't be
        # introspected. Fall back to no-arg call.
        await callback()
        return

    params = sig.parameters
    accepts_var_keyword = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )

    kwargs: dict = {}
    if accepts_var_keyword or "discovered_devices" in params:
        kwargs["discovered_devices"] = discovered_devices
    if accepts_var_keyword or "inventory_query_type" in params:
        kwargs["inventory_query_type"] = inventory_query_type

    await callback(**kwargs)


def _is_inventory_trailer(message: str) -> bool:
    """Detect a "$18<all-FF><CRC>" trailer frame.

    The module emits one of these during a register scan to signal that
    the remaining registers are unprogrammed. The payload between the
    ``$18`` header and the trailing 3-byte CRC is all 0xFF. Treat any
    all-FF payload of length >= 1 byte as a trailer.
    """

    if not isinstance(message, str):
        return False
    if not message.startswith(MODULE_SCAN_TRAILER_PREFIX):
        return False
    # 3 chars header + 6 chars CRC = 9 chars of bookkeeping; payload
    # lives in-between.
    body = message[len(MODULE_SCAN_TRAILER_PREFIX) : -6]
    if not body:
        return False
    return all(ch == "F" for ch in body.upper())


class NikobusDiscovery:
    def __init__(
        self,
        coordinator,
        *,
        config_dir,
        create_task,
        button_data=None,
        on_button_save=None,
        module_data=None,
        on_module_save=None,
        on_progress=None,
        broad_scan: bool = False,
    ):
        self.discovered_devices = {}
        self._coordinator = coordinator
        self._config_dir = config_dir
        self._create_task = create_task
        self._button_data = button_data
        self._on_button_save = on_button_save
        self._module_data = module_data
        self._on_module_save = on_module_save
        # 0.16.0 vendor-aligned default scans only the 48 vendor regs
        # per module. ``broad_scan=True`` re-adds the pre-0.16.0
        # sub=04 0x00..0x3F sweep as a safety-net extra pass.
        self._broad_scan: bool = bool(broad_scan)
        if module_data is not None:
            existing_modules = module_data.get("nikobus_module")
            if not isinstance(existing_modules, dict):
                module_data["nikobus_module"] = {}
        self._on_progress = on_progress
        # Running counters reflected in every ``DiscoveryProgress``.
        self._progress_module_index = 0
        self._progress_module_total = 0
        self._progress_register_total = 0
        self._progress_decoded_records = 0
        # 0.16.1 vendor-aligned scan tracking. The pre-0.16.0 progress
        # model assumed a single per-module register sweep starting at
        # 0x10 — the new vendor plan reads non-contiguous bytes across
        # multiple passes (e.g. sub=00 reads 0x05..0x09 then 0x3E).
        # These counters give consumers a cumulative
        # ``registers_sent / register_total`` ratio that's meaningful
        # regardless of how many passes the plan runs.
        self._progress_module_register_total: int = 0
        self._progress_module_registers_sent: int = 0
        self._progress_pass_index: int = 0
        self._progress_pass_total: int = 0
        self._progress_current_sub_byte: str | None = None
        # Set of unknown device-type bytes already warned about this
        # session. Pre-0.5.4 each scan logged the same WARNING N times
        # per type (one per record); the dedupe collapses that to a
        # single line per type so a noisy install with several uncatalogued
        # types doesn't flood the log on every inventory pass.
        self._unknown_device_types_warned: set[str] = set()
        if button_data is not None:
            existing = button_data.get("nikobus_button")
            if not isinstance(existing, dict):
                button_data["nikobus_button"] = {}
        self._module_timeout_seconds = 5.0
        self._inventory_timeout_seconds = 10.0
        self._decoders = [
            DimmerDecoder(coordinator),
            SwitchDecoder(coordinator),
            ShutterDecoder(coordinator),
            PcLogicDecoder(coordinator),
            PcLinkDecoder(coordinator),
        ]
        self._timeout_task: asyncio.Task | None = None
        self._inventory_timeout_task: asyncio.Task | None = None
        self.discovery_stage: str | None = None
        self._register_scan_queue: list[str] = []
        self._inventory_addresses: set[str] = set()
        self._module_found_data: bool = False
        self._module_consecutive_empties: int = 0
        # Sequential register-scan coordination. The listener dispatches
        # $2E / $1E / $18 frames directly to the event callback (they
        # bypass the command-handler response queue). During a scan we
        # hook the parser entry points to notify this event so the
        # per-command loop can wake up when a data frame or trailer
        # arrives, without rewriting the listener.
        self._scan_event: asyncio.Event = asyncio.Event()
        self._scan_trailer_seen: bool = False
        self._scan_active: bool = False
        self._scan_lock: asyncio.Lock = asyncio.Lock()
        # Cross-module accumulators for the remote-transmitter synthesis
        # pass. Per-module ``merge_linked_modules`` collects button
        # addresses that didn't resolve to any inventory entry; we hold
        # them here until all module scans finish, then cluster by
        # 4-hex suffix and synthesise virtual transmitter parents +
        # passthrough children for clusters above the threshold.
        self._accumulated_unmatched: set[str] = set()
        self._accumulated_command_mapping: dict = {}
        self.reset_state()

    def reset_state(self, *, update_flags: bool = True):
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None
        if self._inventory_timeout_task:
            self._inventory_timeout_task.cancel()
            self._inventory_timeout_task = None
        self._payload_buffer = ""
        self._module_address = None
        self._module_type = None
        self._module_channels: int | None = None
        self._scan_response_index = 0
        self._register_scan_queue = []
        self._inventory_addresses = set()
        self._inventory_identity_queued: set[str] = set()
        self._module_found_data = False
        self._module_consecutive_empties = 0
        self.discovery_stage = None
        self._decoded_buffer: dict | None = None
        self._accumulated_unmatched = set()
        self._accumulated_command_mapping = {}
        if update_flags:
            self._coordinator.discovery_running = False
            self._coordinator.discovery_module = False
            self._coordinator.discovery_module_address = None
            self._coordinator.inventory_query_type = None

    def normalize_module_address(
        self, address: str, *, source: str, reverse_bus_order: bool = False
    ) -> str:
        """Return a canonical module address, logging when normalization occurs."""

        raw = (address or "").strip().upper()
        normalized = raw

        try:
            if reverse_bus_order:
                normalized = reverse_hex(raw)
        except ValueError:
            normalized = raw

        if normalized != raw:
            _LOGGER.debug(
                "Normalized module address | raw=%s normalized=%s source=%s",
                raw,
                normalized,
                source,
            )

        return normalized

    def _get_decoder(self):
        for decoder in getattr(self, "_decoders", []):
            if decoder.can_handle(self._module_type):
                return decoder
        return None

    def _resolve_module_type(
        self, address: str, discovered_device: dict | None
    ) -> str | None:
        """Resolve the module type for ``address``.

        Coordinator config is authoritative — it reflects the user's
        physical wiring via ``dict_module_data``. The inventory
        self-report is only used when config has no entry for the
        address (first-time scan of a newly-added module).

        When both sources disagree, log at INFO so the override is
        visible in ordinary HA logs. This has been observed in the
        wild: a physical switch module self-reporting device_type=0x03
        during the PC-Link identity phase.
        """

        config_type = self._coordinator.get_module_type(address)
        inventory_type = (discovered_device or {}).get("module_type")

        if config_type and inventory_type and config_type != inventory_type:
            _LOGGER.debug(
                "Module type conflict | address=%s config=%s inventory=%s — using config",
                address,
                config_type,
                inventory_type,
            )

        return config_type or inventory_type

    # ------------------------------------------------------------------
    # Sequential register scan
    # ------------------------------------------------------------------

    def _notify_scan_frame(self, message: str) -> None:
        """Wake the sequential scan loop on each inbound discovery frame.

        Called from ``parse_module_inventory_response`` and
        ``handle_device_address_inventory`` for every ``$2E`` / ``$1E``
        / ``$18`` message while a scan is running. A $18 frame whose
        payload is all-FF is treated as a trailer — the module has no
        more programmed memory and the scan should short-circuit.
        """

        if not self._scan_active:
            return
        if message.startswith(MODULE_SCAN_TRAILER_PREFIX) and _is_inventory_trailer(
            message
        ):
            self._scan_trailer_seen = True
        self._scan_event.set()

    async def _scan_module_registers(
        self,
        normalized_address: str,
        base_command: str,
        command_range,
        sub_byte: str = "04",
    ) -> None:
        """Read each register one at a time, waiting for ACK + optional data.

        ``sub_byte`` is the 2-hex byte appended after the register byte
        in the read command. Different sub-bytes address different memory
        banks on a module: ``"04"`` is the default (button-link records),
        ``"00"`` and ``"01"`` access additional banks discovered via PC
        software trace analysis.

        Replaces the former fire-and-forget queue fill. Per register:

        1. Send the inventory read command.
        2. Wait up to ``MODULE_SCAN_ACK_TIMEOUT`` for a ``$05…`` ACK.
           Retry once on timeout; skip the register if still missing.
        3. Wait up to ``MODULE_SCAN_DATA_TIMEOUT`` for the matching
           ``$2E`` / ``$1E`` data frame. Silence is legitimate — empty
           registers produce no data.
        4. If a ``$18`` trailer arrives, break; the module has signalled
           end-of-programmed-memory.

        Two concurrent scans are prevented by ``self._scan_lock``; the
        second caller awaits the first.
        """

        listener = self._coordinator.nikobus_command._listener
        connection = self._coordinator.nikobus_command._connection

        async with self._scan_lock:
            self._scan_active = True
            self._scan_trailer_seen = False
            self._scan_event.clear()
            # Suppress the inactivity timer that scan-response parsing
            # keeps rescheduling. Without this, the first register of a
            # pass that a module ignores drops us into a 5 s window of
            # no responses → the timer fires ``_finalize_discovery``
            # mid-scan, tearing down the connection while the rest of
            # this pass and any follow-up passes are still queued.
            # We finalize explicitly from ``query_module_inventory``
            # once all passes complete.
            self._cancel_timeout()
            # Per-pass register total (legacy compatibility — kept so
            # callers reading ``register_total`` get a non-zero value
            # mid-pass even when the cumulative counters aren't set,
            # e.g. forensic-mode scans that bypass the vendor plan).
            try:
                pass_register_total = len(command_range)
            except TypeError:
                pass_register_total = 0
            # If the vendor plan didn't pre-populate the cumulative
            # totals (e.g. forensic single-pass scan), fall back to
            # the per-pass total so the progress UI still shows
            # something meaningful.
            if not self._progress_module_register_total:
                self._progress_module_register_total = pass_register_total
                self._progress_module_registers_sent = 0
            self._progress_register_total = self._progress_module_register_total
            # Cumulative count BEFORE this pass starts. The trailer /
            # give-up adjustments below collapse the cumulative total
            # to ``pre_pass_sent + this_pass_sent`` — the actual reads
            # that completed — rather than the originally-planned total.
            pre_pass_sent = self._progress_module_registers_sent
            try:
                registers_sent = 0
                consecutive_give_ups = 0
                for reg in command_range:
                    if self._scan_trailer_seen:
                        _LOGGER.debug(
                            "Register scan short-circuited by trailer | module=%s "
                            "last_register=0x%02X sent=%d",
                            normalized_address,
                            reg,
                            registers_sent,
                        )
                        # Trailer short-circuits ONLY the current pass.
                        # Collapse the cumulative total to "what was
                        # actually sent" so the ratio reaches 100 %
                        # naturally instead of getting stuck below.
                        self._progress_module_register_total = (
                            pre_pass_sent + registers_sent
                        )
                        self._progress_register_total = (
                            self._progress_module_register_total
                        )
                        break
                    partial_hex = f"{base_command}{reg:02X}{sub_byte}"
                    pc_link_command = make_pc_link_inventory_command(partial_hex)
                    ack_ok = await self._read_register_once(
                        pc_link_command,
                        reg,
                        normalized_address,
                        listener,
                        connection,
                    )
                    registers_sent += 1
                    self._progress_module_registers_sent += 1
                    await self._emit_progress(
                        PHASE_REGISTER_SCAN,
                        module_address=normalized_address,
                        register=reg,
                    )
                    if ack_ok:
                        consecutive_give_ups = 0
                    else:
                        consecutive_give_ups += 1
                        if consecutive_give_ups >= MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT:
                            # Expected on bank-incompatible sub-bytes; fast-fail
                            # is a feature, not a problem. Keep at DEBUG so it
                            # doesn't surface in the integration UI.
                            _LOGGER.debug(
                                "Register scan pass aborted — module not responding | "
                                "module=%s base_cmd=%s sub=%s last_register=0x%02X "
                                "consecutive_give_ups=%d sent=%d",
                                normalized_address,
                                base_command,
                                sub_byte,
                                reg,
                                consecutive_give_ups,
                                registers_sent,
                            )
                            # Collapse the cumulative module total to
                            # what was actually sent — same rationale as
                            # the trailer-short-circuit branch above.
                            self._progress_module_register_total = (
                                pre_pass_sent + registers_sent
                            )
                            self._progress_register_total = (
                                self._progress_module_register_total
                            )
                            break
                    await asyncio.sleep(COMMAND_EXECUTION_DELAY)
                else:
                    _LOGGER.debug(
                        "Register scan completed full range | module=%s sent=%d",
                        normalized_address,
                        registers_sent,
                    )
            finally:
                self._scan_active = False
                self._scan_trailer_seen = False

    async def _read_register_once(
        self,
        command: str,
        reg: int,
        module_address: str,
        listener,
        connection,
    ) -> bool:
        """Send a single register-read and wait for ACK + optional data frame.

        Returns True when the ACK was observed (whether or not a data
        frame followed), False when all retries failed to see an ACK.
        """

        ack_prefix = f"$05{command[3:5]}"

        for attempt in range(MODULE_SCAN_RETRY_LIMIT + 1):
            # Drain any stale entries from the response queue — we are
            # the only consumer while _awaiting_response is set.
            while not listener.response_queue.empty():
                try:
                    listener.response_queue.get_nowait()
                    listener.response_queue.task_done()
                except asyncio.QueueEmpty:
                    break

            self._scan_event.clear()
            listener._awaiting_response = True
            try:
                try:
                    await connection.send(command)
                except Exception:
                    # Typically a transient "Not connected" during
                    # mid-scan reconnect cycles; the outer scan loop
                    # fast-fails cleanly. Keep at DEBUG.
                    _LOGGER.debug(
                        "Register scan send failed | module=%s reg=0x%02X attempt=%d",
                        module_address,
                        reg,
                        attempt + 1,
                        exc_info=True,
                    )
                    continue

                # Wait for the ACK that matches our command.
                ack_ok = await self._await_matching_ack(
                    listener.response_queue, ack_prefix
                )
                if not ack_ok:
                    _LOGGER.debug(
                        "Register scan ACK timeout | module=%s reg=0x%02X attempt=%d",
                        module_address,
                        reg,
                        attempt + 1,
                    )
                    continue

                # ACK in hand: wait briefly for an accompanying data frame.
                # Silence is valid for empty registers — don't treat it as
                # an error.
                try:
                    await asyncio.wait_for(
                        self._scan_event.wait(),
                        timeout=MODULE_SCAN_DATA_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    pass
                return True
            finally:
                listener._awaiting_response = False

        # Expected per-register behaviour on bank-incompatible sub-bytes
        # (handled by the outer fast-fail). Keep at DEBUG to avoid
        # cluttering the integration log.
        _LOGGER.debug(
            "Register scan gave up on register | module=%s reg=0x%02X",
            module_address,
            reg,
        )
        # The ACK+data for this register can still arrive moments later.
        # If we leave stale bytes in the payload buffer or in the response
        # queue, subsequent registers will decode against the wrong
        # remainder (one-register drift producing phantom records).
        # Flush both so the next register starts from a clean slate.
        self._payload_buffer = ""
        while not listener.response_queue.empty():
            try:
                listener.response_queue.get_nowait()
                listener.response_queue.task_done()
            except asyncio.QueueEmpty:
                break
        return False

    @staticmethod
    async def _await_matching_ack(queue, ack_prefix: str) -> bool:
        """Drain the response queue until an ACK with ``ack_prefix`` is seen.

        Returns False if ``MODULE_SCAN_ACK_TIMEOUT`` elapses with no match.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + MODULE_SCAN_ACK_TIMEOUT
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return False
            try:
                queue.task_done()
            except ValueError:
                pass
            if isinstance(msg, str) and msg.startswith(ack_prefix):
                return True

    def _is_known_module_address(self, address: str | None) -> bool:
        normalized = (address or "").upper()
        return any(
            normalized in modules for modules in self._coordinator.dict_module_data.values()
        )

    def _cancel_timeout(self) -> None:
        if self._timeout_task:
            if asyncio.current_task() is not self._timeout_task:
                self._timeout_task.cancel()
            self._timeout_task = None

    def _cancel_inventory_timeout(self) -> None:
        if self._inventory_timeout_task:
            if asyncio.current_task() is not self._inventory_timeout_task:
                self._inventory_timeout_task.cancel()
            self._inventory_timeout_task = None

    def _schedule_timeout(self) -> None:
        self._cancel_timeout()
        module_address = self._module_address
        self._timeout_task = self._create_task(
            self._timeout_after(module_address)
        )

    def _schedule_inventory_timeout(self) -> None:
        self._cancel_inventory_timeout()
        self._inventory_timeout_task = self._create_task(
            self._inventory_timeout_after()
        )

    async def _check_early_termination(self, address: str, had_data: bool) -> bool:
        """Track consecutive empty module inventory responses for logging.

        Early termination is disabled because roller/shutter modules have
        sparsely-programmed registers — button links are spread across the
        full register range (0x10-0xFF) with large gaps between them.  The
        full scan (~36 s per module) is acceptable for a one-time discovery.

        Always returns ``False`` so the caller continues scanning.
        """
        if had_data:
            self._module_found_data = True
            self._module_consecutive_empties = 0
        else:
            if self._module_found_data:
                self._module_consecutive_empties += 1

        return False

    async def _timeout_after(self, module_address: str | None) -> None:
        try:
            await asyncio.sleep(self._module_timeout_seconds)
        except asyncio.CancelledError:
            return
        await self._finalize_discovery(module_address)

    async def _inventory_timeout_after(self) -> None:
        try:
            await asyncio.sleep(self._inventory_timeout_seconds)
        except asyncio.CancelledError:
            return

        try:
            await self._finalize_inventory_phase()
        except Exception as err:
            _LOGGER.error("CRITICAL ERROR in _finalize_inventory_phase: %s", err, exc_info=True)
            self.reset_state()

    async def _emit_progress(
        self,
        phase: str,
        *,
        module_address: str | None = None,
        register: int | None = None,
    ) -> None:
        """Invoke the caller-supplied ``on_progress`` callback (if any).

        The callback is optional, runs asynchronously, and must not be
        allowed to abort the scan if it raises — log and swallow.
        """

        callback = self._on_progress
        if callback is None:
            return
        progress = DiscoveryProgress(
            phase=phase,
            module_address=module_address,
            module_index=self._progress_module_index,
            module_total=self._progress_module_total,
            register=register,
            register_total=self._progress_register_total,
            registers_sent=self._progress_module_registers_sent,
            pass_index=self._progress_pass_index,
            pass_total=self._progress_pass_total,
            sub_byte=self._progress_current_sub_byte,
            decoded_records=self._progress_decoded_records,
        )
        try:
            result = callback(progress)
            if inspect.isawaitable(result):
                await result
        except Exception:
            _LOGGER.warning(
                "Discovery on_progress callback raised; continuing scan",
                exc_info=True,
            )

    def _reset_module_context(self) -> None:
        self._payload_buffer = ""
        self._module_address = None
        self._module_type = None
        self._module_channels = None
        self._module_found_data = False
        self._module_consecutive_empties = 0
        self._scan_response_index = 0
        # Re-arm the alt-alignment skip-pending counter on every
        # decoder so the next per-module scan that picks one up
        # starts from a clean slate (no carry from a prior module).
        for decoder in getattr(self, "_decoders", []):
            reset = getattr(decoder, "reset_scan_buffers", None)
            if reset is not None:
                reset()

    async def _finalize_discovery(self, module_address: str | None = None) -> None:
        self._cancel_timeout()
        resolved_address = (
            module_address
            or self._module_address
            or self._coordinator.discovery_module_address
        )
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None
        self._reset_module_context()

        if self.discovery_stage == "register_scan" and self._register_scan_queue:
            # Let the bus breathe before scanning the next module
            await asyncio.sleep(1.0)
            await self._start_next_register_scan()
            return

        await self._complete_discovery_run(resolved_address)

    # Marker strings that flag synthesized input-module children in
    # ``discovered_devices`` and in the button store's ``type`` field.
    # HA-side display code keys off the ``pc_logic_parent_address``
    # provenance field to render each as ``LM-INPUT N`` parented
    # under the owning module device.
    PC_LOGIC_INPUT_TYPE: str = "PC-Logic Logical Input"
    PC_LOGIC_INPUT_MODEL: str = "05-201"
    INTERFACE_MODULE_INPUT_TYPE: str = "Modular Interface Input"
    INTERFACE_MODULE_INPUT_MODEL: str = "05-206"

    # Module types whose 6 inputs are synthesised by the library
    # (firmware-computed addresses, not enumerated in $1011). Both
    # use the same derivation formula in ``protocol.py`` —
    # validated empirically for ``pc_logic`` on two installs (940C,
    # 8DC8); applied to ``interface_module`` on the same-shape
    # hypothesis pending hardware confirmation.
    _INPUT_MODULE_SYNTHESIS_TYPES: frozenset[str] = frozenset(
        {"pc_logic", "interface_module"}
    )

    def _synthesize_pc_logic_inputs(self) -> None:
        """Add virtual-button entries for each input module's children.

        Iterates ``self.discovered_devices`` for ``pc_logic`` and
        ``interface_module`` modules, computes the bus addresses their
        inputs emit (via ``derive_pc_logic_input_physicals``), and
        synthesises a 2-channel ``category="Button"`` entry per input.
        The standard merge layer then writes them into the button
        store with one operation point per key (1A primary, 1B alias
        per ``PC_LOGIC_KEY_MAPPING``).

        The synthesis-time module-type → description/model mapping:

          * ``pc_logic`` → ``PC-Logic Logical Input`` / ``05-201``
          * ``interface_module`` → ``Modular Interface Input`` / ``05-206``

        Idempotent: re-running merges into existing entries cleanly
        via ``merge_discovered_buttons``'s upsert semantics. Kept
        named ``_synthesize_pc_logic_inputs`` for callsite stability.
        """

        type_metadata = {
            "pc_logic": (
                self.PC_LOGIC_INPUT_TYPE,
                self.PC_LOGIC_INPUT_MODEL,
            ),
            "interface_module": (
                self.INTERFACE_MODULE_INPUT_TYPE,
                self.INTERFACE_MODULE_INPUT_MODEL,
            ),
        }

        new_entries: dict[str, dict] = {}
        for module_addr, device in self.discovered_devices.items():
            if not isinstance(device, dict):
                continue
            module_type = device.get("module_type")
            if module_type not in self._INPUT_MODULE_SYNTHESIS_TYPES:
                continue
            channels_count = int(device.get("channels_count") or 0)
            if channels_count <= 0:
                # Catalogue should always provide 6 for 05-201/05-206;
                # defend against future variants with zero channels.
                continue
            try:
                input_physicals = derive_pc_logic_input_physicals(
                    module_addr, channels_count
                )
            except ValueError as err:
                _LOGGER.warning(
                    "Could not derive %s inputs for %s (%s); "
                    "inputs will not be surfaced for this module.",
                    module_type,
                    module_addr,
                    err,
                )
                continue

            description, model = type_metadata[module_type]
            for slot_index, input_phys in enumerate(input_physicals, start=1):
                if input_phys in self.discovered_devices:
                    # Real button already discovered at this address —
                    # don't shadow it. Vanishingly unlikely in practice
                    # (the synthesised range is firmware-reserved) but
                    # guard anyway.
                    _LOGGER.debug(
                        "%s input slot %d address %s already in "
                        "inventory — skipping synthesis",
                        module_type,
                        slot_index,
                        input_phys,
                    )
                    continue
                new_entries[input_phys] = {
                    "description": description,
                    "discovered_name": description,
                    "category": "Button",
                    "device_type": "LM",  # synthetic marker, not a real DEVICE_TYPES byte
                    "model": model,
                    "address": input_phys,
                    "channels": 2,
                    "channels_count": 2,
                    "module_type": "other_module",
                    "discovered": True,
                    # Provenance tags the HA-side renderer keys off to
                    # parent the synthesized device under the owning
                    # module (instead of the wall-buttons category)
                    # and to render the friendly ``LM-INPUT N`` name.
                    # ``pc_logic_parent_address`` retained as the field
                    # name for HA-callsite compatibility — same shape
                    # for both pc_logic and interface_module parents.
                    "pc_logic_parent_address": module_addr,
                    "pc_logic_parent_type": module_type,
                    "pc_logic_slot_index": slot_index,
                }
                _LOGGER.info(
                    "Synthesized %s input | parent=%s slot=%d "
                    "address=%s",
                    module_type,
                    module_addr,
                    slot_index,
                    input_phys,
                )

        self.discovered_devices.update(new_entries)

    # Minimum cluster size that qualifies as a "virtual transmitter".
    # Set to 8 (one 8-channel button's worth of A/B/C/D × 2 keys) so
    # we catch unenrolled multi-key remotes and large keypads while
    # still ignoring random small coincidences in flash garbage.
    REMOTE_TRANSMITTER_CLUSTER_THRESHOLD: int = 8

    # Prefix prepended to a 4-hex suffix to form the synthetic
    # ``physical address`` of a virtual transmitter parent. The full
    # synthetic ID is e.g. ``RT-E31C`` — used as the via_device
    # identifier so HA can group all 52 emitted codes under one
    # parent device.
    REMOTE_TRANSMITTER_PREFIX: str = "RT-"

    def _synthesize_remote_transmitters_from_unmatched(self) -> None:
        """Synthesise virtual transmitters from clusters of unmatched
        button references collected across module scans.

        A "cluster" is N >= threshold button addresses sharing the
        last 4 hex characters (the low 16 bits). Each cluster gets:

          * A virtual transmitter parent entry in
            ``self.discovered_devices`` (synthetic key
            ``RT-<suffix>``, ``category="Module"``,
            ``module_type="remote_transmitter"``).
          * One passthrough button child per cluster member, keyed
            in the button store by the observed bus address itself.
            ``merge_discovered_buttons`` has a passthrough branch
            for entries carrying ``remote_transmitter_address`` —
            it writes the op-point's ``bus_address`` directly from
            the synthesis entry, skipping the
            ``convert_nikobus_address`` round-trip (which isn't a
            true bijection for all 24-bit values).

        After populating ``discovered_devices``, the caller is
        expected to re-run ``merge_discovered_buttons`` so the new
        entries land in the button store, then re-run
        ``merge_linked_modules`` with the accumulated command
        mapping to wire up the previously-unmatched link records.
        """

        if not self._accumulated_unmatched:
            _LOGGER.info(
                "Remote-transmitter synthesis | accumulator empty — no "
                "unmatched references were collected during this scan "
                "(either no scan ran or every BP-cell reference resolved "
                "to an existing button-store entry)."
            )
            return

        # Cluster by last 4 hex chars.
        clusters: dict[str, list[str]] = {}
        for addr in self._accumulated_unmatched:
            if len(addr) < 4:
                continue
            suffix = addr[-4:].upper()
            clusters.setdefault(suffix, []).append(addr.upper())

        # Always log what we found, even when no cluster meets the
        # threshold — diagnosing "synthesis didn't fire" is impossible
        # without visibility into the accumulator contents and the
        # cluster shape we computed.
        cluster_summary = sorted(
            ((suffix, len(members)) for suffix, members in clusters.items()),
            key=lambda item: -item[1],
        )
        sample_addresses = sorted(self._accumulated_unmatched)[:20]
        _LOGGER.info(
            "Remote-transmitter synthesis | accumulator_size=%d "
            "unique_suffixes=%d threshold=%d top_clusters=%s "
            "sample_addresses=%s",
            len(self._accumulated_unmatched),
            len(clusters),
            self.REMOTE_TRANSMITTER_CLUSTER_THRESHOLD,
            cluster_summary[:10],
            sample_addresses,
        )

        new_entries: dict[str, dict] = {}
        for suffix, members in clusters.items():
            if len(members) < self.REMOTE_TRANSMITTER_CLUSTER_THRESHOLD:
                continue

            transmitter_id = f"{self.REMOTE_TRANSMITTER_PREFIX}{suffix}"
            new_entries[transmitter_id] = {
                "category": "Module",
                "module_type": "remote_transmitter",
                "model": "RF Remote (synthesized)",
                "description": f"Remote Transmitter ({suffix})",
                "address": transmitter_id,
                "channels": 0,
                "channels_count": 0,
                "transmitter_suffix": suffix,
                "transmitter_member_count": len(members),
                "discovered": True,
            }

            for bus_address in sorted(set(members)):
                if bus_address in self.discovered_devices:
                    # A real button enrolled later in the scan
                    # already covers this address — don't shadow.
                    continue
                new_entries[bus_address] = {
                    "category": "Button",
                    "device_type": "RT",  # synthetic marker
                    "model": "Remote Code",
                    "description": f"Remote {bus_address}",
                    "discovered_name": f"Remote {bus_address}",
                    "address": bus_address,
                    "channels": 1,
                    "channels_count": 1,
                    "module_type": "other_module",
                    "discovered": True,
                    # The merge layer keys off
                    # ``remote_transmitter_address`` to use the
                    # passthrough branch: op-point bus_address is
                    # set from ``remote_transmitter_bus_address``
                    # directly. HA-side renderer parents the device
                    # under the synthetic transmitter via the same
                    # provenance field.
                    "remote_transmitter_address": transmitter_id,
                    "remote_transmitter_suffix": suffix,
                    "remote_transmitter_bus_address": bus_address,
                }

            _LOGGER.info(
                "Synthesized remote transmitter | suffix=%s "
                "member_count=%d transmitter_id=%s",
                suffix,
                len(members),
                transmitter_id,
            )

        if new_entries:
            self.discovered_devices.update(new_entries)

    async def _finalize_inventory_phase(self) -> None:
        """Finalize the PC-Link inventory phase."""
        self._cancel_inventory_timeout()
        _LOGGER.debug("Entering _finalize_inventory_phase. Stage: %s", self.discovery_stage)

        # Stage 1: we have inventory addresses but haven't queued identity/register queries yet
        if self.discovery_stage == "inventory_addresses" and self._inventory_addresses:
            pending_addresses = self._inventory_addresses - self._inventory_identity_queued
            if pending_addresses:
                _LOGGER.debug("Found pending inventory addresses, queuing identity queries.")
                await self._run_inventory_identity_queries(pending_addresses)
                self._inventory_identity_queued.update(pending_addresses)
                self.discovery_stage = "inventory_identity"
                self._schedule_inventory_timeout()
                return
            else:
                _LOGGER.debug("No pending inventory addresses. Moving directly to Stage 2.")
                self.discovery_stage = "inventory_identity"

        # Stage 1.6: synthesise PC-Logic logical-input "virtual buttons".
        # PC-Logic doesn't enumerate its inputs in PC-Link's $1011
        # inventory frames — the input bus addresses are computed by
        # firmware from the PC-Logic's own module address (see
        # ``derive_pc_logic_input_physicals`` in protocol.py for the
        # formula). Add them to ``discovered_devices`` as
        # ``category="Button"`` entries so the regular merge layer
        # writes them into the button store as 2-channel virtual
        # buttons, parented under the PC-Logic module.
        self._synthesize_pc_logic_inputs()

        # Stage 2: inventory complete -> persist results
        _LOGGER.debug("Starting updates for module and button data.")
        try:
            if self._module_data is not None:
                merge_discovered_modules(
                    self._module_data, self.discovered_devices
                )
                _LOGGER.debug("Finished merge_discovered_modules.")
                if self._on_module_save is not None:
                    await self._on_module_save()
                    _LOGGER.debug("Finished on_module_save callback.")
            if self._button_data is not None:
                merge_discovered_buttons(
                    self._button_data,
                    self.discovered_devices,
                    KEY_MAPPING,
                    convert_nikobus_address,
                )
                _LOGGER.debug("Finished merge_discovered_buttons.")
                if self._on_button_save is not None:
                    await self._on_button_save()
                    _LOGGER.debug("Finished on_button_save callback.")
        except Exception:
            _LOGGER.error("Error during inventory finalization", exc_info=True)
            raise

        _LOGGER.info(
            "PC Link inventory scan finished | discovered=%d",
            len(self.discovered_devices),
        )

        _LOGGER.debug(
            "DUMP OF DISCOVERED DEVICES:\n%s",
            json.dumps(self.discovered_devices, indent=2)
        )

        _LOGGER.info(
            "PC Link inventory phase completed. Module discovery is manual; stopping here."
        )

        # End discovery here (do not chain into register_scan automatically)
        await self._complete_discovery_run(None)
        return

    async def _run_inventory_identity_queries(self, addresses: set[str]) -> None:
        # The identity phase reads register 0xA0..0xFF (96 regs) on
        # each address discovered during PHASE_INVENTORY. Surface a
        # per-address total + cumulative counter so the HA progress
        # bar tracks the actual work being done — otherwise consumers
        # fall back to a stale ``register_total`` from the previous
        # phase (typically 0) and either freeze or fly off-scale.
        sorted_addresses = sorted(addresses)
        identity_range = range(0xA0, 0x100)
        per_address_total = len(identity_range)

        self._progress_module_index = 0
        self._progress_module_total = len(sorted_addresses)
        self._progress_module_register_total = per_address_total
        self._progress_register_total = per_address_total
        self._progress_module_registers_sent = 0
        self._progress_pass_index = 1
        self._progress_pass_total = 1
        self._progress_current_sub_byte = "04"
        await self._emit_progress(PHASE_IDENTITY)

        for index, address in enumerate(sorted_addresses, start=1):
            bus_order_address = address[2:4] + address[:2]

            _LOGGER.debug(
                "PC Link inventory enumeration starting | address=%s bus=%s",
                address,
                bus_order_address,
            )

            # Reset per-address cumulative counter so each address's
            # progress bar starts at 0/96 rather than carrying over the
            # previous address's count.
            self._progress_module_index = index
            self._progress_module_registers_sent = 0

            for reg in identity_range:
                payload = f"10{bus_order_address}{reg:02X}04"
                pc_link_command = make_pc_link_inventory_command(payload)

                _LOGGER.debug(
                    "PC Link inventory key queued | address=%s bus=%s reg=%02X",
                    address,
                    bus_order_address,
                    reg,
                )
                await self._coordinator.nikobus_command.queue_command(pc_link_command)
                self._progress_module_registers_sent += 1
                await self._emit_progress(
                    PHASE_IDENTITY,
                    module_address=address,
                    register=reg,
                )

        # Reset per-module pass tracking so subsequent phases don't
        # carry stale identity-phase state.
        self._progress_pass_index = 0
        self._progress_pass_total = 0
        self._progress_current_sub_byte = None

    async def _start_next_register_scan(self) -> None:
        if not self._register_scan_queue:
            _LOGGER.info("All modules in queue have been scanned.")
            await self._complete_discovery_run(None)
            return

        next_module = self._register_scan_queue.pop(0)
        normalized_address = self.normalize_module_address(
            next_module, source="register_scan_queue"
        )
        _LOGGER.info(
            "Discovery started | module=%s (Remaining in queue: %d)",
            normalized_address,
            len(self._register_scan_queue)
        )
        # Reset per-module state so the next queued module is re-classified
        # from scratch. Otherwise _module_type carries over from the previous
        # scan and the wrong decoder runs on the current module's data.
        self._module_type = None
        self._module_channels = None
        self._module_found_data = False
        self._module_consecutive_empties = 0
        self._scan_response_index = 0
        # Reset per-module progress counters. The vendor plan reads
        # different totals per module type (e.g. switch = 48 vendor regs,
        # PC-Link = 93 inventory regs), so each module's progress bar
        # needs to start from 0/0 and rebuild its target.
        self._progress_module_register_total = 0
        self._progress_module_registers_sent = 0
        self._progress_pass_index = 0
        self._progress_pass_total = 0
        self._progress_current_sub_byte = None
        self._coordinator.discovery_running = True
        self._coordinator.discovery_module = True
        self._coordinator.discovery_module_address = normalized_address
        self._progress_module_index += 1
        await self._emit_progress(
            PHASE_REGISTER_SCAN, module_address=normalized_address
        )
        await self.query_module_inventory(normalized_address, from_queue=True)

    async def _complete_discovery_run(self, resolved_address: str | None) -> None:
        self._cancel_inventory_timeout()
        _LOGGER.info("Discovery finished")
        await self._emit_progress(PHASE_FINALIZING)

        # Cluster-synthesis pass for unmatched references collected
        # across the per-module scans. Multi-page Easywave remotes
        # emit dozens of distinct bus codes from one physical
        # transmitter, none of which appear in PC-Link inventory.
        # The decoders see them in module BP cells, the merge layer
        # logs them as unmatched and skips the link record. Here we
        # cluster the unmatched set by 4-hex suffix, synthesise a
        # virtual transmitter parent + passthrough children for any
        # cluster meeting the threshold, and re-run the merges so
        # the previously-skipped link records resolve.
        if self._button_data is not None and self._accumulated_unmatched:
            pre_synth_count = len(self.discovered_devices)
            self._synthesize_remote_transmitters_from_unmatched()
            synthesised = len(self.discovered_devices) - pre_synth_count
            if synthesised:
                try:
                    merge_discovered_buttons(
                        self._button_data,
                        self.discovered_devices,
                        KEY_MAPPING,
                        convert_nikobus_address,
                    )
                    # Re-run link merge with the accumulated
                    # command_mapping so the previously-unmatched
                    # records resolve to the newly-synthesised
                    # children. dedup in merge_linked_modules keeps
                    # already-resolved entries idempotent.
                    (
                        updated_buttons,
                        links_added,
                        outputs_added,
                        _residual_unmatched,
                    ) = merge_linked_modules(
                        self._button_data,
                        self._accumulated_command_mapping,
                    )
                    _LOGGER.info(
                        "Remote-transmitter cluster synthesis | "
                        "new_devices=%d updated_buttons=%d "
                        "links_added=%d outputs_added=%d "
                        "residual_unmatched=%d",
                        synthesised,
                        updated_buttons,
                        links_added,
                        outputs_added,
                        len(_residual_unmatched),
                    )
                    if self._on_button_save is not None:
                        await self._on_button_save()
                except Exception:  # pragma: no cover - defensive
                    _LOGGER.exception(
                        "Remote-transmitter post-synthesis merge failed"
                    )

        # Capture state for the callback's kwargs (Bug 1 fix per
        # Nikobus-HA #319). The consumer's callback runs in the same
        # async flow and may re-enter the library (e.g., to call
        # ``detect_stale_inventory``); we don't want it racing with
        # us clearing instance state.
        captured_devices = dict(self.discovered_devices)
        captured_query_type = getattr(
            self._coordinator, "inventory_query_type", None
        )

        # Split reset: flip the "discovery in progress" flags BEFORE
        # the callback so consumer re-entry into the library is
        # unguarded. Per fdebrus's design note: the integration's
        # post-discovery reconciliation calls back into
        # ``detect_stale_inventory`` from within this callback, and
        # ``discovery_running=True`` would trip any "already running"
        # guard the consumer adds.
        self._coordinator.discovery_running = False
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None

        try:
            await _notify_discovery_finished(
                self,
                discovered_devices=captured_devices,
                inventory_query_type=captured_query_type,
            )
        finally:
            # Clear instance state AFTER the callback returns. By
            # this point the consumer has either snapshotted what it
            # needed (via the kwargs) or completed any synchronous
            # work it wanted to do.
            self.discovered_devices = {}
            self._coordinator.inventory_query_type = None
            # Internal scan state (payload buffer, register queue,
            # etc.) — flags already flipped above, so don't re-flip.
            self.reset_state(update_flags=False)

    async def start_inventory_discovery(self):
        self.reset_state(update_flags=False)
        self.discovered_devices = {}
        self.discovery_stage = "inventory_addresses"
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None
        self._coordinator.discovery_running = True
        self._coordinator.inventory_query_type = InventoryQueryType.PC_LINK
        self._progress_module_index = 0
        self._progress_module_total = 0
        self._progress_register_total = 0
        self._progress_decoded_records = 0
        # PHASE_INVENTORY is the ``#A`` bus broadcast — one round-trip,
        # not a register-by-register scan. Surface it as a single unit
        # of work so the HA progress bar shows determinate progress
        # rather than a misleading "0 / 240"-style fallback.
        self._progress_module_register_total = 1
        self._progress_register_total = 1
        self._progress_module_registers_sent = 0
        self._progress_pass_index = 1
        self._progress_pass_total = 1
        self._progress_current_sub_byte = None
        _LOGGER.info("PC Link inventory enumeration started")
        _LOGGER.debug("Queueing PC Link inventory command #A")
        await self._coordinator.nikobus_command.queue_command("#A")
        # Mark the single unit as in-flight so the bar leaves 0 once
        # the command is on the wire. Completion is signalled when
        # PHASE_IDENTITY takes over.
        self._progress_module_registers_sent = 1
        self._schedule_inventory_timeout()
        await self._emit_progress(PHASE_INVENTORY)

    # Output-bearing module types: only these respond predictably to
    # the ``$1012<addr>`` status query that ``detect_stale_inventory``
    # uses as a presence probe. PC Link / PC Logic / feedback / audio
    # / modular interface either ARE the bridge or don't respond
    # uniformly, so they're excluded from the probe pass.
    _BUS_PROBE_MODULE_TYPES: frozenset[str] = frozenset({
        "switch_module",
        "dimmer_module",
        "roller_module",
    })

    async def detect_stale_inventory(
        self,
        *,
        outer_attempts: int = 1,
        outer_delay: float = 0.0,
    ) -> dict[str, list[str]]:
        """Cross-check Module-category entries against bus presence.

        Use case: a user with a second-hand PC-Link sees records from
        the previous owner's installation in their inventory dump.
        Niko's PC software writes new programming on top of old, but
        unused register slots aren't auto-zeroed, so any module /
        button records the new install doesn't overwrite stay present
        in PC-Link flash. There's no on-wire signal that distinguishes
        stale from current — the only reliable check is "does the
        device actually respond on the bus?"

        For each output-bearing module address in
        ``coordinator.dict_module_data``, call ``get_output_state``,
        which is the standard ``$1012<addr>`` query. That call's
        internal retry policy (``MAX_ATTEMPTS=3`` with 5 s per
        attempt) IS the per-module retry budget — present modules
        get up to 3 wire attempts to ACK, absent modules consume
        ~15 s of processor time before classifying as absent.

        Probes run **serially** in queue order. A module slow to ACK
        does not starve subsequent probes (Bug 2 / Nikobus-HA #319
        regression: prior versions wrapped each probe in
        ``asyncio.wait_for`` with a 2 s outer cap, racing the queue's
        natural retry budget and false-negativing real modules whose
        first wire attempt got blocked behind an absent-module's
        15 s inner retry loop).

        Buttons aren't probed directly (they only emit on press), but
        when a button's ``linked_modules`` block points only at stale
        modules, it's flagged as orphaned — the link table says it
        drives nothing real. Buttons with no ``linked_modules`` at
        all are NOT flagged: discovery may not have reached them yet,
        or they may genuinely have no programmed routing today.

        Returns a manifest the caller (typically the HA integration)
        decides what to do with — surface in UI, auto-purge, etc. The
        library doesn't mutate the persisted stores; the caller does.

        Args:
            outer_attempts: Number of full-sweep passes over all
                yet-unclassified modules. Defaults to 1 (single
                pass — the command layer's own retry budget is
                usually enough). Each outer pass re-probes every
                module not yet classified ``present``; modules
                that ACK'd on an earlier pass are skipped. Set to 2
                or more to give modules an extra chance after the
                bus has had a chance to quiesce — useful on heavily
                loaded installs where a transient bus jam can cause
                all 3 inner wire attempts to land in the same busy
                window.
            outer_delay: Sleep between outer passes, in seconds.
                Defaults to 0.0. Skipped after the final pass. Use
                in tandem with ``outer_attempts`` to let bus
                contention clear between probe rounds — e.g.
                ``outer_attempts=2, outer_delay=3.0``.

        Returns:
            Dict with four lists, all sorted, addresses upper-case:
              - ``checked``: every address probed
              - ``present_modules``: probes that ACK'd
              - ``absent_modules``: probes that failed
              - ``orphaned_buttons``: buttons whose entire
                ``linked_modules`` set sits inside ``absent_modules``
        """

        empty: dict[str, list[str]] = {
            "checked": [],
            "present_modules": [],
            "absent_modules": [],
            "orphaned_buttons": [],
        }

        nikobus_command = getattr(self._coordinator, "nikobus_command", None)
        if nikobus_command is None or not hasattr(
            nikobus_command, "get_output_state"
        ):
            _LOGGER.warning(
                "detect_stale_inventory: coordinator has no nikobus_command "
                "with get_output_state; returning empty manifest"
            )
            return empty

        addresses: list[str] = []
        bucket = getattr(self._coordinator, "dict_module_data", {}) or {}
        if isinstance(bucket, dict):
            for module_type, modules in bucket.items():
                if module_type not in self._BUS_PROBE_MODULE_TYPES:
                    continue
                if isinstance(modules, dict):
                    for addr in modules:
                        if addr:
                            addresses.append(str(addr).upper())
                elif isinstance(modules, list):
                    for entry in modules:
                        if isinstance(entry, dict) and entry.get("address"):
                            addresses.append(str(entry["address"]).upper())

        addresses = sorted(set(addresses))

        present_set: set[str] = set()
        absent_last_reason: dict[str, str] = {}

        outer_passes = max(1, int(outer_attempts))
        outer_pause = max(0.0, float(outer_delay))

        for outer in range(1, outer_passes + 1):
            remaining = [a for a in addresses if a not in present_set]
            if not remaining:
                break

            for addr in remaining:
                # No outer ``asyncio.wait_for`` here — see docstring.
                # ``get_output_state`` waits the command layer's own
                # natural retry budget (MAX_ATTEMPTS × per-attempt
                # timeout) then either returns or raises. The queue
                # is held for the full duration of each probe so
                # commands don't race each other into stale-future
                # territory.
                try:
                    await nikobus_command.get_output_state(addr, group=1)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    absent_last_reason[addr] = type(exc).__name__
                else:
                    _LOGGER.debug(
                        "Bus presence probe | addr=%s status=present "
                        "outer=%d/%d",
                        addr,
                        outer,
                        outer_passes,
                    )
                    present_set.add(addr)
                    absent_last_reason.pop(addr, None)

            # Pause before the next outer pass (only if more passes
            # remain AND at least one module is still unclassified).
            if outer < outer_passes and outer_pause > 0:
                still_remaining = [a for a in addresses if a not in present_set]
                if still_remaining:
                    await asyncio.sleep(outer_pause)

        present: list[str] = sorted(present_set)
        absent: list[str] = []
        for addr in addresses:
            if addr in present_set:
                continue
            reason = absent_last_reason.get(addr, "timeout")
            _LOGGER.info(
                "Bus presence probe | addr=%s status=absent reason=%s "
                "outer_passes=%d",
                addr,
                reason,
                outer_passes,
            )
            absent.append(addr)

        orphaned: list[str] = []
        if absent and self._button_data is not None:
            absent_set = set(absent)
            buttons = self._button_data.get("nikobus_button") or {}
            entries: list[tuple[str, dict]] = []
            if isinstance(buttons, dict):
                for phys_addr, button in buttons.items():
                    if isinstance(button, dict):
                        entries.append((str(phys_addr), button))
            elif isinstance(buttons, list):
                for button in buttons:
                    if isinstance(button, dict) and button.get("address"):
                        entries.append((str(button["address"]), button))

            for phys_addr, button in entries:
                target_addrs: set[str] = set()
                op_points = button.get("operation_points") or {}
                if not isinstance(op_points, dict):
                    continue
                for op in op_points.values():
                    if not isinstance(op, dict):
                        continue
                    links = op.get("linked_modules") or []
                    if not isinstance(links, list):
                        continue
                    for block in links:
                        if (
                            isinstance(block, dict)
                            and block.get("module_address")
                        ):
                            target_addrs.add(
                                str(block["module_address"]).upper()
                            )
                if target_addrs and target_addrs.issubset(absent_set):
                    orphaned.append(phys_addr.upper())

        manifest = {
            "checked": addresses,
            "present_modules": sorted(present),
            "absent_modules": sorted(absent),
            "orphaned_buttons": sorted(set(orphaned)),
        }

        _LOGGER.info(
            "Stale-inventory probe complete | checked=%d present=%d "
            "absent=%d orphaned_buttons=%d",
            len(manifest["checked"]),
            len(manifest["present_modules"]),
            len(manifest["absent_modules"]),
            len(manifest["orphaned_buttons"]),
        )

        return manifest

    def handle_device_address_inventory(self, message: str) -> None:
        # Signal the sequential scan loop first. A $18 frame that hits
        # this handler during a register scan is either an (unexpected)
        # address-inventory record or an end-of-memory trailer; either
        # way the scan loop needs to wake.
        self._notify_scan_frame(message)
        clean_message = message.strip("\x02\x03\r\n")
        marker_index = clean_message.find(DEVICE_ADDRESS_INVENTORY)
        if marker_index == -1:
            _LOGGER.debug(
                "Inventory record ignored | reason=missing_marker message=%s",
                message,
            )
            return
        start_index = marker_index + len(DEVICE_ADDRESS_INVENTORY)
        raw_address = (clean_message[start_index : start_index + 4] or "").upper()

        # Validate the signature byte before treating this as a PC-Link
        # response. Both PC-Link (0x50) and PC-Logic (0x40) reply to
        # the broadcast ``#A`` query; if PC-Logic wins the race, our
        # subsequent inventory reads would target the wrong controller
        # and come back empty. The signature byte is at payload offset
        # 1 (after the address bytes and a leading 0x00 padding byte):
        # ``$18 <addr_lo><addr_hi> 00 <sig> 0F 3F FF <crc>``.
        signature_hex = (
            clean_message[start_index + 6 : start_index + 8] or ""
        ).upper()
        try:
            signature_byte = int(signature_hex, 16) if signature_hex else None
        except ValueError:
            signature_byte = None
        if (
            signature_byte is not None
            and signature_byte != PC_LINK_INVENTORY_SIGNATURE_BYTE
        ):
            _LOGGER.warning(
                "Inventory record rejected | reason=non_pc_link_signature "
                "raw=%s signature=0x%02X (expected 0x%02X — PC-Link); "
                "this responder is most likely a PC-Logic answering #A "
                "before the PC-Link did. Verify a PC-Link (model 0A) "
                "is present on the bus.",
                raw_address,
                signature_byte,
                PC_LINK_INVENTORY_SIGNATURE_BYTE,
            )
            return

        normalized = self.normalize_module_address(
            raw_address, source="device_address_inventory", reverse_bus_order=True
        )
        is_new = normalized not in self._inventory_addresses
        self._inventory_addresses.add(normalized)
        _LOGGER.debug(
            "Inventory record | raw=%s normalized=%s", raw_address, normalized
        )
        _LOGGER.debug("Inventory record | address=%s", normalized)
        self._ensure_pc_link_address(normalized, source="device_address_inventory")
        if is_new and self.discovery_stage == "inventory_addresses":
            self._create_task(
                self._queue_inventory_identity_queries_for_address(normalized)
            )
        self._schedule_inventory_timeout()

    async def _queue_inventory_identity_queries_for_address(self, address: str) -> None:
        if address in self._inventory_identity_queued:
            return
        await self._run_inventory_identity_queries({address})
        self._inventory_identity_queued.add(address)

    def _ensure_pc_link_address(self, address: str, *, source: str) -> None:
        if not address:
            return

        existing = self.discovered_devices.get(address)
        if existing and existing.get("module_type") != "pc_link":
            _LOGGER.debug(
                "Skipping PC Link address record | address=%s reason=existing_module_type",
                address,
            )
            return

        coordinator_modules = getattr(self._coordinator, "dict_module_data", {}) or {}
        known_pc_links = coordinator_modules.get("pc_link") or {}
        if known_pc_links and address not in known_pc_links:
            _LOGGER.debug(
                "Skipping PC Link address record | address=%s reason=known_pc_link_present source=%s",
                address,
                source,
            )
            return

        pc_link_info = DEVICE_TYPES.get("0A", {})
        name = pc_link_info.get("Name", "PC-Link")
        model = pc_link_info.get("Model", "05-200")
        last_seen = datetime.now(timezone.utc).isoformat()
        module_type = get_module_type_from_device_type("0A")
        base_device = {
            "description": name,
            "discovered_name": name,
            "category": "Module",
            "device_type": "0A",
            "model": model,
            "address": address,
            "channels": 0,
            "channels_count": 0,
            "module_type": module_type,
            "discovered": True,
            "last_discovered": last_seen,
        }
        if existing:
            existing.update(base_device)
        else:
            self.discovered_devices[address] = base_device

        _LOGGER.debug(
            "PC Link address recorded | address=%s source=%s",
            address,
            source,
        )

    async def query_module_inventory(
        self,
        device_address,
        *,
        from_queue: bool = False,
        register_start: int | None = None,
        register_end: int | None = None,
        sub_byte: str | None = None,
    ):
        """Scan a module's register space.

        Production mode (no range params): pick the per-module-type
        register range from ``_scan_range_for_sub`` and run the
        configured extra passes from ``_EXTRA_SCAN_SUBS_BY_MODULE_TYPE``.
        Non-output module types (``feedback_module``, ``other_module``,
        ``interface_module``, ``audio_module``) early-return without
        scanning.

        Forensic mode (``register_start`` and ``register_end`` both
        provided): scan **only** the specified range with the given
        ``sub_byte`` (default ``"04"``). Skip the extra-pass logic and
        bypass the non-output-module guard so any module — including
        ones the production path declines to scan — can be inspected.
        Useful for reverse-engineering storage layouts: e.g.
        ``register_start=0x70, register_end=0x83, sub_byte="01"``
        targets the BP-cell region a vendor trace revealed.
        """

        if register_start is None and register_end is None:
            custom_range_mode = False
        elif register_start is None or register_end is None:
            raise ValueError(
                "query_module_inventory: register_start and register_end "
                "must both be provided (or both omitted)"
            )
        else:
            if not (0 <= register_start <= 0xFF):
                raise ValueError(
                    f"register_start 0x{register_start:X} out of range 0x00..0xFF"
                )
            if not (0 <= register_end <= 0xFF):
                raise ValueError(
                    f"register_end 0x{register_end:X} out of range 0x00..0xFF"
                )
            if register_end < register_start:
                raise ValueError(
                    f"register_end (0x{register_end:X}) must be >= "
                    f"register_start (0x{register_start:X})"
                )
            custom_range_mode = True

        if isinstance(device_address, str) and device_address.strip().upper() == "ALL":
            if custom_range_mode or sub_byte is not None:
                raise ValueError(
                    "query_module_inventory: register range / sub_byte "
                    "overrides are not compatible with ALL mode; supply "
                    "a specific module address"
                )
            all_addresses = []
            dict_data = getattr(self._coordinator, "dict_module_data", {})
            for module_type, modules in dict_data.items():
                if module_type not in NON_OUTPUT_MODULE_TYPES:
                    module_iter = modules.values() if isinstance(modules, dict) else modules
                    for module in module_iter:
                        addr = module.get("address") if isinstance(module, dict) else None
                        if addr:
                            all_addresses.append(addr)

            if not all_addresses:
                _LOGGER.warning(
                    "No output modules found in config to scan (dict_module_data keys=%s)",
                    list(dict_data.keys()) if isinstance(dict_data, dict) else type(dict_data).__name__,
                )
                self.reset_state()
                return

            _LOGGER.info("Starting sequential discovery queue for ALL output modules: %s", all_addresses)
            self.discovery_stage = "register_scan"
            self._register_scan_queue = all_addresses
            self._progress_module_total = len(all_addresses)
            self._progress_module_index = 0
            await self._start_next_register_scan()
            return

        normalized_address = self.normalize_module_address(
            device_address, source="query_module_inventory"
        )

        self.discovery_stage = self.discovery_stage or "register_scan"
        base_command = f"10{normalized_address}"
        self._module_address = normalized_address
        self._coordinator.inventory_query_type = InventoryQueryType.MODULE

        discovered_device = self.discovered_devices.get(normalized_address, {})

        if not self._coordinator.discovery_module:
            _LOGGER.info("Discovery started | module=%s", normalized_address)
            if not from_queue:
                self._coordinator.discovery_running = True
                # Single-module entry — seed progress for a queue of one.
                self._progress_module_total = 1
                self._progress_module_index = 1
                await self._emit_progress(
                    PHASE_REGISTER_SCAN, module_address=normalized_address
                )
            self._coordinator.discovery_module = True
            self._coordinator.discovery_module_address = normalized_address

        if self._module_type is None:
            self._module_type = self._resolve_module_type(
                normalized_address, discovered_device
            )

        # ``pc_logic`` is intentionally NOT in this set as of 0.4.11:
        # PC-Logic (05-201) holds the BP-cell connection table that
        # forwards button presses to output modules in heavily-routed
        # installs. Without scanning it, output-module flash records
        # that reference PC-Logic-synthesized addresses can't be
        # resolved and ``linked_modules`` ends up empty for those
        # buttons. The PcLogicDecoder is currently a logging stub
        # (Stage 1 instrumentation); see CHANGELOG 0.4.11.
        is_output_module = self._module_type not in NON_OUTPUT_MODULE_TYPES

        coordinator_channels = (
            self._coordinator.get_module_channel_count(normalized_address)
            if self._is_known_module_address(normalized_address)
            else 0
        )
        discovered_channels = discovered_device.get("channels")
        self._module_channels = next(
            (count for count in (coordinator_channels, discovered_channels) if count is not None),
            None,
        )

        # Per-module read uses function 0x10 ("read register"); the dimmer
        # uses 0x22 instead because its longer 8-byte records are returned
        # in a different response format. Address is byte-swapped on the
        # wire (Niko convention: little-endian module address).
        base_command = f"10{normalized_address[2:4] + normalized_address[:2]}"
        if self._module_type == "dimmer_module":
            base_command = f"22{normalized_address[2:4] + normalized_address[:2]}"

        # Forensic mode: user supplied an explicit register range.
        # Bypass the per-module-type tuning and the non-output-module
        # guard, scan exactly the range they asked for, and stop.
        if custom_range_mode:
            effective_sub = (sub_byte or "04").upper()
            forensic_range = range(register_start, register_end + 1)
            _LOGGER.info(
                "Forensic register scan | module=%s function=%s sub=%s "
                "range=0x%02X..0x%02X (custom range mode — extra passes skipped)",
                normalized_address,
                base_command[:2],
                effective_sub,
                forensic_range.start,
                forensic_range.stop - 1,
            )
            await self._scan_module_registers(
                normalized_address,
                base_command,
                forensic_range,
                sub_byte=effective_sub,
            )
            await self._finalize_discovery(normalized_address)
            return

        if not is_output_module:
            _LOGGER.debug(
                "Skipping register scan for non-output module | module=%s type=%s",
                normalized_address,
                self._module_type,
            )
            if self.discovery_stage == "inventory":
                return

            await self._finalize_discovery(normalized_address)
            return

        # 0.16.0 vendor-aligned scan plan: for every output module AND
        # PC-Logic, scan the exact (sub=00, sub=01, sub=04) register
        # lists the PC software reads per the 2026-05-08 trace. No
        # per-firmware widening — full vendor alignment.
        #
        # PC-Link's scan profile is different (controller-side module
        # registry, not per-module link table); it's handled in the
        # ``_PC_LINK_REGISTERS`` branch above.
        #
        # When ``broad_scan=True`` is set on this discovery instance,
        # the plan appends the legacy sub=04 0x00..0x3F sweep as an
        # extra pass after the vendor primary — safety net for any
        # firmware revision whose link table doesn't sit in the
        # vendor band.
        scan_passes = _scan_passes_for_module_type(
            self._module_type, broad_scan=self._broad_scan
        )
        if not scan_passes:
            # Module type not in the per-product plan (audio/interface/other
            # have no link table). Fall through to finalize.
            await self._finalize_discovery(normalized_address)
            return

        # Compute cumulative register total across all passes for this
        # module — surfaced to ``on_progress`` so the UI can show
        # one bar per module rather than one bar per pass.
        module_register_total = sum(len(regs) for _sub, regs in scan_passes)
        self._progress_module_register_total = module_register_total
        self._progress_module_registers_sent = 0
        self._progress_pass_total = len(scan_passes)
        self._progress_pass_index = 0

        for pass_index, (sub_byte, register_list) in enumerate(
            scan_passes, start=1
        ):
            function_code = base_command[:2]
            wire_sub = _wire_sub_byte(sub_byte)
            self._progress_pass_index = pass_index
            self._progress_current_sub_byte = wire_sub
            _LOGGER.debug(
                "Register scan pass starting | module=%s function=%s "
                "sub=%s wire_sub=%s registers=%d pass=%d/%d",
                normalized_address,
                function_code,
                sub_byte,
                wire_sub,
                len(register_list),
                pass_index,
                len(scan_passes),
            )
            await self._scan_module_registers(
                normalized_address,
                base_command,
                register_list,
                sub_byte=wire_sub,
            )

        # Clear per-module pass tracking so the finalize event doesn't
        # carry stale pass info from the last pass.
        self._progress_pass_index = 0
        self._progress_pass_total = 0
        self._progress_current_sub_byte = None
        await self._finalize_discovery(normalized_address)

    async def parse_inventory_response(self, payload) -> InventoryResult | None:
        result = InventoryResult()
        try:
            self.discovery_stage = self.discovery_stage or "inventory"
            if payload.startswith("$") and "$" in payload[1:]:
                payload = payload.split("$")[-1]
            payload = payload.lstrip("$")
            payload_bytes = bytes.fromhex(payload)

            _LOGGER.debug(
                "Inventory raw frame | length=%d hex=%s",
                len(payload_bytes),
                payload_bytes.hex().upper(),
            )

            # --- FIX 1: The data payload starts at byte 3 ---
            data_bytes = payload_bytes[3:19] if len(payload_bytes) >= 19 else payload_bytes[3:]

            self._schedule_inventory_timeout()

            # All-FF response = no record at this slot. Skip and continue
            # the sweep — don't treat it as end-of-project. Real installs
            # have FF gaps mid-project (e.g. user deleted a module and
            # the slot got zero-erased) and the pre-0.5.13 single-FF
            # terminator dropped every record past such a gap. Residue
            # filtering moved to ``detect_stale_inventory`` (0.5.16) +
            # the HA-side button reconciliation, which operate on actual
            # bus presence rather than inferring from register patterns.
            if bool(data_bytes) and all(b == 0xFF for b in data_bytes):
                _LOGGER.debug(
                    "Empty PC Link registry block (FFFF...) detected. "
                    "Skipping to next."
                )
                return result

            if len(payload_bytes) < 15:
                _LOGGER.debug(
                    "Discovery skipped | reason=payload_too_short length=%d",
                    len(payload_bytes),
                )
                return result

            device_type_hex = f"{payload_bytes[7]:02X}"

            if device_type_hex == "FF":
                _LOGGER.debug(
                    "Discovery skipped | type=inventory module=%s reason=empty_register",
                    self._module_address,
                )
                return result

            device_info = classify_device_type(device_type_hex, DEVICE_TYPES)
            category = device_info.get("Category") or "Module"
            name = device_info.get("Name") or "Unknown"
            model = device_info.get("Model") or "N/A"
            channels = device_info.get("Channels", 0) or 0
            slice_end = 13 if category == "Module" else 14
            raw_address = payload_bytes[11:slice_end].hex().upper()
            converted_address = self.normalize_module_address(
                raw_address,
                source="device_address_inventory",
                reverse_bus_order=True,
            )

            # --- FIX: Skip deleted or uninitialized memory slots ---
            if converted_address in ("FFFF", "FFFFFF"):
                _LOGGER.debug(
                    "Discovery skipped | reason=deleted_or_empty_address address=%s type=%s",
                    converted_address,
                    device_type_hex
                )
                return result
            # -------------------------------------------------------

            if device_info.get("Category", "Unknown") == "Unknown":
                if device_type_hex not in self._unknown_device_types_warned:
                    self._unknown_device_types_warned.add(device_type_hex)
                    _LOGGER.warning(
                        "Unknown device detected: Type %s at Address %s. "
                        "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information.",
                        device_type_hex,
                        converted_address,
                    )
                else:
                    _LOGGER.debug(
                        "Unknown device detected (deduped): Type %s at Address %s",
                        device_type_hex,
                        converted_address,
                    )

            module_type = get_module_type_from_device_type(device_type_hex)
            if module_type == "pc_link":
                _LOGGER.info(
                    "PC Link detected during inventory enumeration | address=%s",
                    converted_address,
                )

            last_seen = datetime.now(timezone.utc).isoformat()
            device_entry = {
                "description": name,
                "discovered_name": name,
                "category": category,
                "device_type": device_type_hex,
                "model": model,
                "address": converted_address,
                "channels": channels,
                "channels_count": channels,
                "module_type": module_type,
                "discovered": True,
                "last_discovered": last_seen,
            }

            if category == "Button":
                result.buttons.append(device_entry)
            else:
                result.modules.append(device_entry)

            # Store device directly
            self.discovered_devices[converted_address] = device_entry

            _LOGGER.debug(
                "Inventory classification | module_address=%s device_type=%s module_type=%s "
                "model=%s channels=%s raw_type_byte=0x%02X raw_addr_bytes=%s",
                converted_address,
                device_type_hex,
                module_type,
                model,
                channels,
                payload_bytes[7] if len(payload_bytes) > 7 else 0,
                payload_bytes[11:slice_end].hex().upper() if len(payload_bytes) >= slice_end else "",
            )

            _LOGGER.info(
                "Discovered %s - %s, Model: %s, at Address: %s",
                category,
                name,
                model,
                converted_address,
            )
            return result
        except Exception:
            _LOGGER.error("Failed to parse Nikobus payload", exc_info=True)
            self.reset_state()
            return None

    async def parse_module_inventory_response(self, message):
        # Wake the sequential scan loop as soon as a data/trailer frame
        # arrives. Parsing the frame still runs below; this hook only
        # signals the scan coordinator.
        self._notify_scan_frame(message)

        # --- Route PC-Link frames to the correct parser ---
        if self._coordinator.inventory_query_type == InventoryQueryType.PC_LINK:
            await self.parse_inventory_response(message)
            return
        # --------------------------------------------------

        try:
            matched_header = next(
                (h for h in DEVICE_INVENTORY_ANSWER if message.startswith(h)), None
            )
            if not matched_header:
                return

            frame_body = message[len(matched_header) :]

            if len(frame_body) < 4:
                return

            address_segment = frame_body[:4].upper()
            address = reverse_hex(address_segment)
            payload_and_crc = frame_body[4:]

            self._module_address = address

            if self._module_type is None:
                discovered = self.discovered_devices.get(address, {})
                self._module_type = self._resolve_module_type(address, discovered)

            coordinator_channels = (
                self._coordinator.get_module_channel_count(address)
                if self._is_known_module_address(address)
                else 0
            )
            discovered_channels = self.discovered_devices.get(address, {}).get("channels")
            self._module_channels = next(
                (count for count in (coordinator_channels, discovered_channels) if count is not None),
                None,
            )

            decoder = self._get_decoder()
            if decoder is None:
                _LOGGER.error("No decoder available for module type: %s", self._module_type)
                self._schedule_timeout()
                return

            if hasattr(decoder, "set_module_address"):
                decoder.set_module_address(address)
            if hasattr(decoder, "set_module_channel_count"):
                decoder.set_module_channel_count(self._module_channels)

            analysis = decoder.analyze_frame_payload(self._payload_buffer, payload_and_crc)
            if analysis is None:
                self._schedule_timeout()
                return

            self._module_address = address
            self._payload_buffer = analysis["remainder"]
            self._scan_response_index += 1
            response_index = self._scan_response_index

            _LOGGER.debug(
                "Register scan response | module=%s response_index=%d frame_hex=%s "
                "buffered_chunks=%d remainder_len=%d",
                address,
                response_index,
                payload_and_crc.upper(),
                len(analysis["chunks"]),
                len(analysis["remainder"]),
            )

            decoded_commands: list[DecodedCommand] = []
            for chunk in analysis["chunks"]:
                normalized_chunk = chunk.strip().upper()
                if not normalized_chunk:
                    continue
                _LOGGER.debug(
                    "Discovery relationship chunk | module=%s response_index=%d chunk=%s",
                    address,
                    response_index,
                    normalized_chunk,
                )
                if all(c == "F" for c in normalized_chunk):
                    # All-F chunks are the controller's "no record at this
                    # register" sentinel. Length depends on module type
                    # (12 hex for switch/roller, 16 for dimmer, 32 for PC
                    # Link / PC Logic), so we check by content rather than
                    # against a single fixed string.
                    _LOGGER.debug(
                        "Discovery relationship empty chunk detected | module=%s response_index=%d chunk=%s",
                        address,
                        response_index,
                        normalized_chunk,
                    )
                    # Just skip the empty chunk, do NOT abort the scan!
                    continue

                decoded_commands.extend(
                    decoder.decode(normalized_chunk, module_address=address)
                )

            if decoded_commands:
                await self._handle_decoded_commands(address, decoded_commands)

            # COM-trace-aligned early-stop: if the response's full data
            # region ends with the per-module-type FF terminator tail,
            # this is the last-record-in-the-table sentinel. Stop the
            # current pass (the register loop checks ``_scan_trailer_seen``
            # at the top of each iteration). Existing chunks for this
            # register were already decoded above.
            tail_len = _FF_TERMINATOR_TAIL_HEX.get(self._module_type)
            if tail_len:
                data_region = analysis.get("payload_region", "")
                if len(data_region) >= tail_len and data_region[-tail_len:] == "F" * tail_len:
                    _LOGGER.debug(
                        "Register scan FF-tail terminator detected | module=%s "
                        "response_index=%d tail_len=%d data_region=%s",
                        address,
                        response_index,
                        tail_len,
                        data_region,
                    )
                    self._scan_trailer_seen = True

            if await self._check_early_termination(address, bool(decoded_commands)):
                return

            if not self._coordinator.discovery_module:
                await self._finalize_discovery(address)
            else:
                self._schedule_timeout()

        except Exception:
            _LOGGER.error("Failed to parse module inventory response", exc_info=True)
            self.reset_state()

    async def _handle_decoded_commands(
        self, module_address: str | None, decoded_commands: list[DecodedCommand]
    ):
        # Count successfully-decoded records for the progress tracker.
        # Each DecodedCommand that makes it this far represents one real
        # link; the button-store merge further down may deduplicate, but
        # the on-wire reality is "we saw this many records."
        if isinstance(decoded_commands, list):
            self._progress_decoded_records += sum(
                1 for c in decoded_commands if isinstance(c, DecodedCommand)
            )
        # Build IR receiver lookup from the current in-memory button store
        # so that split_ir_button_address and decode_ir_channel work for
        # any IR receiver, not just hardcoded prefixes.
        ir_receiver_lookup = None
        if self._button_data is not None:
            buttons = self._button_data.get("nikobus_button") or {}
            if isinstance(buttons, dict):
                ir_receiver_lookup = build_ir_receiver_lookup(buttons) or None

        new_commands = []
        command_mapping = {}

        for command in decoded_commands:
            if not isinstance(command, DecodedCommand):
                continue

            decoded = command.metadata or {}

            if decoded.get("push_button_address") is None and decoded.get("button_address") is not None:
                decoded["push_button_address"] = decoded.get("button_address")

            if decoded.get("push_button_address") is None and decoded.get("button_address") is None:
                continue

            new_commands.append(decoded)

            if module_address:
                add_to_command_mapping(command_mapping, decoded, module_address, ir_receiver_lookup)

        self._decoded_buffer = {
            "module_address": module_address,
            "commands": new_commands,
            "command_mapping": command_mapping,
        }

        _LOGGER.debug(
            "Discovery decoded commands | module=%s count=%d",
            self._decoded_buffer["module_address"],
            len(self._decoded_buffer["commands"]),
        )

        if self._button_data is None:
            return

        (
            updated_buttons,
            links_added,
            outputs_added,
            unmatched,
        ) = merge_linked_modules(self._button_data, command_mapping)
        # Accumulate unmatched references and the originating command
        # mapping across module scans so we can run the remote-
        # transmitter cluster-synthesis pass at end-of-discovery.
        if unmatched:
            self._accumulated_unmatched.update(unmatched)
        if command_mapping:
            for key, outputs in command_mapping.items():
                bucket = self._accumulated_command_mapping.setdefault(key, [])
                for output in outputs:
                    if output not in bucket:
                        bucket.append(output)
        # Only log at INFO when something actually merged; routine
        # no-op merges (the common case on re-discovery) stay at DEBUG.
        if updated_buttons or links_added or outputs_added:
            _LOGGER.info(
                "Discovered links merged into store: %d buttons updated, %d link blocks added, %d outputs added.",
                updated_buttons,
                links_added,
                outputs_added,
            )
        else:
            _LOGGER.debug(
                "Discovered links merged into store: %d buttons updated, %d link blocks added, %d outputs added.",
                updated_buttons,
                links_added,
                outputs_added,
            )
        if self._on_button_save is not None and (
            updated_buttons or links_added or outputs_added
        ):
            await self._on_button_save()


def run_decoder_harness(coordinator):
    """Lightweight harness to exercise discovery decoders without full HA runtime."""

    sample_messages = [
        "$0522$1E6C0E5F1550000300B4FF452CA9",  # dimmer frame with expected 16-hex chunk
        "5F1550000300B4FF",  # raw chunk form
    ]

    decoders = [DimmerDecoder(coordinator), SwitchDecoder(coordinator), ShutterDecoder(coordinator)]
    for message in sample_messages:
        _LOGGER.info("HARNESS message=%s", message)
        for decoder in decoders:
            results = decoder.decode(message)
            if not results:
                continue
            for result in results:
                _LOGGER.info(
                    "HARNESS decoder=%s payload_len=%s chunk_len=%s payload=%s metadata=%s",
                    decoder.module_type,
                    len(result.payload_hex) if result.payload_hex else "?",
                    len(result.chunk_hex) if result.chunk_hex else "?",
                    result.payload_hex,
                    result.metadata,
                )
