"""Nikobus Command Handler."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .const import (
    COMMAND_EXECUTION_DELAY,
    COMMAND_ACK_WAIT_TIMEOUT,
    COMMAND_ANSWER_WAIT_TIMEOUT,
    COMMAND_POST_ACK_ANSWER_TIMEOUT,
    MAX_ATTEMPTS,
)
from .exceptions import NikobusError, NikobusSendError, NikobusTimeoutError
from .protocol import make_pc_link_command, calculate_group_number

_LOGGER = logging.getLogger(__name__)


class NikobusCommandHandler:
    """Handles command processing for Nikobus."""

    def __init__(
        self,
        connection: Any,
        listener: Any,
        module_states: dict[str, bytearray] | None = None,
    ) -> None:
        """Initialize the command handler.

        Args:
            connection: The NikobusConnect instance.
            listener: The NikobusEventListener instance.
            module_states: Optional shared state buffer for module outputs.
        """
        self._connection = connection
        self._listener = listener
        self._module_states: dict[str, bytearray] = module_states if module_states is not None else {}

        self._running: bool = False
        self._command_task: asyncio.Task[None] | None = None
        self._command_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._pending_get_futures: dict[str, asyncio.Future[str]] = {}
        self._queued_get_keys: set[str] = set()

    async def start(self) -> None:
        """Start the command processing loop."""
        self._running = True
        self._command_task = asyncio.create_task(self._process_commands())

    async def stop(self) -> None:
        """Stop the command processing loop."""
        self._running = False
        for future in list(self._pending_get_futures.values()):
            if not future.done():
                future.cancel()
        self._pending_get_futures.clear()
        self._queued_get_keys.clear()
        if self._command_task:
            self._command_task.cancel()
            try:
                await self._command_task
            except asyncio.CancelledError:
                _LOGGER.info("Command processing task was cancelled")
            self._command_task = None

    async def _process_commands(self) -> None:
        """Process commands from the queue."""
        _LOGGER.info("Nikobus command processing starting")
        while self._running:
            try:
                command_item = await self._command_queue.get()
                command = command_item["command"]
                address = command_item.get("address")
                future: asyncio.Future[str] | None = command_item.get("future")
                completion_handler: Callable[[], Awaitable[None]] | None = (
                    command_item.get("completion_handler")
                )

                # Bug-2 fix (0.5.20): if the caller's future was
                # cancelled before processing (e.g., its outer
                # deadline fired while we were blocked behind a
                # slow probe ahead in the queue), skip the wire
                # send entirely. ``get_output_state``'s except
                # handler has already cleared the dedup key, so a
                # retry that re-queued is now in the queue and
                # will be processed in due course.
                if future is not None and future.cancelled():
                    gid_skip = command[3:5] if len(command) >= 5 else ""
                    if gid_skip in ("12", "17") and address:
                        self._queued_get_keys.discard(
                            f"{address.upper()}_{'1' if gid_skip == '12' else '2'}"
                        )
                    _LOGGER.debug(
                        "Skipping stale command %s — caller future cancelled",
                        command,
                    )
                    self._command_queue.task_done()
                    continue

                _LOGGER.debug("Processing command %s with address %s", command, address)

                gid = command[3:5] if len(command) >= 5 else ""
                if gid in ("12", "17") and address:
                    self._queued_get_keys.discard(
                        f"{address.upper()}_{'1' if gid == '12' else '2'}"
                    )

                try:
                    if not address:
                        await self._send_command(command)
                        if completion_handler and callable(completion_handler):
                            res = completion_handler()
                            if inspect.isawaitable(res):
                                await res
                    else:
                        result = await self._send_command_get_answer(command, address)
                        if future and not future.done():
                            future.set_result(result)
                        if completion_handler and callable(completion_handler):
                            res = completion_handler()
                            if inspect.isawaitable(res):
                                await res
                except Exception as err:
                    _LOGGER.exception("Failed to process command %s", command)
                    if future and not future.done():
                        future.set_exception(err)
                finally:
                    self._command_queue.task_done()

                await asyncio.sleep(COMMAND_EXECUTION_DELAY)
            except Exception:
                _LOGGER.exception("Command processing loop failed")

    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Update the internal state buffer for a module channel."""
        addr = address.upper()
        if addr not in self._module_states:
            self._module_states[addr] = bytearray(12)
        idx = channel - 1
        if 0 <= idx < 12:
            self._module_states[addr][idx] = value

    def get_bytearray_group_state(self, address: str, group: int) -> bytearray:
        """Return a copy of the 6-byte state for a specific group."""
        addr = address.upper()
        state = self._module_states.get(addr, bytearray(12))
        start = 0 if group == 1 else 6
        return bytearray(state[start:start + 6])

    async def get_output_state(self, address: str, group: int) -> str:
        """Get the output state of a module.

        Waits ``COMMAND_ACK_WAIT_TIMEOUT`` for the response future
        (set by the queue processor when ``_send_command_get_answer``
        returns or raises). The command pipeline runs up to
        ``MAX_ATTEMPTS`` wire-level retries internally; callers that
        want a probe-style "is this module on the bus?" semantic
        should rely on that retry budget rather than wrapping this
        call in their own ``asyncio.wait_for`` — that pattern races
        the queue's dedup mechanism and is the root cause of the
        Nikobus-HA #319 IKIKN trace where real modules were being
        misclassified as absent.
        """
        _LOGGER.debug("Getting output state — address %s, group %s", address, group)
        command_code = 0x12 if int(group) == 1 else 0x17
        command = make_pc_link_command(command_code, address)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        key = f"{address.upper()}_{group}"
        self._pending_get_futures[key] = future
        # Dedup key used by ``queue_command`` — mirror the layout
        # ('_1' for group 1, '_2' for group 2). Tracked locally so
        # we can clear it on cancel and let the next call re-queue
        # without being suppressed.
        dedup_key = f"{address.upper()}_{'1' if int(group) == 1 else '2'}"
        try:
            await self.queue_command(command, address, future=future)
            return await asyncio.wait_for(future, timeout=COMMAND_ACK_WAIT_TIMEOUT)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            if not future.done():
                future.cancel()
            # Clear dedup so the next call for this address can
            # re-queue. Any stale command still in the queue gets
            # discarded by ``_process_commands`` on pop via the
            # ``future.cancelled()`` check.
            self._queued_get_keys.discard(dedup_key)
            raise
        finally:
            self._pending_get_futures.pop(key, None)

    def resolve_pending_get(self, address: str, group: int, state: str) -> None:
        """Resolve a pending get_output_state future directly from a feedback callback."""
        key = f"{address.upper()}_{group}"
        future = self._pending_get_futures.get(key)
        if future and not future.done():
            _LOGGER.debug(
                "Feedback fast-path: resolving pending GET for %s group %s", address, group
            )
            future.set_result(state)

    async def set_output_state(
        self,
        address: str,
        channel: int,
        value: int,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Set a single channel state and queue the command."""
        _LOGGER.debug(
            "Setting output state - Address: %s, Channel: %d, Value: %d",
            address, channel, value
        )
        group = calculate_group_number(channel)

        self.set_bytearray_state(address, channel, value)
        current_bytes = self.get_bytearray_group_state(address, group)

        cmd_code = 0x15 if group == 1 else 0x16
        payload = current_bytes[:6] + bytearray([0xFF])

        command = make_pc_link_command(cmd_code, address, payload)

        await self.queue_command(
            command, address, completion_handler=completion_handler
        )
        _LOGGER.debug("Command successfully queued for module %s, channel %d", address, channel)

    async def set_output_states(
        self,
        address: str,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Prepare and queue the output states for a module."""
        _LOGGER.debug("Preparing to set output states for module %s", address)
        addr = address.upper()
        state = self._module_states.get(addr)
        if state is None:
            _LOGGER.warning("Cannot set output states — module %s not in state buffer", address)
            return

        has_second_group = len(state) > 6 and any(b != 0 for b in state[6:12])
        channel_states = state[:6] + bytearray([0xFF])
        await self.queue_command(
            make_pc_link_command(0x15, address, channel_states),
            address,
            completion_handler=None if has_second_group else completion_handler,
        )

        if has_second_group:
            channel_states = state[6:12] + bytearray([0xFF])
            await self.queue_command(
                make_pc_link_command(0x16, address, channel_states),
                address,
                completion_handler=completion_handler,
            )

    async def queue_command(
        self,
        command: str,
        address: str | None = None,
        future: asyncio.Future[str] | None = None,
        completion_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Queue a command for processing."""
        _LOGGER.debug("Queueing command %s", command)

        gid = command[3:5] if len(command) >= 5 else ""
        if gid in ("12", "17") and address:
            dedup_key = f"{address.upper()}_{'1' if gid == '12' else '2'}"
            if dedup_key in self._queued_get_keys:
                _LOGGER.debug(
                    "Suppressing duplicate GET for %s (already queued)", dedup_key
                )
                return
            self._queued_get_keys.add(dedup_key)

        command_item = {
            "command": command,
            "address": address,
            "future": future,
            "completion_handler": completion_handler,
        }
        await self._command_queue.put(command_item)
        _LOGGER.debug("Command queued %s", command)

    def drain_queue(self) -> int:
        """Drain all pending commands from the queue.

        Used by discovery to abort remaining register reads after early
        termination, and by :meth:`reset` after a reconnect. Any queued
        command carrying a caller future gets that future cancelled so
        the awaiter is released instead of hanging until its own
        timeout. Returns the number of discarded commands.
        """
        count = 0
        while not self._command_queue.empty():
            try:
                item = self._command_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            future = item.get("future")
            if future is not None and not future.done():
                future.cancel()
            self._command_queue.task_done()
            count += 1
        return count

    def reset(self) -> None:
        """Clear per-connection state after a transport reconnect.

        Drains the queue (cancelling queued caller futures) and clears
        the GET-dedup key set — commands queued against the dead
        connection must not block their re-issue on the new one. The
        processing loop itself keeps running; only the backlog is
        dropped.
        """
        discarded = self.drain_queue()
        self._queued_get_keys.clear()
        if discarded:
            _LOGGER.info(
                "Command handler reset: %d stale command(s) discarded", discarded
            )

    async def _send_command(self, command: str) -> None:
        """Send a command to the Nikobus system."""
        _LOGGER.debug("Sending command %s", command)
        try:
            await self._connection.send(command)
        except NikobusError:
            _LOGGER.exception("Failed to send command %s", command)
            raise

    async def _send_command_get_answer(self, command: str, address: str) -> str:
        """Send a command and wait for an answer from the Nikobus system."""
        _LOGGER.debug(
            "Sending command %s to address %s, waiting for answer", command, address
        )
        wait_ack, wait_answer = self._prepare_ack_and_answer_signals(command, address)

        gid = command[3:5] if len(command) >= 5 else ""
        if gid in ("12", "17"):
            self._listener.set_pending_query_group(
                address.upper(), 1 if gid == "12" else 2
            )
        state = await self._wait_for_ack_and_answer(command, wait_ack, wait_answer)
        if state is None:
            raise NikobusTimeoutError(
                f"Failed to receive state for command '{command}' after {MAX_ATTEMPTS} attempts."
            )
        return state

    def _prepare_ack_and_answer_signals(
        self, command: str, address: str
    ) -> tuple[str, str]:
        """Prepare the acknowledgment and answer signals based on the command prefix."""
        command_prefix = command[:3]
        command_part = command[3:5]
        ack_signal = f"$05{command_part}"

        prefix_mapping = {
            "$1E": "$0EFF",
            "$05": "$1C",
            "$10": "$1C",
        }
        answer_prefix = prefix_mapping.get(command_prefix, "$1C")
        answer_signal = f"{answer_prefix}{address[2:]}{address[:2]}"

        _LOGGER.debug(
            "Prepared signals: ACK=%s, ANSWER=%s, COMMAND=%s, ADDRESS=%s",
            ack_signal, answer_signal, command, address,
        )
        return ack_signal, answer_signal

    async def _wait_for_ack_and_answer(
        self, command: str, wait_ack: str, wait_answer: str
    ) -> str | None:
        """Wait for an acknowledgment and answer with retries."""
        self._listener._awaiting_response = True
        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                while not self._listener.response_queue.empty():
                    try:
                        self._listener.response_queue.get_nowait()
                        self._listener.response_queue.task_done()
                    except asyncio.QueueEmpty:
                        break
                try:
                    await self._connection.send(command)
                    _LOGGER.debug(
                        "Attempt %d/%d waiting for ACK %s, answer %s",
                        attempt, MAX_ATTEMPTS, wait_ack, wait_answer,
                    )
                    state = await self._wait_for_ack_and_answer_state(wait_ack, wait_answer)
                    if state is not None:
                        _LOGGER.debug("Received valid state from device")
                        return state
                except (NikobusSendError, NikobusTimeoutError) as err:
                    _LOGGER.warning("Attempt %d failed: %s", attempt, err, exc_info=True)
                    if attempt == MAX_ATTEMPTS:
                        raise
                except Exception as err:
                    _LOGGER.exception("Unhandled exception on attempt %d", attempt)
                    if attempt == MAX_ATTEMPTS:
                        raise NikobusError(f"Unhandled exception: {err}") from err
            raise NikobusTimeoutError(
                f"Failed to receive ACK and state for command '{command}' after {MAX_ATTEMPTS} attempts."
            )
        finally:
            self._listener._awaiting_response = False

    async def _wait_for_ack_and_answer_state(
        self, wait_ack: str, wait_answer: str
    ) -> str | None:
        """Wait for both acknowledgment and answer signals, then extract the state."""
        ack_received = False
        answer_received = False
        state: str | None = None
        loop = asyncio.get_running_loop()
        end_time = loop.time() + COMMAND_ACK_WAIT_TIMEOUT

        while loop.time() < end_time:
            try:
                remaining = end_time - loop.time()
                per_msg_timeout = (
                    COMMAND_POST_ACK_ANSWER_TIMEOUT if ack_received
                    else COMMAND_ANSWER_WAIT_TIMEOUT
                )
                message = await asyncio.wait_for(
                    self._listener.response_queue.get(),
                    timeout=min(per_msg_timeout, remaining),
                )
                self._listener.response_queue.task_done()
                _LOGGER.debug("Message received %s", message)
                if wait_ack in message:
                    _LOGGER.debug("ACK received")
                    ack_received = True
                if wait_answer in message:
                    if wait_answer.startswith("$0EFF"):
                        _LOGGER.debug("Answer received (set-command ack)")
                        state = ""
                        answer_received = True
                    elif len(message) >= len(wait_answer) + 2 + 12:
                        _LOGGER.debug("Answer received")
                        state = self._parse_state_from_message(message, wait_answer)
                        answer_received = True
                    else:
                        _LOGGER.debug(
                            "Ignoring short get-response — len %d, need >= %d, message %s",
                            len(message), len(wait_answer) + 2 + 12, message,
                        )
                if ack_received and answer_received:
                    return state
            except asyncio.TimeoutError:
                _LOGGER.debug("Timeout while waiting for ACK/answer")
                break
            except Exception as err:
                _LOGGER.exception("Failed while waiting for messages")
                raise NikobusError(f"Error while waiting for messages: {err}") from err

        return None

    def _parse_state_from_message(self, message: str, answer_signal: str) -> str:
        """Parse and return the state from a received message."""
        idx = message.find(answer_signal)
        if idx == -1:
            _LOGGER.warning("Answer signal %s not found in message %s", answer_signal, message)
            return ""
        state_index = idx + len(answer_signal) + 2
        state = message[state_index:state_index + 12]
        if len(state) < 12:
            _LOGGER.warning(
                "State data truncated (%d/12 chars) in message %s", len(state), message
            )
            return ""
        return state
