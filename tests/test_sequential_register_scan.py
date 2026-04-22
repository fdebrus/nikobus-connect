"""Tests for the sequential send-and-wait register scan.

Replaces the former fire-and-forget queue fill: each register read is
sent one at a time; the scan loop waits for the $05 ACK, then up to
MODULE_SCAN_DATA_TIMEOUT for a matching $2E data frame. A $18 trailer
short-circuits the remaining reads.

These tests drive ``_scan_module_registers`` directly with a fake
connection + listener so we can assert exact send order, retry
behaviour, and trailer handling without spinning up real asyncio
transports or the event listener loop.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nikobus_connect import const
from nikobus_connect.discovery.discovery import NikobusDiscovery


# --- fakes ---------------------------------------------------------------


class FakeListener:
    def __init__(self) -> None:
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_response: bool = False


class FakeConnection:
    """Test double: every send() invokes a user-supplied async handler.

    The handler decides what — if anything — to enqueue on the listener
    or notify on the discovery instance for the given command.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.on_send = None  # set by the test

    async def send(self, command: str) -> None:
        self.sent.append(command)
        if self.on_send is not None:
            await self.on_send(command)


class FakeCommand:
    def __init__(self) -> None:
        self._listener = FakeListener()
        self._connection = FakeConnection()


class FakeCoordinator:
    def __init__(self) -> None:
        self.nikobus_command = FakeCommand()
        self.dict_module_data: dict = {}
        self.discovery_running = False
        self.discovery_module = False
        self.discovery_module_address: str | None = None
        self.inventory_query_type = None


def _drop_coro(coro):
    try:
        coro.close()
    except AttributeError:
        pass
    task = MagicMock()
    task.cancel = MagicMock()
    return task


def _make_discovery(tmp_path) -> NikobusDiscovery:
    coord = FakeCoordinator()
    return NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=_drop_coro,
        button_data={"nikobus_button": {}},
        on_button_save=None,
    )


def _register_from_command(command: str) -> int:
    """Extract the register byte (hex string 0xNN) from our inventory command.

    Layout: ``$14 <fn:2><addr:4><reg:2>04 <crc16><crc8>``.
    """

    # "$14" header = 3 chars, then fn=2, addr=4 → 9. Register is chars 9-10.
    return int(command[9:11], 16)


def _ack_for(command: str) -> str:
    """The ACK the module would send back for a given command."""

    return f"$05{command[3:5]}"


# --- test 1: full scan decodes every data frame & stops at trailer -------


@pytest.mark.asyncio
async def test_sequential_scan_decodes_data_frames_and_honours_trailer(
    tmp_path, monkeypatch
):
    """Feed a script: ACK + data frame for registers 0x10-0x20, then a
    trailer at 0x20. Assert the scan terminates at 0x20 and every data
    frame is delivered to the scan-frame hook (so decoding would run).
    """

    # Keep the loop tight — we're firing ~17 iterations.
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)

    discovery = _make_discovery(tmp_path)
    discovery._coordinator.discovery_module = True
    delivered_frames: list[str] = []

    async def on_send(command: str) -> None:
        reg = _register_from_command(command)
        ack = _ack_for(command)
        # ACK always comes back.
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)
        if reg == 0x20:
            # Trailer frame — module signals end of programmed memory.
            frame = "$18FFFFFFFFFFFFFFBF9558"
        elif reg in (0x10, 0x11, 0x15):
            # Real data frames for a few registers.
            frame = f"$2E0747812454F00BFF741528F01002741528F078B67{reg:01X}"
        else:
            # Empty register — ACK only, no $2E data.
            return
        delivered_frames.append(frame)
        # Simulate the listener routing: call the parser directly, which
        # in turn calls ``_notify_scan_frame``.
        await discovery.parse_module_inventory_response(frame)

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    await discovery._scan_module_registers(
        "4707", "100747", range(0x10, 0x30)
    )

    # 17 registers: 0x10..0x20 inclusive. Scan must stop at 0x20 (trailer).
    sent = discovery._coordinator.nikobus_command._connection.sent
    registers_sent = [_register_from_command(c) for c in sent]
    assert registers_sent[0] == 0x10
    assert registers_sent[-1] == 0x20
    assert 0x21 not in registers_sent
    # Trailer was observed and cleared again when the scan exited.
    assert discovery._scan_trailer_seen is False
    assert discovery._scan_active is False
    # The scan hook saw every data frame (3 real frames + 1 trailer).
    assert delivered_frames[-1].startswith("$18")
    assert sum(1 for f in delivered_frames if f.startswith("$2E")) == 3


# --- test 2: missing ACK retries once ------------------------------------


