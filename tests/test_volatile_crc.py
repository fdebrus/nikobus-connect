"""A dimmer's power-loss memory moves its CRC; a prediction tells it apart.

A 05-008-02 writes state into 0x7E3..0x7F9 when power drops, inside the
range its function-0x13 CRC covers. On a real install (Nikobus-HA,
2026-10) two power cuts moved the CRC of two dimmers whose link tables
were byte-for-byte unchanged. The CRC is affine, so the CRC a change of
those bytes alone produces can be computed from the old CRC and the
change. The figures below are the module-reported CRCs and the
power-loss bytes of that install's three backups.
"""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock

import pytest

from nikobus_connect.api import (
    MODULE_VOLATILE_RANGES,
    NikobusAPI,
    image_crc,
    predict_module_crc,
)
from nikobus_connect.protocol import family_matches

START = 0x7E3
#: (module-reported CRC, bytes 0x7E3..0x7F9) — 25-09, after cut 1, after cut 2.
C95D = [
    (0xC28A, "FFFFFFFFFFFFFFFFFFFFFF000000000000000000000000"),
    (0x46D6, "62E5FFFFFFFFFFFFFFFFFF000000000000000000000000"),
    (0x46CA, "62E5FFFFFFFFFFFFFFFFFF006200000000000000000000"),
]
C5C1 = [
    (0xEF12, "FFFFFFFFFFFFFFFFFFFFFF000000000000000000000000"),
    (0xB7B6, "E1FFFFFFFFFFFFFFFFFFFF000000000000000000000000"),
    (0xB7B6, "E1FFFFFFFFFFFFFFFFFFFF000000000000000000000000"),
]


def _win(hex_bytes: str) -> dict[int, bytes]:
    return {START: bytes.fromhex(hex_bytes)}


@pytest.mark.parametrize("series", [C95D, C5C1], ids=["C95D", "C5C1"])
@pytest.mark.parametrize(("a", "b"), [(0, 1), (1, 2), (0, 2)])
def test_power_cuts_on_a_real_install_are_predicted(series, a, b) -> None:
    old_crc, old = series[a]
    new_crc, new = series[b]
    assert predict_module_crc("dimmer_module", old_crc, _win(old), _win(new)) == new_crc


def test_the_range_ends_where_the_crc_coverage_ends() -> None:
    ((start, end),) = MODULE_VOLATILE_RANGES["dimmer_module"]
    assert (start, end) == (0x7E3, 0x7FA)
    assert len(bytes.fromhex(C95D[0][1])) == end - start


def _image(seed: int) -> bytearray:
    rng = random.Random(seed)
    return bytearray(rng.randrange(256) for _ in range(0xFD0))


def test_prediction_matches_a_full_crc_and_catches_other_changes() -> None:
    image = _image(1)
    old_crc = image_crc(bytes(image), "dimmer_module")
    old = {START: bytes(image[START:0x7FA])}
    image[0x7EF] = 0x62
    image[0x7E3:0x7E5] = b"\x62\xe5"
    new = {START: bytes(image[START:0x7FA])}
    assert predict_module_crc("dimmer_module", old_crc, old, new) == image_crc(bytes(image), "dimmer_module")
    # A link byte changed as well: the prediction no longer matches.
    image[0x120] ^= 0x01
    assert predict_module_crc("dimmer_module", old_crc, old, new) != image_crc(bytes(image), "dimmer_module")


def test_bytes_outside_the_crc_coverage_do_not_count() -> None:
    image = _image(2)
    old_crc = image_crc(bytes(image), "dimmer_module")
    old = {0x7FA: bytes(image[0x7FA:0x800])}
    image[0x7FA:0x800] = b"\x00" * 6
    new = {0x7FA: bytes(image[0x7FA:0x800])}
    assert predict_module_crc("dimmer_module", old_crc, old, new) == old_crc == image_crc(bytes(image), "dimmer_module")


def test_unknown_layout_or_mismatched_maps_predict_nothing() -> None:
    assert predict_module_crc("audio_module", 1, {}, {}) is None
    assert predict_module_crc("dimmer_module", 1, {START: b"\x00"}, {START + 1: b"\x00"}) is None
    assert predict_module_crc("dimmer_module", 1, {START: b"\x00"}, {START: b"\x00\x00"}) is None
    assert predict_module_crc("dimmer_module", 0x1234, _win(C95D[0][1]), _win(C95D[0][1])) == 0x1234


async def test_read_module_volatile_reads_only_the_four_blocks() -> None:
    image = _image(3)
    handler = MagicMock()

    async def _query(func, address, args):
        block = int.from_bytes(args, "little")  # make_block_index_args
        return b"\x5d\xc9" + bytes(image[block * 8 : block * 8 + 8])

    handler.query = AsyncMock(side_effect=_query)
    api = NikobusAPI(handler, {})
    got = await api.read_module_volatile("C95D", "dimmer_module")
    assert got == {START: bytes(image[START:0x7FA])}
    assert handler.query.await_count == 4  # blocks 0xFC..0xFF
    assert await api.read_module_volatile("4707", "switch_module") == {}


def test_an_unknown_family_byte_is_not_a_mismatch() -> None:
    assert family_matches("dimmer_module", 0x30) is True
    assert family_matches("dimmer_module", 0x10) is False  # a switch answering
    assert family_matches("dimmer_module", 0x77) is None  # no known family
    assert family_matches("audio_module", 0x30) is None
