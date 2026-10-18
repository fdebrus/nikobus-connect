"""Optimized Nikobus API for Controlling Switches, Lights, and Covers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

from .const import COMMAND_ACK_WAIT_TIMEOUT, DEFAULT_PRESS_REPEAT
from .exceptions import NikobusError
from .protocol import (
    FUNC_GET_TIME,
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

#: Wait between a feedback key's press leaving the interface and the read
#: that checks it: the module switches within a few hundred milliseconds.
FEEDBACK_KEY_SETTLE_S: Final[float] = 0.6

# Programming-image sizes per output-module family (bytes). Switch and
# roller modules share one layout (256-byte index, 6-byte link records
# from 0x100, count at 0x6FA); dimmers carry two link banks plus a
# per-channel configuration block and are read in 8-byte blocks.
MODULE_IMAGE_SIZES: Final[dict[str, int]] = {
    "switch_module": 0x700,
    "roller_module": 0x700,
    "dimmer_module": 0xFD0,
}

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


# Bytes a module rewrites by itself, outside any programming, that its
# function-0x13 CRC still covers. A 05-008-02 dimmer stores state there
# when power drops — 0x7EE onwards one byte per channel, the level the
# channel had (12 bytes reach exactly the end of the CRC range at
# 0x7FA); 0x7E3/0x7E4 changed at a first power cut too, meaning unknown.
# Observed on a real install (Nikobus-HA, 2026-10): after two power cuts
# only these bytes differed between backups, and the module CRC moved.
MODULE_VOLATILE_RANGES: Final[dict[str, tuple[tuple[int, int], ...]]] = {
    "dimmer_module": ((0x7E3, 0x7FA),),
}


def _covered_length(module_type: str) -> int | None:
    size = MODULE_IMAGE_SIZES.get(module_type)
    if size is None:
        return None
    ranges = MODULE_CRC_RANGES.get(module_type)
    return size if ranges is None else sum(end - start for start, end in ranges)


def _covered_position(module_type: str, offset: int) -> int | None:
    """Position of image byte ``offset`` in the sequence the CRC covers."""
    ranges = MODULE_CRC_RANGES.get(module_type)
    if ranges is None:
        return offset
    position = 0
    for start, end in ranges:
        if start <= offset < end:
            return position + offset - start
        position += end - start
    return None


def predict_module_crc(
    module_type: str,
    old_crc: int,
    old_bytes: dict[int, bytes],
    new_bytes: dict[int, bytes],
) -> int | None:
    """The CRC the module would report if only these bytes had changed.

    ``old_bytes`` / ``new_bytes`` map an image offset to the bytes found
    there when ``old_crc`` was read and now. The module's CRC16 (init
    0xFFFF, polynomial 0x1021, nothing reflected) is affine over GF(2):
    for messages of one length, ``crc(a ^ d) = crc(a) ^ crc(d) ^ crc(0)``,
    so the new CRC follows from the old one and the change alone, without
    the rest of the image. A module reporting exactly this CRC changed
    those bytes and nothing else. ``None`` when the layout is unknown or
    the two maps do not describe the same bytes.
    """
    length = _covered_length(module_type)
    if length is None or set(old_bytes) != set(new_bytes):
        return None
    delta = bytearray(length)
    for offset, before in old_bytes.items():
        after = new_bytes[offset]
        if len(after) != len(before):
            return None
        for index, (x, y) in enumerate(zip(before, after, strict=True)):
            position = _covered_position(module_type, offset + index)
            if position is not None:
                delta[position] ^= x ^ y
    if not any(delta):
        return old_crc
    return old_crc ^ calc_crc1(bytes(delta).hex()) ^ calc_crc1(bytes(length).hex())


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
        #: Repeats per key press put on the bus by :meth:`press_button`.
        self.press_repeat: int = DEFAULT_PRESS_REPEAT

    def _get_channel_info(self, module_key: str, address: str, channel: int) -> dict[str, Any]:
        """Safely retrieve channel metadata and always return a dict."""
        module_data = self._module_data.get(module_key, {})
        try:
            chan = module_data.get(address, {}).get("channels", [])[channel - 1]
            return chan if chan else {}
        except (IndexError, KeyError, TypeError):
            return {}

    def press_command(self, bus_addr: str) -> str:
        """The single write that puts one key press on the bus.

        ``#N<addr>\r#E1`` repeated ``press_repeat`` times, joined in one
        command so the frames leave the interface back to back at line
        speed. Queued as separate items they would go out 150 ms apart
        (the queue's pacing), with other commands able to slip in
        between — which an impulse/toggle link can count as two presses
        ("on, then immediately off again").
        """
        try:
            repeats = max(1, int(self.press_repeat))
        except (TypeError, ValueError):
            repeats = DEFAULT_PRESS_REPEAT
        return "\r".join([f"#N{bus_addr}\r#E1"] * repeats)

    async def press_button(
        self, bus_addr: str, completion_handler: Callable[..., Any] | None = None
    ) -> None:
        """Put one key press on the bus, as a real key would (see ``press_command``)."""
        await self._command_handler.queue_command(
            self.press_command(bus_addr), completion_handler=completion_handler
        )

    async def _send_bus_command(self, bus_addr: str, completion_handler: Callable[..., Any] | None = None) -> None:
        """Helper to send a standard Nikobus bus trigger (#N...#E1)."""
        await self.press_button(bus_addr, completion_handler=completion_handler)

    async def _dispatch_action(
        self,
        module_key: str,
        address: str,
        channel: int,
        target_state: int,
        cmd_key: str,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
    ) -> None:
        """Unified dispatcher for all module actions.

        ``failure_handler`` is called with the error when the set-output
        exchange fails after its last attempt (the call itself returns as
        soon as the request is queued).
        """
        chan_info = self._get_channel_info(module_key, address, channel)
        bus_cmd = chan_info.get(cmd_key)

        if bus_cmd and module_key == "switch_module":
            await self._switch_by_key(
                address, channel, target_state != STATE_OFF, bus_cmd,
                completion_handler=completion_handler,
                failure_handler=failure_handler,
            )
            return

        try:
            if bus_cmd:
                _LOGGER.debug("Sending bus trigger for %s — %s", address, bus_cmd)
                await self._send_bus_command(bus_cmd, completion_handler)
                self._command_handler.set_bytearray_state(address, channel, target_state)
            else:
                _LOGGER.debug("Setting output state for %s chan %d to %s", address, channel, hex(target_state))
                await self._command_handler.set_output_state(
                    address, channel, target_state,
                    completion_handler=completion_handler,
                    failure_handler=failure_handler,
                )
        except NikobusError as err:
            _LOGGER.error("API action failed for %s: %s", address, err)
            raise

    # --- FEEDBACK KEYS ---

    def has_feedback_key(self, module_key: str, address: str, channel: int) -> bool:
        """Whether the channel is driven by pressing a key (``led_on`` / ``led_off``)."""
        info = self._get_channel_info(module_key, address, channel)
        return bool(info.get("led_on") or info.get("led_off"))

    async def _read_channel(self, address: str, channel: int) -> bool | None:
        """Read the channel's group from the module; ``None`` when unanswered.

        The answer is written into the state buffer, so the host shows what
        the module reports rather than what was asked for.
        """
        group = 1 if channel <= 6 else 2
        try:
            state_hex = await self._command_handler.get_output_state(address, group)
            image = bytes.fromhex((state_hex or "")[:12])
        except (NikobusError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Reading %s group %d failed: %s", address, group, err)
            return None
        if len(image) < 6:
            return None
        first = 1 if group == 1 else 7
        for offset, value in enumerate(image):
            self._command_handler.set_bytearray_state(address, first + offset, value)
        return image[(channel - 1) % 6] != STATE_OFF

    async def _switch_by_key(
        self,
        address: str,
        channel: int,
        want_on: bool,
        key: str,
        *,
        current: bool | None = None,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
    ) -> bool:
        """Bring a switch channel to ``want_on`` by pressing its feedback key.

        A feedback key keeps the plate's LED in step, which a set-output
        does not; but it is usually a toggle, so pressing it blindly can
        switch the wrong way and a lost press goes unnoticed. The channel
        is read first (unless ``current`` is given) and the key is left
        alone when the output is already where it should be; after the
        press the channel is read again, and an output that did not move
        is set directly — the output is then right, the LED may lag until
        the next real press. When the channel cannot be read, the output
        is set directly. Returns ``True`` once the module has been read,
        so the host can show the state from the buffer.
        """
        target = STATE_ON if want_on else STATE_OFF
        if current is None:
            current = await self._read_channel(address, channel)
        if current is None:
            _LOGGER.warning(
                "Module %s did not answer before pressing feedback key %s — "
                "setting channel %d directly",
                address, key, channel,
            )
            await self._command_handler.set_output_state(
                address, channel, target,
                completion_handler=completion_handler,
                failure_handler=failure_handler,
            )
            return False
        if current == want_on:
            _LOGGER.debug(
                "Module %s channel %d already %s — feedback key %s not pressed",
                address, channel, "on" if want_on else "off", key,
            )
            if completion_handler is not None:
                await completion_handler()
            return True

        loop = asyncio.get_running_loop()
        on_wire: asyncio.Future[None] = loop.create_future()

        async def _sent() -> None:
            if not on_wire.done():
                on_wire.set_result(None)

        _LOGGER.debug(
            "Pressing feedback key %s to switch %s channel %d %s",
            key, address, channel, "on" if want_on else "off",
        )
        await self.press_button(key, completion_handler=_sent)
        try:
            await asyncio.wait_for(on_wire, timeout=COMMAND_ACK_WAIT_TIMEOUT)
        except TimeoutError:
            _LOGGER.warning("Feedback key %s was not sent within %ss", key, COMMAND_ACK_WAIT_TIMEOUT)
        await asyncio.sleep(FEEDBACK_KEY_SETTLE_S)
        after = await self._read_channel(address, channel)
        if after == want_on:
            if completion_handler is not None:
                await completion_handler()
            return True
        _LOGGER.warning(
            "Feedback key %s did not switch %s channel %d %s — setting it directly; "
            "the key's LED may lag until the next press",
            key, address, channel, "on" if want_on else "off",
        )
        await self._command_handler.set_output_state(
            address, channel, target,
            completion_handler=completion_handler,
            failure_handler=failure_handler,
        )
        return after is not None

    # --- SWITCHES ---

    async def turn_on_switch(
        self,
        address: str,
        channel: int,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
    ) -> None:
        """Turn on a switch module output."""
        await self._dispatch_action("switch_module", address, channel, STATE_ON, "led_on", completion_handler, failure_handler)

    async def turn_off_switch(
        self,
        address: str,
        channel: int,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
    ) -> None:
        """Turn off a switch module output."""
        await self._dispatch_action("switch_module", address, channel, STATE_OFF, "led_off", completion_handler, failure_handler)

    async def toggle_switch(
        self,
        address: str,
        channel: int,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
    ) -> bool | None:
        """Toggle a switch module output from the state the module reports.

        A host's own idea of the state may be stale, and with a toggle key
        it decides nothing anyway: the channel is read, then switched to
        the other state — by its feedback key when it has one (verified as
        in ``turn_on_switch``), else by a set-output. Returns the state
        asked for, or ``None`` when the module could not be read; a lone
        toggle key is then pressed blind, the only press that can still
        be right.
        """
        info = self._get_channel_info("switch_module", address, channel)
        on_key, off_key = info.get("led_on"), info.get("led_off")
        current = await self._read_channel(address, channel)
        if current is None:
            if on_key and on_key == off_key:
                _LOGGER.warning(
                    "Module %s did not answer — pressing toggle key %s blind", address, on_key
                )
                await self.press_button(on_key, completion_handler=completion_handler)
            else:
                _LOGGER.warning("Module %s did not answer — toggle of channel %d skipped", address, channel)
            return None
        want_on = not current
        key = on_key if want_on else off_key
        if key:
            await self._switch_by_key(
                address, channel, want_on, key, current=current,
                completion_handler=completion_handler,
                failure_handler=failure_handler,
            )
        else:
            await self._command_handler.set_output_state(
                address, channel, STATE_ON if want_on else STATE_OFF,
                completion_handler=completion_handler,
                failure_handler=failure_handler,
            )
        return want_on

    # --- DIMMERS ---

    async def turn_on_light(
        self,
        address: str,
        channel: int,
        brightness: int,
        current_brightness: int = 0,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
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
                address, channel, brightness,
                completion_handler=completion_handler,
                failure_handler=failure_handler,
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
        failure_handler: Callable[[BaseException], Any] | None = None,
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
                address, channel, STATE_OFF,
                completion_handler=completion_handler,
                failure_handler=failure_handler,
            )
        except NikobusError as err:
            _LOGGER.error("API dimmer action failed for %s: %s", address, err)
            raise

    # --- COVERS ---

    async def open_cover(
        self,
        address: str,
        channel: int,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
    ) -> None:
        """Open a cover/roller shutter."""
        await self._dispatch_action("roller_module", address, channel, STATE_OPEN, "led_on", completion_handler, failure_handler)

    async def close_cover(
        self,
        address: str,
        channel: int,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
    ) -> None:
        """Close a cover/roller shutter."""
        await self._dispatch_action("roller_module", address, channel, STATE_CLOSE, "led_off", completion_handler, failure_handler)

    async def stop_cover(
        self,
        address: str,
        channel: int,
        direction: str,
        completion_handler: Callable[..., Any] | None = None,
        failure_handler: Callable[[BaseException], Any] | None = None,
    ) -> None:
        """Stop cover movement."""
        chan_info = self._get_channel_info("roller_module", address, channel)
        cmd_key = "led_on" if direction == "opening" else "led_off"

        try:
            if bus_cmd := chan_info.get(cmd_key):
                await self._send_bus_command(bus_cmd, completion_handler)
                self._command_handler.set_bytearray_state(address, channel, STATE_OFF)
            else:
                await self._command_handler.set_output_state(
                    address, channel, STATE_OFF, completion_handler, failure_handler
                )
        except NikobusError as err:
            _LOGGER.error("API cover action failed for %s: %s", address, err)
            raise

    def module_channel_count(self, address: str) -> int | None:
        """Channel count of a module from the caller-supplied inventory, or ``None``."""
        addr = address.upper()
        for modules in self._module_data.values():
            if not isinstance(modules, dict):
                continue
            entry = modules.get(addr) or modules.get(address)
            if isinstance(entry, dict):
                channels = entry.get("channels")
                if isinstance(channels, list):
                    return len(channels)
        return None

    async def set_output_states_for_module(
        self,
        address: str,
        completion_handler: Callable[..., Any] | None = None,
        *,
        num_channels: int | None = None,
    ) -> None:
        """Write both output groups of a module (the whole-module write).

        The module's channel count decides whether the second group is
        written; it is taken from the inventory this API was built with
        unless the caller passes ``num_channels``. See
        :meth:`NikobusCommandHandler.set_output_states` for why guessing
        it from the state buffer is not good enough.
        """
        if not num_channels:
            num_channels = self.module_channel_count(address)
        await self._command_handler.set_output_states(
            address, completion_handler=completion_handler, num_channels=num_channels
        )

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

    async def read_module_range(
        self, address: str, module_type: str, start: int, end: int
    ) -> bytes:
        """Read image bytes ``start``..``end`` (exclusive) — only the blocks holding them."""
        if module_type not in MODULE_IMAGE_SIZES:
            raise NikobusError(f"no memory image layout known for {module_type}")
        func, block_size = (
            (FUNC_READ_BLOCK8, 8) if module_type == "dimmer_module" else (FUNC_READ_BLOCK16, 16)
        )
        first, last = start // block_size, (end - 1) // block_size
        data = bytearray()
        for block in range(first, last + 1):
            payload = await self._command_handler.query(
                func, address, make_block_index_args(block)
            )
            chunk = payload[2:]
            if len(chunk) != block_size:
                raise NikobusError(
                    f"block {block} of {address}: expected {block_size} bytes, got {len(chunk)}"
                )
            data += chunk
        offset = start - first * block_size
        return bytes(data[offset : offset + end - start])

    async def read_module_volatile(self, address: str, module_type: str) -> dict[int, bytes]:
        """The bytes of ``MODULE_VOLATILE_RANGES`` as the module holds them now.

        ``{}`` for a module type without such bytes. Read-only: a few
        block reads (four for a dimmer).
        """
        return {
            start: await self.read_module_range(address, module_type, start, end)
            for start, end in MODULE_VOLATILE_RANGES.get(module_type, ())
        }

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
