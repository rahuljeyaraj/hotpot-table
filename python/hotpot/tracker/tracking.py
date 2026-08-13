"""tracker/tracking.py — doc section 11.3's two hands, two roles.

The requirement is one sentence: **the right hand selects; the left hand
holds the bowl and must never select anything.** Everything below exists
because the obvious implementation of that sentence — ask MediaPipe which
hand this is, every frame — does not survive contact with an overhead
camera. Doc section 11.3: "handedness from an overhead camera with a
possibly-mirrored image is not reliable enough to bet the interaction on."

So role is a property of a **tracked identity**, not of a frame:

    1. Track hands across frames by nearest-neighbour on the cursor point
       (gate: 150 px in stage space). Assign each a stable id.
    2. When a hand first appears:
         - if no pointer currently exists  -> pointer
         - else if MediaPipe says "Right"  -> pointer, demote the other
         - else                            -> ambient
    3. Role is LOCKED for the lifetime of that tracked id.
    4. When the pointer disappears for >500ms its role is released. The
       remaining ambient hand is promoted only after a further 500ms.

Three things about that, worth having written down before anyone changes it:

**Steps 2 and 3 are in tension and the tension is deliberate.** "Role is
LOCKED for the lifetime of that tracked id" and "demote the other" cannot
both be unconditionally true. Read literally, and as implemented: a role
never changes *on its own* — no confidence wobble, no re-classification, no
drift can flip it mid-gesture. The single exception is the explicit
demotion in step 2, which fires only at the instant a NEW hand appears and
is labelled Right. That is the case the exception exists for: a diner puts
the bowl down first and then reaches in with their right hand.

**The 500ms + 500ms in step 4 is two different guards, not one delay split
in half.** The first 500ms is "is the pointer really gone, or did the
detector blink?" — a track survives that long unseen before its id retires,
so a one-frame dropout cannot mint a new id and re-run step 2. The second
500ms is "should the bowl hand inherit control?" — and its answer is
usually no, because the pointer hand normally comes straight back. A new
hand arriving during that window takes the pointer role immediately (step 2
sees no pointer), which is right: the delay protects an *incumbent*
ambient hand from being promoted, it is not a lockout on the table.

**Matching is greedy in order of increasing distance, not in track order.**
`common.geometry.match_nearest` already does exactly this and is already
tested, so it is reused rather than reimplemented. The reason it matters
here is the same reason it mattered there: with two hands close together,
matching in list order lets the first track claim a detection that belonged
much more clearly to the second, and the two ids swap. An id swap swaps the
roles with it — the bowl hand silently becomes the pointer — which is the
one failure this whole module exists to prevent.

**RIG_FEEDBACK item 11 (2026-08-13): the match gate widens with how long a
track has gone unseen, for the same reason item 8's smoothing is
time-based rather than a fixed per-frame blend.** `MATCH_GATE_PX` alone
was a fixed 150px *regardless of `dt`*, sized (see the constant's own
comment) against a 33ms/30Hz frame. At this rig's measured low end
(4Hz, `tracker/main.py`'s docstring) an ordinary fast reach — nowhere
near the "slap" the 150px number was reasoned against — already covers
more than 150px between frames. A detection that misses the gate is not
treated as the same hand moving fast; it is treated as a *second* hand
(`_appear`), and the true hand's motion is stranded on a fresh,
**unsmoothed** track (see below) while the old one sits frozen for up to
`TRACK_GRACE_S` before retiring — which reads, on the table, as the
cursor sticking in place and then reappearing already at the new spot,
not sliding there. `_match_gate_px()` returns `max(match_gate_px,
match_speed_px_s * dt)`: unchanged at a normal camera rate (the floor
dominates, so every existing gate test below is untouched), wider only
when a track's own `dt` says the frame really was that far apart. This
does not fully remove the two-hands-passing-close risk the fixed gate
existed to bound (see `_match`'s own docstring) — it only lets that risk
grow on the same slow frames where matching is already less certain,
rather than on every frame regardless of rate. **Reasoned, not yet
confirmed against the actual stuck/reappear symptom on the rig** — see
RIG_FEEDBACK item 11.

**RIG_FEEDBACK item 8 (2026-08-13): the tracked position is smoothed here,
by a per-track time-based EMA, not fixed-per-frame.** No filter existed
between a raw per-frame detection and what went on the wire; the cursor
visibly jittered. `alpha = 1 - exp(-dt / tau)` rather than a constant
blend factor because this process's own frame interval is not constant
(`tracker/main.py`'s docstring: measured camera rate has ranged 4-30Hz on
this rig depending on what the acquisition scheduler is doing that tick) —
a fixed alpha tuned for one interval would over-smooth a slow frame and
under-smooth a fast one, where the time-based form gives the same real-
world responsiveness either way.

This is *step 1½*, folded into `_match`'s existing per-track update rather
than a second per-hand history kept in `tracker/main.py`: a track's
identity and its `last_seen` timestamp already exist here, which is
exactly the state an EMA needs (the previous value and the elapsed time
since it), and nowhere else in the pipeline has that continuity yet — a
detection hasn't been matched to an id until this module runs. A brand
new track (step 2, `_appear`) is never smoothed: there is no history to
blend against, and blending a first sighting toward its own value would
be a no-op with extra steps, not a safety measure.

**Sits downstream of `tracker/main.py`'s `_to_stage`, which is where
RIG_FEEDBACK item 1's shadow-clearance offset is applied — deliberately,
and it does not matter which side of the offset the filter is on.** The
offset is a fixed per-axis constant (`CURSOR_SHADOW_CLEARANCE_MM`
converted to px), and an EMA is linear: `EMA(x + c) = EMA(x) + c` for any
constant `c`. Filtering before adding a constant offset or after produces
the identical output, so "before" was not built as a second filtering
pass ahead of `_to_stage` — that would have needed a second per-hand
history to exist before track identity does, purely to end up with a
number this module's own state already produces for free.

Everything here is pure: no clock of its own (`now` is passed in), no
sockets, no camera. That is what lets doc section 21's M5 acceptance
scenarios — "left hand over bin 3, nothing happens; try hard to make it
select" — be tests rather than only rig work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from hotpot.common import cursorbus, geometry
from hotpot.tracker.backend import HAND_RIGHT, Detection

# Doc section 11.3 step 1's gate, in stage-space pixels. A hand crossing
# the 1524mm table in one 33ms camera frame would have to be moving about
# 4.5 m/s to break this, which is a slap rather than a reach. This is a
# FLOOR now (see `_match_gate_px` and RIG_FEEDBACK item 11 below) — the
# effective gate never shrinks below it, at any frame rate.
MATCH_GATE_PX = 150.0

# RIG_FEEDBACK item 11's widening term, in stage px/s. `_match_gate_px`
# uses `max(MATCH_GATE_PX, MATCH_SPEED_PX_S * dt)`. 3 m/s is a fast,
# deliberate reach — brisker than an ordinary pick, still well under the
# 4.5 m/s "slap" the fixed floor above was reasoned against — chosen so
# it stays BELOW the floor's own implied budget at this rig's high end
# (30Hz, dt=33ms: 3000 * 0.033 = 99px < 150px, so the floor alone governs
# and every gate test written before this item is unaffected) and only
# takes over at the low end this rig has actually measured (4Hz, dt=250ms:
# 3000 * 0.25 = 750px), where the fixed floor was the whole problem.
# Widening the gate on a slow frame does trade away some of the
# two-hands-passing-close protection the fixed gate existed for (see
# `_match`'s own docstring) — accepted deliberately, and only on the same
# slow frames where matching was already less certain to begin with, not
# on every frame regardless of rate. **Not yet tuned against the rig's
# real stuck/reappear symptom — a starting point, same as item 8's tau.**
MATCH_SPEED_PX_S = 3000.0

# Doc section 11.3 step 4's two windows. Named separately because they
# guard different things — see the module docstring.
TRACK_GRACE_S = 0.5      # unseen this long -> the id retires, role released
PROMOTE_DELAY_S = 0.5    # ...and only this much later may an ambient inherit

# RIG_FEEDBACK item 8's EMA time constant, in seconds — see the module
# docstring. Not measured against the rig's actual jitter yet, only
# reasoned: 100ms is under `TRACK_GRACE_S`/`PROMOTE_DELAY_S` (so it cannot
# make either guard's timing feel different) and comfortably above a
# single frame interval at every rate this rig has measured (4-30Hz per
# `tracker/main.py`'s docstring, i.e. 33-250ms between frames), so it
# blends more than one sample without lagging a deliberate hand movement
# by something a diner would notice. **A starting point, tuned by
# watching it on the rig, not guessed once and left** — the developer
# feedback's own words for exactly this number. `0.0` disables smoothing
# outright (used by a few tests below whose own job is matching/role
# logic, not filtering).
TRACK_SMOOTHING_TAU_S = 0.10


@dataclass
class Track:
    """One hand identity, alive across frames."""

    id: int
    role: str
    x: float
    y: float
    conf: float
    last_seen: float
    handedness: Optional[str] = None
    seen_frames: int = 1

    @property
    def is_pointer(self) -> bool:
        return self.role == cursorbus.ROLE_POINTER


@dataclass
class HandTracker:
    """Detections in, doc section 4.6 hands out. Stateful across calls."""

    match_gate_px: float = MATCH_GATE_PX
    match_speed_px_s: float = MATCH_SPEED_PX_S
    track_grace_s: float = TRACK_GRACE_S
    promote_delay_s: float = PROMOTE_DELAY_S
    smoothing_tau_s: float = TRACK_SMOOTHING_TAU_S

    tracks: List[Track] = field(default_factory=list)
    _next_id: int = 1
    # When the pointer role last became vacant by a track retiring. None
    # while a pointer exists or while none ever has. This is what the
    # second 500ms is measured from — not "when the frame went empty",
    # which would restart the clock on every frame with no hands in it and
    # make promotion unreachable.
    _pointer_released_at: Optional[float] = None

    # -- the one entry point ----------------------------------------------

    def update(self, detections: Sequence[Detection], now: float
               ) -> List[cursorbus.Hand]:
        """Advance one frame. Returns what goes on the wire, in stable id
        order so a consumer reading `hands[0]` frame after frame is not
        reading a different hand each time.

        `detections` are in **stage space** already — the caller applies
        the homography before calling, so the 150px gate is a stage-space
        distance exactly as doc section 11.3 specifies. Doing it the other
        way round would make the gate a camera-pixel distance that changes
        meaning with the camera's mounting.
        """
        self._match(detections, now)
        self._retire(now)
        self._promote(now)
        return [cursorbus.Hand(id=t.id, role=t.role, x=t.x, y=t.y,
                               conf=t.conf)
                for t in sorted(self.tracks, key=lambda t: t.id)]

    # -- step 1: matching --------------------------------------------------

    def _match(self, detections: Sequence[Detection], now: float) -> None:
        existing = list(self.tracks)
        paired = geometry.match_nearest(
            [(t.x, t.y) for t in existing],
            [(d.x, d.y) for d in detections],
            max_distance_px=[self._match_gate_px(t, now) for t in existing])

        claimed = set()
        for track, det_idx in zip(existing, paired):
            if det_idx is None:
                continue
            det = detections[det_idx]
            claimed.add(det_idx)
            track.x, track.y = self._smoothed(track, det, now)
            track.conf = det.conf
            track.last_seen = now
            track.seen_frames += 1
            # Handedness is refreshed but **the role is not recomputed
            # from it** (step 3). It is kept only so a future diagnostic
            # can show what MediaPipe currently thinks, and it is
            # deliberately never read again by this module after the
            # track's first frame.
            if det.handedness is not None:
                track.handedness = det.handedness

        for idx, det in enumerate(detections):
            if idx not in claimed:
                self._appear(det, now)

    def _match_gate_px(self, track: "Track", now: float) -> float:
        """RIG_FEEDBACK item 11 — see the module docstring. `dt <= 0` (a
        track updated this same tick already, or a non-monotonic clock)
        gets the plain floor, the same guard `_smoothed` uses for the same
        reason.
        """
        dt = now - track.last_seen
        if dt <= 0:
            return self.match_gate_px
        return max(self.match_gate_px, self.match_speed_px_s * dt)

    def _smoothed(self, track: "Track", det: Detection, now: float):
        """RIG_FEEDBACK item 8 — see the module docstring. `(x, y)` blended
        `alpha` of the way from the track's current position toward this
        frame's detection, `alpha` derived from the real time elapsed
        since the track was last updated rather than assumed to be one
        frame interval.

        `dt <= 0` (a duplicate or out-of-order timestamp — should not
        happen with a real monotonic clock, but costs nothing to guard)
        snaps straight to the detection rather than dividing by a
        non-positive number.
        """
        if self.smoothing_tau_s <= 0:
            return det.x, det.y
        dt = now - track.last_seen
        if dt <= 0:
            return det.x, det.y
        alpha = 1.0 - math.exp(-dt / self.smoothing_tau_s)
        return (track.x + alpha * (det.x - track.x),
                track.y + alpha * (det.y - track.y))

    # -- step 2: a hand first appears --------------------------------------

    def _appear(self, det: Detection, now: float) -> None:
        """Doc section 11.3 step 2, in its own order.

        The order is the whole rule and is easy to get subtly wrong. "No
        pointer exists" is checked FIRST, before handedness — which is what
        makes a left-handed diner alone at the table able to use it at all
        (doc section 11.3's own rationale: "with one hand on the table,
        that hand is the pointer regardless of which hand it is"). Checking
        handedness first would leave a lone left hand as ambient forever,
        pointing at a table that never responds.
        """
        role = cursorbus.ROLE_AMBIENT
        current = self.pointer()
        if current is None:
            role = cursorbus.ROLE_POINTER
        elif det.handedness == HAND_RIGHT:
            # The one sanctioned role change (see the module docstring):
            # a right hand arriving beside an existing pointer takes over,
            # and the incumbent is demoted rather than there being two.
            current.role = cursorbus.ROLE_AMBIENT
            role = cursorbus.ROLE_POINTER

        self.tracks.append(Track(
            id=self._next_id, role=role, x=det.x, y=det.y, conf=det.conf,
            last_seen=now, handedness=det.handedness))
        self._next_id += 1
        if role == cursorbus.ROLE_POINTER:
            self._pointer_released_at = None

    # -- step 4: retirement and promotion ----------------------------------

    def _retire(self, now: float) -> None:
        keep: List[Track] = []
        lost_pointer = False
        for track in self.tracks:
            if now - track.last_seen > self.track_grace_s:
                if track.is_pointer:
                    lost_pointer = True
                continue
            keep.append(track)
        self.tracks = keep
        if lost_pointer and self.pointer() is None:
            # Stamped once, when the role actually becomes vacant — not
            # refreshed on later frames, or the promotion deadline below
            # would keep moving away and never arrive.
            self._pointer_released_at = now

    def _promote(self, now: float) -> None:
        if self._pointer_released_at is None:
            return
        if self.pointer() is not None:
            self._pointer_released_at = None
            return
        if now - self._pointer_released_at < self.promote_delay_s:
            return
        # The longest-lived ambient hand, not the first in the list: if two
        # are on the table, the one that has been there longer is the one
        # the diner has been using as their steady hand.
        candidates = [t for t in self.tracks
                      if t.role == cursorbus.ROLE_AMBIENT]
        if not candidates:
            return
        winner = max(candidates, key=lambda t: t.seen_frames)
        winner.role = cursorbus.ROLE_POINTER
        self._pointer_released_at = None

    # -- queries -----------------------------------------------------------

    def pointer(self) -> Optional[Track]:
        for track in self.tracks:
            if track.is_pointer:
                return track
        return None

    def reset(self) -> None:
        """Forget every track and every role.

        Called when the frame source goes away (doc section 6.4's stale
        camera): the hands that come back after a camera restart are new
        hands as far as any 150px gate is concerned, and carrying a role
        across a gap of unknown length would mean the bowl hand keeping the
        pointer role it inherited before the outage.
        """
        self.tracks = []
        self._pointer_released_at = None
