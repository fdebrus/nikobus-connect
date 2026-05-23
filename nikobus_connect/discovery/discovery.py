import asyncio
import inspect
import json
import logging
import os
from datetime import datetime, timezone

from .base import (
    DecodedCommand,
    DiscoveryProgress,
    InventoryQueryType,
    InventoryResult,
    PHASE_FINALIZING,
    PHASE_IDENTITY,
    PHASE_INVENTORY,
    PHASE_REGISTER_SCAN,
)
from .dimmer_decoder import DimmerDecoder
from .pc_link_decoder import PcLinkDecoder
from .pc_logic_decoder import PcLogicDecoder
from .shutter_decoder import ShutterDecoder
from .switch_decoder import SwitchDecoder
from .mapping import (
    DEVICE_TYPES,
    KEY_MAPPING,
    KEY_MAPPING_MODULE,
    get_module_type_from_device_type,
)
from .protocol import (
    classify_device_type,
    convert_nikobus_address,
    derive_pc_logic_input_physicals,
    reverse_hex,
)
from ..const import (
    COMMAND_EXECUTION_DELAY,
    DEVICE_ADDRESS_INVENTORY,
    DEVICE_INVENTORY_ANSWER,
    MODULE_SCAN_ACK_TIMEOUT,
    MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT,
    MODULE_SCAN_DATA_TIMEOUT,
    MODULE_SCAN_RETRY_LIMIT,
    MODULE_SCAN_TRAILER_PREFIX,
    PC_LINK_INVENTORY_SIGNATURE_BYTE,
)
from .fileio import (
    merge_discovered_buttons,
    merge_discovered_modules,
    merge_linked_modules,
)
from ..protocol import make_pc_link_inventory_command

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IR channel decoding
# ---------------------------------------------------------------------------
# IR receivers use bus addresses where the last byte increments from a base.
# E.g. base 0D1C80 → slots 0D1C81..0D1CBF; base 0FFEC0 → slots 0FFEC1..
# Channel number = slot_byte - base_byte  (range 01-39).
# Bank (A/B/C/D) is determined by the key index on the button:
#   4-ch buttons: key 0→C, 1→A, 2→D, 3→B  (labels 1C, 1A, 1D, 1B)
#   8-ch buttons: keys 0-3 = group 2 (C,A,D,B), keys 4-7 = group 1 (C,A,D,B)
# The C,A,D,B pattern repeats every 4 keys, so bank = map[key % 4].
_IR_BANK_CYCLE = ("C", "A", "D", "B")
_IR_MAX_CHANNEL = 39

# Vendor "load current installation" register map, captured from a
# Niko PC software COM3 trace on 2026-05-08 against module 0x3D82.
# The PC software does NOT bus-scan to discover modules — it reads
# from a fixed list loaded from its saved project file. This is the
# authoritative per-module read sequence used by the vendor:
#
#   sub=00 → 6 regs (module header / identity)
#            0x05, 0x06, 0x07, 0x08, 0x09, 0x3E
#   sub=01 → 37 regs (link table + checksum at 0x96)
#            0x70..0x93 contiguous (36 regs), then 0x96
#            (regs 0x94, 0x95 deliberately skipped)
#   sub=04 → 5 regs (status / state)
#            0x65, 0x66, 0x67, 0x68, 0x69
#
# Total: 48 register reads per module. 0.16.0 wires this exact list
# into the default scan plan for ALL output modules (switch, roller,
# dimmer) AND PC-Logic — full alignment with vendor logic, no
# install- or firmware-specific exceptions.
#
# Safety net: ``broad_scan=True`` on the discovery instance re-adds
# the pre-0.16.0 sub=04 0x00..0x3F sweep as an extra pass after the
# vendor primary, for installs that report missing records on a
# firmware revision where the link table doesn't sit in the
# vendor-canonical 0x70..0x96 band.
_VENDOR_REGISTER_MAP_BY_SUB: dict[str, tuple[int, ...]] = {
    "00": (0x05, 0x06, 0x07, 0x08, 0x09, 0x3E),
    "01": tuple(range(0x70, 0x94)) + (0x96,),
    "04": (0x65, 0x66, 0x67, 0x68, 0x69),
}

# Source attribution for ``_VENDOR_REGISTER_MAP_BY_SUB``. Surfaced as
# a constant so a regression-flagging test can pin the provenance
# without re-stating the trace context inline.
_VENDOR_REGISTER_MAP_TRACE_SOURCE = (
    "Niko PC software COM3 trace, 2026-05-08, "
    "addr 3D82, 'load current installation' operation"
)

# Pre-0.16.0 broad-scan extra: when ``broad_scan=True`` is set on the
# discovery instance, add this contiguous sub=04 sweep as an *extra*
# pass after the vendor-aligned primary. Safety net for firmware
# revisions whose link table doesn't sit in the vendor's 0x70..0x96
# band — e.g. a 2026-05-04 dimmer capture (116D + 0E0A) showed
# records past 0x3F that the vendor map alone would miss.
_BROAD_SCAN_LEGACY_REGISTERS: tuple[int, ...] = tuple(range(0x00, 0x40))


# Sub-bytes scanned per module type, in order. Default plan is fully
# vendor-aligned (sub=00 header, sub=01 link table, sub=04 status) for
# every output module AND PC-Logic — no install-specific exceptions.
# PC-Link uses a separate captured trace (module-registry band) keyed
# off the synthetic ``"pc_link_inventory"`` sub-byte.
_SCAN_SUBS_BY_MODULE_TYPE: dict[str, tuple[str, ...]] = {
    "switch_module": ("00", "01", "04"),
    "roller_module": ("00", "01", "04"),
    "dimmer_module": ("00", "01", "04"),
    "pc_logic":      ("00", "01", "04"),
    "pc_link":       ("pc_link_inventory",),
}

# Per-module-type sub=04 broad-scan extra registers (legacy band).
# Adds the pre-0.16.0 sub=04 0x00..0x3F sweep ALONGSIDE the vendor
# primary when ``broad_scan=True``. Switch/roller/dimmer all benefit
# from the same extra band; PC-Logic too.
_BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE: dict[str, tuple[str, ...]] = {
    "switch_module": ("04_broad",),
    "roller_module": ("04_broad",),
    "dimmer_module": ("04_broad",),
    "pc_logic":      ("04_broad",),
}

# Per-sub-byte register lists.
#
#   "00"/"01"/"04" → exact vendor register lists, applied to every
#     output module AND PC-Logic. No approximation to contiguous ranges.
#   "04_broad" → synthetic sub-byte used only by ``broad_scan=True``.
#     Iterates the pre-0.16.0 sub=04 0x00..0x3F sweep; mapped back
#     to sub=04 on the wire by ``_wire_sub_byte``.
#   "pc_link_inventory" → PC-Link's module-registry band (0xA3..0xFF).
#     Captured separately (May 2024 trace) since PC-Link doesn't have
#     per-module link tables — only a controller-side module list.
#     Mapped to sub=04 on the wire.
_SCAN_REGISTERS_BY_SUB: dict[str, tuple[int, ...]] = {
    "00": _VENDOR_REGISTER_MAP_BY_SUB["00"],
    "01": _VENDOR_REGISTER_MAP_BY_SUB["01"],
    "04": _VENDOR_REGISTER_MAP_BY_SUB["04"],
    "04_broad": _BROAD_SCAN_LEGACY_REGISTERS,
    "pc_link_inventory": tuple(range(0xA3, 0x100)),
}

# Conservative fallback when a caller hands us a sub-byte the trace
# didn't cover (keeps future sub-bytes probeable without a silent skip).
_DEFAULT_SCAN_REGISTERS: tuple[int, ...] = tuple(range(0x00, 0x100))

# PC-Link's scan profile is a separate captured trace — the controller
# reads its OWN module-registry table from this band, not the per-module
# link table the vendor map describes. Captured from a Nikobus PC
# software serial trace on real hardware (May 2024) and aligned to
# the 0xA3 start to skip the 0x00..0x07 dead-zone where PC Link's
# consecutive-give-up early-stop fires.
_PC_LINK_REGISTERS: tuple[int, ...] = tuple(range(0xA3, 0x100))

# Module-type buckets whose addresses are NOT included in the
# ``query_module_inventory("ALL")`` register-scan queue and whose
# per-module dispatch path is short-circuited (no ``$1410…04`` reads
# issued).
#
# - ``feedback_module`` (0x42): 05-207 doesn't expose a routable link
#   table; its programming lives on the source modules' BP cells.
# - ``other_module``: catch-all bucket — primarily Button-class devices
#   (4-OP / 2-OP / RF / IR / Motion / Feedback Button) that carry no
#   register memory, plus any Module-category device whose name fails
#   to match a more specific keyword in
#   ``get_module_type_from_device_type``.
# - ``interface_module`` (0x37, 05-206): Modular Interface, 6 inputs.
#   The inputs feed the PC-Logic for routing — the interface itself
#   has no BP-cell table to scan. If a future capture proves
#   otherwise, drop the bucket from this set and add a decoder.
# - ``audio_module`` (0x2B, 05-205): Audio Distribution. No
#   button-link routing surface today; left as visibility-only until
#   a real install validates the storage format.
NON_OUTPUT_MODULE_TYPES: frozenset[str] = frozenset({
    "feedback_module",
    "other_module",
    "interface_module",
    "audio_module",
})

def _scan_subs_for_module_type(
    module_type: str | None, *, broad_scan: bool = False
) -> tuple[str, ...]:
    """Return the ordered sequence of sub-bytes to scan for a module type.

    Default plan is fully vendor-aligned: every output module AND
    PC-Logic gets the exact (sub=00, sub=01, sub=04) sequence the
    PC software reads (Niko COM3 trace, 2026-05-08). No
    install-specific or firmware-specific exceptions.

    ``broad_scan=True`` appends the pre-0.16.0 sub=04 0x00..0x3F sweep
    (synthetic ``"04_broad"`` token) as an extra pass after the
    vendor primary — safety net for firmware revisions whose link
    table doesn't sit in the vendor-canonical band.

    PC-Link is NOT in the table — it has its own scan profile, see
    ``_PC_LINK_REGISTERS``. Module types we don't scan at all (audio,
    feedback, interface, other) return an empty tuple.
    """

    if module_type not in _SCAN_SUBS_BY_MODULE_TYPE:
        # PC-Link / unknown — leave to the caller's existing dispatch.
        return ()
    plan = _SCAN_SUBS_BY_MODULE_TYPE[module_type]
    if broad_scan:
        extras = _BROAD_SCAN_EXTRA_SUBS_BY_MODULE_TYPE.get(module_type, ())
        seen: set[str] = set()
        combined: list[str] = []
        for sub in (*plan, *extras):
            if sub in seen:
                continue
            seen.add(sub)
            combined.append(sub)
        return tuple(combined)
    return plan


def _scan_registers_for_sub(
    sub_byte: str, module_type: str | None = None
) -> tuple[int, ...]:
    """Return the exact register list to scan for a given sub-byte.

    Vendor-aligned: every module type uses the same per-sub register
    list. No per-module-type widening — the whole point of 0.16.0 is
    to stop deviating from the vendor's reads.

    Unknown sub-bytes fall back to a full 0x00..0xFF sweep (defensive,
    keeps future sub-bytes probeable for callers that bypass the plan).
    """
    return _SCAN_REGISTERS_BY_SUB.get(sub_byte, _DEFAULT_SCAN_REGISTERS)


