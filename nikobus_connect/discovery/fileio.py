import asyncio
import json
import logging
import os
import tempfile

from .mapping import KEY_MAPPING, KEY_MAPPING_MODULE

_LOGGER = logging.getLogger(__name__)

MODULE_TYPE_ORDER = [
    "switch_module",
    "dimmer_module",
    "roller_module",
    "pc_link",
    "pc_logic",
    "feedback_module",
    "other_module",
]

DESCRIPTION_PREFIX = {
    "switch_module": "switch_module_s",
    "dimmer_module": "dimmer_module_d",
    "roller_module": "roller_module_r",
    "pc_link": "pc_link_",
    "pc_logic": "pc_logic_",
    "feedback_module": "feedback_module_",
    "other_module": "other_module_",
}


def _inline_channels(json_text: str) -> str:
    """Collapse channel objects to a single line while preserving indentation."""

    def _is_simple_object(block_lines: list[str]) -> bool:
        if len(block_lines) < 3:
            return False

        closing = block_lines[-1].lstrip()
        if not (closing.startswith("}") or closing.startswith("},")):
            return False

        inner = block_lines[1:-1]

        # We removed the length restriction here so that objects with
        # multiple keys (like shutters) still inline perfectly!
        return not any("{" in line or "}" in line for line in inner)

    lines = json_text.splitlines()
    output: list[str] = []
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        if line.strip() == "{" and idx + 2 < len(lines):
            block: list[str] = [line]
            cursor = idx + 1
            while cursor < len(lines):
                block.append(lines[cursor])
                if lines[cursor].lstrip().startswith("}"):
                    break
                cursor += 1

            if _is_simple_object(block):
                indent = line[: line.index("{")]
                inner_content = " ".join(part.strip() for part in block[1:-1])
                closing = block[-1].strip()
                inline = f"{indent}{{ {inner_content} }}"
                if closing.startswith("},"):
                    inline += ","
                output.append(inline)
                idx = cursor + 1
                continue

        output.append(line)
        idx += 1

    return "\n".join(output) + ("\n" if json_text.endswith("\n") else "")


async def _write_json_atomic(file_path, data, inline_channels: bool = False):
    """Write JSON data atomically to avoid partial writes."""

    def _write(path):
        serialized = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=False)
        if inline_channels:
            serialized = _inline_channels(serialized)
        with open(path, "w", encoding="utf-8") as file:
            file.write(serialized)

    directory = os.path.dirname(file_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix="tmp_", suffix=".json")
    os.close(fd)
    try:
        await asyncio.to_thread(_write, tmp_path)
        os.replace(tmp_path, file_path)
        _LOGGER.debug("Data written to file: %s", file_path)
    except Exception:
        _LOGGER.exception("Failed to write data to file %s", file_path)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


async def write_json_file(file_path, data, inline_channels: bool = False):
    """Write JSON data to a file asynchronously."""
    await _write_json_atomic(file_path, data, inline_channels=inline_channels)


async def read_json_file(file_path):
    """Read JSON data from a file asynchronously. Returns dict or None on error."""
    if not os.path.exists(file_path):
        return None

    def _read(path):
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    try:
        return await asyncio.to_thread(_read, file_path)
    except Exception:
        _LOGGER.error("Failed to read data from file %s", file_path, exc_info=True)
        return None


def _normalize_address(address):
    return address.strip().upper() if isinstance(address, str) else ""


def _key_label_to_raw(channels, key_label):
    """Convert a linked_button key label (e.g. "1C") to the raw key index.

    A physical wall button can have multiple operation points; each point
    triggers a different logical button.  linked_button[].key stores the
    operation point as a human label ("1A".."2D") while module registers
    encode the key as a raw index (0..7).  Mapping the label back to the
    raw index lets us disambiguate multiple logical buttons that share the
    same physical wall button.

    Returns None if the label cannot be resolved for the given channel count.
    """
    if not isinstance(key_label, str):
        return None
    label = key_label.strip()
    if not label:
        return None

    try:
        ch_int = int(channels) if channels is not None else None
    except (TypeError, ValueError):
        return None

    label_map = KEY_MAPPING.get(ch_int)
    raw_map = KEY_MAPPING_MODULE.get(ch_int)
    if not label_map or not raw_map:
        return None

    hex_char = label_map.get(label)
    if hex_char is None:
        return None

    for key_raw, h in raw_map.items():
        if h == hex_char:
            return key_raw
    return None


def _key_raw_to_label(channels, key_raw):
    """Inverse of :func:`_key_label_to_raw`: raw index (0..7) -> label ("1A".."2D").

    Returns None if the raw index can't be resolved for the given channel count.
    """

    try:
        ch_int = int(channels) if channels is not None else None
    except (TypeError, ValueError):
        return None

    raw_map = KEY_MAPPING_MODULE.get(ch_int)
    label_map = KEY_MAPPING.get(ch_int)
    if not raw_map or not label_map:
        return None

    hex_char = raw_map.get(key_raw)
    if hex_char is None:
        return None

    for label, h in label_map.items():
        if h == hex_char:
            return label
    return None


