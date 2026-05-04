"""Parser for the 16-byte register-record format used by PC-Link / PC-Logic.

Reverse-engineered from Nikobus PC-software serial traces captured on
real hardware across multiple installs. Each register read on a
controller-class module (PC Link 05-200, PC Logic 05-201) returns a
single 16-byte record. There are two record types:

  - **Module registry record**: metadata about a bus module the
    controller knows about. Layout:
    ``<marker> 00 00 00 <type> 00 00 00 <addr_lo> <addr_hi> 00 00 <slot> 00 00 00``
  - **Link record**: a button-press → output-channel routing entry.
    Layout:
    ``<chan> 00 00 00 <mode> 00 00 <flag> <p0> <p1> <p2> 00 <slot> 00 00 00``

Discrimination between the two types is by structural shape, not by a
single fixed byte-0 marker. Stage 2a's 0.5.0/0.5.1 parser pinned
``byte_0 == 0x03`` for registry records based on the first install's
trace (``86F5``); a second install (``846F``) showed the marker can be
``0x04`` instead. The shape — byte 4 carrying a Module device-type
code AND bytes 8-9 holding a known module address — is install-stable
and so is what the parser keys on when ``known_module_addresses`` is
supplied.

Common to BOTH record types: bytes 1-3 are always ``0x00 0x00 0x00``.
That invariant is the cleanest way to reject the noise chunks the PC
Link emits at low-register reads (``00 01 02 03 ...`` counter dumps,
``FF FF FF 00 00 ...`` partial-empty fragments) — and it's verified
across all real records in both traces.

Stage 2a contract: parse and surface records for visibility; do NOT
synthesize ``DecodedCommand`` outputs. Stage 2b will resolve byte-0
target indices against the in-scan-buffered registry to produce real
``linked_modules`` entries — once we have multiple users' dumps to
validate the byte-0 → ``(module, channel)`` mapping against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Union

from .mapping import DEVICE_TYPES


RECORD_HEX_LEN = 32
"""16 bytes per record × 2 hex chars per byte."""

REGISTRY_MARKER = 0x03
"""Legacy byte-0 marker for registry records. Recognised as a
fast-path; the structural rule (Module device-type at byte 4 + known
address at bytes 8-9) covers every variant we've actually seen."""

_MODULE_DEVICE_TYPE_CODES: frozenset[int] = frozenset(
    int(code, 16)
    for code, info in DEVICE_TYPES.items()
    if info.get("Category") == "Module"
)
"""Device-type byte values that legitimately appear in registry
records. Buttons live in link-record payloads, not in the registry,
so a byte-4 value outside this set is a strong signal that the chunk
isn't a registry record."""


@dataclass(frozen=True, slots=True)
class ModuleRegistryRecord:
    """One known-module entry from the controller's registry table.

    The trace shows registry entries clustered in a contiguous register
    range (0xA4..0xAC on the user's PC Link), one per module on the bus.
    Every byte aligns with ``DEVICE_TYPES`` and the user's HA install,
    so this record is mostly redundant with the existing PC-Link
    inventory enumeration phase. It's surfaced anyway because it's
    cheap to log and confirms the parser's alignment.
    """

    raw_hex: str
    device_type: int           # byte 4 — matches DEVICE_TYPES keys
    address: str               # bytes 8-9, byte-swapped to bus form
    type_slot: int             # byte 12 — N-th instance of this device type


@dataclass(frozen=True, slots=True)
class LinkRecord:
    """One button → output-channel routing entry.

    Field semantics partially confirmed against the user's trace:

    - ``channel_index``: byte 0. In the trace it ranges 0x04..0x21,
      with duplicates allowed (multiple buttons can route to the same
      channel). Hypothesized to index into the controller's flat
      channel map built from the registry; resolution to a concrete
      ``(module_address, channel)`` deferred until Stage 2b.
    - ``mode_byte``: byte 4. Matches the ``SWITCH_MODE_MAPPING`` /
      ``DIMMER_MODE_MAPPING`` index ranges seen in the trace.
    - ``flag_byte``: byte 7. Observed values: ``0x80``, ``0x40``,
      ``0x00`` — likely "primary / secondary / config" indicator.
    - ``payload_bytes``: bytes 8-10. Looks like a 3-byte address-or-
      key field; exact transformation to a ``button_address`` not
      yet confirmed.
    - ``slot``: byte 12. Increments across records; appears to be the
      LOM's link-table slot index.

    Bytes 1-3, 5-6, 11, 13-15 are zero in every record observed and
    are not exposed.
    """

    raw_hex: str
    channel_index: int
    mode_byte: int
    flag_byte: int
    payload_bytes: str   # 6-char hex slice (3 bytes)
    slot: int


PcRecord = Union[ModuleRegistryRecord, LinkRecord]


def is_empty_record(chunk_hex: str) -> bool:
    """All-FF chunk = the controller marks "no record at this register"."""

    if not chunk_hex:
        return False
    chunk_hex = chunk_hex.upper()
    return all(c == "F" for c in chunk_hex)


