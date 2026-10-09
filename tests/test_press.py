"""A key press inferred from its frames: duration, hold milestones,
burst patience, release — driven by an injected clock, no sleeping."""

from __future__ import annotations

from collections import deque

import pytest

from nikobus_connect.press import (
    BURST_RECENT_GAPS_WINDOW,
    FRAME_CADENCE_S,
    MAX_EXTENDED_RELEASE_MS,
    RELEASE_THRESHOLD_MS,
    SHORT_PRESS_S,
    PressState,
    PressTracker,
    impacted_groups,
    is_short_press,
    press_bucket,
    primary_link,
    timers_crossed,
    update_release_threshold,
)


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def _tracker(clock: Clock | None = None) -> tuple[PressTracker, Clock]:
    clock = clock or Clock()
    return PressTracker(clock=clock, press_id=lambda addr, now: f"{addr}@{now:.3f}"), clock


def _state(frame_count: int = 1, **kw: object) -> PressState:
    state = PressState(address="C5E952", press_id="p", started_at=0.0, last_frame_at=0.0)
    state.frame_count = frame_count
    for key, value in kw.items():
        setattr(state, key, value)
    return state


# --- duration is wire time -------------------------------------------------


def test_the_first_frame_starts_a_press_with_one_frame_counted() -> None:
    tracker, _ = _tracker()
    state, started, timers = tracker.frame("c5e952")
    assert started and timers == []
    assert state.address == "C5E952"
    assert state.frame_count == 1
    assert state.release_threshold_ms == float(RELEASE_THRESHOLD_MS)
    assert "C5E952" in tracker and tracker.active("C5E952") is state


def test_further_frames_count_whatever_the_wall_clock_says() -> None:
    """Five frames delivered in one burst are still 200 ms of wire time."""
    tracker, _ = _tracker()
    for _ in range(5):
        state, started, _timers = tracker.frame("C5E952")
    assert not started
    assert state.frame_count == 5
    assert state.duration_s == pytest.approx(5 * FRAME_CADENCE_S)
    assert state.is_short and state.bucket == 0


@pytest.mark.parametrize(
    ("duration", "bucket", "short"),
    [(0.2, 0, True), (0.99, 0, True), (1.0, 1, False), (2.5, 2, False), (3.88, 3, False), (12.0, 3, False)],
)
def test_a_release_is_filed_by_its_duration(duration: float, bucket: int, short: bool) -> None:
    assert press_bucket(duration) == bucket
    assert is_short_press(duration) is short
    assert SHORT_PRESS_S == 1.0


# --- hold milestones -------------------------------------------------------


def test_a_burst_of_eighty_frames_crosses_the_three_milestones_once_each() -> None:
    """Frames flushed at once still cross 1, 2 and 3 s of wire time, in
    order, and each milestone is reported exactly once."""
    tracker, _ = _tracker()
    crossed: list[tuple[int, int]] = []
    for _ in range(80):
        state, _started, timers = tracker.frame("C5E952")
        crossed += [(state.frame_count, t) for t in timers]
    assert crossed == [(25, 1), (50, 2), (75, 3)]


def test_a_tap_crosses_no_milestone() -> None:
    tracker, _ = _tracker()
    assert all(tracker.frame("C5E952")[2] == [] for _ in range(5))


def test_milestones_are_idempotent_on_a_state() -> None:
    state = _state(frame_count=40)
    assert timers_crossed(state) == [1]
    assert timers_crossed(state) == []
    state.frame_count = 80
    assert timers_crossed(state) == [2, 3]


# --- burst patience --------------------------------------------------------


def _gaps(*values: float) -> deque[float]:
    gaps: deque[float] = deque(maxlen=BURST_RECENT_GAPS_WINDOW)
    gaps.extend(values)
    return gaps


def test_normal_cadence_keeps_the_baseline() -> None:
    state = _state(frame_count=10, recent_gaps=_gaps(0.040, 0.040, 0.040, 0.040))
    update_release_threshold(state)
    assert state.release_threshold_ms == float(RELEASE_THRESHOLD_MS)


def test_a_burst_window_extends_the_patience_to_the_implied_stall() -> None:
    state = _state(frame_count=50, recent_gaps=_gaps(0.0005, 0.0005, 0.0005, 0.0005))
    update_release_threshold(state)
    assert state.release_threshold_ms == 2000.0  # 50 frames × 40 ms


def test_the_extension_is_capped() -> None:
    state = _state(frame_count=300, recent_gaps=_gaps(0.0005, 0.0005, 0.0005, 0.0005))
    update_release_threshold(state)
    assert state.release_threshold_ms == float(MAX_EXTENDED_RELEASE_MS)