def merge_discovered_modules(module_data, discovered_devices):
    """Merge discovery results into the caller-owned module store in place.

    Option-A shape (keyed by physical address, parallel to the button
    store)::

        {"nikobus_module": {
            "<address>": {
                "module_type": "switch_module" | "dimmer_module" | ...,
                "description": "<user-editable name>",
                "model": "<hw model>",
                "channels": [
                    {"description": "...", "entity_type": "light",
                     "led_on": ..., "led_off": ...,
                     "operation_time_up": ..., "operation_time_down": ...},
                    ...
                ],
                "discovered_info": {"name", "device_type", "channels_count"},
            }
        }}

    User-owned fields (``description`` at both module and channel level,
    ``entity_type``, ``led_on``/``led_off``, ``operation_time_*``) are
    preserved verbatim — discovery never overwrites them. Discovery
    owns ``model``, ``address``, ``discovered_info``, the
    ``module_type`` bucket, and defaults for channels newly appended
    beyond the previous ``channels_count``.

    Parameters
    ----------
    module_data : dict
        The caller-owned live store (mutated in place).
    discovered_devices : dict
        ``address -> device`` mapping from discovery.
    """

    modules = module_data.setdefault("nikobus_module", {})
    if not isinstance(modules, dict):
        modules = {}
        module_data["nikobus_module"] = modules

    def _default_channel(module_type: str, index: int) -> dict:
        channel = {"description": f"not_in_use output_{index}"}
        if module_type == "roller_module":
            channel["operation_time_up"] = "30"
        return channel

    def _pad_channels(
        module_type: str, existing: list, channels_count: int
    ) -> list:
        """Return a channel list of length ``channels_count``.

        Keeps every existing entry verbatim (user fields preserved);
        appends defaults for any index beyond the current length. Never
        shrinks — if discovery reports fewer channels than the store
        has, extras stay (safer than dropping user data).
        """

        if channels_count <= 0:
            return list(existing) if isinstance(existing, list) else []
        out: list[dict] = []
        source = existing if isinstance(existing, list) else []
        for idx in range(max(channels_count, len(source))):
            if idx < len(source) and isinstance(source[idx], dict):
                out.append(source[idx])
            else:
                out.append(_default_channel(module_type, idx + 1))
        return out

    def _refresh_discovered_info(channels_count: int, device: dict) -> dict:
        info = {
            "name": device.get("discovered_name") or device.get("description", ""),
            "device_type": device.get("device_type"),
        }
        if channels_count > 0:
            info["channels_count"] = channels_count
        return info

    def _generate_description(module_type: str) -> str:
        prefix = DESCRIPTION_PREFIX.get(module_type, f"{module_type}_")
        existing = {
            m.get("description")
            for m in modules.values()
            if isinstance(m, dict) and m.get("module_type") == module_type
        }
        counter = 1
        while f"{prefix}{counter}" in existing:
            counter += 1
        return f"{prefix}{counter}"

    updated = 0
    added = 0

    for device in discovered_devices.values():
        if not isinstance(device, dict):
            continue
        if device.get("category") != "Module":
            continue

        address = _normalize_address(device.get("address"))
        if not address:
            continue

        module_type = device.get("module_type", "other_module")

        channels_count = device.get("channels_count")
        if channels_count is None:
            channels_count = device.get("channels") or 0
        try:
            channels_count = int(channels_count)
        except (TypeError, ValueError):
            channels_count = 0

        existing = modules.get(address)
        if isinstance(existing, dict):
            # Refresh discovery-owned fields only; keep everything else.
            existing["module_type"] = module_type
            discovered_model = device.get("model", "") or ""
            if discovered_model and (
                not existing.get("model")
                or existing.get("model") != discovered_model
            ):
                existing["model"] = discovered_model
            existing["discovered_info"] = _refresh_discovered_info(
                channels_count, device
            )
            if channels_count > 0:
                existing["channels"] = _pad_channels(
                    module_type, existing.get("channels", []), channels_count
                )
            updated += 1
        else:
            # New module — insert with generated description + defaults.
            entry: dict = {
                "module_type": module_type,
                "description": _generate_description(module_type),
                "model": device.get("model", "") or "",
                "discovered_info": _refresh_discovered_info(
                    channels_count, device
                ),
            }
            if channels_count > 0:
                entry["channels"] = _pad_channels(
                    module_type, [], channels_count
                )
            modules[address] = entry
            added += 1

    if added or updated:
        _LOGGER.info(
            "Module store merge summary: new=%d refreshed=%d", added, updated
        )
    return added, updated


def find_module(module_data: dict, address: str) -> tuple[str, dict] | None:
    """Locate a module entry by address in the Option-A module store.

    Returns ``(normalized_address, entry_dict)`` or ``None``.
    """

    if not isinstance(module_data, dict):
        return None
    modules = module_data.get("nikobus_module")
    if not isinstance(modules, dict):
        return None
    target = _normalize_address(address)
    if not target:
        return None
    entry = modules.get(target)
    if isinstance(entry, dict):
        return target, entry
    return None