def is_noise_chunk(chunk_hex: str) -> bool:
    """Detect chunks that aren't records but also aren't all-FF.

    Two patterns observed on real hardware:

    1. **Counter dumps.** When the PC Link's full-sweep scan reads
       low registers (0x00..0x3F on the second user's install),
       responses come back as the register-counter pattern itself —
       e.g. ``000102030405060708090A0B0C0D0E0F``,
       ``101112131415161718191A1B1C1D1E1F``. These are 16 sequential
       bytes, not records.
    2. **Partial-empty fragments.** Chunks like
       ``0000FFFFFFFFFFFFFFFFFFFFFFFFFFFF`` or
       ``FFFFFFFFFFFFFFFFFFFFFFFF00000000`` show up at scan boundaries
       on the second user's install. Not all-FF, not all-zero, not a
       record.

    Real records always have ``bytes 1-3 == 00 00 00`` (verified
    against both traces). Counter dumps fail this; partial fragments
    fail this; only true records pass. The all-zero chunk is rejected
    explicitly because its bytes 1-3 ARE zero but the record carries
    no information.
    """

    if not isinstance(chunk_hex, str):
        return False
    chunk_hex = chunk_hex.strip().upper()
    if len(chunk_hex) != RECORD_HEX_LEN:
        return False

    if all(c == "0" for c in chunk_hex):
        return True

    return chunk_hex[2:8] != "000000"


def parse_pc_record(
    chunk_hex: str,
    *,
    known_module_addresses: Iterable[str] | None = None,
) -> PcRecord | None:
    """Parse a 32-hex-char (16-byte) PC controller record.

    Returns ``None`` for chunks that are empty, noise, the wrong
    length, or otherwise unparseable.

    Discrimination between registry and link records:

    - Fast path: ``byte_0 == REGISTRY_MARKER (0x03)``. Pins the
      first-install (``86F5``) convention so existing tests still pass
      without supplying address context.
    - Structural path: when ``known_module_addresses`` is supplied,
      a chunk whose byte 4 is a Module device-type code AND whose
      bytes 8-9 (byte-swapped) match a known address parses as a
      registry record regardless of byte 0. Catches the second-install
      (``846F``) convention where the marker is ``0x04``.
    - Otherwise the chunk is treated as a link record.

    ``known_module_addresses`` should be the bus-form addresses of all
    modules in the live inventory (``coordinator.dict_module_data``).
    """

    if not isinstance(chunk_hex, str):
        return None
    chunk_hex = chunk_hex.strip().upper()
    if len(chunk_hex) != RECORD_HEX_LEN:
        return None
    if is_empty_record(chunk_hex):
        return None
    if is_noise_chunk(chunk_hex):
        return None

    try:
        marker = int(chunk_hex[0:2], 16)
    except ValueError:
        return None

    if marker == REGISTRY_MARKER:
        return _parse_registry_record(chunk_hex)

    if known_module_addresses is not None and _looks_like_registry_shape(
        chunk_hex, known_module_addresses
    ):
        return _parse_registry_record(chunk_hex)

    return _parse_link_record(chunk_hex, marker)


def _looks_like_registry_shape(
    chunk_hex: str, known_module_addresses: Iterable[str]
) -> bool:
    """Structural test: byte 4 is a Module device-type AND bytes 8-9
    byte-swapped is a known module address. Together these are strong
    enough to override the byte-0 marker check."""

    try:
        device_type = int(chunk_hex[8:10], 16)
    except ValueError:
        return False
    if device_type not in _MODULE_DEVICE_TYPE_CODES:
        return False

    address = (chunk_hex[18:20] + chunk_hex[16:18]).upper()
    known = {a.strip().upper() for a in known_module_addresses if a}
    return address in known


def _parse_registry_record(chunk_hex: str) -> ModuleRegistryRecord | None:
    """Parse a ``byte_0 == 0x03`` registry record.

    Layout (validated against trace):
        ``03 00 00 00 <type> 00 00 00 <addr_lo> <addr_hi> 00 00 <slot> 00 00 00``
    """

    try:
        device_type = int(chunk_hex[8:10], 16)
        addr_lo = chunk_hex[16:18]
        addr_hi = chunk_hex[18:20]
        type_slot = int(chunk_hex[24:26], 16)
    except ValueError:
        return None

    address = (addr_hi + addr_lo).upper()
    return ModuleRegistryRecord(
        raw_hex=chunk_hex,
        device_type=device_type,
        address=address,
        type_slot=type_slot,
    )


def _parse_link_record(chunk_hex: str, marker: int) -> LinkRecord | None:
    """Parse a non-registry, non-empty record as a link entry.

    Layout (validated against trace):
        ``<chan> 00 00 00 <mode> 00 00 <flag> <p0> <p1> <p2> 00 <slot> 00 00 00``

    A real link record carries non-FF data in at least one of the
    extracted fields. If the marker (``byte 0``) is non-FF but every
    other extracted field is 0xFF, the chunk is treated as a
    near-empty bus artefact — e.g. a stray bit-flip in an otherwise
    all-FF response, observed in practice on real hardware — and
    rejected. Without this guard, all-FF chunks with a single noise
    byte at an unparsed position synthesize phantom link records
    with ``channel_idx=0xFF mode=0xFF flag=0xFF payload=FFFFFF slot=0xFF``.
    """

    try:
        mode_byte = int(chunk_hex[8:10], 16)
        flag_byte = int(chunk_hex[14:16], 16)
        payload_bytes = chunk_hex[16:22]
        slot = int(chunk_hex[24:26], 16)
    except ValueError:
        return None

    payload_all_ff = bool(payload_bytes) and all(c == "F" for c in payload_bytes.upper())
    if (
        marker == 0xFF
        and mode_byte == 0xFF
        and flag_byte == 0xFF
        and payload_all_ff
        and slot == 0xFF
    ):
        return None

    return LinkRecord(
        raw_hex=chunk_hex,
        channel_index=marker,
        mode_byte=mode_byte,
        flag_byte=flag_byte,
        payload_bytes=payload_bytes,
        slot=slot,
    )


__all__ = [
    "RECORD_HEX_LEN",
    "REGISTRY_MARKER",
    "ModuleRegistryRecord",
    "LinkRecord",
    "PcRecord",
    "is_empty_record",
    "is_noise_chunk",
    "parse_pc_record",
]
