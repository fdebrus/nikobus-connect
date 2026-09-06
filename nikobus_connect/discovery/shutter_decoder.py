"""Deterministic roller shutter decoder."""

from __future__ import annotations

import logging
from typing import Any

from ..coordinator_protocol import CoordinatorProtocol
from .chunk_decoder import BaseChunkingDecoder
from .mapping import ROLLER_MODE_MAPPING, ROLLER_T3_MAPPING, ROLLER_TIMER_MAPPING
from .protocol import (
    _format_channel,
    _is_all_ff,
    _safe_int,
    get_button_address,
    get_push_button_address,
)

_LOGGER = logging.getLogger(__name__)


def _timer_value(
    t1_raw: int | None, mode_raw: int | None = None
) -> tuple[str | None, str | None]:
    """Resolve the T1 nibble to its human-readable label.

    Mode 0x05 (M06 "Open with operating time") and mode 0x06 (M07 "Close
    with operating time") use ``ROLLER_T3_MAPPING`` — a paired
    ``long-press / short-press`` duration. All other roller modes
    (M01/M02/M03/M05) use ``ROLLER_TIMER_MAPPING`` — a single operating
    time.
    """
    if t1_raw is None:
        return None, None
    if mode_raw == 0x03:
        # M04 "Stop" takes no parameter (vendor LinkModeBase: KP 1); the
        # nibble carries nothing meaningful, so don't label it.
        return None, None
    if mode_raw in (0x05, 0x06):
        return ROLLER_T3_MAPPING.get(t1_raw), None
    timer_entry = ROLLER_TIMER_MAPPING.get(t1_raw)
    return (timer_entry[0], None) if timer_entry else (None, None)


def decode(payload_hex: str, raw_bytes: list[str], context: Any) -> dict[str, Any] | None:
    """Decode a shutter payload following the deterministic selector rule."""

    if _is_all_ff(payload_hex, 12):
        _LOGGER.debug(
            "Skipped roller module %s — empty slot, payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    if len(raw_bytes) != 6:
        _LOGGER.debug(
            "Skipped roller module %s — invalid length, payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    t2_raw = _safe_int(raw_bytes[0][1])
    key_raw = _safe_int(raw_bytes[1][0])
    channel_raw = _safe_int(raw_bytes[1][1])
    t1_raw = _safe_int(raw_bytes[2][0])
    mode_raw = _safe_int(raw_bytes[2][1])

    if key_raw is None or channel_raw is None or mode_raw is None:
        _LOGGER.debug(
            "Skipped roller module %s — invalid payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    if mode_raw not in ROLLER_MODE_MAPPING:
        _LOGGER.debug(
            "Skipped roller module %s — unknown mode, payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    channel_decoded = (channel_raw // 2) + 1
    channel_count = context.module_channel_count
    if channel_count is not None and not (1 <= channel_decoded <= channel_count):
        _LOGGER.debug(
            "Skipped roller module %s — invalid channel, payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    button_address = get_button_address(payload_hex[-6:])
    coord_get_channels = (
        context.coordinator.get_button_channels
        if context.coordinator is not None
        else None
    )
    push_button_address, normalized_button = get_push_button_address(
        key_raw,
        button_address,
        coord_get_channels,
    )

    t1_val, t2_val = _timer_value(t1_raw, mode_raw)

    decoded = {
        "payload": payload_hex,
        "button_address": normalized_button,
        "push_button_address": push_button_address,
        "key_raw": key_raw,
        "channel_raw": channel_raw,
        "channel": channel_decoded,
        "mode_raw": mode_raw,
        "t1_raw": t1_raw,
        "t2_raw": t2_raw,
        "K": key_raw,
        "C": _format_channel(channel_decoded),
        "T1": t1_val,
        "T2": t2_val,
        "M": ROLLER_MODE_MAPPING.get(mode_raw),
        # 0.5.22: see ``switch_decoder.decode`` for the source-label
        # rationale. Roller reads its own link table directly →
        # ``output_module_table``.
        "record_source": "output_module_table",
    }

    _LOGGER.debug(
        "Decoded roller module %s — button %s, key %s, channel %s, mode %s",
        context.module_address,
        normalized_button,
        key_raw,
        decoded["channel"],
        decoded["M"],
    )

    return decoded


class ShutterDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator: CoordinatorProtocol | None) -> None:
        super().__init__(coordinator, "roller_module")


__all__ = ["ShutterDecoder", "decode"]