def _wire_sub_byte(sub_byte: str) -> str:
    """Map a plan-time sub-byte to its on-the-wire form.

    The plan uses synthetic sub-byte tokens to keep register lists
    distinct (``"04_broad"`` for the legacy sweep, ``"pc_link_inventory"``
    for the PC-Link registry band), but on the wire they all map back
    to ``"04"`` because that's the function code Niko's protocol uses
    for ``read register`` regardless of which memory region the host
    is interested in.
    """
    return "04" if sub_byte in ("04_broad", "pc_link_inventory") else sub_byte


def decode_ir_channel(ir_slot_addr: str | None, key_raw: int | None, ir_base_byte: int = 0x80) -> str | None:
    """Derive the IR channel label from a bus slot address and key index.

    Parameters
    ----------
    ir_slot_addr : str
        The 6-char IR slot address (e.g. "0D1C91").
    key_raw : int
        The raw key index (0-7).
    ir_base_byte : int
        The base byte of the IR receiver (default 0x80).  Channel is
        derived as ``slot_byte - ir_base_byte``.

    Returns the label (e.g. "17A") or None for non-IR / out-of-range addresses.
    """
    if not ir_slot_addr or key_raw is None:
        return None

    a = ir_slot_addr.strip().upper()
    if len(a) != 6:
        return None

    try:
        slot_byte = int(a[-2:], 16)
    except ValueError:
        return None

    channel = slot_byte - ir_base_byte
    if channel < 1 or channel > _IR_MAX_CHANNEL:
        return None

    if not isinstance(key_raw, int) or key_raw < 0 or key_raw > 7:
        return None

    bank = _IR_BANK_CYCLE[key_raw % 4]
    return f"{channel:02d}{bank}"


def build_ir_receiver_lookup(buttons) -> dict[str, int]:
    """Build a mapping of 4-char IR address prefixes to their base byte.

    Operates on the Option-A physical-keyed button store. ``buttons`` may
    be the ``nikobus_button`` dict itself (physical_address -> entry) or
    any iterable of ``(physical_address, entry)`` pairs.

    Returns e.g. {"0D1C": 0x80, "0FFE": 0xC0}.
    """
    if isinstance(buttons, dict):
        items = buttons.items()
    else:
        items = buttons

    lookup: dict[str, int] = {}
    for physical_addr, button in items:
        if not isinstance(button, dict):
            continue
        if "IR" not in (button.get("type") or ""):
            continue
        addr = (physical_addr or "").strip().upper()
        if len(addr) != 6:
            continue
        try:
            prefix = addr[:4]
            base_byte = int(addr[-2:], 16)
            lookup.setdefault(prefix, base_byte)
        except ValueError:
            continue
    return lookup


