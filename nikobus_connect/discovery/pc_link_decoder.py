"""PC Link (05-200) decoder.

The PC Link is the bus controller — the device that bridges the
USB-serial port to the Nikobus bus. Its register memory stores both
a module registry (what the install looks like) and the link table
(button → output-channel routing). The Nikobus PC software walks
that memory at every "Read configuration" operation.

Stage 1 left ``pc_link`` excluded from the scan-queue + chunking
layer entirely. Stage 2a (0.5.0) included it in both, parsed the
16-byte records into structured form, and surfaced them at INFO so
users could attach the dump to GitHub issues without enabling
component-level debug. Stage 2b (0.5.1) added the byte-0 →
``(target_module, channel)`` resolver but kept the decoder in
visibility-only mode: ``decode_chunk`` returned ``[]`` so the merge
layer didn't ingest PC-Link records.

Stage 2c (0.5.10): with the resolver validated against the
fdebrus install, ``decode_chunk`` now emits ``DecodedCommand``s for
link records that resolve to an output-bearing target. The metadata
carries the **target** module address as the override
``add_to_command_mapping`` honours, so a PC-Link link record adds an
entry to the source button's ``linked_modules`` pointing at the real
output module — not at the PC-Link itself. Registry records remain
visibility-only (their inventory equivalent already populates
``module_data`` via the inventory phase).

The function-level ``decode()`` hook stays return-``None`` because
it's a one-shot path with no registry buffer — without registry
context the resolver can't run.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import DecodedCommand
from .chunk_decoder import BaseChunkingDecoder
from .pc_record_parser import (
    LinkRecord,
    ModuleRegistryRecord,
    RegistryBuffer,
    is_empty_record,
    is_noise_chunk,
    link_record_to_decoded_metadata,
    parse_pc_record,
    resolve_link_target,
)

_LOGGER = logging.getLogger(__name__)
_LOG_PREFIX = "PC-Link"


def _known_module_addresses(coordinator) -> set[str]:
    """Collect bus-form addresses of every module in the live inventory.

    Used by ``parse_pc_record`` to identify registry records by shape
    (Module device-type + known address) when the byte-0 marker varies
    by firmware. Returns an empty set when the coordinator is missing
    or has no inventory yet — the parser falls back to the legacy
    byte-0 == 0x03 fast path in that case.
    """

    if coordinator is None:
        return set()
    buckets = getattr(coordinator, "dict_module_data", None) or {}
    addresses: set[str] = set()
    for module_map in buckets.values():
        if isinstance(module_map, dict):
            addresses.update(addr.upper() for addr in module_map if addr)
    return addresses


def decode(payload_hex: str, raw_bytes: list[str], context) -> dict[str, Any] | None:
    """Module-level decoder hook used by ``decode_command_payload``.

    The chunking layer routes through this for any chunk it produces
    when ``module_type=pc_link``. We log a structured INFO line per
    record (or a DEBUG line for empty / unparseable chunks) and return
    ``None`` — the merge layer must not see these records yet.

    This entry point is one-shot and doesn't carry a registry buffer,
    so the Stage 2b channel-target resolver can't run here. Use the
    ``PcLinkDecoder`` class for scans that should resolve link
    targets — the per-instance buffer accumulates registry records
    across chunks within a scan.
    """

    return _log_record(
        payload_hex,
        getattr(context, "module_address", None),
        coordinator=getattr(context, "coordinator", None),
    )


class PcLinkDecoder(BaseChunkingDecoder):
    """PC-Link variant of the chunk-based decoder pipeline.

    Overrides ``decode_chunk`` to bypass the switch/dimmer/roller
    ``reverse_before_decode`` flag — PC-Link records are stored in a
    fixed on-wire byte order that the parser consumes directly.

    Holds a per-instance ``RegistryBuffer`` that accumulates
    ``ModuleRegistryRecord`` entries seen during a scan. Once the
    registry is populated, link records' byte 0 is resolved against
    a flat output-channel map built from the coordinator's
    ``get_module_channel_count`` lookups, and the resolved
    ``(target_module_address, channel)`` is logged at INFO and (since
    Stage 2c, 0.5.10) emitted as a ``DecodedCommand`` whose metadata
    carries the resolved target as the ``module_address`` override
    ``add_to_command_mapping`` consumes.
    """

    def __init__(self, coordinator):
        super().__init__(coordinator, "pc_link")
        self._registry = RegistryBuffer()

    def reset_registry(self) -> None:
        """Clear the registry buffer between scans."""

        self._registry.reset()

    def reset_scan_buffers(self) -> None:
        """Clear per-scan state. Extends the base alt-alignment reset
        with the registry reset so a fresh scan starts with no carried
        registry state."""

        super().reset_scan_buffers()
        self._registry.reset()

    def decode_chunk(self, chunk, module_address=None):
        chunk = chunk.strip().upper()
        addr = module_address or self._module_address
        return _decode_and_log(
            chunk,
            addr,
            coordinator=self._coordinator,
            prefix=_LOG_PREFIX,
            registry=self._registry,
            module_type=self.module_type,
        )


def _log_record(
    chunk_hex: str,
    module_address: str | None,
    *,
    coordinator=None,
    prefix: str = _LOG_PREFIX,
    registry: RegistryBuffer | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any] | None:
    """Function-level hook used by ``decode_command_payload``.

    One-shot path: no registry buffer is plumbed through, so the
    resolver can't run here. We log the record and return ``None``;
    callers wanting decoded ``DecodedCommand`` outputs go through the
    class-based ``PcLinkDecoder`` / ``PcLogicDecoder`` which carries
    a per-scan registry.
    """

    _decode_and_log(
        chunk_hex,
        module_address,
        coordinator=coordinator,
        prefix=prefix,
        registry=registry,
        module_type=None,
        logger=logger,
    )
    return None


def _decode_and_log(
    chunk_hex: str,
    module_address: str | None,
    *,
    coordinator,
    prefix: str,
    registry: RegistryBuffer | None,
    module_type: str | None,
    logger: logging.Logger | None = None,
) -> list[DecodedCommand]:
    """Parse, log, and return a list of ``DecodedCommand``s for the
    merge layer.

    Registry records accumulate into ``registry`` (when supplied) and
    return an empty list — they're metadata, not links. Link records
    return a single ``DecodedCommand`` when the resolver succeeds AND
    ``module_type`` is supplied (the class-based path); otherwise
    they're logged but not emitted.
    """

    commands: list[DecodedCommand] = []
    log = logger or _LOGGER
    chunk_hex = (chunk_hex or "").strip().upper()

    if is_empty_record(chunk_hex):
        log.debug(
            "%s empty record | module=%s payload=%s",
            prefix,
            module_address,
            chunk_hex,
        )
        return commands

    if is_noise_chunk(chunk_hex):
        log.debug(
            "%s noise chunk | module=%s payload=%s",
            prefix,
            module_address,
            chunk_hex,
        )
        return commands

    record = parse_pc_record(
        chunk_hex,
        known_module_addresses=_known_module_addresses(coordinator),
    )
    if record is None:
        log.debug(
            "%s unparseable chunk | module=%s payload=%s",
            prefix,
            module_address,
            chunk_hex,
        )
        return commands

    if isinstance(record, ModuleRegistryRecord):
        if registry is not None:
            registry.add(record)
        log.info(
            "%s module-registry record | module=%s device_type=0x%02X "
            "address=%s type_slot=%d raw=%s",
            prefix,
            module_address,
            record.device_type,
            record.address,
            record.type_slot,
            record.raw_hex,
        )
        return commands

    if isinstance(record, LinkRecord):
        log.info(
            "%s link record | module=%s channel_idx=0x%02X mode=0x%02X "
            "flag=0x%02X payload=%s slot=0x%02X raw=%s",
            prefix,
            module_address,
            record.channel_index,
            record.mode_byte,
            record.flag_byte,
            record.payload_bytes,
            record.slot,
            record.raw_hex,
        )

        if registry is None or coordinator is None:
            return commands

        target = resolve_link_target(
            record.channel_index, registry, coordinator
        )
        if target is None:
            log.debug(
                "%s link target | module=%s channel_idx=0x%02X "
                "resolved=None (out of flat-map range or empty registry)",
                prefix,
                module_address,
                record.channel_index,
            )
            return commands

        target_address, target_channel = target
        log.info(
            "%s link target | module=%s channel_idx=0x%02X "
            "resolved=%s ch=%d",
            prefix,
            module_address,
            record.channel_index,
            target_address,
            target_channel,
        )

        if module_type is None:
            # Function-level path — log only, no command emission.
            return commands

        metadata = link_record_to_decoded_metadata(
            record, registry, coordinator
        )
        if metadata is None:
            log.debug(
                "%s link skipped | module=%s channel_idx=0x%02X "
                "reason=metadata_resolution_failed (mode/key/source unknown)",
                prefix,
                module_address,
                record.channel_index,
            )
            return commands

        commands.append(
            DecodedCommand(
                module_type=module_type,
                raw_message=chunk_hex,
                chunk_hex=chunk_hex,
                payload_hex=record.raw_hex,
                metadata=metadata,
            )
        )

    return commands


__all__ = ["PcLinkDecoder", "decode"]
