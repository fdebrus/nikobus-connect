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

from .mapping import (
    DEVICE_TYPES,
    DIMMER_MODE_MAPPING,
    KEY_MAPPING_MODULE,
    ROLLER_MODE_MAPPING,
    SWITCH_MODE_MAPPING,
)


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


OUTPUT_BEARING_DEVICE_TYPES: frozenset[int] = frozenset({
    0x01,  # Switch Module (12 ch)
    0x02,  # Roller Shutter Module (6 ch)
    0x03,  # Dimmer Module (12 ch)
    0x09,  # Compact Switch Module (4 ch)
    0x31,  # Compact Switch Module variant (4 ch)
    0x32,  # Compact Dim Controller (4 ch)
})
"""Device-type bytes whose modules drive load outputs and therefore
participate in the PC-Link flat channel map indexed by link records'
byte 0.

Excluded Module device types and why:

- ``0x08`` (PC Logic), ``0x0A`` (PC Link self) — controllers, no load.
- ``0x2B`` (Audio Distribution) — 0 channels in DEVICE_TYPES, treated as no
  output.
- ``0x37`` (Modular Interface 6 inputs) — its channels are inputs (sensor
  feeds), not outputs.
- ``0x42`` (Feedback Module) — feedback-only.

Verified by inspecting both traces: link records observed on
fdebrus's 86F5 install have ``channel_idx`` in 0x04..0x21 (max 33),
which matches a flat output map of 12+6+6+12+4+12 = 52 channels for
the six output-bearing modules in registry order. The second user's
846F install has channel indices in 0x04..0x12, also within the
expected band given their output-bearing module count."""


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


class RegistryBuffer:
    """Accumulator for ``ModuleRegistryRecord`` entries seen during a scan.

    The PC Link's link table is preceded by a registry section whose
    encounter order determines the flat output-channel index that
    link records' byte 0 references (Stage 2b hypothesis). This class
    holds those records in arrival order, ignoring duplicates that
    arise when the controller re-emits the same register.

    A single ``RegistryBuffer`` lives on a ``PcLinkDecoder`` instance
    and is reset between scans by the decoder.
    """

    __slots__ = ("_records", "_seen_addresses")

    def __init__(self) -> None:
        self._records: list[ModuleRegistryRecord] = []
        self._seen_addresses: set[str] = set()

    def add(self, record: ModuleRegistryRecord) -> bool:
        """Append a registry record. Returns ``True`` if appended,
        ``False`` if it was a duplicate (same bus address)."""

        addr = record.address.upper()
        if addr in self._seen_addresses:
            return False
        self._seen_addresses.add(addr)
        self._records.append(record)
        return True

    def reset(self) -> None:
        """Clear all recorded entries. Called between scan runs."""

        self._records.clear()
        self._seen_addresses.clear()

    @property
    def records(self) -> tuple[ModuleRegistryRecord, ...]:
        """Records in encounter order. Read-only snapshot."""

        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __bool__(self) -> bool:
        return bool(self._records)


def build_flat_channel_map(
    registry: RegistryBuffer,
    coordinator,
) -> list[tuple[str, int]]:
    """Build the flat output-channel map a PC-Link link record's byte 0
    indexes into.

    Walks ``registry`` in encounter order and, for each output-bearing
    module (``OUTPUT_BEARING_DEVICE_TYPES``), appends one
    ``(address, channel_1based)`` entry per channel. Modules without
    output channels (PC Link self, PC Logic, feedback, audio
    distribution, modular interface inputs) are skipped.

    Channel counts come from ``coordinator.get_module_channel_count``
    so the map reflects the live install's actual configuration. A
    module the coordinator can't size (channel count returned as 0,
    None, or non-int) is also skipped.
    """

    flat: list[tuple[str, int]] = []
    if coordinator is None:
        return flat

    get_count = getattr(coordinator, "get_module_channel_count", None)
    for record in registry.records:
        if record.device_type not in OUTPUT_BEARING_DEVICE_TYPES:
            continue
        try:
            count = get_count(record.address) if get_count else None
        except Exception:  # pragma: no cover - defensive
            count = None
        if not isinstance(count, int) or count <= 0:
            continue
        for ch in range(1, count + 1):
            flat.append((record.address, ch))
    return flat


def resolve_link_target(
    channel_index: int,
    registry: RegistryBuffer,
    coordinator,
) -> tuple[str, int] | None:
    """Resolve a link record's byte 0 to ``(target_address, channel)``.

    Returns ``None`` when:

    - ``channel_index`` is negative or ``>=`` the flat-map length.
    - ``registry`` is empty or only holds non-output modules.
    - ``coordinator`` is ``None`` or its channel-count lookup yields 0
      for every module.

    The resolution is hypothesised against trace data from two
    installs and not yet validated against a button-press → output
    ground-truth. Stage 2b ships the resolver with logging-only
    semantics; the merge layer doesn't act on its output until
    cross-install confirmation lands.
    """

    if channel_index < 0:
        return None
    flat = build_flat_channel_map(registry, coordinator)
    if channel_index >= len(flat):
        return None
    return flat[channel_index]