_BUTTON_KEYS_BY_CHANNEL_COUNT = {
    1: ["1A"],
    2: ["1A", "1B"],
    4: ["1A", "1B", "1C", "1D"],
    8: ["1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"],
}


def _ensure_buttons_dict(button_data: dict) -> dict:
    """Return the ``nikobus_button`` dict, creating it if missing."""

    buttons = button_data.setdefault("nikobus_button", {})
    if not isinstance(buttons, dict):
        buttons = {}
        button_data["nikobus_button"] = buttons
    return buttons


def find_operation_point(
    button_data: dict, bus_address: str
) -> tuple[str, str, dict] | None:
    """Locate an operation point by its bus-emitted address.

    Returns ``(physical_address, key_label, operation_point_dict)`` or
    ``None`` if no operation point in the store declares this
    ``bus_address``. Integrations can use this to route a button-press
    event (which arrives as a bus address) to the correct op-point entry.

    Since 0.4.3 this also surfaces IR virtual op-points: each
    ``operation_points["IR:{code}"]`` entry carries a ``bus_address``
    computed at discovery time, so a runtime IR press routes the same
    way as a wall press. For those, ``key_label`` is the storage key
    (e.g. ``"IR:10B"``) rather than a wall-key name.
    """

    buttons = button_data.get("nikobus_button")
    if not isinstance(buttons, dict):
        return None

    target = _normalize_address(bus_address)
    if not target:
        return None

    for physical_addr, button in buttons.items():
        if not isinstance(button, dict):
            continue
        op_points = button.get("operation_points")
        if not isinstance(op_points, dict):
            continue
        for key_label, op_point in op_points.items():
            if not isinstance(op_point, dict):
                continue
            if _normalize_address(op_point.get("bus_address")) == target:
                return physical_addr, key_label, op_point
    return None


def find_ir_operation_point(
    button_data: dict, receiver_address: str, ir_code: str
) -> tuple[str, str, dict] | None:
    """Locate a virtual IR op-point on a given IR receiver.

    Returns ``(receiver_address, storage_key, op_point_dict)`` where
    ``storage_key`` is the ``"IR:{code}"`` entry inside the receiver's
    ``operation_points``. Returns ``None`` if the receiver is missing
    or carries no op-point for that IR code.
    """

    if not isinstance(button_data, dict):
        return None
    buttons = button_data.get("nikobus_button")
    if not isinstance(buttons, dict):
        return None

    receiver = _normalize_address(receiver_address)
    if not receiver or not ir_code:
        return None

    physical = buttons.get(receiver)
    if not isinstance(physical, dict):
        return None
    op_points = physical.get("operation_points")
    if not isinstance(op_points, dict):
        return None

    storage_key = _ir_op_point_key(ir_code)
    op_point = op_points.get(storage_key)
    if isinstance(op_point, dict):
        return receiver, storage_key, op_point
    return None


def merge_discovered_buttons(
    button_data, discovered_devices, key_mapping, convert_nikobus_address
):
    """Merge discovered Button devices into the caller-owned ``button_data``.

    Produces the Option-A physical-keyed shape::

        {"nikobus_button": {<physical_address>: {
             "type": "<button model description>",
             "model": "<hw model>",
             "channels": <int>,
             "description": "<physical button description>",
             "operation_points": {
                 "<key label>": {
                     "bus_address": "<derived bus addr>",
                     "linked_modules": [...],
                 },
                 ...
             },
         }}}

    Mutates ``button_data`` in place.
    """

    buttons = _ensure_buttons_dict(button_data)

    for device_address, device in discovered_devices.items():
        if device.get("category") != "Button":
            continue

        physical_addr = _normalize_address(device_address)
        if not physical_addr:
            continue

        description = device.get("description", "") or ""
        model = device.get("model", "") or ""
        num_channels = device.get("channels", 0)

        keys = _BUTTON_KEYS_BY_CHANNEL_COUNT.get(num_channels)
        if keys is None:
            _LOGGER.error(
                "Unexpected number of channels: %s for device %s",
                num_channels,
                physical_addr,
            )
            continue

        mapping = key_mapping.get(num_channels, {})
        converted_address = convert_nikobus_address(physical_addr)
        try:
            original_nibble = int(converted_address[0], 16)
        except (ValueError, IndexError):
            _LOGGER.error(
                "Invalid converted address for device %s: %s",
                physical_addr,
                converted_address,
            )
            continue

        generated_phys_desc = (
            f"{description} #N{physical_addr}" if description else f"#N{physical_addr}"
        )

        # Upsert the physical button record.
        phys_entry = buttons.setdefault(
            physical_addr,
            {
                "type": description,
                "model": model,
                "channels": num_channels,
                "description": generated_phys_desc,
                "operation_points": {},
            },
        )
        # Refresh physical metadata from the latest discovery.
        phys_entry["type"] = description or phys_entry.get("type") or ""
        phys_entry["model"] = model or phys_entry.get("model") or ""
        phys_entry["channels"] = num_channels or phys_entry.get("channels")
        # Only (re)generate the description when none exists or the stored
        # one is still the auto-generated form — never overwrite a custom
        # name a user may have set downstream.
        current_desc = phys_entry.get("description")
        if not current_desc or current_desc.endswith(f"#N{physical_addr}"):
            phys_entry["description"] = generated_phys_desc

        op_points = phys_entry.setdefault("operation_points", {})
        if not isinstance(op_points, dict):
            op_points = {}
            phys_entry["operation_points"] = op_points

        channels_data: dict[str, dict] = {}

        for idx, key_label in enumerate(keys, start=1):
            if key_label not in mapping:
                continue
            add_value = int(mapping[key_label], 16)
            new_nibble_value = (original_nibble + add_value) & 0xF
            new_nibble_hex = f"{new_nibble_value:X}"
            updated_addr = _normalize_address(new_nibble_hex + converted_address[1:])
            channels_data[f"channel_{idx}"] = {
                "key": key_label,
                "address": updated_addr,
            }
            generated_op_desc = f"Push button {key_label} #N{updated_addr}"
            op_point = op_points.setdefault(
                key_label,
                {"bus_address": updated_addr, "description": generated_op_desc},
            )
            op_point["bus_address"] = updated_addr
            current_op_desc = op_point.get("description")
            if not current_op_desc or current_op_desc.endswith(f"#N{updated_addr}"):
                op_point["description"] = generated_op_desc

        # Surface channel-address mapping on the discovered device so later
        # merge steps (which consume ``command_mapping`` keyed by bus
        # addresses) can correlate without recomputing the transform.
        device["channels_data"] = channels_data


