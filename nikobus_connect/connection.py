"""Nikobus Connection Handler."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

import serial_asyncio

from .const import (
    COMMANDS_HANDSHAKE,
    EXPECTED_HANDSHAKE_RESPONSE,
    PRESENCE_PROBE_ATTEMPTS,
    PRESENCE_PROBE_COMMAND,
    PRESENCE_PROBE_TIMEOUT,
)
from .exceptions import NikobusConnectionError, NikobusSendError, NikobusReadError

_LOGGER = logging.getLogger(__name__)


class NikobusConnect:
    """Manages the asynchronous connection (Serial or TCP) to the Nikobus PC-Link."""

    def __init__(self, connection_string: str) -> None:
        """Initialize the connection handler."""
        self._connection_string = connection_string
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        """Return True if the connection is active."""
        return self._is_connected

    async def connect(self) -> None:
        """Establish the connection."""
        _LOGGER.debug("Attempting to connect to Nikobus: %s", self._connection_string)

        try:
            if ":" in self._connection_string and not self._connection_string.startswith("/"):
                host, port = self._connection_string.split(":", 1)
                self._reader, self._writer = await asyncio.open_connection(host, int(port))
            else:
                self._reader, self._writer = await serial_asyncio.open_serial_connection(
                    url=self._connection_string,
                    baudrate=9600,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False
                )

            self._is_connected = True
            _LOGGER.info("Connected to Nikobus on %s", self._connection_string)
            try:
                await self._handshake()
                await self._probe()
            except Exception:
                await self.disconnect()
                raise

        except (OSError, asyncio.TimeoutError) as err:
            self._is_connected = False
            _LOGGER.error("Failed to connect to %s: %s", self._connection_string, err)
            raise NikobusConnectionError(f"Connection failed: {err}") from err

    async def _handshake(self) -> None:
        """Perform the full modem init + handshake sequence once after connecting."""
        _LOGGER.debug("Starting Nikobus handshake")
        try:
            for cmd in COMMANDS_HANDSHAKE:
                await self.send(cmd)
                await asyncio.sleep(0.2)
            _LOGGER.info("Nikobus handshake completed successfully")
        except Exception as err:
            _LOGGER.error("Handshake failed: %s", err)
            raise NikobusConnectionError(f"Handshake failed: {err}") from err

    async def _probe(self) -> None:
        """Prove a Nikobus device is on the other end, not just an open port.

        The handshake writes blindly; a wrong port, an unpowered PC-Link,
        a bridge with nothing behind it or a serial handle left dead by
        another program all "complete" it. The presence probe (a status
        query to the null address) is acknowledged with ``$0511`` by the
        PC-Link and by a feedback module used as gateway. The interface
        holds that acknowledgement until the next ``$`` frame it
        receives, so each attempt sends the probe twice and reads after
        the second. Raises ``NikobusConnectionError`` when nothing
        answers within ``PRESENCE_PROBE_ATTEMPTS`` attempts.
        """
        assert self._reader is not None
        loop = asyncio.get_running_loop()
        for attempt in range(1, PRESENCE_PROBE_ATTEMPTS + 1):
            await self.send(PRESENCE_PROBE_COMMAND)
            await asyncio.sleep(0.2)
            await self.send(PRESENCE_PROBE_COMMAND)
            deadline = loop.time() + PRESENCE_PROBE_TIMEOUT
            while (remaining := deadline - loop.time()) > 0:
                try:
                    data = await asyncio.wait_for(self._reader.readuntil(b"\r"), remaining)
                except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                    break
                text = data.decode("ascii", errors="ignore")
                if EXPECTED_HANDSHAKE_RESPONSE in text:
                    _LOGGER.info(
                        "Nikobus device answered the presence probe on %s (attempt %d)",
                        self._connection_string,
                        attempt,
                    )
                    return
                _LOGGER.debug("Presence probe: ignoring %r while waiting for %s", text.strip(), EXPECTED_HANDSHAKE_RESPONSE)
            _LOGGER.warning(
                "No Nikobus device answered the presence probe on %s (attempt %d/%d)",
                self._connection_string,
                attempt,
                PRESENCE_PROBE_ATTEMPTS,
            )
        raise NikobusConnectionError(
            f"{self._connection_string} opened but no Nikobus device answered the presence "
            f"probe after {PRESENCE_PROBE_ATTEMPTS} attempts. Check the cable and the PC-Link "
            "power, make sure the Nikobus PC software is not holding the port, and if the "
            "PC-Link was used by another program, power-cycle it."
        )

    async def reconnect_with_backoff(
        self,
        *,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        on_attempt: Callable[[int, float], Any] | None = None,
    ) -> int:
        """Reconnect with exponential backoff until ``connect()`` succeeds.

        Loops ``connect()`` (transport + handshake) forever, sleeping
        ``initial_delay`` doubled per failure up to ``max_delay``, and
        returns the number of attempts the successful connect took.
        Cancellation propagates — callers stop the loop by cancelling
        the task that awaits this coroutine.

        ``on_attempt(attempt, delay)`` — sync or async — is invoked
        before each try so callers can surface progress (log lines,
        availability updates) without owning the loop.
        """
        attempt = 0
        delay = initial_delay
        while True:
            attempt += 1
            if on_attempt is not None:
                result = on_attempt(attempt, delay)
                if inspect.isawaitable(result):
                    await result
            try:
                await self.connect()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning(
                    "Reconnect attempt %d failed: %s — retrying in %.0fs",
                    attempt,
                    err,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
                continue
            _LOGGER.info("Reconnected to Nikobus after %d attempt(s)", attempt)
            return attempt

    async def disconnect(self) -> None:
        """Close the connection and cleanup resources."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as err:
                _LOGGER.debug("Failed to close connection: %s", err)

        self._reader = None
        self._writer = None
        self._is_connected = False
        _LOGGER.info("Nikobus connection closed")

    async def ping(self) -> bool:
        """Verify the PC-Link is responsive by sending an #E1 command."""
        if not self._is_connected:
            await self.connect()

        try:
            await self.send("#E1")
            _LOGGER.debug("Nikobus ping (#E1) successful")
            return True
        except Exception as err:
            _LOGGER.error("Nikobus ping failed: %s", err)
            raise NikobusConnectionError(f"Hardware not responding: {err}") from err

    async def send(self, command: str) -> None:
        """Send a command string to the bus with thread-safe locking."""
        if not self._is_connected or not self._writer:
            raise NikobusConnectionError("Cannot send: Not connected.")

        async with self._lock:
            try:
                payload = command.strip() + "\r"
                data = payload.encode("ascii")
                self._writer.write(data)
                await self._writer.drain()
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.error("Write failed: %s", err)
                await self.disconnect()
                raise NikobusSendError(f"Write error: {err}") from err

    async def read(self) -> bytes:
        """Read a single frame (CR-terminated) from the bus."""
        if not self._is_connected or not self._reader:
            raise NikobusConnectionError("Cannot read: Not connected.")

        try:
            data = await self._reader.readuntil(b'\r')
            return data
        except asyncio.LimitOverrunError as err:
            _LOGGER.error("Read buffer overrun — disconnecting")
            await self.disconnect()
            raise NikobusReadError("Buffer overrun") from err
        except (OSError, asyncio.IncompleteReadError) as err:
            _LOGGER.error("Read failed: %s", err)
            await self.disconnect()
            raise NikobusReadError(f"Read error: {err}") from err
