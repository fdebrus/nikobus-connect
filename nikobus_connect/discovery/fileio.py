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
        _LOGGER.info("Data written to file: %s", file_path)
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


async def update_module_data(file_path, discovered_devices):
    """Create or merge the integration module config from discovery results.

    Parameters
    ----------
    file_path : str
        Absolute path to the module config JSON file.
    discovered_devices : dict
        Mapping of address -> device info dicts from discovery.
    """

    def _ensure_inventory(data: dict | None) -> dict[str, list]:
        data = data or {}

        def _as_list(value):
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return [item for item in value.values() if isinstance(item, dict)]
            return []

        inventory: dict[str, list] = {}
        for module_type, modules in data.items():
            inventory[module_type] = _as_list(modules)

        for module_type in MODULE_TYPE_ORDER:
            inventory.setdefault(module_type, [])

        return inventory

    def _default_channel(module_type: str, index: int) -> dict:
        channel = {"description": f"not_in_use output_{index}"}
        if module_type == "roller_module":
            channel["operation_time_up"] = "30"
        return channel

    def _sanitize_channel(module_type: str, channel: dict, index: int) -> dict:
        # Preserve any existing user keys (entity_type, led_on/off, etc.)
        sanitized: dict = dict(channel) if isinstance(channel, dict) else {}

        # Normalize / guarantee description
        sanitized["description"] = (sanitized.get("description") or "").strip()

        # Normalize / guarantee operation_time_up only for roller_module
        if module_type == "roller_module":
            op = sanitized.get("operation_time_up", "30")
            sanitized["operation_time_up"] = str(op) if op not in (None, "") else "60"
        else:
            # If other module types accidentally carry operation_time_up, keep it or drop it:
            # safest is to keep it as-is (do nothing), so we don't destroy user data.
            pass

        return sanitized

    def _build_channels(module_type: str, channels: list, channels_count: int) -> list:
        if channels_count <= 0:
            return []

        sanitized_channels: list[dict] = []
        channels_list = channels if isinstance(channels, list) else []

        for idx in range(channels_count):
            if idx < len(channels_list):
                sanitized_channel = _sanitize_channel(
                    module_type, channels_list[idx], idx + 1
                )
            else:
                sanitized_channel = _default_channel(module_type, idx + 1)

            sanitized_channels.append(sanitized_channel)

        return sanitized_channels

    def _sanitize_discovered_info(info: dict | None) -> dict:
        info = info or {}
        sanitized: dict = {}
        for key in ("name", "device_type", "channels_count"):
            if key not in info:
                continue
            value = info.get(key)
            if value in (None, ""):
                continue
            if key == "channels_count":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
                if value <= 0:
                    continue
            sanitized[key] = value
        return sanitized

    def _canonical_module(module: dict, module_type: str) -> dict:
        address = _normalize_address(module.get("address"))
        description = module.get("description", "") or ""
        model = module.get("model", "") or ""
        discovered_info = _sanitize_discovered_info(module.get("discovered_info"))

        channels_from_discovery = discovered_info.get("channels_count")
        fallback_channel_count = module.get(
            "channels_count", len(module.get("channels", []))
        )
        try:
            fallback_channel_count = int(fallback_channel_count)
        except (TypeError, ValueError):
            fallback_channel_count = None

        if channels_from_discovery is None:
            channels_from_discovery = (
                fallback_channel_count if fallback_channel_count is not None else 0
            )
            if channels_from_discovery > 0:
                discovered_info["channels_count"] = channels_from_discovery

        channels_list = _build_channels(
            module_type, module.get("channels", []), channels_from_discovery or 0
        )

        canonical = {
            "description": description,
            "model": model,
            "address": address,
        }

        if channels_list:
            canonical["channels"] = channels_list

        # Append discovered_info at the bottom
        canonical["discovered_info"] = discovered_info

        return canonical

    def _inventory_to_map(inventory: dict[str, list]) -> dict[str, dict]:
        mapped: dict[str, dict] = {}
        for module_type, modules in inventory.items():
            module_lookup: dict[str, dict] = {}
            for module in modules:
                address = _normalize_address(module.get("address"))
                if not address:
                    continue
                module_lookup[address] = _canonical_module(module, module_type)
            mapped[module_type] = module_lookup
        return mapped

    def _generate_description(module_type: str, module_lookup: dict[str, dict]) -> str:
        prefix = DESCRIPTION_PREFIX.get(module_type, f"{module_type}_")
        existing = {module.get("description") for module in module_lookup.values()}
        counter = 1
        candidate = f"{prefix}{counter}"
        while candidate in existing:
            counter += 1
            candidate = f"{prefix}{counter}"
        return candidate

    def _refresh_discovered_info(channels_count: int, device: dict) -> dict:
        discovery_info = {
            "name": device.get("discovered_name") or device.get("description", ""),
            "device_type": device.get("device_type"),
        }
        if channels_count > 0:
            discovery_info["channels_count"] = channels_count
        return discovery_info

    filtered_devices = [
        device
        for device in discovered_devices.values()
        if device.get("category") == "Module"
    ]

    existing_data = await read_json_file(file_path)

    inventory = _ensure_inventory(existing_data)
    inventory_map = _inventory_to_map(inventory)

    for device in filtered_devices:
        module_type = device.get("module_type", "unknown_module")
        address = _normalize_address(device.get("address"))
        if not address:
            continue

        module_lookup = inventory_map.setdefault(module_type, {})

        channels_count = device.get("channels_count")
        if channels_count is None:
            channels_count = device.get("channels") or 0
        try:
            channels_count = int(channels_count)
        except (TypeError, ValueError):
            channels_count = 0

        discovered_info = _refresh_discovered_info(channels_count, device)

        existing_module = module_lookup.get(address)
        if existing_module:
            description = existing_module.get("description") or _generate_description(
                module_type, module_lookup
            )
            model_value = existing_module.get("model")
            discovered_model = device.get("model", "")
            if not model_value or (
                discovered_model and discovered_model != model_value
            ):
                model_value = discovered_model
            channels = existing_module.get("channels", [])
        else:
            description = _generate_description(module_type, module_lookup)
            model_value = device.get("model", "")
            channels = []

        updated_module = {
            "description": description,
            "model": model_value,
            "address": address,
        }

        if channels_count > 0:
            updated_module["channels"] = _build_channels(
                module_type, channels, channels_count
            )

        # Append discovered_info at the bottom
        updated_module["discovered_info"] = discovered_info

        module_lookup[address] = updated_module

    # Rebuild inventory lists with canonical ordering and sanitization
    ordered_inventory: dict[str, list] = {}
    for module_type in MODULE_TYPE_ORDER:
        module_entries = inventory_map.get(module_type, {})
        ordered_inventory[module_type] = [
            _canonical_module(module, module_type)
            for module in module_entries.values()
        ]

    for module_type, modules in inventory_map.items():
        if module_type in MODULE_TYPE_ORDER:
            continue
        ordered_inventory[module_type] = [
            _canonical_module(module, module_type) for module in modules.values()
        ]

    await write_json_file(file_path, ordered_inventory, inline_channels=True)