@pytest.mark.asyncio
async def test_missing_ack_retries_once_then_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    # Tighten timeouts so the test finishes fast when an ACK is missed.
    monkeypatch.setattr(const, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(const, "MODULE_SCAN_DATA_TIMEOUT", 0.02)

    # The module-level attributes in discovery.py import the names; for
    # the test to actually see the smaller timeouts, patch there too.
    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    discovery = _make_discovery(tmp_path)
    discovery._coordinator.discovery_module = True

    drop_attempts: dict[int, int] = {}

    async def on_send(command: str) -> None:
        reg = _register_from_command(command)
        # Drop the first ACK for register 0x12 only.
        if reg == 0x12 and drop_attempts.get(reg, 0) == 0:
            drop_attempts[reg] = 1
            return  # silence — listener gets nothing
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    await discovery._scan_module_registers("4707", "100747", range(0x10, 0x14))

    sent = discovery._coordinator.nikobus_command._connection.sent
    registers_sent = [_register_from_command(c) for c in sent]
    # 0x12 was sent twice (original + one retry). 0x10, 0x11, 0x13 once each.
    assert registers_sent.count(0x12) == 2
    assert registers_sent.count(0x10) == 1
    assert registers_sent.count(0x11) == 1
    assert registers_sent.count(0x13) == 1


# --- test 3: ACK without data frame is not an error ----------------------


@pytest.mark.asyncio
async def test_empty_register_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    discovery = _make_discovery(tmp_path)
    discovery._coordinator.discovery_module = True

    async def on_send(command: str) -> None:
        # ACK every command; no $2E frames ever.
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    # Must complete without raising; scan progresses through every register.
    await discovery._scan_module_registers("4707", "100747", range(0x10, 0x14))

    sent = discovery._coordinator.nikobus_command._connection.sent
    registers_sent = [_register_from_command(c) for c in sent]
    assert registers_sent == [0x10, 0x11, 0x12, 0x13]


# --- test 4: trailer short-circuits the loop -----------------------------


@pytest.mark.asyncio
async def test_trailer_halts_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    discovery = _make_discovery(tmp_path)
    discovery._coordinator.discovery_module = True

    async def on_send(command: str) -> None:
        reg = _register_from_command(command)
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)
        if reg == 0x20:
            await discovery.parse_module_inventory_response(
                "$18FFFFFFFFFFFFFFBF9558"
            )

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    # Full 240-register range; must stop at 0x20.
    await discovery._scan_module_registers("4707", "100747", range(0x10, 0x100))

    sent = discovery._coordinator.nikobus_command._connection.sent
    registers_sent = [_register_from_command(c) for c in sent]
    assert registers_sent[-1] == 0x20
    # 0x21..0xFF must never be sent.
    assert all(r <= 0x20 for r in registers_sent)


# --- test 5: concurrent scans are serialised via the lock ----------------


@pytest.mark.asyncio
async def test_concurrent_scans_do_not_interleave(tmp_path, monkeypatch):
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    discovery = _make_discovery(tmp_path)
    discovery._coordinator.discovery_module = True

    async def on_send(command: str) -> None:
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    # Fire two scans in parallel. The second one must wait until the
    # first releases the scan lock — commands from the two must NOT
    # interleave in connection.sent.
    first = asyncio.create_task(
        discovery._scan_module_registers("AAAA", "10AAAA", range(0x10, 0x14))
    )
    second = asyncio.create_task(
        discovery._scan_module_registers("BBBB", "10BBBB", range(0x10, 0x14))
    )

    await asyncio.gather(first, second)

    sent = discovery._coordinator.nikobus_command._connection.sent
    # All 4 commands for one module complete before any for the other.
    first_prefix_count = sum(1 for c in sent[:4] if c.startswith("$1410AAAA"))
    last_prefix_count = sum(1 for c in sent[4:] if c.startswith("$1410BBBB"))
    assert first_prefix_count + last_prefix_count == 8, (
        f"commands interleaved: {sent}"
    )


# --- test 6: trailer predicate pure-function -----------------------------


