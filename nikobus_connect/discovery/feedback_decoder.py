"""Decoder for the feedback module (05-207) programming image.

The feedback module keeps five regions in its memory. Offsets are
memory offsets; the module answers 16-byte block reads (function
0x10) with ``block = offset // 16``.

======  ========  ==================================================
offset  length    content
======  ========  ==================================================
0x0000  0x4000    input-event records, 8 bytes each, ``FF`` terminated
0x4000  0x2000    LED-slot -> tracked-output lists (byte stream)
0x6000  0x0100    tracked output modules, 8 bytes each
0x6100  0x0100    24 x 3-byte push-button module addresses (one per group)
0x6200  0x1700    touch-button records, tab names, LED modes of slots 0..191
======  ========  ==================================================

LED slots are numbered 0..255. Slots ``8k .. 8k+7`` belong to the
push-button module of group ``k`` (its address sits in the 0x6100
table, 3 bytes per group); slots 192..255 are the feedback module's
own LEDs. Within a group the row follows the key order A, B, C, D
(and 1A..1D, 2A..2D on 8-key plates).

Two builds of the programming software place the group table
differently: at offset 0 of the 0x6100 region, or at offset 0x60 (seen
on a live module). ``decode_group_addresses`` detects which.

The 4th byte of a tracked-output record is the index of the module's
first tracked output in the LED lists (a running count over the
records before it).

Bus addresses: a push-button key transmits a 24-bit address. Reversing
its bit order gives ``module_address_22 << 2 | key_index`` where
``key_index`` is 1 for A, 3 for B, 0 for C and 2 for D. The 0x6100
table stores ``module_address_22``; the 24-bit input addresses inside
the records are the same bit-reversed form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

FEEDBACK_IMAGE_SIZE: Final[int] = 0x7900

REGION_INPUT_RECORDS: Final[tuple[int, int]] = (0x0000, 0x4000)
REGION_LED_LISTS: Final[tuple[int, int]] = (0x4000, 0x2000)
REGION_OUTPUT_MODULES: Final[tuple[int, int]] = (0x6000, 0x0100)
REGION_GROUP_ADDRESSES: Final[tuple[int, int]] = (0x6100, 0x0100)
REGION_EXTRA: Final[tuple[int, int]] = (0x6200, 0x1700)
LED_MODE_TABLE_OFFSET: Final[int] = REGION_EXTRA[0] + 0x1600
LED_MODE_TABLE_LENGTH: Final[int] = 192

GROUP_COUNT: Final[int] = 24
GROUP_ENTRY_SIZE: Final[int] = 3
GROUP_TABLE_BASES: Final[tuple[int, ...]] = (0x00, 0x60)
SLOTS_PER_GROUP: Final[int] = 8
OWN_LED_SLOT_BASE: Final[int] = GROUP_COUNT * SLOTS_PER_GROUP

# Row inside a group -> key label, by number of keys on the plate.
KEY_LABELS_BY_ROW: Final[dict[int, tuple[str, ...]]] = {
    1: ("1A",),
    2: ("1A", "1B"),
    4: ("1A", "1B", "1C", "1D"),
    8: ("1A", "1B", "1C", "1D", "2A", "2B", "2C", "2D"),
}

LED_MODES: Final[dict[int, str]] = {
    0: "auto",
    1: "auto_inverted",
    2: "direct",
    3: "direct_inverted",
    4: "off",
    5: "on",
}

# EEPROM type byte of a tracked output module -> discovery module type.
EEPROM_TYPE_TO_MODULE_TYPE: Final[dict[int, str]] = {
    1: "switch_module",
    2: "roller_module",
    3: "dimmer_module",
    8: "dimmer_module",
    9: "switch_module",
}

# Key index (low two bits of the bit-reversed key address) -> key letter.
KEY_LETTER_BY_INDEX: Final[dict[int, str]] = {1: "A", 3: "B", 0: "C", 2: "D"}


def reverse_bits24(value: int) -> int:
    """Reverse the bit order of a 24-bit value."""
    return int(f"{value & 0xFFFFFF:024b}"[::-1], 2)


def key_address_from_input(input_address: int) -> str:
    """Bus address (6 hex chars) of the key behind a stored input address."""
    return f"{reverse_bits24(input_address):06X}"


def key_index_from_input(input_address: int) -> int:
    """Key index (0..3) of a stored input address."""
    return input_address & 0x3


def plate_addresses_for_group(module_address_22: int) -> tuple[str, ...]:
    """Candidate 24-bit plate addresses for a 0x6100 table entry.

    The table stores the 22-bit address with bit 0 normalised (it is
    cleared for 8-key plates, whose two rows differ in that bit), so
    both variants are returned, the stored one first.
    """
    base = (module_address_22 & 0x3FFFFF) << 2
    alt = base ^ 0x4
    return (f"{base:06X}", f"{alt:06X}")


@dataclass
class FeedbackOutput:
    """One tracked output of an output module."""

    index: int
    module_address: str
    channel: int
    eeprom_type: int
    module_type: str | None


@dataclass
class FeedbackLedOutput:
    """One entry of an LED's tracking list."""

    output_index: int
    output: FeedbackOutput | None
    inverted: bool = False
    input_address: str | None = None