def test_patience_never_shrinks_while_the_burst_lasts() -> None:
    state = _state(frame_count=100, recent_gaps=_gaps(0.0005, 0.0005, 0.0005, 0.0005))
    update_release_threshold(state)
    assert state.release_threshold_ms == 4000.0
    state.frame_count = 50
    update_release_threshold(state)
    assert state.release_threshold_ms >= 4000.0


def test_a_clean_window_relaxes_the_patience() -> None:
    state = _state(frame_count=100, release_threshold_ms=4000.0, recent_gaps=_gaps(0.040, 0.040, 0.040, 0.040))
    update_release_threshold(state)
    assert state.release_threshold_ms == float(RELEASE_THRESHOLD_MS)


def test_a_mixed_window_leaves_the_patience_alone() -> None:
    state = _state(frame_count=100, release_threshold_ms=4000.0, recent_gaps=_gaps(0.040, 0.0005, 0.040, 0.040))
    update_release_threshold(state)
    assert state.release_threshold_ms == 4000.0


def test_the_tracker_measures_the_gaps_itself() -> None:
    tracker, clock = _tracker()
    tracker.frame("C5E952")
    for _ in range(60):
        clock.tick(0.0005)
        state, _s, _t = tracker.frame("C5E952")
    assert state.release_threshold_ms == pytest.approx(61 * FRAME_CADENCE_S * 1000)
    for _ in range(BURST_RECENT_GAPS_WINDOW):
        clock.tick(0.040)
        state, _s, _t = tracker.frame("C5E952")
    assert state.release_threshold_ms == float(RELEASE_THRESHOLD_MS)


# --- release ---------------------------------------------------------------


def test_release_is_due_after_the_patience_of_silence() -> None:
    tracker, clock = _tracker()
    state, _s, _t = tracker.frame("C5E952")
    clock.tick(0.2)
    assert tracker.release_due("C5E952") is None
    clock.tick(0.11)
    assert tracker.release_due("C5E952") is state
    assert tracker.end("C5E952") is state
    assert "C5E952" not in tracker
    assert tracker.release_due("C5E952") is None


def test_a_watcher_of_an_ended_press_never_claims_its_successor() -> None:
    tracker, clock = _tracker()
    first, _s, _t = tracker.frame("C5E952")
    tracker.end("C5E952", first.press_id)
    clock.tick(0.5)
    second, _s, _t = tracker.frame("C5E952")
    assert second.press_id != first.press_id
    clock.tick(1.0)
    assert tracker.release_due("C5E952", first.press_id) is None
    assert tracker.end("C5E952", first.press_id) is None
    assert tracker.release_due("C5E952", second.press_id) is second


def test_a_ninety_seven_frame_burst_is_a_long_press_in_bucket_three() -> None:
    """The misclassification the wire-time anchor exists to prevent: a
    bridge delivering 97 frames at once is 3.88 s of hold, not a tap."""
    tracker, clock = _tracker()
    for _ in range(97):
        tracker.frame("C5E952")
    clock.tick(MAX_EXTENDED_RELEASE_MS / 1000 + 0.1)
    state = tracker.release_due("C5E952")
    assert state is not None
    assert state.duration_s == pytest.approx(97 * FRAME_CADENCE_S)
    assert state.bucket == 3 and not state.is_short


def test_clear_forgets_every_press_and_returns_them() -> None:
    tracker, _ = _tracker()
    tracker.frame("AA0000")
    tracker.frame("BB0000")
    assert {s.address for s in tracker.clear()} == {"AA0000", "BB0000"}
    assert dict(tracker.states) == {}


def test_press_ids_are_unique_by_default() -> None:
    tracker = PressTracker(clock=Clock())
    a, _s, _t = tracker.frame("AA0000")
    tracker.end("AA0000")
    b, _s, _t = tracker.frame("AA0000")
    assert a.press_id != b.press_id and a.press_id.startswith("AA0000-")


# --- what a press reaches --------------------------------------------------

OP_POINT = {
    "bus_address": "295682",
    "linked_modules": [
        {"module_address": "81f6", "outputs": [{"channel": 7}, {"channel": 9}, {"channel": 2}]},
        {"module_address": "4707", "outputs": [{"channel": 1}]},
        "junk",
        {"module_address": "", "outputs": [{"channel": 1}]},
        {"module_address": "0E6C", "outputs": [{"channel": "x"}, {}]},
    ],
}


def test_impacted_groups_are_the_distinct_module_groups_in_link_order() -> None:
    assert impacted_groups(OP_POINT) == [("81F6", 2), ("81F6", 1), ("4707", 1)]
    assert impacted_groups({}) == []


def test_the_primary_link_names_the_press() -> None:
    assert primary_link(OP_POINT) == ("81F6", 7)
    assert primary_link({"linked_modules": [{"module_address": "4707"}]}) == ("4707", None)
    assert primary_link({}) == (None, None)
