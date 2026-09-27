"""Build the memory a module would hold from a declaration.

The library decodes memory into links; to simulate a module we need the
other direction. Each family stores its links differently:

* **switch / roller** — six-byte records in 16-byte registers from
  ``0x10`` of bank ``00``. A record is stored byte-reversed relative to
  the order the decoder reads it in.
* **dimmer** — eight-byte records, one per 8-byte register from ``0x20``
  of bank ``00``, read with function ``0x22`` instead of ``0x10``.
* **audio** — bank ``01`` from register ``0x38``: filler, then a
  two-byte header (the record count, then ``00``), then six-byte
  records ``<bus address> <function> <zone> 01``, stored as they read.

A switch / roller / dimmer record does *not* store the address a key
puts on the wire; it stores a bit-shuffled form of it, which the
library's ``get_button_address`` turns back into the bus address. A
declaration names the bus address (that is the address a host presses
and the one the integration shows), so the encoder shuffles it the
other way — see :func:`encode_button_address`. Audio records are the
exception: those hold the ``#N`` payload verbatim.

Every encoder is checked against a real module: encode, hand the bytes
to the library's own decoder, and the links must come back.
"""

from __future__ import annotations

from .topology import AudioTrigger, Link, ModuleSpec

REGISTER_HEX = 32  # sixteen bytes per register
#: The dimmer's registers are half as wide, and its records fill one each.
DIMMER_REGISTER_HEX = 16
FILLER = "F" * REGISTER_HEX

#: Where each family's table starts, as (sub byte, first register).
TABLE_START = {
    "switch_module": ("00", 0x10),
    "roller_module": ("00", 0x10),
    "dimmer_module": ("00", 0x20),
    "audio_module": ("01", 0x38),
}

#: How wide one register of each family is, in hex digits.
REGISTER_WIDTH = {
    "switch_module": REGISTER_HEX,
    "roller_module": REGISTER_HEX,
    "dimmer_module": DIMMER_REGISTER_HEX,
    "audio_module": REGISTER_HEX,
}


def register_width(module_type: str) -> int:
    """Hex digits in one register of this family (16 bytes unless dimmer)."""
    return REGISTER_WIDTH.get(module_type, REGISTER_HEX)


def _reverse_bytes(hex_str: str) -> str:
    return "".join(
        reversed([hex_str[i : i + 2] for i in range(0, len(hex_str), 2)])
    )


def encode_button_address(bus_address: str) -> str:
    """The three bytes a module stores for a key whose bus address is this.

    The inverse of the library's ``get_button_address``, which maps the
    stored bytes ``b0..b23`` to ``00 | b16..b23 | b8..b15 | b0..b5``.
    Bits 6 and 7 of the stored form are dropped on the way out, so the
    forward map is not injective; this reconstruction sets them to zero,
    the value real captures carry. A bus address only ever uses 22 bits
    — anything wider is not one.
    """
    value = int(bus_address, 16)
    if not 0 <= value < 1 << 22:
        raise ValueError(f"not a bus address: {bus_address!r}")
    bits = format(value, "024b")
    stored = bits[18:24] + "00" + bits[10:18] + bits[2:10]
    return format(int(stored, 2), "06X")


def channel_nibble(module_type: str, channel: int) -> int:
    """The channel as a record stores it.

    Zero-based, except on a roller module: a roller channel drives two
    outputs, and its records count in those, so channel 3 is stored as
    4 (the decoder halves it back).
    """
    index = max(0, channel - 1)
    return index * 2 if module_type == "roller_module" else index


def encode_link(link: Link, module_type: str = "switch_module") -> str:
    """One switch / roller record, in storage order.

    The decoder reads the record byte-reversed, as
    ``[_ t2][key channel][t1 mode][address]``, so build that and turn
    it round.
    """
    read_order = (
        f"0{link.t2:X}"
        f"{link.key:X}{channel_nibble(module_type, link.channel):X}"
        f"{link.t1:X}{link.mode:X}"
        f"{encode_button_address(link.button)}"
    )
    return _reverse_bytes(read_order)


def encode_dimmer_link(link: Link) -> str:
    """One dimmer record, in storage order.

    Eight bytes rather than six: the decoder reads
    ``[pad][pad][_ t2][key channel][t1 mode][address]``, the first two
    bytes reserved on every record seen so far.
    """
    read_order = (
        "0000"
        f"0{link.t2:X}"
        f"{link.key:X}{channel_nibble('dimmer_module', link.channel):X}"
        f"{link.t1:X}{link.mode:X}"
        f"{encode_button_address(link.button)}"
    )
    return _reverse_bytes(read_order)


def encode_audio_trigger(trigger: AudioTrigger) -> str:
    """One audio record, stored as it reads."""
    return f"{trigger.button.upper()}{trigger.function:02X}{trigger.zone:02X}01"


def _registers(stream: str, first_register: int, width: int) -> dict[int, str]:
    """Cut a byte stream into registers, padding the last with filler."""
    out: dict[int, str] = {}
    for index in range(0, len(stream), width):
        chunk = stream[index : index + width]
        out[first_register + index // width] = chunk.ljust(width, "F")
    return out


def encode_module(spec: ModuleSpec) -> dict[str, dict[int, str]]:
    """The module's memory as ``{sub byte: {register: register hex}}``.

    A captured memory on the spec wins: replaying a real module is
    exact where building one is only as good as our understanding.
    """
    if spec.memory:
        return {sub: dict(regs) for sub, regs in spec.memory.items()}
    if not spec.encodable:
        return {}

    sub, first = TABLE_START[spec.type]
    width = register_width(spec.type)
    if spec.type == "audio_module":
        records = "".join(encode_audio_trigger(t) for t in spec.triggers)
        # Six filler bytes, then the count and a zero byte, as a real
        # module lays it out.
        stream = "FFFFFFFFFFFF" + f"{len(spec.triggers):02X}" + "00" + records
    elif spec.type == "dimmer_module":
        stream = "".join(encode_dimmer_link(link) for link in spec.links)
    else:
        stream = "".join(encode_link(link, spec.type) for link in spec.links)
    if not stream:
        return {}
    # A table ends where the filler starts: the scan stops on a register
    # whose tail is all FF, so close with one.
    return {sub: _registers(stream + "F" * width, first, width)}


def module_status_payload(spec: ModuleSpec, family_byte: int) -> str:
    """The five data bytes of a status reply that follow the address echo.

    ``<status> <family> <state> <count A> <count B>``; the counts bound
    the register scan.
    """
    count_a = len(spec.triggers or spec.links)
    count_b = 0xFF
    if spec.type == "dimmer_module":
        count_b = 0
    return f"00{family_byte:02X}0C{count_a:02X}{count_b:02X}"
