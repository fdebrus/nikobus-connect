"""A key press as the bus shows it: frames, hold, release.

A Nikobus key does not send "pressed" and "released". It repeats its
``#N<address>`` frame every 40 ms for as long as it is held, and stops.
Everything a host wants to know about a press — that it started, how
long it was held, when it ended, whether it was a tap or a hold — has
to be inferred from that stream, and this module does the inferring.

Two things make the inference less obvious than counting seconds:

* **Duration is wire time, not wall time.** A serial-over-IP bridge or
  a busy host can hold frames back and deliver them in a burst, so the
  gap between the first and last frame says nothing. What survives any
  buffering is the number of frames: each one is 40 ms the wire was
  carrying the key. ``frame_count * FRAME_CADENCE_S`` is the duration.
* **Release is silence, with patience for bursts.** A press has ended
  when no frame arrived for ``RELEASE_THRESHOLD_MS``. But a bridge that
  just flushed a burst (gaps far below 40 ms) is likely to stall again,
  so the patience is extended to the wire time the burst implied, up
  to ``MAX_EXTENDED_RELEASE_MS``, and relaxes once the cadence is
  normal again.

:class:`PressTracker` holds the state per address and is driven by the
host: :meth:`PressTracker.frame` on every ``#N`` frame, and
:meth:`PressTracker.release_due` from whatever clock or loop the host
runs. It knows nothing of event buses, tasks or module refreshes; the
clock is injected so a test can drive it without sleeping.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: One ``#N`` frame is this much held time on the wire.
FRAME_CADENCE_S: float = 0.040

#: A press shorter than this is a tap, from this one a hold.
SHORT_PRESS_S: float = 1.0

#: Hold milestones, in seconds of wire time; one notification each.
TIMER_THRESHOLDS_S: tuple[int, ...] = (1, 2, 3)

#: Buckets a release is filed under: ``0`` below 1 s … ``3`` from 3 s.
MAX_BUCKET: int = 3

#: Silence after the last frame that ends a press, on a healthy link.
#: 150 ms would do for the wire itself; 300 absorbs ordinary bridge
#: hiccups.
RELEASE_THRESHOLD_MS: int = 300

#: An inter-frame gap below this cannot have come from the wire — the
#: frame came out of a buffer. Marks a burst.
BURST_GAP_THRESHOLD_S: float = 0.005

#: The last gaps considered, and how many of them must be burst-shaped
#: before the release patience is extended.
BURST_RECENT_GAPS_WINDOW: int = 4
BURST_DETECT_GAP_COUNT: int = 3

#: The most patience a burst can buy. Bounds the release latency.
MAX_EXTENDED_RELEASE_MS: int = 5000


@dataclass
class PressState:
    """One key being held, as far as the frames seen so far say."""

    address: str
    press_id: str
    started_at: float
    last_frame_at: float
    frame_count: int = 1
    last_timer_threshold: int = 0
    recent_gaps: deque[float] = field(
        default_factory=lambda: deque(maxlen=BURST_RECENT_GAPS_WINDOW)
    )
    release_threshold_ms: float = float(RELEASE_THRESHOLD_MS)

    @property
    def duration_s(self) -> float:
        """Held time on the wire: frames times cadence."""
        return self.frame_count * FRAME_CADENCE_S

    @property
    def bucket(self) -> int:
        return press_bucket(self.duration_s)

    @property
    def is_short(self) -> bool:
        return is_short_press(self.duration_s)

    def silence_ms(self, now: float) -> float:
        """Milliseconds since the last frame."""
        return (now - self.last_frame_at) * 1000.0

    def release_due(self, now: float) -> bool:
        """True once the silence has outlasted the current patience."""
        return self.silence_ms(now) >= self.release_threshold_ms


def press_bucket(duration_s: float) -> int:
    """``0`` for under a second, ``1``, ``2``, and ``3`` for three seconds or more."""
    return min(int(duration_s), MAX_BUCKET)


def is_short_press(duration_s: float) -> bool:
    return duration_s < SHORT_PRESS_S


def update_release_threshold(state: PressState) -> None:
    """Adjust the patience for release from the recent inter-frame gaps.

    Mostly burst gaps: a bridge stall just drained into us, and the
    next one is likely; wait as long as the wire time this press has
    accumulated, never less than before, never beyond the cap. A full
    window of ordinary gaps: the link is healthy again, back to the
    baseline so a real release is seen promptly. Anything in between
    leaves the patience as it is.
    """
    burst_gaps = sum(1 for gap in state.recent_gaps if gap < BURST_GAP_THRESHOLD_S)
    if burst_gaps >= BURST_DETECT_GAP_COUNT:
        implied_stall_ms = state.duration_s * 1000.0
        state.release_threshold_ms = min(
            float(MAX_EXTENDED_RELEASE_MS),
            max(state.release_threshold_ms, implied_stall_ms),
        )
    elif burst_gaps == 0 and len(state.recent_gaps) >= BURST_RECENT_GAPS_WINDOW:
        state.release_threshold_ms = float(RELEASE_THRESHOLD_MS)


def timers_crossed(state: PressState) -> list[int]:
    """The hold milestones this press has just reached, each once.

    Anchored to wire time, so a burst that delivers three seconds of
    frames at once crosses all three in one call, in order.
    """
    crossed: list[int] = []
    elapsed = state.duration_s
    for threshold in TIMER_THRESHOLDS_S:
        if state.last_timer_threshold >= threshold or elapsed < threshold:
            continue
        state.last_timer_threshold = threshold
        crossed.append(threshold)
    return crossed


def _default_press_id(address: str, now: float) -> str:
    return f"{address}-{now:.3f}-{uuid.uuid4().hex[:8]}"


class PressTracker:
    """The presses in flight, one per key address.

    ``clock`` is a monotonic seconds source; ``press_id`` names a new
    press from its address and start time. Both default to the real
    thing and exist for tests.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        press_id: Callable[[str, float], str] = _default_press_id,
    ) -> None:
        self._clock = clock
        self._press_id = press_id
        self._states: dict[str, PressState] = {}

    @property
    def states(self) -> Mapping[str, PressState]:
        """The presses in flight, by upper-case address. Read-only view."""
        return self._states

    def active(self, address: str) -> PressState | None:
        return self._states.get(address.upper())

    def __contains__(self, address: object) -> bool:
        return isinstance(address, str) and address.upper() in self._states

    def frame(self, address: str) -> tuple[PressState, bool, list[int]]:
        """Account for one ``#N`` frame.

        Returns the press it belongs to, whether this frame started it,
        and the hold milestones it crossed. A first frame starts a press
        with one frame counted; a further frame counts itself, records
        the gap, and adjusts the release patience.
        """
        key = address.upper()
        now = self._clock()
        state = self._states.get(key)
        if state is None:
            state = PressState(
                address=key,
                press_id=self._press_id(key, now),
                started_at=now,
                last_frame_at=now,
            )
            self._states[key] = state
            return state, True, timers_crossed(state)
        state.recent_gaps.append(now - state.last_frame_at)
        state.last_frame_at = now
        state.frame_count += 1
        update_release_threshold(state)
        return state, False, timers_crossed(state)

    def release_due(self, address: str, press_id: str | None = None) -> PressState | None:
        """The press at ``address`` if its silence has outlasted its
        patience, else ``None``. With ``press_id``, only that press —
        so a watcher of an ended press never claims its successor. The
        press stays tracked until :meth:`end`."""
        state = self._states.get(address.upper())
        if state is None or (press_id is not None and state.press_id != press_id):
            return None
        return state if state.release_due(self._clock()) else None

    def end(self, address: str, press_id: str | None = None) -> PressState | None:
        """Forget the press at ``address`` and return it; ``None`` if
        there is none, or if ``press_id`` names another one."""
        key = address.upper()
        state = self._states.get(key)
        if state is None or (press_id is not None and state.press_id != press_id):
            return None
        return self._states.pop(key)

    def clear(self) -> list[PressState]:
        """Forget every press; returns what was in flight."""
        states = list(self._states.values())
        self._states.clear()
        return states


