"""Optimized Nikobus API for Controlling Switches, Lights, and Covers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

from .discovery.feedback_decoder import (
    FEEDBACK_IMAGE_SIZE,
    LED_MODE_TABLE_LENGTH,
    LED_MODE_TABLE_OFFSET,
    REGION_GROUP_ADDRESSES,
    REGION_INPUT_RECORDS,
    REGION_LED_LISTS,
    REGION_OUTPUT_MODULES,
)
from .exceptions import NikobusError, NikobusTimeoutError
from .protocol import (
    FUNC_GET_TIME,
    FUNC_LINK_MODE_OFF,
    FUNC_LINK_MODE_ON,
    FUNC_MODULE_CRC,
    FUNC_MODULE_STATUS,
    FUNC_READ_BLOCK8,
    FUNC_READ_BLOCK16,
    FUNC_SET_TIME,
    ModuleStatus,
    calc_crc1,
    make_block_index_args,
    make_set_time_args,
    parse_module_crc,
    parse_module_status,
    parse_pc_link_time,
)

_LOGGER = logging.getLogger(__name__)

# Nikobus state constants
STATE_OFF = 0x00
STATE_ON = 0xFF
STATE_OPEN = 0x01
STATE_CLOSE = 0x02

# Programming-image sizes per output-module family (bytes). Switch and
# roller modules share one layout (256-byte index, 6-byte link records
# from 0x100, count at 0x6FA); dimmers carry two link banks plus a
# per-channel configuration block and are read in 8-byte blocks.
MODULE_IMAGE_SIZES: Final[dict[str, int]] = {
    "switch_module": 0x700,
    "roller_module": 0x700,
    "dimmer_module": 0xFD0,
    "feedback_module": FEEDBACK_IMAGE_SIZE,
}

# Module types whose reported CRC coverage is not known; their image
# can be read and backed up but not checked against the module's CRC.
MODULE_CRC_UNKNOWN: Final[frozenset[str]] = frozenset({"feedback_module"})

_BLOCK16: Final[int] = 16
_ALL_FF_BLOCK: Final[bytes] = b"\xff" * _BLOCK16

# Byte ranges a module includes in the CRC16 it reports for function
# 0x13. Switch/roller modules cover their whole image. Dimmers cover
# both banks but skip the six bytes between them (0x7FA..0x7FF, a
# version/flags word) — validated on a real 05-007: only this coverage
# reproduces the module-reported CRC. Absent entry = whole image.
MODULE_CRC_RANGES: Final[dict[str, tuple[tuple[int, int], ...]]] = {
    "dimmer_module": ((0x000, 0x7FA), (0x800, 0xFD0)),
}


def image_crc(image: bytes, module_type: str) -> int:
    """CRC16 over the parts of ``image`` the module itself checksums."""
    ranges = MODULE_CRC_RANGES.get(module_type)
    covered = (
        image if ranges is None else b"".join(image[start:end] for start, end in ranges)
    )
    return calc_crc1(covered.hex())


class NikobusAPI:
    """Nikobus API with optimistic state updates and consolidated logic."""

    def __init__(self, command_handler: Any, module_data: dict[str, Any]) -> None:
        """Initialize the API.

        Args:
            command_handler: The NikobusCommandHandler instance.
            module_data: Dictionary containing module configuration (channels, etc.).
        """
        self._command_handler = command_handler
        self._module_data = module_data

    def _get_channel_info(self, module_key: str, address: str, channel: int) -> dict[str, Any]:
        """Safely retrieve channel metadata and always return a dict."""
        module_data = self._module_data.get(module_key, {})
        try:
            chan = module_data.get(address, {}).get("channels", [])[channel - 1]
            return chan if chan else {}
        except (IndexError, KeyError, TypeError):
            return {}

    async def _send_bus_command(self, bus_addr: str, completion_handler: Callable[..., Any] | None = None) -> None:
        """Helper to send a standard Nikobus bus trigger (#N...#E1)."""
        await self._command_handler.queue_command(
            f"#N{bus_addr}\r#E1", completion_handler=completion_handler
        )

    async def _dispatch_action(
        self,
        module_key: str,
        address: str,
        channel: int,
        target_state: int,
        cmd_key: str,
        completion_handler: Callable[..., Any] | None = None,
    ) -> None:
        """Unified dispatcher for all module actions."""
        chan_info = self._get_channel_info(module_key, address, channel)
        bus_cmd = chan_info.get(cmd_key)

        try:
            if bus_cmd:
                _LOGGER.debug("Sending bus trigger for %s — %s", address, bus_cmd)
                await self._send_bus_command(bus_cmd, completion_handler)
                self._command_handler.set_bytearray_state(address, channel, target_state)
            else:
                _LOGGER.debug("Setting output state for %s chan %d to %s", address, channel, hex(target_state))
                await self._command_handler.set_output_state(
                    address, channel, target_state, completion_handler=completion_handler
                )
        except NikobusError as err:
            _LOGGER.error("API action failed for %s: %s", address, err)
            raise

    # --- SWITCHES ---

    async def turn_on_switch(self, address: str, channel: int, completion_handler: Callable[..., Any] | None = None) -> None:
        """Turn on a switch module output."""
        await self._dispatch_action("switch_module", address, channel, STATE_ON, "led_on", completion_handler)

    async def turn_off_switch(self, address: str, channel: int, completion_handler: Callable[..., Any] | None = None) -> None:
        """Turn off a switch module output."""
        await self._dispatch_action("switch_module", address, channel, STATE_OFF, "led_off", completion_handler)

    # --- DIMMERS ---

    async def turn_on_light(
        self,
        address: str,
        channel: int,
        brightness: int,
        current_brightness: int = 0,
        completion_handler: Callable[..., Any] | None = None,
    ) -> None:
        """Turn on a dimmer output to a specific brightness.

        ``led_on`` is a bus-address broadcast that simulates a wall-button
        press; the receiving button toggles its LED on press. Firing it
        on every command (brightness changes while already on) would
        therefore flip the LED into the wrong state. Only emit the
        trigger when transitioning from off (0) to non-zero brightness.
        Callers MUST pass the actual previous bus-reflected brightness;
        leaving it at the default 0 reproduces the legacy
        "fire-every-time" behaviour for backward compat.
        """
        brightness = max(0, min(255, int(brightness)))
        chan_info = self._get_channel_info("dimmer_module", address, channel)

        try:
            if current_brightness == 0 and (led_on := chan_info.get("led_on")):
                await self._send_bus_command(led_on)

            await self._command_handler.set_output_state(
                address, channel, brightness, completion_handler=completion_handler
            )
        except NikobusError as err:
            _LOGGER.error("API dimmer action failed for %s: %s", address, err)
            raise

    async def turn_off_light(
        self,
        address: str,
        channel: int,
        current_brightness: int = 1,
        completion_handler: Callable[..., Any] | None = None,
    ) -> None:
        """Turn off a dimmer output.

        Mirrors :meth:`turn_on_light`: ``led_off`` is a toggle-on-press
        broadcast and must only fire when the light is actually
        transitioning on → off. Default ``current_brightness=1`` keeps
        legacy callers firing the trigger (matches pre-fix behaviour)
        while new callers passing 0 correctly suppress it on
        already-off → off no-ops.
        """
        chan_info = self._get_channel_info("dimmer_module", address, channel)

        try:
            if current_brightness > 0 and (led_off := chan_info.get("led_off")):
                await self._send_bus_command(led_off)

            await self._command_handler.set_output_state(
                address, channel, STATE_OFF, completion_handler=completion_handler
            )
        except NikobusError as err:
            _LOGGER.error("API dimmer action failed for %s: %s", address, err)
            raise

    # --- COVERS ---

    async def open_cover(self, address: str, channel: int, completion_handler: Callable[..., Any] | None = None) -> None:
        """Open a cover/roller shutter."""
        await self._dispatch_action("roller_module", address, channel, STATE_OPEN, "led_on", completion_handler)

    async def close_cover(self, address: str, channel: int, completion_handler: Callable[..., Any] | None = None) -> None:
        """Close a cover/roller shutter."""
        await self._dispatch_action("roller_module", address, channel, STATE_CLOSE, "led_off", completion_handler)

    async def stop_cover(self, address: str, channel: int, direction: str, completion_handler: Callable[..., Any] | None = None) -> None:
        """Stop cover movement."""
        chan_info = self._get_channel_info("roller_module", address, channel)
        cmd_key = "led_on" if direction == "opening" else "led_off"

        try:
            if bus_cmd := chan_info.get(cmd_key):
                await self._send_bus_command(bus_cmd, completion_handler)
                self._command_handler.set_bytearray_state(address, channel, STATE_OFF)
            else:
                await self._command_handler.set_output_state(address, channel, STATE_OFF, completion_handler)
        except NikobusError as err:
            _LOGGER.error("API cover action failed for %s: %s", address, err)
            raise

    async def set_output_states_for_module(self, address: str, completion_handler: Callable[..., Any] | None = None) -> None:
        """Batch update all output states for a specific module."""
        await self._command_handler.set_output_states(address, completion_handler=completion_handler)

    # --- MAINTENANCE: module status, EEPROM integrity, PC-Link clock ---

    async def get_module_status(self, address: str) -> ModuleStatus:
        """Ask a module for its status (EEPROM-error flag, type, record counts)."""
        payload = await self._command_handler.query(FUNC_MODULE_STATUS, address)
        return parse_module_status(payload, address)

    async def get_module_crc(self, address: str) -> int:
        """Return the CRC16 the module computes over its whole memory image."""
        payload = await self._command_handler.query(FUNC_MODULE_CRC, address, b"\x00")
        return parse_module_crc(payload)

    async def get_pc_link_time(self, address: str) -> datetime:
        """Read the PC-Link's clock (naive local time as the controller keeps it)."""
        payload = await self._command_handler.query(FUNC_GET_TIME, address)
        return parse_pc_link_time(payload)

    async def set_pc_link_time(self, address: str, moment: datetime) -> None:
        """Write the PC-Link's clock. ``moment`` is taken as local wall time."""
        await self._command_handler.query(
            FUNC_SET_TIME, address, make_set_time_args(moment)
        )

    async def read_module_memory(
        self,
        address: str,
        module_type: str,
        progress: Callable[[int, int], Any] | None = None,
    ) -> bytes:
        """Read a module's full programming image, block by block.

        Dimmer-class modules answer 8-byte blocks (function 0x22); every
        other output module answers 16-byte blocks (0x10). ``progress``
        is called with ``(blocks_done, blocks_total)``.
        """
        size = MODULE_IMAGE_SIZES.get(module_type)
        if size is None:
            raise NikobusError(f"no memory image layout known for {module_type}")
        if module_type == "feedback_module":
            return await self.read_feedback_image(address, progress)
        func, block_size = (
            (FUNC_READ_BLOCK8, 8) if module_type == "dimmer_module" else (FUNC_READ_BLOCK16, 16)
        )
        total = size // block_size
        image = bytearray()
        for block in range(total):
            payload = await self._command_handler.query(
                func, address, make_block_index_args(block)
            )
            data = payload[2:]  # 2-byte address echo precedes the block data
            if len(data) != block_size:
                raise NikobusError(
                    f"block {block} of {address}: expected {block_size} bytes, got {len(data)}"
                )
            image += data
            if progress is not None:
                progress(block + 1, total)
        return bytes(image)

    async def read_memory_range(
        self,
        address: str,
        start: int,
        length: int,
        *,
        stop_on_empty: bool = False,
        progress: Callable[[int, int], Any] | None = None,
    ) -> bytes:
        """Read ``length`` bytes from memory offset ``start`` in 16-byte blocks.

        ``start`` and ``length`` must be multiples of 16. With
        ``stop_on_empty`` the read ends at the first all-``FF`` block,
        which is how ``FF``-terminated tables are bounded; the returned
        bytes are then shorter than ``length``.
        """
        if start % _BLOCK16 or length % _BLOCK16:
            raise ValueError("start and length must be multiples of 16")
        total = length // _BLOCK16
        first = start // _BLOCK16
        image = bytearray()
        for index in range(total):
            payload = await self._command_handler.query(
                FUNC_READ_BLOCK16, address, make_block_index_args(first + index)
            )
            data = payload[2:]
            if len(data) != _BLOCK16:
                raise NikobusError(
                    f"block {first + index} of {address}: expected {_BLOCK16} bytes, got {len(data)}"
                )
            image += data
            if progress is not None:
                progress(index + 1, total)
            if stop_on_empty and data == _ALL_FF_BLOCK:
                break
        return bytes(image)

    async def set_link_mode(self, address: str, enabled: bool) -> None:
        """Put a module in, or take it out of, link (programming) mode.

        Acknowledged only. Link mode by itself changes no programming:
        clearing and writing memory are separate functions.
        """
        await self._command_handler.query(
            FUNC_LINK_MODE_ON if enabled else FUNC_LINK_MODE_OFF, address
        )

    async def read_feedback_image(
        self,
        address: str,
        progress: Callable[[int, int], Any] | None = None,
        *,
        link_mode: bool = True,
    ) -> bytes:
        """Read the programmed parts of a feedback module (05-207) image.

        The module's memory is 0x7900 bytes, most of it unused ``FF``
        space. The two tables that are ``FF``-terminated (input-event
        records, LED lists) are read until their first empty block, the
        fixed tables (tracked output modules, group addresses, LED
        modes) in full. The result is a ``FEEDBACK_IMAGE_SIZE`` image
        with ``FF`` in the parts that were not read.

        A feedback module answers its status query but ignores block
        reads in normal operation. With ``link_mode`` the read is
        retried in link mode when the first block goes unanswered, the
        way the module is programmed; link mode is left again in every
        case.
        """
        try:
            return await self._read_feedback_regions(address, progress)
        except NikobusTimeoutError:
            if not link_mode:
                raise
            _LOGGER.info(
                "Feedback module %s ignores block reads; retrying in link mode", address
            )
        await self.set_link_mode(address, True)
        try:
            return await self._read_feedback_regions(address, progress)
        finally:
            await self.set_link_mode(address, False)

    async def _read_feedback_regions(
        self, address: str, progress: Callable[[int, int], Any] | None
    ) -> bytes:
        image = bytearray(b"\xff" * FEEDBACK_IMAGE_SIZE)
        fixed = (
            REGION_OUTPUT_MODULES,
            REGION_GROUP_ADDRESSES,
            (LED_MODE_TABLE_OFFSET, LED_MODE_TABLE_LENGTH),
        )
        bounded = (REGION_LED_LISTS, REGION_INPUT_RECORDS)
        # Progress counts the fixed regions exactly and the bounded ones
        # by what they actually return.
        done = 0
        total = sum(length // _BLOCK16 for _, length in fixed)

        def _step(_current: int, _total: int) -> None:
            nonlocal done
            done += 1
            if progress is not None:
                progress(done, max(total, done))

        for start, length in fixed:
            data = await self.read_memory_range(address, start, length, progress=_step)
            image[start : start + len(data)] = data
        for start, length in bounded:
            data = await self.read_memory_range(
                address, start, length, stop_on_empty=True, progress=_step
            )
            image[start : start + len(data)] = data
        return bytes(image)

    async def verify_module_memory(
        self, address: str, module_type: str, image: bytes | None = None
    ) -> tuple[bool, int, int]:
        """Compare the module's own image CRC with a CRC computed locally.

        Returns ``(matches, module_crc, computed_crc)``. ``image`` may be
        passed when it was just read (backup), otherwise it is read now.
        """
        if image is None:
            image = await self.read_module_memory(address, module_type)
        computed = image_crc(image, module_type)
        reported = await self.get_module_crc(address)
        return reported == computed, reported, computed
