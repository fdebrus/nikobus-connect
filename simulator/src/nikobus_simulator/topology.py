"""What an installation contains, declared rather than captured.

The point of declaring a topology is to simulate hardware nobody here
owns. A dict (or the YAML/JSON holding it) names the modules, their
type, their channels and the links programmed into them; the encoders
turn that into the memory a real module would hold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Module types the simulator can build memory for. Anything else is
# accepted as a stub: present on the bus, answering status, no table.
ENCODABLE_TYPES = frozenset(
    {"switch_module", "roller_module", "dimmer_module", "audio_module"}
)

DEFAULT_CHANNELS = {
    "switch_module": 12,
    "dimmer_module": 12,
    "roller_module": 6,
}


@dataclass(frozen=True)
class Link:
    """One programmed link: a key drives a channel in some mode.

    ``button`` is the bus address the key puts on the wire — the ``#N``
    payload, and the address the integration shows. It is not what the
    module stores: a record holds a bit-shuffled form of it, which
    :func:`~nikobus_simulator.encoding.encode_button_address` builds.
    A press is matched on the bus address, as a module matches a key.
    """

    button: str
    channel: int
    #: The raw mode a record stores — one less than the vendor's mode
    #: number, so 0 is M01 and 4 is M05 "impulse".
    mode: int = 0
    key: int = 0
    t1: int = 0
    t2: int = 0

    def __post_init__(self) -> None:
        if len(self.button) != 6:
            raise ValueError(f"button address must be 6 hex digits: {self.button!r}")
        int(self.button, 16)


#: The object byte of an audio record that means the module's own Power
#: object rather than one of its zones.
AUDIO_POWER_OBJECT = 0x08


@dataclass(frozen=True)
class AudioTrigger:
    """One audio link: an address drives a function on an object.

    ``target`` is the object byte: ``0``–``3`` for zones 1–4, and
    ``0x08`` for the module's Power object, which is not a zone.
    """

    button: str
    function: int
    target: int

    @property
    def is_power(self) -> bool:
        return self.target == AUDIO_POWER_OBJECT


@dataclass
class ModuleSpec:
    """A module in the installation."""

    address: str
    type: str
    channels: int = 0
    links: list[Link] = field(default_factory=list)
    triggers: list[AudioTrigger] = field(default_factory=list)
    #: Byte-exact memory, when replaying a captured module instead of
    #: building one: ``{sub_byte: {register: 16-byte hex}}``.
    memory: dict[str, dict[int, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.address = self.address.upper()
        if len(self.address) != 4:
            raise ValueError(f"module address must be 4 hex digits: {self.address!r}")
        int(self.address, 16)
        if not self.channels:
            self.channels = DEFAULT_CHANNELS.get(self.type, 0)

    @property
    def encodable(self) -> bool:
        return self.type in ENCODABLE_TYPES


@dataclass
class Installation:
    """The gateway and the modules behind it."""

    modules: list[ModuleSpec] = field(default_factory=list)
    gateway_address: str = "86F5"
    gateway_family: str = "pc_link"

    def module(self, address: str) -> ModuleSpec | None:
        target = address.upper()
        return next((m for m in self.modules if m.address == target), None)


def _links(raw: Any) -> list[Link]:
    out = []
    for item in raw or []:
        out.append(
            Link(
                button=str(item["button"]).upper(),
                channel=int(item["channel"]),
                mode=int(item.get("mode", 0)),
                key=int(item.get("key", 0)),
                t1=int(item.get("t1", 0)),
                t2=int(item.get("t2", 0)),
            )
        )
    return out


#: Slot the virtual bank gives each audio function, in bank order. The
#: address's high nibble is the slot; the vendor numbers the mode three
#: higher than the function byte (0x0A -> M13 "volume up").
AUDIO_FUNCTION_SLOTS = (0x0A, 0x02, 0x0B, 0x03, 0x0D, 0x00, 0x0E, 0x01)
#: And the low nibble is the zone — not in zone order, as a real 05-205
#: lays it out (zone 1, zone 3, zone 2, zone 4).
AUDIO_ZONE_OFFSETS = (0x00, 0x10, 0x08, 0x18)
#: The two bytes a virtual bank carries after the slot byte. Any value
#: does; this is the one the module the layout was read from uses.
DEFAULT_AUDIO_BANK = "83CF"


def _audio_trigger(item: dict[str, Any]) -> AudioTrigger:
    """One declared trigger. ``object`` names it; ``zone`` still works."""
    target = item.get("object")
    if target is None:
        target = item.get("zone", 0)
    return AudioTrigger(
        button=str(item["button"]).upper(),
        function=int(item["function"]),
        target=int(target),
    )


def _triggers(
    raw: Any,
    zones: int,
    bank: str = DEFAULT_AUDIO_BANK,
    extra: Any = None,
) -> list[AudioTrigger]:
    """Audio triggers, either listed explicitly or generated per zone.

    Generated addresses follow the grid a real module's virtual bank
    holds: the first byte is ``slot << 5 | zone offset`` and the rest is
    the bank, and the records are ordered by address as the module
    stores them. ``extra`` appends triggers that sit outside the grid —
    a Power object's, or a second bank's — which is what a real module's
    table looks like once someone has programmed one.
    """
    if raw:
        return [_audio_trigger(item) for item in raw]
    if not 1 <= zones <= len(AUDIO_ZONE_OFFSETS):
        raise ValueError(f"an audio module has 1..{len(AUDIO_ZONE_OFFSETS)} zones, not {zones}")
    out: list[AudioTrigger] = []
    for slot, function in enumerate(AUDIO_FUNCTION_SLOTS):
        for zone in range(zones):
            first = slot * 0x20 + AUDIO_ZONE_OFFSETS[zone]
            out.append(
                AudioTrigger(button=f"{first:02X}{bank}", function=function, target=zone)
            )
    out.extend(_audio_trigger(item) for item in extra or [])
    return sorted(out, key=lambda t: t.button)


def load_installation(data: dict[str, Any]) -> Installation:
    """Build an installation from a plain mapping (YAML or JSON)."""
    gateway = data.get("gateway") or {}
    modules = []
    for raw in data.get("modules") or []:
        spec = ModuleSpec(
            address=str(raw["address"]),
            type=str(raw["type"]),
            channels=int(raw.get("channels", 0)),
            links=_links(raw.get("links")),
        )
        if spec.type == "audio_module":
            spec.triggers = _triggers(
                raw.get("triggers"),
                int(raw.get("zones", 4)),
                str(raw.get("bank", DEFAULT_AUDIO_BANK)).upper(),
                raw.get("extra_triggers"),
            )
        modules.append(spec)
    return Installation(
        modules=modules,
        gateway_address=str(gateway.get("address", "86F5")).upper(),
        gateway_family=str(gateway.get("family", "pc_link")),
    )


def load_file(path: str | Path) -> Installation:
    """Load a declaration from JSON, or YAML when PyYAML is installed."""
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith((".yaml", ".yml")):
        import yaml

        return load_installation(yaml.safe_load(text))
    return load_installation(json.loads(text))


PRESETS: dict[str, dict[str, Any]] = {
    # One of everything, wired the way a small house is. ``mode`` is the
    # raw value a record stores, one less than the vendor's mode number:
    # 0 is M01 "on / off", 4 is M05 "impulse", 1 is M02 "open".
    "house": {
        "gateway": {"address": "86F5", "family": "pc_link"},
        "modules": [
            {
                "address": "4707",
                "type": "switch_module",
                "channels": 12,
                "links": [
                    {"button": "0B1380", "key": 0, "channel": 1, "mode": 0},
                    {"button": "0B1390", "key": 1, "channel": 2, "mode": 0},
                    {"button": "0B13A0", "key": 2, "channel": 7, "mode": 4},
                ],
            },
            {
                "address": "0E6C",
                "type": "dimmer_module",
                "channels": 12,
                "links": [{"button": "0B1380", "key": 0, "channel": 1, "mode": 0}],
            },
            {
                "address": "9105",
                "type": "roller_module",
                "channels": 6,
                "links": [{"button": "0B13B0", "key": 3, "channel": 1, "mode": 0}],
            },
        ],
    },
    # A twelve-channel module only: the shape that hid the group-2 bug.
    "twelve": {
        "modules": [
            {
                "address": "81F6",
                "type": "switch_module",
                "channels": 12,
                "links": [
                    {"button": "20958A", "key": 0, "channel": ch, "mode": 4}
                    for ch in range(7, 13)
                ],
            }
        ]
    },
    # An Audio Distribution module with four zones, plus the module's
    # own Power object — which is not a zone, and whose trigger sits
    # outside the per-zone grid, as it does on the module this layout
    # was read from.
    "audio": {
        "modules": [
            {
                "address": "8334",
                "type": "audio_module",
                "zones": 4,
                "extra_triggers": [
                    {"button": "8483CF", "function": 0x08, "object": 0x08}
                ],
            }
        ]
    },
}


def preset(name: str) -> Installation:
    """One of the installations shipped with the simulator."""
    if name not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; have {sorted(PRESETS)}")
    return load_installation(PRESETS[name])