@dataclass
class FeedbackLed:
    """One LED slot and what it tracks."""

    slot: int
    group: int | None
    row: int | None
    plate_addresses: tuple[str, ...]
    own_led: int | None
    mode: int | None
    mode_name: str | None
    polarity_inverted: bool
    listless: bool
    outputs: list[FeedbackLedOutput] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "group": self.group,
            "row": self.row,
            "plate_addresses": list(self.plate_addresses),
            "own_led": self.own_led,
            "mode": self.mode,
            "mode_name": self.mode_name,
            "polarity_inverted": self.polarity_inverted,
            "listless": self.listless,
            "outputs": [
                {
                    "output_index": item.output_index,
                    "module_address": item.output.module_address if item.output else None,
                    "channel": item.output.channel if item.output else None,
                    "module_type": item.output.module_type if item.output else None,
                    "inverted": item.inverted,
                    "input_address": item.input_address,
                }
                for item in self.outputs
            ],
        }


@dataclass
class FeedbackInputRecord:
    """One input-event record: which key press changes which output."""

    offset: int
    input_address: int
    key_address: str
    link_mode: int
    param1: int
    param3: int
    output_eeprom_type: int
    output_index: int
    output: FeedbackOutput | None
    dim_level: int


@dataclass
class FeedbackImage:
    """Decoded feedback module image."""

    modules: list[dict[str, Any]]
    outputs: list[FeedbackOutput | None]
    group_addresses: list[int | None]
    leds: list[FeedbackLed]
    input_records: list[FeedbackInputRecord]
    group_table_base: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "modules": self.modules,
            "outputs": [None if out is None else vars(out) for out in self.outputs],
            "group_table_base": self.group_table_base,
            "group_addresses": [
                None if addr is None else f"{addr:06X}" for addr in self.group_addresses
            ],
            "leds": [led.as_dict() for led in self.leds],
            "input_records": [
                {
                    "key_address": rec.key_address,
                    "link_mode": rec.link_mode,
                    "param1": rec.param1,
                    "param3": rec.param3,
                    "output_index": rec.output_index,
                    "module_address": rec.output.module_address if rec.output else None,
                    "channel": rec.output.channel if rec.output else None,
                    "dim_level": rec.dim_level,
                }
                for rec in self.input_records
            ],
        }


def _output_at(outputs: list[FeedbackOutput | None], index: int) -> FeedbackOutput | None:
    return outputs[index] if 0 <= index < len(outputs) else None


def _be24(data: bytes, offset: int) -> int:
    return (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]


def _region(image: bytes, region: tuple[int, int]) -> bytes:
    start, length = region
    return image[start : start + length]


