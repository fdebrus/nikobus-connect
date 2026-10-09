"""Reading the stores the merge writes: what drives what, and what is left.

Discovery fills two stores a host keeps — the button store
(``{physical address: entry}`` with the operation points and the
modules each one drives) and the central-function broadcasts — and a
host then needs plain answers out of them: which keys control an
output, which buttons are residue of a previous owner, whether a
central function is a shutter group or a light scene, what a project
file's colour-controller links map onto. None of it is a host's
business to work out; it is the shape of the library's own records.
Every function here is pure: stores in, plain data out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..nkb.parser import mode_code
from ..rgb import rgb_key_role, rgb_mode_label
from .mapping import DEVICE_TYPES

#: An output record read from a PC-Link / PC-Logic register table rather
#: than from an output module's own link table. A button whose every
#: record comes from there is residue of a previous owner's programming —
#: unless the install has a PC-Logic, where it may be a scene trigger.
REGISTRY_SOURCES: frozenset[str] = frozenset({"pc_link_registry", "pc_logic_registry"})

#: Devices that emit press frames but never appear in an output module's
#: link table: the 05-058 universal interface in either mode (device
#: types 0x43 and 0x44). Empty links are their normal state, not residue.
#: Matched on the ``type`` the inventory writes into the button entry.
INPUT_ONLY_BUTTON_TYPES: frozenset[str] = frozenset(
    str(DEVICE_TYPES[code]["Name"]) for code in ("43", "44")
)

#: The statuses :func:`classify_button_status` files a button under.
STATUS_ACTIVE = "active"
STATUS_LEGACY_ORPHAN = "legacy_orphan"
STATUS_LEGACY_UNDECODED = "legacy_undecoded"
STATUS_SYNTHESIZED_INPUT = "synthesized_input"
STATUS_INPUT_ONLY = "input_only"

MemberSet = frozenset[tuple[str, int, str]]


# --- member sets -----------------------------------------------------------


def member_set_from_outputs(outputs: Any) -> MemberSet:
    """``{(module, channel, mode code)}`` for an output list — the key a
    scene or central function is matched by, the same for a project
    file's group, a stored CF and a routing-graph operation point."""
    out: set[tuple[str, int, str]] = set()
    for o in outputs or []:
        if not isinstance(o, Mapping):
            continue
        mod = o.get("module_address")
        ch = o.get("channel")
        code = mode_code(o.get("mode"))
        if isinstance(mod, str) and isinstance(ch, int) and code:
            out.add((mod.upper(), ch, code))
    return frozenset(out)


def cf_member_set(cf: Mapping[str, Any] | None) -> MemberSet:
    """Member-set key of a stored central-function entry."""
    return member_set_from_outputs((cf or {}).get("outputs"))


# --- what a button drives --------------------------------------------------


def collect_button_linked_modules(phys: Mapping[str, Any]) -> set[str]:
    """Every module address any operation point of a button drives."""
    linked: set[str] = set()
    op_points = phys.get("operation_points") or {}
    if not isinstance(op_points, Mapping):
        return linked
    for op_point in op_points.values():
        if not isinstance(op_point, Mapping):
            continue
        for link in op_point.get("linked_modules") or []:
            if not isinstance(link, Mapping):
                continue
            addr = link.get("module_address")
            if addr:
                linked.add(str(addr).upper())
    return linked