def split_ir_button_address(
    addr: str | None,
    ir_receiver_lookup: dict[str, int] | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Nikobus IR receiver: physical device is XXXX{base}, IR slots are XXXX{base+1}..
    Returns (physical_addr, ir_slot_addr, ir_slot_byte_hex).
    Non-IR addresses return (addr, None, None).

    Parameters
    ----------
    addr : str
        The 6-char address to classify.
    ir_receiver_lookup : dict
        Mapping of 4-char prefix → base byte, built by build_ir_receiver_lookup().
        Falls back to legacy {"0D1C": 0x80} when None.
    """
    if not addr:
        return None, None, None

    a = addr.strip().upper()
    if len(a) != 6:
        return a, None, None

    if ir_receiver_lookup is None:
        ir_receiver_lookup = {"0D1C": 0x80}

    prefix = a[:4]
    if prefix not in ir_receiver_lookup:
        return a, None, None

    base_byte = ir_receiver_lookup[prefix]
    physical = f"{prefix}{base_byte:02X}"
    if a == physical:
        return physical, None, None

    return physical, a, a[-2:]


def add_to_command_mapping(command_mapping, decoded_command, module_address, ir_receiver_lookup=None):
    """Store decoded command information, allowing one-to-many button mappings."""
    push_button_address = decoded_command.get("push_button_address")

    # Fall back to physical device address when push_button_address could not
    # be resolved (e.g. coordinator doesn't know the button's channel count).
    # fileio._rebuild_address_lookup() maps physical addresses via
    # linked_button[].address, so the match will still succeed.
    if push_button_address is None:
        push_button_address = decoded_command.get("button_address")

    # Accept legacy/new decoder fields
    key_raw = decoded_command.get("key_raw")
    if key_raw is None:
        key_raw = decoded_command.get("key")  # <-- IMPORTANT fallback

    if push_button_address is None or key_raw is None:
        return

    # Normalize key to a stable string/int (depending on what your decoders use)
    if isinstance(key_raw, str):
        key_raw = key_raw.strip()
        if key_raw.isdigit():
            key_raw = int(key_raw)

    physical_push, ir_push_addr, ir_push_slot = split_ir_button_address(push_button_address, ir_receiver_lookup)

    button_address = decoded_command.get("button_address")
    physical_btn, ir_btn_addr, ir_btn_slot = split_ir_button_address(button_address, ir_receiver_lookup)

    # Derive IR channel label (e.g. "17A") from the bus slot address + key.
    ir_slot_addr = ir_btn_addr or ir_push_addr
    ir_base_byte = 0x80
    if ir_slot_addr and ir_receiver_lookup:
        prefix = ir_slot_addr[:4].upper()
        ir_base_byte = ir_receiver_lookup.get(prefix, 0x80)
    ir_channel = decode_ir_channel(ir_slot_addr, key_raw, ir_base_byte) if ir_slot_addr else None

    # Mapping key: prefer logical IR channel label; fall back to raw slot byte.
    ir_key = ir_channel or ir_btn_slot or ir_push_slot

    # For IR records the nibble-shifted wire address (e.g. "D44E2C" for
    # receiver 0D1C80 + code 10B) doesn't start with an IR receiver prefix,
    # so split_ir_button_address leaves physical_push as the shifted form.
    # Use the IR receiver's physical base instead so the merge-time
    # resolver can locate the receiver and attach the link to an
    # IR:{code} op-point. physical_btn is that base when button_address
    # is the pre-shift slot address (always the case for IR records).
    if ir_key and physical_btn:
        mapping_address = physical_btn
    else:
        mapping_address = physical_push
    mapping_key = (mapping_address, key_raw, ir_key)
    outputs = command_mapping.setdefault(mapping_key, [])

    channel_number = decoded_command.get("channel")

    # PC-Link / PC-Logic decoders set ``module_address`` in the
    # decoded metadata to the **resolved target** module — not the
    # controller they were scanned from. Honour that override so the
    # link lands on the real output module. Switch/dimmer/roller
    # decoders never set this field, so the positional argument (the
    # module being scanned) is used in those cases.
    target_module_address = (
        decoded_command.get("module_address") or module_address
    )

    output_definition = {
        "module_address": target_module_address,
        "channel": channel_number,
        "mode": decoded_command.get("M"),
        "t1": decoded_command.get("T1"),
        "t2": decoded_command.get("T2"),
        "payload": decoded_command.get("payload"),

        # button addresses
        "button_address": physical_btn or physical_push or button_address,
        "ir_button_address": ir_btn_addr or ir_push_addr,

        # IR channel label (e.g. "17A", "30B") derived from slot address + key.
        "ir_code": ir_channel or ir_btn_slot or ir_push_slot,

        # 0.5.22: scan-source provenance. ``output_module_table`` for
        # records read from a switch / dimmer / roller's own link
        # table (current programming). ``pc_link_registry`` or
        # ``pc_logic_registry`` for records read from PC-Link or
        # PC-Logic register memory (may be stale residue from a
        # previous install, or scene-only programming the output
        # modules don't carry). HA-side reconciliation filters on
        # this to avoid treating residue as active programming.
        # See Nikobus-HA #319 for the IKIKN forensic.
        "record_source": decoded_command.get("record_source"),
    }

    dedupe_key = (
        output_definition["module_address"],
        output_definition["channel"],
        output_definition["mode"],
        output_definition["t1"],
        output_definition["t2"],
        output_definition.get("ir_code"),
        output_definition.get("ir_button_address"),
    )

    existing_keys = {
        (
            entry.get("module_address"),
            entry.get("channel"),
            entry.get("mode"),
            entry.get("t1"),
            entry.get("t2"),
            entry.get("ir_code"),
            entry.get("ir_button_address"),
        )
        for entry in outputs
    }

    if dedupe_key not in existing_keys:
        outputs.append(output_definition)


async def _notify_discovery_finished(
    discovery,
    *,
    discovered_devices: dict | None = None,
    inventory_query_type=None,
) -> None:
    """Call the discovery finished callback when available.

    0.5.20: ``discovered_devices`` and ``inventory_query_type`` are
    passed as keyword arguments so consumers don't have to read
    mutable instance state (which the library may have cleared
    before the callback fired in 0.5.19 and earlier — see
    Nikobus-HA #319).

    Backward-compat: if the consumer's callback predates 0.5.20 and
    has a no-arg signature (``async def cb()``), it's called with
    no args. Callbacks that accept ``**kwargs`` or explicitly name
    the new parameters receive them.
    """

    callback = getattr(discovery, "on_discovery_finished", None)
    if not callback:
        return

    try:
        sig = inspect.signature(callback)
    except (TypeError, ValueError):
        # Some callables (built-ins, partials with quirks) can't be
        # introspected. Fall back to no-arg call.
        await callback()
        return

    params = sig.parameters
    accepts_var_keyword = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )

    kwargs: dict = {}
    if accepts_var_keyword or "discovered_devices" in params:
        kwargs["discovered_devices"] = discovered_devices
    if accepts_var_keyword or "inventory_query_type" in params:
        kwargs["inventory_query_type"] = inventory_query_type

    await callback(**kwargs)


def _is_inventory_trailer(message: str) -> bool:
    """Detect a "$18<all-FF><CRC>" trailer frame.

    The module emits one of these during a register scan to signal that
    the remaining registers are unprogrammed. The payload between the
    ``$18`` header and the trailing 3-byte CRC is all 0xFF. Treat any
    all-FF payload of length >= 1 byte as a trailer.
    """

    if not isinstance(message, str):
        return False
    if not message.startswith(MODULE_SCAN_TRAILER_PREFIX):
        return False
    # 3 chars header + 6 chars CRC = 9 chars of bookkeeping; payload
    # lives in-between.
    body = message[len(MODULE_SCAN_TRAILER_PREFIX) : -6]
    if not body:
        return False
    return all(ch == "F" for ch in body.upper())


class NikobusDiscovery:
    def __init__(
        self,
        coordinator,
        *,
        config_dir,
        create_task,
        button_data=None,
        on_button_save=None,
        module_data=None,
        on_module_save=None,
        on_progress=None,
        broad_scan: bool = False,
    ):
        self.discovered_devices = {}
        self._coordinator = coordinator
        self._config_dir = config_dir
        self._create_task = create_task
        self._button_data = button_data
        self._on_button_save = on_button_save
        self._module_data = module_data
        self._on_module_save = on_module_save
        # 0.16.0 vendor-aligned default scans only the 48 vendor regs
        # per module. ``broad_scan=True`` re-adds the pre-0.16.0
        # sub=04 0x00..0x3F sweep as a safety-net extra pass.
        self._broad_scan: bool = bool(broad_scan)
        if module_data is not None:
            existing_modules = module_data.get("nikobus_module")
            if not isinstance(existing_modules, dict):
                module_data["nikobus_module"] = {}
        self._on_progress = on_progress
        # Running counters reflected in every ``DiscoveryProgress``.
        self._progress_module_index = 0
        self._progress_module_total = 0
        self._progress_register_total = 0
        self._progress_decoded_records = 0
        # Set of unknown device-type bytes already warned about this
        # session. Pre-0.5.4 each scan logged the same WARNING N times
        # per type (one per record); the dedupe collapses that to a
        # single line per type so a noisy install with several uncatalogued
        # types doesn't flood the log on every inventory pass.
        self._unknown_device_types_warned: set[str] = set()
        if button_data is not None:
            existing = button_data.get("nikobus_button")
            if not isinstance(existing, dict):
                button_data["nikobus_button"] = {}
        self._module_timeout_seconds = 5.0
        self._inventory_timeout_seconds = 10.0
        self._decoders = [
            DimmerDecoder(coordinator),
            SwitchDecoder(coordinator),
            ShutterDecoder(coordinator),
            PcLogicDecoder(coordinator),
            PcLinkDecoder(coordinator),
        ]
        self._timeout_task: asyncio.Task | None = None
        self._inventory_timeout_task: asyncio.Task | None = None
        self.discovery_stage: str | None = None
        self._register_scan_queue: list[str] = []
        self._inventory_addresses: set[str] = set()
        self._module_found_data: bool = False
        self._module_consecutive_empties: int = 0
        # Sequential register-scan coordination. The listener dispatches
        # $2E / $1E / $18 frames directly to the event callback (they
        # bypass the command-handler response queue). During a scan we
        # hook the parser entry points to notify this event so the
        # per-command loop can wake up when a data frame or trailer
        # arrives, without rewriting the listener.
        self._scan_event: asyncio.Event = asyncio.Event()
        self._scan_trailer_seen: bool = False
        self._scan_active: bool = False
        self._scan_lock: asyncio.Lock = asyncio.Lock()
        # Cross-module accumulators for the remote-transmitter synthesis
        # pass. Per-module ``merge_linked_modules`` collects button
        # addresses that didn't resolve to any inventory entry; we hold
        # them here until all module scans finish, then cluster by
        # 4-hex suffix and synthesise virtual transmitter parents +
        # passthrough children for clusters above the threshold.
        self._accumulated_unmatched: set[str] = set()
        self._accumulated_command_mapping: dict = {}
        self.reset_state()

    def reset_state(self, *, update_flags: bool = True):
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None
        if self._inventory_timeout_task:
            self._inventory_timeout_task.cancel()
            self._inventory_timeout_task = None
        self._payload_buffer = ""
        self._module_address = None
        self._module_type = None
        self._module_channels: int | None = None
        self._scan_response_index = 0
        self._register_scan_queue = []
        self._inventory_addresses = set()
        self._inventory_identity_queued: set[str] = set()
        self._module_found_data = False
        self._module_consecutive_empties = 0
        self.discovery_stage = None
        self._decoded_buffer: dict | None = None
        self._accumulated_unmatched = set()
        self._accumulated_command_mapping = {}
        if update_flags:
            self._coordinator.discovery_running = False
            self._coordinator.discovery_module = False
            self._coordinator.discovery_module_address = None
            self._coordinator.inventory_query_type = None

    def normalize_module_address(
        self, address: str, *, source: str, reverse_bus_order: bool = False
    ) -> str:
        """Return a canonical module address, logging when normalization occurs."""

        raw = (address or "").strip().upper()
        normalized = raw

        try:
            if reverse_bus_order:
                normalized = reverse_hex(raw)
        except ValueError:
            normalized = raw

        if normalized != raw:
            _LOGGER.debug(
                "Normalized module address | raw=%s normalized=%s source=%s",
                raw,
                normalized,
                source,
            )

        return normalized

    def _get_decoder(self):
        for decoder in getattr(self, "_decoders", []):
            if decoder.can_handle(self._module_type):
                return decoder
        return None

    def _resolve_module_type(
        self, address: str, discovered_device: dict | None
    ) -> str | None:
        """Resolve the module type for ``address``.

        Coordinator config is authoritative — it reflects the user's
        physical wiring via ``dict_module_data``. The inventory
        self-report is only used when config has no entry for the
        address (first-time scan of a newly-added module).

        When both sources disagree, log at INFO so the override is
        visible in ordinary HA logs. This has been observed in the
        wild: a physical switch module self-reporting device_type=0x03
        during the PC-Link identity phase.
        """

        config_type = self._coordinator.get_module_type(address)
        inventory_type = (discovered_device or {}).get("module_type")

        if config_type and inventory_type and config_type != inventory_type:
            _LOGGER.debug(
                "Module type conflict | address=%s config=%s inventory=%s — using config",
                address,
                config_type,
                inventory_type,
            )

        return config_type or inventory_type

    # ------------------------------------------------------------------
    # Sequential register scan
    # ------------------------------------------------------------------

    def _notify_scan_frame(self, message: str) -> None:
        """Wake the sequential scan loop on each inbound discovery frame.

        Called from ``parse_module_inventory_response`` and
        ``handle_device_address_inventory`` for every ``$2E`` / ``$1E``
        / ``$18`` message while a scan is running. A $18 frame whose
        payload is all-FF is treated as a trailer — the module has no
        more programmed memory and the scan should short-circuit.
        """

        if not self._scan_active:
            return
        if message.startswith(MODULE_SCAN_TRAILER_PREFIX) and _is_inventory_trailer(
            message
        ):
            self._scan_trailer_seen = True
        self._scan_event.set()

    async def _scan_module_registers(
        self,
        normalized_address: str,
        base_command: str,
        command_range,
        sub_byte: str = "04",
    ) -> None:
        """Read each register one at a time, waiting for ACK + optional data.

        ``sub_byte`` is the 2-hex byte appended after the register byte
        in the read command. Different sub-bytes address different memory
        banks on a module: ``"04"`` is the default (button-link records),
        ``"00"`` and ``"01"`` access additional banks discovered via PC
        software trace analysis.

        Replaces the former fire-and-forget queue fill. Per register:

        1. Send the inventory read command.
        2. Wait up to ``MODULE_SCAN_ACK_TIMEOUT`` for a ``$05…`` ACK.
           Retry once on timeout; skip the register if still missing.
        3. Wait up to ``MODULE_SCAN_DATA_TIMEOUT`` for the matching
           ``$2E`` / ``$1E`` data frame. Silence is legitimate — empty
           registers produce no data.
        4. If a ``$18`` trailer arrives, break; the module has signalled
           end-of-programmed-memory.

        Two concurrent scans are prevented by ``self._scan_lock``; the
        second caller awaits the first.
        """

        listener = self._coordinator.nikobus_command._listener
        connection = self._coordinator.nikobus_command._connection

        async with self._scan_lock:
            self._scan_active = True
            self._scan_trailer_seen = False
            self._scan_event.clear()
            # Suppress the inactivity timer that scan-response parsing
            # keeps rescheduling. Without this, the first register of a
            # pass that a module ignores drops us into a 5 s window of
            # no responses → the timer fires ``_finalize_discovery``
            # mid-scan, tearing down the connection while the rest of
            # this pass and any follow-up passes are still queued.
            # We finalize explicitly from ``query_module_inventory``
            # once all passes complete.
            self._cancel_timeout()
            # Progress: reset the register counter to the full scan range;
            # it drops to ``registers_sent`` when a trailer short-circuits.
            try:
                self._progress_register_total = len(command_range)
            except TypeError:
                self._progress_register_total = 0
            try:
                registers_sent = 0
                consecutive_give_ups = 0
                for reg in command_range:
                    if self._scan_trailer_seen:
                        _LOGGER.debug(
                            "Register scan short-circuited by trailer | module=%s "
                            "last_register=0x%02X sent=%d",
                            normalized_address,
                            reg,
                            registers_sent,
                        )
                        self._progress_register_total = registers_sent
                        break
                    partial_hex = f"{base_command}{reg:02X}{sub_byte}"
                    pc_link_command = make_pc_link_inventory_command(partial_hex)
                    ack_ok = await self._read_register_once(
                        pc_link_command,
                        reg,
                        normalized_address,
                        listener,
                        connection,
                    )
                    registers_sent += 1
                    await self._emit_progress(
                        PHASE_REGISTER_SCAN,
                        module_address=normalized_address,
                        register=reg,
                    )
                    if ack_ok:
                        consecutive_give_ups = 0
                    else:
                        consecutive_give_ups += 1
                        if consecutive_give_ups >= MODULE_SCAN_CONSECUTIVE_GIVE_UP_LIMIT:
                            # Expected on bank-incompatible sub-bytes; fast-fail
                            # is a feature, not a problem. Keep at DEBUG so it
                            # doesn't surface in the integration UI.
                            _LOGGER.debug(
                                "Register scan pass aborted — module not responding | "
                                "module=%s base_cmd=%s sub=%s last_register=0x%02X "
                                "consecutive_give_ups=%d sent=%d",
                                normalized_address,
                                base_command,
                                sub_byte,
                                reg,
                                consecutive_give_ups,
                                registers_sent,
                            )
                            self._progress_register_total = registers_sent
                            break
                    await asyncio.sleep(COMMAND_EXECUTION_DELAY)
                else:
                    _LOGGER.debug(
                        "Register scan completed full range | module=%s sent=%d",
                        normalized_address,
                        registers_sent,
                    )
            finally:
                self._scan_active = False
                self._scan_trailer_seen = False

    async def _read_register_once(
        self,
        command: str,
        reg: int,
        module_address: str,
        listener,
        connection,
    ) -> bool:
        """Send a single register-read and wait for ACK + optional data frame.

        Returns True when the ACK was observed (whether or not a data
        frame followed), False when all retries failed to see an ACK.
        """

        ack_prefix = f"$05{command[3:5]}"

        for attempt in range(MODULE_SCAN_RETRY_LIMIT + 1):
            # Drain any stale entries from the response queue — we are
            # the only consumer while _awaiting_response is set.
            while not listener.response_queue.empty():
                try:
                    listener.response_queue.get_nowait()
                    listener.response_queue.task_done()
                except asyncio.QueueEmpty:
                    break

            self._scan_event.clear()
            listener._awaiting_response = True
            try:
                try:
                    await connection.send(command)
                except Exception:
                    # Typically a transient "Not connected" during
                    # mid-scan reconnect cycles; the outer scan loop
                    # fast-fails cleanly. Keep at DEBUG.
                    _LOGGER.debug(
                        "Register scan send failed | module=%s reg=0x%02X attempt=%d",
                        module_address,
                        reg,
                        attempt + 1,
                        exc_info=True,
                    )
                    continue

                # Wait for the ACK that matches our command.
                ack_ok = await self._await_matching_ack(
                    listener.response_queue, ack_prefix
                )
                if not ack_ok:
                    _LOGGER.debug(
                        "Register scan ACK timeout | module=%s reg=0x%02X attempt=%d",
                        module_address,
                        reg,
                        attempt + 1,
                    )
                    continue

                # ACK in hand: wait briefly for an accompanying data frame.
                # Silence is valid for empty registers — don't treat it as
                # an error.
                try:
                    await asyncio.wait_for(
                        self._scan_event.wait(),
                        timeout=MODULE_SCAN_DATA_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    pass
                return True
            finally:
                listener._awaiting_response = False

        # Expected per-register behaviour on bank-incompatible sub-bytes
        # (handled by the outer fast-fail). Keep at DEBUG to avoid
        # cluttering the integration log.
        _LOGGER.debug(
            "Register scan gave up on register | module=%s reg=0x%02X",
            module_address,
            reg,
        )
        # The ACK+data for this register can still arrive moments later.
        # If we leave stale bytes in the payload buffer or in the response
        # queue, subsequent registers will decode against the wrong
        # remainder (one-register drift producing phantom records).
        # Flush both so the next register starts from a clean slate.
        self._payload_buffer = ""
        while not listener.response_queue.empty():
            try:
                listener.response_queue.get_nowait()
                listener.response_queue.task_done()
            except asyncio.QueueEmpty:
                break
        return False

    @staticmethod
    async def _await_matching_ack(queue, ack_prefix: str) -> bool:
        """Drain the response queue until an ACK with ``ack_prefix`` is seen.

        Returns False if ``MODULE_SCAN_ACK_TIMEOUT`` elapses with no match.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + MODULE_SCAN_ACK_TIMEOUT
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return False
            try:
                queue.task_done()
            except ValueError:
                pass
            if isinstance(msg, str) and msg.startswith(ack_prefix):
                return True

    def _is_known_module_address(self, address: str | None) -> bool:
        normalized = (address or "").upper()
        return any(
            normalized in modules for modules in self._coordinator.dict_module_data.values()
        )

    def _cancel_timeout(self) -> None:
        if self._timeout_task:
            if asyncio.current_task() is not self._timeout_task:
                self._timeout_task.cancel()
            self._timeout_task = None

    def _cancel_inventory_timeout(self) -> None:
        if self._inventory_timeout_task:
            if asyncio.current_task() is not self._inventory_timeout_task:
                self._inventory_timeout_task.cancel()
            self._inventory_timeout_task = None

    def _schedule_timeout(self) -> None:
        self._cancel_timeout()
        module_address = self._module_address
        self._timeout_task = self._create_task(
            self._timeout_after(module_address)
        )

    def _schedule_inventory_timeout(self) -> None:
        self._cancel_inventory_timeout()
        self._inventory_timeout_task = self._create_task(
            self._inventory_timeout_after()
        )

    async def _check_early_termination(self, address: str, had_data: bool) -> bool:
        """Track consecutive empty module inventory responses for logging.

        Early termination is disabled because roller/shutter modules have
        sparsely-programmed registers — button links are spread across the
        full register range (0x10-0xFF) with large gaps between them.  The
        full scan (~36 s per module) is acceptable for a one-time discovery.

        Always returns ``False`` so the caller continues scanning.
        """
        if had_data:
            self._module_found_data = True
            self._module_consecutive_empties = 0
        else:
            if self._module_found_data:
                self._module_consecutive_empties += 1

        return False

    async def _timeout_after(self, module_address: str | None) -> None:
        try:
            await asyncio.sleep(self._module_timeout_seconds)
        except asyncio.CancelledError:
            return
        await self._finalize_discovery(module_address)

    async def _inventory_timeout_after(self) -> None:
        try:
            await asyncio.sleep(self._inventory_timeout_seconds)
        except asyncio.CancelledError:
            return

        try:
            await self._finalize_inventory_phase()
        except Exception as err:
            _LOGGER.error("CRITICAL ERROR in _finalize_inventory_phase: %s", err, exc_info=True)
            self.reset_state()

    async def _emit_progress(
        self,
        phase: str,
        *,
        module_address: str | None = None,
        register: int | None = None,
    ) -> None:
        """Invoke the caller-supplied ``on_progress`` callback (if any).

        The callback is optional, runs asynchronously, and must not be
        allowed to abort the scan if it raises — log and swallow.
        """

        callback = self._on_progress
        if callback is None:
            return
        progress = DiscoveryProgress(
            phase=phase,
            module_address=module_address,
            module_index=self._progress_module_index,
            module_total=self._progress_module_total,
            register=register,
            register_total=self._progress_register_total,
            decoded_records=self._progress_decoded_records,
        )
        try:
            result = callback(progress)
            if inspect.isawaitable(result):
                await result
        except Exception:
            _LOGGER.warning(
                "Discovery on_progress callback raised; continuing scan",
                exc_info=True,
            )

    def _reset_module_context(self) -> None:
        self._payload_buffer = ""
        self._module_address = None
        self._module_type = None
        self._module_channels = None
        self._module_found_data = False
        self._module_consecutive_empties = 0
        self._scan_response_index = 0
        # Re-arm the alt-alignment skip-pending counter on every
        # decoder so the next per-module scan that picks one up
        # starts from a clean slate (no carry from a prior module).
        for decoder in getattr(self, "_decoders", []):
            reset = getattr(decoder, "reset_scan_buffers", None)
            if reset is not None:
                reset()

    async def _finalize_discovery(self, module_address: str | None = None) -> None:
        self._cancel_timeout()
        resolved_address = (
            module_address
            or self._module_address
            or self._coordinator.discovery_module_address
        )
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None
        self._reset_module_context()

        if self.discovery_stage == "register_scan" and self._register_scan_queue:
            # Let the bus breathe before scanning the next module
            await asyncio.sleep(1.0)
            await self._start_next_register_scan()
            return

        await self._complete_discovery_run(resolved_address)

    # Marker strings that flag synthesized input-module children in
    # ``discovered_devices`` and in the button store's ``type`` field.
    # HA-side display code keys off the ``pc_logic_parent_address``
    # provenance field to render each as ``LM-INPUT N`` parented
    # under the owning module device.
    PC_LOGIC_INPUT_TYPE: str = "PC-Logic Logical Input"
    PC_LOGIC_INPUT_MODEL: str = "05-201"
    INTERFACE_MODULE_INPUT_TYPE: str = "Modular Interface Input"
    INTERFACE_MODULE_INPUT_MODEL: str = "05-206"

    # Module types whose 6 inputs are synthesised by the library
    # (firmware-computed addresses, not enumerated in $1011). Both
    # use the same derivation formula in ``protocol.py`` —
    # validated empirically for ``pc_logic`` on two installs (940C,
    # 8DC8); applied to ``interface_module`` on the same-shape
    # hypothesis pending hardware confirmation.
    _INPUT_MODULE_SYNTHESIS_TYPES: frozenset[str] = frozenset(
        {"pc_logic", "interface_module"}
    )

    def _synthesize_pc_logic_inputs(self) -> None:
        """Add virtual-button entries for each input module's children.

        Iterates ``self.discovered_devices`` for ``pc_logic`` and
        ``interface_module`` modules, computes the bus addresses their
        inputs emit (via ``derive_pc_logic_input_physicals``), and
        synthesises a 2-channel ``category="Button"`` entry per input.
        The standard merge layer then writes them into the button
        store with one operation point per key (1A primary, 1B alias
        per ``PC_LOGIC_KEY_MAPPING``).

        The synthesis-time module-type → description/model mapping:

          * ``pc_logic`` → ``PC-Logic Logical Input`` / ``05-201``
          * ``interface_module`` → ``Modular Interface Input`` / ``05-206``

        Idempotent: re-running merges into existing entries cleanly
        via ``merge_discovered_buttons``'s upsert semantics. Kept
        named ``_synthesize_pc_logic_inputs`` for callsite stability.
        """

        type_metadata = {
            "pc_logic": (
                self.PC_LOGIC_INPUT_TYPE,
                self.PC_LOGIC_INPUT_MODEL,
            ),
            "interface_module": (
                self.INTERFACE_MODULE_INPUT_TYPE,
                self.INTERFACE_MODULE_INPUT_MODEL,
            ),
        }

        new_entries: dict[str, dict] = {}
        for module_addr, device in self.discovered_devices.items():
            if not isinstance(device, dict):
                continue
            module_type = device.get("module_type")
            if module_type not in self._INPUT_MODULE_SYNTHESIS_TYPES:
                continue
            channels_count = int(device.get("channels_count") or 0)
            if channels_count <= 0:
                # Catalogue should always provide 6 for 05-201/05-206;
                # defend against future variants with zero channels.
                continue
            try:
                input_physicals = derive_pc_logic_input_physicals(
                    module_addr, channels_count
                )
            except ValueError as err:
                _LOGGER.warning(
                    "Could not derive %s inputs for %s (%s); "
                    "inputs will not be surfaced for this module.",
                    module_type,
                    module_addr,
                    err,
                )
                continue

            description, model = type_metadata[module_type]
            for slot_index, input_phys in enumerate(input_physicals, start=1):
                if input_phys in self.discovered_devices:
                    # Real button already discovered at this address —
                    # don't shadow it. Vanishingly unlikely in practice
                    # (the synthesised range is firmware-reserved) but
                    # guard anyway.
                    _LOGGER.debug(
                        "%s input slot %d address %s already in "
                        "inventory — skipping synthesis",
                        module_type,
                        slot_index,
                        input_phys,
                    )
                    continue
                new_entries[input_phys] = {
                    "description": description,
                    "discovered_name": description,
                    "category": "Button",
                    "device_type": "LM",  # synthetic marker, not a real DEVICE_TYPES byte
                    "model": model,
                    "address": input_phys,
                    "channels": 2,
                    "channels_count": 2,
                    "module_type": "other_module",
                    "discovered": True,
                    # Provenance tags the HA-side renderer keys off to
                    # parent the synthesized device under the owning
                    # module (instead of the wall-buttons category)
                    # and to render the friendly ``LM-INPUT N`` name.
                    # ``pc_logic_parent_address`` retained as the field
                    # name for HA-callsite compatibility — same shape
                    # for both pc_logic and interface_module parents.
                    "pc_logic_parent_address": module_addr,
                    "pc_logic_parent_type": module_type,
                    "pc_logic_slot_index": slot_index,
                }
                _LOGGER.info(
                    "Synthesized %s input | parent=%s slot=%d "
                    "address=%s",
                    module_type,
                    module_addr,
                    slot_index,
                    input_phys,
                )

        self.discovered_devices.update(new_entries)

    # Minimum cluster size that qualifies as a "virtual transmitter".
    # Set to 8 (one 8-channel button's worth of A/B/C/D × 2 keys) so
    # we catch unenrolled multi-key remotes and large keypads while
    # still ignoring random small coincidences in flash garbage.
    REMOTE_TRANSMITTER_CLUSTER_THRESHOLD: int = 8

    # Prefix prepended to a 4-hex suffix to form the synthetic
    # ``physical address`` of a virtual transmitter parent. The full
    # synthetic ID is e.g. ``RT-E31C`` — used as the via_device
    # identifier so HA can group all 52 emitted codes under one
    # parent device.
    REMOTE_TRANSMITTER_PREFIX: str = "RT-"

    def _synthesize_remote_transmitters_from_unmatched(self) -> None:
        """Synthesise virtual transmitters from clusters of unmatched
        button references collected across module scans.

        A "cluster" is N >= threshold button addresses sharing the
        last 4 hex characters (the low 16 bits). Each cluster gets:

          * A virtual transmitter parent entry in
            ``self.discovered_devices`` (synthetic key
            ``RT-<suffix>``, ``category="Module"``,
            ``module_type="remote_transmitter"``).
          * One passthrough button child per cluster member, keyed
            in the button store by the observed bus address itself.
            ``merge_discovered_buttons`` has a passthrough branch
            for entries carrying ``remote_transmitter_address`` —
            it writes the op-point's ``bus_address`` directly from
            the synthesis entry, skipping the
            ``convert_nikobus_address`` round-trip (which isn't a
            true bijection for all 24-bit values).

        After populating ``discovered_devices``, the caller is
        expected to re-run ``merge_discovered_buttons`` so the new
        entries land in the button store, then re-run
        ``merge_linked_modules`` with the accumulated command
        mapping to wire up the previously-unmatched link records.
        """

        if not self._accumulated_unmatched:
            _LOGGER.info(
                "Remote-transmitter synthesis | accumulator empty — no "
                "unmatched references were collected during this scan "
                "(either no scan ran or every BP-cell reference resolved "
                "to an existing button-store entry)."
            )
            return

        # Cluster by last 4 hex chars.
        clusters: dict[str, list[str]] = {}
        for addr in self._accumulated_unmatched:
            if len(addr) < 4:
                continue
            suffix = addr[-4:].upper()
            clusters.setdefault(suffix, []).append(addr.upper())

        # Always log what we found, even when no cluster meets the
        # threshold — diagnosing "synthesis didn't fire" is impossible
        # without visibility into the accumulator contents and the
        # cluster shape we computed.
        cluster_summary = sorted(
            ((suffix, len(members)) for suffix, members in clusters.items()),
            key=lambda item: -item[1],
        )
        sample_addresses = sorted(self._accumulated_unmatched)[:20]
        _LOGGER.info(
            "Remote-transmitter synthesis | accumulator_size=%d "
            "unique_suffixes=%d threshold=%d top_clusters=%s "
            "sample_addresses=%s",
            len(self._accumulated_unmatched),
            len(clusters),
            self.REMOTE_TRANSMITTER_CLUSTER_THRESHOLD,
            cluster_summary[:10],
            sample_addresses,
        )

        new_entries: dict[str, dict] = {}
        for suffix, members in clusters.items():
            if len(members) < self.REMOTE_TRANSMITTER_CLUSTER_THRESHOLD:
                continue

            transmitter_id = f"{self.REMOTE_TRANSMITTER_PREFIX}{suffix}"
            new_entries[transmitter_id] = {
                "category": "Module",
                "module_type": "remote_transmitter",
                "model": "RF Remote (synthesized)",
                "description": f"Remote Transmitter ({suffix})",
                "address": transmitter_id,
                "channels": 0,
                "channels_count": 0,
                "transmitter_suffix": suffix,
                "transmitter_member_count": len(members),
                "discovered": True,
            }

            for bus_address in sorted(set(members)):
                if bus_address in self.discovered_devices:
                    # A real button enrolled later in the scan
                    # already covers this address — don't shadow.
                    continue
                new_entries[bus_address] = {
                    "category": "Button",
                    "device_type": "RT",  # synthetic marker
                    "model": "Remote Code",
                    "description": f"Remote {bus_address}",
                    "discovered_name": f"Remote {bus_address}",
                    "address": bus_address,
                    "channels": 1,
                    "channels_count": 1,
                    "module_type": "other_module",
                    "discovered": True,
                    # The merge layer keys off
                    # ``remote_transmitter_address`` to use the
                    # passthrough branch: op-point bus_address is
                    # set from ``remote_transmitter_bus_address``
                    # directly. HA-side renderer parents the device
                    # under the synthetic transmitter via the same
                    # provenance field.
                    "remote_transmitter_address": transmitter_id,
                    "remote_transmitter_suffix": suffix,
                    "remote_transmitter_bus_address": bus_address,
                }

            _LOGGER.info(
                "Synthesized remote transmitter | suffix=%s "
                "member_count=%d transmitter_id=%s",
                suffix,
                len(members),
                transmitter_id,
            )

        if new_entries:
            self.discovered_devices.update(new_entries)

    async def _finalize_inventory_phase(self) -> None:
        """Finalize the PC-Link inventory phase."""
        self._cancel_inventory_timeout()
        _LOGGER.debug("Entering _finalize_inventory_phase. Stage: %s", self.discovery_stage)

        # Stage 1: we have inventory addresses but haven't queued identity/register queries yet
        if self.discovery_stage == "inventory_addresses" and self._inventory_addresses:
            pending_addresses = self._inventory_addresses - self._inventory_identity_queued
            if pending_addresses:
                _LOGGER.debug("Found pending inventory addresses, queuing identity queries.")
                await self._run_inventory_identity_queries(pending_addresses)
                self._inventory_identity_queued.update(pending_addresses)
                self.discovery_stage = "inventory_identity"
                self._schedule_inventory_timeout()
                return
            else:
                _LOGGER.debug("No pending inventory addresses. Moving directly to Stage 2.")
                self.discovery_stage = "inventory_identity"

        # Stage 1.6: synthesise PC-Logic logical-input "virtual buttons".
        # PC-Logic doesn't enumerate its inputs in PC-Link's $1011
        # inventory frames — the input bus addresses are computed by
        # firmware from the PC-Logic's own module address (see
        # ``derive_pc_logic_input_physicals`` in protocol.py for the
        # formula). Add them to ``discovered_devices`` as
        # ``category="Button"`` entries so the regular merge layer
        # writes them into the button store as 2-channel virtual
        # buttons, parented under the PC-Logic module.
        self._synthesize_pc_logic_inputs()

        # Stage 2: inventory complete -> persist results
        _LOGGER.debug("Starting updates for module and button data.")
        try:
            if self._module_data is not None:
                merge_discovered_modules(
                    self._module_data, self.discovered_devices
                )
                _LOGGER.debug("Finished merge_discovered_modules.")
                if self._on_module_save is not None:
                    await self._on_module_save()
                    _LOGGER.debug("Finished on_module_save callback.")
            if self._button_data is not None:
                merge_discovered_buttons(
                    self._button_data,
                    self.discovered_devices,
                    KEY_MAPPING,
                    convert_nikobus_address,
                )
                _LOGGER.debug("Finished merge_discovered_buttons.")
                if self._on_button_save is not None:
                    await self._on_button_save()
                    _LOGGER.debug("Finished on_button_save callback.")
        except Exception:
            _LOGGER.error("Error during inventory finalization", exc_info=True)
            raise

        _LOGGER.info(
            "PC Link inventory scan finished | discovered=%d",
            len(self.discovered_devices),
        )

        _LOGGER.debug(
            "DUMP OF DISCOVERED DEVICES:\n%s",
            json.dumps(self.discovered_devices, indent=2)
        )

        _LOGGER.info(
            "PC Link inventory phase completed. Module discovery is manual; stopping here."
        )

        # End discovery here (do not chain into register_scan automatically)
        await self._complete_discovery_run(None)
        return

    async def _run_inventory_identity_queries(self, addresses: set[str]) -> None:
        await self._emit_progress(PHASE_IDENTITY)
        for address in sorted(addresses):
            bus_order_address = address[2:4] + address[:2]

            _LOGGER.debug(
                "PC Link inventory enumeration starting | address=%s bus=%s",
                address,
                bus_order_address,
            )

            for reg in range(0xA0, 0x100):
                payload = f"10{bus_order_address}{reg:02X}04"
                pc_link_command = make_pc_link_inventory_command(payload)

                _LOGGER.debug(
                    "PC Link inventory key queued | address=%s bus=%s reg=%02X",
                    address,
                    bus_order_address,
                    reg,
                )
                await self._coordinator.nikobus_command.queue_command(pc_link_command)

    async def _start_next_register_scan(self) -> None:
        if not self._register_scan_queue:
            _LOGGER.info("All modules in queue have been scanned.")
            await self._complete_discovery_run(None)
            return

        next_module = self._register_scan_queue.pop(0)
        normalized_address = self.normalize_module_address(
            next_module, source="register_scan_queue"
        )
        _LOGGER.info(
            "Discovery started | module=%s (Remaining in queue: %d)",
            normalized_address,
            len(self._register_scan_queue)
        )
        # Reset per-module state so the next queued module is re-classified
        # from scratch. Otherwise _module_type carries over from the previous
        # scan and the wrong decoder runs on the current module's data.
        self._module_type = None
        self._module_channels = None
        self._module_found_data = False
        self._module_consecutive_empties = 0
        self._scan_response_index = 0
        self._coordinator.discovery_running = True
        self._coordinator.discovery_module = True
        self._coordinator.discovery_module_address = normalized_address
        self._progress_module_index += 1
        await self._emit_progress(
            PHASE_REGISTER_SCAN, module_address=normalized_address
        )
        await self.query_module_inventory(normalized_address, from_queue=True)

    async def _complete_discovery_run(self, resolved_address: str | None) -> None:
        self._cancel_inventory_timeout()
        _LOGGER.info("Discovery finished")
        await self._emit_progress(PHASE_FINALIZING)

        # Cluster-synthesis pass for unmatched references collected
        # across the per-module scans. Multi-page Easywave remotes
        # emit dozens of distinct bus codes from one physical
        # transmitter, none of which appear in PC-Link inventory.
        # The decoders see them in module BP cells, the merge layer
        # logs them as unmatched and skips the link record. Here we
        # cluster the unmatched set by 4-hex suffix, synthesise a
        # virtual transmitter parent + passthrough children for any
        # cluster meeting the threshold, and re-run the merges so
        # the previously-skipped link records resolve.
        if self._button_data is not None and self._accumulated_unmatched:
            pre_synth_count = len(self.discovered_devices)
            self._synthesize_remote_transmitters_from_unmatched()
            synthesised = len(self.discovered_devices) - pre_synth_count
            if synthesised:
                try:
                    merge_discovered_buttons(
                        self._button_data,
                        self.discovered_devices,
                        KEY_MAPPING,
                        convert_nikobus_address,
                    )
                    # Re-run link merge with the accumulated
                    # command_mapping so the previously-unmatched
                    # records resolve to the newly-synthesised
                    # children. dedup in merge_linked_modules keeps
                    # already-resolved entries idempotent.
                    (
                        updated_buttons,
                        links_added,
                        outputs_added,
                        _residual_unmatched,
                    ) = merge_linked_modules(
                        self._button_data,
                        self._accumulated_command_mapping,
                    )
                    _LOGGER.info(
                        "Remote-transmitter cluster synthesis | "
                        "new_devices=%d updated_buttons=%d "
                        "links_added=%d outputs_added=%d "
                        "residual_unmatched=%d",
                        synthesised,
                        updated_buttons,
                        links_added,
                        outputs_added,
                        len(_residual_unmatched),
                    )
                    if self._on_button_save is not None:
                        await self._on_button_save()
                except Exception:  # pragma: no cover - defensive
                    _LOGGER.exception(
                        "Remote-transmitter post-synthesis merge failed"
                    )

        # Capture state for the callback's kwargs (Bug 1 fix per
        # Nikobus-HA #319). The consumer's callback runs in the same
        # async flow and may re-enter the library (e.g., to call
        # ``detect_stale_inventory``); we don't want it racing with
        # us clearing instance state.
        captured_devices = dict(self.discovered_devices)
        captured_query_type = getattr(
            self._coordinator, "inventory_query_type", None
        )

        # Split reset: flip the "discovery in progress" flags BEFORE
        # the callback so consumer re-entry into the library is
        # unguarded. Per fdebrus's design note: the integration's
        # post-discovery reconciliation calls back into
        # ``detect_stale_inventory`` from within this callback, and
        # ``discovery_running=True`` would trip any "already running"
        # guard the consumer adds.
        self._coordinator.discovery_running = False
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None

        try:
            await _notify_discovery_finished(
                self,
                discovered_devices=captured_devices,
                inventory_query_type=captured_query_type,
            )
        finally:
            # Clear instance state AFTER the callback returns. By
            # this point the consumer has either snapshotted what it
            # needed (via the kwargs) or completed any synchronous
            # work it wanted to do.
            self.discovered_devices = {}
            self._coordinator.inventory_query_type = None
            # Internal scan state (payload buffer, register queue,
            # etc.) — flags already flipped above, so don't re-flip.
            self.reset_state(update_flags=False)

    async def start_inventory_discovery(self):
        self.reset_state(update_flags=False)
        self.discovered_devices = {}
        self.discovery_stage = "inventory_addresses"
        self._coordinator.discovery_module = False
        self._coordinator.discovery_module_address = None
        self._coordinator.discovery_running = True
        self._coordinator.inventory_query_type = InventoryQueryType.PC_LINK
        self._progress_module_index = 0
        self._progress_module_total = 0
        self._progress_register_total = 0
        self._progress_decoded_records = 0
        _LOGGER.info("PC Link inventory enumeration started")
        _LOGGER.debug("Queueing PC Link inventory command #A")
        await self._coordinator.nikobus_command.queue_command("#A")
        self._schedule_inventory_timeout()
        await self._emit_progress(PHASE_INVENTORY)

    # Output-bearing module types: only these respond predictably to
    # the ``$1012<addr>`` status query that ``detect_stale_inventory``
    # uses as a presence probe. PC Link / PC Logic / feedback / audio
    # / modular interface either ARE the bridge or don't respond
    # uniformly, so they're excluded from the probe pass.
    _BUS_PROBE_MODULE_TYPES: frozenset[str] = frozenset({
        "switch_module",
        "dimmer_module",
        "roller_module",
    })

    async def detect_stale_inventory(
        self,
        *,
        outer_attempts: int = 1,
        outer_delay: float = 0.0,
    ) -> dict[str, list[str]]:
        """Cross-check Module-category entries against bus presence.

        Use case: a user with a second-hand PC-Link sees records from
        the previous owner's installation in their inventory dump.
        Niko's PC software writes new programming on top of old, but
        unused register slots aren't auto-zeroed, so any module /
        button records the new install doesn't overwrite stay present
        in PC-Link flash. There's no on-wire signal that distinguishes
        stale from current — the only reliable check is "does the
        device actually respond on the bus?"

        For each output-bearing module address in
        ``coordinator.dict_module_data``, call ``get_output_state``,
        which is the standard ``$1012<addr>`` query. That call's
        internal retry policy (``MAX_ATTEMPTS=3`` with 5 s per
        attempt) IS the per-module retry budget — present modules
        get up to 3 wire attempts to ACK, absent modules consume
        ~15 s of processor time before classifying as absent.

        Probes run **serially** in queue order. A module slow to ACK
        does not starve subsequent probes (Bug 2 / Nikobus-HA #319
        regression: prior versions wrapped each probe in
        ``asyncio.wait_for`` with a 2 s outer cap, racing the queue's
        natural retry budget and false-negativing real modules whose
        first wire attempt got blocked behind an absent-module's
        15 s inner retry loop).

        Buttons aren't probed directly (they only emit on press), but
        when a button's ``linked_modules`` block points only at stale
        modules, it's flagged as orphaned — the link table says it
        drives nothing real. Buttons with no ``linked_modules`` at
        all are NOT flagged: discovery may not have reached them yet,
        or they may genuinely have no programmed routing today.

        Returns a manifest the caller (typically the HA integration)
        decides what to do with — surface in UI, auto-purge, etc. The
        library doesn't mutate the persisted stores; the caller does.

        Args:
            outer_attempts: Number of full-sweep passes over all
                yet-unclassified modules. Defaults to 1 (single
                pass — the command layer's own retry budget is
                usually enough). Each outer pass re-probes every
                module not yet classified ``present``; modules
                that ACK'd on an earlier pass are skipped. Set to 2
                or more to give modules an extra chance after the
                bus has had a chance to quiesce — useful on heavily
                loaded installs where a transient bus jam can cause
                all 3 inner wire attempts to land in the same busy
                window.
            outer_delay: Sleep between outer passes, in seconds.
                Defaults to 0.0. Skipped after the final pass. Use
                in tandem with ``outer_attempts`` to let bus
                contention clear between probe rounds — e.g.
                ``outer_attempts=2, outer_delay=3.0``.

        Returns:
            Dict with four lists, all sorted, addresses upper-case:
              - ``checked``: every address probed
              - ``present_modules``: probes that ACK'd
              - ``absent_modules``: probes that failed
              - ``orphaned_buttons``: buttons whose entire
                ``linked_modules`` set sits inside ``absent_modules``
        """

        empty: dict[str, list[str]] = {
            "checked": [],
            "present_modules": [],
            "absent_modules": [],
            "orphaned_buttons": [],
        }

        nikobus_command = getattr(self._coordinator, "nikobus_command", None)
        if nikobus_command is None or not hasattr(
            nikobus_command, "get_output_state"
        ):
            _LOGGER.warning(
                "detect_stale_inventory: coordinator has no nikobus_command "
                "with get_output_state; returning empty manifest"
            )
            return empty

        addresses: list[str] = []
        bucket = getattr(self._coordinator, "dict_module_data", {}) or {}
        if isinstance(bucket, dict):
            for module_type, modules in bucket.items():
                if module_type not in self._BUS_PROBE_MODULE_TYPES:
                    continue
                if isinstance(modules, dict):
                    for addr in modules:
                        if addr:
                            addresses.append(str(addr).upper())
                elif isinstance(modules, list):
                    for entry in modules:
                        if isinstance(entry, dict) and entry.get("address"):
                            addresses.append(str(entry["address"]).upper())

        addresses = sorted(set(addresses))

        present_set: set[str] = set()
        absent_last_reason: dict[str, str] = {}

        outer_passes = max(1, int(outer_attempts))
        outer_pause = max(0.0, float(outer_delay))

        for outer in range(1, outer_passes + 1):
            remaining = [a for a in addresses if a not in present_set]
            if not remaining:
                break

            for addr in remaining:
                # No outer ``asyncio.wait_for`` here — see docstring.
                # ``get_output_state`` waits the command layer's own
                # natural retry budget (MAX_ATTEMPTS × per-attempt
                # timeout) then either returns or raises. The queue
                # is held for the full duration of each probe so
                # commands don't race each other into stale-future
                # territory.
                try:
                    await nikobus_command.get_output_state(addr, group=1)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    absent_last_reason[addr] = type(exc).__name__
                else:
                    _LOGGER.debug(
                        "Bus presence probe | addr=%s status=present "
                        "outer=%d/%d",
                        addr,
                        outer,
                        outer_passes,
                    )
                    present_set.add(addr)
                    absent_last_reason.pop(addr, None)

            # Pause before the next outer pass (only if more passes
            # remain AND at least one module is still unclassified).
            if outer < outer_passes and outer_pause > 0:
                still_remaining = [a for a in addresses if a not in present_set]
                if still_remaining:
                    await asyncio.sleep(outer_pause)

        present: list[str] = sorted(present_set)
        absent: list[str] = []
        for addr in addresses:
            if addr in present_set:
                continue
            reason = absent_last_reason.get(addr, "timeout")
            _LOGGER.info(
                "Bus presence probe | addr=%s status=absent reason=%s "
                "outer_passes=%d",
                addr,
                reason,
                outer_passes,
            )
            absent.append(addr)

        orphaned: list[str] = []
        if absent and self._button_data is not None:
            absent_set = set(absent)
            buttons = self._button_data.get("nikobus_button") or {}
            entries: list[tuple[str, dict]] = []
            if isinstance(buttons, dict):
                for phys_addr, button in buttons.items():
                    if isinstance(button, dict):
                        entries.append((str(phys_addr), button))
            elif isinstance(buttons, list):
                for button in buttons:
                    if isinstance(button, dict) and button.get("address"):
                        entries.append((str(button["address"]), button))

            for phys_addr, button in entries:
                target_addrs: set[str] = set()
                op_points = button.get("operation_points") or {}
                if not isinstance(op_points, dict):
                    continue
                for op in op_points.values():
                    if not isinstance(op, dict):
                        continue
                    links = op.get("linked_modules") or []
                    if not isinstance(links, list):
                        continue
                    for block in links:
                        if (
                            isinstance(block, dict)
                            and block.get("module_address")
                        ):
                            target_addrs.add(
                                str(block["module_address"]).upper()
                            )
                if target_addrs and target_addrs.issubset(absent_set):
                    orphaned.append(phys_addr.upper())

        manifest = {
            "checked": addresses,
            "present_modules": sorted(present),
            "absent_modules": sorted(absent),
            "orphaned_buttons": sorted(set(orphaned)),
        }

        _LOGGER.info(
            "Stale-inventory probe complete | checked=%d present=%d "
            "absent=%d orphaned_buttons=%d",
            len(manifest["checked"]),
            len(manifest["present_modules"]),
            len(manifest["absent_modules"]),
            len(manifest["orphaned_buttons"]),
        )

        return manifest

    def handle_device_address_inventory(self, message: str) -> None:
        # Signal the sequential scan loop first. A $18 frame that hits
        # this handler during a register scan is either an (unexpected)
        # address-inventory record or an end-of-memory trailer; either
        # way the scan loop needs to wake.
        self._notify_scan_frame(message)
        clean_message = message.strip("\x02\x03\r\n")
        marker_index = clean_message.find(DEVICE_ADDRESS_INVENTORY)
        if marker_index == -1:
            _LOGGER.debug(
                "Inventory record ignored | reason=missing_marker message=%s",
                message,
            )
            return
        start_index = marker_index + len(DEVICE_ADDRESS_INVENTORY)
        raw_address = (clean_message[start_index : start_index + 4] or "").upper()

        # Validate the signature byte before treating this as a PC-Link
        # response. Both PC-Link (0x50) and PC-Logic (0x40) reply to
        # the broadcast ``#A`` query; if PC-Logic wins the race, our
        # subsequent inventory reads would target the wrong controller
        # and come back empty. The signature byte is at payload offset
        # 1 (after the address bytes and a leading 0x00 padding byte):
        # ``$18 <addr_lo><addr_hi> 00 <sig> 0F 3F FF <crc>``.
        signature_hex = (
            clean_message[start_index + 6 : start_index + 8] or ""
        ).upper()
        try:
            signature_byte = int(signature_hex, 16) if signature_hex else None
        except ValueError:
            signature_byte = None
        if (
            signature_byte is not None
            and signature_byte != PC_LINK_INVENTORY_SIGNATURE_BYTE
        ):
            _LOGGER.warning(
                "Inventory record rejected | reason=non_pc_link_signature "
                "raw=%s signature=0x%02X (expected 0x%02X — PC-Link); "
                "this responder is most likely a PC-Logic answering #A "
                "before the PC-Link did. Verify a PC-Link (model 0A) "
                "is present on the bus.",
                raw_address,
                signature_byte,
                PC_LINK_INVENTORY_SIGNATURE_BYTE,
            )
            return

        normalized = self.normalize_module_address(
            raw_address, source="device_address_inventory", reverse_bus_order=True
        )
        is_new = normalized not in self._inventory_addresses
        self._inventory_addresses.add(normalized)
        _LOGGER.debug(
            "Inventory record | raw=%s normalized=%s", raw_address, normalized
        )
        _LOGGER.debug("Inventory record | address=%s", normalized)
        self._ensure_pc_link_address(normalized, source="device_address_inventory")
        if is_new and self.discovery_stage == "inventory_addresses":
            self._create_task(
                self._queue_inventory_identity_queries_for_address(normalized)
            )
        self._schedule_inventory_timeout()

    async def _queue_inventory_identity_queries_for_address(self, address: str) -> None:
        if address in self._inventory_identity_queued:
            return
        await self._run_inventory_identity_queries({address})
        self._inventory_identity_queued.add(address)

    def _ensure_pc_link_address(self, address: str, *, source: str) -> None:
        if not address:
            return

        existing = self.discovered_devices.get(address)
        if existing and existing.get("module_type") != "pc_link":
            _LOGGER.debug(
                "Skipping PC Link address record | address=%s reason=existing_module_type",
                address,
            )
            return

        coordinator_modules = getattr(self._coordinator, "dict_module_data", {}) or {}
        known_pc_links = coordinator_modules.get("pc_link") or {}
        if known_pc_links and address not in known_pc_links:
            _LOGGER.debug(
                "Skipping PC Link address record | address=%s reason=known_pc_link_present source=%s",
                address,
                source,
            )
            return

        pc_link_info = DEVICE_TYPES.get("0A", {})
        name = pc_link_info.get("Name", "PC-Link")
        model = pc_link_info.get("Model", "05-200")
        last_seen = datetime.now(timezone.utc).isoformat()
        module_type = get_module_type_from_device_type("0A")
        base_device = {
            "description": name,
            "discovered_name": name,
            "category": "Module",
            "device_type": "0A",
            "model": model,
            "address": address,
            "channels": 0,
            "channels_count": 0,
            "module_type": module_type,
            "discovered": True,
            "last_discovered": last_seen,
        }
        if existing:
            existing.update(base_device)
        else:
            self.discovered_devices[address] = base_device

        _LOGGER.debug(
            "PC Link address recorded | address=%s source=%s",
            address,
            source,
        )

    async def query_module_inventory(
        self,
        device_address,
        *,
        from_queue: bool = False,
        register_start: int | None = None,
        register_end: int | None = None,
        sub_byte: str | None = None,
    ):
        """Scan a module's register space.

        Production mode (no range params): pick the per-module-type
        register range from ``_scan_range_for_sub`` and run the
        configured extra passes from ``_EXTRA_SCAN_SUBS_BY_MODULE_TYPE``.
        Non-output module types (``feedback_module``, ``other_module``,
        ``interface_module``, ``audio_module``) early-return without
        scanning.

        Forensic mode (``register_start`` and ``register_end`` both
        provided): scan **only** the specified range with the given
        ``sub_byte`` (default ``"04"``). Skip the extra-pass logic and
        bypass the non-output-module guard so any module — including
        ones the production path declines to scan — can be inspected.
        Useful for reverse-engineering storage layouts: e.g.
        ``register_start=0x70, register_end=0x83, sub_byte="01"``
        targets the BP-cell region a vendor trace revealed.
        """

        if register_start is None and register_end is None:
            custom_range_mode = False
        elif register_start is None or register_end is None:
            raise ValueError(
                "query_module_inventory: register_start and register_end "
                "must both be provided (or both omitted)"
            )
        else:
            if not (0 <= register_start <= 0xFF):
                raise ValueError(
                    f"register_start 0x{register_start:X} out of range 0x00..0xFF"
                )
            if not (0 <= register_end <= 0xFF):
                raise ValueError(
                    f"register_end 0x{register_end:X} out of range 0x00..0xFF"
                )
            if register_end < register_start:
                raise ValueError(
                    f"register_end (0x{register_end:X}) must be >= "
                    f"register_start (0x{register_start:X})"
                )
            custom_range_mode = True

        if isinstance(device_address, str) and device_address.strip().upper() == "ALL":
            if custom_range_mode or sub_byte is not None:
                raise ValueError(
                    "query_module_inventory: register range / sub_byte "
                    "overrides are not compatible with ALL mode; supply "
                    "a specific module address"
                )
            all_addresses = []
            dict_data = getattr(self._coordinator, "dict_module_data", {})
            for module_type, modules in dict_data.items():
                if module_type not in NON_OUTPUT_MODULE_TYPES:
                    module_iter = modules.values() if isinstance(modules, dict) else modules
                    for module in module_iter:
                        addr = module.get("address") if isinstance(module, dict) else None
                        if addr:
                            all_addresses.append(addr)

            if not all_addresses:
                _LOGGER.warning(
                    "No output modules found in config to scan (dict_module_data keys=%s)",
                    list(dict_data.keys()) if isinstance(dict_data, dict) else type(dict_data).__name__,
                )
                self.reset_state()
                return

            _LOGGER.info("Starting sequential discovery queue for ALL output modules: %s", all_addresses)
            self.discovery_stage = "register_scan"
            self._register_scan_queue = all_addresses
            self._progress_module_total = len(all_addresses)
            self._progress_module_index = 0
            await self._start_next_register_scan()
            return

        normalized_address = self.normalize_module_address(
            device_address, source="query_module_inventory"
        )

        self.discovery_stage = self.discovery_stage or "register_scan"
        base_command = f"10{normalized_address}"
        self._module_address = normalized_address
        self._coordinator.inventory_query_type = InventoryQueryType.MODULE

        discovered_device = self.discovered_devices.get(normalized_address, {})

        if not self._coordinator.discovery_module:
            _LOGGER.info("Discovery started | module=%s", normalized_address)
            if not from_queue:
                self._coordinator.discovery_running = True
                # Single-module entry — seed progress for a queue of one.
                self._progress_module_total = 1
                self._progress_module_index = 1
                await self._emit_progress(
                    PHASE_REGISTER_SCAN, module_address=normalized_address
                )
            self._coordinator.discovery_module = True
            self._coordinator.discovery_module_address = normalized_address

        if self._module_type is None:
            self._module_type = self._resolve_module_type(
                normalized_address, discovered_device
            )

        # ``pc_logic`` is intentionally NOT in this set as of 0.4.11:
        # PC-Logic (05-201) holds the BP-cell connection table that
        # forwards button presses to output modules in heavily-routed
        # installs. Without scanning it, output-module flash records
        # that reference PC-Logic-synthesized addresses can't be
        # resolved and ``linked_modules`` ends up empty for those
        # buttons. The PcLogicDecoder is currently a logging stub
        # (Stage 1 instrumentation); see CHANGELOG 0.4.11.
        is_output_module = self._module_type not in NON_OUTPUT_MODULE_TYPES

        coordinator_channels = (
            self._coordinator.get_module_channel_count(normalized_address)
            if self._is_known_module_address(normalized_address)
            else 0
        )
        discovered_channels = discovered_device.get("channels")
        self._module_channels = next(
            (count for count in (coordinator_channels, discovered_channels) if count is not None),
            None,
        )

        if self._coordinator.discovery_module:
            base_command = f"10{normalized_address[2:4] + normalized_address[:2]}"
            if self._module_type == "dimmer_module":
                base_command = f"22{normalized_address[2:4] + normalized_address[:2]}"
            # Per-pass register range is picked below from
            # _SCAN_REGISTER_RANGE_BY_SUB; this placeholder is only
            # used by the non-output-module early-return path below.
            command_range = None
        else:
            command_range = range(0xA4, 0x100)

        # Forensic mode: user supplied an explicit register range.
        # Bypass the per-module-type tuning and the non-output-module
        # guard, scan exactly the range they asked for, and stop.
        if custom_range_mode:
            effective_sub = (sub_byte or "04").upper()
            forensic_range = range(register_start, register_end + 1)
            _LOGGER.info(
                "Forensic register scan | module=%s function=%s sub=%s "
                "range=0x%02X..0x%02X (custom range mode — extra passes skipped)",
                normalized_address,
                base_command[:2],
                effective_sub,
                forensic_range.start,
                forensic_range.stop - 1,
            )
            await self._scan_module_registers(
                normalized_address,
                base_command,
                forensic_range,
                sub_byte=effective_sub,
            )
            await self._finalize_discovery(normalized_address)
            return

        if not is_output_module:
            _LOGGER.debug(
                "Skipping register scan for non-output module | module=%s type=%s",
                normalized_address,
                self._module_type,
            )
            if self.discovery_stage == "inventory":
                return

            await self._finalize_discovery(normalized_address)
            return

        # 0.16.0 vendor-aligned scan plan: for every output module AND
        # PC-Logic, scan the exact (sub=00, sub=01, sub=04) register
        # lists the PC software reads per the 2026-05-08 trace. No
        # per-firmware widening — full vendor alignment.
        #
        # PC-Link's scan profile is different (controller-side module
        # registry, not per-module link table); it's handled in the
        # ``_PC_LINK_REGISTERS`` branch above.
        #
        # When ``broad_scan=True`` is set on this discovery instance,
        # the plan appends the legacy sub=04 0x00..0x3F sweep as an
        # extra pass after the vendor primary — safety net for any
        # firmware revision whose link table doesn't sit in the
        # vendor band.
        scan_subs = _scan_subs_for_module_type(
            self._module_type, broad_scan=self._broad_scan
        )
        if not scan_subs:
            # Module type not in the vendor plan (e.g. PC-Link is
            # handled by its own branch, audio/feedback/interface
            # have no link table at all). Fall through to finalize.
            await self._finalize_discovery(normalized_address)
            return

        for sub_byte in scan_subs:
            function_code = base_command[:2]
            register_list = _scan_registers_for_sub(
                sub_byte, module_type=self._module_type
            )
            wire_sub = _wire_sub_byte(sub_byte)
            _LOGGER.debug(
                "Register scan pass starting | module=%s function=%s "
                "sub=%s wire_sub=%s registers=%d",
                normalized_address,
                function_code,
                sub_byte,
                wire_sub,
                len(register_list),
            )
            await self._scan_module_registers(
                normalized_address,
                base_command,
                register_list,
                sub_byte=wire_sub,
            )

        await self._finalize_discovery(normalized_address)

    async def parse_inventory_response(self, payload) -> InventoryResult | None:
        result = InventoryResult()
        try:
            self.discovery_stage = self.discovery_stage or "inventory"
            if payload.startswith("$") and "$" in payload[1:]:
                payload = payload.split("$")[-1]
            payload = payload.lstrip("$")
            payload_bytes = bytes.fromhex(payload)

            _LOGGER.debug(
                "Inventory raw frame | length=%d hex=%s",
                len(payload_bytes),
                payload_bytes.hex().upper(),
            )

            # --- FIX 1: The data payload starts at byte 3 ---
            data_bytes = payload_bytes[3:19] if len(payload_bytes) >= 19 else payload_bytes[3:]

            self._schedule_inventory_timeout()

            # All-FF response = no record at this slot. Skip and continue
            # the sweep — don't treat it as end-of-project. Real installs
            # have FF gaps mid-project (e.g. user deleted a module and
            # the slot got zero-erased) and the pre-0.5.13 single-FF
            # terminator dropped every record past such a gap. Residue
            # filtering moved to ``detect_stale_inventory`` (0.5.16) +
            # the HA-side button reconciliation, which operate on actual
            # bus presence rather than inferring from register patterns.
            if bool(data_bytes) and all(b == 0xFF for b in data_bytes):
                _LOGGER.debug(
                    "Empty PC Link registry block (FFFF...) detected. "
                    "Skipping to next."
                )
                return result

            if len(payload_bytes) < 15:
                _LOGGER.debug(
                    "Discovery skipped | reason=payload_too_short length=%d",
                    len(payload_bytes),
                )
                return result

            device_type_hex = f"{payload_bytes[7]:02X}"

            if device_type_hex == "FF":
                _LOGGER.debug(
                    "Discovery skipped | type=inventory module=%s reason=empty_register",
                    self._module_address,
                )
                return result

            device_info = classify_device_type(device_type_hex, DEVICE_TYPES)
            category = device_info.get("Category") or "Module"
            name = device_info.get("Name") or "Unknown"
            model = device_info.get("Model") or "N/A"
            channels = device_info.get("Channels", 0) or 0
            slice_end = 13 if category == "Module" else 14
            raw_address = payload_bytes[11:slice_end].hex().upper()
            converted_address = self.normalize_module_address(
                raw_address,
                source="device_address_inventory",
                reverse_bus_order=True,
            )

            # --- FIX: Skip deleted or uninitialized memory slots ---
            if converted_address in ("FFFF", "FFFFFF"):
                _LOGGER.debug(
                    "Discovery skipped | reason=deleted_or_empty_address address=%s type=%s",
                    converted_address,
                    device_type_hex
                )
                return result
            # -------------------------------------------------------

            if device_info.get("Category", "Unknown") == "Unknown":
                if device_type_hex not in self._unknown_device_types_warned:
                    self._unknown_device_types_warned.add(device_type_hex)
                    _LOGGER.warning(
                        "Unknown device detected: Type %s at Address %s. "
                        "Please open an issue on https://github.com/fdebrus/Nikobus-HA/issues with this information.",
                        device_type_hex,
                        converted_address,
                    )
                else:
                    _LOGGER.debug(
                        "Unknown device detected (deduped): Type %s at Address %s",
                        device_type_hex,
                        converted_address,
                    )

            module_type = get_module_type_from_device_type(device_type_hex)
            if module_type == "pc_link":
                _LOGGER.info(
                    "PC Link detected during inventory enumeration | address=%s",
                    converted_address,
                )

            last_seen = datetime.now(timezone.utc).isoformat()
            device_entry = {
                "description": name,
                "discovered_name": name,
                "category": category,
                "device_type": device_type_hex,
                "model": model,
                "address": converted_address,
                "channels": channels,
                "channels_count": channels,
                "module_type": module_type,
                "discovered": True,
                "last_discovered": last_seen,
            }

            if category == "Button":
                result.buttons.append(device_entry)
            else:
                result.modules.append(device_entry)

            # Store device directly
            self.discovered_devices[converted_address] = device_entry

            _LOGGER.debug(
                "Inventory classification | module_address=%s device_type=%s module_type=%s "
                "model=%s channels=%s raw_type_byte=0x%02X raw_addr_bytes=%s",
                converted_address,
                device_type_hex,
                module_type,
                model,
                channels,
                payload_bytes[7] if len(payload_bytes) > 7 else 0,
                payload_bytes[11:slice_end].hex().upper() if len(payload_bytes) >= slice_end else "",
            )

            _LOGGER.info(
                "Discovered %s - %s, Model: %s, at Address: %s",
                category,
                name,
                model,
                converted_address,
            )
            return result
        except Exception:
            _LOGGER.error("Failed to parse Nikobus payload", exc_info=True)
            self.reset_state()
            return None

    async def parse_module_inventory_response(self, message):
        # Wake the sequential scan loop as soon as a data/trailer frame
        # arrives. Parsing the frame still runs below; this hook only
        # signals the scan coordinator.
        self._notify_scan_frame(message)

        # --- Route PC-Link frames to the correct parser ---
        if self._coordinator.inventory_query_type == InventoryQueryType.PC_LINK:
            await self.parse_inventory_response(message)
            return
        # --------------------------------------------------

        try:
            matched_header = next(
                (h for h in DEVICE_INVENTORY_ANSWER if message.startswith(h)), None
            )
            if not matched_header:
                return

            frame_body = message[len(matched_header) :]

            if len(frame_body) < 4:
                return

            address_segment = frame_body[:4].upper()
            address = reverse_hex(address_segment)
            payload_and_crc = frame_body[4:]

            self._module_address = address

            if self._module_type is None:
                discovered = self.discovered_devices.get(address, {})
                self._module_type = self._resolve_module_type(address, discovered)

            coordinator_channels = (
                self._coordinator.get_module_channel_count(address)
                if self._is_known_module_address(address)
                else 0
            )
            discovered_channels = self.discovered_devices.get(address, {}).get("channels")
            self._module_channels = next(
                (count for count in (coordinator_channels, discovered_channels) if count is not None),
                None,
            )

            decoder = self._get_decoder()
            if decoder is None:
                _LOGGER.error("No decoder available for module type: %s", self._module_type)
                self._schedule_timeout()
                return

            if hasattr(decoder, "set_module_address"):
                decoder.set_module_address(address)
            if hasattr(decoder, "set_module_channel_count"):
                decoder.set_module_channel_count(self._module_channels)

            analysis = decoder.analyze_frame_payload(self._payload_buffer, payload_and_crc)
            if analysis is None:
                self._schedule_timeout()
                return

            self._module_address = address
            self._payload_buffer = analysis["remainder"]
            self._scan_response_index += 1
            response_index = self._scan_response_index

            _LOGGER.debug(
                "Register scan response | module=%s response_index=%d frame_hex=%s "
                "buffered_chunks=%d remainder_len=%d",
                address,
                response_index,
                payload_and_crc.upper(),
                len(analysis["chunks"]),
                len(analysis["remainder"]),
            )

            decoded_commands: list[DecodedCommand] = []
            for chunk in analysis["chunks"]:
                normalized_chunk = chunk.strip().upper()
                if not normalized_chunk:
                    continue
                _LOGGER.debug(
                    "Discovery relationship chunk | module=%s response_index=%d chunk=%s",
                    address,
                    response_index,
                    normalized_chunk,
                )
                if all(c == "F" for c in normalized_chunk):
                    # All-F chunks are the controller's "no record at this
                    # register" sentinel. Length depends on module type
                    # (12 hex for switch/roller, 16 for dimmer, 32 for PC
                    # Link / PC Logic), so we check by content rather than
                    # against a single fixed string.
                    _LOGGER.debug(
                        "Discovery relationship empty chunk detected | module=%s response_index=%d chunk=%s",
                        address,
                        response_index,
                        normalized_chunk,
                    )
                    # Just skip the empty chunk, do NOT abort the scan!
                    continue

                decoded_commands.extend(
                    decoder.decode(normalized_chunk, module_address=address)
                )

            if decoded_commands:
                await self._handle_decoded_commands(address, decoded_commands)

            if await self._check_early_termination(address, bool(decoded_commands)):
                return

            if not self._coordinator.discovery_module:
                await self._finalize_discovery(address)
            else:
                self._schedule_timeout()

        except Exception:
            _LOGGER.error("Failed to parse module inventory response", exc_info=True)
            self.reset_state()

    async def _handle_decoded_commands(
        self, module_address: str | None, decoded_commands: list[DecodedCommand]
    ):
        # Count successfully-decoded records for the progress tracker.
        # Each DecodedCommand that makes it this far represents one real
        # link; the button-store merge further down may deduplicate, but
        # the on-wire reality is "we saw this many records."
        if isinstance(decoded_commands, list):
            self._progress_decoded_records += sum(
                1 for c in decoded_commands if isinstance(c, DecodedCommand)
            )
        # Build IR receiver lookup from the current in-memory button store
        # so that split_ir_button_address and decode_ir_channel work for
        # any IR receiver, not just hardcoded prefixes.
        ir_receiver_lookup = None
        if self._button_data is not None:
            buttons = self._button_data.get("nikobus_button") or {}
            if isinstance(buttons, dict):
                ir_receiver_lookup = build_ir_receiver_lookup(buttons) or None

        new_commands = []
        command_mapping = {}

        for command in decoded_commands:
            if not isinstance(command, DecodedCommand):
                continue

            decoded = command.metadata or {}

            if decoded.get("push_button_address") is None and decoded.get("button_address") is not None:
                decoded["push_button_address"] = decoded.get("button_address")

            if decoded.get("push_button_address") is None and decoded.get("button_address") is None:
                continue

            new_commands.append(decoded)

            if module_address:
                add_to_command_mapping(command_mapping, decoded, module_address, ir_receiver_lookup)

        self._decoded_buffer = {
            "module_address": module_address,
            "commands": new_commands,
            "command_mapping": command_mapping,
        }

        _LOGGER.debug(
            "Discovery decoded commands | module=%s count=%d",
            self._decoded_buffer["module_address"],
            len(self._decoded_buffer["commands"]),
        )

        if self._button_data is None:
            return

        (
            updated_buttons,
            links_added,
            outputs_added,
            unmatched,
        ) = merge_linked_modules(self._button_data, command_mapping)
        # Accumulate unmatched references and the originating command
        # mapping across module scans so we can run the remote-
        # transmitter cluster-synthesis pass at end-of-discovery.
        if unmatched:
            self._accumulated_unmatched.update(unmatched)
        if command_mapping:
            for key, outputs in command_mapping.items():
                bucket = self._accumulated_command_mapping.setdefault(key, [])
                for output in outputs:
                    if output not in bucket:
                        bucket.append(output)
        # Only log at INFO when something actually merged; routine
        # no-op merges (the common case on re-discovery) stay at DEBUG.
        if updated_buttons or links_added or outputs_added:
            _LOGGER.info(
                "Discovered links merged into store: %d buttons updated, %d link blocks added, %d outputs added.",
                updated_buttons,
                links_added,
                outputs_added,
            )
        else:
            _LOGGER.debug(
                "Discovered links merged into store: %d buttons updated, %d link blocks added, %d outputs added.",
                updated_buttons,
                links_added,
                outputs_added,
            )
        if self._on_button_save is not None and (
            updated_buttons or links_added or outputs_added
        ):
            await self._on_button_save()


def run_decoder_harness(coordinator):
    """Lightweight harness to exercise discovery decoders without full HA runtime."""

    sample_messages = [
        "$0522$1E6C0E5F1550000300B4FF452CA9",  # dimmer frame with expected 16-hex chunk
        "5F1550000300B4FF",  # raw chunk form
    ]

    decoders = [DimmerDecoder(coordinator), SwitchDecoder(coordinator), ShutterDecoder(coordinator)]
    for message in sample_messages:
        _LOGGER.info("HARNESS message=%s", message)
        for decoder in decoders:
            results = decoder.decode(message)
            if not results:
                continue
            for result in results:
                _LOGGER.info(
                    "HARNESS decoder=%s payload_len=%s chunk_len=%s payload=%s metadata=%s",
                    decoder.module_type,
                    len(result.payload_hex) if result.payload_hex else "?",
                    len(result.chunk_hex) if result.chunk_hex else "?",
                    result.payload_hex,
                    result.metadata,
                )
