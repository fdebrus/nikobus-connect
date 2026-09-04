# Changelog

## 0.36.0

**Feedback module (05-207): image read and decode.**

- `MODULE_IMAGE_SIZES` gains `feedback_module` (0x7900 bytes). `NikobusAPI.read_module_memory()` reads it through the new `read_feedback_image()`: the fixed tables (tracked output modules at 0x6000, push-button module addresses at 0x6100, LED modes at 0x7800) in full and the two `FF`-terminated tables (input-event records at 0x0000, LED lists at 0x4000) up to their first empty block, so a backup takes a few dozen block reads instead of nearly two thousand. New generic `read_memory_range()`.
- New `nikobus_connect.discovery.feedback_decoder`: `decode_feedback_image()` turns the image into the tracked outputs (module, channel), the 24 push-button module groups, every LED slot with its mode, polarity and the outputs it tracks, and the input-event records (which key press changes which output). Address helpers convert between the stored 22-bit module address / 24-bit input address and the 6-hex key addresses discovery uses (bit-reversed: `module_address << 2 | key_index`, key index A=1, B=3, C=0, D=2). The layout was pinned from the module's memory-map plugin and validated against real key addresses.
- `MODULE_CRC_UNKNOWN` lists module types whose reported CRC coverage is not known (the feedback module); callers skip the CRC comparison for them.
- **Fix: the PC-Link clock reply no longer reaches the feedback callback.** A clock reply (`$1CFF...`) has the frame code of an output-state answer; while a query waits for it the listener now routes it to the response queue only. Before, installs with a feedback module logged a phantom module (the PC-Link address byte-swapped) carrying the date as its output state.


## 0.35.1

- **Fix: dimmer integrity check reported a false CRC mismatch.** A dimmer module's self-reported CRC16 (function 0x13) covers both link banks but skips the six bytes between them (0x7FA..0x7FF); `verify_module_memory()` computed it over the whole image, so every dimmer failed the check. Validated on a real 05-007: only that coverage reproduces the module's CRC. New `image_crc()` / `MODULE_CRC_RANGES` hold the per-module coverage.


## 0.35.0

**Module status, integrity and clock commands; scans sized by the module itself.**

- New `NikobusCommandHandler.query()` sends any PC-Link function and returns the reply payload; the listener now hands `$18`/`$2E`/`$1E` answers to a waiting caller as well as to discovery.
- `NikobusAPI` gains `get_module_status()` (function 0x11: EEPROM-error flag, type signature, link-record counts), `get_module_crc()` (0x13), `get_pc_link_time()` / `set_pc_link_time()` (0x1D / 0x1E), `read_module_memory()` (block reads of the whole programming image: 0x700 bytes for switch/roller, 0xFD0 for dimmers) and `verify_module_memory()` (module CRC vs. a locally computed CRC16).
- **Discovery reads exactly the records a module holds.** Before scanning an output module the engine asks it for its record counts (0x11) and reads only those blocks — switch/roller from block 0x10, dimmer bank 0 from 0x20, bank 1 from sub 01/0x20 plus the configuration blocks — instead of a fixed band. Modules with more links than the old band covered are now read completely; modules that don't answer 0x11 keep the fixed band. An EEPROM-error flag is logged.
- **Dimmer T2 (ramp time) is decoded** from the record's third byte instead of being reported as unknown.
- **Registry header accepts every header version.** The PC-Link registry marker is `<ver> 55 AA AA <count>` with `ver` in 0x49..0x5E; only 0x5E was recognised before, so older units never got the count-bounded sweep.


## 0.28.0

**Fix: register scan no longer stops at the first empty register, so link
records past a mid-table gap are read.**

The per-module register scan concluded end-of-table on the *first*
register whose data region was all-FF (empty). Real installs have empty
gaps mid-table — a deleted-slot gap, or a central function whose link
records are written past such a gap — and every record after the gap was
silently dropped. Observed on a production roller module (8B9C): the scan
short-circuited at register ``0x1C`` after 12 reads on an all-FF register,
so a close-only ("NEER") roller central function whose records sit beyond
that gap never decoded and never surfaced.

The data-region FF-tail terminator is now gap-tolerant: it requires a run
of ``MODULE_SCAN_FF_TERMINATOR_STREAK_LIMIT`` (3) *consecutive* empty
registers before concluding end-of-table; an isolated gap resets the run
and the scan continues (still bounded by the per-module scan band). This
mirrors the PC-Link inventory scan, which already tolerates mid-project
FF gaps. The explicit ``$18`` all-FF trailer frame still short-circuits
immediately — that is the module signalling end-of-memory, not an
inference.


## 0.27.3

**Fix: near-all-FF inventory slots no longer become phantom devices.**

A real PC-Link inventory frame (fdebrus install, deterministic across
two scans) carried a slot whose address normalises to ``3FFFFF`` (low
20 bits all set) with a type byte 0x23 that classifies as a 05-304 RF
push button. The empty-slot guard only skipped ``FFxxxx`` high-byte
filler, so this slot leaked through and created a phantom 4-key RF
button (keys 3FFFFF / 7FFFFF / BFFFFF / FFFFFF — the last being the
universal empty-slot value) that decodes no links and shows up as
``legacy_undecoded`` on every scan. The guard now also skips addresses
ending in ``FFFFF`` (the low-20-bits-set filler signature). Pinned with
the exact production frame.


## 0.27.2

**Detect corrupt module link tables and skip them (no phantom records).**

A production scan (fdebrus install, 2026-06-09) had one module (4707)
whose link table read mid-record relative to the scanned register
window: a fixed-stride walk produced ~21 phantom buttons and lost every
real record. The Nikobus PC software independently flagged the install
as corrupt and asked for reprogramming; after the user reprogrammed
4707 it read cleanly — confirming the misalignment was genuine module
corruption, not a decoder issue.

Policy: we do NOT try to recover a corrupt table (re-aligning a corrupt
scan only yields a partial/uncertain picture — records pushed out of
the scanned window are gone, and the proper fix is reprogramming).
Instead the decoder DETECTS the misalignment — evidence-gated: a
non-phase-0 byte offset decodes >= 2 host-inventory button addresses,
strictly more than phase 0, as the unique maximum — SKIPS the module's
link decode (no phantom buttons enter the store), and FLAGS the module
on ``NikobusDiscovery.corrupt_link_tables`` so the host can tell the
user to reprogram it. A WARNING is logged. With no inventory to score
against, or ambiguous evidence, behaviour is unchanged (best-effort,
never guesses). Aligned modules are never flagged.

## 0.27.1

**Discovery audit fixes.**

- **Stuck discovery flags on failure** — every discovery entry point
  (`start_inventory_discovery`, `query_module_inventory`,
  `_start_next_register_scan`) sets `coordinator.discovery_running`
  early; an exception escaping after that point (not connected, send
  failure, cancellation on reload) left the flag stuck True — the host
  then suppressed polling forever and rejected every new scan with
  "discovery already running". All three paths now reset the discovery
  state (flags, accumulators, timers) on the way out and re-raise.
- **Provenance upgrade on dedupe hit** (fileio) — a link record first
  scanned via PC-Link / PC-Logic registry memory and later confirmed in
  the output module's own link table kept its registry tag, which could
  make the host's residue classifier flag a perfectly-active button as
  previous-owner residue. The authoritative `output_module_table` tag
  now wins on a dedupe hit (never downgraded).
- **Remote-transmitter collision guard** (fileio) — a clustered remote
  code resolving onto an address already holding a real inventory
  button used to clobber it (channels forced to 1, its 1A op-point's
  bus address rewritten). The merge is now skipped with a warning; the
  wall button stays authoritative.
- +8 regression tests. Audit notes: the decoder/parser layer was
  audited clean (bounds, byte order, chunk alignment, mapping tables);
  the $2E/$1E scan-frame CRC is extracted but deliberately left
  unvalidated — the trailing-field format is unconfirmed on real
  hardware and a wrong check would drop legitimate frames.

## 0.27.0

**API: formal `CoordinatorProtocol` host contract.**

The contract between the library and its host coordinator was implicit —
scattered `getattr(coordinator, ...)` calls that silently skipped
features on a missing member, and cross-object attribute writes
documented only in comments. `nikobus_connect.CoordinatorProtocol` now
declares the full surface (6 attributes + `get_module_type` /
`get_module_channel_count` / `get_button_channels`); every library
signature that takes a coordinator is typed against it.

- Hosts can declare conformance and have mypy verify they implement
  everything the library touches.
- `coordinator=None` remains the supported "no host" form for parsers
  and the decoder harness; a non-None coordinator must be conformant
  (the old "object missing get_button_channels" shape is no longer a
  tolerated input).
- Typing surfaced three unguarded `nikobus_command` accesses (register
  scan + the two inventory paths) — they now fail fast with a clear
  RuntimeError instead of an AttributeError mid-scan.

**Robustness: reconnect-with-backoff primitive + post-reconnect reset APIs.**

The HA integration's reconnect loop owned the transport backoff and had
to reach into library privates to clear per-connection state. Both
concerns now live in the library:

- `NikobusConnect.reconnect_with_backoff(initial_delay=1.0,
  max_delay=30.0, on_attempt=None)` — loop `connect()` (transport +
  handshake) with exponential capped backoff until success; returns the
  attempt count; cancellation propagates; `on_attempt(attempt, delay)`
  (sync or async) surfaces progress without the caller owning the loop.
- `NikobusEventListener.reset()` — clear the partial-frame buffer, the
  query-group map and unconsumed responses after a reconnect.
- `NikobusCommandHandler.reset()` — drain the queue and clear the
  GET-dedup keys. `drain_queue()` now cancels queued caller futures so
  awaiters are released instead of hanging until their own timeout.
- +20 tests over the previously-untested transport/listener/command
  core (connect/handshake/send/read error paths, backoff
  doubling/cap/cancellation, frame buffering, dispatch gating, resets).

## 0.26.0

**New: `.nkb` project-file reader (`nikobus_connect.nkb`).**

Moves the Nikobus `.nkb` parser down from the Home Assistant integration
into the library, where the other Nikobus vendor-format readers (bus
frames, PC-Link records) already live. A `.nkb` is the export from
Niko's PC software — a ZIP holding an MS-Access database with the
install's user-given names, rooms, per-output names, and Central
Function (scene) groups; the bus carries none of that, so reading the
project file is the only way to recover friendly names / Areas / named
scenes.

- `nikobus_connect.nkb.parse_nkb(path)` → `NkbData(addresses, scenes,
  outputs)`: `{address: (name, room)}`, scene groups as
  `(module, channel, mode)` member sets (for member-set matching against
  discovered CFs), and `{(module, channel): name}` output names.
- Also exports `find_nkb_file`, `mode_code`, `CANONICAL_NKB_FILENAME`,
  and the `NkbData` / `SceneDef` types.
- The MS-Access reader is vendored under `nkb/_access_parser` (the
  upstream sdist won't build on modern setuptools); the only new runtime
  dependency is `construct>=2.10`. The vendored reader is excluded from
  `mypy`/`ruff`, matching its third-party status.

The parser is HA-agnostic; the *apply* side (writing names/Areas into
the HA registry, matching scenes to CF entities) stays in the
integration.

## 0.25.0

**Typed release: `nikobus_connect` is now `mypy --strict` clean and ships a
`py.typed` marker.**

No behavioural change — this release is a type-annotation and packaging
pass over the whole library, plus a round of code modernization. The
headline payoff is for downstream consumers: with `py.typed` shipped
(PEP 561), the Home Assistant integration can type-check directly against
this library and drop its `ignore_missing_imports` override.

- **`py.typed` marker shipped** and wired into the wheel via
  `[tool.setuptools.package-data]`, so installed copies carry their type
  information.
- **Whole package is `mypy --strict` clean.** Every module under
  `nikobus_connect` is annotated: the core I/O layer (`api`, `command`,
  `listener`, `connection`, `protocol`) and the discovery subsystem
  (`discovery`, `fileio`, `protocol`, `pc_record_parser`, the lookup
  tables in `mapping`, and the chunk decoders). Dynamic bus-decoded
  payloads are typed as `dict[str, Any]`; nullable dict-key and register
  paths are narrowed with explicit guards rather than suppressed.
- **`[tool.mypy]` config added** — strict mode scoped to the package,
  with a single per-module override for `pyserial-asyncio` (which ships
  no type information upstream).
- **Code modernization** (behaviour-preserving): PEP 604 unions and
  `collections.abc` callables across the core modules, DRY-ed the
  listener callback dispatch, chained the connection buffer-overrun
  error, removed unused imports, and `make_pc_link_command` now accepts
  `bytes | bytearray`.
- **`__version__` is now derived from installed package metadata**
  (`importlib.metadata.version`) instead of three drifting hard-coded
  literals.

## 0.24.0

**Scene-centric light-scene Central Functions: one CF, many triggers.**

Matches Niko's own architecture (confirmed against the Nikobus software
manual §15.6 "Light scene / Central functions"): a light scene is a
single *named output group* that can be activated from any number of
inputs via the `MCF` connection mode. `_classify_cf_scenes_from_command_mapping`
now groups light-scene op-points by their **member set** and emits **one**
`CFBroadcast` per distinct set instead of one per firing address.

- New `CFBroadcast.triggered_by: list[str]` — every address that
  activates the CF, sorted; `bus_address` is the canonical (sorted-first)
  one. Defaults to `[bus_address]` for single-trigger CFs (including the
  `38xx` PC-Logic broadcast path), so the field is always populated.
- Two buttons / IR codes wired to the **identical** outputs+modes collapse
  into a single scene with both addresses in `triggered_by` (previously two
  duplicate scenes).
- An M14 on-scene and a separate off-trigger stay **distinct** — the member
  set includes each output's *mode*, so on/off don't merge.
- Per-key / per-IR-code scenes with different member sets still split
  correctly (the 0.21–0.23 behaviour), now keyed on the canonical address.

## 0.23.0

Classify light-scene CFs from the merged button store, keyed on each
op-point's own wire address (per-key / per-IR-code split) — fixes IR
light-scenes collapsing under the receiver base.

## 0.22.0

Emit the keyed **wire address** for light-scene CFs (the `#N` frame the
bus actually emits), fixing activation that previously did nothing.

## 0.21.0

Classify button / IR-sourced light-scene Central Functions ("MCF"
mechanism) from the merged button store, complementing the `38xx`
PC-Logic broadcast path.

## 0.20.8

**DIAGNOSTIC build — not for release.** Widen CF classification to a
catch-all so the discovery log surfaces *every* ``38 XX XX`` CF-space
address an output-module link record actually points at, letting us
learn this install's real CF family layout from the log.

Added a third, lowest-priority pattern ``^38[0-9A-F]{4}$`` labelled
``cf_other``. The known specific labels (``switch_pair`` 0x3840..0x3847,
``roller_pair`` 0x3880) still win where they match; anything else in the
0x38 space now falls through to ``cf_other`` instead of being dropped.
Still safe in principle because the classifier only emits/logs a CF when
real module link records target the address — but this is intentionally
over-broad for log inspection and must be narrowed before any release.

## 0.20.7

Recognise the full switch-CF family range when classifying Central
Functions that are actually in use.

**Switch CFs span 0x3840..0x3847, not just 0x3841.** The CF activation
broadcast classifier (`_classify_cf_broadcasts_from_unmatched`) finds
which Central Functions are in use purely from the output-module link
tables: an output module that participates in a CF carries a normal
link record whose SOURCE is the CF trigger address, which lands in
`_accumulated_unmatched`; the classifier then attaches the CF's
(module, channel, mode) members and surfaces it as a scene. Empty CF
slots have no link records and are never emitted — this is the
in-use-vs-empty discrimination, sourced entirely from the modules (no
user input, no `.nkb` project file).

The classifier only recognised the `38 41 XX` switch family, but the
PC-Logic CF trigger-address grid (function 0x10, sub=0x02) decodes —
via `convert_nikobus_address(<prefix>8700)` — onto all eight switch
families `0x3840..0x3847` (prefix 0x00..0xE0). Real CFs programmed on
any of the other seven families were therefore silently dropped.
Broadened the `switch_pair` pattern to `^384[0-7][0-9A-F]{2}$`. This is
safe because a family address with no module link members is still
never turned into a scene.

## 0.19.1

Progress-bar fixes for the discovery state machine.

**"First step shows 96/96 = 100%" bug.** The identity phase scans 96
registers per address (sub=4 0xA0..0xFF) and ends with
``_progress_register_total = 96`` and ``_progress_module_registers_sent``
at the last per-address count. The library reset
``_progress_module_register_total`` and ``_progress_module_registers_sent``
in ``_start_next_register_scan`` but **not** ``_progress_register_total``,
so the first ``PHASE_REGISTER_SCAN`` emit for the first module carried
the stale ``register_total = 96`` from identity. Fixed by resetting
``_progress_register_total``, ``_progress_module_register_total``, and
``_progress_module_registers_sent`` at the END of the identity loop
AND at the START of every ``_start_next_register_scan`` (defence in
depth).

**Cumulative ratio > 100% on multi-pass modules.** When the FF-tail
early-stop fired inside pass N of a multi-pass module (dimmer,
pc_logic, pc_link), the previous code collapsed
``_progress_module_register_total`` to ``pre_pass_sent + registers_sent``
— losing the remaining-pass budget. Pass N+1 then ran with
``_progress_module_registers_sent`` incrementing past
``_progress_module_register_total``, producing ratios > 100% during
the second and third passes. Fixed by subtracting only the UNUSED
portion of the early-stopped pass from the cumulative total, so
the budget for remaining passes is preserved. Single-pass modules
still collapse to ``registers_sent`` naturally.

## 0.19.0

All module scan profiles realigned to the Nikobus PC software's
own bus behaviour, captured from a real COM7 monitoring session
(24/05/2026 full-session trace, both TX and RX directions).

**The DLL-derived plans were partially wrong.** PR #87 already
established this for PC-Link: the ``Niko_05_XXX.dll`` ``GetDLLReadInfo``
sections describe the **host's project-file layout**, not the bus
reads. This PR extends that finding to every other module type by
comparing the PC software's actual register-read sequence against
each module type's profile.

