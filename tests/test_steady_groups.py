"""Which output groups a host may write without reading them first.

A group write carries six outputs from the host's buffer, so the group
is read first when the buffer may be behind (Nikobus-HA #557). Since
0.56.1 that was every group older than three seconds: half a second
before every relay click. ``steady_groups`` names the groups where it
is not needed between two refreshes, because nothing but a key telegram
changes their outputs; the handler then reads those only when the host
forgot their state (a press) or a refresh was missed.

The stores are built with the real switch decoder and merge, so the
mode and timer strings are the ones a scan files.
"""

from __future__ import annotations

import pytest

from nikobus_connect import const
from nikobus_connect.command import NikobusCommandHandler
from nikobus_connect.discovery.discovery import add_to_command_mapping
from nikobus_connect.discovery.fileio import merge_linked_modules
from nikobus_connect.discovery.protocol import decode_command_payload
from nikobus_connect.discovery.store import steady_groups
from tests.test_set_coalescing import _Connection, _drain, _Listener
from tests.test_two_button_plates import _Coord, _inventory, _stored_plate_address

PLATE = "1843B4"  # a 4-key plate
SWITCH = "4707"

#: Switch link mode nibbles, as ``SWITCH_MODE_MAPPING`` numbers them.
ON_OFF, ON_TIME, OFF_TIME, IMPULSE, DELAYED_OFF, FLASHING = 0, 1, 2, 4, 5, 7


def _stores(*links: tuple[int, int, int, int], scanned: bool = True, module_type: str = "switch_module"):
    """Stores with ``(key, channel, mode, timer nibble)`` links on ``SWITCH``."""
    buttons = _inventory(PLATE, "06")
    coord = _Coord(buttons)
    mapping: dict = {}
    for key, channel, mode, timer in links:
        record = f"FF{key:X}{channel - 1:X}{timer:X}{mode:X}{_stored_plate_address(PLATE)}"
        decoded = decode_command_payload(
            record, "switch_module", coord, module_address=SWITCH, module_channel_count=12
        )
        assert decoded is not None
        add_to_command_mapping(mapping, decoded, SWITCH, None)
    merge_linked_modules(buttons, mapping)
    entry: dict = {"module_type": module_type, "channels": [{} for _ in range(12)]}
    if scanned:
        entry["links_read_at"] = "2026-11-09T10:00:00+00:00"
    return buttons, {"nikobus_module": {SWITCH: entry}}


def test_plain_on_off_and_impulse_links_leave_both_groups_steady() -> None:
    assert steady_groups(*_stores((1, 1, ON_OFF, 0xF), (3, 8, IMPULSE, 0xF))) == {(SWITCH, 1), (SWITCH, 2)}


def test_a_module_without_links_is_steady_once_its_table_was_read() -> None:
    assert steady_groups(*_stores()) == {(SWITCH, 1), (SWITCH, 2)}


def test_on_and_off_with_an_operating_time_of_zero_are_steady() -> None:
    buttons, modules = _stores((1, 2, ON_TIME, 0x0), (3, 2, OFF_TIME, 0x0))
    links = buttons["nikobus_button"][PLATE]["operation_points"]
    assert {o["t1"] for op in links.values() for b in op.get("linked_modules", []) for o in b["outputs"]} == {"0s"}
    assert steady_groups(buttons, modules) == {(SWITCH, 1), (SWITCH, 2)}


@pytest.mark.parametrize(
    ("mode", "timer"),
    [
        pytest.param(ON_TIME, 0x1, id="on-for-1s"),
        pytest.param(OFF_TIME, 0x5, id="off-time-not-in-the-table"),
        pytest.param(DELAYED_OFF, 0x2, id="delayed-off"),
        pytest.param(FLASHING, 0xF, id="flashing"),
    ],
)
def test_a_link_with_a_timer_takes_its_group_out(mode: int, timer: int) -> None:
    # The link is on channel 8: group 2 is out, group 1 stays.
    assert steady_groups(*_stores((1, 1, ON_OFF, 0xF), (3, 8, mode, timer))) == {(SWITCH, 1)}


def test_a_module_whose_table_was_not_read_in_full_is_never_steady() -> None:
    assert steady_groups(*_stores((1, 1, ON_OFF, 0xF), scanned=False)) == set()


