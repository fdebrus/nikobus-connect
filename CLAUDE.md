# nikobus-connect

Async Python library for the Nikobus home-automation bus, reached through a
PC-Link (05-200) or another serial gateway. It is the protocol layer of the
Home Assistant integration [Nikobus-HA](https://github.com/fdebrus/Nikobus-HA);
protocol parsing and bus state machines belong here, not in the integration.

## Commands

```bash
pip install -e ".[test]" mypy ruff
python -m pytest tests -q          # ~1000 tests, no hardware needed
mypy                               # strict, must stay clean (config in pyproject.toml)
ruff check nikobus_connect tests   # compare with main: there are pre-existing findings
```

Ruff has findings on `main` already. Do not run `ruff check --fix` across the
package; it rewrites unrelated code. Check that a change adds no new finding
(compare against `git stash` or `main`). `simulator/` is a separate package
(`nikobus-simulator`) that speaks the PC-Link protocol over TCP for testing
without hardware.

## Layout

- `connection.py` serial/TCP transport and presence probe; `listener.py` frame
  splitting, CRC checks, routing to callbacks; `command.py` the command queue,
  retries, ack/answer matching, group-write merging, the bus lock.
- `api.py` switch/dimmer/cover control, feedback-key switching, module status,
  CRC, memory images, PC-Link clock. `press.py`, `travel.py`, `rgb.py` helpers.
- `discovery/` PC-Link inventory and per-module link-table scan
  (`discovery.py`), decoders per module family, `mapping.py` (device types,
  key layouts, modes), `fileio.py` (merging into the host's stores),
  `store.py` / `progress.py` (pure read-back helpers for a host).
- `nkb/` reader for the vendor `.nkb` project file (vendored Access parser in
  `nkb/_access_parser`, not linted or type-checked).
- `PROTOCOL.md` the protocol notes. `CHANGELOG.md` one entry per release.

## Rules that must not be broken

- **Never write to a module.** The only write the library performs is setting
  the PC-Link clock (`set_pc_link_time`). Never put a module into programming
  (link) mode: it is also the write-enable mode. Maintenance features are
  read-only.
- **No hardware assumption without a cited capture.** A key layout, type byte,
  model mapping, mode table entry or timing constant states where it was
  observed (install, frame or link record, date) in a comment, or says that it
  is unverified. One install is not proof for a whole type: the 05-060 two-key
  plate exists in two key layouts under the same type byte, and fixing it to
  one layout (0.57.1) broke other installs.
- **Vendor-neutral docs.** Protocol facts may come from interoperability
  analysis, but no decompiled or disassembled vendor code goes into the repo,
  only the resulting facts (see `documentation/` in Nikobus-HA for the form).
- **User data stays out of the repo.** Logs, `.nkb` files and stores shared
  by users are untrusted input: open them in a scratch directory, run Python
  on them with `-I`, and never commit names, rooms or files. Addresses in a
  test fixture are fine.

## Conventions

- Addresses: output modules are 4 hex digits (`C9A5`), buttons and keys 6
  (`1809FE`). `convert_nikobus_address` is not an involution: two spellings
  (`684100` / `384100`) give the same bus frame.
- A key's bus frame is the plate address converted, with its first digit
  shifted per key (`record_bus_address`, `KEY_MAPPING`, per-type
  `DEVICE_TYPE_KEY_MAPPING`, per-plate `PER_PLATE_LAYOUT_TYPES`).
- In `command_mapping` built by the decoders, the first element of a key is
  the key's **bus frame** once the host knows the plate's channel count, not
  the plate's physical address; the physical address is in each output's
  `button_address`.
- Export every helper a host uses (package `__init__`, `__all__`), so the
  integration never needs private names or module internals.

## Tests

- Exercise the real code path. Build link records through the decoder or
  `add_to_command_mapping`, not by hand: a test that built mapping keys from
  the plate's physical address passed while the feature never ran on a real
  install (0.57.2).
- A fake must behave like what it replaces; when it cannot (timing, the
  serial line), say what it does not model.
- Every bug fix gets a test that fails without the fix.

## Releases

- Bump `version` in `pyproject.toml` and add a `CHANGELOG.md` entry (what
  changed for a user, with the evidence for hardware-related changes).
- The library is released and published to PyPI first; Nikobus-HA then
  raises `nikobus-connect>=` in its `manifest.json`. HACS only upgrades the
  library when that pin requires it.
