"""The whole-module write must not drop the second group.

``set_output_states`` writes both output groups of a module: ``0x15``
for channels 1-6, ``0x16`` for 7-12. Whether the second frame is sent
is a property of the *hardware* — does this module have twelve outputs —
but it used to be guessed from the state buffer: "any non-zero byte in
group 2". Six zero bytes mean two different things there, "no second
group" and "all six channels are being turned off", so a 12-channel
module switched fully off never received its ``0x16`` frame. The relays
stayed on while the caller's state said off, until the next poll put
the entities back on (issue #148).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from nikobus_connect.api import NikobusAPI
from nikobus_connect.command import NikobusCommandHandler

ADDR = "81F6"
GROUP_1 = "$1E15F681"   # 0x15 + address little-endian
GROUP_2 = "$1E16F681"   # 0x16 + address little-endian


def _handler(state: bytearray) -> NikobusCommandHandler:
    handler = NikobusCommandHandler(
        connection=MagicMock(), listener=MagicMock(), module_states={ADDR: state}
    )
    return handler


def _queued(handler: NikobusCommandHandler) -> list[dict]:
    items = []
    while not handler._command_queue.empty():
        items.append(handler._command_queue.get_nowait())
    return items


def _payload(command: str) -> str:
    """The six state bytes of a set frame."""
    return command[9:21]


# --- the regression ------------------------------------------------------


async def test_twelve_channel_module_switched_fully_off_still_writes_group_2() -> None:
    handler = _handler(bytearray(12))  # every channel off
    await handler.set_output_states(ADDR, num_channels=12)
    commands = [item["command"] for item in _queued(handler)]
    assert len(commands) == 2
    assert commands[0].startswith(GROUP_1)
    assert commands[1].startswith(GROUP_2)
    assert _payload(commands[1]) == "00" * 6


async def test_six_channel_module_writes_only_group_1() -> None:
    handler = _handler(bytearray(12))
    done = AsyncMock()
    await handler.set_output_states(ADDR, completion_handler=done, num_channels=6)
    items = _queued(handler)
    assert len(items) == 1
    assert items[0]["command"].startswith(GROUP_1)
    # The one frame sent carries the caller's completion handler.
    assert items[0]["completion_handler"] is done


async def test_completion_handler_rides_the_last_frame() -> None:
    handler = _handler(bytearray(12))
    done = AsyncMock()
    await handler.set_output_states(ADDR, completion_handler=done, num_channels=12)
    items = _queued(handler)
    assert [item["completion_handler"] for item in items] == [None, done]


# --- the fallback, for callers that cannot supply the count --------------


async def test_without_a_count_a_non_empty_group_2_is_still_written() -> None:
    state = bytearray(12)
    state[6] = 0xFF
    handler = _handler(state)
    await handler.set_output_states(ADDR)
    commands = [item["command"] for item in _queued(handler)]
    assert len(commands) == 2
    assert _payload(commands[1]) == "FF" + "00" * 5


async def test_without_a_count_an_all_zero_group_2_is_assumed_absent(caplog) -> None:
    """The documented limit of the guess — and why callers pass the count."""
    handler = _handler(bytearray(12))
    with caplog.at_level("DEBUG"):
        await handler.set_output_states(ADDR)
    assert len(_queued(handler)) == 1
    assert "channel count unknown" in caplog.text


async def test_a_zero_count_is_treated_as_unknown() -> None:
    """A caller whose inventory has no entry passes 0, not a 6-channel claim."""
    state = bytearray(12)
    state[7] = 0xFF
    handler = _handler(state)
    await handler.set_output_states(ADDR, num_channels=0)
    assert len(_queued(handler)) == 2


# --- the API supplies the count from its inventory ----------------------


def _api(module_data: dict) -> tuple[NikobusAPI, MagicMock]:
    handler = MagicMock()
    handler.set_output_states = AsyncMock()
    return NikobusAPI(handler, module_data), handler


async def test_api_takes_the_channel_count_from_its_inventory() -> None:
    api, handler = _api(
        {"switch_module": {ADDR: {"channels": [{} for _ in range(12)]}}}
    )
    await api.set_output_states_for_module(address=ADDR)
    assert handler.set_output_states.await_args.kwargs["num_channels"] == 12


async def test_api_finds_the_module_whatever_its_type_and_case() -> None:
    api, handler = _api(
        {"dimmer_module": {"0E6C": {"channels": [{} for _ in range(6)]}}}
    )
    await api.set_output_states_for_module(address="0e6c")
    assert handler.set_output_states.await_args.kwargs["num_channels"] == 6
    assert api.module_channel_count("0E6C") == 6


async def test_api_passes_none_for_a_module_it_does_not_know() -> None:
    api, handler = _api({"switch_module": {"4707": {"channels": [{}]}}})
    await api.set_output_states_for_module(address=ADDR)
    assert handler.set_output_states.await_args.kwargs["num_channels"] is None
    assert api.module_channel_count(ADDR) is None


async def test_an_explicit_count_wins_over_the_inventory() -> None:
    api, handler = _api({"switch_module": {ADDR: {"channels": [{} for _ in range(6)]}}})
    await api.set_output_states_for_module(address=ADDR, num_channels=12)
    assert handler.set_output_states.await_args.kwargs["num_channels"] == 12