@pytest.mark.asyncio
async def test_giveup_on_ack_timeout_flushes_buffer_and_queue(
    tmp_path, monkeypatch
):
    """After a register is given up on, stale remainder bytes and any late
    ACK/data must not leak into the next register's decode.

    Reproduces the drift observed in the wild: first register's ACK arrived
    70 ms after both retries timed out. The late ACK then matched the next
    register's wait, and the late data frame concatenated with that
    register's buffer — every subsequent chunk was misaligned by 4 bytes,
    producing phantom records.
    """

    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    from nikobus_connect.discovery import discovery as dmod

    monkeypatch.setattr(dmod, "MODULE_SCAN_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(dmod, "MODULE_SCAN_DATA_TIMEOUT", 0.02)
    monkeypatch.setattr(dmod, "COMMAND_EXECUTION_DELAY", 0.0)

    discovery = _make_discovery(tmp_path)
    discovery._coordinator.discovery_module = True
    # Seed the payload buffer with 4 bytes of "previous" remainder the test
    # can check for after the give-up flush.
    discovery._payload_buffer = "DEADBEEF"

    async def on_send(command: str) -> None:
        reg = _register_from_command(command)
        if reg == 0x10:
            # Never ACK — force the give-up path.
            return
        ack = _ack_for(command)
        discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(ack)

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    # Inject a stale ACK into the queue to simulate a late arrival racing
    # the next register.
    discovery._coordinator.nikobus_command._listener.response_queue.put_nowait(
        "$0510STALE"
    )

    await discovery._scan_module_registers("4707", "100747", range(0x10, 0x14))

    # After scan completion the buffer must be empty — the give-up path
    # flushed it, and no subsequent register left remainder because every
    # other ACK was synthetic (no data frames followed).
    assert discovery._payload_buffer == ""
    # The stale queue entry was drained.
    queue = discovery._coordinator.nikobus_command._listener.response_queue
    assert queue.empty()


def test_inventory_trailer_predicate():
    from nikobus_connect.discovery.discovery import _is_inventory_trailer

    # Canonical trailer: $18 + all-F payload + 3-byte CRC.
    assert _is_inventory_trailer("$18FFFFFFFFFFFFFFBF9558") is True
    # Non-F byte inside the payload = address-inventory record, not trailer.
    assert _is_inventory_trailer("$18007407CCCCCC") is False
    # Wrong header = never a trailer.
    assert _is_inventory_trailer("$2EFFFFFFFFFFFFFFAABBCC") is False
    # Empty string.
    assert _is_inventory_trailer("") is False
    # Too short for header + CRC = no payload.
    assert _is_inventory_trailer("$18ABCDEF") is False


# --- multi-pass fast-fail ------------------------------------------------


@pytest.mark.asyncio
async def test_scan_aborts_after_consecutive_ack_give_ups(tmp_path, monkeypatch):
    """When a module ignores a function+sub combination (no ACKs arrive
    for a configurable number of consecutive registers), the scan bails
    out of the pass instead of walking all 256 registers. Without this,
    a non-responding module wastes ~13 minutes per pass at the default
    ACK timeout + retry budget."""

    # Shorten timeouts so the test completes in seconds, not minutes.
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr(const, "MODULE_SCAN_ACK_TIMEOUT", 0.01)
    monkeypatch.setattr(const, "MODULE_SCAN_DATA_TIMEOUT", 0.01)
    # Also patch the already-imported symbols in discovery.py (they're
    # imported by name, so monkey-patching const alone doesn't help).
    from nikobus_connect.discovery import discovery as disc
    monkeypatch.setattr(disc, "MODULE_SCAN_ACK_TIMEOUT", 0.01)
    monkeypatch.setattr(disc, "MODULE_SCAN_DATA_TIMEOUT", 0.01)
    monkeypatch.setattr(disc, "MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT", 5)

    discovery = _make_discovery(tmp_path)
    discovery._coordinator.discovery_module = True

    async def on_send(command: str) -> None:
        # Module never ACKs.
        return

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    await discovery._scan_module_registers(
        "0E6C", "106C0E", range(0x00, 0x100), sub_byte="00"
    )

    sent = discovery._coordinator.nikobus_command._connection.sent
    # After MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT = 5 give-ups the
    # scan aborts. Each register may retry once on ACK timeout, so
    # count distinct register bytes rather than raw sends.
    distinct_registers = {_register_from_command(c) for c in sent}
    assert distinct_registers == {0x00, 0x01, 0x02, 0x03, 0x04}, (
        f"expected abort after registers 0x00..0x04, got {distinct_registers}"
    )
    # And it must NOT have walked the whole 256-register range.
    assert 0xFF not in distinct_registers


@pytest.mark.asyncio
async def test_scan_cancels_pending_inactivity_timeout(tmp_path, monkeypatch):
    """The scan-response parser keeps rescheduling a 5 s inactivity
    timer that calls ``_finalize_discovery`` if nothing responds.
    When a new scan pass starts (e.g. pass 2 of a multi-pass scan),
    any stale timer from the previous pass must be cancelled —
    otherwise it fires mid-scan and tears down discovery while the
    current pass is still running."""

    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr(const, "MODULE_SCAN_ACK_TIMEOUT", 0.01)
    monkeypatch.setattr(const, "MODULE_SCAN_DATA_TIMEOUT", 0.01)
    from nikobus_connect.discovery import discovery as disc
    monkeypatch.setattr(disc, "MODULE_SCAN_ACK_TIMEOUT", 0.01)
    monkeypatch.setattr(disc, "MODULE_SCAN_DATA_TIMEOUT", 0.01)

    discovery = _make_discovery(tmp_path)
    discovery._coordinator.discovery_module = True

    # Simulate an armed inactivity timer from the previous pass.
    cancelled: list[bool] = []

    class FakeTask:
        def cancel(self):
            cancelled.append(True)

    discovery._timeout_task = FakeTask()

    async def on_send(command: str) -> None:
        return  # no ACK -> every register gives up

    discovery._coordinator.nikobus_command._connection.on_send = on_send

    await discovery._scan_module_registers(
        "0E6C", "106C0E", range(0x00, 0x10), sub_byte="01"
    )

    # The pre-existing timer got cancelled at scan start.
    assert cancelled == [True]
    # And cleared from the instance.
    assert discovery._timeout_task is None