async def update_button_data(file_path, discovered_devices, key_mapping, convert_nikobus_address):
    """Update the button config JSON file based on discovered devices.

    Parameters
    ----------
    file_path : str
        Absolute path to the button config JSON file.
    discovered_devices : dict
        Mapping of address -> device info dicts from discovery.
    key_mapping : dict
        KEY_MAPPING from mapping module.
    convert_nikobus_address : callable
        Address conversion function.
    """
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    existing_data = []
    if os.path.exists(file_path):
        existing_json = await read_json_file(file_path)
        if existing_json:
            existing_data = existing_json.get("nikobus_button", [])
            if not isinstance(existing_data, list):
                existing_data = []

    updated_data = existing_data.copy()
    lookup = {
        button.get("address"): button
        for button in updated_data
        if "address" in button
    }

    for device_address, device in discovered_devices.items():
        if device.get("category") != "Button":
            continue
        description = device.get("description", "")
        model = device.get("model", "")
        num_channels = device.get("channels", 0)
        # Key labeling logic
        if num_channels == 1:
            keys = ["1A"]
        elif num_channels == 2:
            keys = ["1A", "1B"]
        elif num_channels == 4:
            keys = ["1A", "1B", "1C", "1D"]
        elif num_channels == 8:
            keys = ["1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"]
        else:
            _LOGGER.error("Unexpected number of channels: %d for device %s", num_channels, device_address)
            continue
        mapping = key_mapping.get(num_channels, {})
        channels_data = {}
        converted_address = convert_nikobus_address(device_address)
        try:
            original_nibble = int(converted_address[0], 16)
        except (ValueError, IndexError):
            _LOGGER.error("Invalid converted address for device %s: %s", device_address, converted_address)
            continue
        for idx, key in enumerate(keys, start=1):
            if key in mapping:
                add_value = int(mapping[key], 16)
                new_nibble_value = (original_nibble + add_value) & 0xF
                new_nibble_hex = f"{new_nibble_value:X}"
                updated_addr = new_nibble_hex + converted_address[1:]
                channels_data[f"channel_{idx}"] = {
                    "key": key,
                    "address": updated_addr,
                }
        device["channels_data"] = channels_data
        for channel_info in channels_data.values():
            discovered_channel_address = channel_info["address"]
            key = channel_info["key"]
            new_info = {
                "type": description,
                "model": model,
                "address": device_address,
                "channels": num_channels,
                "key": key,
            }
            button = lookup.get(discovered_channel_address)
            if button:
                discovered_list = button.setdefault("linked_button", [])
                found_info = next(
                    (
                        info
                        for info in discovered_list
                        if info.get("key") == new_info["key"]
                        and info.get("address") == new_info["address"]
                    ),
                    None,
                )
                if found_info:
                    if (
                        found_info.get("type") != new_info["type"]
                        or found_info.get("model") != new_info["model"]
                        or found_info.get("channels") != new_info["channels"]
                    ):
                        found_info.update(
                            {
                                "type": new_info["type"],
                                "model": new_info["model"],
                                "channels": new_info["channels"],
                            }
                        )
                else:
                    discovered_list.append(new_info)
            else:
                new_button = {
                    "description": f"{description} #N{discovered_channel_address}",
                    "address": discovered_channel_address,
                    "impacted_module": [{"address": "", "group": ""}],
                    "linked_button": [new_info],
                }
                updated_data.append(new_button)
                lookup[discovered_channel_address] = new_button

    output_json = {"nikobus_button": updated_data}
    await write_json_file(file_path, output_json)


