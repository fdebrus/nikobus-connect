"""Count-bounded register scan plans and the versioned registry header."""

from __future__ import annotations

from nikobus_connect.discovery.discovery import (
    _count_driven_passes,
    _registry_header_count,
)
from nikobus_connect.protocol import ModuleStatus


def _status(a: int, b: int = 0) -> ModuleStatus:
    return ModuleStatus("86F5", False, 0x01, a, b, b"")


def test_switch_plan_covers_all_records_plus_one_block():
    # 20 records × 6 bytes = 120 bytes = 7.5 blocks → 8, plus one slack → 9 blocks
    ((sub, regs),) = _count_driven_passes("switch_module", _status(20))
    assert sub == "00"
    assert regs == tuple(range(0x10, 0x10 + 9))


def test_switch_plan_zero_records_still_reads_one_block():
    ((_sub, regs),) = _count_driven_passes("roller_module", _status(0))
    assert regs == (0x10,)


def test_switch_plan_is_capped_at_table_end():
    ((_sub, regs),) = _count_driven_passes("switch_module", _status(255))
    assert regs[-1] == 0x6F


def test_dimmer_plan_two_banks_and_config_block():
    passes = _count_driven_passes("dimmer_module", _status(5, 3))
    assert passes[0] == ("00", tuple(range(0x20, 0x25)))
    assert passes[1] == ("00", tuple(range(0xF8, 0x100)))
    assert passes[2] == ("01", tuple(range(0x20, 0x23)))


def test_dimmer_plan_without_second_bank():
    passes = _count_driven_passes("dimmer_module", _status(1, 0))
    assert len(passes) == 2


def test_controllers_keep_their_fixed_plan():
    assert _count_driven_passes("pc_link", _status(9)) is None
    assert _count_driven_passes("pc_logic", _status(9)) is None


def test_registry_header_accepts_version_range():
    tail = b"\x55\xaa\xaa" + (23).to_bytes(4, "little")
    assert _registry_header_count(b"\x00\x01\x5e" + tail) == 23   # current firmware
    assert _registry_header_count(b"\x00\x01\x49" + tail) == 23   # oldest supported
    assert _registry_header_count(b"\x00\x01\x5f" + tail) is None  # too new
    assert _registry_header_count(b"\x00\x01\x48" + tail) is None  # too old
    assert _registry_header_count(tail) is None                    # no version byte


def test_registry_header_skips_false_signature_hits():
    tail = b"\x55\xaa\xaa" + (7).to_bytes(4, "little")
    data = b"\x10\x55\xaa\xaa\x00\x00\x00\x00\x5e" + tail
    assert _registry_header_count(data) == 7
