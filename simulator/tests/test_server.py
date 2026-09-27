"""The socket the host connects to, and the command line that opens it."""

from __future__ import annotations

import asyncio

import pytest
from nikobus_simulator.server import NikobusSimulator, main
from nikobus_simulator.topology import preset


async def _exchange(port: int, *lines: str) -> list[str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write("".join(f"{line}\r" for line in lines).encode("ascii"))
        await writer.drain()
        answers = []
        while True:
            try:
                data = await asyncio.wait_for(reader.readuntil(b"\r"), 0.5)
            except (TimeoutError, asyncio.IncompleteReadError):
                break
            answers.append(data.decode("ascii").strip())
        return answers
    finally:
        writer.close()


async def test_the_simulator_binds_a_port_and_answers_on_it() -> None:
    async with NikobusSimulator(preset("house")) as simulator:
        assert simulator.port != 0
        assert simulator.connection_string == f"127.0.0.1:{simulator.port}"
        assert await _exchange(simulator.port, "#A") == [
            "$18F58600500F3FFFAC61FE"
        ]


async def test_a_press_and_a_query_in_one_write_are_read_as_two_lines() -> None:
    """The host sends a press burst as a single write; framing must hold."""
    async with NikobusSimulator(preset("house")) as simulator:
        answers = await _exchange(
            simulator.port, "#N0B1380", "#E1", "#N0B1380", "#E1", "#A"
        )
        assert answers[-1].startswith("$18F586")
        assert simulator.gateway.modules["4707"].outputs[0] == 0xFF


async def test_a_client_that_disappears_does_not_take_the_server_with_it() -> None:
    async with NikobusSimulator(preset("house")) as simulator:
        _reader, writer = await asyncio.open_connection("127.0.0.1", simulator.port)
        writer.close()
        await asyncio.sleep(0.05)
        assert await _exchange(simulator.port, "#A")


async def test_stopping_twice_is_harmless() -> None:
    simulator = NikobusSimulator(preset("house"))
    await simulator.start()
    await simulator.stop()
    await simulator.stop()


def test_the_command_line_needs_a_preset_or_a_file() -> None:
    with pytest.raises(SystemExit):
        main()


def test_the_command_line_rejects_a_preset_that_is_not_shipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["nikobus-simulator", "--preset", "mansion"])
    with pytest.raises(KeyError, match="unknown preset"):
        main()
