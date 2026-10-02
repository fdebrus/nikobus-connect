"""A set-output request reports its outcome; one attempt fits the caller's wait.

From the 2026-10-01 audit: ``set_output_state`` returned as soon as the
request was queued, a failed exchange was only logged, and the
per-attempt budget on the bus was the caller's whole timeout.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from nikobus_connect import const
from nikobus_connect.command import NikobusCommandHandler
from nikobus_connect.exceptions import NikobusTimeoutError


class _Listener:
    def __init__(self) -> None:
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_response = False
        self._awaited_answer = None

    def set_pending_query_group(self, *_args) -> None:
        pass


class _Connection:
    """Acknowledges every frame unless told to stay silent."""

    def __init__(self, listener: _Listener) -> None:
        self.sent: list[str] = []
        self._listener = listener
        self.silent = False

    async def send(self, command: str) -> None:
        self.sent.append(command)
        if self.silent:
            return
        gid = command[3:5]
        addr_le = command[5:9]
        self._listener.response_queue.put_nowait(f"$05{gid}")
        self._listener.response_queue.put_nowait(f"$0EFF{addr_le}00")


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.SET_COALESCE_WINDOW", 0.01)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_ATTEMPT_TIMEOUT", 0.05)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_ANSWER_WAIT_TIMEOUT", 0.05)
    listener = _Listener()
    connection = _Connection(listener)
    h = NikobusCommandHandler(connection=connection, listener=listener, module_states={})
    h._test_connection = connection  # type: ignore[attr-defined]
    return h


@pytest.mark.asyncio
async def test_a_set_request_resolves_once_acknowledged(handler) -> None:
    await handler.start()
    try:
        future = await handler.set_output_state("81F6", 1, 0xFF)
        result = await asyncio.wait_for(future, 2.0)
    finally:
        await handler.stop()
    assert result is not None
    assert len([f for f in handler._test_connection.sent if f[3:5] == "15"]) == 1


@pytest.mark.asyncio
async def test_a_failed_set_fails_the_future_and_calls_the_handlers(handler) -> None:
    handler._test_connection.silent = True
    failures: list[BaseException] = []
    completions: list[int] = []
    await handler.start()
    try:
        first = await handler.set_output_state(
            "81F6", 1, 0xFF,
            completion_handler=lambda: completions.append(1),
            failure_handler=failures.append,
        )
        second = await handler.set_output_state("81F6", 2, 0xFF, failure_handler=failures.append)
        assert second is first, "a joiner shares the pending request's future"
        with pytest.raises(NikobusTimeoutError):
            await asyncio.wait_for(first, 5.0)
        await asyncio.sleep(0.02)
    finally:
        await handler.stop()
    assert len(failures) == 2 and all(isinstance(e, NikobusTimeoutError) for e in failures)
    assert completions == []
    # Three attempts went out, none more.
    assert len([f for f in handler._test_connection.sent if f[3:5] == "15"]) == const.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_an_ignored_failure_is_not_an_unretrieved_exception(handler, caplog) -> None:
    handler._test_connection.silent = True
    await handler.start()
    try:
        future = await handler.set_output_state("81F6", 1, 0xFF)
        await asyncio.sleep(0.4)
        assert future.done() and future.exception() is not None
    finally:
        await handler.stop()
    del future
    await asyncio.sleep(0)
    assert "never retrieved" not in caplog.text


@pytest.mark.asyncio
async def test_the_attempts_fit_in_the_callers_wait(handler) -> None:
    """Three attempts at the per-attempt budget end before the caller's
    total wait would have; before, one attempt alone took the whole of it."""
    handler._test_connection.silent = True
    await handler.start()
    started = time.monotonic()
    try:
        future = await handler.set_output_state("81F6", 1, 0xFF)
        with pytest.raises(NikobusTimeoutError):
            await asyncio.wait_for(future, 5.0)
    finally:
        await handler.stop()
    elapsed = time.monotonic() - started
    assert elapsed < 0.05 * const.MAX_ATTEMPTS + 0.5


def test_the_attempt_budget_is_a_share_of_the_total() -> None:
    assert const.COMMAND_ATTEMPT_TIMEOUT * const.MAX_ATTEMPTS == const.COMMAND_ACK_WAIT_TIMEOUT


def test_switch_records_report_the_chain_byte_not_a_timer() -> None:
    from nikobus_connect.discovery.protocol import normalize_payload
    from nikobus_connect.discovery.switch_decoder import decode

    class _Ctx:
        module_address = "9105"
        module_channel_count = 12
        coordinator = None

    from nikobus_connect.discovery.protocol import reverse_hex

    # A real switch record in memory order (key 1, channel 4, M01, button
    # 182F18); the chunk layer hands the decoder the reversed bytes, so
    # the record's sixth byte — FF, no further record with this hash —
    # comes first.
    payload = reverse_hex("60BC60F013FF")
    decoded = decode(payload, normalize_payload(payload), _Ctx)
    assert decoded is not None
    assert decoded["t2_raw"] is None
    assert decoded["chain_raw"] == 0xFF