def decode_output_modules(image: bytes) -> list[dict[str, Any]]:
    """Tracked output modules.

    ``[type][addr hi][addr lo][first output index][mask hi][mask lo][FF][FF]``;
    the 4th byte is the LED-list index of the module's first tracked
    output (a running count over the preceding records).
    """
    data = _region(image, REGION_OUTPUT_MODULES)
    modules: list[dict[str, Any]] = []
    for offset in range(0, len(data) - 7, 8):
        record = data[offset : offset + 8]
        if record[0] == 0xFF:
            break
        modules.append(
            {
                "eeprom_type": record[0],
                "module_type": EEPROM_TYPE_TO_MODULE_TYPE.get(record[0]),
                "module_address": f"{record[1]:02X}{record[2]:02X}",
                "first_output_index": record[3],
                "channel_mask": (record[4] << 8) | record[5],
            }
        )
    return modules


def output_table(modules: list[dict[str, Any]]) -> list[FeedbackOutput | None]:
    """Output index -> (module, channel).

    A module's outputs start at its stored first index and follow the
    set mask bits, LSB first; a module with a stored index of 0 that is
    not the first record uses the running count instead. Channels are
    1-based to match the rest of the library. Gaps are ``None``.
    """
    outputs: dict[int, FeedbackOutput] = {}
    running = 0
    for module in modules:
        base = module.get("first_output_index", running)
        if base < running:
            base = running
        index = base
        mask = module["channel_mask"]
        for bit in range(16):
            if mask & (1 << bit):
                outputs[index] = FeedbackOutput(
                    index=index,
                    module_address=module["module_address"],
                    channel=bit + 1,
                    eeprom_type=module["eeprom_type"],
                    module_type=module["module_type"],
                )
                index += 1
        running = index
    if not outputs:
        return []
    return [outputs.get(i) for i in range(max(outputs) + 1)]


def group_table_base(image: bytes) -> int:
    """Offset of the group table inside the 0x6100 region (0 or 0x60).

    One build of the programming software writes the 24 entries from
    offset 0, another from 0x60. The first base whose table holds an
    entry wins; an empty region reads as base 0.
    """
    data = _region(image, REGION_GROUP_ADDRESSES)
    span = GROUP_COUNT * GROUP_ENTRY_SIZE
    for base in GROUP_TABLE_BASES:
        if any(b != 0xFF for b in data[base : base + span]):
            return base
    return GROUP_TABLE_BASES[0]


def decode_group_addresses(image: bytes, base: int | None = None) -> list[int | None]:
    """24 group entries, 3 bytes big-endian each; ``FFFFFF`` = unused."""
    data = _region(image, REGION_GROUP_ADDRESSES)
    if base is None:
        base = group_table_base(image)
    groups: list[int | None] = []
    for index in range(GROUP_COUNT):
        offset = base + GROUP_ENTRY_SIZE * index
        value = _be24(data, offset) if offset + 3 <= len(data) else 0xFFFFFF
        groups.append(None if value == 0xFFFFFF else value)
    return groups


def decode_led_modes(image: bytes) -> dict[int, int]:
    """LED mode per slot 0..191 (``FF`` = not programmed)."""
    data = image[LED_MODE_TABLE_OFFSET : LED_MODE_TABLE_OFFSET + LED_MODE_TABLE_LENGTH]
    return {slot: value for slot, value in enumerate(data) if value != 0xFF}


