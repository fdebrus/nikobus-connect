"""The library, unmodified, driven against the simulator over a socket.

Everything here runs the shipped code paths: the real connection with
its handshake and presence probe, the real listener, the real command
queue, the real discovery scan. The simulator is on the other end of a
TCP socket rather than a serial port, which is the one substitution the
library already supports (a Nikobus bridge is reached the same way).

These are the tests that would have caught the releases that went out
broken — a dropped output group, a probe that read silence as absence, a
link table read with the wrong stride.
"""

from __future__ import annotations

import asyncio
from typing import Self

import pytest
from nikobus_simulator.server import NikobusSimulator
from nikobus_simulator.topology import Installation, preset

from nikobus_connect import const
from nikobus_connect.api import NikobusAPI
from nikobus_connect.command import NikobusCommandHandler
from nikobus_connect.connection import NikobusConnect
from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.listener import NikobusEventListener

#: Types the installation declares, as the host's store names them.
CHANNELS = {"switch_module": 12, "dimmer_module": 12, "roller_module": 6}


class Bus:
    """The library's own objects, wired to a running simulator."""

    def __init__(self, installation: Installation) -> None:
        self.installation = installation
        self.simulator = NikobusSimulator(installation)
        self.events: list[str] = []

    async def __aenter__(self) -> Self:
        await self.simulator.start()
        self.connection = NikobusConnect(self.simulator.connection_string)
        await self.connection.connect()
        self.listener = NikobusEventListener(self.connection, self._on_event)
        self.command = NikobusCommandHandler(self.connection, self.listener)
        self.api = NikobusAPI(self.command, {})
        await self.listener.start()
        await self.command.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.command.stop()
        await self.listener.stop()
        await self.connection.disconnect()
        await self.simulator.stop()

    async def _on_event(self, message: str) -> None:
        self.events.append(message)

    def module(self, address: str):
        return self.simulator.gateway.modules[address]

    async def settle(self) -> None:
        """Let the queue drain and the simulator act on what it was sent."""
        await self.command._command_queue.join()
        await asyncio.sleep(0.05)


