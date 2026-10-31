# nikobus-connect

<p align="left">
  <a href="https://pypi.org/project/nikobus-connect/"><img src="https://img.shields.io/pypi/v/nikobus-connect?style=flat&label=PyPI" alt="PyPI version"></a>
  <img src="https://img.shields.io/pypi/pyversions/nikobus-connect?style=flat" alt="Python versions">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=flat" alt="MIT license"></a>
</p>

Asynchronous Python library for communicating with the **Nikobus** home-automation bus through a PC-Link interface (serial or TCP). It offers high-level control of switches, dimmers and roller/shutter modules, a real-time listener for button presses and feedback frames, a discovery subpackage that enumerates the bus and decodes each module's link table, read-only maintenance of the modules' own programming, and a reader for Nikobus `.nkb` project files.

## Features

- Async serial and TCP connections, with a **presence probe** that proves a Nikobus device is on the line rather than just an open port
- Framing and both checksums verified: the PC-Link's CRC-8 over the serial hop **and** the module's CRC-16 over the payload, so a byte flipped on the bus is dropped instead of decoded
- Command queue with retries and ACK tracking; every bus exchange takes a shared lock, so a queued command can never be sent on top of a discovery read
- Real-time listener for button presses and Feedback Module pushes, and a press tracker that turns a key's frame stream into tap / hold / release with wire-time duration and burst-tolerant release detection
- High-level API: switches, dimmers, covers (open / close / stop), and outputs driven through a wall key with a feedback LED
- Group writes that never undo a change made at the wall: a module group whose state is not recent is read before its six outputs are written, and an answer that arrives meanwhile keeps the writes still on their way
- Discovery: PC-Link inventory, then a per-module register scan bounded by the record count the module itself reports; key layouts per device type, and per plate where one model exists in two (the two-button plates)
- Decoders for switch, roller, dimmer and Audio Distribution link records, PC-Link and PC-Logic registry records, with mode and timer tables taken from the vendor's own parameter tables — and the RGB controller's mode table from its programming leaflet
- Read-only maintenance: module status, the module's memory checksum, full memory images (for backups), the PC-Link clock, and a checksum prediction that tells a dimmer's power-loss memory from a reprogramming
- `.nkb` project reader: modules, buttons, names, rooms, scenes and the keys linked to a colour controller, parsed locally with a vendored pure-Python Access reader

## Installation

```bash
pip install nikobus-connect
```

Requires Python 3.11+. Depends on `pyserial-asyncio` (transport) and `construct` (used by the vendored `.nkb` reader).

## Quick start

Minimal wiring: connect, start the listener and command handler, then use the high-level API.

```python
import asyncio
from nikobus_connect import (
    NikobusAPI,
    NikobusCommandHandler,
    NikobusConnect,
    NikobusEventListener,
)


async def on_bus_event(frame: str) -> None:
    print("bus event:", frame)


async def main():
    # Serial: "/dev/ttyUSB0"  |  TCP: "192.168.1.100:9999"
    conn = NikobusConnect("/dev/ttyUSB0")
    await conn.connect()

    listener = NikobusEventListener(conn, event_callback=on_bus_event)
    handler = NikobusCommandHandler(conn, listener)
    await listener.start()
    await handler.start()

    api = NikobusAPI(handler, module_data={})

    # Turn on channel 1 on switch module C9A5
    await api.turn_on_switch("C9A5", 1)
    await asyncio.sleep(1)
    await api.turn_off_switch("C9A5", 1)

    await handler.stop()
    await listener.stop()
    await conn.disconnect()


asyncio.run(main())
```

Output modules have 4-hex-digit addresses, as printed on the module and shown in the Nikobus software (e.g. `C9A5`); buttons and their keys have 6-hex-digit ones (e.g. `295682`). Channels are 1-indexed.

`connect()` runs the PC-Link handshake and then a presence probe: the `#A` identity broadcast, which the PC-Link (and a PC-Logic) answers with its own status frame, with any Nikobus frame relayed meanwhile counting as proof of life. Silence is not fatal — it is logged, and the verdict is left in `conn.device_answered` (`True` / `False`, `None` before connecting) so a caller can surface it. A silent verdict is not final either: the first well-formed frame the listener receives afterwards flips it to `True` and calls `conn.on_device_answered` (sync or async), so a probe missed while the PC-Link was still resetting on a cold start corrects itself within seconds.

