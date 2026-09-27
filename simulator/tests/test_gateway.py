"""The gateway's quirks — the reason there is a simulator at all.

Each of these cost a release to learn from a real installation, and none
of them is visible to a unit test that mocks the bus away.
"""

from __future__ import annotations

import pytest
from nikobus_simulator.gateway import Gateway
from nikobus_simulator.topology import Link, ModuleSpec, load_installation, preset

from nikobus_connect.protocol import (
    FUNC_MODULE_STATUS,
    FUNC_READ_BLOCK8,
    FUNC_READ_BLOCK16,
    make_pc_link_command,
    parse_module_status,
    reply_payload,
)

#: The output functions the library keeps inline rather than as constants.
FUNC_GET_OUTPUTS_1 = 0x12
FUNC_GET_OUTPUTS_2 = 0x17
FUNC_SET_OUTPUTS_1 = 0x15
FUNC_SET_OUTPUTS_2 = 0x16


class Clock:
    """A clock the test winds, so a key release is an explicit step."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def release(self) -> None:
        """Enough time for the next press to count as a new one."""
        self.now += 1.0


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def house(clock: Clock) -> Gateway:
    return Gateway(preset("house"), now=clock)


def _answers(gateway: Gateway, func: int, address: str, args: bytes | None = None) -> list[str]:
    return gateway.handle(make_pc_link_command(func, address, args))


def _one(lines: list[str], prefix: str) -> str:
    (found,) = [line for line in lines if line.startswith(prefix)]
    return found


# --- who is on the other end --------------------------------------------


def test_the_gateway_answers_the_identity_broadcast(house: Gateway) -> None:
    (identity,) = house.handle("#A")
    assert identity.startswith("$18F586")  # 86F5, low byte first
    assert reply_payload(identity)[3] == 0x50  # the PC-Link's family


def test_a_pc_logic_gateway_says_so() -> None:
    gateway = Gateway(
        load_installation({"gateway": {"address": "9A3C", "family": "pc_logic"}})
    )
    (identity,) = gateway.handle("#A")
    assert reply_payload(identity)[3] == 0x40


def test_the_interface_lines_of_the_handshake_produce_nothing(house: Gateway) -> None:
    for line in ("++++", "ATH0", "ATZ", "#L0", "#E1", ""):
        assert house.handle(line) == []


# --- the withheld acknowledgement ---------------------------------------


def test_a_query_nothing_answers_is_not_acknowledged_until_the_next_frame(
    house: Gateway,
) -> None:
    """The quirk behind four releases of false "nothing answered" warnings.

    The handshake's status query to the null address earns an
    acknowledgement, but the interface keeps it until it has something
    else to send — so a probe that waits for it on a quiet bus waits
    forever.
    """
    assert _answers(house, FUNC_MODULE_STATUS, "0000") == []
    assert house.handle("#A") == ["$0511", _one(house.handle("#A"), "$18")]


def test_an_acknowledgement_travels_in_front_of_the_next_answer(
    house: Gateway,
) -> None:
    assert _answers(house, FUNC_MODULE_STATUS, "0000") == []
    lines = _answers(house, FUNC_MODULE_STATUS, "4707")
    assert lines[0] == "$0511"  # the held one
    assert lines[1] == "$0511"  # this query's own
    assert lines[2].startswith("$18")


def test_a_query_that_is_answered_is_acknowledged_at_once(house: Gateway) -> None:
    lines = _answers(house, FUNC_MODULE_STATUS, "4707")
    assert lines[0] == "$0511"
    assert len(lines) == 2


# --- a press the host sent -----------------------------------------------


def test_the_gateway_never_relays_the_hosts_own_press(house: Gateway) -> None:
    """A host learns nothing from its own press; it has to ask."""
    assert house.handle("#N0B1380") == []


def test_a_press_drives_the_channel_it_is_linked_to(house: Gateway) -> None:
    house.handle("#N0B1380")
    answer = _one(_answers(house, FUNC_GET_OUTPUTS_1, "4707"), "$1C")
    # The address echo, a zero byte, then the six channels of group 1.
    assert reply_payload(answer)[3:9].hex().upper() == "FF0000000000"


def test_an_on_off_link_toggles_on_every_fresh_press(
    house: Gateway, clock: Clock
) -> None:
    house.handle("#N0B1380")
    assert house.modules["4707"].outputs[0] == 0xFF
    clock.release()
    house.handle("#N0B1380")
    assert house.modules["4707"].outputs[0] == 0x00
    clock.release()
    house.handle("#N0B13A0")  # channel 7, M05 impulse
    assert house.modules["4707"].outputs[6] == 0xFF


def test_a_key_still_down_is_one_press_however_often_it_repeats(
    house: Gateway, clock: Clock
) -> None:
    """Why the host may send a press several times over.

    A held key puts its address on the bus again and again, so a module
    counts the burst once. Without that, sending a press three times for
    reliability would leave an impulse link back where it started.
    """
    for _ in range(3):
        house.handle("#N0B1380")
    assert house.modules["4707"].outputs[0] == 0xFF
    clock.release()
    for _ in range(3):
        house.handle("#N0B1380")
    assert house.modules["4707"].outputs[0] == 0x00


def test_two_different_keys_in_the_same_moment_are_two_presses(
    house: Gateway,
) -> None:
    house.handle("#N0B1380")
    house.handle("#N0B1390")
    assert house.modules["4707"].outputs[0] == 0xFF
    assert house.modules["4707"].outputs[1] == 0xFF


def test_a_press_no_module_is_linked_to_moves_nothing(house: Gateway) -> None:
    before = {a: bytes(m.outputs) for a, m in house.modules.items()}
    house.handle("#N123456")
    assert {a: bytes(m.outputs) for a, m in house.modules.items()} == before


def test_one_press_reaches_every_module_linked_to_it(house: Gateway) -> None:
    # 0B1380 drives channel 1 of the switch module and of the dimmer.
    house.handle("#N0B1380")
    assert house.modules["4707"].outputs[0] == 0xFF
    assert house.modules["0E6C"].outputs[0] == 0xFF


def test_a_roller_link_cycles_open_stop_close(
    house: Gateway, clock: Clock
) -> None:
    roller = house.modules["9105"]
    for expected in (0x01, 0x00, 0x02, 0x01):
        house.handle("#N0B13B0")
        assert roller.outputs[0] == expected
        clock.release()


# --- reading and writing the outputs ------------------------------------


def test_both_output_groups_of_a_twelve_channel_module_can_be_read() -> None:
    gateway = Gateway(preset("twelve"))
    gateway.modules["81F6"].outputs[6] = 0xFF  # channel 7, in group 2
    group1 = _one(_answers(gateway, FUNC_GET_OUTPUTS_1, "81F6"), "$1C")
    group2 = _one(_answers(gateway, FUNC_GET_OUTPUTS_2, "81F6"), "$1C")
    assert reply_payload(group1)[3:9].hex().upper() == "000000000000"
    assert reply_payload(group2)[3:9].hex().upper() == "FF0000000000"


def test_a_state_answer_says_nothing_about_which_group_it_is(house: Gateway) -> None:
    """Both groups answer in the same shape — the query is the only clue.

    This is why a host cabled to a Feedback Module, whose queries it
    never sees, files group 2's answer under group 1.
    """
    group1 = _one(_answers(house, FUNC_GET_OUTPUTS_1, "4707"), "$1C")
    group2 = _one(_answers(house, FUNC_GET_OUTPUTS_2, "4707"), "$1C")
    assert group1 == group2


@pytest.mark.parametrize(
    ("func", "channel"), [(FUNC_SET_OUTPUTS_1, 0), (FUNC_SET_OUTPUTS_2, 6)]
)
def test_a_write_lands_in_the_group_it_addresses(
    house: Gateway, func: int, channel: int
) -> None:
    args = bytes([0xFF, 0, 0, 0, 0, 0, 0xFF])
    lines = _answers(house, func, "4707", args)
    assert house.modules["4707"].outputs[channel] == 0xFF
    assert lines[0].startswith("$05")
    # The answer a host waits for: FF, the address, and no payload CRC.
    assert lines[1].startswith("$0EFFF546") is False
    assert lines[1].startswith("$0EFF0747")


def test_a_write_does_not_disturb_the_other_group(house: Gateway) -> None:
    _answers(house, FUNC_SET_OUTPUTS_2, "4707", bytes([0xFF] * 6 + [0xFF]))
    assert bytes(house.modules["4707"].outputs[:6]) == bytes(6)


# --- reading a module's memory ------------------------------------------


def test_a_register_read_returns_the_sixteen_bytes_of_the_link_table(
    house: Gateway,
) -> None:
    lines = _answers(house, FUNC_READ_BLOCK16, "4707", bytes([0x10, 0x00]))
    answer = _one(lines, "$2E")
    assert len(reply_payload(answer)) == 2 + 16


def test_a_dimmer_answers_its_own_read_function_with_eight_bytes(
    house: Gateway,
) -> None:
    lines = _answers(house, FUNC_READ_BLOCK8, "0E6C", bytes([0x20, 0x00]))
    answer = _one(lines, "$1E")
    assert len(reply_payload(answer)) == 2 + 8


def test_a_register_outside_the_table_reads_as_filler(house: Gateway) -> None:
    answer = _one(_answers(house, FUNC_READ_BLOCK16, "4707", bytes([0x39, 0x00])), "$2E")
    assert reply_payload(answer)[2:].hex().upper() == "FF" * 16


def test_the_status_reply_carries_the_family_and_the_record_count(
    house: Gateway,
) -> None:
    answer = _one(_answers(house, FUNC_MODULE_STATUS, "4707"), "$18")
    status = parse_module_status(reply_payload(answer), "4707")
    assert status.type_code == 0x10  # a switch module
    assert status.record_count_a == 3
    assert not status.eeprom_error


def test_a_module_that_is_not_there_answers_nothing(house: Gateway) -> None:
    assert _answers(house, FUNC_MODULE_STATUS, "DEAD") == []
    assert _answers(house, FUNC_GET_OUTPUTS_1, "DEAD") == []


def test_a_module_with_no_table_is_still_on_the_bus() -> None:
    gateway = Gateway(
        load_installation(
            {"modules": [{"address": "1234", "type": "feedback_module"}]}
        )
    )
    answer = _one(_answers(gateway, FUNC_MODULE_STATUS, "1234"), "$18")
    assert parse_module_status(reply_payload(answer), "1234").type_code == 0xA0


def test_a_declared_module_can_be_looked_up_either_way() -> None:
    installation = load_installation(
        {
            "modules": [
                {
                    "address": "4707",
                    "type": "switch_module",
                    "links": [{"button": "0B1380", "channel": 1}],
                }
            ]
        }
    )
    assert installation.module("4707") is installation.module("4707")
    assert installation.module("ffff") is None
    assert installation.modules[0].links == [Link(button="0B1380", channel=1)]


def test_a_module_address_must_be_four_hex_digits() -> None:
    with pytest.raises(ValueError, match="four hex digits|4 hex digits"):
        ModuleSpec(address="47070", type="switch_module")
