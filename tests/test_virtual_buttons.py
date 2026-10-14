"""Buttons that exist only in programming get an entry of their own.

Two kinds of address turn up in output modules' link tables that no
wall plate can emit:

* the PC-Logic's logic outputs. On a real install (2026-10) PC-Logic
  ``8DC8`` fired ``#N002087`` and ``#N402087`` for logic function L01's
  two events, 0.6 s after the key that triggered it; switch module
  ``81F6`` held the link as canonical ``384100`` key 0, mode M01 — a
  two-key mode, so the plate's 1D (``402087``) is the off event. L02
  ("alarm on", 22 outputs off) is the next output, ``384102`` /
  ``102087``. The merge used to drop both as unknown buttons and the
  CF classifier then claimed them as central functions;
* the Nikobus software's virtual input banks (``3CF000``–``3CFFFF``),
  which programs and scenes fire.

Which ``38xxxx`` addresses are a PC-Logic's outputs comes from the
module's own logic-output table, read during its scan; the rest of the
class stays with the central functions.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from nikobus_connect.discovery import fileio
from nikobus_connect.discovery.discovery import NikobusDiscovery
from nikobus_connect.discovery.fileio import (
    VIRTUAL_BUTTON_MODEL,
    VIRTUAL_BUTTON_TYPE,
    merge_discovered_buttons,
    merge_discovered_modules,
    merge_linked_modules,
)
from nikobus_connect.discovery.mapping import KEY_MAPPING
from nikobus_connect.discovery.pc_logic_decoder import (
    LOGIC_OUTPUT_TABLE_MIN,
    PcLogicDecoder,
    parse_logic_output_table,
)
from nikobus_connect.discovery.protocol import (
    VIRTUAL_CLASS_BANK,
    VIRTUAL_CLASS_PC_LOGIC,
    convert_nikobus_address,
    virtual_button_class,
    virtual_plate_address,
    virtual_record_bus_address,
)

# The first rows of PC-Logic 8DC8's logic-output table, as the scan
# logged them (chunk payloads), and the last two.
TABLE_CHUNKS = [
    "FFFFFFFFFFFFFFFF0000002087000010",
    "20870000082087000018208700000420",
    "87000014208700000C208700001C2087",
    "0000022087000012208700000A208700",
    "001A2087000006208700001620870000",
    "0E208700001E20870000012087000011",
    "0F008700001F00870000008087000010",
    "0F808700001F8087FFFFFFFFFFFFFFFF",
]
TABLE_8DC8 = [
    "002087", "102087", "082087", "182087", "042087", "142087", "0C2087", "1C2087",
    "022087", "122087", "0A2087", "1A2087", "062087", "162087", "0E2087", "1E2087",
    "012087", "112087",
]

# Switch module 81F6: L01 (ch 6, on/off) and L02's all-off, as decoded.
L01_ON = {
    "module_address": "81F6", "channel": 6, "mode": "M01 (On / off)",
    "t1": "0s", "t2": None, "button_address": "384100",
    "record_source": "output_module_table",
}
L02_OFF = {
    "module_address": "81F6", "channel": 4, "mode": "M03 (Off + Operating time)",
    "t1": "0s", "t2": None, "button_address": "384102",
    "record_source": "output_module_table",
}
ALL_OFF_DIMMER = {
    "module_address": "C5C1", "channel": 1, "mode": "M06 (Off + Operating time)",
    "t1": "0s", "t2": None, "button_address": "384102",
    "record_source": "output_module_table",
}
PETER_RECORDS = {
    ("384100", 0, None): [dict(L01_ON)],
    ("384102", 0, None): [dict(L02_OFF), dict(ALL_OFF_DIMMER)],
}


def _make_discovery(tmp_path, button_data=None, tables=None) -> NikobusDiscovery:
    coord = MagicMock()
    coord.dict_module_data = {}
    coord.discovery_running = False
    coord.discovery_module = False
    coord.discovery_module_address = None
    coord.inventory_query_type = None
    discovery = NikobusDiscovery(
        coord,
        config_dir=str(tmp_path),
        create_task=MagicMock(),
        button_data=button_data if button_data is not None else {"nikobus_button": {}},
        on_button_save=None,
    )
    if tables is not None:
        discovery._decoders = [SimpleNamespace(logic_outputs_by_module=tables)]
    return discovery


def _merge(discovery: NikobusDiscovery, command_mapping: dict) -> dict:
    button_data = discovery._button_data
    merge_discovered_buttons(
        button_data, discovery.discovered_devices, KEY_MAPPING, convert_nikobus_address
    )
    merge_linked_modules(button_data, command_mapping)
    return button_data["nikobus_button"]


# ---------------------------------------------------------------------------
# Address arithmetic
# ---------------------------------------------------------------------------


def test_virtual_classes_cover_both_spellings_and_the_banks() -> None:
    assert virtual_button_class("384100") == VIRTUAL_CLASS_PC_LOGIC  # canonical
    assert virtual_button_class("684100") == VIRTUAL_CLASS_PC_LOGIC  # vendor form
    assert virtual_button_class("388000") == VIRTUAL_CLASS_PC_LOGIC
    assert virtual_button_class("3CF006") == VIRTUAL_CLASS_BANK
    assert virtual_button_class("3CF040") == VIRTUAL_CLASS_BANK  # the audio bank


def test_wall_buttons_and_junk_are_not_virtual() -> None:
    for address in ("2C4E00", "109DF1", "37FFFF", "390000", "3CEFFF", "3D0000", "E00320", "", "zz"):
        assert virtual_button_class(address) is None, address


def test_the_two_spellings_of_l01_are_one_plate_and_the_frames_match_the_bus() -> None:
    assert convert_nikobus_address("384100") == "002087"
    assert convert_nikobus_address("684100") == "002087"
    assert virtual_plate_address("384100") == "384100"
    assert virtual_plate_address("684100") == "384100"
    assert virtual_plate_address("384102") == "384102"  # L02 is its own plate
    # The frames Peter's PC-Logic put on the bus: L01 on, L01 off; and L02.
    assert virtual_record_bus_address("384100", 0) == "002087"
    assert virtual_record_bus_address("384100", 2) == "402087"  # key 1D of the same plate
    assert virtual_record_bus_address("384102", 0) == "102087"
    assert virtual_record_bus_address("384100", None) is None


# ---------------------------------------------------------------------------
# The PC-Logic's logic-output table
# ---------------------------------------------------------------------------


def test_the_table_is_parsed_from_the_scan_stream_in_order() -> None:
    # The excerpt skips from the sixth row to the last two, so the run
    # ends where the rows stop being consecutive.
    outputs = parse_logic_output_table("".join(TABLE_CHUNKS))
    assert outputs == TABLE_8DC8[:17]
    assert outputs[0] == "002087"  # L01
    assert outputs[1] == "102087"  # L02
    full = "FFFF" + "".join("0000" + a for a in TABLE_8DC8) + "FFFF"
    assert parse_logic_output_table(full) == TABLE_8DC8


def test_a_short_run_is_not_a_table() -> None:
    short = "0000" + "0000".join(TABLE_8DC8[: LOGIC_OUTPUT_TABLE_MIN - 1])
    assert parse_logic_output_table("FFFF" + short + "FFFF") == []
    assert parse_logic_output_table("") == []
    assert parse_logic_output_table("00" * 16) == []


def test_the_decoder_keeps_the_table_per_module() -> None:
    decoder = PcLogicDecoder(None)
    decoder.set_module_address("8DC8")
    for chunk in TABLE_CHUNKS:
        decoder.decode_chunk(chunk)
    decoder.reset_scan_buffers()
    assert decoder.logic_outputs_by_module["8DC8"][:3] == ["002087", "102087", "082087"]
    assert decoder._chunk_stream == []


# ---------------------------------------------------------------------------
# Peter's L01: the PC-Logic logic output
# ---------------------------------------------------------------------------


def test_l01_and_l02_become_plates_under_their_pc_logic_with_the_frames(tmp_path) -> None:
    discovery = _make_discovery(tmp_path, tables={"8DC8": list(TABLE_8DC8)})
    discovery._accumulated_command_mapping = PETER_RECORDS
    discovery._accumulated_unmatched = {"384100", "384102"}

    assert discovery._synthesize_virtual_buttons_from_unmatched() == 2
    assert discovery._accumulated_unmatched == set()

    entry = discovery.discovered_devices["384100"]
    l02 = discovery.discovered_devices["384102"]
    assert l02["virtual_output_index"] == 2
    assert l02["description"] == "PC-Logic 8DC8 L02"
    assert entry["category"] == "Button"
    assert entry["type"] == VIRTUAL_BUTTON_TYPE
    assert entry["model"] == VIRTUAL_BUTTON_MODEL
    assert entry["channels"] == 4
    assert entry["virtual_button"] is True
    assert entry["virtual_class"] == VIRTUAL_CLASS_PC_LOGIC
    assert entry["virtual_parent_address"] == "8DC8"
    assert entry["virtual_output_index"] == 1
    assert entry["description"] == "PC-Logic 8DC8 L01"

    buttons = _merge(discovery, PETER_RECORDS)
    stored = buttons["384100"]
    assert stored["virtual_parent_address"] == "8DC8"
    op_points = stored["operation_points"]
    assert op_points["1C"]["bus_address"] == "002087"
    assert op_points["1D"]["bus_address"] == "402087"
    # Key 0 is 1C; M01 is a two-key mode, so 1D (the off frame) mirrors it.
    on_links = {(m["module_address"], o["channel"]) for m in op_points["1C"]["linked_modules"] for o in m["outputs"]}
    off_links = {(m["module_address"], o["channel"]) for m in op_points["1D"]["linked_modules"] for o in m["outputs"]}
    assert on_links == {("81F6", 6)}
    assert off_links == {("81F6", 6)}
    # L02's all-off links sit on its own plate.
    l02_points = buttons["384102"]["operation_points"]
    assert l02_points["1C"]["bus_address"] == "102087"
    l02_links = {(m["module_address"], o["channel"]) for m in l02_points["1C"]["linked_modules"] for o in m["outputs"]}
    assert l02_links == {("81F6", 4), ("C5C1", 1)}


def test_the_vendor_spelling_names_the_same_plate(tmp_path) -> None:
    """A record spelled the vendor's way (``684100``) merges onto plate 384100."""
    discovery = _make_discovery(tmp_path, tables={"8DC8": list(TABLE_8DC8)})
    records = {("684100", 0, None): [dict(L01_ON, button_address="684100")]}
    discovery._accumulated_command_mapping = records
    discovery._accumulated_unmatched = {"684100"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 1
    assert set(discovery.discovered_devices) == {"384100"}
    buttons = _merge(discovery, records)
    assert buttons["384100"]["operation_points"]["1C"]["linked_modules"][0]["outputs"][0]["channel"] == 6


def test_without_a_table_the_cf_class_is_left_to_the_cf_classifier(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    discovery._accumulated_command_mapping = PETER_RECORDS
    discovery._accumulated_unmatched = {"384100", "384102"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert discovery._accumulated_unmatched == {"384100", "384102"}
    discovery._classify_cf_broadcasts_from_unmatched()
    assert set(discovery.discovered_cf_broadcasts) == {"384100", "384102"}


def test_a_table_kept_in_the_module_store_still_counts(tmp_path) -> None:
    """A per-module scan of the switch module alone must still know the PC-Logic's outputs."""
    discovery = _make_discovery(tmp_path)
    discovery._module_data = {
        "nikobus_module": {
            "8DC8": {"module_type": "pc_logic", "discovered_info": {"logic_outputs": list(TABLE_8DC8)}}
        }
    }
    discovery._accumulated_command_mapping = PETER_RECORDS
    discovery._accumulated_unmatched = {"384100", "384102"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 2
    assert discovery.discovered_devices["384100"]["virtual_parent_address"] == "8DC8"


def test_the_table_is_carried_into_the_module_store(tmp_path) -> None:
    discovery = _make_discovery(tmp_path, tables={"8DC8": list(TABLE_8DC8)})
    discovery.discovered_devices = {
        "8DC8": {
            "category": "Module", "module_type": "pc_logic", "address": "8DC8",
            "description": "PC-Logic", "model": "05-201", "channels_count": 6, "device_type": "0B",
        }
    }
    discovery._attach_logic_output_tables()
    module_data: dict = {"nikobus_module": {}}
    merge_discovered_modules(module_data, discovery.discovered_devices)
    assert module_data["nikobus_module"]["8DC8"]["discovered_info"]["logic_outputs"] == TABLE_8DC8


# ---------------------------------------------------------------------------
# The software's virtual input banks
# ---------------------------------------------------------------------------


def test_bank_key_gets_an_entry_and_its_links(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    records = {
        ("3CF006", 0, None): [{"module_address": "4707", "channel": 1, "mode": "M02 (On)", "record_source": "output_module_table"}],
        ("3CF006", 1, None): [{"module_address": "4707", "channel": 2, "mode": "M03 (Off)", "record_source": "output_module_table"}],
    }
    discovery._accumulated_command_mapping = records
    discovery._accumulated_unmatched = {"3CF006"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 1
    entry = discovery.discovered_devices["3CF006"]
    assert entry["virtual_class"] == VIRTUAL_CLASS_BANK
    assert entry["channels"] == 4
    assert "virtual_parent_address" not in entry
    assert entry["description"] == "Virtual button 3CF006"

    buttons = _merge(discovery, records)
    op_points = buttons["3CF006"]["operation_points"]
    assert op_points["1C"]["bus_address"] == convert_nikobus_address("3CF006")
    assert op_points["1C"]["linked_modules"][0]["outputs"][0]["channel"] == 1
    assert op_points["1A"]["linked_modules"][0]["outputs"][0]["channel"] == 2
    assert not op_points["1D"].get("linked_modules")  # single-key modes are not mirrored


def test_a_key_index_above_three_makes_an_eight_key_plate(tmp_path) -> None:
    discovery = _make_discovery(tmp_path)
    records = {("3CF010", 5, None): [{"module_address": "4707", "channel": 3, "mode": "M02 (On)", "record_source": "output_module_table"}]}
    discovery._accumulated_command_mapping = records
    discovery._accumulated_unmatched = {"3CF010"}
    discovery._synthesize_virtual_buttons_from_unmatched()
    assert discovery.discovered_devices["3CF010"]["channels"] == 8
    op_points = _merge(discovery, records)["3CF010"]["operation_points"]
    assert len(op_points) == 8
    # Raw key 5 is nibble A, the 1A key of an eight-key plate.
    assert op_points["1A"]["linked_modules"][0]["outputs"][0]["channel"] == 3


# ---------------------------------------------------------------------------
# What is left alone
# ---------------------------------------------------------------------------


def test_unknown_wall_buttons_stay_unmatched(tmp_path) -> None:
    discovery = _make_discovery(tmp_path, tables={"8DC8": list(TABLE_8DC8)})
    records = {("2C4E00", 0, None): [{"module_address": "4707", "channel": 1, "mode": "M01 (On / off)", "record_source": "output_module_table"}]}
    discovery._accumulated_command_mapping = records
    discovery._accumulated_unmatched = {"2C4E00"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert discovery.discovered_devices == {}
    _, links, outputs, unmatched = merge_linked_modules(discovery._button_data, records)
    assert (links, outputs, unmatched) == (0, 0, {"2C4E00"})


def test_registry_only_and_ir_records_do_not_make_an_entry(tmp_path) -> None:
    discovery = _make_discovery(tmp_path, tables={"8DC8": list(TABLE_8DC8)})
    discovery._accumulated_command_mapping = {
        ("384100", 0, None): [dict(L01_ON, record_source="pc_link_registry")],
        ("3CF006", 0, "10B"): [{"module_address": "4707", "channel": 1, "mode": "M02 (On)", "record_source": "output_module_table"}],
    }
    discovery._accumulated_unmatched = {"384100", "3CF006"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert discovery.discovered_devices == {}


def test_existing_entries_are_not_shadowed(tmp_path) -> None:
    button_data = {"nikobus_button": {"3CF006": {"description": "Mine", "channels": 4, "operation_points": {}}}}
    discovery = _make_discovery(tmp_path, button_data)
    discovery._accumulated_command_mapping = {("3CF006", 0, None): [{"module_address": "4707", "channel": 1, "mode": "M02 (On)", "record_source": "output_module_table"}]}
    discovery._accumulated_unmatched = {"3CF006"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert button_data["nikobus_button"]["3CF006"]["description"] == "Mine"


def test_synthesis_is_idempotent(tmp_path) -> None:
    discovery = _make_discovery(tmp_path, tables={"8DC8": list(TABLE_8DC8)})
    discovery._accumulated_command_mapping = PETER_RECORDS
    discovery._accumulated_unmatched = {"384100", "384102"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 2
    snapshot = {k: dict(v) for k, v in discovery.discovered_devices.items()}
    discovery._accumulated_unmatched = {"384100", "384102"}
    assert discovery._synthesize_virtual_buttons_from_unmatched() == 0
    assert {k: dict(v) for k, v in discovery.discovered_devices.items()} == snapshot
    assert discovery._accumulated_unmatched == set()


def test_store_constants_are_exported_for_hosts() -> None:
    assert fileio.VIRTUAL_BUTTON_TYPE == "Virtual button"
    assert fileio.VIRTUAL_BUTTON_MODEL == "Virtual"
