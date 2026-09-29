"""The memory the simulator builds is checked by the library's decoders.

A simulator that invents its own memory layout proves nothing. Every
test here hands the bytes the encoders produce to the same decoder the
integration runs against real hardware, through the same chunk layer
(six- or eight-byte records, reversed before decode), and asks for the
links back. The audio module is checked byte for byte against a capture
of a real 05-205 instead — that table's layout is known exactly.
"""

from __future__ import annotations

import pytest
from nikobus_simulator.encoding import (
    encode_audio_trigger,
    encode_button_address,
    encode_module,
    module_status_payload,
    register_width,
)
from nikobus_simulator.topology import (
    AUDIO_FUNCTION_SLOTS,
    Link,
    ModuleSpec,
    preset,
)

from nikobus_connect.discovery.audio_decoder import split_link_table
from nikobus_connect.discovery.protocol import (
    decode_command_payload,
    get_button_address,
)

#: Record length in hex digits, per family, as the chunk layer walks it.
CHUNK_HEX = {"switch_module": 12, "roller_module": 12, "dimmer_module": 16}


def _links_back(spec: ModuleSpec) -> list[dict]:
    """Decode a module's link tables the way the discovery scan does.

    The dimmer's configuration block (``0xF8..0xFF``) is memory but not
    a link table, and the scan does not read it since 0.40.1 — because
    fed to the link decoder it produces phantom buttons. Walk it here
    and those phantoms would show up as "links".
    """
    memory = encode_module(spec)
    stream = "".join(
        registers[key]
        for registers in memory.values()
        for key in sorted(registers)
        if not (spec.type == "dimmer_module" and key >= 0xF8)
    )
    chunk = CHUNK_HEX[spec.type]
    out = []
    for index in range(0, len(stream) - chunk + 1, chunk):
        decoded = decode_command_payload(
            stream[index : index + chunk],
            spec.type,
            None,
            module_address=spec.address,
            reverse_before_decode=True,
            module_channel_count=spec.channels,
        )
        if decoded:
            out.append(decoded)
    return out


# --- the bus address a record stores -------------------------------------


@pytest.mark.parametrize(
    "bus_address",
    ["000000", "0B1380", "0B1390", "0B13A0", "0B13B0", "20958A", "1A2B3C", "3FFFFF"],
)
def test_the_library_reads_back_the_bus_address_we_encoded(bus_address: str) -> None:
    assert get_button_address(encode_button_address(bus_address)) == bus_address


def test_a_key_of_the_same_plate_differs_only_in_the_stored_prefix() -> None:
    # 0B1380..0B13B0 are the four keys of one plate; a module stores them
    # as 004E2C, 404E2C, 804E2C, C04E2C.
    stored = [encode_button_address(f"0B13{n:02X}") for n in (0x80, 0x90, 0xA0, 0xB0)]
    assert stored == ["004E2C", "404E2C", "804E2C", "C04E2C"]


def test_an_address_wider_than_a_bus_address_is_refused() -> None:
    with pytest.raises(ValueError, match="not a bus address"):
        encode_button_address("FF0000")


# --- switch, roller, dimmer ----------------------------------------------


def test_a_switch_link_comes_back_out_of_the_decoder() -> None:
    spec = ModuleSpec(
        address="4707",
        type="switch_module",
        channels=12,
        links=[Link(button="0B1380", channel=7, mode=4, key=2, t1=3)],
    )
    (decoded,) = _links_back(spec)
    assert decoded["button_address"] == "0B1380"
    assert decoded["channel"] == 7
    assert decoded["key_raw"] == 2
    assert decoded["M"].startswith("M05")
    assert decoded["t1_raw"] == 3
    assert decoded["record_source"] == "output_module_table"


def test_every_switch_link_of_a_twelve_channel_module_survives() -> None:
    # The shape behind issue #148: channels 7..12 live in group 2, and a
    # module that stores only those is where a group got dropped.
    spec = preset("twelve").modules[0]
    decoded = _links_back(spec)
    assert [d["channel"] for d in decoded] == [7, 8, 9, 10, 11, 12]
    assert {d["button_address"] for d in decoded} == {"20958A"}


def test_a_roller_link_comes_back_out_of_the_decoder() -> None:
    # A roller channel drives two outputs and its records count in those,
    # so the stored nibble is twice the index — the decoder halves it.
    spec = ModuleSpec(
        address="9105",
        type="roller_module",
        channels=6,
        links=[Link(button="0B13B0", channel=3, mode=1, key=3)],
    )
    (decoded,) = _links_back(spec)
    assert decoded["button_address"] == "0B13B0"
    assert decoded["channel"] == 3
    assert decoded["channel_raw"] == 4
    assert decoded["key_raw"] == 3


def test_a_dimmer_link_comes_back_out_of_the_decoder() -> None:
    spec = ModuleSpec(
        address="0E6C",
        type="dimmer_module",
        channels=12,
        links=[Link(button="0B1380", channel=4, mode=0, key=0, t2=2)],
    )
    (decoded,) = _links_back(spec)
    assert decoded["button_address"] == "0B1380"
    assert decoded["channel"] == 4
    assert decoded["t2_raw"] == 2