@pytest.mark.parametrize("module_type", ["dimmer_module", "roller_module"])
def test_dimmer_and_roller_groups_are_never_steady(module_type: str) -> None:
    assert steady_groups(*_stores(module_type=module_type)) == set()


def test_a_calendar_channel_takes_its_group_out() -> None:
    buttons, modules = _stores((1, 1, ON_OFF, 0xF))
    buttons["nikobus_button"]["E00320"] = {
        "calendar_channel": "CH001A",
        "operation_points": {
            "1A": {"bus_address": "E00320", "linked_modules": [
                {"module_address": SWITCH, "outputs": [{"channel": 9, "mode": "M01 (On / off)"}]},
            ]},
        },
    }
    assert steady_groups(buttons, modules) == {(SWITCH, 1)}


# --- the handler -------------------------------------------------------------------


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(const, "COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.COMMAND_EXECUTION_DELAY", 0.0)
    monkeypatch.setattr("nikobus_connect.command.SET_COALESCE_WINDOW", 0.02)
    listener = _Listener()
    connection = _Connection(listener)
    h = NikobusCommandHandler(connection=connection, listener=listener, module_states={})
    h._test_connection = connection  # type: ignore[attr-defined]
    return h


async def _write(handler, address: str = SWITCH, channel: int = 1) -> int:
    """One set-output; how many reads the module was sent for it."""
    conn = handler._test_connection
    before = len(conn.reads)
    await handler.set_output_state(address, channel, 0xFF)
    await handler.start()
    try:
        await _drain(handler)
    finally:
        await handler.stop()
    return len(conn.reads) - before


def _aged(handler, seconds: float, address: str = SWITCH, group: int = 1) -> None:
    """The group's state was last known ``seconds`` ago."""
    handler.apply_group_state(address, group, "00" * 6)
    handler._group_known_at[(address, group)] -= seconds


async def test_a_steady_group_is_written_without_a_read_within_the_hosts_cadence(handler) -> None:
    handler.set_steady_groups({(SWITCH, 1)}, max_age=600)
    _aged(handler, 60)
    assert await _write(handler) == 0


async def test_an_ordinary_group_of_the_same_age_is_still_read_first(handler) -> None:
    handler.set_steady_groups({(SWITCH, 2)}, max_age=600)
    _aged(handler, 60)
    assert await _write(handler) == 1


async def test_a_steady_group_older_than_the_cadence_is_read_first(handler) -> None:
    handler.set_steady_groups({(SWITCH, 1)}, max_age=600)
    _aged(handler, 700)
    assert await _write(handler) == 1


@pytest.mark.parametrize(
    "forget",
    [
        pytest.param((SWITCH, 1), id="the-group"),
        pytest.param((SWITCH,), id="the-module"),
        pytest.param((), id="everything"),
    ],
)
async def test_a_press_the_host_reports_makes_the_next_write_read_first(handler, forget) -> None:
    handler.set_steady_groups({(SWITCH, 1)}, max_age=600)
    _aged(handler, 1)
    handler.invalidate_group_state(*forget)
    assert await _write(handler) == 1


async def test_forgetting_one_group_leaves_the_others_known(handler) -> None:
    handler.set_steady_groups({(SWITCH, 1), ("C9A5", 1)}, max_age=600)
    _aged(handler, 1)
    _aged(handler, 1, address="C9A5")
    handler.invalidate_group_state("C9A5", 1)
    assert await _write(handler) == 0


async def test_no_group_is_read_first_when_the_host_turned_it_off(handler) -> None:
    handler.read_before_write_s = None
    assert await _write(handler) == 0


async def test_a_reconnect_forgets_every_group(handler) -> None:
    handler.set_steady_groups({(SWITCH, 1)}, max_age=600)
    _aged(handler, 1)
    handler.reset()
    assert handler.group_state_age(SWITCH, 1) is None


def test_steady_groups_are_replaced_and_normalised(handler) -> None:
    handler.set_steady_groups({("c9a5", 1)}, max_age=600)
    handler.set_steady_groups({("4707", "2")}, max_age=240)
    assert handler.steady_groups == frozenset({("4707", 2)})