def decode_led_lists(
    image: bytes, outputs: list[FeedbackOutput | None]
) -> dict[int, tuple[bool, bool, list[FeedbackLedOutput]]]:
    """Per-slot lists from the 0x4000 byte stream.

    Items: ``00|01 idx`` (2 bytes, 01 = inverted tracking) or
    ``02 idx a2 a1 a0`` (5 bytes, with the input address of the link).
    A slot ends with ``04|05 slot`` (05 = inverted polarity). After
    ``08 FF`` only ``04|05 slot`` pairs follow, for list-less LEDs.
    """
    data = _region(image, REGION_LED_LISTS)
    slots: dict[int, tuple[bool, bool, list[FeedbackLedOutput]]] = {}
    current: list[FeedbackLedOutput] = []
    tail = False
    offset = 0
    while offset < len(data):
        tag = data[offset]
        if tag == 0xFF:
            break
        if tag in (0x00, 0x01) and not tail and offset + 1 < len(data):
            index = data[offset + 1]
            current.append(
                FeedbackLedOutput(
                    output_index=index,
                    output=_output_at(outputs, index),
                    inverted=tag == 0x01,
                )
            )
            offset += 2
        elif tag == 0x02 and not tail and offset + 4 < len(data):
            index = data[offset + 1]
            current.append(
                FeedbackLedOutput(
                    output_index=index,
                    output=_output_at(outputs, index),
                    input_address=key_address_from_input(_be24(data, offset + 2)),
                )
            )
            offset += 5
        elif tag in (0x04, 0x05) and offset + 1 < len(data):
            slot = data[offset + 1]
            slots[slot] = (tag == 0x05, tail, current if not tail else [])
            current = []
            offset += 2
        elif tag == 0x08:
            tail = True
            offset += 2
        else:
            raise ValueError(f"unexpected tag 0x{tag:02X} at LED list offset 0x{offset:04X}")
    return slots


def decode_input_records(
    image: bytes, outputs: list[FeedbackOutput | None]
) -> list[FeedbackInputRecord]:
    """8-byte input-event records, terminated by eight ``FF`` bytes."""
    data = _region(image, REGION_INPUT_RECORDS)
    records: list[FeedbackInputRecord] = []
    for offset in range(0, len(data) - 7, 8):
        record = data[offset : offset + 8]
        if record == b"\xff" * 8:
            break
        input_address = (_be24(record, 0) & 0xFFFFFC) | ((record[4] >> 4) & 0x3)
        index = record[5]
        records.append(
            FeedbackInputRecord(
                offset=REGION_INPUT_RECORDS[0] + offset,
                input_address=input_address,
                key_address=key_address_from_input(input_address),
                link_mode=record[3] & 0x0F,
                param1=record[3] >> 4,
                param3=((record[2] & 0x3) << 2) | (record[4] >> 6),
                output_eeprom_type=record[4] & 0x0F,
                output_index=index,
                output=_output_at(outputs, index),
                dim_level=record[6],
            )
        )
    return records


def decode_feedback_image(image: bytes) -> FeedbackImage:
    """Decode a full (``FEEDBACK_IMAGE_SIZE``) or partial image.

    Missing tail bytes are treated as ``FF``.
    """
    if len(image) < FEEDBACK_IMAGE_SIZE:
        image = bytes(image) + b"\xff" * (FEEDBACK_IMAGE_SIZE - len(image))
    modules = decode_output_modules(image)
    outputs = output_table(modules)
    base = group_table_base(image)
    groups = decode_group_addresses(image, base)
    modes = decode_led_modes(image)
    lists = decode_led_lists(image, outputs)

    leds: list[FeedbackLed] = []
    for slot in sorted(lists):
        polarity_inverted, listless, items = lists[slot]
        if slot < OWN_LED_SLOT_BASE:
            group, row = divmod(slot, SLOTS_PER_GROUP)
            address = groups[group]
            plates = plate_addresses_for_group(address) if address is not None else ()
            own_led = None
        else:
            group = row = None
            plates = ()
            own_led = slot - OWN_LED_SLOT_BASE + 1
        mode = modes.get(slot)
        leds.append(
            FeedbackLed(
                slot=slot,
                group=group,
                row=row,
                plate_addresses=plates,
                own_led=own_led,
                mode=mode,
                mode_name=LED_MODES.get(mode) if mode is not None else None,
                polarity_inverted=polarity_inverted,
                listless=listless,
                outputs=items,
            )
        )
    return FeedbackImage(
        modules=modules,
        outputs=outputs,
        group_addresses=groups,
        leds=leds,
        input_records=decode_input_records(image, outputs),
        group_table_base=base,
    )