**Per-module changes (default plans, COM-trace-aligned):**

| Module type | Old default | New default | Sub-bytes |
|---|---|---|---|
| switch_module | 82 reads (anchored sub=00 0x8E..0xAF + sub=04 0x00..0x2F) | **48 reads** (sub=00 0x10..0x3F, parser-driven early-stop) | sub=00 only |
| roller_module | 68 reads (anchored) | **48 reads** (sub=00 0x10..0x3F) | sub=00 only |
| dimmer_module | 245 reads (DLL-derived) | **56 reads** (sub=00 0x20..0x3F + 0xF8..0xFF, sub=01 0x20..0x2F) | sub=00, sub=01 |
| pc_logic | 133 reads at **sub=04** (DLL-derived, wrong sub-byte) | **135 reads at sub=00, sub=02, sub=03** | sub=00/02/03 |
| pc_link | 97 reads (PR #87) | unchanged | sub=00/01/04 |

**The pc_logic fix is critical**: the 0.17.0 DLL-derived plan
scanned PC-Logic at sub=04, but the PC software actually reads at
sub=00, sub=02, and sub=03 — never sub=04. That's why module 940C
returned 0 decoded records on the 2026-05-23 HA trace: every read
was sent to a sub-byte the module doesn't use. The COM trace shows
the PC software reads PC-Logic at 3 distinct bands across 76 unique
register reads.

**Parser-driven early-stop on the trailing-FF terminator.** The PC
software's per-register stop condition (analyzed from the COM trace):
after reading register N, if the response payload's trailing N bytes
are all ``0xFF``, the table-end terminator has been reached and the
scan moves to the next pass / module. Tail length depends on chunk
size:

  - switch/roller/pc_link/pc_logic (16-byte chunks): trailing 6 bytes
  - dimmer (8-byte chunks): trailing 3 bytes

Implemented in ``_scan_module_registers`` by setting
``_scan_trailer_seen`` from the new ``_FF_TERMINATOR_TAIL_HEX`` table.
On the 24/05/2026 trace the early-stop fires at register 0x16 (8394
roller, 7 reads), 0x17 (5B05 4-ch switch, 8 reads), 0x1B (4707
12-ch switch, 12 reads), 0x1C (9105 roller, 13 reads), 0x27 (C9A5
12-ch switch, 24 reads), 0x3F (0E6C dimmer sub=00, 32 reads), and
the equivalent boundaries on pc_link and pc_logic.

**Effective per-module scan sizes on a populated install** (early-stop
fires before the safety ceiling):

  switch: 8-24 reads (vs 312 in 0.17.x, 82 in 0.18.x)
  roller: 7-13 reads (vs 251 in 0.17.x, 68 in 0.18.x)
  dimmer: ~50 reads (vs 245 in 0.17.x, unchanged 0.18.x)
  pc_logic: ~76 reads (vs 133 in 0.17.x, but now at the CORRECT sub-bytes)
  pc_link: ~97 reads (unchanged from 0.18.1)

``broad_scan=True`` widens each default pass to a full 0x00..0xFF
sweep of the same sub-bytes (diagnostic mode for firmware variants
that place records outside the PC-software-observed band).

**Cleanup.** With every profile now COM-trace-validated, the
intermediate DLL-derived (``_*_PROFILE_FULL``) and HA-anchored
(``_*_PROFILE_ANCHORED``) profile constants are no longer needed
and have been removed, along with the long DLL-section comment
blocks, the ``_regs_for_bytes`` byte-offset translator, the
``_FEEDBACK_PROFILE_DEFAULT`` dormant data constant, the unused
``_DEFAULT_SCAN_REGISTERS`` fallback, and stale docstring
references to ``_EXTRA_SCAN_SUBS_BY_MODULE_TYPE`` and
``_scan_range_for_sub`` (helpers that no longer exist). The
profile section of ``discovery.py`` is ~320 lines smaller.

## 0.18.0

Anchored productive-band scan for switch and roller modules.

**Problem.** 0.17.0's per-product DLL profiles work, but they scan far
more registers than necessary on populated installs:

- Switch: 312 register reads per module across 6 passes, of which
  three live-switch traces (2026-05-23, modules C9A5 and 4707) showed
  only ~23-86 productive responses concentrated in two tight clusters
  (sub=00 reg 0x8F..0xA7, sub=04 reg 0x10..0x27 with a deterministic
  hole at 0x14).
- Roller: 251 register reads per module across 5 passes, of which two
  live-roller traces (modules 9105, 8394) showed productive records
  only in sub=00 reg 0x90..0x9C plus a lone "master" slot at 0xF0,
  with sub=01 0x00..0x27 holding a per-channel state mirror.

The long sub=00 sweep (194 contiguous registers) also exhibited a
**reliability failure** on one trace: module 4707 stopped responding
at reg=0xD9 after 156 reads (consecutive_give_ups=8 abort). All
downstream passes (sub=01, sub=04) then ACK-timed-out completely,
yielding zero records — the module was effectively exhausted.

**Cross-validation.** The DLL magic tuple for the switch
(``Niko_05_000_01.dll`` ``GetDLLReadInfo`` returns
``offset=0x100, recsize=6, recs_per_unit=0x10, length=0``) confirms
the trace: every decoded chunk was exactly 6 bytes, and the page
size of 16 records × 6 bytes = 96 bytes = 6 register reads matches
the 4 × 6-register page structure visible in both productive
clusters.

Empirical mode-distribution analysis on C9A5 showed sub=00 and
sub=04 store **disjoint** record sets (0 overlap across 32 + 42
records), but sub=01 0x70..0x96 was a near-subset of sub=04
(9 of 12 records duplicated). Dropping sub=01 from the default loses
~3 unique records but saves 44 register reads.

**Changes.**

- ``_SWITCH_PROFILE_ANCHORED`` (new): sub=00 0x8E..0xAF (34) +
  sub=04 0x00..0x2F (48) = **82 reads** per switch, padded around
  the observed clusters for install headroom.
- ``_ROLLER_PROFILE_ANCHORED`` (new): sub=00 0x8E..0xA8 + 0xF0 +
  sub=01 0x00..0x27 = **68 reads** per roller.
- ``_MODULE_SCAN_PROFILES`` now points switch/roller at the anchored
  profiles; the full DLL-derived plans are renamed ``_SWITCH_PROFILE_FULL``
  and ``_ROLLER_PROFILE_FULL`` and remain accessible via the
  ``_MODULE_SCAN_PROFILES_BROAD_EXTRA`` superset.
- ``broad_scan=True`` on ``NikobusDiscovery`` restores the full
  DLL-derived coverage (anchored + dropped bands + huge variable
  sections) for installs that want the safety-net sweep back.

**Performance.** Default scan time drops by ~3.8× on switches and
~3.7× on rollers. Module-exhaustion failures from the long sub=00
sweep are eliminated since no single pass exceeds 34 reads.

**Validation status.** Anchored profiles are validated against 3
switch and 2 roller traces. The dimmer / pc_logic / pc_link profiles
are unchanged — no trace data yet to narrow them.

## 0.18.1

PC-Link profile restored to the empirically-validated bus scan plan.

The 0.17.0 DLL-derived plan for PC-Link (280 reads across sub=00 long
sweep + sub=01 + sub=02 + sub=03) was based on the
``Niko_05_200.dll``'s ``GetDLLReadInfo`` section list — but a real
PC-software COM4 trace captured 24/05/2024 against pc_link module
86F5 shows the DLL sections describe the **host's project-file
layout, not bus reads**. The PC software never touches sub=02 or
sub=03, never sweeps sub=00 0x3F..0xFF, and the actual scan plan is:

  sub=00 0x05..0x09, 0x3E         (6 — vendor-aligned header)
  sub=01 0x70..0x93, 0x96         (37 — vendor-aligned secondary)
  sub=04 0x65..0x69                (5 — vendor-aligned status)
  sub=04 0xA3..0xD3                (49 — PC-Link module registry)

Total: 97 reads.

The 2026-05-23 HA trace of 86F5 corroborates this — the 0.17.0 plan
returned **0 decoded records across 280 reads** on real hardware,
because every band it scans is empty/unused on PC-Link bus memory.

The 0.17.0 DLL plan is preserved as ``_PC_LINK_PROFILE_BROAD_EXTRA``
behind ``broad_scan=True`` for any future firmware variant that
might expose those regions.

## 0.17.1

Discovery speed regression fix for installs with feedback modules.

**Problem.** 0.17.0's per-product profiles included a feedback-module
scan (~912 register reads at sub=4/5/6/7 derived from
Niko_05_207.dll). Real-world testing on a feedback module showed it
**doesn't respond to a single one of those reads** — every read
ACK-times-out (1.5 s × 2 attempts ≈ 3 s per register). Net cost:
**~45 minutes wasted per feedback module** in the install.

The give-up logic that should abort the pass after
``MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT`` consecutive ACK timeouts
did not fire — the abort log line is absent from the diagnostic log
despite every register reading timing out. Tracked separately; the
likely candidates are stale-ACK matching in ``_await_matching_ack``
or a counter-reset path not yet identified. Until that's understood,
the safe fix is to keep feedback out of the scan plan since the
profile yields zero records anyway.

**Fixes.**

- ``feedback_module`` restored to ``NON_OUTPUT_MODULE_TYPES``. The
  Niko_05_207.dll-derived profile is preserved as
  ``_FEEDBACK_PROFILE_DEFAULT`` (data constant) for future firmware
  variants that respond differently.
- ``MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT`` lowered 16 → 8 as a
  defensive measure for any future profile that walks into a
  non-responsive section.

**Scan-time impact** (user's 8-module install, log analysis):

| Module | 0.17.0 | 0.17.1 |
|---|---|---|
| Feedback (966C) | ~45 min | skipped |
| Switch / Roller / Dimmer / PC-Logic / PC-Link | ~3 min each | unchanged |
| **Total** | ~65 min | ~20 min |

## 0.17.0

Per-product scan plans derived from Niko's product DLLs. Restores
records the 0.16.0 vendor-aligned scan was silently dropping.

**Root cause.** 0.16.0's "vendor-aligned" scan was built from one
COM3 trace on one module (0x3D82, 48 register reads). We applied that
single trace to every module type. Static analysis of Niko's PC
software DLL set (CalcMemMap.dll dispatcher + 8 per-product DLLs)
proved the architecture is plugin-driven: ``CalcMemMap.dll`` loads
each ``Niko_05_XXX.dll`` and calls its ``GetDLLReadInfo`` export to
learn that product's read profile, expressed as ``(byte_offset,
length)`` sections.

**Bus-level addressing.** Each register read returns 16 bytes (one
BP cell), and ``byte_offset = (sub_byte * 256 + register) * 16``.
Mapping the dimmer DLL's 8 sections through this formula reproduces
the 0x3D82 trace's 48 reads exactly — but only because PC software
had set the conditional-skip flag for section 3 (the variable-length
link table, default 0xC85 bytes ≈ 201 reads). On a bus discovery
where no project cache is primed, section 3 MUST be read or all of
the dimmer's link records are lost.

**Per-product profiles.** Each output module type now has its own
``_PROFILE_*`` tuple in ``discovery.py``, derived from
``GetDLLReadInfo`` disassembly:

  - **DIMMER** (Niko_05_100): 8 sections, ~248 reads — includes the
    variable section 3 link table previously missed.
  - **ROLLER** (Niko_05_202): 5 sections, ~251 reads — includes the
    variable section 1 link table.
  - **PC-LOGIC** (Niko_05_201a): 4 sections all at sub=4, ~133 reads.
  - **PC-LINK** (Niko_05_200): replaces the empirical sub=4 0xA3..0xFF
    sweep with DLL-derived sub=0/2/3 bands, ~280 reads.
  - **FEEDBACK** (Niko_05_207): previously NOT scanned. Now 912 reads
    (sub=4 full band + sub=6 bands). Removed from
    ``NON_OUTPUT_MODULE_TYPES``.
  - **SWITCH** (Niko_05_000_01): DLL returns a magic tuple rather
    than a section list; the EXE-side dispatch couldn't be decoded
    confidently without dynamic analysis. Pragmatic profile applies
    the dimmer/roller pattern (sub=0 link table band) PLUS the
    pre-0.16.0 sub=4 0x00..0x3F band as a proven safety net.

**Scan-time impact.** Cumulative reads per module: switch ~312,
dimmer ~248, roller ~251, pc_logic ~133, pc_link ~280, feedback ~912.
Most variable-length sections terminate early via the existing
``MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT`` once flash filler appears,
so the wall-clock cost is typically 1.5–2× the previous 48-register
plan rather than 5×.

**API changes.** Internal helpers ``_scan_subs_for_module_type`` and
``_scan_registers_for_sub`` are replaced by
``_scan_passes_for_module_type`` returning ``tuple[ScanSection, ...]``
where ``ScanSection = (sub_byte, register_tuple)``. The constants
``_VENDOR_REGISTER_MAP_BY_SUB``, ``_SCAN_SUBS_BY_MODULE_TYPE``,
``_SCAN_REGISTERS_BY_SUB``, ``_PC_LINK_REGISTERS``,
``_BROAD_SCAN_LEGACY_REGISTERS``, and
``_BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE`` are removed.
``broad_scan=True`` still opts in to the conditional sections PC
software skips when its project cache is primed (dimmer section 7 ~720
reads, roller section 2 ~700 reads).

**Source DLLs analysed** (Nikobus PC software install, 2026-05-23):
``Niko_05_000_01.dll`` (switch 12-ch), ``Niko_05_007.dll``,
``Niko_05_010.dll`` (older switch variants), ``Niko_05_100.dll``
(dimmer), ``Niko_05_200.dll`` (PC-Link), ``Niko_05_201a.dll``
(PC-Logic), ``Niko_05_202.dll`` (roller), ``Niko_05_207.dll``
(feedback). All extractions pinned by tests in
``test_progress_vendor_aligned.py`` and ``test_register_scan_range.py``.

## 0.16.3

Fix discovery progress display during inventory + identity phases.
Pre-0.16.3 the library only emitted accurate ``register_total`` /
``registers_sent`` during PHASE_REGISTER_SCAN, leaving the earlier
phases at the stale defaults. HA consumers fell back to a 240-register
safety value, so a PC-Link scan that actually reads 96 registers per
address appeared in the UI as "stuck at 96 / 240".

**PHASE_IDENTITY** (per-address probe, ``0xA0..0xFF`` = 96 regs):

- ``register_total`` now set to 96 per address before the scan starts
- ``module_total`` set to the address-queue length, ``module_index``
  incremented per address so the UI can show "module 2/3"
- ``registers_sent`` increments per register read inside the loop and
  resets to 0 between addresses, so each address's progress bar
  starts fresh at 0/96 rather than carrying over the cumulative
  count from the previous address
- ``pass_index`` / ``pass_total`` set to ``1/1`` since identity is
  always a single sub=04 sweep per address
- A per-register ``_emit_progress`` is called inside the inner loop
  with the current register byte, so the UI bar actually advances

**PHASE_INVENTORY** (the ``#A`` bus broadcast):

- ``register_total`` set to 1 (the ``#A`` broadcast is one unit of
  work, not a register sweep)
- ``registers_sent`` set to 1 once the command is on the wire
- HA consumers no longer fall back to ``0 / 240``

Both fixes set the per-module cumulative counters (``_progress_module_*``)
AND the legacy single-pass counter (``_progress_register_total``) so
both 0.16.1+ consumers and any pre-0.16.1 fallback paths see the
correct values.

Pinned with 5 new tests in ``test_inventory_identity_progress.py``:

- identity-phase register_total = 96 per address
- identity-phase registers_sent increments 1..96 per register
- identity-phase counter resets between addresses
- identity-phase module_index / module_total track the address queue
- inventory-phase register_total = 1 (single unit of work)

446 tests pass.

## 0.16.2

**Vendor-exact ``mapping.py``.** Every Model number and Name in
``DEVICE_TYPES`` now traces directly to Niko's master product
database (``product.mdb``). Each entry carries a new ``VendorRef``
field with the ``S_DB_*`` localization key from ``ProductBase``,
so the link to vendor data is mechanically verifiable.

**Catalogue corrections** (pre-0.16.2 entries used wholesale /
regional aliases that don't appear in any modern Niko catalogue):

| Byte | Pre-0.16.2 Model | 0.16.2 Model | Vendor reference |
|------|-----------------|--------------|------------------|
| 0x04 | 05-342 (retired) | **05-060** (alt ``4*-072``, legacy ``05-060-01``) | ``S_DB_BUSDRUKKNOP_2`` |
| 0x06 | 05-346 (retired) | **05-064** (alt ``4*-074``, legacy ``05-064-01``) | ``S_DB_BUSDRUKKNOP_4`` |
| 0x0C | 05-348 (retired) | **05-09x** (legacy ``05-09x-01``) | ``S_DB_KNOP_4_IR_UNIQUE`` |
| 0x12 | 05-349 (retired) | **4*-078** (legacy ``05-078-01``) | ``S_DB_KNOP_8_GRAFIET`` |
| 0x1F | "Unknown" | **05-301-4*** (alt ``05-302``, legacy ``410-00001``) | ``S_DB_RF_WAND_2`` |
| 0x23 | "Unknown" | **05-303-4*** (alt ``05-304``, legacy ``410-00002``) | ``S_DB_RF_WAND_4`` |

The two pre-0.16.2 ``Unknown`` entries (0x1F / 0x23) carried
"we know it's a 2/4-channel RF-bus wall device but don't know the
SKU" — product.mdb resolves both to Niko's ``05-301-4*`` /
``05-303-4*`` references with the legacy ``410-00001`` /
``410-00002`` codes documented as alternates.

**Mode-name strings** in ``SWITCH_MODE_MAPPING``, ``DIMMER_MODE_MAPPING``,
``ROLLER_MODE_MAPPING`` updated to use Niko PC-software UI English
wording exactly:

- ``M02`` → ``"On + Operating time"`` (was ``"On, with operating time"``)
- ``M03`` → ``"Off + Operating time"`` (was ``"Off, with operation time"``)
- ``M06`` → ``"Delayed off (up to 2h)"`` (was ``"Delayed off (long up to 2h)"``)
- ``M07`` → ``"Delayed on (up to 2h)"`` (was ``"Delayed on (long up to 2h)"``)
- ``M11`` → ``"Delayed off (up to 50s)"`` (was ``"Delayed off (short up to 50sec.)"``)
- ``M12`` → ``"Delayed on (up to 50s)"`` (was ``"Delayed on (short up to 50sec.)"``)
- Dimmer M05/M06 → ``"On / Off + Operating time"`` (matched switch terminology)
- Dimmer M13/M14 → ``"… (1 button)"`` (was ``"… (1key)"``)

**New mode-vendor-ref tables** (sibling to the mode-name tables):

```python
SWITCH_MODE_VENDOR_REF = {0: "S_DB_DESC_SCHAKEL_M1", ...}
ROLLER_MODE_VENDOR_REF = {...}
DIMMER_MODE_VENDOR_REF = {...}
```

Surfaces the ``S_DB_DESC_*`` localization key for each mode byte so
future i18n / vendor-tooling callers don't need to re-derive it from
the mode string.

**No behaviour change.** All decoders (``switch_decoder``,
``dimmer_decoder``, ``shutter_decoder``, ``pc_record_parser``) still
read from ``*_MODE_MAPPING`` and produce the same ``M`` field shape
``"M07 (...)"``. Only the parenthetical wording changed.

**New regression tests** in ``test_device_types_vendor_exact.py`` (10
tests) pin every DEVICE_TYPES entry against its vendor ref, the
mode-table parallel structure, and the legacy-alias exclusions —
so a future "let's just rename this for clarity" edit can't silently
drift from vendor terminology.

**Updated tests** in ``test_pc_logic_stage1`` /
``test_pc_link_stage2b`` to reflect the new 0x1F / 0x23 model
identifications and the updated M07 wording.

## 0.16.1

Surface vendor-aligned scan progress in ``DiscoveryProgress`` so UI
consumers can show an accurate per-module progress bar regardless of
how many passes the plan runs or which register byte is currently
being read.

The pre-0.16.1 model assumed:
  - registers start at 0x10
  - one scan pass per module
  - ``register_total`` = the length of that single pass

None of those hold under the vendor plan (sub=00 reads 0x05..0x09 + 0x3E
then sub=01 reads 0x70..0x93 + 0x96 then sub=04 reads 0x65..0x69). With
just the per-pass length surfaced, an HA progress consumer that did
``done = register - 0x10 + 1`` against ``register_total = 37`` would
hit ``register = 0x70`` and compute 97/37 = 262 % progress on the first
read.

**New fields on ``DiscoveryProgress``:**

- ``registers_sent`` — cumulative count across all passes for the
  CURRENT module (resets to 0 on each new module). Use this for the
  numerator of the progress percentage; ignore ``register`` for the
  ratio.
- ``pass_index`` / ``pass_total`` — 1-based pass position within the
  module's plan (e.g. 2 of 3 means we're scanning the link table
  pass after the header pass).
- ``sub_byte`` — the wire sub-byte of the current pass (``"00"``,
  ``"01"``, ``"04"``), useful for surfacing the scan phase verbosely
  in diagnostic logs.

**Changed semantics of existing fields:**

- ``register_total`` — now the CUMULATIVE target for the current
  module (e.g. 48 for the vendor plan, 112 with ``broad_scan=True``,
  93 for PC-Link). Previously was the per-pass length. Falls back
  to the per-pass length only when a caller bypasses the vendor
  plan (e.g. forensic mode).
- ``register`` — still the current byte being read, but no longer
  monotonically increasing from 0x10. May jump between passes
  (e.g. 0x09 → 0x3E → 0x70). UI consumers should NOT use this as
  the progress numerator.

**Existing trailer / give-up early-stop behaviour preserved:** when
a scan pass ends early (``$18FFFF...`` trailer or
``MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT`` consecutive ACK timeouts),
the cumulative ``register_total`` collapses to the count of
registers actually sent — so the ratio reaches 100 % naturally.

**Reference numbers** for HA-side UI target tuning:

| Module | Vendor plan target | broad_scan target |
|---|---|---|
| Switch / Roller / Dimmer / PC-Logic | 48 regs | 112 regs |
| PC-Link | 93 regs | 93 regs (no broad-scan extras) |

A typical install of PC-Link + PC-Logic + 3 switches + 1 dimmer
totals **333 register reads** across the discovery sweep (down from
~1170 pre-0.16.0).

Pinned with new ``test_progress_vendor_aligned.py`` (8 tests covering
the new fields, the per-module plan targets, and the typical-install
headline number).

## 0.16.0

**Full vendor alignment for the scan plan — no firmware-specific
exceptions, no defensive sweeps, every module reads exactly the 48
registers Niko's PC software reads.**

Niko's PC software (COM3 trace 2026-05-08, switch module 3D82) uses
EXACTLY this per-module read sequence for every output module:

| Sub-byte | Register list                    | Purpose                | Count |
|----------|----------------------------------|------------------------|-------|
| sub=00   | 0x05, 0x06, 0x07, 0x08, 0x09, 0x3E | module header / identity | 6 |
| sub=01   | 0x70..0x93 + 0x96                 | link table              | 37 |
| sub=04   | 0x65..0x69                        | status / state          | 5 |
|          |                                   | **Total per module**    | **48** |

0.16.0 applies this exact list to:

- **Switch module** (05-000-02 / 05-002-02): was sub=04 0x00..0x3F + sub=01 0x70..0x96 = 103 regs → now 48 regs (vendor)
- **Roller module** (05-001-02): was 103 regs → 48 regs (vendor)
- **Dimmer module** (05-007-02 / 05-008-02): was sub=04 + sub=01 both 0x00..0xFF = **512 regs** → now 48 regs (vendor). The pre-0.16.0 firmware-specific full-sweep exception (2026-05-04 capture on modules 116D + 0E0A) is **gone**.
- **PC-Logic** (05-201): was sub=04 0x00..0xFF = 256 regs → now 48 regs (vendor). The pre-0.16.0 defensive 0x00..0xFF override is **gone**.

PC-Link keeps its existing 0xA3..0xFF inventory-band scan — that's the
controller-side module-registry, a different operation from the
per-module vendor link-table reads. Already vendor-aligned per the
May 2024 capture; no change.

**Safety net** — opt-in legacy scan via ``broad_scan=True`` on the
discovery instance. Re-adds the pre-0.16.0 sub=04 0x00..0x3F sweep
as an extra pass after the vendor primary. Switch / roller / dimmer
/ PC-Logic all get the safety net. Use when a firmware revision
stores its link table outside the vendor band:

```python
discovery = NikobusDiscovery(coordinator, ..., broad_scan=True)
```

**Plumbing changes:**

- New ``_SCAN_SUBS_BY_MODULE_TYPE`` — module-type → ordered sub-byte plan
- New ``_SCAN_REGISTERS_BY_SUB`` — sub-byte → exact register tuple (NOT a ``range``; preserves the non-contiguous 0x96 in the vendor's sub=01 list)
- New ``_BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE`` + ``_BROAD_SCAN_LEGACY_REGISTERS`` — opt-in safety-net plumbing
- New synthetic sub-byte tokens: ``"04_broad"`` (legacy 0x00..0x3F), ``"pc_link_inventory"`` (PC-Link's 0xA3..0xFF). Both collapse to ``"04"`` on the wire via ``_wire_sub_byte``.
- New ``_scan_subs_for_module_type(module_type, broad_scan=…)`` helper
- New ``_scan_registers_for_sub(sub_byte, module_type=…)`` helper
- ``NikobusDiscovery.__init__`` gains ``broad_scan: bool = False``

**Removed:**

- ``_EXTRA_SCAN_SUBS_BY_MODULE_TYPE`` (replaced by ``_SCAN_SUBS_BY_MODULE_TYPE``)
- ``_SCAN_REGISTER_RANGE_BY_SUB`` (replaced by ``_SCAN_REGISTERS_BY_SUB``)
- ``_SCAN_REGISTER_RANGE_BY_MODULE_TYPE`` / ``_SCAN_REGISTER_RANGE_BY_MODULE_TYPE_AND_SUB`` (no per-module-type widening any more)
- ``_PC_LOGIC_SCAN_RANGE_OVERRIDE`` / ``_PC_LINK_SCAN_RANGE_OVERRIDE`` constants (PC-Logic shares the vendor plan; PC-Link's range is inlined into ``_SCAN_REGISTERS_BY_SUB``)
- ``_scan_range_for_sub`` helper (use ``_scan_registers_for_sub`` instead)

**Net effect** on a typical install with one PC-Logic + one PC-Link +
2-3 switch modules + 1 dimmer:
- Pre-0.16.0: 256 + 93 + 3×103 + 512 = **1170 registers per discovery sweep**
- 0.16.0:    48 + 93 + 3×48 + 48 = **333 registers per discovery sweep** (**~3.5× faster**)

This is what "scan-time matches the vendor" looks like.

**Mode-name and timer tables in mapping.py are unchanged** — this
release only affects WHICH bytes we read from each module.

Pinned in:
- ``test_full_vendor_alignment`` (20 tests covering the vendor map,
  the per-module plan, register-list exactness, wire-sub-byte
  collapse, and broad_scan opt-in behaviour)
- Updated ``test_register_scan_range`` (10 tests rewritten for the
  new 3-pass-with-exact-register-list model)
- Updated ``test_pc_logic_stage1`` (3 tests rewritten for the
  vendor-aligned PC-Logic plan)
- Updated ``test_pc_link_stage2`` (1 test pinned for the inventory
  band unchanged)

## 0.15.4

Resolve dimmer T1 to its **per-mode** parameter table. Niko's product
database has four distinct T1 tables for the dimmer module, dispatched
by mode byte:

* **M01/M02/M03** → 3-value on/off-step config (``DIMMER_T1_1``)
* **M05/M06**     → 4-value push time (``DIMMER_T1_2``)
* **M07**         → 16-value delayed-off duration (``DIMMER_T1_3``)
* **M11/M12**     → 16-value preset dim level as % (``DIMMER_AMOUNT_PERCENT``)

Pre-change, ``dimmer_decoder._timer_value`` consulted a single
positional table (``DIMMER_TIMER_MAPPING``) for every mode, which:
- collapsed M01/M02/M03 silently to ``None`` (step config never surfaced)
- conflated M05/M06 push times with the T2 ramp-time column (wrong value)
- reported M11/M12 preset levels as voltages (``"5,0 V"`` etc.) rather
  than the percentages the Niko PC software shows
- always reported ``None`` for T2 because the T2 nibble was never wired

Now ``DIMMER_MODE_T1_LOOKUP`` dispatches to the correct table per
mode, and ``_timer_value`` accepts a ``t2_raw`` parameter that, when
provided, resolves T2 against the canonical ``DIMMER_T2_RAMP`` table
(strictly monotonic 16-entry sequence, per Niko ``S_DB_DIMMER_T2``).

Note on T2 wiring: the dimmer chunk's T2 nibble is not yet extracted
in ``dimmer_decoder.decode`` (the previous implementation set
``t2_raw=None`` unconditionally). The new ``_timer_value`` signature
is ready for a follow-up that reads the T2 nibble from the 16-byte
dimmer record, but the chunk-level extraction is unchanged in this
release.

Sourced from product.mdb ParamBase rows KP=11, 12, 13, 14, 15.

Pinned in ``test_dimmer_per_mode_t1`` (10 tests).

**Behaviour change for callers reading the T1 string of dimmer records:**
- M11/M12 records now show ``"5%"`` instead of ``"5,0 V"`` etc.
- M01/M02/M03 records now show step labels instead of ``None``
- M05/M06 records with ``t1_raw > 3`` now show ``None`` (previously
  showed a wrong-table ramp-time value)

## 0.15.0

Resolve BP-cell links from a **05-312 Easywave 52-key** remote into
specific op-points on the button store. 0.14.0 materialised the
remote's 52 op-points with correct bus addresses, but BP cells in
switch / dimmer / roller modules reference the remote with the
*other* per-button encoding — ``physical + offset`` for ``offset
∈ [0, 32)``. None of the existing resolver paths in
``_resolve_operation_point`` (direct physical, +1 sibling alias,
``bus_to_op``, IR-slot) covered that window, so links from
05-312 rockers to outputs stayed unmatched and never reached the
op-points' ``linked_modules`` lists.

The decode is fully algorithmic — no per-install table:

    offset = button_address - physical_base
        bits 4..3 → channel (1..4)
        bits 2..0 → slot
            0..2 → rocker X-1AB .. X-3AB
            3    → X-4AB if key=1 else X-5AB
            4    → channel master rocker X-AB
            5    → channel C button X-C
            6,7  → unused

Validated against the PC-software export and BP-cell scans on the
diagnostic install (modules 8CF5, 8B9C, 9418, C95D, C5C1). Every
rocker reference in the log now lands on the expected op-point
label.

### Added

- ``_build_easywave_52_lookup`` — indexes every address in the
  32-byte BP-cell window of each 52-channel button back to that
  remote's physical base. One pass over ``buttons`` at merge time.
- ``_resolve_easywave_52`` — slot-dispatch decoder for the BP-cell
  offset / key bit, returning the A-half op-point as canonical.
- ``_TWO_BUTTON_PAIRS`` extended with ``X.YA`` ↔ ``X.YB`` pairs for
  channel 1..4, row 1..5 (40 new entries). The existing channel-
  master pairs (``1A``↔``1B``..``4A``↔``4B``) already cover the X-AB
  master rockers.

### Changed

- ``_resolve_operation_point`` gains an optional
  ``easywave_52_lookup`` parameter, consulted before the direct
  physical-match path so ``offset=0`` (which would otherwise alias
  the remote's own physical) routes through slot decode instead of
  the generic resolver that has no ``KEY_MAPPING_MODULE`` entry for
  ``channels=52``.
- ``merge_linked_modules`` builds the new lookup once per call and
  passes it through. No behaviour change for non-05-312 buttons.

### Verified

- ``test_easywave_52_link_resolution`` (new): 14 ground-truth
  (button, key, expected label) tuples derived from the BP-cell
  scans on the diagnostic install, plus offset / slot integrity,
  A↔B mirror propagation, and isolation from 4/8-channel buttons.

## 0.14.0

Support the Niko **05-312 Easywave 52-key hand-held** remote. The
firmware enrols it in PC-Link inventory as device_type ``0x3D``
with ``channels=52``, which the library's catalogue already
recognised — but ``merge_discovered_buttons`` only knew how to
expand ``{1, 2, 4, 8}``-channel buttons into op-points. The
05-312 fell through with::

    ERROR Unexpected number of channels: 52 for device <physical>

The remote was silently dropped from the v2 button store, and any
BP-cell reference to its 52 sub-codes went to the unmatched
accumulator as a single physical (degenerate cluster, below the
transmitter-synthesis threshold of 8). The previous 0.13.x cluster
synthesis was the wrong layer for this — the firmware was already
telling us "this is one button with 52 sub-codes."

### Added

- ``EASYWAVE_52_KEY_MAPPING`` in ``mapping.py`` — the full 52-entry
  per-key first-byte table, derived from a real install and
  pinned with 9 tests. The label scheme matches the user's
  natural naming (``1A``..``1C`` for Ch1 base codes,
  ``1.1A``..``1.5B`` for Ch1 scenes, similarly for Ch2/3/4).
- ``KEY_MAPPING_FIRST_BYTE`` dispatch table — channel counts whose
  per-key offsets are expressed as a full 2-hex first byte rather
  than the standard single-nibble add. ``merge_discovered_buttons``
  picks the right derivation path off this table.
- ``_BUTTON_KEYS_BY_CHANNEL_COUNT[52]`` — the ordered list of 52
  key labels so op-points materialise consistently.

### Changed

- ``merge_discovered_buttons`` gains a multi-key remote branch:
  when ``KEY_MAPPING_FIRST_BYTE`` has an entry for the device's
  channel count, op-point bus addresses are built by full
  first-byte replacement of the physical's converted address
  instead of single-nibble offset.

### Migration

The next discovery run on an install with a 05-312 will materialise
it as one button-store entry with 52 op-points, names matching the
user's v1 ``.migrated`` conventions (e.g., ``Push button 1.1A
#N80E31C``). Existing installs with no 05-312 are unaffected.

### Validated

Against a real 2026-05-21 install where the physical is ``0E31C0``,
all 52 emitted bus addresses round-trip through the new merge path
byte-for-byte. The 4-channel wall-button regression test confirms
the single-nibble path is untouched.

## 0.13.1

Add diagnostic logging to ``_synthesize_remote_transmitters_from_unmatched``.
Field report (user with 52-key Easywave install) showed the
synthesis path not firing after upgrading to library 0.13.0 +
running a full module scan, even though the necessary references
should have surfaced as unmatched. Without log visibility into the
accumulator we can't tell whether (a) the accumulator stayed empty
(no unmatched refs collected — possibly a per-module merge issue),
(b) the accumulator filled with addresses whose 4-hex suffix
doesn't cluster the way 0.13.0 assumed (BP cells store physicals,
which may not share the same suffix as the bus addresses).

### Added

- ``Remote-transmitter synthesis | ...`` INFO log fired at every
  ``_complete_discovery_run``, reporting:
  - ``accumulator_size`` — total unmatched references collected.
  - ``unique_suffixes`` — distinct 4-hex suffixes seen.
  - ``top_clusters`` — top 10 (suffix, count) pairs.
  - ``sample_addresses`` — first 20 raw entries from the accumulator.
- Separate "accumulator empty" log line so the no-data case is
  unambiguous in the wild.

No behaviour change beyond the new log lines.

## 0.13.0

Recover button-store entries for multi-page RF remotes whose
emitted bus codes don't enrol in PC-Link inventory. Pre-v2 versions
of the library accumulated such entries via a more permissive
discovery gate; v2's stricter resolution layer
(``merge_linked_modules``) drops them as ``unmatched_addresses``,
so v2 installs lose them on next discovery. Hardware example: the
user's 52-key Easywave 05-312 with 13 scene pages emits 52 distinct
bus codes sharing the trailing 4-hex ``E31C`` — none of which
appear in ``$1011`` inventory frames.

### Added

- **``_synthesize_remote_transmitters_from_unmatched``** on
  ``NikobusDiscovery`` — clusters accumulated unmatched references
  by their last 4 hex characters and synthesises one virtual
  transmitter parent + N passthrough children per cluster meeting
  the threshold.
- **``REMOTE_TRANSMITTER_CLUSTER_THRESHOLD = 8``** class constant
  (= one 8-channel button's worth of A/B/C/D × 2 keys). Catches
  multi-page remotes and unenrolled 8-channel keypads while
  rejecting random small coincidences in flash garbage.
- **Remote-transmitter passthrough** in ``merge_discovered_buttons``:
  entries carrying ``remote_transmitter_bus_address`` get their
  op-point ``bus_address`` set directly from the synthesis entry,
  bypassing the ``convert_nikobus_address`` round-trip (which is
  not a strict bijection across all 24-bit values — the 3-bit
  button field gets added, not OR'd, into the bit-reversed result,
  and carries can collapse two distinct inputs onto one output).
- **Cross-module accumulators** on ``NikobusDiscovery``
  (``_accumulated_unmatched``, ``_accumulated_command_mapping``)
  collect rejected references and their originating link records
  across all module scans. After the final scan,
  ``_complete_discovery_run`` runs the synthesis pass and re-merges
  the accumulated mapping so previously-skipped links resolve.

### Changed

- ``merge_linked_modules`` now returns ``(updated_buttons,
  links_added, outputs_added, unmatched_addresses)`` — the
  unmatched set is needed for cluster synthesis. Existing tests
  unpacking the tuple updated.

### Synthesised entry shape

```
discovered_devices = {
  "RT-E31C": {                      # synthetic parent
    "category": "Module",
    "module_type": "remote_transmitter",
    "model": "RF Remote (synthesized)",
    "transmitter_suffix": "E31C",
    "transmitter_member_count": 52,
    ...
  },
  "80E31C": {                       # child, keyed by bus address
    "category": "Button",
    "channels": 1,
    "remote_transmitter_address": "RT-E31C",
    "remote_transmitter_suffix": "E31C",
    "remote_transmitter_bus_address": "80E31C",
    ...
  },
  # ... 51 more children
}
```

### Validation

7 new tests in ``test_remote_transmitter_synthesis.py``, pinning:

- Sub-threshold clusters not promoted (3 entries → no synthesis).
- Threshold (8 members) triggers synthesis.
- Full 52-member ``E31C`` install produces 1 parent + 52 children,
  keyed by original bus address.
- Multiple independent clusters → separate transmitters.
- Real enrolled buttons at cluster-member addresses aren't shadowed.
- Synthesis is idempotent across repeated calls.

## 0.12.0

Extend the PC-Logic input synthesis path to **05-206 Modular
Interface** modules. The 05-206 has the same six-input shape as
PC-Logic but with wired dry contacts instead of a logical engine,
and the working hypothesis (pending hardware confirmation on the
6E40 install) is that its firmware uses the same address-derivation
scheme.

### Added

- ``_synthesize_pc_logic_inputs`` now iterates **both** ``pc_logic``
  and ``interface_module`` modules in ``discovered_devices``,
  producing six 2-channel synthetic button entries per module.
  Function name retained for callsite stability — the behaviour
  is generalised.
- New ``pc_logic_parent_type`` provenance field on each synthesised
  entry (``"pc_logic"`` or ``"interface_module"``) so consumers can
  distinguish the two parents without lookup.
- ``NikobusDiscovery.INTERFACE_MODULE_INPUT_TYPE`` /
  ``INTERFACE_MODULE_INPUT_MODEL`` class constants
  (``"Modular Interface Input"`` / ``"05-206"``).
- ``merge_discovered_buttons`` carries ``pc_logic_parent_type``
  through alongside the existing provenance fields.

### Predicted, not yet verified on hardware

For the user's interface_module at ``0x6E40``, the formula predicts:

| Slot | Physical  | 1A primary | 1B alias |
|------|-----------|------------|----------|
| 1    | ``637201``| ``2013B3`` | ``6013B3`` |
| 2    | ``637202``| ``1013B3`` | ``5013B3`` |
| 3    | ``637203``| ``3013B3`` | ``7013B3`` |
| 4    | ``637204``| ``0813B3`` | ``4813B3`` |
| 5    | ``637205``| ``2813B3`` | ``6813B3`` |
| 6    | ``637206``| ``1813B3`` | ``5813B3`` |

These are pinned in
``test_interface_module_predicted_bus_addresses_pending_verification``
so any future correction surfaces immediately.

### Not changed

- PC-Logic 940C / 8DC8 synthesis output is byte-identical to 0.11.0
  (same formula, same provenance shape).
- ``interface_module`` continues to carry its ``channels`` array in
  the module store; only the bus-event-emitting children get
  synthesised.

## 0.11.0

Rewrite the PC-Logic logical-input physical-address derivation so it
works for installs whose PC-Logic address overflowed the 0.8.0
formula. The previous formula (`byteswap(addr) × 8`) refused to
derive inputs for any PC-Logic address whose byteswap exceeded
`0x1FFF` — confirmed against a user install where PC-Logic at
`0x8DC8` (`byteswap × 8 = 0x64468`, 17 bits) raised `ValueError` and
left the synthesis path silent. The user's v1 manual config carries
ground-truth bus addresses for that install; the rewritten formula
predicts them exactly.

### New formula

```
input_physical = 0x600000 | ((pc_logic_addr >> 1) << 4) | slot
```

Reading the layout: a constant ``0x6`` top nibble (PC-Logic module-
class marker), then `pc_logic_addr >> 1` as the next 16 bits (bit 0
of the module address is dropped — Nikobus module addresses are
conventionally even), then the slot index in the low nibble.

### Validated against

| PC-Logic | Physicals                 | 1A primary bus              | 1B alias bus                |
|----------|---------------------------|-----------------------------|-----------------------------|
| `940C`   | `64A061..64A066`          | `21814B`…`19814B`           | `61814B`…`59814B`           |
| `8DC8`   | `646E41..646E46`          | `209D8B`…`189D8B`           | `609D8B`…`589D8B`           |

All 24 bus addresses across the two installs match.

### Migration

Existing PC-Logic input button-store entries are unaffected for
940C-class installs (same physicals as 0.8.0/0.9.0/0.10.0). Installs
that previously raised `ValueError` (8DC8-class) gain synthesised
entries on next discovery — provided the migration carried over the
PC-Logic module record itself.

### Changed

- `derive_pc_logic_input_physicals` rewritten with the new formula;
  no longer raises on byteswap overflow.
- `pc_logic_address_for_input` updated to gate on the new
  ``0x6`` top-nibble marker instead of the old ``0x60..0x6F``
  slot-byte range.
- `pc_logic_input_slot_index` updated likewise.

## 0.10.0

Fix PC-Logic logical-input bus-address derivation. The synthesized
input entries from 0.8.0/0.9.0 carried op-point bus addresses
computed with the standard 2-channel key-mapping (``+8`` / ``+C``
nibble offsets), but hardware emits each press at offsets ``+0`` /
``+4``. Press lookups for PC-Logic inputs miss the button store
entirely on 0.9.0 — every press logs as ``unknown button``.
Confirmed against a 940C install on 2026-05-20: pressing slot 6
fires ``19814B`` and ``59814B``, matching ``convert(64A066)`` plus
offsets 0 and 4.

### Added

- **``PC_LOGIC_KEY_MAPPING``** in ``mapping.py`` —
  ``{2: {"1A": "0", "1B": "4"}}``. Documented as the canonical
  layout for PC-Logic logical inputs, sourced from observed bus
  events rather than a guessed extension of the wall-button table.

### Changed

- **``merge_discovered_buttons``** selects ``PC_LOGIC_KEY_MAPPING``
  for devices whose entry carries ``pc_logic_parent_address``,
  falling back to the standard ``KEY_MAPPING`` for everything else.
- Tests pin the full 6-input × 2-key bus-address table for the
  940C install so future regressions surface immediately.

### Migration

Existing button-store entries for PC-Logic input physicals
(``64A061..64A066`` on a 940C-class install) carry incorrect
``bus_address`` values in their op-points. The next discovery run
rewrites them with the correct addresses; no manual cleanup
required.

## 0.9.0

Drop the redundant ``channels`` array from PC-Logic module entries
in the module store. With 0.8.0's synthesis path, each of the 6
logical inputs is surfaced as its own button-store entry (one
``LM-INPUT N`` device under the PC-Logic module), so the module
record's own per-channel placeholders were duplicating those entries
without driving any entity.

### Changed

- **``merge_discovered_modules``** no longer writes a ``channels``
  array for ``pc_logic`` modules. Existing pc_logic entries in the
  store have their ``channels`` array stripped on next merge.
- The 05-201 ``model`` field on synthesized inputs replaces the
  invented ``05-201-LM`` (released in 0.8.0 → 0.8.1) — the
  ``LM-INPUT N`` device name already disambiguates them from the
  parent PC-Logic module.

### Unchanged

- ``interface_module`` (05-206) still carries its ``channels``
  array — it has no synthesis path yet.
- ``discovered_info.channels_count`` is preserved on pc_logic
  entries for provenance.

## 0.8.0

Surface PC-Logic (05-201) logical inputs as virtual button entries.
PC-Logic's 6 logical inputs emit bus events when triggered (from
hardware buttons or from PC-Logic's own programming), but the
addresses they emit are **not stored** in any flash region the
discovery sweep reaches — they are computed by firmware from the
PC-Logic's own bus address. Validated on the 2026-05-18 install
(PC-Logic at ``940C`` → inputs fire as ``64A061..64A066``,
producing 12 bus events when all 6 are triggered).

### Added

- **``derive_pc_logic_input_physicals(pc_logic_address,
  channel_count)``** in ``protocol.py`` — returns the list of
  6-hex physical addresses for a PC-Logic module's logical inputs.
  Formula: ``((byteswap(addr) * 8) << 8) | (0x60 + slot)`` for
  slot index 1..N.
- **``pc_logic_address_for_input(input_physical)``** — inverse of
  the above, returns the parent PC-Logic address (or ``None`` if
  the input physical doesn't fit the pattern).
- **``pc_logic_input_slot_index(input_physical)``** — returns the
  slot number 1..N for a given input physical.
- **``NikobusDiscovery._synthesize_pc_logic_inputs``** — invoked
  at the end of Stage 1 inventory enumeration. For each
  ``pc_logic`` module in ``discovered_devices``, adds
  ``category="Button"`` entries for its derived input physicals,
  tagged with ``pc_logic_parent_address`` and
  ``pc_logic_slot_index`` provenance fields. The regular
  ``merge_discovered_buttons`` path then writes these into the
  button store as 2-channel virtual buttons.
- **Provenance pass-through in ``merge_discovered_buttons``** —
  the two ``pc_logic_*`` fields survive the merge so HA-side
  rendering can parent the device under the PC-Logic module
  instead of the wall-buttons category.

### Compatibility

- **Production unchanged** for installs without a PC-Logic module
  in inventory (the synthesis loop short-circuits when no
  ``pc_logic`` entry exists).
- **No protocol change.** The synthesis is pure local math
  against addresses already in ``discovered_devices``.

### Known limits

- The formula has only been validated on **one PC-Logic address**
  (``940C``). The derivation refuses (raises ``ValueError``) when
  ``byteswap(addr) * 8`` would overflow 16 bits — e.g. addresses
  with byteswap ≥ ``0x2000``. If a future install reports a
  PC-Logic outside the validated range, the synthesis will skip
  that module with a warning rather than emit incorrect addresses.
  Capture a fresh bus-event log + module address from such an
  install to extend the formula.
- The synthesis assumes the **slot byte starts at ``0x60 + slot``**.
  If a future install allocates slots from a different base (e.g.
  ``0x70..`` or per-PC-Logic-instance bases), the formula needs
  the per-install offset to be discoverable. No evidence of this
  yet — current data is consistent with a universal ``0x60`` base.

## 0.7.0

Forensic register-range mode on ``query_module_inventory``. Lets the
caller scan a caller-supplied register range with a caller-supplied
sub-byte on any module address, bypassing the per-module-type
tuning (``_scan_range_for_sub``) and the non-output-module
early-return guard. Use it to reverse-engineer storage layouts of
modules the production path declines (interface modules, audio
modules) or to focus a scan on a specific region of a module that
production already scans (e.g. inspect ``0x70..0x83`` of PC-Logic
on sub-byte ``01`` to look for the BP-cell layout a vendor trace
revealed).

### Added

- **``query_module_inventory(device_address, *, register_start,
  register_end, sub_byte)``** — three new optional keyword
  arguments. When ``register_start`` and ``register_end`` are both
  provided, the scan walks exactly that range with the given
  ``sub_byte`` (default ``"04"``), skips the extra-pass logic, and
  bypasses the non-output-module guard. Both range bounds are
  required together; an inverted range, an out-of-bounds value, or
  using forensic mode together with ``device_address="ALL"`` raises
  ``ValueError``.

### Compatibility

- **Production path unchanged.** Existing callers that omit the new
  kwargs see exactly the same behaviour as 0.6.0: per-module-type
  range tuning, extra passes, non-output-module guard.
- **No protocol change.** The forensic mode reuses the same
  ``$1410 <addr> <reg> <sub>`` reads the production path uses; only
  the range and the per-module guard differ.

### Why this matters

Reverse-engineering Nikobus's PC-Logic output storage required
scanning register ranges the production code doesn't normally
touch. Calling the internal ``_scan_module_registers`` helper
directly worked but wasn't a stable surface. This API surfaces the
forensic capability as a documented parameter set, used in the
Nikobus-HA integration by the ``nikobus.query_module_inventory``
service action (which gains matching ``register_start`` /
``register_end`` / ``sub_byte`` fields in the same release cycle).

## 0.6.0

Revert chunk decoding to the 0.8.0 (pre-rewrite) single-alignment
walk. Drops the alternate-alignment dual-pass and the decoder-side
``is_known_button_canonical`` / ``_is_garbage_chunk`` filters that
were added to mop up the phantoms the dual-pass produced.

### Background

The 0.5.5..0.5.24 chunker ran every per-module scan against three
alignments in parallel — primary (offset 0) plus alternates at
stream offsets 4 and 8 — added to recover records from two real
user captures (2026-04-30 and 2026-05-04) that appeared to live at
non-zero offsets. The decoder gates were added to filter phantoms
from the misaligned passes.

Re-analysing the 2026-05-15 log (Gen3 user, install programmed
via DIN-button learn mode without ever using Niko PC software)
showed that:

1. The Gen3 byte layout decodes the records on this install
   correctly — at offset 0, with no firmware variation.
2. The ``unknown_button`` skip-reason flood that drove the previous
   "pre-Gen3 firmware has a different byte layout" hypothesis was
   actually the inventory gate rejecting real records because the
   PC-Link inventory was incomplete (the user programmed via DIN
   buttons, so the PC-Link never wrote the buttons to its
   ``0xA0..0xFF`` registry area).
3. The alternate-alignment "productive offsets" finding from the
   2026-05-04 capture appears to be byte-slop from misaligned
   windows passing the shape checks rather than genuine firmware
   variation.

The 0.8.0 walk (``idx += expected_len``, single alignment, no
phantom filters beyond ``_is_all_ff`` for empty slots) is the
simpler model. Records pack contiguously from offset 0; the
``payload_buffer`` threads partial records across frame boundaries;
the decoders accept every chunk that passes shape checks
(``mode`` in range, ``channel`` in range).

### Changed

- **``chunk_decoder.py``** — single-alignment walk only. Removed
  ``_ALT_ALIGNMENT_SKIP_CHARS``, ``_alt_payload_buffers``, and the
  alternate-alignment loop in ``analyze_frame_payload``.
  ``reset_scan_buffers`` is now a no-op stub; subclasses
  (``pc_link_decoder``, ``pc_logic_decoder``) keep their own
  registry-clearing overrides via ``super()``.
- **``switch_decoder.py`` / ``dimmer_decoder.py`` /
  ``shutter_decoder.py``** — no longer call
  ``_is_garbage_chunk`` or ``is_known_button_canonical``. Decoders
  emit any chunk that passes ``_is_all_ff``, mode, and channel
  checks. The helpers themselves remain in ``protocol.py`` for
  external callers and the merge-layer 8-channel ``+1`` alias
  consumer.

### Removed

- ``BaseChunkingDecoder._alt_payload_buffers`` and
  ``_alt_first_frame_skip_pending`` state.
- ``_ALT_ALIGNMENT_SKIP_CHARS`` module-level table.
- Decoder-side calls to ``_is_garbage_chunk`` and
  ``is_known_button_canonical``.
- Four alt-alignment regression tests in
  ``tests/test_chunk_buffering.py``.
- Three decoder-side phantom-rejection tests in
  ``tests/test_inventory_guard.py`` (the standalone
  ``is_known_button_canonical`` unit tests and the
  positive-decode tests remain).

### What to expect

- Installs whose modules answer at offset 0 (every Gen3 install
  observed to date plus the 2026-04-30 install) → unchanged
  behaviour.
- Installs programmed via DIN-button learn mode (PC-Link inventory
  incomplete or empty) → real button-link records now reach the
  merge layer instead of being rejected as ``unknown_button``;
  ~30-40 buttons per install surface where previously none did.
- Installs whose records were reported at offset 4 / 8 → need
  re-testing. If real records genuinely sit at those offsets on
  that firmware (rather than being misalignment artefacts), this
  revert regresses them. Capture from the affected install will
  confirm or invalidate the previous finding.

## 0.5.24

Catalogue housekeeping. Removes three Reserved entries from
``DEVICE_TYPES`` that the 2026-05-15 pre-Gen3 PC-Link forensic
confirmed are firmware diagnostic-echo artifacts, not real device
types.

### Removed

- **``DEVICE_TYPES["14"]``** — Reserved 0x14
- **``DEVICE_TYPES["24"]``** — Reserved 0x24
- **``DEVICE_TYPES["34"]``** — Reserved 0x34

### Why

A user on a pre-Gen3 PC-Link (model 05-200, original RS232 hardware)
ran discovery and the library fabricated a phantom button at
``0A0908`` (type ``0x04``). The DEBUG log showed PC-Link's inventory
response carrying this exact byte pattern across four consecutive
register reads:

```
2E909E 000102030405060708090A0B0C0D0E0F 0A23CB
2E909E 101112131415161718191A1B1C1D1E1F 4F720E
2E909E 202122232425262728292A2B2C2D2E2F 808184
2E909E 303132333435363738393A3B3C3D3E3F C5D0BA
```

Each register returns ``[N, N+1, N+2, ..., N+15]``. This is the
firmware's "no inventory programmed" diagnostic response — a
sequential identity placeholder that keeps the bus protocol sane
when the flash has never been written. The same pattern was
documented in 2026-05-04 from a different user's PC-Logic dump
(80D9, full sweep returned this pattern across 248 of 256 reads).

Our decoder reads byte 7 of each response as the device-type. With
this echo pattern:

| Register | Byte 7 | Decoded as |
|----------|--------|------------|
| `0xA0` | `0x04` | 05-342 Bus push button (real type, but by coincidence) |
| `0xA1` | `0x14` | "Reserved 0x14" (echo artifact) |
| `0xA2` | `0x24` | "Reserved 0x24" (echo artifact) |
| `0xA3` | `0x34` | "Reserved 0x34" (echo artifact) |

The Reserved entries for ``0x14`` / ``0x24`` / ``0x34`` had been
in DEVICE_TYPES since 0.5.4 — originally added to silence the
"Unknown device detected" warning that fired on installs with this
exact pattern (which we now know are pre-Gen3 PC-Link diagnostic
responses, not legitimate hardware). With the entries removed,
the WARNING fires correctly, surfacing the malformed frame as the
indicator it always was: not a known device type that needs
cataloguing, but a firmware-format mismatch that needs a different
fix path.

### What stays

- ``DEVICE_TYPES["05"]`` — kept. May or may not be a similar
  artifact; insufficient evidence to remove yet.
- ``DEVICE_TYPES["46"]`` — kept. Same reasoning.
- ``DEVICE_TYPES["3B"]`` — kept. Explained separately as
  PC-Logic BP-cell stride records (not a device type, but
  intentionally catalogued to suppress the warning on installs
  with PC-Logic 05-201).

### What this does NOT fix

The same user's Stage-2 module register scans (against output
modules ``5E1D`` / ``59BC`` / ``A303`` / ``150B`` / ``5875``,
all Gen2 ``-02`` revision) return data in an unrecognised format
— mostly all-FF with sparse single non-FF bytes, fundamentally
different from the Gen3-era 6-byte record format the library
decodes. Auto-discovery of button-link records on pre-Gen3 / Gen2
installs requires either:

- A Niko PC software ``Read configuration`` trace from such an
  install (which this user can't provide — no Windows PC), to
  reverse-engineer the pre-Gen3 module storage layout.
- An option in the integration to fall back to manual YAML
  button declaration when auto-discovery yields nothing — the
  v1 path that previously worked for this user.

The next release will likely add the YAML fallback option so
users on mixed-generation installs can configure manually
alongside the auto-discovery path that works for Gen3 hardware.

### Tests

- ``test_reserved_device_types_are_catalogued`` parametrize set
  reduced from 6 to 3 entries (only ``0x05``, ``0x3B``, ``0x46``
  remain Reserved).
- New: ``test_pre_gen3_echo_pattern_types_are_not_catalogued``
  parametrized over the removed three — pins that re-adding them
  would surface as a test failure, preventing accidental
  regression.
- ``test_reserved_category_does_not_trigger_unknown_warning``
  updated to use ``0x05`` (still Reserved) instead of ``0x14``
  (no longer Reserved).
- 278/278 tests pass.

## 0.5.23

Housekeeping pass after a codebase audit across the 0.5.0 → 0.5.22
iteration cycle. The audit checked for dead code, unused imports,
stale comments referencing removed features, and consolidation
opportunities across the output-module decoders.

### Removed

- **Two unused imports in ``discovery.py``** —
  ``EXPECTED_CHUNK_LEN`` (from ``dimmer_decoder``) and
  ``CHANNEL_MAPPING`` (from ``mapping``). Leftover from earlier
  refactor passes. No behaviour change.

### Audit findings preserved as-is

The audit also identified one larger opportunity that we chose
NOT to take:

- The three output-module decoders (``switch_decoder``,
  ``dimmer_decoder``, ``shutter_decoder``) share ~60 lines of
  near-identical validation scaffolding (all-FF guard, garbage
  chunk guard, length check, mode-mapping lookup, button-canonical
  gate, push-button resolution). A shared helper could DRY this
  up, but the byte-offset differences per module type would push
  most of the variation into the helper's parameter surface,
  trading mechanical duplication for indirection. The three
  decoders are stable, have been validated against multiple
  installs, and are very straight-line readable as-is. Defer.

All other audit categories (defensive ``# pragma: no cover``
handlers, module-level constants in ``discovery.py``, test
fixtures, stage-1/2 historical comments in the PC-Link / PC-Logic
decoders) came back clean.

## 0.5.22

Surfaces decoder scan-source provenance so HA-side reconciliation
can distinguish current programming (output-module link tables)
from potentially-stale PC-Link / PC-Logic registry records.

Motivated by fdebrus's Nikobus-HA #319 IKIKN forensic:

> 17 valid buttons → 23 records, all 6 bytes, all M05 (Impulse)
> 25 phantom buttons → 26 records, all 16 bytes, mixed modes
> Zero overlap between the two record sets.

The 25 phantom buttons are previous-owner residue. Their records
live only in PC-Link's registry (the previous owner used Niko's PC
software). The current owner reprogrammed via DIN-button learn-mode
which writes only to output modules' own tables, leaving PC-Link's
registry untouched. The library decoded both sources and merged
them into ``nikobus_button`` indistinguishably, so the HA side
classified all 25 as ``active``.

### Added

- **``record_source`` field on every entry in
  ``linked_modules[].outputs[]``.** Labels the decoder scan source:

  | Value | Source path | Reliability |
  |-------|-------------|-------------|
  | ``"output_module_table"`` | Switch / dimmer / roller module's own link table | Current programming, authoritative |
  | ``"pc_link_registry"`` | PC-Link's register memory (16-byte records) | May be stale residue |
  | ``"pc_logic_registry"`` | PC-Logic's register memory (16-byte records) | May be stale residue |

  Absent from legacy data (pre-0.5.22 stores) — HA-side treats
  absence as "source unknown" rather than guessing.

- **Module decoder annotations**:
  - ``switch_decoder.decode`` → ``"output_module_table"``
  - ``dimmer_decoder.decode`` → ``"output_module_table"``
  - ``shutter_decoder.decode`` → ``"output_module_table"``
  - ``pc_record_parser.link_record_to_decoded_metadata`` accepts a
    new keyword-only ``record_source`` parameter; defaults to
    ``None`` so legacy callers are unaffected.
  - ``pc_link_decoder._decode_and_log`` derives the label from
    ``module_type``: ``"pc_link_registry"`` for ``pc_link``,
    ``"pc_logic_registry"`` for ``pc_logic``.

### Changed

- **``add_to_command_mapping``** copies ``record_source`` from the
  decoded metadata into the ``output_definition`` it constructs.
- **``merge_linked_modules``** writes ``record_source`` into the
  per-output entry stored under
  ``button["operation_points"][key]["linked_modules"][i]["outputs"][j]``.
  Field is omitted from the stored entry when the source is
  unknown (None) — keeps the schema clean for legacy data.

### Recommended HA-side filter

The HA reconciliation pass adds one rule:

```python
def output_is_registry(output: dict) -> bool:
    return output.get("record_source") in {
        "pc_link_registry",
        "pc_logic_registry",
    }

# After the existing classification:
if all_outputs_are_registry(button):
    button["status"] = "legacy_orphan"   # or new bucket "legacy_registry_only"
```

Buttons with at least one ``output_module_table`` source stay
``active``. Buttons with only registry sources → residue bucket.

For IKIKN's install after this lands:
- 14 valid + active (unchanged)
- 3 valid + legacy_undecoded (unchanged)
- 25 phantom + ``legacy_orphan`` ← was ``active`` (the bug fix)
- 9 phantom + legacy_undecoded (unchanged)

### Tests

- ``tests/test_record_source_provenance.py`` (new file, 8 tests):
  - Output-module decoders emit ``"output_module_table"``
    (3 tests: switch, dimmer, shutter).
  - ``link_record_to_decoded_metadata`` emits the supplied label
    (2 tests: ``pc_link_registry``, ``pc_logic_registry``).
  - Backward-compat: omitting ``record_source`` keeps the field
    out of the metadata (1 test).
  - End-to-end: ``record_source`` survives through
    ``merge_linked_modules`` into the persisted store
    (1 test).
  - Legacy data: missing ``record_source`` on decoded command →
    field absent (not None) in stored entry (1 test).

- Full suite: 278/278 pass.

### Backward compatibility

- Schema is purely additive — existing readers that ignore the new
  field keep working unchanged.
- ``link_record_to_decoded_metadata``'s new ``record_source``
  parameter is keyword-only with default ``None`` — no positional-arg
  callers are affected.
- HA-side migration: optional. Without the new filter rule, the
  field is harmless but doesn't fix IKIKN's residue. With the
  filter rule, the 25 phantom-active buttons re-bucket to
  legacy_orphan and disappear from the active button view.

## 0.5.21

Simplification pass on `detect_stale_inventory` after fdebrus's
2026-05-12 IKIKN log proved the outer retry layer was actively
harmful. The command pipeline already has a 3-attempt retry budget
in `_wait_for_ack_and_answer`; layering another retry on top of it
caused real modules to be starved when an absent module hogged the
queue.

### Removed

- **`detect_stale_inventory` `timeout` kwarg** — no longer needed.
  The call no longer wraps `get_output_state` in
  `asyncio.wait_for`. Each probe runs to the command pipeline's
  natural conclusion (`MAX_ATTEMPTS=3` × per-attempt timeout).
- **`detect_stale_inventory` `max_attempts` kwarg** — the per-probe
  retry budget is now the command pipeline's `MAX_ATTEMPTS=3`.
  Adding more layers on top duplicated work and caused starvation.
- **`detect_stale_inventory` `retry_delay` kwarg** — only useful
  paired with the removed inner retry loop.
- **`get_output_state` `timeout` kwarg** — added in 0.5.20 to work
  around the dedup race, no longer needed without an outer
  `wait_for` racing the queue.

### Changed

- **Probes run serially in queue order, no caller-side timeout.**
  Each call to `get_output_state` waits the command pipeline's
  full retry budget (~15 s worst case for an absent module). A
  slow absent module ahead of a real module in the probe order
  delays subsequent probes — but **does not starve them**, which
  was the Nikobus-HA #319 IKIKN bug. The previous (0.5.18-0.5.20)
  outer wrap raced the dedup mechanism: when the outer 2-s cap
  fired, the still-queued command's dedup key blocked retries,
  and the real module's wire send never happened.

### Kept

- **`outer_attempts` and `outer_delay` kwargs** (defaults 1, 0.0)
  — opt-in second sweep with bus-quiesce delay between passes,
  useful on installs where transient bus jams cause a real
  module's first pass to fail and a second pass after the bus
  settles to succeed.
- **`get_output_state` dedup-clear-on-cancel** — still useful when
  the calling task gets cancelled higher up (integration shutdown,
  user cancels discovery).
- **`_process_commands` cancelled-future skip** — still useful
  defence against stale commands.

### Wallclock budget after simplification

For IKIKN's 3-module probe (1CEC fast / 3D28 absent / 8110 slow):
- 1CEC: ~0.25 s (fast ACK)
- 3D28: ~15 s (command pipeline's 3 attempts × 5 s)
- 8110: ~0.5-3 s (waits behind 3D28, then ACKs on first attempt)

Total: ~18 s. The previous (0.5.20) version would have spent ~17 s
exhausting outer retries on 3D28 with the outer 2-s cap racing the
queue, then false-negatived 8110 because its wire send never
happened. Now 8110 lands correctly as present.

### Tests

- Rewrote `tests/test_stale_inventory_detection.py` for the
  simplified API (12 tests).
- Rewrote `tests/test_command_dedup_race.py` for cancellation-
  not-timeout cases (3 tests).
- New: `test_detect_stale_inventory_no_starvation_on_slow_first_module`
  — pins the IKIKN architectural fix.

## 0.5.20

Two bug fixes from fdebrus's Nikobus-HA #319 forensic on the IKIKN
install (2026-05-10 logs). Both shipped together because they share
a single field report and the Bug-2 fix unblocks dropping HA-side
workarounds for Bug 1.

### Fixed

#### Bug 1: ``discovered_devices`` / ``inventory_query_type`` cleared before ``on_discovery_finished`` fires

Pre-0.5.20: ``_complete_discovery_run`` called ``reset_state()``
**before** ``_notify_discovery_finished``. ``reset_state(update_flags=True)``
sets ``coordinator.inventory_query_type = None``, so by callback
entry the field was already None. Consumers couldn't snapshot it
even at the top of the callback (Nikobus-HA #329 / #331 confirmed
this with explicit early snapshots).

**Fix** (per fdebrus's design note):

1. **Split the reset.** Discovery-in-progress flags
   (``discovery_running``, ``discovery_module``,
   ``discovery_module_address``) are flipped to ``False`` /
   ``None`` **before** the callback. This lets the consumer
   re-enter the library inside the callback (e.g. to ``await
   detect_stale_inventory()``) without tripping any "discovery
   already running" guard.
2. **Pass state as kwargs.** ``on_discovery_finished`` now
   receives:
   ```python
   async def on_discovery_finished(
       *,
       discovered_devices: dict,
       inventory_query_type: InventoryQueryType | None,
   ):
       ...
   ```
   Consumers don't need to read mutable instance state at all.
3. **Clear instance fields after** the callback returns. By that
   point the consumer has either snapshotted what it needed (via
   the kwargs) or completed any synchronous work.

**Backward compat**: pre-0.5.20 callbacks with no-arg signatures
(``async def cb()``) are still supported. ``_notify_discovery_finished``
inspects the callback signature and calls accordingly:
  - explicit ``discovered_devices`` / ``inventory_query_type``
    parameters → passed by keyword
  - ``**kwargs`` → both passed
  - no parameters → called with no args (legacy path)

#### Bug 2: ``detect_stale_inventory`` retries suppressed by queue dedup

Pre-0.5.20: ``detect_stale_inventory`` wrapped ``get_output_state``
in ``asyncio.wait_for(timeout=2.0)``. The IKIKN trace showed:

```
T=0    detect_stale_inventory → get_output_state("1CEC", group=1)
       → creates F1, queue_command adds "1CEC_1" to dedup set,
         command queued behind a slow 3D28 probe
       → wait_for(F1, timeout=2.0)
T=2    outer timeout fires, F1 cancelled
       → BUT dedup key "1CEC_1" still in set (cmd never popped)
T=2.5  retry → get_output_state again
       → creates F2, queue_command sees dedup key → SUPPRESS
       → F2 never resolves → 1CEC false-negatived as absent
T=15+  3D28 finally finishes, 1CEC popped, wire send happens,
       result lands on cancelled future → no-op
```

**Fix** — three coordinated changes:

1. **``get_output_state`` accepts a ``timeout`` keyword argument**
   (defaults to ``None`` → use library
   ``COMMAND_ACK_WAIT_TIMEOUT=15 s``). Callers should pass an
   explicit timeout rather than wrapping in ``asyncio.wait_for``.
   In the function's ``except (Cancelled, Timeout)`` branch the
   dedup key is now also discarded — so the next call for the
   same address re-queues cleanly instead of being suppressed.

2. **``_process_commands`` skips cancelled futures** on pop. If
   the caller's future was cancelled before the processor got to
   the command (e.g. blocked behind a slow probe), the wire send
   is skipped and the dedup key cleared. Avoids wasted bus
   traffic and resolves the corner where a stale command would
   suppress an in-flight retry.

3. **``detect_stale_inventory`` drops the outer
   ``asyncio.wait_for``** and passes ``timeout`` directly to
   ``get_output_state``. The retries inside the inner-attempt
   loop now actually reach the wire.

After this, ``attempts=N`` accounting is accurate: each attempt
corresponds to exactly one queued+sent (or cancelled-before-send)
command.

### Added

- **``outer_attempts`` and ``outer_delay`` kwargs on
  ``detect_stale_inventory``.** Default ``outer_attempts=1``,
  ``outer_delay=0.0`` preserve pre-0.5.20 single-pass behaviour.
  Use ``outer_attempts=2, outer_delay=3.0`` (recommended for the
  Nikobus-HA integration) to add a bus-quiesce window between
  probe rounds. Each outer pass skips modules already classified
  ``present``. This lets the HA side drop the outer retry loop
  from PR #329 once 0.5.20 lands.

### Tests

- ``test_complete_discovery_run_callback_signature_compat`` — pins
  signature detection: no-arg callbacks, ``**kwargs`` callbacks,
  and explicit-param callbacks all dispatch correctly.
- ``tests/test_command_dedup_race.py`` (new file, 3 tests) —
  Bug-2 fix pins: ``get_output_state`` accepts ``timeout`` kwarg,
  clears dedup on timeout, and ``_process_commands`` skips
  cancelled futures.
- ``test_detect_stale_inventory_outer_loop_recovers_module_after_quiesce``
  — outer-loop fixture pin: a module that fails inner-attempt
  loop on pass 1 but ACKs on pass 2 must classify as ``present``.
- ``test_detect_stale_inventory_outer_loop_skips_already_present`` —
  classified-present modules not re-probed on subsequent passes.
- ``test_detect_stale_inventory_outer_attempts_default_matches_pre_0_5_20``
  — backward-compat pin: ``outer_attempts=1`` makes wire-send
  count identical to 0.5.19.
- Extended ``test_detect_stale_inventory_defaults_pinned`` to
  cover all five keyword defaults.
- Test mocks updated to accept ``timeout`` kwarg
  (``get_output_state(addr, group, *, timeout=None)``).

### Notes

- The ``discovery_running`` flag now transitions ``True → False``
  **before** the callback fires (was: after). Per fdebrus's design
  note: the integration's post-discovery reconciliation runs inside
  the callback and would otherwise trip a "discovery already
  running" guard.
- Total tests: 275 passing.

## 0.5.19

### Added

- **``NikobusDiscovery.detect_stale_inventory()`` retry support.**
  Two new keyword arguments:

  - ``max_attempts: int = 3`` — number of probe attempts per module
    before classifying as absent. Default of 3 (up from the implicit
    1 in 0.5.18) makes the new retry behaviour the default.
  - ``retry_delay: float = 0.5`` — sleep between attempts for the
    same module, in seconds. Skipped after the final attempt. Set
    to ``0`` for back-to-back retries (useful in tests).

  Field report from the Nikobus-HA side (2026-05-10 IKIKN install):
  with ``max_attempts=1`` and ``timeout=2.0`` (the 0.5.18 contract)
  a real ``switch_module`` at ``8110`` false-negatived because its
  ``$1012`` ACK landed at 2.0-3.0 s under post-discovery bus
  congestion. With three attempts at 2 s each, ``8110`` lands as
  ``present`` on attempt 2.

  Worst-case wall-clock per probe with the new defaults:
  ``max_attempts × timeout + (max_attempts - 1) × retry_delay``
  = ``3 × 2.0 + 2 × 0.5`` = **7 s per module**. An 8-module probe
  with all attempts timing out completes in ~59 s — acceptable for
  a manual discovery action.

  Callers wanting the pre-0.5.19 single-attempt contract can pass
  ``max_attempts=1``.

### Tests

- ``test_detect_stale_inventory_retries_slow_module_to_present`` —
  IKIKN-fixture pin: a module whose ACK lands on attempt 2 must
  classify as ``present``. Asserts ``call_counts["8110"] == 2`` so
  the retry path is exercised exactly as designed.
- ``test_detect_stale_inventory_max_attempts_one_preserves_pre_0_5_19_behaviour`` —
  pins the opt-out at ``max_attempts=1``: each address probed
  exactly once.
- ``test_detect_stale_inventory_retry_delay_zero_skips_sleep`` —
  pins that ``retry_delay=0`` skips the inter-attempt sleep.
  Useful for tests that need fast absent-classification.
- Renamed ``test_detect_stale_inventory_default_timeout_is_two_seconds``
  to ``test_detect_stale_inventory_defaults_pinned`` and extended
  to cover all three keyword defaults via ``inspect.signature``.

### Notes for Nikobus-HA

The IKIKN field report also surfaced the broader pattern that
post-discovery probing alone is fragile under bus congestion. The
HA side has already responded with a combined-predicate eviction
(Nikobus-HA #328): a module is evicted only if it BOTH fails the
probe AND was not in the current inventory sweep. With this PR's
retry support, ``detect_stale_inventory`` becomes more reliable as
a single signal again, but the combined-predicate stays the
defence-in-depth.

## 0.5.18

### Changed

- **``NikobusDiscovery.detect_stale_inventory()`` default ``timeout``
  raised from 0.6 s to 2.0 s.** Real-install report from fdebrus on
  0.5.17 (https://github.com/fdebrus/Nikobus-HA/issues/319): on a
  9-module install where discovery correctly surfaced all 9, the
  bus-presence probe (default 0.6 s) misclassified 3 of 6
  output-bearing modules as absent. The HA-side then dropped them
  from ``nikobus.modules`` even though they exist on the bus.

  Root cause: ``get_output_state`` (the underlying ``$1012<addr>``
  helper) has a 15 s inner ACK timeout × 3 retries. On a busy bus
  — queue not yet drained after a fresh discovery, modules
  momentarily serving button presses — a present module can take
  1-2 s to ACK. The original 0.6 s outer cap raced the queue and
  fired before the present module's ACK arrived, classifying it as
  absent.

  2.0 s absorbs queue-drain latency and momentary busy-states
  without changing the manifest's contract. An 8-module probe
  now completes in ~16 s worst-case (all timing out) instead of
  ~5 s — acceptable for a manual discovery action.

  Callers passing an explicit ``timeout=`` kwarg are unaffected.

### Tests

- ``tests/test_stale_inventory_detection.py`` adds
  ``test_detect_stale_inventory_default_timeout_is_two_seconds`` —
  pins the new default via ``inspect.signature``. Future regressions
  back toward 0.6 s fail fast instead of silently re-introducing
  the false-negative bug.

## 0.5.17

### Removed

- **PC-Link inventory all-FF terminator (0.5.13 / 0.5.14)** — the
  early-stop heuristic that drained the queue on the first all-FF
  response after at least one real record. Issue #319 (Nikobus-HA)
  reported a user with 9 known modules where discovery surfaced
  only 6: their PC-Link's project memory has a legitimate all-FF
  gap mid-project (deleted module, slot zero-erased), and the
  terminator dropped every record past the gap. The trace evidence
  that motivated the original 0.5.13 terminator (fdebrus's 2024-05-24
  Niko PC software capture stopping at register C3) was a contiguous
  install where the terminator happened to coincide with the project
  end — never validated against gapped projects.

### Changed

- **All-FF inventory responses are now skipped, not terminated.** The
  PC-Link sub=04 sweep over ``range(0xA0, 0x100)`` always runs the
  full 96 registers. ``parse_inventory_response`` treats an all-FF
  payload as "no record at this slot, continue" — the pre-0.5.13
  behaviour. Removed the ``_pc_link_inventory_terminator_seen`` /
  ``_pc_link_inventory_data_seen`` state flags, the
  ``_is_pc_link_inventory_terminator`` predicate, and the
  ``drain_queue`` call in the inventory path.

- Residue filtering moved entirely to the post-discovery layer:

  1. ``detect_stale_inventory()`` (added in 0.5.16) probes each
     output-bearing module via ``$1012<addr>`` and returns the
     ``absent_modules`` / ``orphaned_buttons`` manifest.
  2. The HA-side discovery flow (``Nikobus-HA`` 2.0.x) consumes the
     manifest to drop absent modules from the persisted store and
     classify orphan buttons.

  Bus-presence is a strictly stronger signal than register-content
  patterns — actual hardware response, not a heuristic. The read-layer
  terminator was a pre-0.5.16 workaround; with the probe wired in,
  it's redundant and (per #319) actively harmful on gapped projects.

### Cost / behaviour notes

- Wall-clock cost: ~5-9 s extra per discovery on a contiguous install
  (96 reads × ~150 ms = ~14 s; old early-stop saved between 5-9 s
  depending on project size). Discovery is a manual user action, not
  a hot path — acceptable trade for never silently dropping records.
- Second-hand PC-Link installs (residue from previous owner): the
  store will now receive residue records at scan time; the HA-side
  must call ``detect_stale_inventory()`` post-scan and prune. Without
  that wire-up, residue persists in the JSON stores. Pre-0.5.17 the
  terminator filtered residue at read time as a defence-in-depth;
  that defence is removed because it can't distinguish residue from
  legitimate gaps.

### Tests

- ``tests/test_pc_link_inventory_terminator.py`` rewritten (7 tests)
  to pin the new contract: leading FF doesn't drain, FF-after-data
  doesn't drain, multiple consecutive FF blocks don't drain, full
  ``range(0xA0, 0x100)`` is always queued, ``drain_queue`` is never
  called from the inventory path, and — the bug-fix pin for #319 —
  ``test_record_after_ff_gap_is_decoded`` proves that a record
  arriving after an all-FF block is still decoded into
  ``discovered_devices``.

## 0.5.16

### Added

- **``NikobusDiscovery.detect_stale_inventory()``** — bus-presence
  cross-check for inventory entries left over from a previous install
  on the same PC-Link. Reverse-engineering note: Niko's PC software
  writes new programming on top of old register space but doesn't
  zero-fill unused slots, so a second-hand PC-Link's flash still
  carries the previous owner's module / button records. The user
  reporting this had a clean install with three modules in their
  inventory dump but only two of them physically present — the third
  was the previous owner's hardware. Their inventory dump also
  showed 34 stale buttons across the 0x3Bxx-0x3Exx and 100xxx-102xxx
  address bands.

  The new method:

  1. Iterates output-bearing module addresses
     (``switch_module`` / ``dimmer_module`` / ``roller_module``) in
     ``coordinator.dict_module_data``.
  2. Sends ``$1012<addr>`` (output-state group 1) to each. Modules
     replying within ``timeout`` (default 0.6 s) classify as
     ``present_modules``; modules timing out classify as
     ``absent_modules``.
  3. Iterates ``button_data["nikobus_button"]`` and flags any button
     whose ``linked_modules`` set is a non-empty subset of
     ``absent_modules`` as ``orphaned_buttons``. Buttons with mixed
     present + absent links stay (they still drive something real);
     buttons with no links at all stay (they may just be undecoded
     so far).

  Returns a manifest the caller decides what to do with — surface in
  HA UI, auto-purge ``nikobus_module.json`` /
  ``nikobus_button.json``, etc. The library deliberately doesn't
  mutate the persisted stores; the integration's HA-side service
  handler does.

  Non-output module types (``pc_link``, ``pc_logic``, ``feedback_module``,
  ``audio_module``, ``interface_module``) are excluded from the
  probe pass — they either ARE the bridge or don't respond uniformly
  to ``$1012`` queries, so a probe failure there can't be safely
  interpreted as "stale".

  Originally drafted as PR #47 against a 0.5.13 base, then closed
  in favour of the all-FF terminator approach (PR #48 → 0.5.13,
  PR #49 → 0.5.14). Revived after the 2026-05-08 vendor trace
  (see below) confirmed Niko's PC software has no bus-protocol
  filter for residual records — it sidesteps the residue problem
  by reading from the saved project file rather than bus-scanning.
  Our library has no project file, so a presence cross-check is
  the only reliable way to surface stale entries.

- **``_VENDOR_REGISTER_MAP_BY_SUB``** — vendor's per-(sub-byte,
  register) read sequence captured from a Niko PC software COM3
  trace on 2026-05-08 against module ``0x3D82`` executing "load
  current installation". Reference data only; NOT wired into the
  scan loop.

  Captured per sub-byte:

  - ``sub=00`` → 6 specific regs (header / identity):
    ``0x05, 0x06, 0x07, 0x08, 0x09, 0x3E`` (reg ``0x3E`` read TWICE
    back-to-back as a sanity probe).
  - ``sub=01`` → 37 regs in ``0x70..0x96`` (link table + checksum):
    ``0x70..0x93`` contiguous (36 regs) plus ``0x96`` (vendor
    deliberately skips ``0x94`` and ``0x95``). Reg ``0x96`` read
    at both start and end of the readout — likely a
    concurrent-modification detector.
  - ``sub=04`` → 5 regs in ``0x65..0x69`` (status / state).

  Total: 48 register reads per module vs. our current ~167 (~3.5x
  faster). The most striking divergence is sub=04: the vendor
  reads ``0x65..0x69`` while our scan covers ``0x00..0x3F``. Our
  existing comments note "sub=00 returns byte-identical data to
  sub=04" — this is consistent with us reading the same memory
  region under two different sub-byte aliases, while the vendor
  uses sub=04 for a separate state region entirely.

  Captured but NOT activated: switching the default scan strategy
  without staged validation against multiple installs risks silent
  data loss (the vendor may rely on project-file context our scan
  doesn't have, e.g., known module identity). Future PRs can opt
  in once validated against more captures.

- ``tests/test_stale_inventory_detection.py`` — nine tests covering:
  empty-coordinator defensive default, present/absent classification,
  non-output-module exclusion, orphaned-button cascade (mixed-link
  case stays, no-link case stays, only-absent-link case orphans),
  case-insensitive address comparison, empty ``dict_module_data``,
  ``CancelledError`` propagation, per-probe timeout boundary, and a
  pin against the real-world second-hand-PC-Link install.

- ``tests/test_vendor_register_map.py`` — eight tests pinning the
  vendor map exactly as decoded from the trace: per-sub register
  lists, the deliberate ``0x94`` / ``0x95`` skip, the 48-read
  total, the three-sub-bytes-only invariant, byte-range bounds,
  and the trace-source attribution string.

## 0.5.15

### Fixed

- **``DEVICE_TYPES["1F"]`` and ``DEVICE_TYPES["23"]`` are RF-bus
  push buttons, not hand-held transmitters.** Niko's official
  ``PMNikobus_EN.pdf`` catalogue (the comprehensive product manual,
  available in fdebrus/Nikobus-HA's documentation/) describes
  RF-bus push buttons as battery-powered wall-mounted devices
  that pair with the 05-300 modular RF interface to integrate
  into Nikobus over 868.3 MHz:

  > "Single RF-bus push button: this RF-bus push button has two
  > operation areas available. It is finished with a full
  > rocker, either with or without labelling."
  >
  > "Double RF-bus push button: this RF-bus push button has four
  > operation areas available. It is finished with two
  > half-rockers, either with or without labelling, or with a 3/4
  > and a 1/4 rocker."

  fdebrus confirmed from his install:

  - ``0x1F`` (2 channels, e.g. address ``2E58F6``) = **Single
    RF-bus push button, 2 operation areas**.
  - ``0x23`` (4 channels, addresses ``201250`` and ``204915``) =
    **Double RF-bus push button, 4 operation areas**.

  Pre-0.5.15 we mapped these to Niko's hand-held SKUs (05-311
  and 05-312) — both wrong: 05-311 is the 1-channel hand-held
  mini-transmitter and 05-312 is the 13-button hand-held Easywave
  remote. Niko sells the RF-bus push buttons as a base radio
  module + interchangeable face plates rather than under a
  single SKU, so the catalogue doesn't list a specific Model
  number; Model stays ``"Unknown"`` for both until someone reads
  the printed number off the physical radio module.

  Name updates:

  - ``0x1F``: ``"Mini hand-held RF transmitter, 2 channels"`` →
    ``"Single RF-bus push button, 2 operation areas"``
  - ``0x23``: ``"Easywave hand-held RF transmitter, 4 channels"``
    → ``"Double RF-bus push button, 4 operation areas"``

  This also retracts the earlier pairing of ``0x23`` and ``0x3D``
  as "two firmware-reported modes of a single 05-312". They're
  different products: ``0x23`` is the Double RF-bus push button
  (wall device, Model unknown), ``0x3D`` remains the 05-312
  hand-held in its 52-circuit population view.

### Changed

- **Documented two duplicate-Model entries that are intentionally
  correct.** Niko firmware uses different device-type bytes for the
  same physical SKU in two known cases — caught during the catalogue
  audit and now pinned with inline comments + invariants tests so a
  future "deduplicate" cleanup can't silently break real installs:

  - ``0x09`` and ``0x31`` both → ``05-002-02`` Compact switch
    module. Single 4-output product per Niko; firmware-revision
    artefact dictates which byte is reported.
  - ``0x43`` and ``0x44`` both → ``05-058`` Universal interface.
    Niko 05-058 is a single 4-input product configurable as push
    buttons (4 telegrams = ``0x43``) OR switches (4 inputs × 2
    state-change telegrams = 8 channels = ``0x44``).

  (An earlier draft of this audit also paired ``0x23`` and ``0x3D``
  as "two modes of 05-312"; that pairing was retracted when
  ``0x23`` was identified as a wall switch — see the Fixed section
  above.)

### Tests

- ``tests/test_pc_logic_stage1.py`` — five new tests pin the audit
  invariants:

  - ``test_device_type_0x09_and_0x31_share_05_002_02_sku``
  - ``test_device_type_0x43_and_0x44_share_05_058_sku``
  - ``test_device_type_0x1f_model_is_unknown_not_05_311``
  - ``test_device_type_0x23_model_is_unknown_not_05_312``
  - ``test_device_type_0x3d_remains_05_312_easywave_hand_held``
  - ``test_device_type_0x25_remains_correct_05_311_1ch``

  248/248 pass.

### Notes

- **No behaviour change for existing installs** — the routing
  layer keys off the device-type byte, not the Model field, so
  the ``0x1F`` device that was previously called "05-311 / 2
  channels" still surfaces as "Wireless RF transmitter, 2
  channels / Unknown" without losing any functionality. Channel
  count stays at 2; the Model string only changes the displayed
  identifier.

## 0.5.14

### Fixed

- **PC-Link inventory terminator no longer fires on leading
  untouched flash.** 0.5.13's all-FF terminator-stop was too eager:
  it triggered the queue drain on the FIRST all-FF response,
  regardless of where it appeared. Real-install testing on
  2026-05-07 caught the regression — the user's install has
  register A0 returning pure all-FF (untouched flash before the
  active project's actual start register), so 0.5.13 drained all
  95 remaining queued reads and only the PC-Link itself was
  discovered:

  ```
  16:35:13.725 DEBUG ... Bus Frame: $2EF586FFFF…(16 bytes FF)…CC98D0
  16:35:13.726 INFO  ... PC Link inventory: all-FF terminator received —
                     stopping sweep (drained 95 remaining queued reads).
  16:35:23.736 INFO  ... PC Link inventory scan finished | discovered=1
  ```

  Niko's PC software handles this correctly — its trace shows it
  starting reads at register A3 (skipping leading flash) and
  stopping on the first all-FF AFTER records. The fix mirrors that
  behaviour without hardcoding a "start register": a
  ``_pc_link_inventory_data_seen`` gate flips True the first time
  any non-all-FF response is parsed (real record, test pattern,
  even malformed frame). All-FF responses BEFORE the gate flips
  are leading flash and get skipped via the legacy DEBUG path.
  All-FF responses AFTER the gate flips are the active-project
  terminator and trigger the drain.

  Both flags clear in ``reset_state`` so subsequent scans start
  fresh. No behaviour change on installs where the project starts
  at A0 — the first response there is a real record (not all-FF),
  ``data_seen`` flips on it, and the terminator fires on the
  all-FF after.

### Tests

- ``tests/test_pc_link_inventory_terminator.py`` — three new tests
  pin the gate behaviour, plus updates to four existing tests that
  implicitly relied on the 0.5.13 "first all-FF stops" semantics:

  - ``test_leading_all_ff_does_not_drain_before_data`` (new) —
    pins the user-2026-05-07 regression directly. Three leading
    all-FF responses, no drain, no terminator flag.
  - ``test_all_ff_after_data_drains_queue`` (new) — real record
    flips the gate; subsequent all-FF triggers the drain.
  - ``test_leading_all_ff_then_data_then_terminator`` (new) —
    realistic install pattern: 3 leading flash registers + 5
    records + 1 terminator. Drain fires once, on the trailing
    all-FF.

## 0.5.13

### Fixed

- **PC-Link inventory sweep stops at the first all-FF terminator
  *that follows real records*, matching Niko's PC software.** The
  previous behaviour read the full ``A0..FF`` register range and
  picked up records that the active project doesn't reach —
  typically residue left in flash by a previous installation on the
  same PC-Link (the second-hand-PC-Link scenario from
  https://github.com/user-attachments/files/27457361/log-2.txt where
  one user's dump showed 1 module + 34 buttons from the previous
  owner mixed in with their current install).

  Important nuance (caught after the first commit on this branch
  via a real-install test): registers ``A0..A2`` (or similar) on
  many real installs are **pure all-FF — untouched flash before the
  active project's actual start register**. The first cut of this
  fix triggered the terminator on register A0 unconditionally and
  drained the queue before any records had been read. The
  user-2026-05-07 install was one such case: A0 came back all-FF,
  the terminator fired, all 95 remaining queued reads drained, and
  only the PC-Link itself was discovered.

  The corrected rule mirrors what the Niko PC software actually
  does: only treat all-FF as the terminator AFTER at least one
  non-all-FF response has been seen (``_pc_link_inventory_data_seen``
  gate). Leading untouched-flash registers are skipped without
  triggering the drain; the drain fires only on the all-FF block
  that comes after the project's records.

  Wire-level capture of Niko's PC software performing its
  ``Read preview`` operation against fdebrus's PC-Link
  (2024-05-24, COM4) shows the sub=04 sweep going
  ``A3 → A4 → … → C2 → C3`` and stopping. C3's response is a 22-byte
  frame with a pure all-FF 16-byte payload:

  ```
  $0510 $2EF586 FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF CC98D0
  ```

  Niko's software treats this as the end-of-active-project marker
  and never reads C4..FF — so any residue past that point is
  invisible to it. The library now mirrors that behaviour: the first
  time ``parse_inventory_response`` sees an all-FF frame during the
  inventory phase, it sets a per-scan terminator flag, calls
  ``coordinator.nikobus_command.drain_queue()`` to discard the
  remaining queued reads, and logs at INFO with the count of
  drained commands. The queueing loop in
  ``_run_inventory_identity_queries`` also short-circuits if the
  flag flipped between iterations.

  Subsequent all-FF responses on the same scan keep the legacy
  "skip and continue" DEBUG path — only the first occurrence
  triggers the drain. The flag resets in ``reset_state`` so the
  next inventory scan starts fresh.

### Tests

- ``tests/test_pc_link_inventory_terminator.py`` — seven tests:
  - First all-FF response in the inventory phase drains the queue
    and sets the terminator flag.
  - Subsequent all-FF responses don't drain again (idempotent).
  - Real registry records before the terminator don't drain.
  - All-FF responses outside the inventory phase (e.g. during
    Stage-2 register scans) don't drain — protects against draining
    the Stage-2 queue when modules legitimately return FF for
    unprogrammed registers.
  - The terminator flag clears in ``reset_state`` so a subsequent
    inventory enumeration starts fresh.
  - Defensive: missing ``drain_queue`` on the command object
    (older harness) doesn't raise.
  - The queueing loop in ``_run_inventory_identity_queries``
    bails out when the terminator flag flips mid-loop, so we don't
    queue all 96 registers if the response arrives early.

### Notes

- The HA coordinator's existing "3 consecutive empty blocks"
  early-stop is now redundant for PC-Link inventory but harmless —
  the library's first-all-FF drain fires first. The HA-side rule
  can stay as a defence-in-depth backstop or get removed later.

## 0.5.12

### Changed

- **``DEVICE_TYPES`` ``Name`` fields aligned with Niko's official
  product-page wording** so the inventory log line, the device
  registry entry, and the entity description all match what users
  see in Niko's catalogue and the Nikobus PC software. Mapping
  before → after:

  | Hex | Model | Before | After |
  |---|---|---|---|
  | 01 | 05-000-02 | Switch Module | Switching module |
  | 02 | 05-001-02 | Roller Shutter Module | Roller shutter module |
  | 03 | 05-007-02 | Dimmer Module | Dimmer module |
  | 04 | 05-342 | Button with 2 Operation Points | Bus push button, 2 control buttons |
  | 06 | 05-346 | Button with 4 Operation Points | Bus push button, 4 control buttons |
  | 08 | 05-201 | PC Logic | PC-Logic |
  | 09 | 05-002-02 | Compact Switch Module | Compact switch module |
  | 0A | 05-200 | PC Link | PC-Link |
  | 0C | 05-348 | IR Button with 4 Operation Points | Bus push button, 4 control buttons with IR receiver |
  | 12 | 05-349 | Button with 8 Operation Points | Bus push button, 8 control buttons |
  | 1F | 05-311 | RF Transmitter with 2 Operation Points | Mini hand-held RF transmitter, 2 channels |
  | 22 | 05-057 | Switch Interface | Interface for switches |
  | 23 | 05-312 | RF Transmitter with 4 Operation Points | Easywave hand-held RF transmitter, 4 channels |
  | 25 | 05-311 | Portable RF Transmitter with 1 Operation Point | Mini hand-held RF transmitter, 1 channel |
  | 26 | 05-314 | RF868 Mini Transmitter with 4 Operation Points | RF868 mini transmitter, 4 channels |
  | 28 | 05-7X5 | Motion Detector | Motion detector with Nikobus interface |
  | 2B | 05-205 | Audio Distribution | Audio distribution module |
  | 31 | 05-002-02 | Compact Switch Module | Compact switch module |
  | 32 | 05-008-02 | Compact Dim Controller | Compact dim controller |
  | 37 | 05-206 | Modular Interface 6 inputs | Modular interface, 6 inputs |
  | 3D | 05-312 | RF Transmitter, 52 operation points | Easywave RF transmitter, 52 operation points |
  | 3F | 05-060-02 | Feedback Button with 2 Operation Points | Bus push button, 2 control buttons with two feedback LEDs |
  | 40 | 05-064-02 | Feedback Button with 4 Operation Points | Bus push button, 4 control buttons with four feedback LEDs |
  | 41 | 05-078-02 | Feedback Button with 8 Operation Points | Bus push button, 8 control buttons with eight feedback LEDs |
  | 42 | 05-207 | Feedback Module | Feedback module |
  | 43 | 05-058 | Universal interface | Universal interface, 4 channels |
  | 44 | 05-058 | Switch Interface | Universal interface, 8 channels |

  Existing user data (``nikobus_module.json`` / ``nikobus_button.json``)
  is unaffected at load time — descriptions stay whatever the previous
  scan wrote. The new names land on the next inventory refresh.

- **``get_module_type_from_device_type`` switched from name-based
  keyword matching to a static
  ``device-type-byte → bucket`` map.** The pre-0.5.12 resolver
  matched substrings of the ``Name`` field (``"pc link"``, ``"dimmer"``,
  ``"audio"``, etc.) — every name change carried hidden risk of
  re-routing a device. The new resolver consults
  ``_MODULE_TYPE_BY_DEVICE_TYPE`` directly. Naming becomes a pure
  display concern.

### Added

- **``0x21 → 05-056 Push Button Interface``** promoted from
  ``Reserved`` to a real ``DEVICE_TYPES`` entry. The 05-056 is the
  Niko Nikobus interface for push buttons (2 inputs, ``Category="Button"``)
  per Niko's product page (https://products.niko.eu/de-at/article/05-056).
  Same family as 05-057 (``0x22``); the differentiator is just the
  variant. A user install confirmed the device-type byte against the
  printed model number, removing the last unverified entry from the
  Reserved block for that user's hardware. The new entry uses the
  Niko-aligned name ``"Interface for push buttons"`` and the
  cataloguing test in ``tests/test_unknown_device_dedup.py`` drops
  ``0x21`` from its parametrize list since the entry is no longer
  Reserved.

## 0.5.11

### Fixed

- **PC-Link inventory enumeration now ignores PC-Logic responses to
  the broadcast ``#A`` query.** Both controllers reply to the
  address-inquiry broadcast with a
  ``$18 <addr> 00 <sig> 0F 3F FF <crc>`` frame; byte 4 of the
  payload (``<sig>``) carries the device signature — ``0x50`` on
  PC-Link, ``0x40`` on PC-Logic. Pre-0.5.11 ``handle_device_address_inventory``
  accepted whichever frame arrived first, so on installs with both
  controllers the PC-Logic could win the race and our subsequent
  inventory-memory reads (``$1410<pc-logic-addr>NN04``) would target
  the wrong device — every register came back empty and discovery
  silently produced nothing.

  ``handle_device_address_inventory`` now reads the signature byte
  out of the frame and rejects responses where it isn't ``0x50``
  with a clear WARNING:

  ```
  Inventory record rejected | reason=non_pc_link_signature
  raw=3588 signature=0x40 (expected 0x50 — PC-Link); this responder
  is most likely a PC-Logic answering #A before the PC-Link did.
  Verify a PC-Link (model 0A) is present on the bus.
  ```

  Validated against three real-hardware traces: fdebrus PC-Link 86F5
  and issue-307 PC-Link 846F both carry sig=0x50; the new-user
  PC-Logic 8835 carries sig=0x40.

### Added

- ``PC_LINK_INVENTORY_SIGNATURE_BYTE = 0x50`` in ``const.py``,
  documented with the three trace-confirmed sample frames so future
  edits don't need to re-derive the value from raw captures.
- ``tests/test_pc_link_signature_filter.py`` — 8 tests pinning the
  signature filter:
  - 0x50 frames accepted (both real-install samples).
  - 0x40 frames rejected with a structured WARNING that names the
    raw address and both signature bytes.
  - Mixed-order races (PC-Link first vs. PC-Logic first) end with
    only the PC-Link recorded.
  - Unknown signature bytes rejected (defensive default).
  - Truncated frames don't crash.

## 0.5.10

### Changed

- **Specialty Module-category devices get their own ``module_type``
  buckets.** Previously every Module whose ``Name`` failed to match the
  switch / dimmer / roller / pc_link / pc_logic / feedback keyword tree
  fell through to ``other_module`` — the same bucket the integration
  uses for button-class devices, so HA-side routing couldn't tell a
  05-206 from a 4-OP wall button. ``get_module_type_from_device_type``
  now produces:

  - ``interface_module`` for 0x37 / 05-206 (Modular Interface, 6 inputs).
  - ``audio_module`` for 0x2B / 05-205 (Audio Distribution).

  Both new buckets are added to a hoisted ``NON_OUTPUT_MODULE_TYPES``
  constant in ``discovery.py`` so the scan-queue exclusion in
  ``query_module_inventory("ALL")`` and the per-module dispatch
  short-circuit stay in lock-step. The new buckets short-circuit the
  scan today (no validated link-table format for either device);
  toggling that off later is a one-line change.

- **The non-output exclusion list is now a single shared constant.**
  ``discovery.py`` previously duplicated ``{"feedback_module",
  "other_module"}`` in two places (scan-queue selection + per-module
  dispatch). Both call sites now read from
  ``NON_OUTPUT_MODULE_TYPES``.

### Fixed

- **05-057 Switch Interface channel count corrected from 4 to 2.**
  Cross-referenced against the printed device image — the 05-057 has
  exactly two ``IN`` terminals (an external switching contact module
  with 2 inputs), not 4. ``DEVICE_TYPES["22"]`` now carries
  ``"Channels": 2``. Existing installs that already discovered this
  device with channels=4 will refresh on the next inventory scan.

### Added

- **PC Logic (05-201) inventory now declares 6 channels.** ``DEVICE_TYPES["08"]``
  was missing the ``Channels`` field, so PC-Logic modules entered the
  inventory with ``channels=0`` and HA had nothing to surface for the
  Master PC-Logic's six local inputs (LM01–LM06). The entry now carries
  ``"Channels": 6`` so the inventory phase produces a 6-channel
  ``channels_count`` and downstream platforms can create one entity per
  local input.

- **PC-Link / PC-Logic decoders ingest into the merge layer (Stage 2c).**
  Both decoders held the resolver in logging-only mode through Stage 2b
  (0.5.1). With the byte-0 → ``(target_module, channel)`` resolution
  validated against the fdebrus install (52-channel flat map across 6
  output-bearing modules; 9 link records cross-checked), ``decode_chunk``
  now emits ``DecodedCommand`` entries for every link record where:

  1. ``channel_index`` resolves to an output-bearing target via the
     registry-built flat channel map.
  2. The target's device type is in
     ``_MODE_TABLE_BY_DEVICE_TYPE`` (switch / roller / dimmer
     variants).
  3. The mode byte's low nibble maps to a known mode for the target.
  4. The source button's channel count is known (so ``flag_byte``
     reverse-resolves to a key index via ``KEY_MAPPING_MODULE``).

  When any of those gates fail, the link is logged but no command is
  emitted — defensive behaviour that keeps the merge layer free of
  half-resolved entries. Registry records remain visibility-only;
  their inventory-phase equivalent already populates ``module_data``.

- **``add_to_command_mapping`` honours a ``module_address`` override
  in the decoded metadata.** PC-Link / PC-Logic decoders set the
  resolved **target** module as ``module_address`` so the link lands
  on the real output module's ``linked_modules`` block, not on the
  controller (PC-Link / PC-Logic) currently being scanned.
  Switch/dimmer/roller decoders never set this field; their links
  continue to use the positional ``module_address`` argument (the
  module being scanned), so this change is invisible to those paths.

### Changed

- ``PcLinkDecoder.reset_scan_buffers`` and ``PcLogicDecoder.reset_scan_buffers``
  now clear the per-instance ``RegistryBuffer`` in addition to the base
  alt-alignment state. Discovery already calls ``reset_scan_buffers``
  at scan boundaries, so a fresh scan starts with no carried registry.
- The shared decode-and-log helper (formerly ``pc_link_decoder._log_record``)
  takes an explicit ``logger`` argument so PC-Logic's structured INFO
  lines surface under the ``pc_logic_decoder`` logger rather than
  ``pc_link_decoder``. The log prefix (``"PC-Link"`` / ``"PC-Logic"``)
  is unchanged, so log greps and existing dashboards keep working.

### Tests

- ``test_pc_logic_stage1.py`` — three new tests:
  ``test_device_type_0x08_carries_six_channels``,
  ``test_pc_logic_decoder_emits_decoded_command_for_resolved_link_record``
  (PC-Logic Stage 2c parity with PC-Link, asserting the full
  ``DecodedCommand`` shape and the resolved-target override),
  ``test_pc_logic_decoder_reset_scan_buffers_clears_registry``.
- ``test_pc_link_stage2b.py`` — split the old "still returns []"
  assertion into two narrower tests:
  ``test_pc_link_decoder_registry_records_emit_no_commands`` and
  ``test_pc_link_decoder_link_record_without_button_channels_returns_empty``.
  Added ``test_pc_link_decoder_emits_decoded_command_for_resolved_link_record``
  to pin the positive path.

## 0.5.9

### Fixed

- **Switch / roller chunker adds a third alt alignment at stream
  offset 4.** 0.5.6 introduced a dual alignment (offsets 0 and 8) to
  cover firmware revisions that did or didn't prepend a 4-byte
  response header. The 2026-05-04 PR-#42 follow-up scan showed a
  third productive offset on the same install: button ``3AC4A9``'s
  link record on switch ``B909`` (key=1, channel=5, mode M01)
  sits at frame offset 16 of register 58, half-way between the
  primary (offset 0) and existing +8 alt — neither alignment
  catches it. Probing all 12 stream-start offsets across every
  output module on this install showed offset 4 is consistently
  productive on B909 (8 records exclusive to off=4), 72C8 (5),
  3162 (2), and 48A7 (4). The chunker now runs three alignments
  in parallel — offsets {0, 4, 8} — for switch and roller modules.
  Replay numbers, 2026-05-04 capture, all 12 output modules:

  | Strategy | Matched chunks |
  |---|---|
  | 0.5.4 (buffered+0 only) | 21 |
  | 0.5.5 (per-frame@0) | 49 |
  | 0.5.6 (buffered+0 ∪ +8) | 280 |
  | 0.5.9 (buffered+0 ∪ +4 ∪ +8) | **323** |

  CPU cost is negligible — each additional alt alignment runs
  through the same decoder gates that filter phantoms on every
  call. Coverage is the union of all three alignments. Dimmer
  doesn't run alt alignment (16-char chunks against 16-char
  frames are header-insensitive across every captured firmware).

### Changed

- ``BaseChunkingDecoder._alt_payload_buffer`` (single string)
  becomes ``_alt_payload_buffers`` (dict keyed on skip value);
  ``_alt_first_frame_skip_pending`` (single int) becomes a dict
  with the same keys. Same cost-amortisation behaviour, scaled
  to N parallel alt alignments. ``reset_scan_buffers`` re-arms
  every alt skip's pending counter.

### Tests

- ``test_chunk_buffering.py`` — new
  ``test_switch_alt_alignment_recovers_offset_4_records`` pinning
  the third alignment, with the actual ``3AC4A9`` record from the
  2026-05-04 capture as the canary.
  ``test_alt_alignment_resets_per_scan`` updated to assert the
  per-skip dict shape (`{4: 4, 8: 8}` rearm pattern).

## 0.5.8

### Fixed

- **8-channel button link records arriving via the `+1` alias now
  merge.** Link records on dimmer / switch / roller modules encode
  the button address as ``physical + 1`` for raw key indices 4-7 of
  an 8-channel button. The decoder accepts those via
  ``is_known_button_canonical``'s sibling check (``protocol.py``),
  but ``_resolve_operation_point`` had no analogous fallback — it
  tried ``buttons.get(canonical)`` directly and
  ``bus_to_op.get(canonical)``, neither of which covers the
  canonical+1 case (the bus index's +1 alias is on the bus address,
  not on the physical address — they coincide only by accident).
  Records dropped silently at merge.

  On the 2026-05-04 install button ``1D3252`` (8-ch) was the
  textbook case: 5 records on roller ``5538`` arrived exclusively
  as the ``1D3253`` alias (raw key 4-7), and all 5 silently
  dropped. Eight other 8-channel buttons on the same install
  (``1CBE46``, ``1E1B16``, ``1E2078``, ``1E206C``, ``1C8DD8``,
  ``1E2A1A``, ``1E1272``) had records arriving both ways — only
  the direct half merged.

  After the fix, the alias half folds back to the physical
  8-channel button when its canonical-1 sibling exists in the
  store and has ``channels == 8``. 4-channel and 2-channel buttons
  are unaffected — their link records never use the
  ``physical + 1`` encoding, and the new path guards on
  ``channels == 8`` so it can't invent ghost links.

### Tests

- ``test_8ch_alias_merge.py`` (new) — pins the +1-alias merge
  fallback: 8-channel canonical+1 folds back to the physical
  button; 4-channel and 2-channel canonical+1 must NOT fold;
  direct match takes precedence over the fallback so we don't
  mis-route a record when both ``X`` and ``X+1`` are registered
  buttons.

## 0.5.7

### Fixed

- **Dimmer module register scan reverts to the pre-0.4.10 full-sweep
  range.** 0.4.10 narrowed the dimmer scan to ``sub=04 → 0x00..0x3F``
  + ``sub=01 → 0x70..0x96`` (103 registers total) on the strength of
  a single Nikobus-PC-software serial trace. The 2026-05-04 capture
  from a different dimmer firmware revision (modules 116D + 0E0A,
  10-channel and 12-channel 05-007-02) shows that narrowing drops
  link records on dimmer channels 3 and 5 — PC software clearly
  displays connections to those outputs (BP1 / BP8 / BP19 / BP27 /
  BP30 / BP35 etc. driving 116D's O09 / O11 / O12), but our scan
  recovered records only on channels 1, 2, 6 because the link table
  on this firmware extends past the 0.4.10 sub=04 cap into
  ``0x40..0x80``. Restoring the pre-0.4.10 ``range(0x00, 0x100)``
  for both dimmer passes recovers the missing records. Switch and
  roller stay at the tuned ranges — their narrowing has been
  validated against multiple firmware captures and we don't have
  evidence of a similar gap there. Cost: ~3 minutes extra per
  dimmer scan; benefit: every link record on every captured dimmer
  firmware revision becomes visible to the merge layer.

### Changed

- **New ``_SCAN_REGISTER_RANGE_BY_MODULE_TYPE_AND_SUB`` per-pass
  override**, keyed on ``(module_type, sub_byte)``. Takes precedence
  over the per-module-type override and the per-sub-byte default.
  Lets us widen one specific (module-type, sub-byte) combination
  without disturbing any other. Currently used to register dimmer's
  ``sub=04`` and ``sub=01`` for the full-sweep restoration above.

### Tests

- ``test_register_scan_range.py`` — dimmer two-pass test and
  ``test_dimmer_scan_total_registers_full_sweep_per_pass`` updated
  to assert ``range(0x00, 0x100)`` for both dimmer passes (was
  ``0x00..0x3F`` + ``0x70..0x96``). The dimmer-pass-1 starts-at-zero
  test still pins the lower bound so the 0.4.4 records-in-low-band
  fix doesn't regress.
- ``test_pc_logic_stage1.py`` — split the per-output-module default
  test into a switch/roller variant and a dedicated dimmer
  full-sweep variant. New
  ``test_scan_range_priority_per_pass_overrides_per_module`` pins
  the priority order for the new ``_SCAN_REGISTER_RANGE_BY_MODULE_TYPE_AND_SUB``
  table.

## 0.5.6

### Fixed

- **Switch / roller register scans recover the records that the
  0.5.5 per-frame-discard chunker missed.** 0.5.5 dropped the
  trailing register-end padding when a frame was self-contained,
  which fixed alignment on hardware whose records pack at stream
  offset 0 within each register but missed the records that pack
  *across* register boundaries. The 2026-05-04 install (10 output
  modules including 29FA, the user-attachments capture from
  Issue #X) is one such case: its firmware prepends a 4-byte
  response header to every switch / roller scan, so records pack
  contiguously across register frames starting at stream offset 8.
  Per-frame-discard saw 49 of those records out of 166 actually
  present in the capture; button **3AC4A9** specifically — the
  driver of the original report — wasn't among the 49.
  
  The chunker now runs **two buffered alignments** per switch /
  roller scan: the historic 0.2.1 buffered path at stream offset 0
  *plus* a second buffered path shifted 8 chars at stream start.
  Both alignments emit chunks into the same return list; the
  decoder's `unknown_button` / `unknown_mode` gates filter the
  alignment that produces phantoms; the merge layer dedupes when
  both alignments lock onto the same record.
  
  Replay numbers against the 2026-05-04 capture, 10 output modules:
  
  | Strategy | Matched chunks | Distinct buttons | 3AC4A9 found |
  |---|---|---|---|
  | 0.5.4 (buffered+0) | 21 | ~10 | no |
  | 0.5.5 (per-frame@0) | 49 | ~12 | no |
  | 0.5.6 (buffered+0 ∪ buffered+8) | **187** | **39** | yes |
  
  The dual-alignment design works without firmware detection:
  when the firmware doesn't prepend a header (e.g. the 2026-04-30
  install with modules 4707 / 9105 / C9A5), the alt path produces
  phantoms that the decoder gates reject before reaching merge.
  Dimmer doesn't run alt alignment — 16-char chunks against
  16-char frames are header-insensitive on every captured firmware.

### Changed

- **`BaseChunkingDecoder.reset_scan_buffers()`** new public method.
  Discovery's `_reset_module_context()` calls it on every decoder
  at scan boundary so the alt-alignment skip-pending counter
  re-arms cleanly between modules.

### Tests

- `test_chunk_buffering.py` rewrites the two 0.5.5 tests that
  pinned per-frame-discard semantics. New pins:
  - chunks are emitted at *both* alignments from a single
    full-size switch frame
  - alt-alignment recovers offset-8 records that primary misses
    on header-prepending firmware (29FA frame 19 layout: 4-byte
    prefix + 2 records)
  - `reset_scan_buffers` re-arms the per-scan skip-pending counter
  - dimmer doesn't emit alt-alignment chunks
  
  The original three cross-frame buffering tests still pin the
  primary buffered path unchanged.

## 0.5.5

### Fixed

- **Switch / roller register scans now produce link records on real
  hardware.** The chunker buffered every register response's trailing
  remainder forward into the next frame's data region. For switch and
  roller modules — which return 32 hex chars of data per register
  against a 12-char chunk size (32 = 2*12 + 8 padding) — this shifted
  every subsequent chunk's alignment by 8 chars and turned every
  decoded `button_address` into a phantom value. The `unknown_button`
  gate then rejected all of them, so users observed
  `Discovered links merged into store: 0 buttons updated, 0 link
  blocks added, 0 outputs added` for every switch and roller scan
  while dimmer scans (16 hex data = 1 chunk = 0 padding) worked fine.
  When a frame's data region holds at least one full chunk and no
  carry is queued from a prior fragmented frame, the chunker now
  treats the frame as self-contained and discards the trailing
  register-end padding. The synthetic-fragmentation path that the
  buffering tests pin (frames < chunk_len feeding the running buffer)
  still works as before. Replay against a real-hardware capture with
  10 affected output modules: 0 → 49 newly-linked button records
  surface from the switch and roller scans.

### Changed

- **Switch and roller modules now run the same sub=04 + sub=01
  two-pass scan as dimmer.** The original sub=01 rejection (0.4.8,
  "phantom records the merge layer drops") was logged under the
  broken cross-frame chunker; every chunk on a 32-char switch frame
  was 8 chars out of phase regardless of which sub-byte sourced it.
  With the chunker fix above, sub=01 returns its own productive band
  on switch and roller — same `0x70..0x96` range as dimmer — and the
  decoder's `unknown_button` / `unknown_mode` gates filter any
  genuine config-byte phantoms that survive. Cost: ~40 s extra per
  switch / roller module; benefit: link records that live outside
  `0x00..0x3E` on sub=04 (e.g. buttons whose records reside in the
  extended bank) become visible to the merge layer.

### Tests

- `test_chunk_buffering.py` adds three pinning tests for the
  per-register-padding-discard behaviour, alongside the existing
  three that pin the cross-frame buffered path. Fragmented frames
  (data region < chunk length) still buffer; full-size frames
  (data region ≥ chunk length, no buffered carry) extract chunks
  from the data region only and drop the tail.
- `test_register_scan_range.py` updates the switch / roller
  single-pass tests to assert the new sub=04 + sub=01 two-pass
  behaviour, including the tuned 0x70..0x96 range on the secondary
  pass.

## 0.5.4

### Fixed

- **Switch / dimmer modules no longer abort scanning at register
  0x00..0x04.** `MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT` raised from 5
  to 16. On installs whose switch / dimmer firmwares silently ignore
  function-10 / function-22 reads in the 0x00..0x04 dead zone (real
  hardware: 4 switch modules + 1 dimmer all aborted at register 0x04
  with `consecutive_give_ups=5`), the prior limit fired immediately
  and aborted the entire pass before reaching the productive 0x05+
  band that the PC-software trace sweeps. 16 buys enough headroom to
  power past the leading dead zone while still aborting unproductive
  passes within ~30 s instead of walking the full 256-register range.

### Added (phantom-rejection guard at decode time)

- **`is_known_button_canonical()` helper** in `discovery.protocol`.
  Returns `True` when a decoded canonical button address belongs to a
  known button — direct match in the live inventory, or the +1 sibling
  of an 8-channel button (raw indices 4-7 of an 8-ch button decode to
  `inventory_addr + 1`, aliased at merge time by
  `_build_bus_to_op_index`). Lenient when no coordinator / button API
  is available, so test harnesses and bare-metal tooling still produce
  records.

- **Switch / dimmer / shutter decoders apply the guard** after
  computing `button_address`. Chunks whose last 3 bytes land on
  routing or cell-prefix bytes (rather than a real button-link
  record's address bytes) decode to canonicals matching no inventory
  entry. Pre-0.5.4 those reached the merge layer, got logged as
  `unmatched`, and bloated the per-scan log without ever contributing
  a real `linked_modules` entry. Now they're dropped at decode time
  with a `reason=unknown_button` debug line.

### Changed

- **"Unknown device detected" warning is deduped per session.**
  Pre-0.5.4 every record carrying an uncatalogued device-type byte
  logged a fresh WARNING (with the "please open an issue" CTA). On
  installs with several uncatalogued types, that meant ~26 duplicate
  WARNINGs per scan. Now each distinct type byte warns once per
  `NikobusDiscovery` instance; subsequent occurrences DEBUG.

- **Catalogued seven previously-unknown device types** observed on
  real hardware (fdebrus/nikobus-connect issue logs): `0x05`, `0x14`,
  `0x21`, `0x24`, `0x34`, `0x46`, and `0x3B` (the last appearing at
  addresses `3CF000`, `3CF010`, … on a 16-byte stride consistent with
  PC-Logic 05-201 BP-cell directory entries). All marked
  `Category="Reserved"` so the inventory parser silences the warning
  but neither `merge_discovered_modules` nor `merge_discovered_buttons`
  acts on them. Authoritative identification (Nikobus product code,
  channel count) welcome via GitHub issue.

## 0.5.3

### Added (Stage 2b plumbing — logging-only)

- **PC-Link link records now log a resolved target.** Each
  `PC-Link link record` INFO line is followed by a `PC-Link link
  target` INFO line carrying the resolved
  `(target_module_address, channel)` derived from byte 0 of the
  record. Resolution walks a flat output-channel map built from the
  controller's registry section in encounter order, indexing into
  the live install's actual channel counts via
  `coordinator.get_module_channel_count`.

  Stage 2b is **logging-only** in this release: the resolver runs in
  production but `PcLinkDecoder.decode_chunk` still returns `[]`, so
  the merge layer doesn't ingest PC-Link link records. Users can
  validate the resolver's output against their physical install
  (does pressing button X really drive `module=Y ch=Z`?) before we
  flip the merge gate in a follow-up. Out-of-range or
  empty-registry resolutions log at DEBUG to keep the INFO stream
  clean.

- **`RegistryBuffer` accumulator on `PcLinkDecoder`.** A per-instance
  buffer collects `ModuleRegistryRecord` entries during a scan,
  preserving encounter order (the link-record byte-0 indexing
  contract) and dropping duplicates when the controller re-emits
  the same register. Public method `reset_registry()` clears it
  between scans.

- **`OUTPUT_BEARING_DEVICE_TYPES`** in `pc_record_parser`.
  The set of device-type bytes whose modules drive load outputs
  and therefore appear in the flat channel map: `0x01` (switch),
  `0x02` (roller), `0x03` (dimmer), `0x09` and `0x31` (compact
  switch), `0x32` (compact dim). PC Link self (`0x0A`), PC Logic
  (`0x08`), Audio Distribution (`0x2B`), Modular Interface inputs
  (`0x37`), and Feedback Module (`0x42`) are excluded — their
  channels (or absence thereof) don't participate in the
  link-record byte-0 mapping.

- **`build_flat_channel_map(registry, coordinator)`** and
  **`resolve_link_target(channel_index, registry, coordinator)`**.
  Pure functions that build the flat output map and resolve a
  single byte-0 index. Both fail closed (return `[]` / `None`) on
  missing coordinator, unsized modules, or out-of-range indices.

### Tests

- 22 new tests in `tests/test_pc_link_stage2b.py` covering the
  registry buffer (accumulation, dedup, encounter order, reset),
  the output-bearing device-type set (positive/negative
  membership), the flat channel map (52-entry result for fdebrus's
  install pinned to expected `(addr, ch)` pairs at every band
  boundary, plus skip behaviour for excluded device types and
  zero-channel modules), the resolver (12 known
  `(channel_idx, addr, ch)` pinpoints from the trace, plus
  out-of-range / negative / empty-registry / non-output-only
  fail-closed cases), and `PcLinkDecoder` integration (registry
  accumulation across chunks, the new `link target` INFO line on
  successful resolution, DEBUG logging when resolution fails, and
  the Stage-2a contract that `decode_chunk` keeps returning `[]`).
- 186/186 passing.

### Migration

- HA integrations bumping `nikobus-connect>=0.5.3` start seeing
  `PC-Link link target` INFO lines next to each link record. No
  config or behaviour change beyond logging — the merge layer
  output is identical to 0.5.2. Use the new lines to validate the
  resolver against your install before opting into Stage 2b's
  merge activation in a future release.

## 0.5.2

### Fixed

- **Registry records with byte-0 marker `0x04` are now recognised.**
  A second user's PC Link (`846F`) emits registry records with
  `byte_0 == 0x04` instead of `0x03`. Same 16-byte structure (byte 4 =
  Module device-type, bytes 8-9 byte-swapped = address, byte 12 =
  per-type slot), but our 0.5.0/0.5.1 parser pinned `0x03` as the
  marker and routed every `0x04` registry chunk to
  `_parse_link_record`, emitting a phantom link record per registered
  module. `parse_pc_record` now accepts an optional
  `known_module_addresses` kwarg; when supplied, a chunk whose byte 4
  is a Module device-type AND whose bytes 8-9 byte-swapped match a
  known address is parsed as a registry record regardless of byte 0.
  The `0x03` fast path is preserved for backward compatibility.

- **Counter-pattern and partial-empty noise chunks are now rejected.**
  The same user's full-sweep 0.5.0 log contained:
  - Sequential register-counter dumps from the PC Link's low-register
    self-test data (e.g. `000102030405060708090A0B0C0D0E0F`,
    `101112131415161718191A1B1C1D1E1F`) — all 16 bytes are sequential,
    not a record.
  - Partial-empty fragments like `0000FFFFFFFFFFFFFFFFFFFFFFFFFFFF`
    and `FFFFFFFFFFFFFFFFFFFFFFFF00000000` at scan boundaries.
  Both classes were being parsed as link records with garbage fields.
  New `is_noise_chunk` helper in `pc_record_parser` keys on the
  invariant that real records always have `bytes 1-3 == 0x00 0x00
  0x00` (verified against both installs' traces) and explicitly
  rejects all-zero chunks. The PC Link / PC Logic decoders run this
  check between `is_empty_record` and `parse_pc_record`, so noise
  chunks now log at DEBUG instead of emitting phantom INFO records.

### Tests

- 12 new tests in `tests/test_pc_record_parser.py`:
  - 4 noise-rejection tests covering all-zero, counter dumps, and
    partial-empty fragments.
  - 7 flex-marker tests covering the 12 second-install registry
    chunks (parametrised), positive structural extraction, fall-
    through when the address is unknown, fall-through when byte 4 is
    a Button device-type, plus two backward-compat assertions for
    the byte-0 == 0x03 fast path.
- 1 existing 0.5.1 test (`test_link_record_with_real_data_in_one_field_is_accepted`)
  updated to use real-record-shape chunks (bytes 1-3 = 00) since
  0.5.2's noise filter now rejects chunks where bytes 1-3 are
  non-zero.
- 1 existing test (`test_byte_zero_zero_routes_to_registry_record_only_when_marker_matches`)
  reframed as `test_byte_zero_zero_routes_to_link_when_record_has_real_data`
  for the same reason.
- 164/164 passing.

### Migration

- HA integrations bumping `nikobus-connect>=0.5.2` get the fixes
  automatically. The visible difference for users on installs where
  the registry marker is `0x04`: previously-misclassified registry
  chunks now emit `PC-Link module-registry record` INFO lines
  instead of phantom `PC-Link link record` lines, and noise chunks
  no longer pollute the INFO stream.

- Stage 2b (merging real link records into `linked_modules`) is
  still gated; this release is preparation for it. Stage 2b will
  start once the cleaned-up logs from a second install confirm the
  byte-0 → `(target_module, channel)` mapping hypothesis.

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
