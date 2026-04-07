"""Nikobus Event Listener."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Optional

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
        feedback_callback: Optional[Callable[[int, str], Any]] = None,
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
        self._listener_task: asyncio.Task | None = None
        self.response_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self.on_connection_lost: Callable[[], Any] | None = None
        self._frame_buffer = ""
        self._last_query_group: dict[str, int] = {}
        self._awaiting_response: bool = False

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

    async def start(self) -> None:
        """Start the background listening task."""
        self._running = True
        self._listener_task = asyncio.create_task(self._listen_loop())
        _LOGGER.info("Nikobus Event Listener started.")

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
                    _LOGGER.debug("Bus Frame: %s", frame)
                    await self._dispatch_message(frame)
            except asyncio.TimeoutError:
                continue
            except Exception as err:
                _LOGGER.error("Listener loop error: %s", err)
                if not self._connection.is_connected:
                    _LOGGER.warning("Connection lost — listener loop exiting.")
                    self._running = False
                    if self.on_connection_lost:
                        if asyncio.iscoroutinefunction(self.on_connection_lost):
                            await self.on_connection_lost()
                        else:
                            self.on_connection_lost()
                    break
                await asyncio.sleep(1)

    def _extract_frames(self, raw: str) -> list[str]:
        """Normalize and extract frames from serial data."""
        self._frame_buffer += raw.replace("\x02", "").replace("\x03", "").replace("\n", "\r")

        if "\r" not in self._frame_buffer:
            return []

        *frames, self._frame_buffer = self._frame_buffer.split("\r")

        extracted = []
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
            if asyncio.iscoroutinefunction(self._event_callback):
                await self._event_callback(message)
            else:
                self._event_callback(message)
            return

        # Command acknowledgments go straight to the response queue
        if any(message.startswith(cmd) for cmd in COMMAND_PROCESSED):
            self._enqueue_response(message)
            if asyncio.iscoroutinefunction(self._event_callback):
                await self._event_callback(message)
            else:
                self._event_callback(message)
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
                if self._has_feedback_module and self._feedback_callback:
                    if len(message) >= 7:
                        addr = (message[5:7] + message[3:5]).upper()
                        group = self._last_query_group.get(addr, 1)
                        if asyncio.iscoroutinefunction(self._feedback_callback):
                            await self._feedback_callback(group, message)
                        else:
                            self._feedback_callback(group, message)
                if self._awaiting_response:
                    self._enqueue_response(message)
            return

        # Manual refresh commands ($0512/$0517)
        if any(message.startswith(r) for r in MANUAL_REFRESH_COMMAND):
            if self.validate_crc(message):
                self._enqueue_response(message)
            return

        # Discovery frames ($18 inventory, $2E/$1E register answers)
        # Forward to event callback so the integration can route them.
        if message.startswith(("$18", "$2E", "$1E")):
            if asyncio.iscoroutinefunction(self._event_callback):
                await self._event_callback(message)
            else:
                self._event_callback(message)
            return

        # All other PC-Link responses
        if message.startswith("$"):
            if message.startswith("$05") or self.validate_crc(message):
                self._enqueue_response(message)
            return

        # General event callback for unhandled messages
        if asyncio.iscoroutinefunction(self._event_callback):
            await self._event_callback(message)
        else:
            self._event_callback(message)

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
                    "Length mismatch: expected %d chars, got %d (frame: %s)",
                    expected_total - 1, len(message), message,
                )
                return False

            payload_with_crc16 = message[:-2]
            expected_crc8 = message[-2:]
            calculated_crc8 = int_to_hex(calc_crc2(payload_with_crc16), 2)

            return calculated_crc8.upper() == expected_crc8.upper()
        except (ValueError, IndexError, AttributeError):
            return False