The gateway usually answers the probe with its own status frame as well; when it does, `conn.gateway_address` and `conn.gateway_family` (`pc_link`, `feedback_module` or `pc_logic`) say what is on the other end.

## Examples

### Switch on/off

```python
await api.turn_on_switch("C9A5", 1)
await api.turn_off_switch("C9A5", 1)
```

Set-output commands for one module group are merged: several `turn_on` / `turn_off` calls on channels of the same group made within a few tens of milliseconds go out as one frame carrying all six bytes, one acknowledgement, one relay click.

That frame carries all six outputs, taken from the state the handler last knew. An output switched at the wall since then would be switched back, so a group whose state is more than a few seconds old, or unknown, is read first (the command handler's `group_state_age(address, group)` tells how old it is). A host that stores answers or pushed frames should do it with the handler's `apply_group_state(address, group, state_hex)`: it lays the writes still waiting for their frame back on top, so an answer that arrives just before a write cannot undo it.

A channel whose module entry names a feedback key (`led_on` / `led_off`) is switched by pressing that key, so the key's LED follows the output: the module is read, the key pressed, and the module read again to check the press took. `api.has_feedback_key("switch_module", "C9A5", 1)` tells which channels work that way, and `api.toggle_switch("C9A5", 1)` switches a channel to the opposite of what the module reports rather than of what the host thinks.

### Dimmer brightness

Brightness is 0-255. Pass the current brightness so the API can decide whether to send the "turn on" bus trigger.

```python
# Ramp to 50%
await api.turn_on_light("0E6C", 2, brightness=128, current_brightness=0)

# Change level without re-triggering "on"
await api.turn_on_light("0E6C", 2, brightness=200, current_brightness=128)

# Off
await api.turn_off_light("0E6C", 2)
```

### Cover / roller shutter

A roller module reports direction, not position; `nikobus_connect.travel.TravelCalculator` times the travel from the module's run times (`start_travel("opening", latency=…)`, `stop()`, `current_position()`), with an injectable clock.


```python
await api.open_cover("9105", 1)
await asyncio.sleep(5)
await api.stop_cover("9105", 1, direction="opening")

await api.close_cover("9105", 1)
```

### Pressing a key from the host

```python
api.press_repeat = 3          # default; "2 to register, 3 to be sure"
await api.press_button("295682")
```

One write: the `#N` telegram repeated as a real key repeats it while held, back to back. Do not queue the repeats yourself — spaced 150 ms apart, an impulse/toggle link can count them as two presses. The interface never relays the host's own press back, so refresh the impacted module afterwards if you need its state.

### A press from its frames

A key repeats its `#N` frame every 40 ms while held. `nikobus_connect.press` infers the press from that stream: duration is the frame count times the cadence (the only measure that survives a bridge delivering frames in a burst), hold milestones at 1, 2 and 3 s are reported once each, and release is silence that outlasts a patience the tracker extends after a burst. The clock is injected.

```python
from nikobus_connect.press import PressTracker

tracker = PressTracker()
state, started, milestones = tracker.frame("C5E952")   # on every #N frame
...
if (ended := tracker.release_due("C5E952")) is not None:  # from your loop
    tracker.end("C5E952", ended.press_id)
    ended.duration_s, ended.bucket, ended.is_short
```

### Listening to button presses

Button frames arrive as `#Nxxxxxx` strings. Use `nikobus_button_to_module` to recover the source module and button label (`1A`, `1B`, ... `2D`).

```python
from nikobus_connect import nikobus_button_to_module


async def on_bus_event(frame: str) -> None:
    if frame.startswith("#N") and len(frame) >= 8:
        module, button = nikobus_button_to_module(frame[:8])
        print(f"button pressed: module={module} key={button}")


listener = NikobusEventListener(conn, event_callback=on_bus_event)
```

### Feedback Module pushes

A Feedback Module (05-207) polls the output modules it is configured to track, and the PC-Link relays both its queries and the modules' answers. Pass `has_feedback_module=True` and a `feedback_callback` to receive those answers; the callback gets the output group (1 for channels 1-6, 2 for 7-12) and the raw frame, and nothing has to be sent to obtain them.

The group comes from the query that preceded the answer, so this only works through a PC-Link: the Feedback Module's own serial port relays the answers but never its own queries. `listener.feedback_queries_seen` and `listener.feedback_answers_seen` tell the two apart — answers without queries mean the port is the Feedback Module's, and a caller should poll instead of trusting pushed state.

```python
async def on_feedback(group: int, frame: str) -> None:
    address = (frame[5:7] + frame[3:5]).upper()
    print(f"module {address} group {group} pushed {frame[9:21]}")


listener = NikobusEventListener(
    conn,
    event_callback=on_bus_event,
    feedback_callback=on_feedback,
    has_feedback_module=True,
)
```

### TCP connection

Use `host:port` instead of a device path:

```python
conn = NikobusConnect("192.168.1.100:9999")
```

## Maintenance

Every output module holds its own programming — which button drives which output, in which mode, with which timers — and can report a checksum over it. These calls are **read-only on the bus**, except `set_pc_link_time`, which is the only write the library performs. Putting a module into its programming (link) mode is deliberately not offered: that mode is also the write-enable mode.

```python
status = await api.get_module_status("9105")
# ModuleStatus(address, eeprom_error, type_code, record_count_a, record_count_b)

# The checksum the module computes over its own memory.
crc = await api.get_module_crc("9105")

# Full memory image, block by block: 0x700 bytes for switch and roller
# modules, 0xFD0 for dimmers. Suitable for a backup file.
image = await api.read_module_memory("9105", "switch_module")

# Compare the module's own checksum with one computed over the image.
matches, reported, computed = await api.verify_module_memory(
    "9105", "switch_module", image
)
```

A module's checksum is also a cheap way to notice that it was reprogrammed with the Nikobus PC software: record it after reading the module's links, and compare later with a single frame.

A dimmer also keeps the levels it restores after a power cut in its memory, so its checksum moves after a power cut without any reprogramming. Record those bytes with the checksum, and the checksum they explain can be computed later:

```python
from nikobus_connect.api import predict_module_crc

before = await api.read_module_volatile("0E6C", "dimmer_module")  # with the baseline checksum
...
now = await api.read_module_volatile("0E6C", "dimmer_module")
if predict_module_crc("dimmer_module", baseline_crc, before, now) == await api.get_module_crc("0E6C"):
    ...  # only the power-loss memory changed: not reprogrammed
```

`MODULE_VOLATILE_RANGES` lists those bytes per module type (none for switch and roller modules).

### PC-Link clock

The PC-Link keeps a naive local-time clock for the calendar functions of the Nikobus software; it does not follow daylight-saving changes on its own.

```python
now = await api.get_pc_link_time("86F5")
await api.set_pc_link_time("86F5", datetime.now())
```

## Discovery

The `nikobus_connect.discovery` subpackage enumerates the bus, identifies each module's type, and decodes the per-channel link tables into a JSON configuration.

Discovery runs in two stages. The first reads the PC-Link's own registry — the inventory of every module and physical button in the project. The second visits each output module and reads its link table; the module is asked how many records it holds (its status reply), and exactly those blocks are read, rather than sweeping a fixed band. Link records decode to a button address, an output channel, a mode and its timers, using mode and parameter tables checked against the Nikobus software's own; a dimmer's per-channel configuration block, which is memory but not links, is left out of the scan so it cannot decode into buttons that do not exist. A record whose button address is one of the PC-Link's calendar channels (CH001 … CH100, fired by calendar programs and scenes) is filed under a synthesized `PC-Link Calendar Channel` entry rather than dropped. When a module's table has been read in full (its record count reported, every block acknowledged), the links stored for it are replaced by what the table holds, so a link an older version filed differently, or a copy the PC-Link still keeps in its memory, does not linger. The same goes for a record whose button exists only in programming: a PC-Logic logic output (the module fires one like a key; its own memory holds the table of frames it can emit, which names and numbers them, `PC-Logic 8DC8 L01`) or a key of the software's virtual input banks gets a synthesized `Virtual button` entry, so its links survive the merge and the frame it puts on the bus is a known press rather than an unknown one. A central function of the PC-Link shares the logic outputs' address class and is told apart by that table. An address outside those classes that matches no plate stays unmatched: inventing a wall button is worse than dropping a link.

```python
from nikobus_connect.discovery import NikobusDiscovery
```

### Key layouts

A key's bus address is the plate's address with its first digit shifted by an offset per key. Which offsets a plate uses depends on the product, not only on its number of keys:

| Device | Keys | Offsets |
|---|---|---|
| Four- and eight-button plates, interfaces, RF transmitters | by key count | the generic table (`KEY_MAPPING`) |
| 4\*-082 two-button plate | 1A / 1B | 0 / 4 (the four-button's right pair) |
| 05-060 / 4\*-072, 05-061, 05-060-02 | 1A / 1B | 8 / C **or** 0 / 4, per plate |
| Motion detector with Nikobus interface (05-7\*5) | on / off / light / dark | 8 / C / E / A |

The two-button plates of the third row exist in both layouts under the same type byte and model. A module's link record names the key index the plate sends, 1 / 3 for one layout and 0 / 2 for the other, so the link merge reads each plate's layout from its own records before resolving them, and keeps it on the entry as `key_layout` for the next inventory. A plate whose keys have no link starts in the generic layout. `has_per_plate_key_layout(entry)` and `two_button_bus_addresses(physical, layout)` in `discovery.fileio` give a host the same answers, and `discovery.protocol.motion_detector_bus_addresses(physical)` the detector's four frames by role.

While a run is going, `nikobus_connect.discovery.progress` turns its stage and counters into the one percentage every host shows, partial runs rescaled to span the bar. Once the stores are filled, `nikobus_connect.discovery.store` settles them after a scan (`reconcile_inventory`: evict what the residue probe found silent, file every button under its status) and reads them back for a host: the `controlled_by` index (which keys drive an output), a button's status after a scan (active, residue of a previous owner, or an input that never writes a link table), whether a central function is a shutter group or a light scene, and the full trigger-to-outputs routing graph. Pure functions, stores in and plain data out.

`NikobusDiscovery` is designed to be driven by a coordinator that owns the command handler and a background task scheduler — see the [Home Assistant integration](https://github.com/fdebrus/Nikobus-HA) for a complete reference implementation.

### Audio Distribution module

A 05-205 keeps its links in memory bank `01`: a record count, then one six-byte record per link, `<bus address> <function> <object> 01`. The object is a zone (`0x00`–`0x03`) or the module's own Power object (`0x08`). Discovery decodes them into the function and object they drive (`M16 (On)` on `Zone 2`, `M01 (Power)` on the Power object, …); every function byte is checked against a real project file. The three address bytes are the `#N` payload exactly as it goes on the bus, so a host drives a function by sending them — nothing is derived, and they must not be passed through the wall-button address transform. Each trigger becomes its own button-store entry, keyed on that address and naming the module it drives.

```python
from nikobus_connect.discovery.audio_decoder import split_link_table, decode
```

### RGB controller

Device type `0x46` is the 340-00112 RGB / LED controller; it reaches the module store under its own `rgb_module` bucket. The controller answers the ordinary state query and nothing else: no register read, no checksum query, and a set-output write gets no acknowledgement — so it is not scanned, and it is driven only by the keys linked to it. Its fifteen link modes are catalogued from the vendor's programming leaflet — nine shared with the dimmer, six colour-only (colour path, scenarios) — and apply to the 340-00111 / 340-00113 orientation lights of the same family.

The state image is `[on] [R] [G] [B] 00 00`, all four as lit-or-not flags (calibrated on a real controller by freezing its colour loop and reading after each colour); both output groups return the same image. `nikobus_connect.rgb` decodes it and names what each key of each mode does on a short press:

```python
from nikobus_connect.rgb import decode_rgb_state, rgb_key_role, rgb_mode_label

state = decode_rgb_state("FFFF00FF0000")
state.on, state.colour_name, state.rgb   # True, "magenta", (255, 0, 255)

rgb_mode_label(19)        # "M19 (Start/stop scenario)"
rgb_key_role(19, "1C")    # "start_stop"
rgb_key_role(19, "1D")    # "off"
```

Which keys are linked to a controller cannot be read from the bus — not by this library, and not by the vendor software, which only status-polls the controller and writes a project file with its settings but no link. The installer's project file is the only record, and `parse_nkb()` returns it as `rgb_links` (controller, plate, key, the bus address the key puts on the wire, mode number). A host presses the key whose role is `on` or `off`.

What the vendor software *writes* into the controller is known, from its own plugin for this family (`Niko_05_010.dll`) and its executable (`nikobus.exe` 4.3.1), both decompiled: a 7968-byte image whose link table sits at byte `0x190` as 128 records of 18 bytes, written inside a link (programming) mode the software holds for the whole write, and **never read back** — the upload routine skips wall buttons, the 340-00111 and the 340-00112 before sending a frame, which is exactly what the captured installation read showed. The software has no direct colour command either: its simulation mode presses the linked key on the bus, as a host does. The layout, the record format and the write protocol are documented in the Nikobus-HA repository (`documentation/rgb-controller-memory-map.md`) for anyone reading a capture. This library carries none of it: it reads state and presses keys, and nothing in it writes to a module or enters link mode.

## Reading a `.nkb` project

`nikobus_connect.nkb` parses a Nikobus project export (a ZIP holding an Access database) locally, with a vendored pure-Python reader. It yields the module and button inventory, the friendly names and rooms the bus itself does not carry, and the scene definitions.

```python
from nikobus_connect.nkb import find_nkb_file, parse_nkb

path = find_nkb_file("/config")
if path is not None:
    data = parse_nkb(path)
```

## Package structure

```
nikobus_connect/
    __init__.py             # Public API re-exports
    api.py                  # Switch/dimmer/cover control, module status,
                            # CRC, memory images, PC-Link clock
    command.py              # Command queue, retries, the bus lock
    connection.py           # Serial and TCP transport, handshake, presence probe
    const.py                # Protocol constants
    coordinator_protocol.py # Protocol a driving coordinator must satisfy
    exceptions.py           # Exception hierarchy
    listener.py             # Bus event listener, CRC validation, dispatch
    press.py                # Press tracker: tap / hold / release from #N frames
    protocol.py             # CRCs, framing, command builders, reply decoding
    rgb.py                  # RGB controller state decoding
    travel.py               # Shutter position from travel time
    discovery/
        __init__.py         # Discovery public API
        audio_decoder.py    # Audio Distribution module link records
        base.py             # Data classes and enums
        chunk_decoder.py    # Base chunked-response decoder
        dimmer_decoder.py
        discovery.py        # Discovery orchestrator (inventory + register scan)
        fileio.py           # Config merge and JSON I/O
        mapping.py          # Device types, modes, timer and level tables
        pc_link_decoder.py  # PC-Link registry records
        pc_logic_decoder.py # PC-Logic registry records
        pc_record_parser.py # Registry record parsing
        progress.py         # One progress percentage from a run's counters
        protocol.py         # Discovery-specific protocol helpers
        shutter_decoder.py
        store.py            # Reading the stores back: routing, statuses
        switch_decoder.py
    nkb/
        __init__.py         # .nkb public API
        config_builder.py   # Inventory from a project file
        parser.py           # Names, rooms, scenes, links
        _access_parser/     # Vendored pure-Python Access reader
```

## Interoperability

This library was developed independently, for interoperability between software the user runs and Nikobus hardware the user already owns, in line with Article 6 of Directive 2009/24/EC. The `.nkb` reader parses a project file the user already owns, locally, for the same purpose. Nikobus is a trademark of Niko NV; this project is not affiliated with, endorsed by, or sponsored by Niko NV.

## License

MIT — see [LICENSE](LICENSE). Release notes are in [CHANGELOG.md](CHANGELOG.md).
