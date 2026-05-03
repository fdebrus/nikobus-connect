# Changelog

## 0.5.1

### Fixed

- **PC Link register scan now starts at the productive band 0xA3
  instead of 0x00.** 0.5.0 swept the full 0x00..0xFF range; on a real
  install (fdebrus, log 2026-05-03 22:10) the scan aborted at
  register 0x04 after 5 consecutive ACK timeouts because PC Link
  doesn't respond to register reads in 0x00..0x07, tripping the
  `MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT` early-stop. The Nikobus
  PC-software trace shows the productive band is exactly
  0xA3..0xFF; tuning the range there sidesteps the early-stop and
  matches the official tool's scan strategy. New constant
  `_PC_LINK_SCAN_RANGE_OVERRIDE` in `discovery.py`; the
  `_SCAN_REGISTER_RANGE_BY_MODULE_TYPE["pc_link"]` value moves from
  `range(0x00, 0x100)` to `range(0xA3, 0x100)`.

- **Phantom link-record on near-empty chunks no longer surfaces.**
  Same install's PC Logic scan returned chunks like
  `FFFFFFFFFFFFFFFFFFFFFFFFFFFF05FF` — all bytes 0xFF except for one
  stray 0x05 at byte offset 14, which the parser doesn't even
  extract. 0.5.0's `is_empty_record` required every hex char to be
  'F', so that chunk was routed to `_parse_link_record` and emitted
  a phantom record with `channel_idx=0xFF mode=0xFF flag=0xFF
  payload=FFFFFF slot=0xFF`. 0.5.1 tightens `_parse_link_record`:
  if `marker`, `mode_byte`, `flag_byte`, `payload_bytes`, and `slot`
  are all 0xFF, the chunk is treated as a near-empty bus artefact
  and rejected. Pinned by `test_near_empty_chunk_with_stray_byte_is_rejected`
  using the exact chunk observed on the live install.

### Tests

- 4 new tests in `tests/test_pc_record_parser.py` for the empty-
  record tightening (full all-FF; all-FF extracted fields with
  stray byte at unused offset 13 / 14; positive case where one
  non-FF in any extracted field is accepted).
- `test_pc_link_module_runs_register_scan` updated to pin the new
  `range(0xA3, 0x100)` instead of the 0.5.0 full sweep.
- 142/142 passing.

### Migration

- HA integrations bumping `nikobus-connect>=0.5.1` get the fixes
  automatically. Expect `Discovery started | module=86F5` on the
  next "Scan all module links" run to actually produce records
  (decoded via the structured INFO logs added in 0.5.0) instead of
  aborting at register 0x04.

## 0.5.0

### Added

