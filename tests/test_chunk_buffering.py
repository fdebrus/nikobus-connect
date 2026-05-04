"""Regression tests for cross-frame chunk buffering.

The old library decoded ``dimmer_module`` records through the same buffered
path as switch/roller — module register responses were concatenated into a
running payload buffer and chunks were sliced off at the expected length
(16 hex chars for dimmer, 12 for switch/roller).

The current library accidentally split dimmer into a per-frame path that
drops anything shorter than a full chunk. These tests pin the buffered
behavior so the regression can't come back.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.discovery.chunk_decoder import _CHUNK_LENGTHS
from nikobus_connect.discovery.dimmer_decoder import DimmerDecoder
from nikobus_connect.discovery.switch_decoder import SwitchDecoder


def _coordinator() -> MagicMock:
    coord = MagicMock()
    # Return a positive channel count so the per-decoder inventory
    # guard (added in 0.5.4) treats every decoded canonical as "known"
    # — these tests cover chunk *buffering* mechanics and shouldn't
    # exercise the inventory-existence path.
    coord.get_button_channels = MagicMock(return_value=4)
    coord.get_module_channel_count = MagicMock(return_value=None)
    return coord


def test_chunk_lengths_includes_dimmer():
    """Dimmer records are 16 hex chars; the table must know that."""
    assert _CHUNK_LENGTHS["dimmer_module"] == 16
    assert _CHUNK_LENGTHS["switch_module"] == 12
    assert _CHUNK_LENGTHS["roller_module"] == 12


def test_dimmer_record_split_across_two_frames_is_recovered():
    """A 16-char dimmer record split 8/8 across two frames must decode.

    Each frame arrives as ``payload_and_crc`` (data + 6-char CRC). The
    first frame alone cannot produce a chunk; the buffer must hold the
    partial bytes until the second frame completes the 16-char record.
    """
    decoder = DimmerDecoder(_coordinator())
    decoder.set_module_address("0E6C")

    # Real record: FFB4001100D00E61 (reversed from 610ED000110 0B4FF).
    # Split the memory-order form 8/8 across two frames.
    record_memory_order = "610ED0001100B4FF"  # reverse of FFB4001100D00E61
    frame1_data = record_memory_order[:8]  # "610ED000"
    frame2_data = record_memory_order[8:]  # "1100B4FF"

    # Frame 1: data + fake 6-char CRC. Nothing should come out yet.
    analysis1 = decoder.analyze_frame_payload("", frame1_data + "AAAAAA")
    assert analysis1["chunks"] == []
    assert analysis1["remainder"] == frame1_data

    # Frame 2: buffer carries frame 1's remainder; together they make a
    # full 16-char chunk and one decoded command.
    analysis2 = decoder.analyze_frame_payload(
        analysis1["remainder"], frame2_data + "BBBBBB"
    )
    assert len(analysis2["chunks"]) == 1
    assert analysis2["chunks"][0] == record_memory_order
    assert analysis2["remainder"] == ""

    commands = decoder.decode_chunk(analysis2["chunks"][0], "0E6C")
    assert len(commands) == 1
    metadata = commands[0].metadata
    assert metadata["key_raw"] == 1
    assert metadata["channel"] == 2
    assert metadata["mode_raw"] == 0


def test_switch_record_split_across_frames_is_recovered():
    """Same guarantee for switch modules: 12-char chunk assembled from
    multiple sub-12-char frames."""
    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("C9A5")

    record_memory_order = "60BC60F013FF"  # decodes to key=1 ch=4 M01 btn=182F18
    # Break across three 4-char frames.
    frames = [record_memory_order[i : i + 4] for i in (0, 4, 8)]

    buffer = ""
    emitted_chunks: list[str] = []
    for data in frames:
        analysis = decoder.analyze_frame_payload(buffer, data + "CCCCCC")
        emitted_chunks.extend(analysis["chunks"])
        buffer = analysis["remainder"]

    assert buffer == ""
    assert emitted_chunks == [record_memory_order]

    commands = decoder.decode_chunk(emitted_chunks[0], "C9A5")
    assert len(commands) == 1
    meta = commands[0].metadata
    assert meta["key_raw"] == 1
    assert meta["channel"] == 4
    assert meta["mode_raw"] == 0


def test_dimmer_two_records_in_one_frame():
    """Two back-to-back dimmer records arriving in a single frame must
    both decode."""
    decoder = DimmerDecoder(_coordinator())
    decoder.set_module_address("0E6C")

    rec_a = "610ED0001100B4FF"
    rec_b = "347234001000B4FF"  # second record (values picked to be valid)
    combined = rec_a + rec_b

    analysis = decoder.analyze_frame_payload("", combined + "DDDDDD")
    assert analysis["chunks"] == [rec_a, rec_b]
    assert analysis["remainder"] == ""