# --- what a press reaches -------------------------------------------------


def _group_of(channel: int) -> int:
    return 1 if channel <= 6 else 2


def impacted_groups(op_point: Mapping[str, Any]) -> list[tuple[str, int]]:
    """The ``(module address, output group)`` pairs a key's links drive.

    Read from the operation point's ``linked_modules`` as the button
    store holds them: channels 1–6 are output group 1, 7–12 group 2.
    Each pair once, in the order the links list them.
    """
    seen: list[tuple[str, int]] = []
    for link in op_point.get("linked_modules") or []:
        if not isinstance(link, Mapping):
            continue
        module = str(link.get("module_address") or "").upper()
        if not module:
            continue
        for out in link.get("outputs") or []:
            if not isinstance(out, Mapping):
                continue
            channel = out.get("channel")
            if not isinstance(channel, int):
                continue
            pair = (module, _group_of(channel))
            if pair not in seen:
                seen.append(pair)
    return seen


def primary_link(op_point: Mapping[str, Any]) -> tuple[str | None, int | None]:
    """The first ``(module address, channel)`` a key's links drive, for
    naming the press; ``(None, None)`` for a key that drives nothing."""
    for link in op_point.get("linked_modules") or []:
        if not isinstance(link, Mapping):
            continue
        module = str(link.get("module_address") or "").upper()
        if not module:
            continue
        outputs = link.get("outputs")
        channel: int | None = None
        if isinstance(outputs, list) and outputs and isinstance(outputs[0], Mapping):
            first = outputs[0].get("channel")
            if isinstance(first, int):
                channel = first
        return module, channel
    return None, None


__all__ = [
    "BURST_DETECT_GAP_COUNT",
    "BURST_GAP_THRESHOLD_S",
    "BURST_RECENT_GAPS_WINDOW",
    "FRAME_CADENCE_S",
    "MAX_BUCKET",
    "MAX_EXTENDED_RELEASE_MS",
    "RELEASE_THRESHOLD_MS",
    "SHORT_PRESS_S",
    "TIMER_THRESHOLDS_S",
    "PressState",
    "PressTracker",
    "impacted_groups",
    "is_short_press",
    "press_bucket",
    "primary_link",
    "timers_crossed",
    "update_release_threshold",
]
