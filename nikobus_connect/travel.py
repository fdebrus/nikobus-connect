"""Where a shutter is, from how long it has been moving.

A Nikobus roller module reports only whether a channel is driving up,
driving down, or idle; it has no idea where the shutter is. A host
that wants a position has to time the travel itself: a shutter that
takes ten seconds to open is at 50 % five seconds after the open
command. This model keeps that arithmetic in one place, with the two
run times the module was programmed with, and a clock the host can
replace in a test.
"""

from __future__ import annotations

import time
from collections.abc import Callable

#: The direction words a host passes; anything else is "closing".
OPENING = "opening"
CLOSING = "closing"


class TravelCalculator:
    """A shutter's position, simulated from its run times.

    ``time_up`` and ``time_down`` are the seconds a full travel takes
    in each direction. The position is 0 closed, 100 open, and starts
    at 100 until a host sets it.
    """

    def __init__(
        self,
        time_up: float,
        time_down: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.time_up = time_up
        self.time_down = time_down
        self.position: float = 100.0
        self._clock = clock
        self._start_time: float | None = None
        self._start_pos: float | None = None
        self._direction: int | None = None  # 1 up, -1 down

    @property
    def is_moving(self) -> bool:
        return self._direction is not None

    def set_position(self, position: float) -> None:
        """Take a known position, clamped to 0..100."""
        self.position = max(0.0, min(100.0, position))

    def start_travel(self, direction: str, latency: float = 0.0) -> None:
        """The shutter started moving ``direction``, ``latency`` seconds ago.

        The latency backdates the start, so a move noticed late — a
        wall key seen on the bus after the module acted — reads the
        right position at once. It cannot be negative. A move started
        while another is running anchors on the position in flight,
        not on the last committed one: re-targeting in the same
        direction must not snap the shutter back.
        """
        self.position = self.current_position()
        self._start_pos = self.position
        self._start_time = self._clock() - max(0.0, latency)
        self._direction = 1 if direction == OPENING else -1

    def stop(self) -> None:
        """The shutter stopped: commit the position reached."""
        self.position = self.current_position()
        self._direction = None

    def current_position(self) -> float:
        """The position now, from the elapsed share of the run time."""
        if self._direction is None or self._start_time is None or self._start_pos is None:
            return self.position
        active_time = self.time_up if self._direction == 1 else self.time_down
        if active_time <= 0:
            return self.position
        progress = (self._clock() - self._start_time) / active_time * 100.0
        if self._direction == 1:
            return min(100.0, self._start_pos + progress)
        return max(0.0, self._start_pos - progress)


__all__ = ["CLOSING", "OPENING", "TravelCalculator"]
