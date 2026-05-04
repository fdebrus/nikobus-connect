"""Regression tests for chunk extraction across register frames.

Two distinct frame shapes need to keep working:

1. Synthetic-fragmentation path — frames shorter than a chunk feed into a
   running buffer, and the chunker must reassemble records that span
   frames. This was the 0.2.1 fix that prevented dimmer records being
   silently dropped when split across two transport reads.

2. Real-hardware register response — each Nikobus register reply carries
   a fixed-size data region (16 hex for dimmer, 32 hex for switch/roller
   and PC Link/PC Logic) followed by per-register padding when the data
   region is wider than a chunk (only switch/roller: 32 = 2*12 + 8). The
   8-char padding is NOT a partial-record continuation; buffering it
   forward (the 0.2.1..0.5.4 behaviour) shifts every subsequent chunk's
   alignment by 8 chars and corrupts decoder reads. The 0.5.5 fix
   discards the padding when the data region alone holds a full chunk
   and no carry is queued from a prior fragmented frame.

Both behaviours are pinned below.
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


# ---------------------------------------------------------------------------
# Per-register padding (0.5.5 fix)
# ---------------------------------------------------------------------------
#
# Real-hardware switch and roller modules return 32 hex chars of data per
# register response. With chunk_len=12 that's 2 whole chunks plus an 8-char
# register-end padding tail. Pre-0.5.5 the chunker buffered that 8-char tail
# into the next frame, shifting every subsequent chunk's alignment by 8
# chars and producing zero merged links from any switch or roller scan.
#
# These tests pin the post-fix behaviour: when a frame's data region is
# already at least one full chunk and no carry is buffered from a prior
# fragmented frame, the chunker treats the frame as self-contained and
# discards the trailing padding.


def test_switch_full_frame_discards_register_end_padding():
    """A 32-char switch frame yields exactly 2 chunks; the 8-char tail
    is dropped, NOT carried into the next frame."""

    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("B909")

    # Two real-hardware records concatenated, plus 8 chars of
    # register-end padding (FFFFFFFF). Sample frame from a captured
    # B909 scan: 2026-05-04 user-attachments log.
    chunk_a = "F6353CF010FF"  # decodes to button 3D8D4F, key=1, ch=1, M01
    chunk_b = "F7637CF011FF"  # decodes to button 3DD8DF, key=1, ch=2, M01
    padding = "FFFFFFFF"
    crc = "D6A005"
    frame = chunk_a + chunk_b + padding + crc

    analysis = decoder.analyze_frame_payload("", frame)

    assert analysis["chunks"] == [chunk_a, chunk_b]
    assert analysis["remainder"] == "", (
        "register-end padding must be discarded, not buffered forward"
    )


def test_switch_back_to_back_full_frames_stay_aligned():
    """Across two consecutive full-size frames the chunk alignment must
    not drift — pre-0.5.5 it drifted 8 chars per frame."""

    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("B909")

    frame1_chunks = ("F6353CF010FF", "F7637CF011FF")  # → 3D8D4F, 3DD8DF
    frame2_chunks = ("F6D9B4F012FF", "EE56E4F00AFF")  # → 3DB66D, 3B95B9
    pad1 = "AAAAAAAA"
    pad2 = "BBBBBBBB"

    a1 = decoder.analyze_frame_payload(
        "", "".join(frame1_chunks) + pad1 + "C00001"
    )
    assert list(a1["chunks"]) == list(frame1_chunks)
    assert a1["remainder"] == ""

    a2 = decoder.analyze_frame_payload(
        a1["remainder"], "".join(frame2_chunks) + pad2 + "C00002"
    )
    assert list(a2["chunks"]) == list(frame2_chunks), (
        "frame 2 must yield its own two chunks at offset 0,12 — not shifted"
    )
    assert a2["remainder"] == ""


def test_dimmer_full_frame_has_no_padding_to_discard():
    """Dimmer registers are 16 chars data = exactly one chunk. The
    per-register branch must produce the single chunk with no
    remainder, identical to the pre-0.5.5 buffered branch's output."""

    decoder = DimmerDecoder(_coordinator())
    decoder.set_module_address("0E6C")

    chunk = "610ED0001100B4FF"
    crc = "ABC123"

    analysis = decoder.analyze_frame_payload("", chunk + crc)
    assert analysis["chunks"] == [chunk]
    assert analysis["remainder"] == ""