- **PC Link (05-200) included in the register-scan queue.** A
  Nikobus PC-software serial trace captured against real hardware
  (Nikobus-HA#303, roswennen's install) showed the controller-resident
  link table — the data needed to resolve the unmatched-button-link
  problem — lives on the **PC Link**, not the PC Logic. Stage 1
  scanned PC Logic in 0.4.11; Stage 2a now scans PC Link too.

  - ``pc_link`` removed from the scan-queue exclusion in
    ``query_module_inventory("ALL")`` and from the
    ``non_output_modules`` set in the per-module path.
  - ``_SCAN_REGISTER_RANGE_BY_MODULE_TYPE`` gains a ``pc_link`` entry
    pinned to the full ``range(0x00, 0x100)`` sweep until the
    productive band is characterised across multiple installs.
  - New ``nikobus_connect/discovery/pc_link_decoder.py`` with
    ``PcLinkDecoder(BaseChunkingDecoder)``. Registered alongside the
    existing decoders on ``NikobusDiscovery._decoders``.
  - ``decode_command_payload`` in ``discovery/protocol.py`` gains a
    ``pc_link`` dispatch branch.

- **Shared 16-byte record parser for PC Link / PC Logic.** New
  ``nikobus_connect/discovery/pc_record_parser.py`` exposes
  ``parse_pc_record(chunk_hex)`` returning a ``ModuleRegistryRecord``
  (when ``byte_0 == 0x03``) or a ``LinkRecord`` (otherwise, non-empty).
  The trace confirms the on-wire format — every byte aligns with
  ``DEVICE_TYPES`` and the user's HA install address list. Parser is
  pinned by ``tests/test_pc_record_parser.py`` against 47 records
  from the trace (9 registry + 38 link).

### Changed

- **PC-Logic / PC-Link chunk stride corrected from 6 bytes (12 hex
  chars) to 16 bytes (32 hex chars).** Stage 1 guessed 12 from
  PC-software BP screenshots; the trace from real hardware showed the
  on-wire stride is 32 hex chars per record, with no per-cell
  sub-structure at the chunk layer. Updated ``_CHUNK_LENGTHS`` for
  both ``pc_link`` and ``pc_logic`` accordingly.
- **PC-Logic decoder now parses 16-byte records via the shared
  parser.** The Stage-1 ``PC-Logic chunk | module=X payload=Y`` log
  line is replaced by structured INFO logs:
  ``PC-Logic module-registry record | module=... device_type=0x... address=... type_slot=... raw=...``
  and
  ``PC-Logic link record | module=... channel_idx=0x... mode=0x... flag=0x... payload=... slot=0x... raw=...``.
  Stage 2a is **visibility-only** — the decoder still returns ``None``
  for every chunk so no records are merged into ``linked_modules``
  until the byte-0 → ``(target_module, channel)`` resolution is
  validated across multiple installs (Stage 2b).
- **Empty-chunk skip in the discovery loop generalised.** Was
  hard-coded to compare against the 12-char string ``"FFFFFFFFFFFF"``;
  now matches any-length all-F chunk so the 32-char PC controller
  empty marker is also skipped without emitting a phantom-record
  decode attempt.

### Migration

- HA integrations bumping the ``nikobus-connect>=0.5.0`` pin in
  ``manifest.json`` will see PC Link enrolled in the
  ``"Scan all module links"`` queue automatically (no HA-side change
  needed). Expect new INFO log lines per record on the next scan;
  these are intentional Stage-2a instrumentation and will be quieted
  in Stage 2b once the merge path lands.

## 0.4.13

### Changed

- **PC-Logic register scan widened to the full 0x00..0xFF range
  (Stage 1.5 instrumentation).** The Stage-1 dump in 0.4.11/0.4.12
  reused the output-module's tuned `0x00..0x3F` band for PC-Logic,
  which on roswennen's 80D9 LOM (Nikobus-HA#303) returned a 4×16
  cell-index directory followed by all-FF — exactly the geometry of
  one BP grid's directory, but no per-cell programming. Five BP grids
  are programmed on that LOM, so the cell content has to live
  somewhere; this release extends PC-Logic's primary `sub=04` pass
  out to the full register range so a re-run can confirm whether the
  rest of the grid lives past the directory.

  - New `_SCAN_REGISTER_RANGE_BY_MODULE_TYPE` table in
    `discovery.py`, keyed by `module_type`. Currently only
    `pc_logic` has an entry; it overrides the per-sub mapping with
    `range(0x00, 0x100)`.
  - `_scan_range_for_sub(sub_byte, module_type=None)` consults the
    per-type table first, then falls back to the per-sub mapping.
    Default behaviour for output modules is unchanged.

  **No-op for installs without PC-Logic.** Switch / dimmer / roller
  scans keep their tuned `0x00..0x3F` and `0x70..0x96` bands —
  regression test
  `test_switch_register_scan_range_unaffected_by_pc_logic_override`
  pins this. PC-Logic scans add ~25 s per LOM at the current
  `COMMAND_EXECUTION_DELAY`; that's the cost of the experiment.

  This is a Stage-1.5 step on the path to the real Stage-2 BP-cell
  decoder. Once the wider sweep produces real bytes (or proves the
  cell content lives at separate BP-unit bus addresses), a follow-up
  release ships the decoder itself.

### Fixed

- **`__version__` in `nikobus_connect/discovery/__init__.py` now
  matches the package version.** The 0.4.12 bump only updated
  `pyproject.toml`, leaving `__version__` reporting `0.4.11`.

## 0.4.11

### Added

- **PC-Logic (05-201) is now visible to discovery — Stage 1 instrumentation.**
  Heavily PC-Logic-routed installs were ending up with empty
  ``linked_modules`` on the majority of buttons. Root cause: the
  output-module flash records reference PC-Logic-synthesized
  addresses, but PC-Logic itself was excluded from the register-scan
  queue, so the merge layer had no namespace to resolve those
  addresses against and dropped the records.

  This release does not yet decode PC-Logic BP-cell bytes — that's
  Stage 2, designed against real bytes from a Stage-1 dump. What
  ships in 0.4.11:

  - ``pc_logic`` removed from the scan-queue exclusion set in
    ``query_module_inventory`` and from the ``non_output_modules``
    set in the per-module inventory path. PC-Logic modules now flow
    through the same register-scan engine as switch/dimmer/roller.
  - New ``nikobus_connect/discovery/pc_logic_decoder.py`` with a
    logging-only stub (``PcLogicDecoder``) that the engine invokes
    for ``module_type=pc_logic``. Every chunk is logged at INFO as
    ``PC-Logic chunk | module=<addr> payload=<hex>``, so users can
    capture the dump without enabling component-level debug.
  - ``decode_command_payload`` in ``discovery/protocol.py`` gains a
    ``pc_logic`` dispatch branch.
  - ``_CHUNK_LENGTHS`` in ``chunk_decoder.py`` gains
    ``"pc_logic": 12`` (best guess from the PC-software BP screenshots;
    will be refined in Stage 2 once real bytes land).

  **No-op for installs without PC-Logic.** The queue addition is
  predicated on a ``pc_logic``-typed module existing in
  ``dict_module_data``; installs without one see zero behaviour
  change. The stub decoder cannot produce a record, so it cannot
  feed the merge layer regardless.

- **DEVICE_TYPES additions.** Three confirmed device-type → model
  mappings that were previously falling through to ``other_module``:

  | Hex | Model  | Channels | Name |
  |-----|--------|----------|------|
  | ``22`` | 05-057 | 4 | Switch Interface |
  | ``26`` | 05-314 | 4 | RF868 Mini Transmitter with 4 Operation Points |
  | ``2B`` | 05-205 | — | Audio Distribution |

  ``0x22`` and ``0x26`` are Button category (no register scan).
  ``0x2B`` is a Module but stays out of the scan path via the
  ``other_module`` fallback in ``get_module_type_from_device_type``
  — its dedicated decoder is tracked separately.

## 0.4.10

### Changed

- **Register scan now uses per-sub-byte productive register ranges.**
  0.4.8 tuned which sub-bytes run per module type (dimmer: 04+01,
  switch/roller: 04 only). 0.4.9 quieted the logs. 0.4.10 completes
  the scan optimisation by narrowing each pass to the specific
  memory region that sub-byte addresses on the module.

  Per-sub register ranges — derived from the PC-software serial
  trace and verified against real hardware:

  | Sub-byte | Range | Size | Memory region |
  |---|---|---|---|
  | ``04`` | ``0x00..0x3F`` | 64 regs | Primary forward-link records |
  | ``00`` | ``0x00..0x3F`` | 64 regs | Same bank as sub=04 (table kept for callers that target it explicitly) |
  | ``01`` | ``0x70..0x96`` | 39 regs | Extended / channel-config bank |

  New module-level constants in ``discovery.py``:
  ``_SCAN_REGISTER_RANGE_BY_SUB``, ``_DEFAULT_SCAN_REGISTER_RANGE``,
  and ``_scan_range_for_sub()``.

  **Net per-module scan-time change vs 0.4.9:**

  | Module | 0.4.9 | 0.4.10 | Δ |
  |---|---|---|---|
  | Dimmer (2 passes: 04+01) | 2 × 256 = 512 regs | 64 + 39 = 103 regs | **−80%** |
  | Switch (1 pass: 04) | 256 regs | 64 regs | **−75%** |
  | Roller (1 pass: 04) | 256 regs | 64 regs | **−75%** |

  **No record regression.** Every productive register the full
  sweep hit is still covered: dimmer records observed in
  ``0x20..0x3E`` sit inside ``0x00..0x3F``; the ``1E0D48`` ch9
  record that the 0.4.7 bank probe unlocked sits inside the sub=01
  ``0x70..0x96`` window. Start at ``0x00`` (not PC tool's ``0x05``)
  preserves the 0.4.4 fix for records observed in ``0x00..0x0F``
  on some real hardware.

  Unknown sub-bytes fall back to the full ``0x00..0xFF`` sweep so
  future protocol variants stay probeable without silent skips.

  New regression tests:
  ``test_dimmer_scan_total_registers_is_tuned_not_full_sweep``,
  ``test_switch_scan_single_pass_is_tuned_not_full_sweep``.

## 0.4.9

### Changed

- **Discovery log chatter demoted to DEBUG.** Now that the scan
  pipeline is correct and stable, the running blow-by-blow no longer
  belongs in end-user logs. The following log lines are now at
  ``DEBUG`` instead of ``INFO`` / ``WARNING``:

  - Per-decoded-record: ``Discovery decoded | type=X module=Y ...``
    (switch / dimmer / roller decoders).
  - Per-record-batch merge: ``Discovery decoded commands | module=X count=N``
    and the paired ``Discovered links merged into store``. The merge
    line is still surfaced at INFO *when something actually merged* —
    no-op merges (the common re-discovery case) stay quiet.
  - Per-pass / per-register scan chatter: ``Register scan pass
    starting``, ``Register scan completed full range``, ``Register
    scan short-circuited by trailer``.
  - Expected fast-fail events: ``Register scan pass aborted — module
    not responding``, ``Register scan send failed``, ``Register scan
    gave up on register``. These are normal outcomes of the
    bank-compatibility probe and were previously WARNING-level.
  - Bookkeeping: ``Inventory record | address=X``, ``PC Link address
    recorded``, ``Skipping register scan for non-output module``,
    ``Module type conflict ... using config``, ``Data written to file``,
    ``Button store merge ran: changes=0``, ``Paired-button inference
    added N mirrored output(s)``.

  **Kept at INFO** (user-facing milestones):
  - Start / finish of discovery and each phase.
  - Per-queue-module ``Discovery started | module=X``.
  - Per-device ``Discovered <category> - <name>, Model: X``.
  - Non-zero merge summaries (``Module store merge summary``,
    ``Discovered links merged into store`` with actual changes).

  **Kept at WARNING** (real issues worth surfacing):
  - ``Discovery on_progress callback raised``.
  - ``No output modules found in config to scan``.
  - ``Unknown device detected ... please open an issue`` (asks for
    user action).

  End-user integration logs should now read as a concise progress
  narrative — start, each module found, start/finish of each scan,
  end — instead of a per-register stream. Anyone debugging the
  discovery pipeline can flip the ``nikobus_connect.discovery``
  logger to DEBUG to get the old firehose back.

## 0.4.8

### Changed

- **Multi-pass scan tuned to only productive banks per module type.**
  Real-hardware diff between pass 1 / pass 2 / pass 3 on both dimmer
  (``0E6C``) and switch (``C9A5``) modules revealed which sub-byte
  banks actually return unique records:

  | Module | ``sub=04`` | ``sub=00`` | ``sub=01`` |
  |---|---|---|---|
  | dimmer | primary (ch 1–6) | **duplicate of 04** | secondary (ch 7–12) |
  | switch | full (ch 1–12) | **duplicate of 04** | reverse-link phantoms |
  | roller | assume full | **duplicate of 04** | assumed phantoms |

  New per-type table ``_EXTRA_SCAN_SUBS_BY_MODULE_TYPE`` picks the
  passes worth running:

  ```python
  _EXTRA_SCAN_SUBS_BY_MODULE_TYPE = {
      "dimmer_module": ("01",),   # 2 passes: 04 + 01
      "switch_module": (),         # 1 pass: 04
      "roller_module": (),         # 1 pass: 04 (provisional)
  }
  ```

  **Net scan-time change vs 0.4.7:**
  - Dimmer: 3 passes → 2 passes (33% faster)
  - Switch: 3 passes → 1 pass (66% faster — back to pre-0.4.5 baseline)
  - Roller: 3 passes → 1 pass (66% faster — back to pre-0.4.5 baseline)

  The dimmer-bank-2 fix from 0.4.7 is preserved; we drop only the
  scans that wasted time with no record gain.

### Notes

- Roller behaviour is provisional — no real-hardware trace has
  confirmed the roller bank layout yet. If a user encounters
  missing roller records, we'll revisit the mapping.
- Phantoms from switch ``sub=01`` never polluted the store (merge
  layer rejected them as unmatched-button), so this is a
  performance + log-cleanliness fix rather than a correctness fix.

Regression tests:
``test_scan_runs_single_pass_per_switch_module``,
``test_scan_runs_single_pass_per_roller_module``,
updated ``test_scan_runs_three_passes_per_dimmer_module`` (now 2
passes, renamed intent).

## 0.4.7

### Fixed

- **Multi-pass scan now uses the right function code per module type.**
  0.4.5 hard-coded function ``10`` for the two extra passes (sub=00,
  sub=01) regardless of module type. That was wrong for dimmer
  modules — they only respond to function ``22`` reads; ``10``-
  prefixed commands are silently dropped, so passes 2 + 3 always
  fast-failed against dimmers and recovered zero records.

  Real-hardware probing confirmed:
  - Switch / roller modules: respond to ``10+04``, ``10+00``, ``10+01``.
  - Dimmer modules: respond to ``22+04``, presumably ``22+00`` and
    ``22+01`` (now reachable for the first time).

  Fix: extra passes reuse the same function code as pass 1 instead
  of hard-coding ``10``. Switch/roller behaviour is unchanged
  (``10`` was already correct for them); dimmers now actually probe
  their additional banks.

  Updated test:
  ``test_scan_runs_three_passes_per_dimmer_module`` — now pins
  ``226C0E`` for all three passes, not the previously-broken mix of
  ``226C0E`` + ``106C0E``.

## 0.4.6

### Fixed

- **Multi-pass scan no longer kills the connection mid-discovery.**
  0.4.5 shipped a three-pass register scan per module. On hardware
  where a module doesn't respond to the new function-10 sub=00 /
  sub=01 reads, the scan walked into two compounding failures:

  1. The inactivity watchdog (``_timeout_after``, 5 s) that the scan-
     response parser keeps rescheduling would fire during the
     first silent stretch of pass 2, triggering
     ``_finalize_discovery`` *while the scan loop was still running*.
     Finalize tore down discovery state; the coordinator closed the
     connection; subsequent register reads failed with
     ``Cannot send: Not connected``, the integration reloaded, and
     the user was left unable to rescan without a full restart.
  2. Each unresponsive register burned ~3 s (ACK timeout × 2
     retries). With 256 registers per pass × 2 new passes, a
     non-responding module wasted ~26 minutes.

  Two fixes in ``_scan_module_registers``:

  - **Cancel the pending inactivity timer at the start of every
    pass.** That timer is a safety net for single-pass mode; in
    multi-pass mode we finalize explicitly after the last pass, so
    the stale timer must not fire between passes.
  - **Fast-fail on consecutive ACK timeouts.** If
    ``MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT`` (default: 5) registers
    in a row give up without an ACK, abort the pass with a warning.
    Per-module worst case drops from ~26 min to ~15 s of extra time
    for bank-incompatible modules.

  Regression tests:
  ``test_scan_aborts_after_consecutive_ack_give_ups``,
  ``test_scan_cancels_pending_inactivity_timeout``.

### Internal

- New constant ``MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT`` (default 5)
  in ``const.py``. Override for tighter / looser bail-out thresholds.

## 0.4.5

### Fixed

- **Module register scan now reads three memory banks per output module
  instead of one.** A real-hardware PC-software serial trace revealed
  the Nikobus PC tool walks each output module with **three** distinct
  sub-byte values (``00``, ``01``, ``04``) on function ``10`` reads;
  each sub-byte addresses a different memory page on the module.
  Records that never surfaced through discovery (e.g. links written
  through the legacy "group" column in the Nikobus PC tool) live in
  the ``00`` and ``01`` banks — both of which the previous one-pass
  scan never touched.

  Behaviour change: every output module is now scanned three times.
  Pass 1 retains the historic command (``$1422<addr>{reg}04`` for
  dimmer, ``$1410<addr>{reg}04`` for switch/roller). Passes 2 + 3
  add ``$1410<addr>{reg}00`` and ``$1410<addr>{reg}01``. Each pass
  walks the full ``0x00..0xFF`` register range.

  **Cost: discovery is ~3× slower per output module.** A previously
  ~2.5 min single-module scan becomes ~7.5 min. The per-bank
  productive register range is narrower than the full sweep on real
  hardware (the PC trace shows e.g. ``A3..D3`` for sub=04 on one
  module); a follow-up will tune per-bank ranges to win this back
  once we map the productive ranges from real-hardware traces.

  Regression tests:
  ``test_scan_runs_three_passes_per_dimmer_module``,
  ``test_scan_runs_three_passes_per_switch_module``.

### Internal

- ``_scan_module_registers`` now accepts a ``sub_byte`` keyword
  (default ``"04"``). External callers don't need to change unless
  they want to target a specific bank.

## 0.4.4

### Fixed

- **Module register scan now covers the full 0x00..0xFF range.**
  Legacy code started at 0x10 (inherited, no comment explaining why),
  silently skipping 16 registers that real hardware can store link
  records in. Confirmed by a user report where a 4-key button had
  1A/1B link records sitting in 0x00..0x0F that never surfaced
  through discovery. The decoder still rejects anything that doesn't
  validate as a link record, so low-register config bytes (if any)
  don't produce phantoms.

  Scan time increases by ~16 extra register reads per output module
  (~8-24s additional worst case per module). Worth it: those records
  are programmed button linkages users expect to see.

  Regression test: ``test_default_scan_range_starts_at_zero_for_output_module``.

## 0.4.3

### Fixed

- **Runtime routing for IR remote presses.** 0.4.2 shipped IR virtual
  op-points at the storage layer, but two issues meant real IR
  discoveries never reached them:

  1. ``add_to_command_mapping`` keyed IR records by the nibble-shifted
     wire address (e.g. ``"D44E2C"``). That address has no recognised
     IR receiver prefix, so the merge-time resolver dropped the
     record as unmatched. Fixed by keying IR records on the receiver
     base (e.g. ``"0D1C80"``) derived from the pre-shift
     ``button_address``. Wall records are unchanged.

  2. ``merge_linked_modules``'s IR path required a matching wall
     op-point at ``key_raw``, which isn't guaranteed for IR-only
     receivers. The IR path is now independent of wall-key presence;
     the IR op-point is materialised directly from (receiver,
     ir_code).

### Added

- **IR op-points now carry a deterministic ``bus_address``.** Each
  IR virtual op-point stores the runtime wire address the receiver
  will emit when the IR code fires, computed as:

      bus_address = convert_nikobus_address(receiver_prefix + (base_byte + channel))
                    with first nibble shifted by KEY_MAPPING_MODULE[4][key_index]

  where ``key_index`` is the inverse of the IR bank cycle
  (``{"C":0, "A":1, "D":2, "B":3}``). Verified against a captured
  real-hardware trace: IR code ``10B`` on receiver ``0D1C80`` emits
  ``#ND44E2C`` on the bus.

  Consequence: ``find_operation_point(button_data, bus_address)``
  now resolves IR presses the same way it resolves wall presses.
  HA integrations route IR entities for free — no second lookup
  helper needed.

- New public helper: ``_compute_ir_bus_address(receiver, ir_code)``
  available through ``nikobus_connect.discovery.fileio`` for callers
  that want to compute the address without mutating the store.

### Behaviour contract changes

- ``find_operation_point`` may now return an IR op-point, with
  ``key_label`` being the storage key (e.g. ``"IR:10B"``). Existing
  wall-key behaviour is unchanged.
- 0.4.2-shaped IR entries without ``bus_address`` are healed on the
  next discovery run — no explicit migration needed.
- Older stores (pre-0.4.3) that haven't re-discovered will still
  deserialise cleanly; IR presses simply won't route until the next
  discovery fills in ``bus_address``.

## 0.4.2

### Added

- **IR codes now surface as virtual op-points on the IR receiver.**
  Records that carry an ``ir_code`` (from module-config scans of IR
  receivers) no longer collapse onto the receiver's wall keys
  (``1A``-``1D``). Each distinct IR code gets its own sibling
  op-point under ``operation_points["IR:{code}"]``, so they appear in
  the UI next to the wall keys of the same receiver.

  IR op-point shape mirrors wall op-points for consistency, with two
  differences: the storage key is always prefixed ``IR:`` (so it can
  never collide with wall keys like ``1A`` / ``2D``); and the entry
  carries ``ir_code`` + auto-description ``IR code {code} #I{code}``
  instead of a ``bus_address``. User-edited descriptions are
  preserved across re-discovery.

  New public helpers: ``find_ir_operation_point(button_data,
  receiver_address, ir_code)`` and ``IR_OP_POINT_PREFIX`` for callers
  that walk the store directly.

## 0.4.1

### Fixed

- **Switch ``M01 (On / off)`` is now recognised as a 2-button pair.**
  Previously only dimmer M01/M02 and roller M01 were mirrored; switch
  M01 was wrongly treated as a single-key toggle. On real hardware
  it's an on/off pair — 1A turns the output on, 1B (or the A↔B
  partner on the wall unit) turns it off, with only one link record
  stored on the module. Paired keys now receive the mirror on
  discovery, same logic as the other 2-button modes.

  Regression test: `test_switch_m01_mirrors_between_on_and_off_keys`.

- ``M15 (Light scene on / off)`` (switch) and ``M03 (Light scene
  on/off)`` (dimmer) are intentionally kept out of the pair set until
  a real-hardware example confirms their pairing convention —
  explicit negative test coverage added.

## 0.4.0

### Breaking

- **Module storage moves to a caller-owned adapter, same pattern as
  the button store (0.2.0).** The library no longer writes
  ``nikobus_module_config.json``. New kwargs on
  ``NikobusDiscovery.__init__``:

  ```python
  NikobusDiscovery(
      coordinator,
      config_dir=...,
      create_task=...,
      button_data=..., on_button_save=...,
      module_data=..., on_module_save=...,   # NEW
      on_progress=...,
  )
  ```

  ``module_data`` is a caller-owned dict mutated in place.
  ``on_module_save`` (sync or async, no-arg) is awaited after every
  merge. Integration is expected to persist via HA's
  ``.storage/nikobus.modules``.

  If either kwarg is omitted, the library skips module persistence
  entirely — no more legacy file writes.

- Removed the public ``update_module_data(file_path, ...)`` helper.

### Added

- **Option-A module store schema** (parallel to the button store):

  ```json
  {"nikobus_module": {
      "<address>": {
          "module_type": "switch_module",
          "description": "<user-editable name>",
          "model": "05-000-02",
          "channels": [ ... ],
          "discovered_info": {"name", "device_type", "channels_count"}
      }
  }}
  ```

  Flat dict keyed by module address. ``module_type`` moves into the
  entry so the top-level grouping dict is gone — integrations group
  via ``entry["module_type"]`` when rendering.

- ``merge_discovered_modules(module_data, discovered_devices)``
  in-memory merge. User-owned fields are preserved verbatim; discovery
  only owns ``model``, ``address``, ``discovered_info``,
  ``module_type``, and defaults for channels appended beyond the
  previous ``channels_count``.

  Fields discovery never touches:
    - module-level ``description``
    - ``channels[i].description``
    - ``channels[i].entity_type``
    - ``channels[i].led_on`` / ``channels[i].led_off``
    - ``channels[i].operation_time_up`` / ``operation_time_down``

- ``find_module(module_data, address) -> (address, entry) | None``
  helper (parallel to ``find_operation_point``).

- 11 regression tests covering the merge semantics, user-field
  preservation across re-discovery, auto-generated unique
  descriptions per module type, roller timing preservation, model
  refresh, non-Module devices skipped, ``find_module`` lookup, and
  end-to-end integration through ``_finalize_inventory_phase``.

### Integration migration

Integrations must now provide ``module_data`` + ``on_module_save``,
the same pattern they already use for buttons:

```python
module_data = await module_storage.async_load() or {"nikobus_module": {}}

NikobusDiscovery(
    coordinator,
    ...,
    module_data=module_data,
    on_module_save=module_storage.async_save,
)
```

A migration step that reads the existing
``<config_dir>/nikobus_module_config.json`` into the new ``.storage``
location on first startup is recommended — see the integration PR
that ships alongside this release.

## 0.3.5

### Added

- **`on_progress` callback for discovery tracking.** New optional
  kwarg on `NikobusDiscovery.__init__` that receives a
  `DiscoveryProgress` snapshot at phase transitions and on every
  register read:

  ```python
  def on_progress(progress: DiscoveryProgress) -> None | Awaitable[None]:
      ...

  NikobusDiscovery(..., on_progress=on_progress)
  ```

  Phases (exported as module-level constants
  `PHASE_INVENTORY` / `PHASE_IDENTITY` / `PHASE_REGISTER_SCAN` /
  `PHASE_FINALIZING`):

  1. `inventory` — PC-Link `#A` enumeration started.
  2. `identity` — per-address device_type queries queued.
  3. `register_scan` — emitted once at the start of each module's
     scan, then again after each register read with `register`
     populated. `module_index` / `module_total` describe position
     within the scan queue.
  4. `finalizing` — discovery finished.

  `DiscoveryProgress` fields: `phase`, `module_address`,
  `module_index`, `module_total`, `register`, `register_total`,
  `decoded_records`. `register_total` drops to the actual sent count
  when a `$18` trailer short-circuits the loop, so a progress bar
  driven by `register / register_total` lands at 100% cleanly at the
  break.

  Both sync and async callbacks are accepted. Exceptions raised by
  the callback are logged at WARNING and swallowed — a misbehaving
  tracker cannot abort a scan.

  Backwards-compatible: existing callers that don't supply
  `on_progress` run unchanged.

- 6 regression tests covering the phase sequence across a full scan,
  trailer-driven `register_total` drop, exception resilience, sync
  vs async callback support, the no-callback path, and the
  `DiscoveryProgress` defaults.

## 0.3.4

### Added

- **Paired-button inference extended to roller M01** ("Open - stop -
  close"). That mode is functionally a 2-button pair: up key opens,
  down key closes, either key stops during movement. The module stores
  the link record on one key only — same implicit-pairing pattern as
  dimmer M01 but the mode name doesn't say "2 buttons" explicitly.

  The paired-mode matcher switched from substring testing
  (`"2 buttons" in mode_text`) to exact match against a small set of
  mode strings pulled from the `mapping` module. Roller M01 joins
  dimmer M01 in the 2-button pair set; dimmer M02 stays in the
  4-button group. Rename drift between `mapping.py` and the matcher
  stays in sync automatically since `mapping` is now the source of
  truth.

  Switch modes remain single-key throughout. Roller M02 ("Open"),
  M03 ("Close"), M04 ("Stop") are single-direction → single-key —
  explicitly covered by negative tests.

- 2 new regression tests:
  - `test_roller_m01_mirrors_between_up_and_down_keys`
  - `test_roller_m02_open_only_is_single_key`

## 0.3.3

### Added

- **Paired-button inference for dimmer M01 / M02 and roller M01.**
  These modes use more than one physical key per output but the module
  only stores a link record on one key; the peer keys act on the same
  output silently. Without inference, peer keys show no
  `linked_modules` in the scan output.

  - Dimmer M01 ("Dim on/off (2 buttons)") — 2 keys (on / off)
  - Dimmer M02 ("Dim on/off (4 buttons)") — 4 keys (on / off / + / -),
    master on 1A (or 2A on 8-op units)
  - Roller M01 ("Open - stop - close") — 2 keys (up opens, down closes;
    either stops during movement)

  `merge_linked_modules` now finishes with a post-pass that walks every
  `operation_points` entry, identifies outputs whose mode text matches
  one of the paired-mode strings (pulled from the `mapping` module so
  rename drift stays in sync), and copies them verbatim to the paired
  peer key(s) on the same physical button. Dedupes against whatever's
  already there; idempotent across re-runs.

  Pair table:
  - 2-button: 1A↔1B, 1C↔1D, 2A↔2B, 2C↔2D
  - 4-button: 1A→{1B,1C,1D}, 2A→{2B,2C,2D} (master-only source —
    records on a non-master key are left alone since we can't infer
    the role assignment).

  All other modes stay single-key. The mirrored record keeps the
  source's mode label verbatim — the module doesn't distinguish
  on-side from off-side in its memory, so synthetic role labels
  would be unverifiable.

- 9 regression tests in `tests/test_paired_button_inference.py`
  covering dimmer M01 both directions, per-output filtering,
  idempotency, M02 master-only sourcing, M02 row independence on 8-op
  units, roller M01 up↔down mirroring, negative coverage for roller
  M02/M03/M04 (single-direction = single-key), and negative coverage
  for other non-paired modes.

## 0.3.2

### Fixed

- **Register-scan ACK timeout was too tight for real hardware.** ACKs from
  live modules land 300–700 ms after the send (the first register of a
  scan hitting the top of that range because the module wakes up on the
  initial command). The 0.3.1 default of 300 ms was catching the edge:
  scans completed but the first register's ACK+data arrived 30–70 ms
  after both retries had timed out. Raised `MODULE_SCAN_ACK_TIMEOUT` to
  1.5 s and `MODULE_SCAN_DATA_TIMEOUT` to 0.5 s. Downstream overrides
  still supported via the const names.
- **Drift-on-timeout produced phantom records.** When a register's
  retries were exhausted and the ACK+data arrived moments later, the
  late ACK matched the next register's wait and the late data frame
  concatenated with that register's buffer — every chunk after the
  timeout was misaligned by 4 bytes. `_read_register_once` now flushes
  `_payload_buffer` and drains the response queue when it gives up on a
  register, so the next register starts from a clean slate.

### Added

- Regression test `test_giveup_on_ack_timeout_flushes_buffer_and_queue`
  pinning the flush behaviour.

## 0.3.1

### Fixed

- **Module register scan now reads every register the module emits.**
  The previous `query_module_inventory` implementation fire-and-forget
  queued all 240 register commands (`0x10..0xFF`) up front at a fixed
  150 ms drain interval. Module responses (`$2E…`) bypass the command
  response queue, so no correlation existed between a sent command and
  a received data frame. Against real hardware this dropped ~6 of every
  7 registers — a module with many programmed links appeared to have
  only the first two or three records.

  Replaced with a sequential send-and-wait loop. For each register:

  1. Send the inventory read.
  2. Await `$05…` ACK (`MODULE_SCAN_ACK_TIMEOUT`, default 300 ms;
     one retry on timeout).
  3. Await the matching `$2E`/`$1E` data frame
     (`MODULE_SCAN_DATA_TIMEOUT`, default 200 ms; silence is valid for
     empty registers).
  4. On `$18<all-FF>…` trailer, short-circuit the remaining reads —
     the module has signalled end-of-programmed-memory.

  The command handler (`command.py`) and listener (`listener.py`) are
  untouched; coordination lives entirely in `NikobusDiscovery` via an
  `asyncio.Event` + `asyncio.Lock` pair. Two concurrent scans are now
  serialised rather than interleaving on the bus.

### Added

- New const knobs in `nikobus_connect.const`, importable from package
  root for downstream overrides:
  - `MODULE_SCAN_ACK_TIMEOUT`
  - `MODULE_SCAN_DATA_TIMEOUT`
  - `MODULE_SCAN_RETRY_LIMIT`
  - `MODULE_SCAN_TRAILER_PREFIX`
- 6 regression tests covering sequential decode, ACK retry, empty-
  register silence, trailer short-circuit, concurrent-scan locking,
  and the trailer predicate.

### Behaviour changes exposed by the rewrite

Old code masked two issues that the sequential scan surfaces:

1. **Scan time on real modules** drops from a fixed ~36 s per module
   (240 × 150 ms) to typically 2–10 s (ACK-bound early termination on
   the trailer plus fast-skip on FF-empty registers). Heavily
   programmed modules take proportionally longer because every data
   register now contributes its real ACK+data latency instead of
   getting fire-and-forgotten.
2. **Dropped data frames** that used to vanish silently now land
   deterministically. Integration-side code that saw intermittent
   button-link gaps should see stable output across re-runs.

### Out of scope

These remain open for follow-up releases:

- Module-type misclassification fallback priority
  (`discovery.py:668-671`).
- Orphan-record placeholder registration.
- Decoder coverage for modes M12/M13/M14/M15 and IR sub-records.

## 0.3.0

- Physical-button-keyed storage schema (Option A) with
  `operation_points` nested under each device.
- Generated `{type} #N{physical}` / `Push button {key} #N{bus}`
  descriptions for globally-unique entity names.
- `find_operation_point(button_data, bus_address)` helper for
  integrations doing press-event routing.
- `build_ir_receiver_lookup` and `_handle_decoded_commands` updated
  for the new shape.

## 0.2.3

- Diagnostic logging around module-type classification and register
  scans (raw inventory frame hex, module-type conflict INFO, per-
  module `response_index`).

## 0.2.2

- Prefer coordinator-config module type over the inventory self-report
  (`discovery.py:668-671`). Avoids firmware that lies about its
  `device_type` byte.

## 0.2.1

- Dimmer register scan now goes through the buffered
  `BaseChunkingDecoder` path — previously dropped records that
  straddled two frames or came back shorter than one full chunk.

## 0.2.0

- Replace button-discovery file IO with a caller-owned
  `button_data` dict + `on_button_save` adapter. Removes
  `nikobus_button_config.json` from the library surface.