_MODE_TABLE_BY_DEVICE_TYPE: dict[int, dict[int, str]] = {
    0x01: SWITCH_MODE_MAPPING,    # Switch Module
    0x09: SWITCH_MODE_MAPPING,    # Compact Switch Module
    0x31: SWITCH_MODE_MAPPING,    # Compact Switch Module variant
    0x02: ROLLER_MODE_MAPPING,    # Roller Shutter Module
    0x03: DIMMER_MODE_MAPPING,    # Dimmer Module
    0x32: DIMMER_MODE_MAPPING,    # Compact Dim Controller
}
"""Mode-mapping table to use when decoding a PC-Link / PC-Logic link
record's mode byte against the resolved target module's device type.
Keys mirror ``OUTPUT_BEARING_DEVICE_TYPES`` exactly."""


def _device_type_for_address(
    registry: RegistryBuffer, address: str
) -> int | None:
    """Return the device-type byte for ``address`` in the registry, or
    ``None`` if the address isn't known. Case-insensitive."""

    address_upper = (address or "").upper()
    for record in registry.records:
        if record.address.upper() == address_upper:
            return record.device_type
    return None


def _key_raw_from_flag_byte(
    flag_byte: int, num_channels: int | None
) -> int | None:
    """Reverse-lookup a key index from a link record's flag byte.

    The flag byte's high nibble matches the per-key add value used by
    ``KEY_MAPPING_MODULE`` to derive push-button addresses. Reversing
    that table for the source button's channel count yields the
    ``key_raw`` index the merge layer needs to attach the link to the
    right operation point.

    Best-effort, single-install-validated. If a future capture shows
    the encoding differs (or varies by source-device class), only this
    function changes — the rest of the merge pipeline is unaffected.
    Returns ``None`` when the channel count is unknown or the nibble
    doesn't appear in ``KEY_MAPPING_MODULE`` for that count.
    """

    if not isinstance(num_channels, int):
        return None
    mapping = KEY_MAPPING_MODULE.get(num_channels)
    if mapping is None:
        return None
    high_nibble = f"{(flag_byte >> 4) & 0xF:X}"
    for key_idx, value in mapping.items():
        if value == high_nibble:
            return key_idx
    return None


def link_record_to_decoded_metadata(
    record: LinkRecord,
    registry: RegistryBuffer,
    coordinator,
) -> dict | None:
    """Translate a parsed ``LinkRecord`` into ``DecodedCommand`` metadata.

    Combines three resolutions:

    1. ``record.channel_index`` → ``(target_module_address, channel)``
       via the registry-driven flat channel map.
    2. Target module's device type → mode-mapping table → mode label
       string compatible with what switch/dimmer/roller decoders emit.
    3. ``record.payload_bytes`` (button address in bus byte order) →
       canonical button address; ``record.flag_byte`` → ``key_raw``
       using ``KEY_MAPPING_MODULE`` reverse lookup against the source
       button's channel count.

    Returns ``None`` when:

    - The channel index can't be resolved (registry incomplete, idx
      out of range, no output-bearing modules).
    - The target's device type isn't in
      ``_MODE_TABLE_BY_DEVICE_TYPE``.
    - The mode byte's low nibble doesn't map to a known mode for the
      target.
    - The source button's channel count is unknown or the flag byte
      doesn't yield a valid key index for it.

    The returned dict carries ``module_address`` set to the **target**
    module — ``add_to_command_mapping`` consumes it as an override to
    the positional argument so the link lands on the resolved output
    module, not on the controller (PC-Link / PC-Logic) currently being
    scanned.
    """

    target = resolve_link_target(record.channel_index, registry, coordinator)
    if target is None:
        return None
    target_address, target_channel = target

    target_device_type = _device_type_for_address(registry, target_address)
    if target_device_type is None:
        return None

    mode_table = _MODE_TABLE_BY_DEVICE_TYPE.get(target_device_type)
    if mode_table is None:
        return None

    mode_index = record.mode_byte & 0x0F
    mode_label = mode_table.get(mode_index)
    if mode_label is None:
        return None

    payload = record.payload_bytes or ""
    if len(payload) != 6:
        return None
    button_address = (payload[4:6] + payload[2:4] + payload[0:2]).upper()

    num_channels: int | None = None
    if coordinator is not None:
        get_btn_channels = getattr(coordinator, "get_button_channels", None)
        if get_btn_channels is not None:
            try:
                lookup = get_btn_channels(button_address)
            except Exception:  # pragma: no cover - defensive
                lookup = None
            if isinstance(lookup, int) and lookup > 0:
                num_channels = lookup

    key_raw = _key_raw_from_flag_byte(record.flag_byte, num_channels)
    if key_raw is None:
        return None

    return {
        "module_address": target_address,
        "channel": target_channel,
        "M": mode_label,
        "T1": None,
        "T2": None,
        "payload": record.raw_hex,
        "button_address": button_address,
        "push_button_address": button_address,
        "key_raw": key_raw,
    }


__all__ = [
    "RECORD_HEX_LEN",
    "REGISTRY_MARKER",
    "OUTPUT_BEARING_DEVICE_TYPES",
    "ModuleRegistryRecord",
    "LinkRecord",
    "PcRecord",
    "RegistryBuffer",
    "build_flat_channel_map",
    "is_empty_record",
    "is_noise_chunk",
    "link_record_to_decoded_metadata",
    "parse_pc_record",
    "resolve_link_target",
]
