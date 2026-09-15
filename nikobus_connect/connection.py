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
    PRESENCE_PROBE_IDENTITY_WAIT,
    PRESENCE_PROBE_SETTLE,
    PRESENCE_PROBE_TIMEOUT,
)
from .exceptions import NikobusConnectionError, NikobusSendError, NikobusReadError
from .protocol import family_name, parse_module_status, reply_payload

_LOGGER = logging.getLogger(__name__)


def _looks_like_nikobus_frame(text: str) -> bool:
    """A ``$``-frame with a plausible length byte, or a ``#N`` button press."""
    if text.startswith("#N") and len(text) >= 8:
        return True
    if text.startswith("$") and len(text) >= 5:
        try:
            declared = int(text[1:3], 16)
        except ValueError:
            return False
        return declared >= 5 and (len(text) == declared - 1 or len(text) == 5)
    return False


class NikobusConnect:
    """Manages the asynchronous connection (Serial or TCP) to the Nikobus PC-Link."""

    def __init__(self, connection_string: str) -> None:
        """Initialize the connection handler."""
        self._connection_string = connection_string
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._is_connected = False
        # Presence-probe verdict of the last ``connect()``: ``True`` when a
        # Nikobus device answered, ``False`` when the port opened but
        # nothing did, ``None`` before the first connect.
        self.device_answered: bool | None = None
        # Called (sync or async) when a probe that had been silent is
        # contradicted by a frame received later — see ``mark_device_answered``.
        self.on_device_answered: Callable[[], Any] | None = None
        # Identity of the gateway, when it answered the probe with its
        # own ``$18`` status frame: address (e.g. ``86F5``) and family
        # (``pc_link`` / ``feedback_module`` / ``pc_logic``).
        self.gateway_address: str | None = None
        self.gateway_family: str | None = None

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

    async def _probe(self) -> bool:
        """Check that a Nikobus device is on the other end, not just an open port.

        The handshake writes blindly; a wrong port, an unpowered PC-Link,
        a bridge with nothing behind it or a serial handle left dead by
        another program all "complete" it. The probe is the ``#A``
        identity broadcast, which the gateway answers with its own
        ``$18`` status frame (address and family) within tens of
        milliseconds — a PC-Link and a PC-Logic both do. Any other
        well-formed Nikobus frame seen meanwhile (a button press, a
        feedback frame, an ack) proves the point just as well, which
        covers a gateway whose ``#A`` behaviour is unknown (a feedback
        module used as gateway) while the bus is alive.

        Records the verdict in ``device_answered`` and returns it. Silence
        is logged as a warning, not raised: an installation whose gateway
        does not answer the probe must still come up. Callers that want a
        hard failure (a set-up wizard testing a port) check the flag.
        """
        assert self._reader is not None
        loop = asyncio.get_running_loop()
        # Let a freshly reset interface settle before the first probe
        # (a cold start swallows frames sent straight after ``ATZ``).
        await asyncio.sleep(PRESENCE_PROBE_SETTLE)
        for attempt in range(1, PRESENCE_PROBE_ATTEMPTS + 1):
            await self.send(PRESENCE_PROBE_COMMAND)
            deadline = loop.time() + PRESENCE_PROBE_TIMEOUT
            while (remaining := deadline - loop.time()) > 0:
                try:
                    data = await asyncio.wait_for(self._reader.readuntil(b"\r"), remaining)
                except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                    break
                text = data.decode("ascii", errors="ignore").strip()
                if EXPECTED_HANDSHAKE_RESPONSE in text or _looks_like_nikobus_frame(text):
                    _LOGGER.info(
                        "Nikobus device answered on %s (attempt %d): %s",
                        self._connection_string,
                        attempt,
                        text[:32],
                    )
                    self.device_answered = True
                    await self._read_gateway_identity(text)
                    return True
            _LOGGER.debug(
                "Presence probe on %s: no answer (attempt %d/%d)",
                self._connection_string,
                attempt,
                PRESENCE_PROBE_ATTEMPTS,
            )
        _LOGGER.warning(
            "%s opened but no Nikobus device answered the presence probe after %d attempts. "
            "Check the cable and the PC-Link power, make sure the Nikobus PC software is not "
            "holding the port, and if the PC-Link was used by another program, power-cycle it. "
            "Continuing: commands will time out until a device answers.",
            self._connection_string,
            PRESENCE_PROBE_ATTEMPTS,
        )
        self.device_answered = False
        return False

    async def _read_gateway_identity(self, first: str) -> None:
        """Note the gateway's address and family from its ``$18`` status.

        The null-address status query is answered by the gateway with
        its own status frame (``$18`` + address + status + family …),
        usually right behind the ``$0511`` ack. ``first`` is the frame
        that proved presence; if it is not the status itself, read a
        little longer for it. Silence here is fine: the identity is a
        bonus, not a requirement.
        """
        assert self._reader is not None
        for frame in first.split("$")[1:]:
            if self._note_identity("$" + frame):
                return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PRESENCE_PROBE_IDENTITY_WAIT
        while (remaining := deadline - loop.time()) > 0:
            try:
                data = await asyncio.wait_for(self._reader.readuntil(b"\r"), remaining)
            except Exception:  # noqa: BLE001 - best effort: the identity is a bonus
                return
            text = data.decode("ascii", errors="ignore").strip()
            for frame in text.split("$")[1:]:
                if self._note_identity("$" + frame):
                    return

    def _note_identity(self, frame: str) -> bool:
        """Record ``gateway_address`` / ``gateway_family`` from a ``$18`` frame."""
        if not frame.startswith("$18") or len(frame) < 23:
            return False
        payload = reply_payload(frame)
        if len(payload) < 7:
            return False
        try:
            status = parse_module_status(payload, payload[1:2].hex() + payload[0:1].hex())
        except ValueError:
            return False
        family = family_name(status.type_code)
        if family is None:
            return False
        self.gateway_address = status.address.upper()
        self.gateway_family = family
        _LOGGER.info(
            "Nikobus gateway on %s is a %s at %s",
            self._connection_string,
            family.replace("_", " "),
            self.gateway_address,
        )
        return True

    def mark_device_answered(self, frame: str) -> None:
        """Overturn a silent probe: a Nikobus frame arrived after all.

        The listener calls this for the first well-formed frame it sees
        while ``device_answered`` is ``False`` — a probe missed on a cold
        start (the interface still resetting) must not leave the verdict
        wrong for the life of the connection. Flips the flag, logs once
        and invokes ``on_device_answered`` (sync or async) so the caller
        can withdraw whatever it surfaced.
        """
        if self.device_answered is not False:
            return
        self.device_answered = True
        _LOGGER.info(
            "Nikobus device answered on %s after the presence probe had been silent: %s",
            self._connection_string,
            frame[:32],
        )
        callback = self.on_device_answered
        if callback is None:
            return
        try:
            result = callback()
            if inspect.isawaitable(result):
                asyncio.ensure_future(result)
        except Exception as err:  # pragma: no cover - caller's problem, keep listening
            _LOGGER.error("on_device_answered callback failed: %s", err)

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
