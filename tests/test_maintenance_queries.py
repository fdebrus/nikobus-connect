"""Maintenance queries: module status, EEPROM CRC, PC-Link clock, block reads."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from nikobus_connect.command import NikobusCommandHandler
from nikobus_connect.protocol import (
    FUNC_GET_TIME,
    FUNC_MODULE_STATUS,
    FUNC_READ_BLOCK16,
    FUNC_SET_TIME,
    append_crc1,
    append_crc2,
    make_block_index_args,
    make_pc_link_command,
    make_set_time_args,
    parse_module_crc,
    parse_module_status,
    parse_pc_link_time,
    reply_payload,
    wire_address,
)


def _frame(data_hex: str) -> str:
    """Build a well-formed reply frame around ``data_hex`` (CRC16 + CRC8)."""
    body = f"${len(data_hex) + 10:02X}{append_crc1(data_hex)}"
    return append_crc2(body)


# --- pure parsers ----------------------------------------------------------


def test_reply_payload_strips_prefix_and_crcs():
    # Real #A answer of a PC-Link: addr F586, status 00, signature 50.
    assert reply_payload("$18F58600500F3FFFAC61FE").hex().upper() == "F58600500F3FFF"
    assert reply_payload("$0511") == b""
    assert reply_payload("garbage") == b""


def test_module_status_fields():
    status = parse_module_status(bytes.fromhex("F58601500F3F07"), "86f5")
    assert status.address == "86F5"
    assert status.eeprom_error is True
    assert status.type_code == 0x50
    assert (status.record_count_a, status.record_count_b) == (0x3F, 0x07)
    with pytest.raises(ValueError):
        parse_module_status(b"\x00\x01", "86F5")


def test_module_crc_little_endian():
    assert parse_module_crc(bytes.fromhex("FFF5860000CDAB")) == 0xABCD


def test_pc_link_time_round_trip():
    moment = datetime(2026, 9, 3, 21, 10, 43)  # noqa: DTZ001
    args = make_set_time_args(moment)
    assert args.hex().upper() == "1A0903150A2BFF"
    assert parse_pc_link_time(b"\xff\x62\x9e" + args[:6]) == moment
    with pytest.raises(ValueError):
        parse_pc_link_time(bytes.fromhex("FF629E9BC1000000FF"))  # unset clock
    with pytest.raises(ValueError):
        make_set_time_args(datetime(1999, 1, 1))  # noqa: DTZ001


def test_block_index_args_little_endian():
    assert make_block_index_args(0x012C) == b"\x2c\x01"
    assert make_pc_link_command(FUNC_READ_BLOCK16, "86F5", make_block_index_args(0x10)).startswith("$1410F5861000")


def test_wire_address():
    assert wire_address("86F5") == "F586"


# --- command handler query path ------------------------------------------


class _FakeConnection:
    def __init__(self, replies):
        self.sent: list[str] = []
        self._replies = replies  # func hex -> list of frames to enqueue
        self.listener = None

    async def send(self, command: str) -> None:
        self.sent.append(command)
        for frame in self._replies.get(command[3:5], []):
            self.listener._enqueue_response(frame)


class _FakeListener:
    def __init__(self):
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_response = False

    def _enqueue_response(self, message: str) -> None:
        self.response_queue.put_nowait(message)

    def set_pending_query_group(self, addr, group):
        pass


async def _run_query(func: int, address: str, frames: list[str], args: bytes | None = None):
    listener = _FakeListener()
    conn = _FakeConnection({f"{func:02X}": frames})
    conn.listener = listener
    handler = NikobusCommandHandler(conn, listener)
    await handler.start()
    try:
        return await asyncio.wait_for(handler.query(func, address, args), timeout=5), conn.sent
    finally:
        await handler.stop()


def test_query_module_status_returns_payload():
    async def scenario():
        ack = "$0511"
        answer = _frame("F58600500F3FFF")
        payload, sent = await _run_query(FUNC_MODULE_STATUS, "86F5", [ack, answer])
        assert sent[0].startswith("$1011F586")
        assert parse_module_status(payload, "86F5").type_code == 0x50

    asyncio.run(scenario())


def test_query_time_uses_ff_prefixed_answer():
    async def scenario():
        answer = _frame("FFF5861A0903150A2B")
        payload, _ = await _run_query(FUNC_GET_TIME, "86F5", ["$051D", answer])
        assert parse_pc_link_time(payload) == datetime(2026, 9, 3, 21, 10, 43)  # noqa: DTZ001

    asyncio.run(scenario())


def test_query_set_time_is_ack_only():
    async def scenario():
        payload, sent = await _run_query(
            FUNC_SET_TIME, "86F5", ["$051E", "$0EFFF586" + "0000" + "00"], make_set_time_args(datetime(2026, 1, 2, 3, 4, 5))  # noqa: DTZ001
        )
        assert payload == b""
        assert sent[0].startswith("$1E1EF5861A01020304")

    asyncio.run(scenario())


def test_query_ignores_unrelated_frames():
    async def scenario():
        other = _frame("F58600500F3FFF").replace("F586", "AAAA", 1)
        answer = _frame("F58600500F3FFF")
        payload, _ = await _run_query(FUNC_MODULE_STATUS, "86F5", ["$0511", other, answer])
        assert payload[:2] == bytes.fromhex("F586")

    asyncio.run(scenario())
