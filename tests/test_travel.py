"""A shutter's position from its run times, with an injected clock.
Ported from Nikobus-HA's cover tests, which drove it through a patched
``time.monotonic``."""

from __future__ import annotations

from nikobus_connect.travel import CLOSING, OPENING, TravelCalculator


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _calc(up: float = 10, down: float = 10, position: float | None = None) -> tuple[TravelCalculator, Clock]:
    clock = Clock()
    c = TravelCalculator(up, down, clock=clock)
    if position is not None:
        c.set_position(position)
    return c, clock


def test_starts_open_and_set_position_clamps() -> None:
    c, _ = _calc()
    assert c.position == 100.0 and not c.is_moving
    c.set_position(150)
    assert c.position == 100.0
    c.set_position(-5)
    assert c.position == 0.0


def test_opening_progress() -> None:
    c, clock = _calc(position=0)
    c.start_travel(OPENING)
    clock.now += 5  # 5 s of a 10 s up-travel = +50 %
    assert c.current_position() == 50.0 and c.is_moving


def test_closing_progress() -> None:
    c, clock = _calc(position=100)
    c.start_travel(CLOSING)
    clock.now += 3
    assert c.current_position() == 70.0


def test_each_direction_has_its_own_run_time() -> None:
    c, clock = _calc(up=20, down=5, position=50)
    c.start_travel(OPENING)
    clock.now += 5  # a quarter of 20 s
    assert c.current_position() == 75.0
    c.start_travel(CLOSING)
    clock.now += 2.5  # half of 5 s
    assert c.current_position() == 25.0


def test_travel_clamps_at_the_ends() -> None:
    c, clock = _calc(position=80)
    c.start_travel(OPENING)
    clock.now += 5
    assert c.current_position() == 100.0
    c, clock = _calc(position=20)
    c.start_travel(CLOSING)
    clock.now += 5
    assert c.current_position() == 0.0


def test_latency_backdates_the_start() -> None:
    c, _ = _calc(position=0)
    c.start_travel(OPENING, latency=2.0)  # noticed 2 s late: already at 20 %
    assert c.current_position() == 20.0
    c, _ = _calc(position=0)
    c.start_travel(OPENING, latency=-5.0)  # a negative latency is zero
    assert c.current_position() == 0.0


def test_stop_commits_the_position_and_ends_the_move() -> None:
    c, clock = _calc(position=0)
    c.start_travel(OPENING)
    clock.now += 5
    c.stop()
    assert c.position == 50.0 and not c.is_moving
    clock.now += 9999
    assert c.current_position() == 50.0


def test_a_zero_run_time_never_moves() -> None:
    c, clock = _calc(up=0, down=0, position=50)
    c.start_travel(OPENING)
    clock.now += 100
    assert c.current_position() == 50.0


def test_retargeting_in_flight_anchors_on_the_position_in_flight() -> None:
    """Re-targeting mid-travel must not snap the shutter back to the
    last committed position."""
    c, clock = _calc(position=100)
    c.start_travel(CLOSING)
    clock.now += 5
    assert c.current_position() == 50.0
    c.start_travel(CLOSING)
    assert c.current_position() == 50.0
    clock.now += 2
    assert c.current_position() == 30.0


def test_an_unknown_direction_word_closes() -> None:
    c, clock = _calc(position=100)
    c.start_travel("down")
    clock.now += 1
    assert c.current_position() == 90.0
