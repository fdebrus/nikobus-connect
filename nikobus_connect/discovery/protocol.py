"""Lightweight helpers and routing for Nikobus discovery decoding."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from .mapping import CHANNEL_MAPPING, KEY_MAPPING_MODULE

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DecoderContext:
    """Shared context passed to all decoder functions."""

    coordinator: Any
    module_address: str | None
    module_channel_count: int | None


def reverse_hex(hex_str: str) -> str:
    """Reverse the bytes in a hex string and return as upper-case hex."""

    b = bytes.fromhex(hex_str)
    reversed_b = b[::-1]
    return reversed_b.hex().upper()


def normalize_payload(payload_hex: str) -> list[str] | None:
    """Normalize payload into a list of hex byte strings."""

    try:
        payload_bytes = bytes.fromhex(payload_hex)
    except ValueError:
        _LOGGER.error("Invalid payload hex: %s", payload_hex)
        return None

    return [f"{byte:02X}" for byte in payload_bytes]


def _is_all_ff(payload_hex: str, expected_length: int | None = None) -> bool:
    """Return True when the payload is entirely the filler value 0xFF."""

    normalized = payload_hex.upper()
    if expected_length is not None and len(normalized) != expected_length:
        return False
    return bool(normalized) and set(normalized) == {"F"}


def _is_garbage_chunk(payload_hex: str) -> bool:
    """Return True when a chunk looks like flash garbage rather than a real entry.

    Applied as a secondary filter after the all-0xFF empty-slot check.
    Catches chunks where:
      - One byte value dominates the chunk (>= 70% of bytes)
      - Filler bytes (0x00 + 0xFF combined) dominate the chunk (>= 70%)

    Real button link entries have varied bytes (the first 3 bytes encode a
    button address, followed by key/channel/mode/timer fields), so neither
    condition triggers on legitimate data observed in the wild. It does
    catch leftover flash patterns like ``F3F3F3F3F3F3F3F3``, ``0000000000FF0000``
    or ``FFFFDC0000FF`` that otherwise pass the mode/channel sanity checks
    and decode to phantom button addresses.
    """

    try:
        raw = bytes.fromhex(payload_hex or "")
    except ValueError:
        return False

    n = len(raw)
    if n < 4:
        return False

    counts: dict[int, int] = {}
    for byte in raw:
        counts[byte] = counts.get(byte, 0) + 1

    # Rule 1: one byte value covers >= 70% of the chunk
    max_count = max(counts.values())
    if max_count * 10 >= n * 7:
        return True

    # Rule 2: filler bytes (0x00 and 0xFF) cover >= 70% of the chunk
    filler = counts.get(0x00, 0) + counts.get(0xFF, 0)
    if filler * 10 >= n * 7:
        return True

    return False


def _safe_int(hex_byte: str | None) -> int | None:
    """Safely convert a two-character hex byte to int."""

    if hex_byte is None:
        return None
    try:
        return int(hex_byte, 16)
    except (TypeError, ValueError):
        return None


def _format_channel(channel_number: int | None) -> str | None:
    """Return a consistent channel label for discovery logs."""

    if channel_number is None or channel_number <= 0:
        return None

    index = channel_number - 1
    return CHANNEL_MAPPING.get(index, f"Channel {channel_number}")


def classify_device_type(device_type_hex: str, device_types: dict) -> dict:
    """Return device metadata for the given device type."""

    normalized_type = (device_type_hex or "").strip().upper()
    return device_types.get(
        normalized_type,
        {"Category": "Unknown", "Name": "Unknown", "Model": "N/A", "Channels": 0},
    )


def convert_nikobus_address(address_string: str) -> str:
    """Convert a hex address string to a Nikobus address."""

    try:
        address = int(address_string, 16)
        if address < 0 or address > 0xFFFFFF:
            return f"[{address_string}]"
        nikobus_address = 0
        for i in range(21):
            nikobus_address = (nikobus_address << 1) | ((address >> i) & 1)
        nikobus_address <<= 1
        button = (address >> 21) & 0x07
        final_address = nikobus_address + button
        return f"{final_address:06X}"
    except ValueError:
        return f"[{address_string}]"


def is_known_button_canonical(
    button_address: str | None,
    coordinator_get_button_channels,
) -> bool:
    """Return ``True`` when ``button_address`` belongs to a known button.

    A canonical button address decoded from a register chunk is
    "known" when it appears in the live button inventory directly,
    or when it's the +1 sibling of an 8-channel button — half the
    keys of an 8-ch button (raw indices 4-7) decode to ``inventory_addr + 1``
    rather than the inventory address itself, and ``_build_bus_to_op_index``
    aliases that case at merge time.

    Used by the per-module decoders as a phantom-rejection gate:
    chunks whose address bytes happen to land on routing/cell-prefix
    bytes (rather than a real button-link record's address bytes)
    decode to canonical addresses that match no inventory entry. Without
    this gate they reach the merge layer, get logged as ``unmatched``,
    and bloat the per-scan log without ever contributing a real entry.

    Returns ``True`` when the lookup is unavailable (no coordinator,
    no button channel API) — discovery should still produce records
    in test harnesses and bare-metal scenarios that don't supply a
    coordinator.
    """

    if not button_address or coordinator_get_button_channels is None:
        return True

    try:
        if coordinator_get_button_channels(button_address) is not None:
            return True
        # 8-ch +1 alias: inventory keys 4-7 decode to canonical+1.
        try:
            sibling = f"{(int(button_address, 16) - 1) & 0xFFFFFF:06X}"
        except (TypeError, ValueError):
            return True
        return coordinator_get_button_channels(sibling) == 8
    except Exception:  # pragma: no cover - defensive
        _LOGGER.debug(
            "is_known_button_canonical: coordinator lookup raised for %s",
            button_address,
            exc_info=True,
        )
        return True


def get_button_address(payload_hex: str) -> str | None:
    """Convert the 3-byte payload suffix into a button address."""

    try:
        bin_str = format(int(payload_hex, 16), "024b")
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.error("Error converting button address to binary: %s", err)
        return None

    modified = bin_str[:4] + bin_str[4:6] + bin_str[8:]
    group1 = modified[:6]
    group2 = modified[6:14]
    group3 = modified[14:]
    new_bin = group3 + group2 + group1
    try:
        result_int = int(new_bin, 2)
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.error("Error converting binary to int: %s", err)
        return None
    return format(result_int, "06X")


def get_push_button_address(
    key_index: int | None,
    button_address: str | None,
    coordinator_get_button_channels,
    convert_func: Callable[[str], str] = convert_nikobus_address,
):
    """Return the derived push button address when possible."""

    if key_index is None or button_address is None:
        return None, button_address

    num_channels = None
    if coordinator_get_button_channels:
        try:
            num_channels = coordinator_get_button_channels(button_address)
        except Exception:  # pragma: no cover - defensive
            _LOGGER.debug("get_button_channels failed for %s", button_address, exc_info=True)
            num_channels = None

    if num_channels is None or num_channels not in KEY_MAPPING_MODULE:
        return None, button_address

    mapping = KEY_MAPPING_MODULE[num_channels]
    if key_index not in mapping:
        return None, button_address

    push_button_address = convert_func(button_address)
    add_value = int(mapping[key_index], 16)
    try:
        original_nibble = int(push_button_address[0], 16)
    except (ValueError, IndexError):
        _LOGGER.debug("Failed to extract nibble from push_button_address: %s", push_button_address, exc_info=True)
        return None, button_address

    new_nibble_value = (original_nibble + add_value) & 0xF
    new_nibble_hex = f"{new_nibble_value:X}"
    final_push_button_address = new_nibble_hex + push_button_address[1:]

    return final_push_button_address, button_address


def decode_command_payload(
    payload_hex: str,
    module_type: str,
    coordinator,
    *,
    module_address: str | None = None,
    reverse_before_decode: bool = False,
    raw_chunk_hex: str | None = None,
    module_channel_count: int | None = None,
):
    """Decode a command payload using the module-specific decoder."""

    payload_hex = (payload_hex or "").strip().upper()
    raw_input = raw_chunk_hex or payload_hex

    if reverse_before_decode:
        payload_hex = reverse_hex(payload_hex)

    raw_bytes = normalize_payload(payload_hex)
    if raw_bytes is None:
        return None

    resolved_channel_count: int | None = module_channel_count
    if coordinator and module_address and resolved_channel_count is None:
        try:
            resolved_channel_count = coordinator.get_module_channel_count(module_address)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug(
                "Module channel lookup failed | module=%s error=%s", module_address, err
            )

    context = DecoderContext(
        coordinator=coordinator,
        module_address=module_address,
        module_channel_count=resolved_channel_count,
    )

    if module_type == "switch_module":
        from . import switch_decoder as decoder_module
    elif module_type == "roller_module":
        from . import shutter_decoder as decoder_module
    elif module_type == "dimmer_module":
        from . import dimmer_decoder as decoder_module
    elif module_type == "pc_logic":
        from . import pc_logic_decoder as decoder_module
    elif module_type == "pc_link":
        from . import pc_link_decoder as decoder_module
    else:
        decoder_module = None

    decoder = getattr(decoder_module, "decode", None) if decoder_module else None
    if decoder is None:
        _LOGGER.error("Unknown module_type '%s' for payload %s", module_type, raw_input)
        return None

    try:
        return decoder(payload_hex, raw_bytes, context)
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.error(
            "Decoder error | type=%s module=%s payload=%s error=%s",
            module_type,
            module_address,
            payload_hex,
            err,
        )
        return None


__all__ = [
    "DecoderContext",
    "decode_command_payload",
    "normalize_payload",
    "reverse_hex",
    "convert_nikobus_address",
    "classify_device_type",
    "get_button_address",
    "get_push_button_address",
    "is_known_button_canonical",
    "_format_channel",
    "_is_all_ff",
    "_is_garbage_chunk",
    "_safe_int",
]
