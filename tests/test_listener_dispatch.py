"""Listener tests — frame extraction, dispatch routing, queue gating
and the post-reconnect ``reset()``.

These cover the previously-untested listener core: partial frames are
buffered across reads, control bytes are stripped, ACKs only reach the
response queue while a caller is waiting (``_awaiting_response``), and
``reset()`` clears every piece of per-connection state.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nikobus_connect.listener import NikobusEventListener


def _listener(**kwargs) -> tuple[NikobusEventListener, list[str]]:
    events: list[str] = []
    listener = NikobusEventListener(
        connection=MagicMock(),
        event_callback=events.append,
        **kwargs,
    )
    return listener, events


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------


def test_partial_frame_buffered_across_reads() -> None:
    listener, _ = _listener()
    assert listener._extract_frames("$0512") == []  # no CR yet — buffered
    assert listener._extract_frames("AB\r") == ["$0512AB"]
    assert listener._frame_buffer == ""


def test_control_bytes_stripped_and_lf_normalised() -> None:
    listener, _ = _listener()
    frames = listener._extract_frames("\x02$0515FF\x03\n")
    assert frames == ["$0515FF"]


def test_concatenated_frames_split_on_prefix() -> None:
    # Two frames glued in one CR-terminated chunk are split on $/#.
    listener, _ = _listener()
    frames = listener._extract_frames("$0515FF#N123456\r")
    assert frames == ["$0515FF", "#N123456"]


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


async def test_button_press_routed_to_event_callback() -> None:
    listener, events = _listener()
    await listener._dispatch_message("#N004E2C")
    assert events == ["#N004E2C"]
    assert listener.response_queue.empty()


async def test_ack_enqueued_only_while_awaiting() -> None:
    listener, events = _listener()
    # Not awaiting: ACK reaches the event callback but NOT the queue
    # (fire-and-forget bursts must not flood the 200-slot queue).
    await listener._dispatch_message("$0515")
    assert listener.response_queue.empty()
    # Awaiting: same frame is enqueued for the command pipeline.
    listener._awaiting_response = True
    await listener._dispatch_message("$0515")
    assert listener.response_queue.qsize() == 1
    assert events == ["$0515", "$0515"]


async def test_discovery_frames_routed_to_event_callback() -> None:
    """CRC-valid $18/$2E/$1E frames reach the event callback.

    Real frames, not placeholders: ``$187E8F0040073FFFB65305`` and
    ``$2E498C19000000010000002A3D000005000000F7F27F`` were captured off
    real Nikobus installs; the $1E frame is synthesised with a matching
    CRC (no real capture was on hand, but the construction is identical).
    """
    listener, events = _listener()
    frames = (
        "$187E8F0040073FFFB65305",
        "$2E498C19000000010000002A3D000005000000F7F27F",
        "$1E4242424242424242424257C4C1",
    )
    for frame in frames:
        await listener._dispatch_message(frame)
    assert events == list(frames)


async def test_corrupted_discovery_frames_are_dropped() -> None:
    """A bit-flipped $18/$2E/$1E frame must NOT reach the event callback.

    Regression for the "Unknown device detected" false-positive bug: an
    uncaught bit error on the wire used to sail straight through to the
    device classifier and get logged as a spurious new device (and could
    seed a phantom module/button in storage). One flipped hex nibble in
    each frame's payload should now fail CRC and be silently dropped.
    """
    listener, events = _listener()
    corrupted = (
        "$187E8F0040073FFEB65305",  # FF -> FE in the payload
        "$2E498C19000000010000002A3D000005000000F7F27E",  # trailing nibble flipped
        "$1E4242424242424242424157C4C1",  # payload nibble flipped
    )
    for frame in corrupted:
        await listener._dispatch_message(frame)
    assert events == []


def test_enqueue_drops_oldest_when_full() -> None:
    listener, _ = _listener()
    for i in range(200):
        listener.response_queue.put_nowait(f"msg{i}")
    listener._enqueue_response("newest")
    assert listener.response_queue.qsize() == 200
    assert listener.response_queue.get_nowait() == "msg1"  # msg0 dropped


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------


def test_reset_clears_all_per_connection_state() -> None:
    listener, _ = _listener()
    listener._frame_buffer = "$05 partial"
    listener._last_query_group["C9A5"] = 2
    listener.response_queue.put_nowait("stale")

    listener.reset()

    assert listener._frame_buffer == ""
    assert listener._last_query_group == {}
    assert listener.response_queue.empty()
