# nikobus-connect

Asynchronous Python library for communicating with the **Nikobus** home-automation bus via a PC-Link interface (serial or TCP). Includes built-in device discovery and decoding for switches, dimmers, and roller/shutter modules.

## Features

- Async serial and TCP connections to the Nikobus PC-Link
- Automatic handshake and CRC handling
- Command queuing with retries and acknowledgement tracking
- Real-time event listener for button presses and module feedback
- High-level API for switches, dimmers, and covers (open/close/stop)
- Device discovery: inventory queries, protocol decoding, and configuration file generation
- Decoders for switch, dimmer, and shutter module responses

## Installation

```bash
pip install nikobus-connect
```

Requires Python 3.11+ and depends only on `pyserial-asyncio`.

## Quick start

```python
import asyncio
from nikobus_connect import NikobusConnect, NikobusCommandHandler, NikobusAPI

async def main():
    # Connect via serial or TCP
    conn = NikobusConnect("/dev/ttyUSB0")       # serial
    # conn = NikobusConnect("192.168.1.100:9999")  # TCP

    await conn.connect()

    handler = NikobusCommandHandler(conn)
    api = NikobusAPI(handler, module_data={})

    # Turn on switch at address "A1B2C3", channel 1
    await api.turn_on_switch("A1B2C3", 1)

    await conn.disconnect()

asyncio.run(main())
```

## Discovery

The `nikobus_connect.discovery` subpackage can query the bus for connected modules, decode their configuration, and generate JSON configuration files.

```python
from nikobus_connect.discovery import NikobusDiscovery

discovery = NikobusDiscovery(command_handler)
await discovery.run_discovery()
```

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
