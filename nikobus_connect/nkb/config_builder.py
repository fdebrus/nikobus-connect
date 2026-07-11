"""Build Home Assistant manual-config structures from a ``.nkb`` project file.

When an install has **no PC-Link** and **no manual config files**, the
integration can bootstrap its inventory from the ``.nkb``: this produces the
same ``nikobus_module_config.json`` / ``nikobus_button_config.json`` shapes
the integration loads, so they can be written to disk as a backup and used as
the inventory source.

What is / isn't derivable from the ``.nkb``:
  * modules — address, model, per-channel names  ✅ (from ``Component`` +
    ``ProductBase`` + ``Objecten``)
  * buttons — address + name for every physical button / input  ✅
  * roller ``operation_time`` — not stored per-shutter; defaulted  ⚠️
  * link records (which button drives which output) — NOT included; those
    are read from the modules by the register scan afterward.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

from ..discovery.mapping import KEY_MAPPING
from ..discovery.protocol import convert_nikobus_address
from .parser import (
    _OUTPUT_PLACEHOLDERS,
    _OUTPUT_PREFIX_RE,
    _rows,
    open_nkb_db,
)

# A single button key face in the ``.nkb`` (``A``..``D`` for 2/4-button
# plates, ``1A``..``2D`` for 8-button plates). Combos (``AB``, ``ABCD`` …)
# and output prefixes (``O01``) are excluded.
_SINGLE_KEY_RE = re.compile(r"^([12]?)([A-D])$")

# Niko reference number (``ProductBase.NikoRefNr``) → (HA category, channels)
# for the output modules the integration supports.
_MODULE_MODELS: dict[str, tuple[str, int]] = {
    "05-000-02": ("switch_module", 12),
    "05-002-02": ("switch_module", 4),
    "05-007-02": ("dimmer_module", 12),
    "05-008-02": ("dimmer_module", 4),
    "05-001-02": ("roller_module", 6),
}

# The ``.nkb`` stores only the *list* of possible roller run-times, not the
# per-shutter selection, so we default and let the user adjust.
_ROLLER_DEFAULT_OPERATION_TIME = "40"

#: Placeholder channel names (in addition to the parser's set) the software
#: writes for an un-renamed output — treated as "no real name".
_EXTRA_OUTPUT_PLACEHOLDERS = frozenset(
    {"ouput", "schakeluitgang", "rolluikuitgang", "dimuitgang"}
)


class NkbConfig(NamedTuple):
    """The two manual-config payloads derived from a ``.nkb``."""

    module_config: dict[str, list[dict[str, Any]]]
    button_config: dict[str, list[dict[str, Any]]]


def _is_placeholder_name(name: str) -> bool:
    low = name.strip().lower()
    return not low or low in _OUTPUT_PLACEHOLDERS or low in _EXTRA_OUTPUT_PLACEHOLDERS


def _key_labels(prefixes: set[str]) -> list[str]:
    """Key labels (``1A``..``2D``) for a button's single-key op-points.

    ``.nkb`` prefixes are ``A``..``D`` on 2/4-button plates (mapped to
    ``1A``..``1D``) and ``1A``..``2D`` on 8-button plates (used as-is).
    """
    labels: set[str] = set()
    for pfx in prefixes:
        m = _SINGLE_KEY_RE.match(pfx)
        if m:
            labels.add(f"{m.group(1) or '1'}{m.group(2)}")
    return sorted(labels)


def _channels_for(labels: list[str]) -> int:
    """Key count (1/2/4/8) whose ``KEY_MAPPING`` contains every label.

    Picked by the label *pattern*, not the raw count: a device exposing a
    ``2X`` face is 8-key even if only some faces are wired; ``1C``/``1D``
    implies 4-key; ``1B`` implies 2-key. This keeps every label inside
    ``KEY_MAPPING[channels]`` so no two faces collapse to the same address.
    """
    if any(lbl[0] == "2" for lbl in labels):
        return 8
    if "1C" in labels or "1D" in labels:
        return 4
    if "1B" in labels:
        return 2
    return 1


def _per_key_bus_address(physical_hex: str, channels: int, label: str) -> str:
    """Bus address the plate emits when key ``label`` is pressed.

    Reproduces the library's own inventory derivation
    (:func:`merge_discovered_buttons`): bit-reverse the physical address
    with :func:`convert_nikobus_address`, then **add** the key face's
    first-nibble offset (``KEY_MAPPING[channels][label]``) to the first
    nibble (wrapping mod 16). This is what a PC-Link inventory would store,
    so the router matches real presses on it. Falls back to the converted /
    physical address when the channel/label pair isn't known.
    """
    converted = convert_nikobus_address(physical_hex)
    if converted.startswith("["):  # convert_nikobus_address failure marker
        return physical_hex
    hexchar = KEY_MAPPING.get(channels, {}).get(label)
    if hexchar is None:
        return converted
    new_nibble = (int(converted[0], 16) + int(hexchar, 16)) & 0xF
    return f"{new_nibble:X}{converted[1:]}"


def build_config(
    components: list[dict[str, Any]],
    productbase: list[dict[str, Any]],
    objecten: list[dict[str, Any]],
    objectbase: dict[Any, dict[str, Any]],
) -> NkbConfig:
    """Pure builder — assemble the two config payloads from raw ``.nkb`` rows.

    Split out from :func:`build_config_from_nkb` so it can be unit-tested
    without an Access database.
    """
    ref_by_kpb = {
        r.get("KeyProductBase"): r.get("NikoRefNr") for r in productbase
    }
    comp_by_key = {c.get("KeyComponent"): c for c in components}

    # {ADDR: {channel: user_name}} for output modules (4-hex addresses).
    channel_names: dict[str, dict[int, str]] = {}
    for o in objecten:
        comp = comp_by_key.get(o.get("KeyComponent"))
        if not comp:
            continue
        pa = comp.get("PhysicalAddress")
        if not (isinstance(pa, int) and 0 < pa < 0x10000):
            continue
        base = objectbase.get(o.get("KeyObjectBase"), {})
        m = _OUTPUT_PREFIX_RE.match(str(base.get("Prefix") or ""))
        if not m:
            continue
        channel_names.setdefault(f"{pa:04X}", {})[int(m.group(1))] = (
            o.get("StrUserName") or ""
        )

    # {BUTTON_ADDR: {key labels}} — the single-key faces each wall plate has,
    # so a multi-key plate becomes a button with one op-point per key
    # (without this, every plate collapses to a single ``1A`` and only that
    # key's link records survive the scan merge).
    button_key_prefixes: dict[str, set[str]] = {}
    for o in objecten:
        comp = comp_by_key.get(o.get("KeyComponent"))
        if not comp:
            continue
        pa = comp.get("PhysicalAddress")
        if not (isinstance(pa, int) and pa >= 0x10000):
            continue
        pfx = str(objectbase.get(o.get("KeyObjectBase"), {}).get("Prefix") or "")
        if _SINGLE_KEY_RE.match(pfx):
            button_key_prefixes.setdefault(f"{pa:06X}", set()).add(pfx)

    module_config: dict[str, list[dict[str, Any]]] = {
        "switch_module": [],
        "dimmer_module": [],
        "roller_module": [],
    }
    buttons: list[dict[str, Any]] = []
    seen_buttons: set[str] = set()

    for r in components:
        pa = r.get("PhysicalAddress")
        if not isinstance(pa, int):
            continue

        # Buttons / inputs / IR / sensors are 24-bit (6-hex).
        if pa >= 0x10000:
            addr = f"{pa:06X}"
            if addr in seen_buttons:
                continue
            seen_buttons.add(addr)
            name = (r.get("StrUserName") or "").strip() or f"Button {addr}"
            model = ref_by_kpb.get(r.get("KeyProductBase")) or ""
            labels = _key_labels(button_key_prefixes.get(addr, set()))
            channels = _channels_for(labels)
            faces = [lbl for lbl in labels if lbl in KEY_MAPPING.get(channels, {})]
            if channels in (2, 4, 8) and faces:
                # Multi-key plate: one entry per key face. The loader groups
                # them onto a single physical button (keyed by ``addr``) with
                # ``channels`` op-points, so the scan can route each key's
                # link records to the right face.
                for label in faces:
                    buttons.append({
                        "address": _per_key_bus_address(addr, channels, label),
                        "description": f"{name} ({label})",
                        "linked_button": [{
                            "address": addr,
                            "key": label,
                            "channels": channels,
                            "type": "Push button",
                            "model": str(model),
                        }],
                    })
            else:
                # Single face (sensor / IR / interface): the loader makes a
                # 1-channel button keyed on the bus address itself.
                buttons.append({"address": addr, "description": name})
            continue

        # Output modules are 16-bit (4-hex).
        if not (0 < pa < 0x10000):
            continue
        model = ref_by_kpb.get(r.get("KeyProductBase"))
        spec = _MODULE_MODELS.get(str(model))
        if spec is None:
            continue  # not a switch/dimmer/roller HA supports
        category, nch = spec
        addr = f"{pa:04X}"
        chans: list[dict[str, Any]] = []
        for i in range(1, nch + 1):
            nm = channel_names.get(addr, {}).get(i, "")
            if _is_placeholder_name(nm):
                nm = f"Output {i}"
            channel: dict[str, Any] = {"description": nm}
            if category == "roller_module":
                channel["operation_time"] = _ROLLER_DEFAULT_OPERATION_TIME
            chans.append(channel)
        module_config[category].append(
            {
                "description": (r.get("StrUserName") or "").strip() or addr,
                "model": model,
                "address": addr,
                "channels": chans,
            }
        )

    buttons.sort(key=lambda b: b["address"])
    return NkbConfig(
        module_config=module_config,
        button_config={"nikobus_button": buttons},
    )


def build_config_from_nkb(nkb_path: str | Path) -> NkbConfig:
    """Read ``nkb_path`` and return the module + button config payloads.

    Blocking (run in an executor). Raises ``ValueError`` on an unreadable
    ``.nkb`` — the caller degrades gracefully.
    """
    with open_nkb_db(nkb_path) as db:
        components = _rows(db, "Component")
        productbase = _rows(db, "ProductBase")
        objecten = _rows(db, "Objecten")
        objectbase = {r["KeyObjectBase"]: r for r in _rows(db, "ObjectBase")}
    return build_config(components, productbase, objecten, objectbase)
