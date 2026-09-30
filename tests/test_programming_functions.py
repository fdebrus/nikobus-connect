"""The programming function codes, from the vendor software's own write
sequence (nikobus.exe 4.3.1, decompiled). The library sends none of
them; the constants pin what a capture would show."""

from __future__ import annotations

from nikobus_connect.protocol import (
    FUNC_CLEAR_EEPROM,
    FUNC_LINK_MODE_OFF,
    FUNC_LINK_MODE_ON,
    FUNC_MEMORY_INVALID,
    FUNC_MEMORY_VALID,
    FUNC_MODULE_CRC,
    FUNC_READ_BLOCK8,
    FUNC_READ_BLOCK16,
    FUNC_WRITE_BLOCK8,
    FUNC_WRITE_BLOCK16,
    make_block_index_args,
    make_pc_link_command,
)
from nikobus_connect.rgb_memory import RGB_LINK_TABLE_BLOCK, build_rgb_link_record


def test_function_codes() -> None:
    assert (FUNC_LINK_MODE_ON, FUNC_LINK_MODE_OFF) == (0x18, 0x19)
    assert (FUNC_MEMORY_VALID, FUNC_MEMORY_INVALID) == (0x1B, 0x1C)
    assert FUNC_CLEAR_EEPROM == 0x23
    assert (FUNC_WRITE_BLOCK16, FUNC_WRITE_BLOCK8) == (0x14, 0x21)
    # the read and write functions pair up by block size
    assert (FUNC_READ_BLOCK16, FUNC_READ_BLOCK8, FUNC_MODULE_CRC) == (0x10, 0x22, 0x13)


def test_the_write_frame_the_vendor_would_send_for_the_first_link_block() -> None:
    """Block 0x19 of a 340-00112 at 801D holding the validating install's
    M19 link: function 0x14, address, block index, sixteen data bytes."""
    record = build_rgb_link_record(address=0x124A36, mode=19, channel=0).to_bytes()
    block = (record + b"\xff" * 16)[:16]
    first_block = RGB_LINK_TABLE_BLOCK.start // 16
    assert first_block == 0x19
    command = make_pc_link_command(
        FUNC_WRITE_BLOCK16, "801D", make_block_index_args(first_block) + block
    )
    assert command.startswith("$" )
    assert "141D801900" + block.hex().upper() in command
