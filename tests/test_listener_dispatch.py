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
    listener, events = _listener()
    for frame in ("$18AABB", "$2E01", "$1E02"):
        await listener._dispatch_message(frame)
    assert events == ["$18AABB", "$2E01", "$1E02"]


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
