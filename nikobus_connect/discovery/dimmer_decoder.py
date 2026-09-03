"""Deterministic dimmer module decoder."""

from __future__ import annotations

import logging
from typing import Any

from ..coordinator_protocol import CoordinatorProtocol
from .chunk_decoder import BaseChunkingDecoder
from .mapping import (
    DIMMER_MODE_MAPPING,
    DIMMER_MODE_T1_LOOKUP,
    DIMMER_T2_RAMP,
)
from .protocol import (
    _format_channel,
    _is_all_ff,
    _safe_int,
    get_button_address,
    get_push_button_address,
)

_LOGGER = logging.getLogger(__name__)

EXPECTED_CHUNK_LEN = 16


def _timer_value(
    mode_raw: int | None, t1_raw: int | None, t2_raw: int | None = None
) -> tuple[str | None, str | None]:
    """Return per-mode T1 and T2 labels for a dimmer record.

    Niko's product database has a distinct T1 parameter table per dimmer
    mode (preset level for M11/M12, push time for M05/M06, delayed-off
    duration for M07, on/off step config for M01/M02/M03). Dispatching
    via ``DIMMER_MODE_T1_LOOKUP`` is the authoritative resolution. T2 is
    the ramp/fade time (``DIMMER_T2_RAMP``) used by every dimmer mode.

    ``t2_raw`` is the low nibble of the record's third byte (the ramp
    parameter every dimmer mode carries); ``None`` when the caller
    could not read it.
    """

    if mode_raw is None:
        return None, None

    t1_val: str | None = None
    t1_table = DIMMER_MODE_T1_LOOKUP.get(mode_raw)
    if t1_table is not None and t1_raw is not None and 0 <= t1_raw < len(t1_table):
        t1_val = t1_table[t1_raw]

    t2_val: str | None = None
    if t2_raw is not None and 0 <= t2_raw < len(DIMMER_T2_RAMP):
        t2_val = DIMMER_T2_RAMP[t2_raw]

    return t1_val, t2_val


def decode(payload_hex: str, raw_bytes: list[str], context: Any) -> dict[str, Any] | None:
    """Decode a dimmer payload using fixed offsets (no heuristics)."""

    if _is_all_ff(payload_hex, EXPECTED_CHUNK_LEN):
        _LOGGER.debug(
            "Skipped dimmer module %s — empty slot, payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    if len(raw_bytes) != 8:
        _LOGGER.debug(
            "Skipped dimmer module %s — invalid length, payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    key_raw = _safe_int(raw_bytes[3][0])
    channel_raw = _safe_int(raw_bytes[3][1])
    t1_raw = _safe_int(raw_bytes[4][0])
    mode_raw = _safe_int(raw_bytes[4][1])
    # Ramp/fade time (T2): low nibble of the third byte. Bytes 0-1 are
    # reserved padding on every dimmer record observed so far.
    t2_raw = _safe_int(raw_bytes[2][1])

    if key_raw is None or channel_raw is None or mode_raw is None:
        _LOGGER.debug(
            "Skipped dimmer module %s — invalid payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    if mode_raw not in DIMMER_MODE_MAPPING:
        _LOGGER.debug(
            "Skipped dimmer module %s — unknown mode, payload %s",
            context.module_address,
            payload_hex,
        )
        return None

    channel_decoded = channel_raw + 1
    channel_count = context.module_channel_count
    if channel_count is not None and not (1 <= channel_decoded <= channel_count):
        _LOGGER.debug(
            "Skipped dimmer module %s — invalid channel, payload %s",
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

    t1_val, t2_val = _timer_value(mode_raw, t1_raw, t2_raw)

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
        "M": DIMMER_MODE_MAPPING.get(mode_raw),
        # 0.5.22: see ``switch_decoder.decode`` for the source-label
        # rationale. Dimmer reads its own link table directly →
        # ``output_module_table``.
        "record_source": "output_module_table",
    }

    _LOGGER.debug(
        "Decoded dimmer module %s — button %s, key %s, channel %s, mode %s",
        context.module_address,
        normalized_button,
        key_raw,
        decoded["channel"],
        decoded["M"],
    )

    return decoded


class DimmerDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator: CoordinatorProtocol | None) -> None:
        super().__init__(coordinator, "dimmer_module")


__all__ = ["EXPECTED_CHUNK_LEN", "DimmerDecoder", "decode"]