def _normalize_key(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _build_bus_to_op_index(buttons: dict) -> dict[str, tuple[str, str]]:
    """Map bus_address -> (physical_address, key_label).

    Includes the +1 alias for 8-channel wall buttons: the bus traffic for
    those can arrive on either the declared bus address or its +1 sibling.
    """

    index: dict[str, tuple[str, str]] = {}
    for physical_addr, button in buttons.items():
        if not isinstance(button, dict):
            continue
        phys = _normalize_address(physical_addr)
        if not phys:
            continue
        channels = button.get("channels")
        op_points = button.get("operation_points")
        if not isinstance(op_points, dict):
            continue
        for key_label, op_point in op_points.items():
            if not isinstance(op_point, dict):
                continue
            bus_addr = _normalize_address(op_point.get("bus_address"))
            if not bus_addr:
                continue
            index.setdefault(bus_addr, (phys, key_label))
            if channels == 8 and len(bus_addr) == 6:
                try:
                    shifted = f"{(int(bus_addr, 16) + 1) & 0xFFFFFF:06X}"
                    index.setdefault(shifted, (phys, key_label))
                except ValueError:
                    _LOGGER.debug(
                        "Invalid hex bus address in operation point: %s", bus_addr
                    )
    return index


def _build_ir_base_lookup(buttons: dict) -> dict[str, int]:
    """Map 4-char IR prefix -> base byte, derived from physical IR receivers."""

    lookup: dict[str, int] = {}
    for physical_addr, button in buttons.items():
        if not isinstance(button, dict):
            continue
        if "IR" not in (button.get("type") or ""):
            continue
        addr = _normalize_address(physical_addr)
        if len(addr) != 6:
            continue
        try:
            prefix = addr[:4]
            base_byte = int(addr[-2:], 16)
        except ValueError:
            continue
        lookup.setdefault(prefix, base_byte)
    return lookup


def _resolve_operation_point(
    push_button_address: str,
    key_raw,
    buttons: dict,
    bus_to_op: dict[str, tuple[str, str]],
    ir_base_lookup: dict[str, int],
):
    """Find the (physical_addr, key_label, operation_point) tuple for a press.

    Returns ``None`` when nothing matches — the caller should drop the link
    rather than invent a ghost button.
    """

    normalized = _normalize_address(push_button_address)
    if not normalized:
        return None

    # 1) Direct physical-address match.
    physical = buttons.get(normalized)
    if isinstance(physical, dict):
        channels = physical.get("channels")
        key_label = _key_raw_to_label(channels, key_raw)
        if key_label:
            op_point = (physical.get("operation_points") or {}).get(key_label)
            if isinstance(op_point, dict):
                return normalized, key_label, op_point

    # 1a) 8-channel +1 alias fallback. Link records on dimmer / switch /
    # roller modules encode the button address as ``physical + 1`` for
    # raw key indices 4-7 of an 8-channel button. The decoder accepts
    # those via ``is_known_button_canonical``'s sibling check, but the
    # decoded ``button_address`` (and the fallback ``push_button_address``
    # in ``add_to_command_mapping`` when the channel-count lookup
    # returned None) is the +1 form. ``buttons`` is keyed by physical
    # address, so the direct match above misses; resolve to the
    # canonical-1 sibling when it's an 8-channel button.
    try:
        sibling_minus1 = f"{(int(normalized, 16) - 1) & 0xFFFFFF:06X}"
    except ValueError:
        sibling_minus1 = None
    if sibling_minus1:
        candidate = buttons.get(sibling_minus1)
        if (
            isinstance(candidate, dict)
            and candidate.get("channels") == 8
        ):
            key_label = _key_raw_to_label(8, key_raw)
            if key_label:
                op_point = (candidate.get("operation_points") or {}).get(
                    key_label
                )
                if isinstance(op_point, dict):
                    return sibling_minus1, key_label, op_point

    # 2) Bus-address match (the usual case for wall buttons).
    bus_hit = bus_to_op.get(normalized)
    if bus_hit is not None:
        phys_addr, key_label = bus_hit
        physical = buttons.get(phys_addr)
        if isinstance(physical, dict):
            op_point = (physical.get("operation_points") or {}).get(key_label)
            if isinstance(op_point, dict):
                return phys_addr, key_label, op_point

    # 3) IR-slot fallback: resolve the physical IR receiver from the prefix
    # table and pick the operation point from key_raw.
    if len(normalized) == 6:
        prefix = normalized[:4]
        base_byte = ir_base_lookup.get(prefix)
        if base_byte is not None:
            base_addr = f"{prefix}{base_byte:02X}"
            physical = buttons.get(base_addr)
            if isinstance(physical, dict):
                channels = physical.get("channels")
                key_label = _key_raw_to_label(channels, key_raw)
                if key_label:
                    op_point = (
                        physical.get("operation_points") or {}
                    ).get(key_label)
                    if isinstance(op_point, dict):
                        return base_addr, key_label, op_point

    return None


def _resolve_ir_receiver_address(
    push_button_address: str, buttons: dict, ir_base_lookup: dict[str, int]
):
    """Find the IR receiver physical address for an IR-flagged mapping entry.

    ``push_button_address`` is whatever ``add_to_command_mapping`` placed as
    the mapping-key's first element. For IR records post-0.4.3 it's the
    receiver's physical base (e.g. ``"0D1C80"``); accept slot addresses
    (e.g. ``"0D1C8A"``) and shifted-wire forms too for forward-compat.
    """

    normalized = _normalize_address(push_button_address)
    if not normalized:
        return None

    # Direct match: the mapping address is the receiver itself.
    if normalized in buttons and isinstance(buttons[normalized], dict):
        return normalized

    # Slot-address fallback: first 4 chars identify the receiver prefix.
    if len(normalized) == 6:
        prefix = normalized[:4]
        base_byte = ir_base_lookup.get(prefix)
        if base_byte is not None:
            base_addr = f"{prefix}{base_byte:02X}"
            if base_addr in buttons and isinstance(buttons[base_addr], dict):
                return base_addr

    return None


IR_OP_POINT_PREFIX = "IR:"

# Bank-ordering used by the IR receiver protocol. ``decode_ir_channel``
# picks the bank via ``_IR_BANK_CYCLE[key_raw % 4]``; we invert it here
# to go from an IR code label ("10B") back to the key_raw that
# ``KEY_MAPPING_MODULE`` expects.
_IR_BANK_TO_KEY_INDEX = {"C": 0, "A": 1, "D": 2, "B": 3}


def _ir_op_point_key(ir_code: str) -> str:
    """Storage key for an IR virtual op-point — always prefixed to avoid
    any theoretical collision with wall keys like ``1A``/``2D``."""

    return f"{IR_OP_POINT_PREFIX}{ir_code}"


def _generated_ir_description(ir_code: str) -> str:
    return f"IR code {ir_code} #I{ir_code}"


def _compute_ir_bus_address(receiver_address: str, ir_code: str):
    """Compute the runtime wire address emitted when ``ir_code`` is
    pressed on the IR receiver at ``receiver_address``.

    The encoding mirrors wall-button bus addresses: slot address =
    receiver_prefix + (receiver_base_byte + channel); the 6-hex is then
    produced by ``convert_nikobus_address`` and the first nibble is
    shifted by ``KEY_MAPPING_MODULE[4][key_index]`` where ``key_index``
    is the bank cycle inverse of the code's trailing letter.

    Returns ``None`` when inputs don't parse (never raises). Verified on
    captured trace: code ``10B`` on receiver ``0D1C80`` -> ``D44E2C``.
    """

    from .protocol import convert_nikobus_address  # local import: avoid cycle
    from .mapping import KEY_MAPPING_MODULE

    if not receiver_address or not ir_code or len(ir_code) < 2:
        return None

    receiver = receiver_address.strip().upper()
    if len(receiver) != 6:
        return None

    bank = ir_code[-1].upper()
    key_index = _IR_BANK_TO_KEY_INDEX.get(bank)
    if key_index is None:
        return None

    try:
        channel = int(ir_code[:-1])
    except ValueError:
        return None
    if channel < 1 or channel > 39:
        return None

    try:
        base_byte = int(receiver[-2:], 16)
    except ValueError:
        return None
    slot_byte = base_byte + channel
    if slot_byte > 0xFF:
        return None

    slot_addr = f"{receiver[:4]}{slot_byte:02X}"
    converted = convert_nikobus_address(slot_addr)
    if not converted or len(converted) != 6:
        return None

    mapping = KEY_MAPPING_MODULE.get(4, {})
    add_hex = mapping.get(key_index)
    if add_hex is None:
        return None

    try:
        add = int(add_hex, 16)
        orig_nib = int(converted[0], 16)
    except ValueError:
        return None

    new_nib = (orig_nib + add) & 0xF
    return f"{new_nib:X}{converted[1:]}".upper()


def _ensure_ir_op_point(
    physical_entry: dict, receiver_address: str, ir_code: str
) -> dict:
    """Return the IR virtual op-point, creating it if missing.

    Sits in ``operation_points`` next to the wall keys, keyed ``IR:{code}``.
    Carries ``ir_code`` plus a computed ``bus_address`` (the wire address
    the receiver emits when the IR code fires) so the usual
    ``find_operation_point(bus_address)`` lookup routes IR presses to the
    op-point at runtime.
    """

    op_points = physical_entry.setdefault("operation_points", {})
    if not isinstance(op_points, dict):
        op_points = {}
        physical_entry["operation_points"] = op_points

    bus_address = _compute_ir_bus_address(receiver_address, ir_code)

    storage_key = _ir_op_point_key(ir_code)
    op_point = op_points.get(storage_key)
    if not isinstance(op_point, dict):
        op_point = {
            "ir_code": ir_code,
            "description": _generated_ir_description(ir_code),
        }
        if bus_address:
            op_point["bus_address"] = bus_address
        op_points[storage_key] = op_point
        return op_point

    # Refresh ir_code + bus_address fields (both are discovery-owned,
    # deterministic and always safe to rewrite). description stays put
    # if the user renamed it.
    op_point["ir_code"] = ir_code
    if bus_address:
        op_point["bus_address"] = bus_address
    current_desc = op_point.get("description")
    auto_desc = _generated_ir_description(ir_code)
    if not current_desc or current_desc == auto_desc:
        op_point["description"] = auto_desc
    return op_point


def merge_linked_modules(button_data, command_mapping):
    """Merge a discovery ``command_mapping`` into the caller-owned ``button_data``.

    Operates on the Option-A physical-keyed shape.

    ``command_mapping`` keys are ``(push_button_address, key_raw, ir_code)``
    tuples; values are lists of output definitions produced by the
    discovery decoders.

    When a mapping entry carries an ``ir_code``, the resulting link lands
    on a virtual IR op-point (``operation_points["IR:{code}"]``) on the
    physical IR receiver, not on one of its wall keys (``1A``-``1D``).

    Returns ``(updated_buttons, links_added, outputs_added)``.
    """

    buttons = _ensure_buttons_dict(button_data)

    def _unpack_mapping_key(mapping_key):
        if not isinstance(mapping_key, tuple):
            return mapping_key, None, None
        if len(mapping_key) == 2:
            return mapping_key[0], mapping_key[1], None
        if len(mapping_key) == 3:
            return mapping_key[0], mapping_key[1], mapping_key[2]
        return (mapping_key[0] if mapping_key else None), None, None

    bus_to_op = _build_bus_to_op_index(buttons)
    ir_base_lookup = _build_ir_base_lookup(buttons)

    updated_buttons = 0
    links_added = 0
    outputs_added = 0
    any_updates = False
    matched_addresses: set[str] = set()
    unmatched_addresses: set[str] = set()

    for mapping_key, outputs in command_mapping.items():
        push_button_address, key_raw, ir_code_from_key = _unpack_mapping_key(
            mapping_key
        )
        if push_button_address is None:
            continue
        if not isinstance(outputs, list) or not outputs:
            continue

        # IR records: route to the receiver's virtual IR op-point (create
        # it if missing). The resolver's wall-key lookup would fail when
        # the receiver has no wall op-point at key_raw, but we don't
        # need one — _ensure_ir_op_point materialises the IR entry from
        # (receiver, ir_code).
        if ir_code_from_key:
            receiver_addr = _resolve_ir_receiver_address(
                push_button_address, buttons, ir_base_lookup
            )
            if receiver_addr is None:
                normalized = _normalize_address(push_button_address)
                if normalized:
                    unmatched_addresses.add(normalized)
                continue
            physical_entry = buttons.get(receiver_addr)
            if not isinstance(physical_entry, dict):
                continue
            op_point = _ensure_ir_op_point(
                physical_entry, receiver_addr, ir_code_from_key
            )
            physical_addr = receiver_addr
            matched_addresses.add(_normalize_address(push_button_address))
        else:
            resolved = _resolve_operation_point(
                push_button_address,
                key_raw,
                buttons,
                bus_to_op,
                ir_base_lookup,
            )
            if resolved is None:
                normalized = _normalize_address(push_button_address)
                if normalized:
                    unmatched_addresses.add(normalized)
                continue

            physical_addr, _key_label, op_point = resolved
            matched_addresses.add(_normalize_address(push_button_address))

        linked_modules = op_point.setdefault("linked_modules", [])
        if not isinstance(linked_modules, list):
            linked_modules = []
            op_point["linked_modules"] = linked_modules

        updated_entry = False

        for output in outputs:
            if not isinstance(output, dict):
                continue
            module_address = output.get("module_address")
            if module_address is None:
                continue

            channel_number = output.get("channel")
            mode_label = output.get("mode")
            t1_val = output.get("t1")
            t2_val = output.get("t2")
            payload_val = output.get("payload")
            button_address = output.get("button_address")
            ir_button_address = output.get("ir_button_address")
            ir_code = output.get("ir_code") or ir_code_from_key

            matching_block = next(
                (
                    block
                    for block in linked_modules
                    if isinstance(block, dict)
                    and block.get("module_address") == module_address
                ),
                None,
            )
            if matching_block is None:
                matching_block = {"module_address": module_address, "outputs": []}
                linked_modules.append(matching_block)
                links_added += 1
                updated_entry = True

            existing_outputs = matching_block.get("outputs")
            if not isinstance(existing_outputs, list):
                existing_outputs = []
                matching_block["outputs"] = existing_outputs

            output_entry = {
                "channel": channel_number,
                "mode": mode_label,
                "t1": t1_val,
                "t2": t2_val,
                "payload": payload_val,
                "button_address": button_address,
            }
            if ir_button_address:
                output_entry["ir_button_address"] = ir_button_address
            if ir_code:
                output_entry["ir_code"] = ir_code

            dedupe_key = (
                output_entry.get("channel"),
                output_entry.get("mode"),
                output_entry.get("t1"),
                output_entry.get("t2"),
                output_entry.get("ir_code"),
                output_entry.get("ir_button_address"),
            )
            existing_keys = {
                (
                    entry.get("channel"),
                    entry.get("mode"),
                    entry.get("t1"),
                    entry.get("t2"),
                    entry.get("ir_code"),
                    entry.get("ir_button_address"),
                )
                for entry in existing_outputs
                if isinstance(entry, dict)
            }
            if dedupe_key not in existing_keys:
                existing_outputs.append(output_entry)
                outputs_added += 1
                updated_entry = True

        if updated_entry:
            linked_modules.sort(
                key=lambda block: (
                    block.get("module_address", "") if isinstance(block, dict) else ""
                )
            )
            for block in linked_modules:
                if not isinstance(block, dict):
                    continue
                block_outputs = block.get("outputs") or []
                if not isinstance(block_outputs, list):
                    block_outputs = []
                block_outputs.sort(
                    key=lambda out: (
                        out.get("channel")
                        if isinstance(out, dict) and out.get("channel") is not None
                        else -1,
                        out.get("mode", "") if isinstance(out, dict) else "",
                        (out.get("ir_code", "") or "") if isinstance(out, dict) else "",
                        (out.get("ir_button_address", "") or "")
                        if isinstance(out, dict)
                        else "",
                    )
                )
                block["outputs"] = block_outputs
            updated_buttons += 1
            any_updates = True

    # merge_linked_modules is called per decoded-record batch during
    # discovery, so no-op runs are the common case. Keep the zero-change
    # path at DEBUG; surface at INFO only when something actually changed.
    if not any_updates:
        _LOGGER.debug(
            "Button store merge ran: changes=0 (updated_buttons=%d, "
            "links_added=%d, outputs_added=%d)",
            updated_buttons,
            links_added,
            outputs_added,
        )
    else:
        _LOGGER.debug(
            "Button store merge summary: updated_buttons=%d, links_added=%d, "
            "outputs_added=%d",
            updated_buttons,
            links_added,
            outputs_added,
        )

    if not matched_addresses and unmatched_addresses:
        unmatched_sample = list(unmatched_addresses)[:5]
        _LOGGER.debug(
            "Button store merge found no matching buttons. unmatched_count=%d sample=%s",
            len(unmatched_addresses),
            unmatched_sample,
        )

    mirrored = _mirror_paired_button_links(buttons)
    if mirrored:
        outputs_added += mirrored
        _LOGGER.debug(
            "Paired-button inference added %d mirrored output(s) for "
            "M01/M02 dimmer modes",
            mirrored,
        )

    return updated_buttons, links_added, outputs_added


# Pair / group keys for paired-button modes.
#
# These modes use more than one physical key per output but store only
# one link record on the module. The peer keys need inference post-scan.
#
#   Dimmer M01 ("Dim on/off (2 buttons)")  — 2 keys (on/off)
#   Dimmer M02 ("Dim on/off (4 buttons)")  — 4 keys (on/off/+/-), master=1A/2A
#   Roller M01 ("Open - stop - close")     — 2 keys (up=open, down=close;
#                                            either stops during movement)
#
# Every other mode across switch/dimmer/roller is single-key.
_TWO_BUTTON_PAIRS: dict[str, tuple[str, ...]] = {
    "1A": ("1B",),
    "1B": ("1A",),
    "1C": ("1D",),
    "1D": ("1C",),
    "2A": ("2B",),
    "2B": ("2A",),
    "2C": ("2D",),
    "2D": ("2C",),
}

# M02 master keys mirror to the rest of their row. Only fired when the
# source key is the master (1A or 2A) — a record on a non-master key is
# left alone, since we'd be guessing the role assignment.
_FOUR_BUTTON_GROUPS: dict[str, tuple[str, ...]] = {
    "1A": ("1B", "1C", "1D"),
    "2A": ("2B", "2C", "2D"),
}

# Exact mode-text matchers. Pulled from ``mapping`` so rename drift on
# either side stays in sync automatically.
from .mapping import (  # noqa: E402
    DIMMER_MODE_MAPPING,
    ROLLER_MODE_MAPPING,
    SWITCH_MODE_MAPPING,
)

_TWO_BUTTON_MODE_TEXTS: frozenset[str] = frozenset(
    {
        DIMMER_MODE_MAPPING[0],  # "M01 (Dim on/off (2 buttons))"
        ROLLER_MODE_MAPPING[0],  # "M01 (Open - stop - close)"
        SWITCH_MODE_MAPPING[0],  # "M01 (On / off)"
    }
)
_FOUR_BUTTON_MODE_TEXTS: frozenset[str] = frozenset(
    {
        DIMMER_MODE_MAPPING[1],  # "M02 (Dim on/off (4 buttons))"
    }
)


def _peers_for_mirror(source_key: str, mode_text: str) -> tuple[str, ...]:
    """Return the keys that should mirror ``source_key`` for ``mode_text``.

    Empty tuple = no mirroring (single-key mode, or non-master M02 source).
    """

    if not isinstance(mode_text, str):
        return ()
    if mode_text in _TWO_BUTTON_MODE_TEXTS:
        return _TWO_BUTTON_PAIRS.get(source_key, ())
    if mode_text in _FOUR_BUTTON_MODE_TEXTS:
        return _FOUR_BUTTON_GROUPS.get(source_key, ())
    return ()


def _output_dedupe_key(output: dict) -> tuple:
    """Same shape used by ``merge_linked_modules`` so mirrored outputs
    dedupe identically against existing entries on the peer key."""

    return (
        output.get("channel"),
        output.get("mode"),
        output.get("t1"),
        output.get("t2"),
        output.get("ir_code"),
        output.get("ir_button_address"),
    )


def _mirror_paired_button_links(buttons: dict) -> int:
    """Synthesize linked_modules entries on paired keys for M01/M02 modes.

    Walks every operation_point's outputs. When an output's mode text
    indicates a 2-button or 4-button dimmer pairing, copy that output to
    the paired peer key(s) on the same physical button. Dedupes against
    whatever's already there.

    Returns the number of mirrored output entries added.
    """

    if not isinstance(buttons, dict):
        return 0

    added = 0

    for physical_addr, entry in buttons.items():
        if not isinstance(entry, dict):
            continue
        op_points = entry.get("operation_points")
        if not isinstance(op_points, dict):
            continue

        # Snapshot keys/items first; we mutate op_point dicts in place but
        # the mapping itself doesn't change shape during the pass.
        for source_key, source_op in list(op_points.items()):
            if not isinstance(source_op, dict):
                continue
            source_modules = source_op.get("linked_modules")
            if not isinstance(source_modules, list):
                continue

            for source_block in source_modules:
                if not isinstance(source_block, dict):
                    continue
                module_address = source_block.get("module_address")
                if not module_address:
                    continue
                source_outputs = source_block.get("outputs")
                if not isinstance(source_outputs, list):
                    continue

                for source_output in source_outputs:
                    if not isinstance(source_output, dict):
                        continue
                    peers = _peers_for_mirror(
                        source_key, source_output.get("mode", "")
                    )
                    if not peers:
                        continue

                    for peer_key in peers:
                        peer_op = op_points.get(peer_key)
                        if not isinstance(peer_op, dict):
                            continue  # peer key not present on this device

                        peer_modules = peer_op.setdefault(
                            "linked_modules", []
                        )
                        if not isinstance(peer_modules, list):
                            peer_modules = []
                            peer_op["linked_modules"] = peer_modules

                        # Find or create the matching module block on
                        # the peer.
                        peer_block = next(
                            (
                                blk
                                for blk in peer_modules
                                if isinstance(blk, dict)
                                and blk.get("module_address") == module_address
                            ),
                            None,
                        )
                        if peer_block is None:
                            peer_block = {
                                "module_address": module_address,
                                "outputs": [],
                            }
                            peer_modules.append(peer_block)

                        peer_outputs = peer_block.setdefault("outputs", [])
                        if not isinstance(peer_outputs, list):
                            peer_outputs = []
                            peer_block["outputs"] = peer_outputs

                        target = _output_dedupe_key(source_output)
                        existing = {
                            _output_dedupe_key(o)
                            for o in peer_outputs
                            if isinstance(o, dict)
                        }
                        if target in existing:
                            continue

                        peer_outputs.append(dict(source_output))
                        added += 1

    return added
