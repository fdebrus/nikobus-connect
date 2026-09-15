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
- Real-time listener for button presses and Feedback Module pushes
- High-level API: switches, dimmers, covers (open / close / stop)
- Discovery: PC-Link inventory, then a per-module register scan bounded by the record count the module itself reports
- Decoders for switch, roller and dimmer link records, PC-Link and PC-Logic registry records, with mode and timer tables taken from the vendor's own parameter tables
- Read-only maintenance: module status, the module's memory checksum, full memory images (for backups), and the PC-Link clock
- `.nkb` project reader: modules, buttons, names, rooms and scenes, parsed locally with a vendored pure-Python Access reader

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

    # Turn on channel 1 on switch module A1B2C3
    await api.turn_on_switch("A1B2C3", 1)
    await asyncio.sleep(1)
    await api.turn_off_switch("A1B2C3", 1)

    await handler.stop()
    await listener.stop()
    await conn.disconnect()


asyncio.run(main())
```

Addresses are the 6-hex-digit module addresses printed on Nikobus modules (e.g. `A1B2C3`). Channels are 1-indexed.

`connect()` runs the PC-Link handshake and then a presence probe: a status query the PC-Link (or a Feedback Module used as gateway) acknowledges, with any Nikobus frame relayed meanwhile counting as proof of life. Silence is not fatal — it is logged, and the verdict is left in `conn.device_answered` (`True` / `False`, `None` before connecting) so a caller can surface it. A silent verdict is not final either: the first well-formed frame the listener receives afterwards flips it to `True` and calls `conn.on_device_answered` (sync or async), so a probe missed while the PC-Link was still resetting on a cold start corrects itself within seconds.

The gateway usually answers the probe with its own status frame as well; when it does, `conn.gateway_address` and `conn.gateway_family` (`pc_link`, `feedback_module` or `pc_logic`) say what is on the other end.

## Examples

### Switch on/off

```python
await api.turn_on_switch("A1B2C3", 1)
await api.turn_off_switch("A1B2C3", 1)
```

### Dimmer brightness

Brightness is 0-255. Pass the current brightness so the API can decide whether to send the "turn on" bus trigger.

```python
# Ramp to 50%
await api.turn_on_light("D1E2F3", 2, brightness=128, current_brightness=0)

# Change level without re-triggering "on"
await api.turn_on_light("D1E2F3", 2, brightness=200, current_brightness=128)

# Off
await api.turn_off_light("D1E2F3", 2)
```

### Cover / roller shutter

```python
await api.open_cover("C0FFEE", 1)
await asyncio.sleep(5)
await api.stop_cover("C0FFEE", 1, direction="opening")

await api.close_cover("C0FFEE", 1)
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

### PC-Link clock

The PC-Link keeps a naive local-time clock for the calendar functions of the Nikobus software; it does not follow daylight-saving changes on its own.

```python
now = await api.get_pc_link_time("86F5")
await api.set_pc_link_time("86F5", datetime.now())
```

## Discovery

The `nikobus_connect.discovery` subpackage enumerates the bus, identifies each module's type, and decodes the per-channel link tables into a JSON configuration.

Discovery runs in two stages. The first reads the PC-Link's own registry — the inventory of every module and physical button in the project. The second visits each output module and reads its link table; the module is asked how many records it holds (its status reply), and exactly those blocks are read, rather than sweeping a fixed band. Link records decode to a button address, an output channel, a mode and its timers, using mode and parameter tables checked against the Nikobus software's own. A record whose button address is one of the PC-Link's calendar channels (CH001 … CH100, fired by calendar programs and scenes) is filed under a synthesized `PC-Link Calendar Channel` entry rather than dropped.

```python
from nikobus_connect.discovery import NikobusDiscovery
```

`NikobusDiscovery` is designed to be driven by a coordinator that owns the command handler and a background task scheduler — see the [Home Assistant integration](https://github.com/fdebrus/Nikobus-HA) for a complete reference implementation.

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
    protocol.py             # CRCs, framing, command builders, reply decoding
    discovery/
        __init__.py         # Discovery public API
        base.py             # Data classes and enums
        chunk_decoder.py    # Base chunked-response decoder
        dimmer_decoder.py
        discovery.py        # Discovery orchestrator (inventory + register scan)
        fileio.py           # Config merge and JSON I/O
        mapping.py          # Device types, modes, timer and level tables
        pc_link_decoder.py  # PC-Link registry records
        pc_logic_decoder.py # PC-Logic registry records
        pc_record_parser.py # Registry record parsing
        protocol.py         # Discovery-specific protocol helpers
        shutter_decoder.py
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