@pytest.fixture(autouse=True)
def _quick_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the settling pause the vendor software also takes.

    It exists for a PC-Link that was just reset over a serial line; a
    socket needs none of it, and a second per test adds up.
    """
    monkeypatch.setattr("nikobus_connect.connection.PRESENCE_PROBE_SETTLE", 0.0)
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)


# --- coming up ------------------------------------------------------------


async def test_the_probe_finds_the_gateway_and_reads_its_identity() -> None:
    async with Bus(preset("house")) as bus:
        assert bus.connection.device_answered is True
        assert bus.connection.gateway_address == "86F5"
        assert bus.connection.gateway_family == "pc_link"


async def test_the_probe_survives_a_gateway_that_holds_its_acknowledgement() -> None:
    """The handshake's own query is answered only in front of the probe.

    A presence probe that waited for that acknowledgement would read the
    bus as silent; the ``#A`` broadcast is what the library asks for now.
    """
    async with Bus(preset("house")) as bus:
        assert bus.connection.device_answered is True


# --- reading and switching outputs ---------------------------------------


async def test_both_output_groups_of_a_twelve_channel_module_are_read() -> None:
    async with Bus(preset("twelve")) as bus:
        bus.module("81F6").outputs[6] = 0xFF  # channel 7
        assert await bus.command.get_output_state("81F6", 1) == "000000000000"
        assert await bus.command.get_output_state("81F6", 2) == "FF0000000000"


async def test_a_channel_switched_through_the_queue_reaches_the_module() -> None:
    async with Bus(preset("house")) as bus:
        await bus.command.set_output_state("4707", 3, 0xFF)
        await bus.settle()
        assert bus.module("4707").outputs[2] == 0xFF


async def test_six_channels_of_one_group_travel_as_one_frame() -> None:
    """The coalescing window: one frame, one acknowledgement, one click."""
    async with Bus(preset("house")) as bus:
        for channel in range(1, 7):
            await bus.command.set_output_state("4707", channel, 0xFF)
        await bus.settle()
        assert bytes(bus.module("4707").outputs[:6]) == b"\xff" * 6
        writes = [e for e in bus.events if e.startswith("$0515")]
        assert len(writes) == 1


async def test_a_channel_in_group_two_is_written_to_group_two() -> None:
    # Issue #148: a twelve-channel module's second group went missing.
    async with Bus(preset("twelve")) as bus:
        await bus.command.set_output_state("81F6", 9, 0xFF)
        await bus.settle()
        assert bus.module("81F6").outputs[8] == 0xFF
        assert bytes(bus.module("81F6").outputs[:6]) == bytes(6)


# --- a press, as a key would send it --------------------------------------


async def test_a_press_from_the_host_drives_the_linked_channels() -> None:
    """What the integration sends for a light on a feedback-LED plate.

    The press goes on the bus as a key's would, every module linked to
    that address acts on it, and the host learns the result by asking —
    the gateway does not relay a press the host sent itself.
    """
    async with Bus(preset("house")) as bus:
        await bus.api.press_button("0B1380")
        await bus.settle()
        assert bus.module("4707").outputs[0] == 0xFF
        assert bus.module("0E6C").outputs[0] == 0xFF
        assert await bus.command.get_output_state("4707", 1) == "FF0000000000"
        assert not [e for e in bus.events if e.startswith("#N")]


async def test_one_press_is_one_write_however_often_it_repeats() -> None:
    async with Bus(preset("house")) as bus:
        bus.api.press_repeat = 3
        command = bus.api.press_command("0B1380")
        assert command.count("#N0B1380") == 3
        await bus.api.press_button("0B1380")
        await bus.settle()
        # Three presses of an on / off link land where one does: the
        # module sees one press per frame, and three toggles end on.
        assert bus.module("4707").outputs[0] == 0xFF


# --- discovery ------------------------------------------------------------


class _Coordinator:
    """The host surface the library's discovery reads (CoordinatorProtocol)."""

    def __init__(self, bus: Bus) -> None:
        self._bus = bus
        self.nikobus_command = bus.command
        self.discovery_running = False
        self.discovery_module: object = False
        self.discovery_module_address: str | None = None
        self.inventory_query_type = None
        # The host's module store, in the shape the scan planner reads:
        # grouped by type, keyed by address.
        self.dict_module_data: dict = {}
        for spec in bus.installation.modules:
            self.dict_module_data.setdefault(spec.type, {})[spec.address] = {
                "address": spec.address,
                "channels": [{} for _ in range(spec.channels)],
            }

    def get_module_type(self, module_id: str) -> str | None:
        spec = self._bus.installation.module(module_id)
        return spec.type if spec else None

    def get_module_channel_count(self, module_id: str) -> int:
        spec = self._bus.installation.module(module_id)
        return spec.channels if spec else 0

    def get_button_channels(self, button_address: str) -> int | None:
        return None


async def _scan(bus: Bus, address: str, tmp_path) -> tuple[list[dict], dict]:
    """Run the real register scan against the simulator.

    Returns ``(records, store)``: the metadata of every record discovery
    decoded, in the order it read them, and the button store the merge
    left behind. Between the two sits ``add_to_command_mapping``, which
    is where 0.39.0 silently dropped every audio record — so a test that
    only looks at the first of them proves less than it appears to.
    """
    button_data: dict = {"nikobus_button": {}}
    discovery = NikobusDiscovery(
        _Coordinator(bus),
        config_dir=str(tmp_path),
        create_task=lambda coro: asyncio.get_running_loop().create_task(coro),
        button_data=button_data,
        on_button_save=None,
    )
    spec = bus.installation.module(address)
    assert spec is not None
    discovery.discovered_devices[address] = {
        "address": address,
        "channels": spec.channels,
    }

    records: list[dict] = []
    handle = discovery._handle_decoded_commands

    async def collect(module_address, decoded_commands):
        records.extend(c.metadata for c in decoded_commands)
        await handle(module_address, decoded_commands)

    discovery._handle_decoded_commands = collect
    bus.listener._event_callback = discovery.parse_module_inventory_response
    await discovery.query_module_inventory(address)
    return records, button_data["nikobus_button"]


