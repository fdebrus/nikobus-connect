"""PC-Logic (05-201) module decoder — Stage 1 instrumentation.

PC-Logic carries up to five BP units, each a 4x16 grid of programmable
cells. Each cell links a button-press input reference to an output
module/channel via a connection mode (M01..M17) plus T1/T2 timer
fields. Heavily PC-Logic-routed installs end up with output-module
flash records that reference PC-Logic-synthesized addresses; without
walking PC-Logic memory those addresses can't be resolved and
``linked_modules`` stays empty across the board.

This module is intentionally a logging-only stub for the 0.4.11
release: we register PC-Logic as a scan target so the engine reads
its register memory, and we log every chunk at INFO so users can
attach the dump to GitHub issues without enabling component-level
debug. The real per-cell field decoder lands in Stage 2 once we have
real bytes to align against the PC-software BP-cell screenshots.
"""

from __future__ import annotations

import logging
from typing import Any

from .chunk_decoder import BaseChunkingDecoder

_LOGGER = logging.getLogger(__name__)


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Log raw PC-Logic chunks and return ``None``.

    Stage 1 contract: never produces a decoded record, never feeds the
    merge layer. Just surfaces the bytes so we can design Stage 2.
    """

    _LOGGER.info(
        "PC-Logic chunk | module=%s payload=%s",
        getattr(context, "module_address", None),
        payload_hex,
    )
    return None


class PcLogicDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator):
        super().__init__(coordinator, "pc_logic")


__all__ = ["PcLogicDecoder", "decode"]
