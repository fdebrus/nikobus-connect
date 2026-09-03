# nikobus-connect

Asynchronous Python library for communicating with the **Nikobus** home-automation bus via a PC-Link interface (serial or TCP). Includes high-level control for switches, dimmers, and roller/shutter modules, a real-time event listener for button presses, and a discovery subpackage that queries the bus and decodes module configuration.

## Features

- Async serial and TCP connections to the Nikobus PC-Link
- Automatic handshake and CRC handling
- Command queue with retries and ACK tracking
- Real-time listener for button presses and module feedback
- High-level API: switches, dimmers, covers (open/close/stop)
- Device discovery with per-module-type tuned register scans
- Decoders for switch, dimmer, and shutter responses

## Installation

```bash
pip install nikobus-connect
```

Requires Python 3.11+ and depends only on `pyserial-asyncio`.

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

### TCP connection

Use `host:port` instead of a device path:

```python
conn = NikobusConnect("192.168.1.100:9999")
```

## Discovery

The `nikobus_connect.discovery` subpackage scans the bus, identifies each module's type, and decodes the per-channel link tables into a JSON configuration file. Since 0.4.10 each scan pass is narrowed to the productive register band observed on real hardware (dimmer: 103 registers instead of 512; switch/roller: 64 instead of 256).

```python
from nikobus_connect.discovery import NikobusDiscovery
```

`NikobusDiscovery` is designed to be driven by a coordinator that owns the command handler and a background task scheduler — see the [Home Assistant integration](https://github.com/fdebrus/Nikobus-Home-Assistant) for a complete reference implementation.

## Package structure

```
nikobus_connect/
    __init__.py          # Public API re-exports
    api.py               # High-level switch/dimmer/cover control
    command.py           # Command queuing and state management
    connection.py        # Serial and TCP connection handling
    const.py             # Protocol constants
    exceptions.py        # Custom exception hierarchy
    listener.py          # Real-time bus event listener
    protocol.py          # CRC, framing, and command builders
    discovery/
        __init__.py      # Discovery public API
        base.py          # Data classes and enums
        chunk_decoder.py # Base chunked-response decoder
        dimmer_decoder.py
        discovery.py     # Main discovery orchestrator
        fileio.py        # JSON config file I/O
        mapping.py       # Device type and channel mappings
        protocol.py      # Discovery-specific protocol helpers
        shutter_decoder.py
        switch_decoder.py
```

## License

MIT — see [LICENSE](LICENSE).
