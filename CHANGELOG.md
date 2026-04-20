# Changelog

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
