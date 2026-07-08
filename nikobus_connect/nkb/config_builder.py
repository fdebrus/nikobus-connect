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

from pathlib import Path
from typing import Any, NamedTuple

from .parser import (
    _OUTPUT_PLACEHOLDERS,
    _OUTPUT_PREFIX_RE,
    _rows,
    open_nkb_db,
)

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
