from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


@dataclass
class DecodedCommand:
    module_type: str
    raw_message: str
    prefix_hex: str | None = None
    chunk_hex: str | None = None
    payload_hex: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class InventoryQueryType(Enum):
    PC_LINK = "pc_link_inventory"
    MODULE = "module_inventory"


@dataclass
class InventoryResult:
    modules: list[dict[str, Any]] = field(default_factory=list)
    buttons: list[dict[str, Any]] = field(default_factory=list)


class Decoder(Protocol):
    module_type: str

    def can_handle(self, module_type: str) -> bool:
        """Return True if this decoder can process the given module type."""

    def decode(self, message: str) -> list[DecodedCommand]:
        """Decode the incoming message or chunk into structured commands."""


# Values the ``phase`` field of :class:`DiscoveryProgress` can take.
# Exposed as a string literal (not an Enum) so downstream integrations
# don't need to import the enum class to compare.
PHASE_INVENTORY = "inventory"
PHASE_IDENTITY = "identity"
PHASE_REGISTER_SCAN = "register_scan"
PHASE_FINALIZING = "finalizing"


@dataclass
class DiscoveryProgress:
    """Snapshot of the discovery pipeline's progress.

    Emitted to ``on_progress`` at phase transitions and during register
    scans. Treat it as read-only — each callback invocation receives a
    fresh instance.

    Fields
    ------
    phase
        One of ``"inventory"``, ``"identity"``, ``"register_scan"``,
        ``"finalizing"``. See the ``PHASE_*`` constants above.
    module_address
        The module currently being scanned (register_scan phase only);
        ``None`` otherwise.
    module_index
        1-based index of ``module_address`` within the register-scan
        queue. 0 outside the register_scan phase.
    module_total
        Total number of modules queued for register scanning. 0 before
        the queue is built.
    register
        Current register byte being read during a module scan;
        ``None`` between modules or outside register_scan. Since
        0.16.1 (vendor-aligned scan), this can land anywhere in the
        per-pass register list — it is NOT monotonically increasing
        from 0x10 like it used to be, because the vendor plan reads
        non-contiguous bytes (e.g. sub=00 reads 0x05..0x09 then
        skips to 0x3E).
    register_total
        Total number of registers the scan plan will read for the
        CURRENT MODULE across all passes (e.g. 48 for the vendor's
        3-pass output-module plan). 0 before the scan starts. Drops
        to the actual sent count when a ``$18`` trailer
        short-circuits the loop.
    registers_sent
        Cumulative count of registers already sent for the current
        module across all passes — the right value to compute a
        per-module percentage. Starts at 0, increments by 1 per
        register read, resets to 0 when a new module starts.
    pass_index
        1-based index of the current scan pass within the module's
        plan (e.g. 1 of 3 for the vendor plan). 0 outside scans.
    pass_total
        Total number of scan passes the plan runs for the current
        module (e.g. 3 for the vendor plan, 4 with ``broad_scan``).
        0 outside scans.
    sub_byte
        The sub-byte of the current scan pass (``"00"``, ``"01"``,
        ``"04"``). ``None`` outside scans.
    decoded_records
        Running total of successfully-decoded link records across the
        whole discovery run.
    """

    phase: str
    module_address: str | None = None
    module_index: int = 0
    module_total: int = 0
    register: int | None = None
    register_total: int = 0
    registers_sent: int = 0
    pass_index: int = 0
    pass_total: int = 0
    sub_byte: str | None = None
    decoded_records: int = 0
