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
# Alternate-alignment dual-pass for switch / roller (0.5.5 fix)
# ---------------------------------------------------------------------------
#
# Real-hardware switch and roller modules return 32 hex chars of data per
# register response. The user-attachments capture from 2026-05-04 shows
# records packing across registers at stream offset 8 (= 4-byte response
# header), while the previous user's 2026-04-30 capture shows them packing
# at stream offset 0 — same protocol, different firmware revisions.
#
# The 0.2.1..0.5.4 chunker only ran the offset-0 alignment, so the
# 2026-05-04 install merged zero links from any switch or roller scan.
# 0.5.5 keeps the primary buffered alignment intact (offset 0) and adds a
# *second* buffered alignment shifted by 8 chars at the start of each
# per-module scan. The decoder's ``unknown_button`` / ``unknown_mode``
# gates filter the alignment that produces phantoms; the merge layer
# dedupes when both alignments lock onto the same record.


def test_switch_full_frame_emits_chunks_at_both_alignments():
    """A 32-char switch frame yields the 2 primary-alignment chunks
    plus alt-alignment chunks shifted by 8 chars from the stream start.
    Real records get caught regardless of which alignment the firmware
    uses; phantoms are filtered downstream by the decoder."""

    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("B909")
    decoder.reset_scan_buffers()

    chunk_a = "F6353CF010FF"  # decodes to button 3D8D4F, key=1, ch=1, M01
    chunk_b = "F7637CF011FF"  # decodes to button 3DD8DF, key=1, ch=2, M01
    padding = "FFFFFFFF"
    crc = "D6A005"
    frame = chunk_a + chunk_b + padding + crc

    analysis = decoder.analyze_frame_payload("", frame)

    # Primary alignment yields the two records straight off offset 0.
    assert chunk_a in analysis["chunks"]
    assert chunk_b in analysis["chunks"]
    # Primary buffer carries the 8-char padding forward (the historic
    # 0.2.1 buffered behaviour).
    assert analysis["remainder"] == padding
    # Alt alignment dropped the first 8 chars (stream-start skip) and
    # extracted whatever fits in the remaining 24-char slice.
    assert len(analysis["chunks"]) >= 2  # at least the primary pair


def test_switch_alt_alignment_recovers_offset_8_records():
    """When records pack at stream offset 8 (firmware adds a 4-byte
    response header), the alt alignment is what surfaces them. This
    test mirrors the layout observed in 29FA frame 19 of the
    2026-05-04 capture: 8-char prefix + 2 records."""

    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("29FA")
    decoder.reset_scan_buffers()

    prefix = "810253FF"  # 4-byte non-record prefix
    rec_a = "EB12A4F004FF"  # decodes to button 3AC4A9, key=0, ch=5, M01
    rec_b = "E7934CF006FF"  # decodes to button 39E4D3, key=0, ch=7, M01
    frame = prefix + rec_a + rec_b + "ABCDEF"  # 32 + 6 chars

    analysis = decoder.analyze_frame_payload("", frame)

    # Primary alignment (offset 0) sees junk + first record at offsets
    # 0/12/24, none of which line up with rec_a or rec_b cleanly.
    # Alt alignment (offset 8 stream-start skip) is what actually
    # extracts rec_a and rec_b.
    assert rec_a in analysis["chunks"], (
        "alt-alignment must surface offset-8 records like rec_a"
    )
    assert rec_b in analysis["chunks"], (
        "alt-alignment must surface the second offset-8 record"
    )


def test_alt_alignment_resets_per_scan():
    """``reset_scan_buffers`` re-arms the first-frame skip counter so a
    new module scan starts clean — otherwise the alt alignment would
    drift across module boundaries."""

    decoder = SwitchDecoder(_coordinator())
    decoder.set_module_address("M1")
    decoder.reset_scan_buffers()

    # Drive one frame through to consume the first-frame skip.
    decoder.analyze_frame_payload("", "F6353CF010FFF7637CF011FFFFFFFFFF" + "AAAAAA")
    # Drive another to exercise post-skip buffered behaviour.
    decoder.analyze_frame_payload("", "00112233445566778899AABBCCDDEEFF" + "BBBBBB")

    assert decoder._alt_first_frame_skip_pending == 0, (
        "skip must be fully consumed by the first frame's data region"
    )
    decoder.reset_scan_buffers()
    assert decoder._alt_first_frame_skip_pending == 8, (
        "reset_scan_buffers must re-arm the skip counter for the next scan"
    )
    assert decoder._alt_payload_buffer == "", (
        "reset_scan_buffers must clear the alt buffer for the next scan"
    )


def test_dimmer_no_alt_alignment_no_extra_chunks():
    """Dimmer registers are 16 chars = exactly one chunk; no header
    has been observed on any captured firmware. Alt alignment must
    NOT run for dimmer (would only produce filtered phantoms and
    bloat logs)."""

    decoder = DimmerDecoder(_coordinator())
    decoder.set_module_address("0E6C")
    decoder.reset_scan_buffers()

    chunk = "610ED0001100B4FF"
    crc = "ABC123"

    analysis = decoder.analyze_frame_payload("", chunk + crc)
    assert analysis["chunks"] == [chunk]
    assert analysis["remainder"] == ""