def _normalize_key(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


async def merge_linked_modules(file_path, command_mapping):
    """Merge discovery command mapping into nikobus_button_config.json.

    Parameters
    ----------
    file_path : str
        Absolute path to the button config JSON file.
    command_mapping : dict
        Mapping of button keys to output definitions.

    Notes:
        - linked_modules blocks are grouped by module_address only.
        - Supports command_mapping keys:
            (push_button_address, key_raw)              [legacy]
            (push_button_address, key_raw, ir_code)     [IR-aware]
        - We do NOT persist key/key_raw in linked_modules; key identity is tracked in linked_button.
        - For IR receivers, the bus-emitted address is stored in linked_button[].address.
          Therefore we must match button entries by:
            - top-level button["address"] (legacy), OR
            - any linked_button[].address (IR / bus identity)
        - If a button is not found in the JSON, it is skipped to prevent ghost/garbage buttons.
    """

    file_exists_before = os.path.exists(file_path)
    file_size_before = os.path.getsize(file_path) if file_exists_before else 0
    _LOGGER.info("Updating button config JSON: %s", file_path)
    _LOGGER.info(
        "Button config JSON stats before update: cwd=%s exists=%s size=%s bytes",
        os.getcwd(),
        file_exists_before,
        file_size_before,
    )

    existing_json = await read_json_file(file_path)
    if existing_json is None:
        existing_json = {"nikobus_button": []}

    buttons = existing_json.get("nikobus_button", [])
    if not isinstance(buttons, list):
        buttons = []

    def _unpack_mapping_key(mapping_key):
        """Return (push_button_address, key_raw, ir_code)."""
        if not isinstance(mapping_key, tuple):
            return mapping_key, None, None
        if len(mapping_key) == 2:
            return mapping_key[0], mapping_key[1], None
        if len(mapping_key) == 3:
            return mapping_key[0], mapping_key[1], mapping_key[2]
        return mapping_key[0] if mapping_key else None, None, None

    def _rebuild_address_lookup() -> tuple[
        dict[str, dict],
        dict[tuple[str, int], dict],
        dict[str, int],
    ]:
        """Map resolvable addresses to their button entries.

        Returns (address_lookup, keyed_lookup, ir_base_lookup):

        - address_lookup: address -> button_entry for top-level button
          addresses and linked_button entries without a resolvable key.
        - keyed_lookup: (address, key_raw) -> button_entry for
          linked_button entries whose ``key`` field can be resolved.
          This lets us distinguish multiple logical buttons that share
          the same physical wall button.
        - ir_base_lookup: 4-char IR prefix -> base byte.
        """
        lookup: dict[str, dict] = {}
        keyed_lookup: dict[tuple[str, int], dict] = {}
        ir_base_lookup: dict[str, int] = {}

        for button in buttons:
            if not isinstance(button, dict):
                continue

            top_addr = _normalize_address(button.get("address"))
            if top_addr:
                lookup.setdefault(top_addr, button)

            linked_button = button.get("linked_button", [])
            if isinstance(linked_button, list):
                for info in linked_button:
                    if not isinstance(info, dict):
                        continue
                    di_addr = _normalize_address(info.get("address"))
                    if not di_addr:
                        continue

                    channels = info.get("channels")
                    key_raw = _key_label_to_raw(channels, info.get("key"))

                    # When we can resolve the specific key, use the
                    # key-specific lookup exclusively.  Populating the
                    # general address_lookup here would cause shared wall
                    # buttons to leak commands to siblings with different
                    # keys (e.g. cover outputs being attributed to the
                    # light button sharing the same wall button).
                    if key_raw is not None:
                        keyed_lookup.setdefault((di_addr, key_raw), button)
                    else:
                        lookup.setdefault(di_addr, button)

                    # Link the secondary MAC address for 8-channel switches
                    if channels == 8 and len(di_addr) == 6:
                        try:
                            base_int = int(di_addr, 16)
                            if base_int < 0xFFFFFF:
                                shifted_addr = f"{(base_int + 1):06X}"
                                if key_raw is not None:
                                    keyed_lookup.setdefault(
                                        (shifted_addr, key_raw), button
                                    )
                                else:
                                    lookup.setdefault(shifted_addr, button)
                        except ValueError:
                            _LOGGER.debug("Invalid hex address in linked_button: %s", di_addr)

                    # Track IR receiver base addresses for slot resolution
                    btn_type = info.get("type", "")
                    if "IR" in btn_type and len(di_addr) == 6:
                        try:
                            prefix = di_addr[:4]
                            base_byte = int(di_addr[-2:], 16)
                            ir_base_lookup.setdefault(prefix, base_byte)
                        except ValueError:
                            pass

        return lookup, keyed_lookup, ir_base_lookup

    def _ensure_button_entry_for_address(
        address_lookup: dict[str, dict],
        keyed_lookup: dict[tuple[str, int], dict],
        ir_base_lookup: dict[str, int],
        normalized_address: str,
        key_raw=None,
        ir_code=None,
    ) -> dict | None:
        """Return existing button entry, or drop it if it's a ghost/garbage."""
        # Key-specific lookup takes priority so shared wall buttons route
        # each command to the correct logical button based on key_raw.
        if key_raw is not None:
            keyed_entry = keyed_lookup.get((normalized_address, key_raw))
            if keyed_entry is not None:
                return keyed_entry

        existing = address_lookup.get(normalized_address)
        if existing:
            return existing

        # Try resolving as an IR slot: replace last byte with the IR base byte
        if len(normalized_address) == 6:
            prefix = normalized_address[:4]
            base_byte = ir_base_lookup.get(prefix)
            if base_byte is not None:
                base_addr = f"{prefix}{base_byte:02X}"
                if key_raw is not None:
                    keyed_entry = keyed_lookup.get((base_addr, key_raw))
                    if keyed_entry is not None:
                        return keyed_entry
                existing = address_lookup.get(base_addr)
                if existing:
                    return existing

        _LOGGER.debug(
            "Ignored ghost link or garbage memory chunk for unknown button: %s (key_raw=%s)",
            normalized_address,
            key_raw,
        )
        return None

    address_lookup, keyed_lookup, ir_base_lookup = _rebuild_address_lookup()

    updated_buttons = 0
    links_added = 0
    outputs_added = 0
    any_updates = False
    matched_addresses: set[str] = set()
    unmatched_addresses: set[str] = set()

    for mapping_key, outputs in command_mapping.items():
        push_button_address, key_raw, ir_code_from_key = _unpack_mapping_key(mapping_key)
        if push_button_address is None:
            continue

        normalized_address = _normalize_address(push_button_address)
        if not normalized_address:
            continue

        if not isinstance(outputs, list) or not outputs:
            continue

        # IMPORTANT: if not found, drop it to avoid ghost buttons.
        button_entry = _ensure_button_entry_for_address(
            address_lookup,
            keyed_lookup,
            ir_base_lookup,
            normalized_address,
            key_raw=key_raw,
            ir_code=ir_code_from_key,
        )

        if button_entry is None:
            unmatched_addresses.add(normalized_address)
            continue

        linked_modules = button_entry.setdefault("linked_modules", [])
        if not isinstance(linked_modules, list):
            linked_modules = []
            button_entry["linked_modules"] = linked_modules

        updated_entry = False
        matched_addresses.add(normalized_address)

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

            # Backward compatible physical identity
            button_address = output.get("button_address")

            # Optional IR identity
            ir_button_address = output.get("ir_button_address")
            ir_code = output.get("ir_code") or ir_code_from_key

            # Match block by module_address only (as before)
            matching_block = next(
                (
                    block
                    for block in linked_modules
                    if isinstance(block, dict) and block.get("module_address") == module_address
                ),
                None,
            )

            if matching_block is None:
                matching_block = {"module_address": module_address, "outputs": []}
                linked_modules.append(matching_block)
                links_added += 1
                updated_entry = True

            existing_outputs = matching_block.get("outputs", [])
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

            # Only persist IR fields when present
            if ir_button_address:
                output_entry["ir_button_address"] = ir_button_address
            if ir_code:
                output_entry["ir_code"] = ir_code

            # Dedupe must include IR identity; otherwise IR variants collapse
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
                matching_block["outputs"] = existing_outputs
                outputs_added += 1
                updated_entry = True

        if updated_entry:
            # Sort blocks by module address
            linked_modules.sort(
                key=lambda block: (block.get("module_address", "") if isinstance(block, dict) else "")
            )

            for block in linked_modules:
                if not isinstance(block, dict):
                    continue
                block_outputs = block.get("outputs", [])
                if not isinstance(block_outputs, list):
                    block_outputs = []
                block_outputs.sort(
                    key=lambda out: (
                        out.get("channel") if isinstance(out, dict) and out.get("channel") is not None else -1,
                        out.get("mode", "") if isinstance(out, dict) else "",
                        (out.get("ir_code", "") or "") if isinstance(out, dict) else "",
                        (out.get("ir_button_address", "") or "") if isinstance(out, dict) else "",
                    )
                )
                block["outputs"] = block_outputs

            updated_buttons += 1
            any_updates = True
        else:
            # Exists but nothing new (all deduped)
            pass

    if any_updates:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        await _write_json_atomic(file_path, {"nikobus_button": buttons})

    file_exists_after = os.path.exists(file_path)
    file_size_after = os.path.getsize(file_path) if file_exists_after else 0
    _LOGGER.info(
        "Button config JSON stats after update: exists=%s size=%s bytes",
        file_exists_after,
        file_size_after,
    )

    if not any_updates:
        _LOGGER.info(
            "Button config JSON updater ran: changes=0 (updated_buttons=%d, links_added=%d, outputs_added=%d)",
            updated_buttons,
            links_added,
            outputs_added,
        )
    else:
        _LOGGER.info(
            "Button config JSON summary: updated_buttons=%d, links_added=%d, outputs_added=%d",
            updated_buttons,
            links_added,
            outputs_added,
        )

    if not matched_addresses and unmatched_addresses:
        unmatched_sample = list(unmatched_addresses)[:5]
        _LOGGER.debug(
            "Button config JSON updater found no matching buttons. unmatched_count=%d sample=%s",
            len(unmatched_addresses),
            unmatched_sample,
        )

    return updated_buttons, links_added, outputs_added