def test_the_dimmer_keeps_one_record_per_eight_byte_register() -> None:
    spec = ModuleSpec(
        address="0E6C",
        type="dimmer_module",
        channels=12,
        links=[Link(button="0B1380", channel=c) for c in (1, 2, 3)],
    )
    registers = encode_module(spec)["00"]
    assert register_width("dimmer_module") == 16
    link_table = [r for r in registers if r < 0xF8]
    assert link_table == [0x20, 0x21, 0x22, 0x23]
    assert all(len(v) == 16 for v in registers.values())


def test_a_dimmer_carries_its_configuration_block() -> None:
    """Registers 0xF8..0xFF hold settings, not links.

    They are there so that a library which reads them and link-decodes
    them — as 0.39.0 and 0.40.0 did — is caught by the end-to-end scan:
    the real 116D bytes decode into four phantom buttons.
    """
    spec = {m.type: m for m in preset("house").modules}["dimmer_module"]
    registers = encode_module(spec)["00"]
    assert {r for r in registers if r >= 0xF8} == set(range(0xF8, 0x100))
    assert registers[0xFA] == "282828282828F8F8"
    assert registers[0xFB] == "F8F8F8F0F8F8F8F8"


def test_each_family_starts_its_table_where_the_scan_looks() -> None:
    house = {m.type: m for m in preset("house").modules}
    assert min(encode_module(house["switch_module"])["00"]) == 0x10
    assert min(encode_module(house["roller_module"])["00"]) == 0x10
    assert min(encode_module(house["dimmer_module"])["00"]) == 0x20


def test_a_table_closes_with_a_register_of_filler() -> None:
    # The scan stops on a register whose tail is all 0xFF; without one it
    # would read to the end of the plan.
    spec = preset("house").modules[0]
    registers = encode_module(spec)["00"]
    last = registers[max(registers)]
    assert set(last) == {"F"}


def test_a_module_with_no_links_holds_nothing() -> None:
    spec = ModuleSpec(address="4707", type="switch_module", channels=12)
    assert encode_module(spec) == {}


def test_a_module_the_encoders_cannot_build_is_still_on_the_bus() -> None:
    spec = ModuleSpec(address="86F5", type="pc_link")
    assert encode_module(spec) == {}


def test_a_captured_memory_is_replayed_untouched() -> None:
    spec = ModuleSpec(
        address="4707",
        type="switch_module",
        channels=12,
        links=[Link(button="0B1380", channel=1)],
        memory={"00": {0x10: "A" * 32}},
    )
    assert encode_module(spec) == {"00": {0x10: "A" * 32}}


# --- what the status reply tells the scan --------------------------------


def test_the_status_reply_counts_the_records_the_scan_must_read() -> None:
    spec = preset("house").modules[0]
    payload = module_status_payload(spec, 0x10)
    assert payload == "00100C03FF"  # status, family, state, count A, count B


def test_a_dimmers_second_bank_is_declared_empty() -> None:
    spec = {m.type: m for m in preset("house").modules}["dimmer_module"]
    assert module_status_payload(spec, 0x30).endswith("0100")


# --- the audio module, byte for byte ------------------------------------


def test_a_generated_audio_table_matches_a_real_module(
    audio_8334_registers: dict[str, str],
) -> None:
    """Four declared zones reproduce the 32 grid records of module 8334.

    The capture holds three further records off the grid (addresses in a
    second bank), so the generated table is compared against the grid
    records alone — in order, byte for byte.
    """
    captured = split_link_table(
        "".join(audio_8334_registers[k] for k in sorted(audio_8334_registers))
    )
    spec = preset("audio").modules[0]
    built = encode_module(spec)["01"]
    generated = split_link_table("".join(built[k] for k in sorted(built)))

    declared = {t.button for t in spec.triggers}
    assert len(captured) == 35
    # The four-zone grid, plus the Power trigger the preset declares.
    assert len(generated) == 33
    assert [r for r in captured if r[:6] in declared] == generated
    # What is left is programming from a second bank — nothing the
    # declaration claims to reproduce.
    assert [r[:6] for r in captured if r[:6] not in declared] == [
        "6E03CF",
        "EE03CF",
    ]


def test_the_power_object_is_encoded_as_the_module_stores_it(
    audio_8334_registers: dict[str, str],
) -> None:
    """``0x08`` in the object byte is the Power object, not a zone."""
    spec = preset("audio").modules[0]
    (power,) = [t for t in spec.triggers if t.is_power]
    assert power.button == "8483CF"
    assert encode_audio_trigger(power) == "8483CF080801"
    captured = split_link_table(
        "".join(audio_8334_registers[k] for k in sorted(audio_8334_registers))
    )
    assert "8483CF080801" in captured


def test_the_audio_table_starts_where_the_audio_scan_looks(
    audio_8334_registers: dict[str, str],
) -> None:
    built = encode_module(preset("audio").modules[0])["01"]
    assert min(built) == 0x38
    assert min(int(k, 16) for k in audio_8334_registers) == 0x38


def test_an_audio_module_can_have_fewer_zones() -> None:
    from nikobus_simulator.topology import load_installation

    one_zone = load_installation(
        {"modules": [{"address": "8334", "type": "audio_module", "zones": 1}]}
    )
    triggers = one_zone.modules[0].triggers
    assert len(triggers) == len(AUDIO_FUNCTION_SLOTS)
    assert {t.target for t in triggers} == {0}


def test_more_zones_than_a_module_has_is_refused() -> None:
    from nikobus_simulator.topology import load_installation

    with pytest.raises(ValueError, match="1..4 zones"):
        load_installation(
            {"modules": [{"address": "8334", "type": "audio_module", "zones": 5}]}
        )
