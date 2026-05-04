"""Deterministic dimmer module decoder."""

from __future__ import annotations

import logging
from typing import Any

from .chunk_decoder import BaseChunkingDecoder
from .mapping import DIMMER_MODE_MAPPING, DIMMER_TIMER_MAPPING
from .protocol import (
    _format_channel,
    _is_all_ff,
    _is_garbage_chunk,
    _safe_int,
    get_button_address,
    get_push_button_address,
    is_known_button_canonical,
)

_LOGGER = logging.getLogger(__name__)

EXPECTED_CHUNK_LEN = 16


def _timer_value(mode_raw: int | None, t1_raw: int | None) -> tuple[str | None, str | None]:
    """Return timer/preset values for dimmer modules based on mode and raw T1."""

    if mode_raw is None or t1_raw is None:
        return None, None

    timer_entry = DIMMER_TIMER_MAPPING.get(t1_raw)
    if timer_entry is None:
        return None, None

    # Preset level (voltage) for preset modes
    if mode_raw in (8, 9):  # M11 Preset on/off, M12 Preset on
        return timer_entry[0], None

    # Time value for timed modes
    if mode_raw in (4, 5, 6, 7):  # M05-M08
        return timer_entry[2], None

    return None, None


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Decode a dimmer payload using fixed offsets (no heuristics)."""

    if _is_all_ff(payload_hex, EXPECTED_CHUNK_LEN):
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=empty_slot payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    if _is_garbage_chunk(payload_hex):
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=garbage_chunk payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    if len(raw_bytes) != 8:
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=invalid_length payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    key_raw = _safe_int(raw_bytes[3][0])
    channel_raw = _safe_int(raw_bytes[3][1])
    t1_raw = _safe_int(raw_bytes[4][0])
    mode_raw = _safe_int(raw_bytes[4][1])

    if None in (key_raw, channel_raw, mode_raw):
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=invalid_payload payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    if mode_raw not in DIMMER_MODE_MAPPING:
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=unknown_mode payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    channel_decoded = channel_raw + 1
    channel_count = context.module_channel_count
    if channel_count is not None and not (1 <= channel_decoded <= channel_count):
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=invalid_channel payload=%s",
            context.module_address,
            payload_hex,
        )
        return None

    button_address = get_button_address(payload_hex[-6:])
    coord_get_channels = getattr(context.coordinator, "get_button_channels", None)
    if not is_known_button_canonical(button_address, coord_get_channels):
        _LOGGER.debug(
            "Discovery skipped | type=dimmer module=%s reason=unknown_button "
            "payload=%s button_address=%s",
            context.module_address,
            payload_hex,
            button_address,
        )
        return None
    push_button_address, normalized_button = get_push_button_address(
        key_raw,
        button_address,
        coord_get_channels,
    )

    t1_val, t2_val = _timer_value(mode_raw, t1_raw)

    decoded = {
        "payload": payload_hex,
        "button_address": normalized_button,
        "push_button_address": push_button_address,
        "key_raw": key_raw,
        "channel_raw": channel_raw,
        "channel": channel_decoded,
        "mode_raw": mode_raw,
        "t1_raw": t1_raw,
        "t2_raw": None,
        "K": key_raw,
        "C": _format_channel(channel_decoded),
        "T1": t1_val,
        "T2": t2_val,
        "M": DIMMER_MODE_MAPPING.get(mode_raw),
    }

    _LOGGER.debug(
        "Discovery decoded | type=dimmer module=%s button=%s key=%s channel=%s mode=%s",
        context.module_address,
        normalized_button,
        key_raw,
        decoded["channel"],
        decoded["M"],
    )

    return decoded


class DimmerDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator):
        super().__init__(coordinator, "dimmer_module")


__all__ = ["DimmerDecoder", "decode", "EXPECTED_CHUNK_LEN"]