def _links(records: list[dict], module: str) -> dict:
    """``{bus address: [(module, channel, mode)]}`` from decoded records."""
    out: dict[str, list] = {}
    for record in records:
        out.setdefault(record["button_address"], []).append(
            (module, record["channel"], record["M"])
        )
    return out


async def test_discovery_reads_back_the_links_that_were_declared(tmp_path) -> None:
    """Declare three links, scan the module, get three links.

    The whole chain: the encoder builds the module's memory, the gateway
    serves it register by register, the library's scan walks it and its
    decoders turn it back into links — with no hardware anywhere.
    """
    async with Bus(preset("house")) as bus:
        records, _store = await _scan(bus, "4707", tmp_path)

    assert _links(records, "4707") == {
        "0B1380": [("4707", 1, "M01 (On / off)")],
        "0B1390": [("4707", 2, "M01 (On / off)")],
        "0B13A0": [("4707", 7, "M05 (Impulse)")],
    }
    # And the keys of the plate came back with them.
    assert [r["key_raw"] for r in records] == [0, 1, 2]


async def test_discovery_reads_a_twelve_channel_module_whole(tmp_path) -> None:
    # Issue #148 in the other direction: every channel of both groups is
    # programmed, and the scan must come back with all twelve records.
    async with Bus(preset("twelve")) as bus:
        records, _store = await _scan(bus, "81F6", tmp_path)

    assert sorted(r["channel"] for r in records) == [7, 8, 9, 10, 11, 12]


async def test_discovery_reads_a_dimmers_eight_byte_records(tmp_path) -> None:
    """A dimmer's table is read eight bytes at a time, with its own function."""
    async with Bus(preset("house")) as bus:
        records, _store = await _scan(bus, "0E6C", tmp_path)

    assert _links(records, "0E6C") == {
        "0B1380": [("0E6C", 1, "M01 (Dim on/off (2 buttons))")]
    }


async def test_discovery_reads_an_audio_modules_triggers(tmp_path) -> None:
    """The 05-205's bank-01 table, the one the library learned in 0.39.0."""
    async with Bus(preset("audio")) as bus:
        records, _store = await _scan(bus, "8334", tmp_path)

    links = _links(records, "8334")
    assert len(links) == 33
    assert links["8083CF"] == [("8334", 1, "M16 (On)")]
    assert links["C083CF"] == [("8334", 1, "M17 (Off)")]
    assert links["A083CF"] == [("8334", 1, "M03 (Source 1)")]
    # The Power object is not a zone, so it drives no channel.
    assert links["8483CF"] == [("8334", None, "M01 (Power)")]


async def test_an_audio_trigger_reaches_the_button_store(tmp_path) -> None:
    """Decoding a record is not the same as keeping it.

    0.39.0 decoded all 35 records of a real module and then dropped
    every one of them on the way to the store, because
    ``add_to_command_mapping`` requires a key and an audio record had
    none — so no audio entity was ever built (Nikobus-HA #310). A test
    that stops at the decoder cannot see that; this one goes all the way
    to what the host reads.
    """
    async with Bus(preset("audio")) as bus:
        _records, store = await _scan(bus, "8334", tmp_path)

    assert len(store) == 33
    entry = store["8083CF"]
    assert entry["type"] == "Audio Trigger"
    assert (entry["audio_zone"], entry["audio_function"]) == (1, "M16 (On)")
    assert entry["audio_module_address"] == "8334"
    assert entry["operation_points"]["AUD"]["linked_modules"][0][
        "module_address"
    ] == "8334"
    # Four zones, eight functions each, and the module's Power object.
    zones = {e.get("audio_zone") for e in store.values()}
    assert zones == {1, 2, 3, 4, None}
    assert sum(1 for e in store.values() if e.get("audio_power")) == 1


async def test_a_wall_buttons_links_still_need_a_known_button(tmp_path) -> None:
    """The audio path is the exception, not a new general rule.

    A switch module's links are filed against buttons the inventory
    found; with an empty store there is nothing to file them under, and
    they stay unmatched rather than inventing a plate.
    """
    async with Bus(preset("house")) as bus:
        records, store = await _scan(bus, "4707", tmp_path)

    assert len(records) == 3
    assert store == {}
