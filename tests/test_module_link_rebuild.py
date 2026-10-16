"""A fully read module's links are filed afresh, not added to.

The store only ever added links, so it kept what older versions had
filed. On a real install (Nikobus-HA, 2026-10) the store of an 8-key
feedback button, BP9 at 106218, held every first-row link a second time
on the key below it (an older merge read the sibling address's keys 0-3
as the second row), the same link under an older mode label or timer,
and a PC-Link-memory copy of a link no module executes. Once a module's
own table has been read in full, every link stored for it is replaced by
what the table holds now; links to modules that could not be read stay.
The fixture is that button's stored entry as it was.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock

from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.fileio import (
    REGISTRY_RECORD_SOURCES,
    merge_linked_modules,
    strip_module_links,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bp9_106218_store_2026-10-16.json"

#: What the Nikobus software shows for BP9: one link per key.
EXPECTED = {
    "1A": {("C7C1", 5)},
    "1B": {("C7C1", 6)},
    "1C": {("C7C1", 7)},
    "1D": {("C95D", 3)},
    "2A": {("C95D", 2)},
    "2B": {("C7C1", 4)},
    "2C": {("C7C1", 11)},
    "2D": {("C9D7", 11)},
}


def _rec(module, channel, mode, button, t2=None, source="output_module_table"):
    return {
        "module_address": module, "channel": channel, "mode": mode, "t1": None, "t2": t2,
        "button_address": button, "record_source": source,
    }


#: This session's records, as the decoders give them (keys as decoded).
SESSION = {
    ("106219", 1, None): [_rec("C7C1", 5, "M05 (Impulse)", "106219")],
    ("106219", 3, None): [_rec("C7C1", 6, "M05 (Impulse)", "106219")],
    ("106219", 0, None): [_rec("C7C1", 7, "M05 (Impulse)", "106219")],
    ("C61182", 3, None): [_rec("C7C1", 4, "M05 (Impulse)", "106218")],
    ("061182", 0, None): [
        _rec("C7C1", 11, "M05 (Impulse)", "106218"),
        # The PC-Link still remembers a link C7C1 does not hold.
        _rec("C7C1", 6, "M02 (On + Operating time)", "106218", source="pc_link_registry"),
    ],
    ("106219", 2, None): [_rec("C95D", 3, "M14 (Dim on/off memory (1 button))", "106219", "1 s")],
    ("861182", 1, None): [_rec("C95D", 2, "M14 (Dim on/off memory (1 button))", "106218", "1 s")],
    ("461182", 2, None): [_rec("C9D7", 11, "M05 (Impulse)", "106218")],
}


def _store() -> dict:
    return {"nikobus_button": {"106218": json.loads(FIXTURE.read_text())}}


def _links(button_data, key):
    op = button_data["nikobus_button"]["106218"]["operation_points"][key]
    return [
        (block["module_address"], out["channel"], out["mode"], out.get("t2"), out.get("record_source"))
        for block in op.get("linked_modules", [])
        for out in block["outputs"]
    ]


def _targets(button_data, key):
    return {(m, c) for m, c, *_ in _links(button_data, key)}


def _discovery(tmp_path, button_data, module_data=None) -> NikobusDiscovery:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    d = NikobusDiscovery(
        coord, config_dir=str(tmp_path), create_task=MagicMock(),
        button_data=button_data, on_button_save=None,
    )
    d._module_data = module_data or {"nikobus_module": {}}
    return d


def test_the_fixture_shows_the_reported_extras() -> None:
    data = _store()
    assert _targets(data, "2A") == {("C7C1", 5), ("C95D", 2)}
    assert _targets(data, "2C") == {("C7C1", 6), ("C7C1", 7), ("C7C1", 11)}
    assert len(_links(data, "1D")) == 2  # same link, two timers


def test_strip_removes_every_link_to_the_modules_and_nothing_else() -> None:
    data = _store()
    removed = strip_module_links(data, {"c7c1"})
    assert removed == 10  # 1A, 1B, 1C, 2A, two on 2B, four on 2C
    for key in EXPECTED:
        assert not {m for m, _ in _targets(data, key)} & {"C7C1"}
    assert _targets(data, "2D") == {("C95D", 3), ("C9D7", 11)}  # untouched


async def test_rebuilding_all_three_modules_leaves_one_link_per_key(tmp_path) -> None:
    data = _store()
    module_data = {"nikobus_module": {a: {"module_type": "switch_module"} for a in ("C7C1", "C95D", "C9D7")}}
    d = _discovery(tmp_path, data, module_data)
    d._accumulated_command_mapping = copy.deepcopy(SESSION)
    for module in ("C7C1", "C95D", "C9D7"):
        await d._rebuild_module_links(module)
    for key, expected in EXPECTED.items():
        assert _targets(data, key) == expected, key
        assert len(_links(data, key)) == 1, key
    assert all(src == "output_module_table" for k in EXPECTED for *_, src in _links(data, k))
    assert all(module_data["nikobus_module"][a].get("links_read_at") for a in ("C7C1", "C95D", "C9D7"))


async def test_a_module_not_read_keeps_its_stored_links(tmp_path) -> None:
    data = _store()
    d = _discovery(tmp_path, data)
    d._accumulated_command_mapping = copy.deepcopy(SESSION)
    await d._rebuild_module_links("C7C1")
    # C95D was not read in full: its extra copy on 2D stays for now.
    assert _targets(data, "2D") == {("C95D", 3), ("C9D7", 11)}
    assert _targets(data, "2C") == {("C7C1", 11)}


async def test_a_registry_copy_does_not_come_back_once_the_module_was_read(tmp_path) -> None:
    data = _store()
    d = _discovery(tmp_path, data)
    d._accumulated_command_mapping = copy.deepcopy(SESSION)
    await d._rebuild_module_links("C7C1")
    # A later merge of the whole session (end of discovery) must not re-add it.
    merge_linked_modules(data, SESSION, d._authoritative_link_modules())
    assert ("C7C1", 6) not in _targets(data, "2C")


def test_registry_copies_are_skipped_for_modules_read_in_an_earlier_session(tmp_path) -> None:
    data = {"nikobus_button": {"106218": json.loads(FIXTURE.read_text())}}
    strip_module_links(data, {"C7C1"})
    d = _discovery(tmp_path, data, {"nikobus_module": {"C7C1": {"links_read_at": "2026-10-16T09:00:00+00:00"}}})
    assert d._authoritative_link_modules() == {"C7C1"}
    merge_linked_modules(data, SESSION, d._authoritative_link_modules())
    assert ("C7C1", 6) not in _targets(data, "2C")
    assert ("C7C1", 11) in _targets(data, "2C")


def test_without_authority_registry_records_merge_as_before() -> None:
    data = {"nikobus_button": {"106218": json.loads(FIXTURE.read_text())}}
    strip_module_links(data, {"C7C1"})
    merge_linked_modules(data, SESSION)
    assert ("C7C1", 6) in _targets(data, "2C")
    assert REGISTRY_RECORD_SOURCES == {"pc_link_registry", "pc_logic_registry"}