def collect_button_outputs(phys: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every output record under every operation point of a button, flat.

    The records are the decoder's: channel, mode, payload, button
    address and, since 0.5.22, ``record_source``.
    """
    outputs: list[dict[str, Any]] = []
    op_points = phys.get("operation_points") or {}
    if not isinstance(op_points, Mapping):
        return outputs
    for op_point in op_points.values():
        if not isinstance(op_point, Mapping):
            continue
        for link in op_point.get("linked_modules") or []:
            if not isinstance(link, Mapping):
                continue
            for out in link.get("outputs") or []:
                if isinstance(out, dict):
                    outputs.append(out)
    return outputs


def all_outputs_registry_sourced(outputs: Iterable[Mapping[str, Any]]) -> bool:
    """True when there are outputs and every one came from a registry.

    A record without ``record_source`` (written before 0.5.22) is of
    unknown source and makes this False, so an old store falls through
    to the ordinary classification without a migration.
    """
    outputs = list(outputs)
    if not outputs:
        return False
    return all(out.get("record_source") in REGISTRY_SOURCES for out in outputs)


def has_pc_logic_module(module_data: Mapping[str, Any] | None) -> bool:
    """True if the module store holds at least one PC-Logic.

    Gates the registry-only verdict: without a PC-Logic, a button whose
    every record is registry-sourced has no module recording the link
    anywhere and is residue; with one, the same shape may be a scene
    trigger the PC-Logic fires, and the user has to judge.
    """
    modules = (module_data or {}).get("nikobus_module", {})
    if not isinstance(modules, Mapping):
        return False
    return any(
        isinstance(m, Mapping) and m.get("module_type") == "pc_logic"
        for m in modules.values()
    )


def classify_button_status(
    phys: Mapping[str, Any],
    remaining_modules: set[str],
    has_pc_logic: bool,
) -> str:
    """Which bucket a button lands in after a scan.

    ``remaining_modules`` are the module addresses still on the bus,
    upper-case; ``has_pc_logic`` the install's topology gate.

    * ``synthesized_input`` — a PC-Logic or Modular Interface input
      child the library synthesized (``pc_logic_parent_address``): a
      bus-event source its parent listens to; empty links are its
      steady state.
    * ``input_only`` — a universal interface, or a PC-Link calendar
      channel: emits presses, never appears in a module's link table.
    * ``legacy_undecoded`` — no decoded output anywhere: either a key
      kept for a host's automations only, or residue.
    * ``legacy_orphan`` — outputs decoded, but every one from a registry
      with no PC-Logic to justify it, or every module they point at is
      gone.
    * ``active`` — at least one linked module survived the scan.
    """
    if phys.get("pc_logic_parent_address"):
        return STATUS_SYNTHESIZED_INPUT
    if phys.get("type") in INPUT_ONLY_BUTTON_TYPES or phys.get("calendar_channel"):
        return STATUS_INPUT_ONLY
    linked = collect_button_linked_modules(phys)
    outputs = collect_button_outputs(phys)
    if not outputs:
        return STATUS_LEGACY_UNDECODED
    if not has_pc_logic and all_outputs_registry_sourced(outputs):
        return STATUS_LEGACY_ORPHAN
    if not (linked & remaining_modules):
        return STATUS_LEGACY_ORPHAN
    return STATUS_ACTIVE


def build_controlled_by_index(
    button_data: Mapping[str, Any] | None,
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """``(module address, channel) -> [the keys that drive it]``.

    Each entry names the key's bus address, description, mode and
    timers, and the plate and key label it sits on.
    """
    index: dict[tuple[str, int], list[dict[str, Any]]] = {}
    buttons = (button_data or {}).get("nikobus_button", {})
    if not isinstance(buttons, Mapping):
        return index
    for physical_addr, phys in buttons.items():
        if not isinstance(phys, Mapping):
            continue
        op_points = phys.get("operation_points") or {}
        if not isinstance(op_points, Mapping):
            continue
        for key_label, op_point in op_points.items():
            if not isinstance(op_point, Mapping):
                continue
            bus_addr = op_point.get("bus_address") or ""
            description = op_point.get("description") or f"Button {bus_addr}"
            for link in op_point.get("linked_modules") or []:
                if not isinstance(link, Mapping):
                    continue
                module_address = link.get("module_address")
                if not module_address:
                    continue
                module_key = str(module_address).upper()
                for out in link.get("outputs") or []:
                    if not isinstance(out, Mapping):
                        continue
                    channel = out.get("channel")
                    if not isinstance(channel, int):
                        continue
                    index.setdefault((module_key, channel), []).append({
                        "bus_address": bus_addr,
                        "description": description,
                        "mode": out.get("mode"),
                        "t1": out.get("t1"),
                        "t2": out.get("t2"),
                        "wall_button_address": physical_addr,
                        "wall_button_key": key_label,
                    })
    return index


def build_routing_graph(
    button_data: Mapping[str, Any] | None,
) -> dict[MemberSet, tuple[list[str], list[dict[str, Any]]]]:
    """Every operation point's member set -> ``(firing addresses, outputs)``.

    The full ``trigger -> outputs`` relation of the button store, the
    data discovery decoded. Used to find the on-bus address that fires
    a named project-file group (matched by member set), including the
    shutter and master groups that never become central functions of
    their own. Addresses driving an identical output set are grouped;
    the sorted-first is the canonical activation address.
    """
    graph: dict[MemberSet, tuple[list[str], list[dict[str, Any]]]] = {}
    buttons = (button_data or {}).get("nikobus_button", {})
    if not isinstance(buttons, Mapping):
        return {}
    for phys in buttons.values():
        if not isinstance(phys, Mapping):
            continue
        for op in (phys.get("operation_points") or {}).values():
            if not isinstance(op, Mapping):
                continue
            addr = op.get("bus_address")
            if not isinstance(addr, str) or not addr:
                continue
            outputs: list[dict[str, Any]] = []
            seen: set[tuple[str, int, str]] = set()
            for link in op.get("linked_modules") or []:
                if not isinstance(link, Mapping):
                    continue
                mod = link.get("module_address")
                if not isinstance(mod, str):
                    continue
                for o in link.get("outputs") or []:
                    if not isinstance(o, Mapping):
                        continue
                    ch = o.get("channel")
                    mode = o.get("mode")
                    if not (isinstance(ch, int) and isinstance(mode, str)):
                        continue
                    dedupe = (mod.upper(), ch, mode)
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    outputs.append(
                        {
                            "module_address": mod.upper(),
                            "channel": ch,
                            "mode": mode,
                            "t1": o.get("t1") if isinstance(o.get("t1"), str) else None,
                            "t2": o.get("t2") if isinstance(o.get("t2"), str) else None,
                        }
                    )
            members = member_set_from_outputs(outputs)
            if not members:
                continue
            entry = graph.setdefault(members, ([], outputs))
            entry[0].append(addr.upper())
    return {m: (sorted(set(a)), o) for m, (a, o) in graph.items()}


# --- central functions -----------------------------------------------------


def flatten_cf_broadcasts(broadcasts: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The ``CFBroadcast`` records discovery built, as plain JSON-safe dicts:
    ``{address: {bus_address, pattern, outputs, triggered_by}}``, with
    ``outputs`` a list of ``{module_address, channel, mode, t1, t2}``."""
    flat: dict[str, dict[str, Any]] = {}
    for addr, cf in broadcasts.items():
        outputs = [
            {
                "module_address": str(m.module_address).upper(),
                "channel": int(m.channel),
                "mode": str(m.mode),
                "t1": getattr(m, "t1", None),
                "t2": getattr(m, "t2", None),
            }
            for m in getattr(cf, "outputs", [])
        ]
        bus_address = str(getattr(cf, "bus_address", addr)).upper()
        triggered_by = [
            str(t).upper() for t in (getattr(cf, "triggered_by", None) or [bus_address])
        ]
        flat[str(addr).upper()] = {
            "bus_address": bus_address,
            "pattern": str(getattr(cf, "pattern", "unknown")),
            "outputs": outputs,
            "triggered_by": triggered_by,
        }
    return flat


def is_roller_member(mode: Any) -> bool:
    """True if an output's mode *wording* makes it a shutter member.

    By wording, not by code: a roller mode label carries ``open``,
    ``close`` or ``stop`` (``"M01 (Open - stop - close)"``), while the
    ``M02`` / ``M03`` codes are shared with switch modules (``"M02 (On +
    Operating time)"``), so a code would misfile a switch member.
    """
    if not isinstance(mode, str):
        return False
    text = mode.lower()
    return ("open" in text) or ("close" in text) or ("stop" in text)


def is_pure_roller_cf(cf: Mapping[str, Any] | None) -> bool:
    """True when a central function has members and every one is a shutter.

    Such a CF is a grouped cover (open / close / stop). One with a light
    scene, preset or switch action among its members is mixed and stays
    a scene.
    """
    outputs = (cf or {}).get("outputs")
    if not isinstance(outputs, list):
        return False
    members = [o for o in outputs if isinstance(o, Mapping)]
    if not members:
        return False
    return all(is_roller_member(o.get("mode")) for o in members)


#: Central functions whose activation address is a real wall key or IR
#: code, as opposed to the bare ``38xx`` PC-Logic broadcasts.
BUTTON_BACKED_SCENE_PATTERNS: tuple[str, ...] = ("light_scene", "nkb_scene")


def is_button_backed_cf(cf: Mapping[str, Any] | None) -> bool:
    """True for a light scene fired by a real key or IR code — an ordinary
    control that drives several outputs, not a scene object of its own."""
    return str((cf or {}).get("pattern") or "") in BUTTON_BACKED_SCENE_PATTERNS


def cf_cover_members(cf: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """A shutter central function's members, one per ``(module, channel)``.

    A roller CF may list a channel twice (an open ``M02`` and a close
    ``M03`` record of a two-key function). Each distinct channel comes
    once, in first-sighting order, with the open and close run times
    (``t1``) taken from the matching mode: ``M01`` "open - stop - close"
    carries both directions, an open-only mode the open time, a
    close-only mode the close time. Members that are not shutters are
    left out.
    """
    members: dict[tuple[str, int], dict[str, Any]] = {}
    order: list[tuple[str, int]] = []
    for o in (cf or {}).get("outputs") or []:
        if not isinstance(o, Mapping):
            continue
        mod = o.get("module_address")
        ch = o.get("channel")
        if not (isinstance(mod, str) and isinstance(ch, int)):
            continue
        mode = o.get("mode")
        if not is_roller_member(mode):
            continue
        text = str(mode).lower()
        has_open = "open" in text
        has_close = ("close" in text) or ("stop" in text)
        key = (mod.upper(), ch)
        if key not in members:
            members[key] = {
                "module_address": mod.upper(),
                "channel": ch,
                "open_time": None,
                "close_time": None,
            }
            order.append(key)
        t1 = o.get("t1")
        if has_open and members[key]["open_time"] is None:
            members[key]["open_time"] = t1
        if has_close and members[key]["close_time"] is None:
            members[key]["close_time"] = t1
    return [members[k] for k in order]


# --- the project file's colour-controller links ----------------------------


def apply_rgb_links(
    modules: dict[str, Any],
    buttons: dict[str, Any],
    links: Iterable[Any],
) -> int:
    """Write a project file's colour-controller links into the stores.

    ``modules`` is the module store (``{address: entry}``), ``buttons``
    the button store (``{physical address: entry}``), ``links`` the
    ``RgbLink`` tuples ``parse_nkb`` returned. A link to a controller
    the store knows lands twice: on the controller's entry as
    ``rgb_links`` (what a host presses, with each key's role in its
    mode) and on the plate key's operation point as a ``linked_modules``
    block on channel 1 (what the controlled-by index and a post-press
    refresh read). A plate the button store lacks still gives the
    controller its link. Returns the number of links applied; applying
    the same links twice changes nothing.
    """
    applied = 0
    by_module: dict[str, list[dict[str, Any]]] = {}
    for link in links or ():
        module_address = str(getattr(link, "module_address", "") or "").upper()
        module = modules.get(module_address)
        if not isinstance(module, dict) or module.get("module_type") != "rgb_module":
            continue
        mode = getattr(link, "mode", None)
        key = str(getattr(link, "key", "") or "")
        role = rgb_key_role(mode, key) if isinstance(mode, int) else None
        mode_label = (
            rgb_mode_label(mode) if isinstance(mode, int)
            else str(getattr(link, "mode_text", "") or "")
        )
        bus_address = str(getattr(link, "bus_address", "") or "").upper()
        plate_address = str(getattr(link, "button_address", "") or "").upper()
        record = {
            "bus_address": bus_address,
            "button_address": plate_address,
            "key": key,
            "mode": mode,
            "mode_label": mode_label,
            "role": role,
        }
        records = by_module.setdefault(module_address, [])
        if record not in records:
            records.append(record)

        phys = buttons.get(plate_address)
        op_points = phys.get("operation_points") if isinstance(phys, dict) else None
        op_point = op_points.get(key) if isinstance(op_points, dict) else None
        if isinstance(op_point, dict):
            linked = op_point.get("linked_modules")
            if not isinstance(linked, list):
                linked = []
                op_point["linked_modules"] = linked
            block = next(
                (
                    b for b in linked
                    if isinstance(b, dict)
                    and str(b.get("module_address") or "").upper() == module_address
                ),
                None,
            )
            if block is None:
                block = {"module_address": module_address, "outputs": []}
                linked.append(block)
            outputs = block.get("outputs")
            if not isinstance(outputs, list):
                outputs = []
                block["outputs"] = outputs
            if not any(isinstance(o, dict) and o.get("channel") == 1 for o in outputs):
                outputs.append({
                    "channel": 1,
                    "mode": mode_label,
                    "button_address": bus_address,
                    "record_source": "nkb",
                })
        applied += 1

    for module_address, records in by_module.items():
        modules[module_address]["rgb_links"] = records
    return applied


__all__ = [
    "BUTTON_BACKED_SCENE_PATTERNS",
    "INPUT_ONLY_BUTTON_TYPES",
    "REGISTRY_SOURCES",
    "STATUS_ACTIVE",
    "STATUS_INPUT_ONLY",
    "STATUS_LEGACY_ORPHAN",
    "STATUS_LEGACY_UNDECODED",
    "STATUS_SYNTHESIZED_INPUT",
    "MemberSet",
    "all_outputs_registry_sourced",
    "apply_rgb_links",
    "build_controlled_by_index",
    "build_routing_graph",
    "cf_cover_members",
    "cf_member_set",
    "classify_button_status",
    "collect_button_linked_modules",
    "collect_button_outputs",
    "flatten_cf_broadcasts",
    "has_pc_logic_module",
    "is_button_backed_cf",
    "is_pure_roller_cf",
    "is_roller_member",
    "member_set_from_outputs",
]
