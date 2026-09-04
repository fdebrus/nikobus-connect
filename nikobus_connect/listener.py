"""Nikobus Event Listener."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from .const import (
    BUTTON_COMMAND_PREFIX,
    COMMAND_PROCESSED,
    FEEDBACK_MODULE_ANSWER,
    FEEDBACK_REFRESH_COMMAND,
    MANUAL_REFRESH_COMMAND,
)
from .protocol import calc_crc2, int_to_hex

_LOGGER = logging.getLogger(__name__)

_FRAME_SPLIT_RE = re.compile(r'(?=[$#])')


class NikobusEventListener:
    """Listens to the PC-Link serial stream and dispatches decoded Nikobus frames."""

    def __init__(
        self,
        connection: Any,
        event_callback: Callable[[str], Any],
        feedback_callback: Callable[[int, str], Any] | None = None,
        has_feedback_module: bool = False,
    ) -> None:
        """Initialize the listener.

        Args:
            connection: The NikobusConnect instance.
            event_callback: Callback for general bus events (button presses, etc.).
            feedback_callback: Optional callback for feedback module answers.
            has_feedback_module: Whether a feedback module is present on the bus.
        """
        self._connection = connection
        self._event_callback = event_callback
        self._feedback_callback = feedback_callback
        self._has_feedback_module = has_feedback_module

        self._running = False
        self._listener_task: asyncio.Task[None] | None = None
        self.response_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self.on_connection_lost: Callable[[], Any] | None = None
        self._frame_buffer = ""
        self._last_query_group: dict[str, int] = {}
        self._awaiting_response: bool = False
        # Answer prefix the command layer is waiting for, while it waits.
        # A PC-Link clock reply ($1CFF...) has the frame code of an
        # output-state answer; matching it here keeps it out of the
        # feedback callback, which would file it as a phantom module.
        self._awaited_answer: str | None = None

    def reset(self) -> None:
        """Clear per-connection state after a transport reconnect.

        Drops the partial-frame buffer (bytes from the dead connection
        must not prefix frames from the new one), the query-group map,
        and any unconsumed responses. Call after ``stop()`` / before
        ``start()`` on the new connection.
        """
        self._frame_buffer = ""
        self._last_query_group.clear()
        while True:
            try:
                self.response_queue.get_nowait()
                self.response_queue.task_done()
            except asyncio.QueueEmpty:
                break

    def set_pending_query_group(self, addr: str, group: int) -> None:
        """Record which group is about to be queried for an address.

        Called by the command layer immediately before it sends a GET command
        so the feedback callback can attribute the matching response to the
        correct group.
        """
        self._last_query_group[addr] = group

    def _enqueue_response(self, message: str) -> None:
        """Add a message to the response queue, dropping the oldest if full."""
        try:
            self.response_queue.put_nowait(message)
        except asyncio.QueueFull:
            try:
                self.response_queue.get_nowait()
                self.response_queue.task_done()
            except asyncio.QueueEmpty:
                pass
            self.response_queue.put_nowait(message)
            _LOGGER.warning("Response queue was full — dropped oldest message to make room")

    @staticmethod
    async def _invoke(callback: Callable[..., Any] | None, *args: Any) -> None:
        """Call ``callback`` with ``args``, awaiting it if it's a coroutine
        function. No-op when ``callback`` is ``None``."""
        if callback is None:
            return
        if asyncio.iscoroutinefunction(callback):
            await callback(*args)
        else:
            callback(*args)

    async def start(self) -> None:
        """Start the background listening task."""
        self._running = True
        self._listener_task = asyncio.create_task(self._listen_loop())
        _LOGGER.info("Nikobus event listener started")

    async def stop(self) -> None:
        """Stop the listener."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

    async def _listen_loop(self) -> None:
        """Continuous loop to read from the Nikobus connection."""
        while self._running:
            try:
                data = await asyncio.wait_for(self._connection.read(), timeout=10)
                if not data:
                    continue

                raw_text = data.decode("Windows-1252", errors="ignore")
                for frame in self._extract_frames(raw_text):
                    _LOGGER.debug("Bus frame %s", frame)
                    await self._dispatch_message(frame)
            except TimeoutError:
                continue
            except Exception as err:
                _LOGGER.error("Listener loop failed: %s", err)
                if not self._connection.is_connected:
                    _LOGGER.warning("Connection lost — listener loop exiting")
                    self._running = False
                    await self._invoke(self.on_connection_lost)
                    break
                await asyncio.sleep(1)

    def _extract_frames(self, raw: str) -> list[str]:
        """Normalize and extract frames from serial data."""
        self._frame_buffer += raw.replace("\x02", "").replace("\x03", "").replace("\n", "\r")

        if "\r" not in self._frame_buffer:
            return []

        *frames, self._frame_buffer = self._frame_buffer.split("\r")

        extracted: list[str] = []
        for frame in frames:
            if frame := frame.strip():
                extracted.extend(f for f in _FRAME_SPLIT_RE.split(frame) if f)

        return extracted

    async def _dispatch_message(self, message: str) -> None:
        """Route messages based on frame content."""
        if not message:
            return

        # Handle button presses — dispatch to event callback and return
        if message.startswith(BUTTON_COMMAND_PREFIX):
            await self._invoke(self._event_callback, message)
            return

        # Command acknowledgments go straight to the response queue
        # but only while a caller is actively waiting for one. Otherwise
        # fire-and-forget bursts (discovery register scans, etc.) would
        # flood the 200-slot queue with ACKs nobody consumes, dropping
        # real polling responses via the "queue was full" path.
        if any(message.startswith(cmd) for cmd in COMMAND_PROCESSED):
            if self._awaiting_response:
                self._enqueue_response(message)
            await self._invoke(self._event_callback, message)
            return

        # GET-state command echoes ($1012/$1017) — track group and discard
        if any(message.startswith(r) for r in FEEDBACK_REFRESH_COMMAND):
            if self._has_feedback_module:
                gid = message[3:5]
                group = {"12": 1, "17": 2}.get(gid, 1)
                if len(message) >= 9:
                    addr = (message[7:9] + message[5:7]).upper()
                    self._last_query_group[addr] = group
            return

        # Feedback module answers ($1C)
        if message.startswith(FEEDBACK_MODULE_ANSWER):
            if self.validate_crc(message):
                if self._is_awaited_query_reply(message):
                    self._enqueue_response(message)
                    return
                if self._has_feedback_module and self._feedback_callback:
                    if len(message) >= 7:
                        addr = (message[5:7] + message[3:5]).upper()
                        group = self._last_query_group.get(addr, 1)
                        await self._invoke(self._feedback_callback, group, message)
                if self._awaiting_response:
                    self._enqueue_response(message)
            return

        # Manual refresh commands ($0512/$0517)
        if any(message.startswith(r) for r in MANUAL_REFRESH_COMMAND):
            if self.validate_crc(message):
                self._enqueue_response(message)
            return

        # Discovery frames ($18 inventory, $2E/$1E register answers).
        # CRC-validated like every other answer class below — an
        # uncaught bit error here used to sail straight through to the
        # device classifier and surface as a spurious "Unknown device
        # detected" warning (and could seed a phantom module/button in
        # storage). The 2-char frame-type code doubles as the frame's
        # declared total length (same scheme validate_crc() already
        # checks for outbound-command answers), so no new CRC logic is
        # needed here — just gating the existing check on this branch
        # too, matching FEEDBACK_MODULE_ANSWER / MANUAL_REFRESH_COMMAND.
        if message.startswith(("$18", "$2E", "$1E")):
            if self.validate_crc(message):
                # A caller waiting on a query (module status, EEPROM
                # CRC, block read) consumes the frame from the response
                # queue; discovery keeps receiving it via the callback.
                if self._awaiting_response:
                    self._enqueue_response(message)
                await self._invoke(self._event_callback, message)
            return

        # All other PC-Link responses. Only enqueue while a caller is
        # actively waiting — otherwise discovery register-scan ACKs like
        # $0522 (which fall through to this path) pile up in the 200-slot
        # response queue and drop real polling answers on the floor.
        if message.startswith("$"):
            if self._awaiting_response and (
                message.startswith("$05") or self.validate_crc(message)
            ):
                self._enqueue_response(message)
            return

        # General event callback for unhandled messages
        await self._invoke(self._event_callback, message)

    def _is_awaited_query_reply(self, message: str) -> bool:
        """Whether ``message`` is the ``FF``-prefixed reply a query waits for."""
        awaited = self._awaited_answer
        return bool(
            self._awaiting_response
            and awaited
            and awaited.startswith("$1CFF")
            and message.startswith(awaited)
        )

    def validate_crc(self, message: str) -> bool:
        """Validate the Nikobus CRC-8 for PC-Link frames."""
        while message.count("$") > 1:
            message = message[message.find("$", 1):]

        if len(message) == 5 and message.startswith("$05"):
            return True

        try:
            total_len_hex = message[1:3]
            expected_total = int(total_len_hex, 16)

            if len(message) != expected_total - 1:
                _LOGGER.error(
                    "Length mismatch — expected %d chars, got %d (frame %s)",
                    expected_total - 1, len(message), message,
                )
                return False

            payload_with_crc16 = message[:-2]
            expected_crc8 = message[-2:]
            calculated_crc8 = int_to_hex(calc_crc2(payload_with_crc16), 2)

            return calculated_crc8.upper() == expected_crc8.upper()
        except (ValueError, IndexError, AttributeError):
            return False
