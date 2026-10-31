"""Regression tests for the 0.56.3 audit fixes.

Command handler: a ``stop()`` while the worker waits in the coalesce
window or the read-before-write releases the caller's future; GET dedup
keys are owned by the queued item; the state-query group is re-announced
before every attempt; the read before a write accepts a held early
answer. Listener: our acknowledged query's answer is attributed to our
group. Discovery: ``reset_state`` resets the decoders, identity reads
are marked queued before the awaits, the inventory window waits while
reads are still queued, a frame that fails to parse is dropped alone.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from unittest.mock import MagicMock

import pytest

from nikobus_connect import command as cmdmod
from nikobus_connect.command import NikobusCommandHandler
from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.listener import NikobusEventListener


class _Conn:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, command: str) -> None:
        self.sent.append(command)


class _Listener:
    def __init__(self) -> None:
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_response = False
        self._awaited_answer: str | None = None
        self._last_query_group: dict[str, int] = {}
        self.announced: list[tuple[str, int]] = []

    def set_pending_query_group(self, addr: str, group: int) -> None:
        self._last_query_group[addr] = group
        self.announced.append((addr, group))


def _handler() -> tuple[NikobusCommandHandler, _Conn, _Listener]:
    conn, listener = _Conn(), _Listener()
    return NikobusCommandHandler(conn, listener), conn, listener


# --- command handler ------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_in_coalesce_window_releases_set_future() -> None:
    handler, _conn, _listener = _handler()
    await handler.start()
    fut = await handler.set_output_state("C9A5", 1, 0xFF)
    await asyncio.sleep(0.01)  # worker popped the item, sleeping in the window
    await handler.stop()
    await asyncio.sleep(0)
    assert fut.done() and fut.cancelled()
    assert handler._pending_set_groups == {}
    assert handler._writing_sets == {}


@pytest.mark.asyncio
async def test_stop_in_read_before_write_releases_set_future() -> None:
    handler, conn, listener = _handler()
    await handler.start()
    fut = await handler.set_output_state("C9A5", 1, 0xFF)
    await asyncio.sleep(cmdmod.SET_COALESCE_WINDOW + 0.03)
    assert conn.sent and conn.sent[0].startswith("$1012A5C9")  # the pre-read went out
    assert listener._awaiting_response
    await handler.stop()
    await asyncio.sleep(0)
    assert fut.cancelled()
    assert listener._awaiting_response is False
    assert handler._writing_sets == {}


@pytest.mark.asyncio
async def test_stale_get_pop_keeps_newer_items_dedup_key() -> None:
    handler, _conn, _listener = _handler()
    first = asyncio.create_task(handler.get_output_state("C9A5", 1))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert "C9A5_1" not in handler._queued_get_keys  # the owner cancelled: released
    second = asyncio.create_task(handler.get_output_state("C9A5", 1))
    await asyncio.sleep(0)
    assert handler._queued_get_keys["C9A5_1"]["future"] is not None
    owner = handler._queued_get_keys["C9A5_1"]
    assert handler._command_queue.qsize() == 2  # stale item + live item

    # Pop the stale item the way the worker does, without starting it.
    stale = handler._command_queue.get_nowait()
    assert stale["future"].cancelled()
    handler._release_get_key("C9A5_1", stale)
    assert handler._queued_get_keys["C9A5_1"] is owner  # not freed by the stale item

    third = asyncio.create_task(handler.get_output_state("C9A5", 1))
    await asyncio.sleep(0)
    assert handler._command_queue.qsize() == 1  # deduplicated onto the live item
    for task in (second, third):
        task.cancel()
    await asyncio.gather(second, third, return_exceptions=True)


@pytest.mark.asyncio
async def test_suppressed_caller_cancel_keeps_owner_key() -> None:
    handler, _conn, _listener = _handler()
    first = asyncio.create_task(handler.get_output_state("C9A5", 1))
    await asyncio.sleep(0)
    second = asyncio.create_task(handler.get_output_state("C9A5", 1))  # deduplicated
    await asyncio.sleep(0)
    assert handler._command_queue.qsize() == 1
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert "C9A5_1" in handler._queued_get_keys  # the first caller's item still holds it
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_drain_queue_releases_keys_of_drained_gets() -> None:
    handler, _conn, _listener = _handler()
    task = asyncio.create_task(handler.get_output_state("C9A5", 2))
    await asyncio.sleep(0)
    assert "C9A5_2" in handler._queued_get_keys
    assert handler.drain_queue() == 1
    assert handler._queued_get_keys == {}
    assert handler.pending_count() == 0
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_pending_count_filters_by_prefix() -> None:
    handler, _conn, _listener = _handler()
    await handler.queue_command("$1410F5A6A004B0")
    await handler.queue_command("$1410F5A6A104B1")
    await handler.queue_command("$1E12A5C9")
    assert handler.pending_count("$1410") == 2
    assert handler.pending_count() == 3


@pytest.mark.asyncio
async def test_query_group_announced_before_every_attempt(monkeypatch) -> None:
    handler, _conn, listener = _handler()
    monkeypatch.setattr(cmdmod, "COMMAND_ATTEMPT_TIMEOUT", 0.02)
    monkeypatch.setattr(cmdmod, "COMMAND_ANSWER_WAIT_TIMEOUT", 0.01)
    monkeypatch.setattr(cmdmod, "COMMAND_POST_ACK_ANSWER_TIMEOUT", 0.01)
    from nikobus_connect.exceptions import NikobusTimeoutError

    with pytest.raises(NikobusTimeoutError):
        await handler._send_command_get_answer("$1012A5C94B71C1", "C9A5", attempts=3)
    assert listener.announced == [("C9A5", 1)] * 3
    assert listener._awaited_query is None


@pytest.mark.asyncio
async def test_read_before_write_uses_held_early_answer(monkeypatch) -> None:
    """A gateway that sends the state answer before the ack still feeds the pre-read."""
    handler, conn, listener = _handler()
    monkeypatch.setattr(cmdmod, "COMMAND_ATTEMPT_TIMEOUT", 0.2)
    monkeypatch.setattr(cmdmod, "COMMAND_ANSWER_WAIT_TIMEOUT", 0.05)
    monkeypatch.setattr(cmdmod, "COMMAND_POST_ACK_ANSWER_TIMEOUT", 0.05)
    answer = "$1C" + "A5C9" + "00" + "FF0000000000" + "0000"

    async def send(command: str) -> None:
        conn.sent.append(command)
        listener.response_queue.put_nowait(answer)  # answer first ...
        listener.response_queue.put_nowait("$0512")  # ... then the ack, then silence

    conn.send = send  # type: ignore[method-assign]
    state = await handler._send_command_get_answer("$1012A5C94B71C1", "C9A5", attempts=1)
    assert state == "FF0000000000"


# --- listener attribution --------------------------------------------------


def _listener_with_callback() -> tuple[NikobusEventListener, list[tuple[int, str]]]:
    seen: list[tuple[int, str]] = []

    async def feedback(group: int, message: str) -> None:
        seen.append((group, message))

    listener = NikobusEventListener(
        MagicMock(),
        event_callback=lambda _m: None,
        feedback_callback=feedback,
        has_feedback_module=True,
    )
    return listener, seen


@pytest.mark.asyncio
async def test_acknowledged_query_answer_keeps_our_group() -> None:
    listener, seen = _listener_with_callback()
    answer = "$1CA5C9FF0000000000A1B2"  # CRC not validated: patch
    listener.validate_crc = lambda _m: True  # type: ignore[method-assign]
    listener.set_pending_query_group("C9A5", 1)
    listener._awaiting_response = True
    listener._awaited_answer = "$1CA5C9"
    await listener._dispatch_message("$0512")  # our ack
    await listener._dispatch_message("$1017A5C9AABB")  # Feedback Module asks group 2
    await listener._dispatch_message(answer)
    assert seen == [(1, answer)]
    assert listener._last_query_group["C9A5"] == 1


@pytest.mark.asyncio
async def test_answer_before_our_ack_follows_last_query_seen() -> None:
    listener, seen = _listener_with_callback()
    answer = "$1CA5C9FF0000000000A1B2"
    listener.validate_crc = lambda _m: True  # type: ignore[method-assign]
    listener.set_pending_query_group("C9A5", 1)
    listener._awaiting_response = True
    listener._awaited_answer = "$1CA5C9"
    await listener._dispatch_message("$1017A5C9AABB")  # Feedback Module asks group 2 ...
    await listener._dispatch_message(answer)  # ... and this answer came before our ack
    assert seen == [(2, answer)]


# --- discovery -------------------------------------------------------------


class _Coord:
    def __init__(self) -> None:
        self.nikobus_command: object | None = None
        self.dict_module_data: dict = {}
        self.discovery_running = False
        self.discovery_module = False
        self.discovery_module_address: str | None = None
        self.inventory_query_type: str | None = None

    def get_module_type(self, _address: str) -> None:
        return None

    def get_module_channel_count(self, _address: str) -> int:
        return 12

    def get_button_channels(self, _address: str) -> int:
        return 2


def _discovery(tmp_path, coordinator: _Coord | None = None) -> NikobusDiscovery:
    return NikobusDiscovery(
        coordinator or _Coord(),
        config_dir=str(tmp_path),
        create_task=asyncio.ensure_future,
        button_data={"nikobus_button": {}},
        module_data={"nikobus_module": {}},
    )


@pytest.mark.asyncio
async def test_reset_state_resets_decoders_and_scan_counters(tmp_path) -> None:
    discovery = _discovery(tmp_path)
    switch = next(d for d in discovery._decoders if d.module_type == "switch_module")
    switch._stream_alignment_decided = True
    switch._module_misaligned = True
    pc_logic = next(d for d in discovery._decoders if d.module_type == "pc_logic")
    pc_logic._input_stream = bytearray(b"\x01" * 32)
    pc_logic._input_count = 5
    discovery._scan_expected_records = 7
    discovery._scan_decoded_records = 3
    discovery._scan_missed_acks = 2
    discovery._abort_discovery_run("host cancelled")
    assert switch._module_misaligned is False
    assert switch._stream_alignment_decided is False
    assert len(pc_logic._input_stream) == 0
    assert discovery._scan_expected_records is None
    assert discovery._scan_decoded_records == 0
    assert discovery._scan_missed_acks == 0


@pytest.mark.asyncio
async def test_identity_reads_marked_queued_before_awaiting(tmp_path) -> None:
    coordinator = _Coord()
    command = MagicMock()
    gate = asyncio.Event()
    queued: list[str] = []

    async def queue_command(cmd: str, **_kwargs) -> None:
        queued.append(cmd)
        await gate.wait()  # the queue is full: block

    command.queue_command = queue_command
    command.pending_count = MagicMock(return_value=0)
    coordinator.nikobus_command = command
    discovery = _discovery(tmp_path, coordinator)
    discovery.discovery_stage = "inventory_addresses"
    discovery._inventory_addresses = {"86F5"}

    task = asyncio.create_task(discovery._queue_inventory_identity_queries_for_address("86F5"))
    await asyncio.sleep(0)
    assert "86F5" in discovery._inventory_identity_queued  # marked while still blocked
    assert discovery._inventory_addresses - discovery._inventory_identity_queued == set()
    gate.set()
    await task
    assert len(queued) == 96


@pytest.mark.asyncio
async def test_inventory_window_waits_while_identity_reads_queued(tmp_path) -> None:
    coordinator = _Coord()
    command = MagicMock()
    command.pending_count = MagicMock(return_value=40)
    coordinator.nikobus_command = command
    discovery = _discovery(tmp_path, coordinator)
    discovery.discovery_stage = "inventory_identity"
    discovery.discovered_devices = {"86F5": {"category": "Module", "module_type": "pc_link"}}
    rearmed: list[bool] = []
    discovery._schedule_inventory_timeout = lambda: rearmed.append(True)  # type: ignore[method-assign]
    await discovery._finalize_inventory_phase()
    assert rearmed == [True]
    assert discovery.discovery_stage == "inventory_identity"  # phase not closed
    command.pending_count.assert_called_with("$1410")


@pytest.mark.asyncio
async def test_identity_read_sent_rearms_window_in_inventory_only(tmp_path) -> None:
    discovery = _discovery(tmp_path)
    rearmed: list[bool] = []
    discovery._schedule_inventory_timeout = lambda: rearmed.append(True)  # type: ignore[method-assign]
    discovery.discovery_stage = "inventory_identity"
    await discovery._identity_read_sent()
    discovery.discovery_stage = "register_scan"
    await discovery._identity_read_sent()
    assert rearmed == [True]


@pytest.mark.asyncio
async def test_identity_reads_carry_the_rearm_handler(tmp_path) -> None:
    coordinator = _Coord()
    command = MagicMock()
    handlers: list[object] = []

    async def queue_command(_cmd: str, completion_handler=None, **_kwargs) -> None:
        handlers.append(completion_handler)

    command.queue_command = queue_command
    coordinator.nikobus_command = command
    discovery = _discovery(tmp_path, coordinator)
    await discovery._run_inventory_identity_queries({"86F5"})
    assert len(handlers) == 96
    assert all(h == discovery._identity_read_sent for h in handlers)


@pytest.mark.asyncio
async def test_unparseable_frame_does_not_reset_the_run(tmp_path, caplog) -> None:
    discovery = _discovery(tmp_path)
    discovery._coordinator.discovery_running = True
    discovery.discovery_stage = "register_scan"
    discovery._register_scan_queue = ["C9A5"]
    with caplog.at_level(logging.ERROR):
        await discovery.parse_module_inventory_response("$2E" + "ZZ")  # not hex
    assert discovery._coordinator.discovery_running is True
    assert discovery.discovery_stage == "register_scan"
    assert discovery._register_scan_queue == ["C9A5"]


@pytest.mark.asyncio
async def test_decoded_records_attributed_to_scanned_module(tmp_path) -> None:
    from nikobus_connect.discovery.discovery import DecodedCommand

    discovery = _discovery(tmp_path)
    discovery._coordinator.discovery_module_address = "C9A5"
    discovery._module_address = "1234"  # a late frame from the previous module set this
    discovery._scan_decoded_records = 0
    record = MagicMock(spec=DecodedCommand)
    before = discovery._progress_decoded_records
    with contextlib.suppress(Exception):  # only the counting at the top is under test
        await discovery._handle_decoded_commands("1234", [record])
    assert discovery._progress_decoded_records == before + 1
    assert discovery._scan_decoded_records == 0  # not the module being scanned
