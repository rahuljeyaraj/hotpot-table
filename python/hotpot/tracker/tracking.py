"""tracker/tracking.py — the single tracked hand's position, smoothed.

**2026-08-13, RIG_FEEDBACK item 11, developer's call: this module used to
implement doc section 11.3's full two-hand design — nearest-neighbour
matching with a widening gate, `_appear`'s handedness-based takeover,
role LOCKED to a track id, a 500ms+500ms retire/promote cycle to hand the
pointer role from one hand to the other. All of that machinery is gone.**

Why: `tracker/main.py` configures MediaPipe with `num_hands=max_hands`,
and `max_hands` has been `1` in both `config/system.default.json` and
`config/system.json` since 2026-08-13 (two-hand tracking measured
unstable on this rig, see that file's own module docstring). That means
`detections` arriving here structurally never carries more than one
hand — MediaPipe itself is configured not to find a second one. Every
piece of the old design existed to answer questions that only make sense
with two hands competing for one role: which detection belongs to which
existing hand (`_match`), which of two hands just arrived gets to be the
pointer (`_appear`), and how long to wait before letting the OTHER hand
take over when the first one's gone (`_retire`/`_promote`). With at most
one hand, ever, none of those questions has a second answer to choose
between — the one hand present, when present, is always the pointer.

**This was also where RIG_FEEDBACK item 11's whole investigation lived,
and it is worth being honest about what that investigation did and
didn't establish.** Three fixes to the two-hand machinery each landed,
each independently confirmed real on the rig, and the stuck-then-snap
symptom persisted through all three — because the rig this was being
debugged on only ever has one hand to track, and the two-hand matching
logic being debugged was answering a question ("which of two hands is
this") that was never actually being asked. A raw-skeleton diagnostic
(`skeletonbus.py`) confirmed the smoothness diners actually see is
achievable — MediaPipe's own output, mapped through the same homography,
with nothing else done to it, is smooth. **This module is now built to
match that: the same one-hand data, filtered only for per-frame jitter,
nothing else in the way.** If two-hand tracking is ever revisited, this
file's git history before this commit has the full doc-11.3 role/match/
hysteresis design to rebuild from — it is not preserved as dead code
here, per this codebase's own rule against leaving a removed mechanism
dormant instead of deleted.

**RIG_FEEDBACK item 8 (2026-08-13, kept): the position is still smoothed,
by a time-based EMA, not fixed-per-frame.** No filter existed between a
raw per-frame detection and what went on the wire; the cursor visibly
jittered. `alpha = 1 - exp(-dt / tau)` rather than a constant blend
factor because this process's own frame interval is not constant
(`tracker/main.py`'s docstring: measured camera rate has ranged 4-30Hz on
this rig depending on what the acquisition scheduler is doing that tick) —
a fixed alpha tuned for one interval would over-smooth a slow frame and
under-smooth a fast one, where the time-based form gives the same real-
world responsiveness either way. A gap with no detection at all (the hand
left, even for one tick) resets the filter outright rather than holding
state across it — the next sighting appears at its own true position, not
creeping in from wherever the hand used to be. That is deliberate: this
module's one job now is not to invent behaviour beyond what the raw data
says, the same discipline that ruled out the handoff-glide mitigation
tried and reverted the same day (see RIG_FEEDBACK item 11's own doc
section) — smoothing real per-frame jitter is not the same thing as
bridging a real gap, and only the former belongs here.

Everything here is pure: no clock of its own (`now` is passed in), no
sockets, no camera. That is what lets doc section 21's M5 acceptance
scenarios be tests rather than only rig work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from hotpot.common import cursorbus
from hotpot.tracker.backend import Detection

# RIG_FEEDBACK item 8's EMA time constant, in seconds — see the module
# docstring. Not measured against the rig's actual jitter yet, only
# reasoned: 100ms is comfortably above a single frame interval at every
# rate this rig has measured (4-30Hz per `tracker/main.py`'s docstring,
# i.e. 33-250ms between frames), so it blends more than one sample
# without lagging a deliberate hand movement by something a diner would
# notice. **A starting point, tuned by watching it on the rig, not
# guessed once and left.** `0.0` disables smoothing outright.
TRACK_SMOOTHING_TAU_S = 0.10

# The one pointer this module ever reports never needs a second id to be
# distinguished from — see the module docstring. Constant rather than
# incrementing: nothing downstream (doc section 4.6's wire shape, core's
# `pick_pointer`/`DwellTracker`) keys off id continuity, only off role
# and position, so there is nothing an incrementing id would buy and it
# would be one more number that means nothing to read in a log.
POINTER_ID = 1


@dataclass
class HandTracker:
    """The single tracked hand in, doc section 4.6's `hands` list out
    (0 or 1 entries — see the module docstring for why never more).
    Stateful only for the EMA filter; nothing here is per-track identity.
    """

    smoothing_tau_s: float = TRACK_SMOOTHING_TAU_S

    _x: Optional[float] = None
    _y: Optional[float] = None
    _conf: float = 0.0
    _last_seen: Optional[float] = None

    # -- the one entry point ----------------------------------------------

    def update(self, detections: Sequence[Detection], now: float
               ) -> List[cursorbus.Hand]:
        """Advance one frame. Returns 0 or 1 hands, always role POINTER.

        `detections` are in **stage space** already — the caller applies
        the homography before calling. Only `detections[0]` is ever used
        — see the module docstring: MediaPipe itself is configured to
        never hand this more than one, so a second entry here would mean
        that configuration changed, not that this module should start
        choosing between two hands again.
        """
        if not detections:
            self._x = self._y = self._last_seen = None
            self._conf = 0.0
            return []

        det = detections[0]
        if self._last_seen is None or self.smoothing_tau_s <= 0:
            x, y = det.x, det.y
        else:
            dt = now - self._last_seen
            if dt <= 0:
                x, y = det.x, det.y
            else:
                alpha = 1.0 - math.exp(-dt / self.smoothing_tau_s)
                x = self._x + alpha * (det.x - self._x)
                y = self._y + alpha * (det.y - self._y)

        self._x, self._y, self._conf, self._last_seen = x, y, det.conf, now
        return [cursorbus.Hand(id=POINTER_ID, role=cursorbus.ROLE_POINTER,
                               x=x, y=y, conf=det.conf)]

    # -- queries -----------------------------------------------------------

    def pointer(self) -> Optional[cursorbus.Hand]:
        if self._last_seen is None:
            return None
        return cursorbus.Hand(id=POINTER_ID, role=cursorbus.ROLE_POINTER,
                              x=self._x, y=self._y, conf=self._conf)

    def reset(self) -> None:
        """Forget the filter. Called when the frame source goes away (doc
        section 6.4's stale camera): the hand that comes back after a
        camera restart must appear at its own true position, not glide in
        from wherever smoothing had it before the outage.
        """
        self._x = self._y = self._last_seen = None
        self._conf = 0.0
