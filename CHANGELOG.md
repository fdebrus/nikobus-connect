# Changelog

## 0.5.13

### Added

- **``NikobusDiscovery.detect_stale_inventory()``** — bus-presence
  cross-check for inventory entries left over from a previous install
  on the same PC-Link. Reverse-engineering note: Niko's PC software
  writes new programming on top of old register space but doesn't
  zero-fill unused slots, so a second-hand PC-Link's flash still
  carries the previous owner's module / button records. The user
  reporting this had a clean install with three modules (8110, 1CEC,
  3D28 from the dump) but only two of them physically present —
  ``3D28`` was the previous owner's hardware. Their inventory dump
  also showed 34 stale buttons across the 0x3Bxx-0x3Exx and 100xxx-
  102xxx address bands.

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

- ``tests/test_stale_inventory_detection.py`` — nine tests covering:
  empty-coordinator defensive default, present/absent classification,
  non-output-module exclusion, orphaned-button cascade (mixed-link
  case stays, no-link case stays, only-absent-link case orphans),
  case-insensitive address comparison, empty ``dict_module_data``,
  ``CancelledError`` propagation, per-probe timeout boundary, and a
  pin against the real-world second-hand-PC-Link install (8110 +
  1CEC present, 3D28 absent).

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
