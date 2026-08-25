"""common/phantom.py — the idle-table "phantom hand": a wandering path
over the stage that visits bins in turn, so the fireball moves and lights
bins while nobody is at the table.

**Pure function of elapsed time, nothing else.** No clock ownership, no
camera, no mutable per-tick state beyond what `position()` derives from
its one argument — deterministic given the same `stage_size`,
`bin_centers` and `seed`. That is what lets two different processes agree
on where the hand is right now with nothing to keep in sync but those
three numbers: `core` (`core/main.py`) decides WHEN the table has been
idle long enough and WHICH bins exist, and pushes that down once, on the
transition edge, as `cfg`'s `phantom_active`/`phantom_started_at`/
`phantom_bin_centers` fields (mirroring `mediapipe_enabled`'s own
live-push shape). `tracker/main.py` is the only process that actually
*emits* it — it is also the one place that already knows, per tick,
whether a REAL hand was tracked this frame, so "a real hand always wins"
falls out of one `if hands: ... else if self._phantom: ...` in
`TrackerProcess.tick()`, with no UDP race against a second sender: there
has only ever been one sender on the tracker's own cursor socket, real or
phantom.

Lives in `common/`, not `core/`, on purpose — `tracker/main.py` imports
nothing from `hotpot.core` today (pricing, cart, fsm — none of it belongs
in a vision process), and this module is pure geometry, the same tier as
`common/geometry.py`, so importing it does not cross that line.

Not yet run against a real camera or a real projector — see
`core/main.py`'s own phantom-activation comment for what is and is not
observed.
"""

from __future__ import annotations

import math
import random
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]

# One "leg" is the walk to a bin plus a pause there. Unhurried on purpose
# — a hand that snaps between bins reads as a bug, not as idle browsing.
LEG_TRAVEL_S = 3.5
LEG_DWELL_S = 2.5
LEG_S = LEG_TRAVEL_S + LEG_DWELL_S

# A small continuous drift so a "held" position still breathes, the same
# reason a real tracked hand on this rig is never perfectly still
# (tracking.py's own smoothing filter has noise to smooth in the first
# place). Two irrational-ratio sines rather than one so the wobble itself
# has no short repeating period a person would notice.
WOBBLE_AMPLITUDE_PX = 6.0
WOBBLE_PERIOD_S = 1.7
_WOBBLE_PERIOD_RATIO = 1.618  # golden ratio — keeps the y-wobble's period
                              # from ever lining up with the x-wobble's


def _ease_in_out(t: float) -> float:
    """Smoothstep: accelerates away from a dwell, decelerates into the
    next one, rather than the constant-velocity snap a linear lerp gives
    at both ends of a leg.
    """
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return t * t * (3.0 - 2.0 * t)


def _wobble(elapsed_s: float, seed: float) -> Point:
    wx = WOBBLE_AMPLITUDE_PX * math.sin(
        2.0 * math.pi * elapsed_s / WOBBLE_PERIOD_S + seed)
    wy = (WOBBLE_AMPLITUDE_PX * 0.6) * math.sin(
        2.0 * math.pi * elapsed_s / (WOBBLE_PERIOD_S * _WOBBLE_PERIOD_RATIO)
        + seed * 1.3)
    return wx, wy


class PhantomHand:
    """`position(elapsed_s)` is the whole contract.

    `bin_centers` with fewer than one usable point (an empty grid, or
    every entry malformed) falls back to a single waypoint at the stage's
    own centre — a phantom hand still has to be somewhere, and the stage
    centre is the one point that is never inside a wall or off the
    table.
    """

    def __init__(self, stage_size: Tuple[float, float],
                 bin_centers: Sequence[Optional[Point]],
                 seed: float = 0.0) -> None:
        self._stage = stage_size
        centers = [c for c in bin_centers if c is not None]
        if not centers:
            centers = [(stage_size[0] / 2.0, stage_size[1] / 2.0)]
        # A seeded stdlib Random, not the `random` module's global state
        # — two processes deriving "the same shuffled visiting order"
        # from nothing but the same seed is the entire point (see the
        # module docstring); global state gives each process its own
        # unrelated sequence.
        rng = random.Random(seed)
        order: List[int] = list(range(len(centers)))
        rng.shuffle(order)
        self._waypoints: List[Point] = [centers[i] for i in order]
        self._seed = seed

    def position(self, elapsed_s: float) -> Point:
        elapsed_s = max(0.0, elapsed_s)
        n = len(self._waypoints)
        leg_idx = int(elapsed_s // LEG_S) % n
        t_in_leg = elapsed_s - (elapsed_s // LEG_S) * LEG_S
        dest = self._waypoints[leg_idx]
        prev = self._waypoints[(leg_idx - 1) % n]
        if t_in_leg < LEG_TRAVEL_S:
            frac = _ease_in_out(t_in_leg / LEG_TRAVEL_S)
            x = prev[0] + (dest[0] - prev[0]) * frac
            y = prev[1] + (dest[1] - prev[1]) * frac
        else:
            x, y = dest
        wx, wy = _wobble(elapsed_s, self._seed)
        sx, sy = self._stage
        x = min(max(x + wx, 0.0), sx)
        y = min(max(y + wy, 0.0), sy)
        return x, y
