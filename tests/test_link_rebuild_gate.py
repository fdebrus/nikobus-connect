"""A module's links are rebuilt only after its whole table was read.

The 0.54.0 rule — a count-driven plan with every register acknowledged —
let a rebuild wipe links after an incomplete read: an acknowledged read
whose data frame the listener dropped on a CRC error, a gap the empty-run
terminator ended the pass on, a table the decoder flagged corrupt. The
gate now also wants as many records decoded as the module announced.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.discovery.base import DecodedCommand
from nikobus_connect.discovery.discovery import NikobusDiscovery


def _discovery(tmp_path) -> NikobusDiscovery:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    return NikobusDiscovery(
        coord, config_dir=str(tmp_path), create_task=MagicMock(),
        button_data={"nikobus_button": {}}, on_button_save=None,
    )


def test_gate_reasons(tmp_path) -> None:
    d = _discovery(tmp_path)
    d._scan_expected_records, d._scan_decoded_records = 12, 12
    assert d._link_table_incomplete("C7C1", count_driven=False) == "scanned with the fixed band"
    assert d._link_table_incomplete("C7C1", count_driven=True) is None
    d._scan_missed_acks = 2
    assert d._link_table_incomplete("C7C1", True) == "2 register read(s) unanswered"
    d._scan_missed_acks = 0
    d.corrupt_link_tables.add("C7C1")
    assert d._link_table_incomplete("c7c1", True) == "link table flagged as corrupt"
    d.corrupt_link_tables.clear()
    d._scan_decoded_records = 9
    assert d._link_table_incomplete("C7C1", True) == "9 of 12 announced record(s) decoded"
    d._scan_decoded_records = 13  # the extra block read past the count may hold one more
    assert d._link_table_incomplete("C7C1", True) is None
    d._scan_expected_records = None
    assert d._link_table_incomplete("C7C1", True) == "record count unknown"


async def test_decoded_records_are_counted_for_the_module_being_scanned(tmp_path) -> None:
    d = _discovery(tmp_path)
    d._module_address = "C7C1"
    d._scan_decoded_records = 0
    empty = DecodedCommand(module_type="switch_module", raw_message="", prefix_hex=None,
                           chunk_hex="", payload_hex="", metadata={})
    await d._handle_decoded_commands("c7c1", [empty, empty])
    await d._handle_decoded_commands("4707", [empty])  # another module: not ours
    assert d._scan_decoded_records == 2


def test_expected_records_come_from_the_status_reply(tmp_path) -> None:
    from nikobus_connect.discovery.discovery import _count_driven_passes
    from nikobus_connect.protocol import ModuleStatus

    # Mirrors _count_bounded_passes: count_b 0xFF means "no second table".
    for count_b, expected in ((0xFF, 12), (0, 12), (5, 17)):
        status = ModuleStatus(address="C7C1", eeprom_error=False, type_code=0x10,
                              record_count_a=12, record_count_b=count_b, raw=b"")
        assert _count_driven_passes("switch_module", status)
        second = status.record_count_b if 0 < status.record_count_b < 0xFF else 0
        assert status.record_count_a + second == expected
