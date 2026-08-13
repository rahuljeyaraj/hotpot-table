"""Tracker process — MediaPipe Hands, roles, and the cursor datagram
(doc sections 3, 4.6, 6.4, 11; doc section 21, M5 build item 1).

Was `common/stub.py` from M0 through M4. From here on it is doc section
11.1's pipeline:

    attach shm -> read latest frame -> downsample -> MediaPipe Hands ->
    landmark 9 -> camera->stage homography -> role assignment -> UDP to
    of and core

Six decisions in here are not obvious from that line, and four of them
were forced by something already built.

**1. The point is warped, not the frame — and this is a change of course
from what M4m expected.** `core/bin_grid.py`'s docstring says the warped
"table crop" is "what MediaPipe will run on (M5, not built)". It is not,
and the reason is physical rather than architectural: `warp_frame_to_stage`
crops to the table, and hands *reach in from outside the table*. A diner's
hand enters over the near edge with the wrist and forearm off-table
entirely, so warping first throws away the part of the hand a palm detector
most needs to see — and it fails as "detection is unreliable near the near
edge", which reads exactly like a lighting or a model problem. So this
process runs MediaPipe on the whole (downsampled) camera frame and puts the
single cursor point through `H_cam_to_stage`, which is doc section 11.1's
own pipeline order, unchanged.

Nothing downstream notices the difference: the warped canvas IS stage space
(`warp_frame_to_stage(frame, H, stage_size)`), so the camera bin grid's
lines and this cursor are already in one space either way. The classifier
still warps its own frames — it crops rectangles and has no hands to lose.

**2. No homography means no datagrams at all.** Doc section 21 makes M5
depend on M4 with a reason attached: "the cursor is meaningless without
it." A cursor emitted in camera pixels but labelled stage space would
hit-test against real bin rects and produce confident, wrong hovers. Core
sends `H` in `welcome` (doc section 5.3); until it does, this process
tracks nothing and says so once.

**3. Stale frames stop the emission, they do not freeze it.** Doc section
6.4: "tracker sends nothing rather than sending a frozen cursor", plus
`{"t":"stat","frames_stale":true}` to core. The tracks are reset too — a
role held across an outage of unknown length would mean the bowl hand
keeping a pointer role it inherited before the camera died.

**4. The frame ring reader is local rather than `classifier.main`'s.**
Not duplication for its own sake: the two want opposite failure behaviour.
`RingSource.frame()` raises a sentence for an operator to read, because a
classifier command has a human waiting on a reply. This one returns None
and keeps polling, because there is nobody waiting and the correct
response to "no frame this instant" at 30Hz is to try again in 5ms. A
shared class would have to serve both and would end up with a flag.

**5. Doc section 11.2's model ladder does not exist in the installed
MediaPipe.** `model_complexity` was a Solutions-API parameter and
`mp.solutions` is gone in mediapipe 1.0.0 (see `backend_mediapipe.py`'s
docstring — verified, not remembered). The ladder is now which `.task`
bundle you load, and Google publishes one. The probe below is still real:
it measures the achieved rate over the first few seconds and logs it, and
it will climb if a second bundle ever lands in `models/`. What it cannot
do is pretend a one-rung ladder was climbed.

**6. SUPERSEDED 2026-08-12, same day it was written — the hand-size/
frame-position theory below is FALSE, disproven with the developer's own
real hand on the live rig, and decision 7 replaces it.** It is kept
verbatim rather than deleted because commit `65b2717` shipped it as fact
and a future reader diffing that commit needs the wrong reasoning in
front of them, not just a hole where it was:

> RIG_FEEDBACK item 2 — "the cursor doesn't appear when the hand is near
> the table edges" — is not about edges, and it is not about confidence
> thresholds, rotation or resolution (all four were tested to exhaustion
> first and none of them moves the number). MediaPipe's palm detector
> letterboxes whatever it is handed into a fixed square input, so the
> only quantity that decides whether a hand is findable is **the hand's
> share of the frame's LONG side**... Cropping is the only lever that
> moves it, and it is worth 83% -> 100% over a 60-trial sweep.

The sweep above was real and the percentages were real — but it swept a
*composited* hand pasted into the frame, and a composited hand and a
real one turned out not to behave the same way. The very next test
pasted the SAME known-good hand into the SAME rig frame at the SAME
size and position a real hand was failing at, and it detected every
time; the real hand failed at every crop, rotation, resolution,
brightness and contrast setting tried. Position was separately
re-confirmed not to matter (`debug/tracker_acquisition_2026-08-12/`,
gitignored but not deleted — the throwaway scripts and evidence images
this paragraph is reporting). Whatever was wrong, it was never about how
much of the frame the hand fills or where it sits in the frame — the
crop below is not, and never was, the fix. **`ROI_MARGIN_PX` and
`MIN_ROI_PX` remain** (decision 7 repurposes the same table footprint as
a scan BOUND, not a detection crop), but `DEFAULT_INPUT_WIDTH` no longer
reaches hand detection at all — see decision 7.

**7. The real fault was sensor noise on the hand's own pixels, only
fixable by denoising a HAND-SIZED window, not the whole table — and
MediaPipe's own tracking state turns out to be tied to the exact crop
framing it was given, which decided this whole mechanism's shape
(2026-08-12).** Three things were measured, each on this rig against the
developer's real hand, none of them against a composited stand-in:

First: denoising a **hand-sized** crop (bilateral filter, twice) finds a
cold real hand reliably; denoising the **whole table crop** the same way
does not — and this was checked hard enough to trust, because it
disagreed with itself once. A whole-crop-then-bilateral run scored
20/20 at one hand position and 0/20 at another in the same live session
(`debug/tracker_acquisition_2026-08-12/raw_test2_score.py` vs
`raw_test3_score.py`) — position-dependent enough that it is not a fix,
it is a trap for whoever re-tries it next. The hand-sized window
(`ACQUISITION_WINDOW_PX`, 700 px at this rig's framing) scored 20/20 at
both positions.

Second: once MediaPipe has successfully detected a hand through a given
crop, it keeps tracking that hand through SUBSEQUENT calls using that
SAME crop **even with the denoise removed** — a real, measured
consequence, not folklore (`debug/tracker_acquisition_2026-08-12/
seed_test2.py`: same window afterwards, 5/5 with no denoise at all).
That is a genuine cost saving: acquisition needs the hand-sized window
AND the denoise; ongoing tracking of an already-found hand needs only
the window.

Third, and this is what makes "hand off to a plain whole-frame detect"
unworkable: switching the SAME `HandLandmarker` instance from a
successful windowed detect back to the ordinary whole-crop-downsampled
call it used to get, **even denoised**, loses the hand on the very next
call — 0/5, immediately (`seed_test2.py` phases 4-5). MediaPipe's
VIDEO-mode tracking state is bound to the crop's own framing (position
and scale), not to "a hand was recently seen somewhere in this camera
feed." There is no seeding it from outside. What DOES survive is a small
SHIFT in the same window's origin, same size, re-centred to follow the
hand tick to tick (`seed_test3.py`: shifts up to 60px, still tracking,
denoise or not) — which is the only reason continuous tracking is
possible here at all without re-running the palm detector from scratch
every single frame.

The mechanism this forced: `_hand_windows` holds up to `max_hands`
committed crops, each a fixed-size window re-centred on its own last
detection every time it is serviced, never handed a differently-framed
crop. `_acquisition_tile_centers` covers the table's own footprint
(decision 6's crop, now a scan BOUND rather than the detection input
itself) with overlapping hand-sized tiles; an empty hand slot's turn is
spent scanning the next tile instead, denoised, looking for a new hand.
One `backend.detect()` call per tick, always — round-robining service
between committed windows and open scan slots is what keeps that true
with `max_hands` > 1, per doc section 11.2's own performance discipline.
A tracked window that stops being found is not dropped instantly
(`ACQUISITION_WINDOW_LOST_S`) — the same "a one-frame dropout should not
cost the whole state" reasoning `tracker/tracking.py`'s own
`TRACK_GRACE_S` already uses for the layer above this one.

**The first rig run of this mechanism pulsed — locked on, lost, locked
on, lost — worse near the edges, and this third finding is exactly why.**
One shared `backend.detect()` call was still being routed through a
SINGLE `MediaPipeBackend` instance for both scanning and tracking. With
`max_hands` at its default of 2, a lone tracked hand still leaves a
second slot open, so the round-robin keeps spending most turns scanning
for a second hand — on the SAME instance the first hand's lock lives on.
Every scan tile is a different crop, so every scan turn reset the tracked
hand's lock exactly as this finding says it would, and the very next
service turn had to re-acquire from a WARM (undenoised) call against
what was, for MediaPipe's own state, a cold crop — a coin flip, which is
what pulsing IS. `backend_factory`, added the same day once this was
seen on the rig: one independent `HandLandmarker` per `max_hands`
tracking slot, plus one more for scanning, so scanning for a second hand
can never touch the instance the first hand is locked to. `_all_backends`
is the one place anything that has to reach every instance (rotation,
mirror, shutdown) goes through, so a future config knob cannot land on
the scanner alone and leave the tracked hands unrotated.

**2026-08-13: per-instance isolation alone did not fix the pulsing —
the real cause was a duplicate window on the same hand.** Confirmed on
the rig with the isolation fix already in place: pulsing was still
there, edges only, never in the table's centre. Three isolated live
tests on the rig ruled out the obvious suspects one at a time — a fixed
699px window at the exact reported edge position scored 36-38/38 with
NO per-tick denoise, 38/38 with it (so denoise-while-tracking is not
it); the SAME window with the real `_clamp_window` re-centring run every
tick, exactly as `tick()` does, scored 302/304 (so re-centre/clamp
toggling at the frame edge is not it either). Neither reproduced any
flicker. What both tests shared, and production does not, is a single
backend serviced every tick with no competing turn. Reading
`_next_detection_input`/`_update_acquisition` side by side found the
actual mechanism: at `max_hands=2` with one real hand on the table, one
slot is permanently free, so the round robin ALWAYS alternates between
servicing that hand and taking a scan turn — a scan tile large enough to
also contain the already-tracked hand (likely; a 700px window against a
table-sized scan region overlaps a lot) would detect that SAME hand and
claim the free slot for it, with no check that anything was already
tracking that position. Two windows on one physical hand, serviced on
alternating ticks, only one reaching the cursor per tick — the
duplicate's rougher scan-tile-derived framing misses often enough that
the cursor visibly drops and returns. `_update_acquisition` now declines
to claim a free slot when the hit already falls inside an existing
window (see its own comment). **Still owed: this fix has not yet been
watched live on the rig** — the mechanism explains the reported symptom
precisely and is grounded in the two ruled-out tests above, not in
guesswork, but "the code can produce this" is not the same claim as "and
this is what did" until someone sees the pulsing actually stop.

**2026-08-13, later the same day: live A/B on the rig — `562eeed` was
necessary but not sufficient, and `max_hands=1` is what actually stops
the pulsing.** Three tests, in order, same rig, same edge position that
pulsed before: `max_hands=1` — pulsing gone, one real hand, developer
confirmed. `max_hands=2` restored, same one-hand-at-the-edge case that
had just been clean — pulsing came straight back. With a SECOND real
hand also at the edge (both slots filled, so `_next_detection_input`
never offers a scan turn — `len(active) == max_hands`), both hands
pulsed, not in lockstep: right detected/left not, then flipped, and
sometimes both missed the same tick. **This is a different failure from
the duplicate-window bug above, which needs a FREE slot to reproduce at
all, and it has not been root-caused** — the round-robin arithmetic
alone (each filled slot serviced every other tick, ~15Hz here at a
~30Hz capture rate, against `tracking.py`'s 500ms `TRACK_GRACE_S`)
does not obviously explain a miss this frequent, and nobody has yet
logged per-tick `conf` at the edge to see whether detection confidence
itself is what is marginal there, the way `min_hand_detection_confidence`
flickering was flagged as a live possibility before this test ran.
**Decided, not diagnosed further: `tracker.max_hands` defaults to `1`
in both `config/system.json` and `config/system.default.json`.**
Two-hand tracking is disabled, not fixed — `_hand_windows`, the round
robin and `backend_factory`'s per-slot instances are all still here,
unchanged, for whenever this is revisited, but nothing on the rig has
shown it can be made to hold up with two real hands yet.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from hotpot.common import config, cursorbus, framebus, geometry, health, log, wire
from hotpot.tracker import backend_mediapipe, backend_stub, tracking
from hotpot.tracker.backend import Backend, Detection

_log = logging.getLogger("hotpot.tracker")

CORE_HOST = "127.0.0.1"
CORE_PORT = 8765          # doc section 4.1 default

_ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = _ROOT / "models"

# Doc section 6.4's staleness bound, the same one `classifier/main.py`
# uses. Past this the camera is dead or stalled and this process must go
# quiet rather than repeat its last cursor.
STALE_S = 0.5

# Doc section 6.5's downsample-before-MediaPipe step. **No longer reaches
# hand detection** — see the module docstring's decision 7: a whole-frame
# downsample is exactly the framing that can never cold-acquire a real
# hand, denoised or not. `downsample()` is kept (still correct, still a
# small pure function, still config-plumbed as `tracker.input_width`) in
# case a future backend wants a cheap whole-frame path again, but
# `TrackerProcess.tick` does not call it any more.
DEFAULT_INPUT_WIDTH = 480

# See the module docstring's decision 7. How far OUTSIDE the table's own
# footprint `table_roi` reaches, in capture pixels — a SCAN bound now, not
# a detection crop (that was decision 6, superseded the same day it was
# measured: see the docstring). Still the right number for the job it has
# now, and for the same physical reason it was measured for the old job: a
# hand reaches in from *outside* the table (decision 1), and a bound tight
# enough to exclude the forearm would exclude acquisition tiles that would
# otherwise have caught it. 200 px is about 210 mm at this rig's
# ~0.95 px/mm, roughly a forearm's width of slack on every side.
DEFAULT_ROI_MARGIN_PX = 200

# A crop smaller than this in either axis is not a table footprint, it is
# a symptom of a bad homography — scan the full frame instead of a sliver.
# Cheap insurance: `H` is exactly the thing in this system that has
# already been observed to come back confidently wrong (CLAUDE.md's
# `rms_px: 0.0, n_points: 4` incident).
MIN_ROI_PX = 160

# ---------------------------------------------------------------------------
# Acquisition (module docstring, decision 7 — 2026-08-12)
# ---------------------------------------------------------------------------

# The hand-sized window's side, in capture pixels. Measured on this rig,
# against the developer's real hand, at two positions (roughly table
# centre and a near-edge reach): 700px scored 20/20 at both — see
# `debug/tracker_acquisition_2026-08-12/raw_test2_score.py` and
# `raw_test3_score.py`. **Not swept across many hand sizes or many diners
# — a single-session number, TUNE rather than assume if it under- or
# over-performs on the rig.** Must stay well clear of the ~90-99px
# transition band decision 6's own (still-valid) composited-hand sweep
# found, which it does with a wide margin: a window this size holds a
# ~100px palm at roughly 14-20% of its own width, several times the
# fraction that mattered on the whole frame.
ACQUISITION_WINDOW_PX = 700

# Centre-to-centre spacing of acquisition scan tiles. Deliberately less
# than `ACQUISITION_WINDOW_PX` so consecutive tiles overlap — a hand
# straddling a tile boundary must still be fully inside at least one tile,
# not split across two. ~33% overlap; not measured against a real hand
# that happened to be sitting exactly on a boundary, so treat as a
# reasoned default rather than a proven one.
ACQUISITION_TILE_STRIDE_PX = 470

# How long a committed tracking window may go unmatched before it is
# freed back to the scan rotation. Doc section 11.3's own two-guard
# pattern (`tracker/tracking.py`'s `TRACK_GRACE_S`/`PROMOTE_DELAY_S`)
# argues for a real grace period rather than dropping on the first empty
# tick: a hand a committed window briefly loses (motion blur, a fast
# gesture) should get a few more tries at the SAME framing — decision 7's
# own finding that re-detection needs the window's exact framing means a
# window given up too eagerly has to re-earn a hit from cold, denoised,
# scan-rotation odds, not just get re-centred.
ACQUISITION_WINDOW_LOST_S = 1.0

# Doc section 11.2's probe: "start at 0, measure for 5 seconds, and if the
# measured rate is above 45 fps try 1 and keep it only if it stays above
# 25." Kept as three named numbers because they are three different
# claims, and the middle one is the only one that is a preference.
PROBE_SECONDS = 5.0
PROBE_CLIMB_ABOVE_FPS = 45.0
PROBE_KEEP_ABOVE_FPS = 25.0

# How long the loop sleeps when there is no new frame. The ring is written
# at ~30Hz, so this is a sixth of a frame interval — short enough that the
# newest frame is picked up promptly, long enough that an idle tracker is
# not a busy loop on a board with four cores and no spare one (doc 10.4).
IDLE_SLEEP_S = 0.005

# 2026-08-12: the staff view's Developer tab redraws its raw-landmark
# debug view (RIG_FEEDBACK item 10) at 10Hz, the same cadence
# core/main.py already uses for the Bins tab and the reduced `hands`
# message — a human eye gets nothing from 60Hz here, and it would just
# compete with the real cursor path for the control link's send queue.
# Own throttle, independent of `emit_hz`.
LANDMARKS_HZ = 10.0

# Developer feedback running M5 on the rig (2026-08-12): the cursor, drawn
# at the tracked landmark's own stage position, sits under the hand's
# shadow and is invisible most of the time — the projected field is the
# table's only light (CLAUDE.md's "hard invariant"), so a hand over its
# own cursor blocks it outright, it is not merely "partly covered".
# Shifted here, upstream of both core's hit test and oF's rendering, so
# the visible dot and whatever it is hovering never disagree — doc
# section 9.4: "core hit-tests stage-space cursors against stage-space
# rects," the same points oF draws. Direction is toward the far edge
# (smaller stage Y — TableGeometry.h's "+y from far edge towards the
# diner"): this module's own docstring establishes hands always reach in
# from the near edge, so that is the one direction clear of the arm/hand
# behind the tracked point, for every bin and every widget.
#
# **Shrunk 2026-08-12** (was 70mm) when the cursor landmark itself moved
# from landmark 9 (middle-finger MCP, the palm centre — deep under the
# hand, needing real clearance) to landmark 8 (index fingertip —
# `backend_mediapipe.py`'s own doc section 11.2 override). The fingertip
# is normally already the most exposed, forward-most point of a reaching
# hand, so it needs only a small nudge clear of its own tip, not a
# fingertip's reach. **Not yet physically confirmed at either value** —
# still owes a rig observation of the cursor actually sitting just ahead
# of the fingertip rather than under it.
CURSOR_SHADOW_CLEARANCE_MM = 15.0

# This rig's plywood (TableGeometry.h/geometry_store.py's TABLE_H_MM).
# Duplicated rather than imported: this process does not import `core`
# (doc's process separation), the same reason geometry_store.py's own
# TABLE_W_MM/TABLE_H_MM are themselves a duplicate of TableGeometry.h's.
_TABLE_H_MM = 914.4


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

class FrameSource:
    """The shared-memory ring, opened lazily and re-opened after failure.

    Lazy and re-opening for `classifier/main.py`'s reason, which applies
    identically here: camera may not be up yet, may die, and comes back
    with a **new** segment (doc section 20.1), so a reader attached once at
    startup would hold a corpse forever after the first camera restart.
    """

    def __init__(self, name: str = framebus.SHM_NAME,
                 open_reader: Optional[Callable[[], Any]] = None) -> None:
        self.name = name
        self._open_reader = open_reader or (lambda: framebus.FrameReader(name))
        self._reader: Optional[Any] = None
        self.last_frame_id: int = -1

    def drop(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            try:
                reader.close()
            except Exception:      # noqa: BLE001 - closing a dead segment
                pass

    def next_frame(self):
        """`(array, frame_id)` for a frame not seen before, or `(None,
        reason)` where reason is one of `"none"`, `"stale"`, `"same"`.

        The reason matters to the caller: `"stale"` is doc section 6.4's
        camera-is-dead case and has to be reported to core and reset the
        tracks, while `"same"` is the ordinary state of a loop spinning
        faster than the camera and means nothing at all.
        """
        import numpy as np      # noqa: WPS433 - local, see geometry.fit

        if self._reader is None:
            try:
                self._reader = self._open_reader()
            except (FileNotFoundError, ValueError):
                return None, "none"
        reader = self._reader
        try:
            if reader.is_stale(timeout_s=STALE_S):
                return None, "stale"
            frame = reader.read()
        except Exception:      # noqa: BLE001 - a dead segment reads as anything
            self.drop()
            return None, "none"
        if frame is None:
            return None, "none"
        if frame.frame_id == self.last_frame_id:
            return None, "same"
        self.last_frame_id = frame.frame_id
        arr = np.frombuffer(frame.data, dtype=np.uint8)
        return arr.reshape((reader.height, reader.width, reader.channels)), \
            frame.frame_id


def downsample(frame, target_width: int):
    """`(small_frame, scale)` where `scale` multiplies a coordinate in the
    small frame back up to the original's pixels.

    Returned rather than recomputed by the caller so there is exactly one
    place the two can disagree. A wrong scale here does not crash: it puts
    every cursor at a fraction of its true position, which looks like a bad
    homography and would be debugged as one.
    """
    import cv2      # noqa: WPS433

    height, width = frame.shape[:2]
    if target_width <= 0 or width <= target_width:
        return frame, 1.0
    scale = width / float(target_width)
    target_height = max(1, int(round(height / scale)))
    small = cv2.resize(frame, (target_width, target_height),
                       interpolation=cv2.INTER_AREA)
    return small, scale


def table_roi(h, stage, frame_shape, margin: float = DEFAULT_ROI_MARGIN_PX):
    """The table's own footprint in capture pixels, padded and clamped —
    `(x0, y0, w, h)`, or None meaning "use the whole frame".

    See the module docstring's decision 7 for why this exists at all —
    it now bounds the acquisition scan rather than being the detection
    crop itself (that was decision 6, superseded).

    None rather than a raise for every reason it can fail, and the caller
    treats all of them identically by detecting on the uncropped frame:
    a table with no homography yet is the ordinary first-boot state (the
    Developer tab's raw-landmark view has to keep working there — that is
    the whole reason `_maybe_send_landmarks` runs ahead of the homography
    gate), and a homography bad enough to put the table off-frame should
    degrade to today's behaviour rather than to a sliver of nothing.

    The four stage corners are projected through `H^-1` rather than the
    table's bounding box being assumed: a homography maps a rectangle to
    a QUADRILATERAL (geometry_store.py's own note, and the reason its
    derived rects came out 26% large), so the camera-space footprint is
    the bounding box of that quad, which on an off-square camera is not
    the same rectangle as any pair of opposite corners would give.
    """
    import math                 # noqa: WPS433 - local, see geometry.fit

    if h is None:
        return None
    try:
        inverse = geometry.invert(h)
    except geometry.GeometryError:
        return None

    stage_w, stage_h = float(stage[0]), float(stage[1])
    if not (stage_w > 0 and stage_h > 0):
        return None
    corners = ((0.0, 0.0), (stage_w, 0.0), (stage_w, stage_h), (0.0, stage_h))
    xs, ys = [], []
    for corner in corners:
        try:
            px, py = geometry.apply(inverse, corner)
        except geometry.GeometryError:
            return None
        if not (math.isfinite(px) and math.isfinite(py)):
            return None
        xs.append(px)
        ys.append(py)

    height, width = frame_shape[0], frame_shape[1]
    x0 = max(0, int(math.floor(min(xs) - margin)))
    y0 = max(0, int(math.floor(min(ys) - margin)))
    x1 = min(int(width), int(math.ceil(max(xs) + margin)))
    y1 = min(int(height), int(math.ceil(max(ys) + margin)))
    if x1 - x0 < MIN_ROI_PX or y1 - y0 < MIN_ROI_PX:
        return None
    if (x0, y0, x1, y1) == (0, 0, int(width), int(height)):
        # Nothing to crop. Saying so lets the caller skip a full-frame
        # copy every tick rather than slicing the array to itself.
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _acquisition_tile_centers(bounds, window_px: float = ACQUISITION_WINDOW_PX,
                              stride_px: float = ACQUISITION_TILE_STRIDE_PX):
    """Evenly-spaced `(cx, cy)` tile centres covering `bounds`
    `(x0, y0, w, h)`, close enough together that a hand-sized window
    centred on any point of `bounds` is fully inside at least one tile.

    One axis at a time (`_axis`), then the cross product — the table is a
    rectangle, and independent axes are what let a wide-but-short ROI get
    3 tiles across and 1 down instead of forcing a square tile count. A
    span no wider than one window is one centre, not zero: `table_roi`
    already refuses anything under `MIN_ROI_PX`, so the only way `w` (or
    `h`) is small here is a table that is genuinely smaller than
    `ACQUISITION_WINDOW_PX`, which one centred tile covers completely.
    """
    import math      # noqa: WPS433 - local, see table_roi

    def axis(origin: float, span: float) -> List[float]:
        if span <= window_px:
            return [origin + span / 2.0]
        first = origin + window_px / 2.0
        last = origin + span - window_px / 2.0
        n = int(math.ceil((span - window_px) / stride_px)) + 1
        if n <= 1:
            return [(first + last) / 2.0]
        step = (last - first) / (n - 1)
        return [first + i * step for i in range(n)]

    x0, y0, w, h = bounds
    xs = axis(float(x0), float(w))
    ys = axis(float(y0), float(h))
    return [(cx, cy) for cy in ys for cx in xs]


def _clamp_window(cx: float, cy: float, window_px: float,
                  frame_w: int, frame_h: int):
    """`(x0, y0, w, h)` — a `window_px` square centred on `(cx, cy)`,
    shrunk and shifted to fit inside a `frame_w` x `frame_h` frame.

    Shrinking (not just shifting) is what keeps this correct against the
    tiny frames `python/tests` uses as well as the real 1920x1080 one: a
    window bigger than the frame it is being cut from cannot be centred by
    translation alone.
    """
    w = int(min(window_px, frame_w))
    h = int(min(window_px, frame_h))
    x0 = int(max(0, min(frame_w - w, cx - w / 2.0)))
    y0 = int(max(0, min(frame_h - h, cy - h / 2.0)))
    return x0, y0, w, h


def _denoise_for_acquisition(img):
    """Bilateral filter, twice. The one thing that turns a cold real hand
    from unfindable into found IN A HAND-SIZED WINDOW — see the module
    docstring's decision 7. Two passes rather than one: measured 20/20 at
    every window size tried from 440px up, where one pass needed the full
    700px to be reliable (`debug/tracker_acquisition_2026-08-12/
    raw_test2_score.py`). Never applied to an already-tracking window
    (decision 7's second finding: tracking survives with no denoise at
    all once a window has one successful hit) — denoise is an
    ACQUISITION cost, not a per-tick one.
    """
    import cv2      # noqa: WPS433

    once = cv2.bilateralFilter(img, 9, 75, 75)
    return cv2.bilateralFilter(once, 9, 75, 75)


# ---------------------------------------------------------------------------
# Model rungs (doc section 11.2, translated — see the module docstring)
# ---------------------------------------------------------------------------

def available_rungs(models_dir: Path = MODELS_DIR) -> List[str]:
    """The model bundles actually present, cheapest first. Absent files are
    skipped rather than being an error — a rig with one bundle has a
    one-rung ladder, which is a fact about the rig, not a fault.
    """
    out = []
    for name in backend_mediapipe.MODEL_RUNGS:
        path = Path(models_dir) / name
        if path.is_file():
            out.append(str(path))
    return out


class _AcquisitionWindow:
    """One committed hand-tracking crop (module docstring, decision 7).

    `x0`/`y0`/`w`/`h` are the EXACT crop last handed to `backend.detect` —
    not a centre-and-size pair recomputed on read, because decision 7's
    own finding is that MediaPipe's tracking is bound to the crop's exact
    framing, so this object has to be able to answer "what did the
    backend actually see" without rounding twice. `last_hit` is wall-clock
    time (`now`, not `time.monotonic()` read fresh — the same clock every
    other timestamp in this class already uses), read by
    `ACQUISITION_WINDOW_LOST_S` to decide when a window has gone
    unmatched long enough to give up on.
    """

    __slots__ = ("x0", "y0", "w", "h", "last_hit")

    def __init__(self, x0: int, y0: int, w: int, h: int,
                last_hit: float) -> None:
        self.x0, self.y0, self.w, self.h = x0, y0, w, h
        self.last_hit = last_hit


# ---------------------------------------------------------------------------
# The process body
# ---------------------------------------------------------------------------

class TrackerProcess:
    """Everything except the sockets' lifecycle, so a test can drive it one
    tick at a time with a fake ring and a scripted backend — the same split
    `CameraProcess`, `Classifier` and `Core` already use.
    """

    def __init__(self, *,
                 source: Optional[FrameSource] = None,
                 backend: Optional[Backend] = None,
                 backend_factory: Optional[Callable[[], Backend]] = None,
                 sender: Optional[cursorbus.Sender] = None,
                 send_stat: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 input_width: int = DEFAULT_INPUT_WIDTH,
                 roi_margin_px: float = DEFAULT_ROI_MARGIN_PX,
                 max_hands: int = 2,
                 emit_hz: float = 60.0,
                 smoothing_tau_s: float = tracking.TRACK_SMOOTHING_TAU_S
                 ) -> None:
        self.source = source or FrameSource()
        self.sender = sender or cursorbus.Sender()
        self.send_stat = send_stat or (lambda msg: None)
        self.input_width = input_width
        self.roi_margin_px = roi_margin_px
        self.max_hands = max(1, int(max_hands))
        self.emit_hz = emit_hz
        # RIG_FEEDBACK item 8: `tracking.py`'s own module docstring has the
        # full reasoning for why the filter lives inside `HandTracker`
        # rather than out here.
        self.tracker = tracking.HandTracker(smoothing_tau_s=smoothing_tau_s)

        # **2026-08-12, found live on the rig, not in a test:** a single
        # shared MediaPipe instance for both scanning and tracking pulsed
        # — locked onto a hand, then lost it, over and over, worse near
        # the edges. Decision 7's own `seed_test2.py` already proved why:
        # switching a `HandLandmarker`'s crop framing even once resets its
        # tracking lock. The scheduler switches framing on purpose EVERY
        # time an open hand slot's turn goes to scanning instead (which is
        # most ticks, since `max_hands` is usually 2 and rarely both
        # filled) — so a single shared instance was being yanked off the
        # tracked hand's window onto a scan tile and back, resetting the
        # lock nearly every cycle. `backend_factory`, when given, builds
        # one INDEPENDENT instance per tracking slot plus one for
        # scanning, so a scan for a second hand never touches the first
        # hand's own detector. `None` (every test in this file) falls
        # back to one shared instance for everything, unchanged from
        # before — a stub backend has no crop-framing state to lose, so
        # the distinction is invisible to a test and only matters against
        # the real `MediaPipeBackend`.
        self._backend_factory = backend_factory
        if backend is not None:
            self._scan_backend: Backend = backend
        elif backend_factory is not None:
            self._scan_backend = backend_factory()
        else:
            self._scan_backend = backend_stub.Stub()
        # `self.backend` is `_scan_backend` by another name — a stable
        # attribute for logging/introspection (`_report_rung`, exception
        # messages) that predates this class having more than one
        # instance to hold. NOT a second factory call: `_all_backends`
        # dedupes by identity, but a fourth real `MediaPipeBackend` load
        # for a label nothing distinct needs would still be a wasted
        # model load every startup.
        self.backend: Backend = self._scan_backend
        self._track_backends: List[Backend] = [
            (backend_factory() if backend_factory else self._scan_backend)
            for _ in range(self.max_hands)]

        # Acquisition (module docstring, decision 7). Up to `max_hands`
        # committed tracking windows, one slot each, `None` where a slot
        # is free for the scan rotation to fill. `_acq_centers` is the
        # scan tile list, cached like the old detection crop was —
        # recomputing it is a matrix inverse plus four projections plus a
        # tile layout, and none of its inputs change at 30Hz. Invalidated
        # wherever `_h` is written (the one place either input can move)
        # and by a frame shape change (`_next_detection_input`'s own
        # check), the same two triggers `_roi`/`_roi_shape` used to have.
        self._hand_windows: List[Optional[_AcquisitionWindow]] = \
            [None] * self.max_hands
        self._acq_centers: List[Any] = []
        self._acq_centers_shape = None
        self._scan_idx = 0
        self._service_idx = 0

        # Doc section 5.3: core owns `H_cam_to_stage` and pushes it in
        # `welcome`. None until it has. Held under a lock because `welcome`
        # arrives on the control link's read thread while the capture loop
        # reads it — the same split every other link in this tree has.
        self._lock = threading.Lock()
        self._h: Optional[List[List[float]]] = None
        self._stage = (1920.0, 1080.0)

        self._warned_no_h = False
        self._stale = False
        self._last_emit: Optional[float] = None
        self._last_landmarks_send: Optional[float] = None
        # MediaPipe's VIDEO mode rejects a timestamp that does not
        # increase, and this process owns the clock (backend.py's
        # docstring) so a backend swap mid-probe cannot restart it.
        self._timestamp_ms = 0

        self.frames_seen = 0
        self.emitted = 0
        self._probe_started: Optional[float] = None
        self._probe_frames = 0
        self.measured_fps: Optional[float] = None

        self._stop = threading.Event()

    # -- configuration from core (doc section 4.2's `welcome`) -------------

    def apply_welcome(self, cfg: Dict[str, Any]) -> None:
        """Doc section 4.2: "core replies to hello with the client's current
        configuration, so clients hold no config of their own beyond how to
        find core."

        Tolerant of every field being absent: a `welcome` from a core that
        has no homography yet is the ordinary first-boot case, not an
        error. A malformed `H` is treated as no `H` — better to emit
        nothing than to emit through a matrix that came out of a bad line.
        """
        if not isinstance(cfg, dict):
            return
        h = cfg.get("homography_cam_to_stage")
        with self._lock:
            if _is_matrix3x3(h):
                self._h = [[float(v) for v in row] for row in h]
                self._warned_no_h = False
            else:
                self._h = None
            stage = cfg.get("stage")
            if (isinstance(stage, (list, tuple)) and len(stage) == 2
                    and all(isinstance(v, (int, float)) for v in stage)):
                self._stage = (float(stage[0]), float(stage[1]))
            # Both inputs to the scan bound just moved. Dropping the cache
            # here rather than comparing values is what stops a
            # re-calibrated table from scanning the old table's footprint
            # until the process is restarted. Committed tracking windows
            # are left alone deliberately — they are camera-pixel
            # positions, not table-relative ones, so a hand already being
            # tracked stays valid through a recalibration.
            self._acq_centers = []
            self._acq_centers_shape = None
        hz = cfg.get("emit_hz")
        if isinstance(hz, (int, float)) and 0 < hz <= 240:
            self.emit_hz = float(hz)
        mirror = cfg.get("mirror_handedness")
        if isinstance(mirror, bool):
            self.set_mirror_handedness(mirror)
        # 2026-08-12: core owns this (`geometry.view_rotation_deg`,
        # `state/view_rotation.json`) the same way it owns the homography
        # — a fact about the physical rig, pushed here rather than read
        # from a local default, so a value this process invented can never
        # disagree with what core actually has on disk. Only 0/90/180/270
        # are ever written there (`GeometryStore.set_view_rotation`'s own
        # validation), so the isinstance/membership check here is a
        # defence against a malformed `cfg`, not a real validation layer.
        rotation = cfg.get("view_rotation_deg")
        if isinstance(rotation, int) and not isinstance(rotation, bool) \
                and rotation in (0, 90, 180, 270):
            self.set_camera_rotation(rotation)

    def _all_backends(self) -> List[Backend]:
        """Every distinct backend instance this process owns — one call
        site for anything that has to reach ALL of them (config that
        applies to the whole camera/session, or shutdown), now that
        decision 7's crop-isolation fix (2026-08-12) can mean more than
        one. Deduplicated by identity: in the common case (no
        `backend_factory`, every test in this file) `_scan_backend` and
        every `_track_backends` entry ARE `self.backend`, and applying a
        setting three times over would be harmless but is not the point.
        """
        out: List[Backend] = []
        for b in [self.backend, self._scan_backend, *self._track_backends]:
            if not any(b is existing for existing in out):
                out.append(b)
        return out

    def set_camera_rotation(self, deg: int) -> None:
        """The camera's physical mount rotation (doc section 12.6's Rotate
        control's old value, `state/view_rotation.json`), applied to
        whatever backend(s) are running so MediaPipe detects against a
        right-way-up frame — see `backend_mediapipe.py`'s own "180-degree
        mount compensation". Same shape as `set_mirror_handedness`: set on
        the backend, at the one place a frame is actually rotated, so
        nothing else in this process ever has to know or care. Every
        backend instance gets it (`_all_backends`) — a scan-only detector
        that missed this would scan against an upside-down frame while
        tracking detectors ran right-way-up.
        """
        for b in self._all_backends():
            if hasattr(b, "mount_rotation_deg"):
                b.mount_rotation_deg = deg

    def set_mirror_handedness(self, mirror: bool) -> None:
        """Doc section 11.3's swap-hands switch, applied live.

        Set on every backend instance (`_all_backends`) rather than held
        here because the label has to be flipped at the one place it is
        produced — anything else means two spellings of the same hand
        existing at once somewhere in the pipeline. Backends that have no
        opinion on handedness (the stub) simply do not have the attribute.
        """
        for b in self._all_backends():
            if hasattr(b, "mirror_handedness"):
                b.mirror_handedness = bool(mirror)

    @property
    def has_homography(self) -> bool:
        with self._lock:
            return self._h is not None

    # -- one iteration -----------------------------------------------------

    def tick(self, now: Optional[float] = None) -> bool:
        """Read at most one new frame, track, emit. True if a datagram
        went out.

        Returning a bool rather than sleeping internally is what lets the
        tests step this deterministically; `run_forever` below owns the
        sleeping.
        """
        now = time.monotonic() if now is None else now

        # Rate cap FIRST, before a frame is pulled. Doc section 4.6 is "one
        # datagram per camera frame" and `emit_hz` (doc section 8.6) is a
        # ceiling on that, not a clock of its own — so a camera slower than
        # emit_hz simply emits at camera rate, and this is a skip rather
        # than a wait.
        #
        # Before the read, not after, and that ordering is load-bearing: a
        # frame consumed and then discarded advances `last_frame_id` past a
        # frame nobody looked at, so the NEXT tick would see the one after
        # it as "the newest" and the cap would silently halve the effective
        # rate. Skipping the read leaves the ring alone and the next tick
        # picks up whatever is newest then, which is what a cap should do.
        #
        # `_last_emit` starts at None rather than 0.0 so the very first
        # tick is never gated. With 0.0 it depended on `time.monotonic()`
        # being far from zero — true in production, and exactly the kind of
        # accident that holds until someone passes a clock in.
        if (self.emit_hz > 0 and self._last_emit is not None
                and (now - self._last_emit) < (1.0 / self.emit_hz)):
            return False

        frame, info = self.source.next_frame()
        if frame is None:
            if info == "stale":
                self._on_stale()
            return False
        self._on_frames_resumed()
        self.frames_seen += 1

        # Read the homography BEFORE detecting, not after: the acquisition
        # scan bound (module docstring, decision 7) is derived from it.
        # The cursor pipeline's own use of `h` further down is unchanged,
        # and so is the rule that a tick with no homography still detects
        # and still reports landmarks — `_acquisition_bounds` falls back
        # to the whole frame for that case exactly as `table_roi` used to.
        with self._lock:
            h = self._h
            stage = self._stage

        crop, origin, service_slot = self._next_detection_input(frame, h, stage)
        scale = 1.0     # decision 7: acquisition/tracking windows are never
                        # downsampled — shrinking the hand is the one thing
                        # that must not happen to them.
        self._timestamp_ms += 1
        if crop is None:
            # Every slot full and no scan tiles built yet (the very first
            # tick, before a frame shape is known) — nothing to detect
            # against. One `_timestamp_ms` tick still burns above so a
            # backend swap immediately after cannot see time run backwards.
            detections: List[Detection] = []
        else:
            # 2026-08-12: routed to a PER-SLOT backend instance, not the
            # single shared one — see `__init__`'s own note. Scanning a
            # different tile on `self._scan_backend` must never touch the
            # instance a tracked hand's own lock lives on.
            active_backend = (self._track_backends[service_slot]
                              if service_slot is not None
                              else self._scan_backend)
            try:
                detections = active_backend.detect(crop, self._timestamp_ms)
            except Exception:      # noqa: BLE001 - a detector must not kill the loop
                _log.exception("tracker: %s raised during detect",
                               active_backend.name)
                detections = []
            self._update_acquisition(detections, origin, frame.shape,
                                     service_slot, now)

        # 2026-08-12: moved ahead of the homography check below, on
        # purpose. Detection itself has nothing to do with the
        # camera->stage solve — MediaPipe finds hands (or doesn't) in raw
        # frame pixels regardless of whether the table has ever been
        # calibrated. The staff view's Developer tab (RIG_FEEDBACK item
        # 10) needs to answer "does MediaPipe see a hand at all"
        # independent of calibration state; gating detection itself on
        # `h` would make that view go blank on an uncalibrated table for
        # a reason that has nothing to do with what it is trying to show.
        # The cursor pipeline below is UNCHANGED — it still requires `h`
        # and still sends nothing without one (doc section 21: "the
        # cursor is meaningless without it").
        self._maybe_send_landmarks(detections, scale, origin, now)

        if h is None:
            if not self._warned_no_h:
                _log.warning("tracker: no camera->stage homography from core "
                             "yet — tracking nothing. Calibrate the table "
                             "corners on the Setup tab (doc 12.6).")
                self._warned_no_h = True
            return False

        staged = self._to_stage(detections, scale, origin, h, stage)
        hands = self.tracker.update(staged, now)
        self.sender.send(hands, ts=time.time())
        self._last_emit = now
        self.emitted += 1
        self._count_probe_frame(now)
        return True

    # -- acquisition (module docstring, decision 7) -------------------------

    def _acquisition_bounds(self, h, stage, shape):
        """The region the scan rotation covers — the table's own footprint
        padded by `roi_margin_px` (decision 6's old crop, now a bound), or
        the whole frame when there is no homography yet, the same
        first-boot fallback `table_roi` itself used to provide directly.
        """
        roi = table_roi(h, stage, shape, self.roi_margin_px)
        if roi is not None:
            return roi
        height, width = shape[0], shape[1]
        return (0, 0, int(width), int(height))

    def _refresh_acquisition_tiles(self, h, stage, shape) -> None:
        """Rebuilds `_acq_centers` when the frame shape changes. A
        homography change is NOT checked here — `apply_welcome` drops the
        cache directly the moment `_h` moves, the same split `_roi`/
        `_roi_shape` used to have, because comparing matrices by value
        every tick is real work this only needs to do once per change.
        """
        if self._acq_centers and self._acq_centers_shape == shape[:2]:
            return
        bounds = self._acquisition_bounds(h, stage, shape)
        self._acq_centers = _acquisition_tile_centers(bounds)
        self._acq_centers_shape = shape[:2]
        self._scan_idx = 0
        _log.info("tracker: acquisition scan covers %dx%d at (%d,%d) of "
                  "%dx%d in %d tiles — see main.py's decision 7",
                  bounds[2], bounds[3], bounds[0], bounds[1],
                  shape[1], shape[0], len(self._acq_centers))

    def _next_detection_input(self, frame, h, stage):
        """`(crop, (origin_x, origin_y), service_slot)` for this tick's one
        `backend.detect()` call.

        Round-robins between servicing each committed `_hand_windows` slot
        and scanning the next acquisition tile, landing on exactly one
        `backend.detect()` call per tick regardless of `max_hands` — doc
        section 11.2's own "one extra call per frame" performance
        discipline, kept true here by construction rather than by
        accident. `service_slot` is the `_hand_windows` index this tick's
        result belongs to (a hand slot was serviced), or `None` (this
        tick scanned instead, and a hit claims the first free slot).

        The active set is recomputed every tick rather than cached: slots
        free and fill between ticks, and a `None`-marked "scan" turn is
        only offered when there is a free slot AND scan tiles exist, so a
        table at `max_hands` capacity spends every tick refreshing
        windows it already has rather than scanning for a hand there is
        no room to track anyway.
        """
        shape = frame.shape
        self._refresh_acquisition_tiles(h, stage, shape)
        frame_h, frame_w = shape[0], shape[1]

        active: List[Optional[int]] = [
            i for i, w in enumerate(self._hand_windows) if w is not None]
        if len(active) < self.max_hands and self._acq_centers:
            active.append(None)      # None marks the scan turn
        if not active:
            return None, (0.0, 0.0), None

        turn = active[self._service_idx % len(active)]
        self._service_idx += 1

        if turn is not None:
            win = self._hand_windows[turn]
            crop = frame[win.y0:win.y0 + win.h, win.x0:win.x0 + win.w]
            return crop, (float(win.x0), float(win.y0)), turn

        cx, cy = self._acq_centers[self._scan_idx % len(self._acq_centers)]
        self._scan_idx += 1
        x0, y0, w, h_px = _clamp_window(cx, cy, ACQUISITION_WINDOW_PX,
                                        frame_w, frame_h)
        crop = _denoise_for_acquisition(frame[y0:y0 + h_px, x0:x0 + w])
        return crop, (float(x0), float(y0)), None

    def _update_acquisition(self, detections: Sequence[Detection], origin,
                            frame_shape, service_slot: Optional[int],
                            now: float) -> None:
        """Folds this tick's detection result back into `_hand_windows` —
        the other half of `_next_detection_input`'s contract.

        A hit re-centres the slot it came from (or, from a scan tick,
        claims the first free slot) on the detection's own capture-pixel
        position — tight re-centring, not the tile/window's own centre,
        because decision 7's own finding is that ongoing tracking needs
        the window kept close to where the hand actually is, and a hand
        rarely sits exactly where a fixed scan tile was centred.

        **Known gap, not fixed here: only `detections[0]` is ever used.**
        If a single scan tile's crop happens to contain TWO hands at once
        (both reaching in close together while cold), only the first
        claims a slot this tick; the second is still relayed to the
        cursor pipeline this same tick (`tick` passes the full
        `detections` list to `_to_stage` regardless of this method), but
        gets no committed window of its own until a later scan cycle
        happens to land on it. This is the "two hands, does round-robin
        scanning miss one" question the module docstring's decision 7
        flags as still open — now answered precisely for the one case
        that matters: cold, simultaneous, same-tile. Two hands arriving
        at different times, or in different tiles, are unaffected.

        A miss ages the slot rather than freeing it immediately
        (`ACQUISITION_WINDOW_LOST_S`) — see the constant's own docstring.
        Only a SERVICED slot can be aged or freed here; a miss on a scan
        tile is not a "loss", it is the ordinary result of most scans.
        """
        frame_h, frame_w = frame_shape[0], frame_shape[1]
        if detections:
            det = detections[0]
            cx = det.x + origin[0]      # scale is always 1.0 here — see tick()
            cy = det.y + origin[1]
            x0, y0, w, h_px = _clamp_window(cx, cy, ACQUISITION_WINDOW_PX,
                                            frame_w, frame_h)
            if service_slot is not None:
                win = self._hand_windows[service_slot]
                win.x0, win.y0, win.w, win.h = x0, y0, w, h_px
                win.last_hit = now
            else:
                # 2026-08-13 pulsing bug: a scan tile overlapping an
                # ALREADY-tracked hand used to claim the free slot too,
                # producing a second window on the same physical hand.
                # The two windows are then serviced on alternating ticks
                # (round robin at max_hands capacity), and only one
                # slot's detection reaches the cursor per tick — the
                # duplicate's rougher, scan-tile-derived framing misses
                # often enough that the cursor visibly drops and returns
                # every other beat. This is a real hand at (cx, cy)
                # already sitting inside an existing committed window,
                # not a second hand — decline the slot instead of
                # cloning a tracker onto a hand that already has one.
                if any(existing is not None and
                       existing.x0 <= cx < existing.x0 + existing.w and
                       existing.y0 <= cy < existing.y0 + existing.h
                       for existing in self._hand_windows):
                    return
                for i, existing in enumerate(self._hand_windows):
                    if existing is None:
                        self._hand_windows[i] = _AcquisitionWindow(
                            x0, y0, w, h_px, now)
                        break
            return
        if service_slot is not None:
            win = self._hand_windows[service_slot]
            if win is not None and now - win.last_hit > ACQUISITION_WINDOW_LOST_S:
                self._hand_windows[service_slot] = None

    def _to_stage(self, detections: Sequence[Detection], scale: float,
                  origin, h: Sequence[Sequence[float]],
                  stage) -> List[Detection]:
        """Window pixels -> capture pixels -> stage space.

        Two steps now, in this order, and neither is optional. The
        backend returned coordinates in the crop it was handed
        (`backend.py`'s docstring) — a native-resolution acquisition tile
        or tracking window (module docstring, decision 7; `scale` is
        always 1.0, kept as a parameter so this method's own shape did
        not have to change) — and `H_cam_to_stage` was solved against the
        camera's **capture** resolution (doc section 8.5's `camera_size`),
        so applying `H` to an un-offset coordinate would be applying it to
        a point in a space it was never fitted for. Dropping the origin
        would put every cursor short by the window's own corner — a
        constant offset, which is exactly what a mis-calibrated table
        looks like.

        Points off the stage are kept, not clipped. A hand held over the
        table edge is a real hand at a real position, and core's hit tests
        answer "no bin" for it correctly; clamping would pile every
        out-of-range hand onto the border of the nearest bin.
        """
        # CURSOR_SHADOW_CLEARANCE_MM converted to this stage's own Y scale —
        # `stage` comes from core (doc section 5.3), not hardcoded, the same
        # geometry_store.mm_to_stage does for the fixed TABLE_H_MM.
        clearance_px = CURSOR_SHADOW_CLEARANCE_MM * stage[1] / _TABLE_H_MM

        origin_x, origin_y = origin
        out: List[Detection] = []
        for det in detections:
            try:
                sx, sy = geometry.apply(h, (det.x * scale + origin_x,
                                            det.y * scale + origin_y))
            except geometry.GeometryError:
                # A point that maps to infinity through a badly conditioned
                # matrix. Dropping the hand is right: there is no position
                # to report, and reporting a huge number would be a cursor
                # somewhere off in the corner of nothing.
                continue
            out.append(Detection(x=sx, y=sy - clearance_px, conf=det.conf,
                                 handedness=det.handedness))
        return out

    # -- staff view debug: every raw MediaPipe point (RIG_FEEDBACK item 10) -

    def _maybe_send_landmarks(self, detections: Sequence[Detection],
                              scale: float, origin, now: float) -> None:
        """Every detected hand's full 21-point skeleton, in CAPTURE-
        resolution camera pixels — never stage space, and deliberately:
        this exists to answer "does MediaPipe see anything" independent
        of the homography, so it must not go through the same transform
        that requires one. Sent over the control link (`send_stat`, the
        same channel `{"t":"stat",...}` already uses) rather than the
        cursorbus UDP path — this is staff-view debug telemetry, not
        part of doc section 4.6's cursor datagram, and core relays it to
        every connected tablet unmodified.

        Sent even when `detections` is empty: an explicit "0 hands right
        now" is itself the signal a human reading the Developer tab
        needs — silence would be indistinguishable from the tracker
        being dead, which the process pip already reports separately.
        """
        if (self._last_landmarks_send is not None
                and (now - self._last_landmarks_send) < (1.0 / LANDMARKS_HZ)):
            return
        self._last_landmarks_send = now
        # The crop's origin goes back on here for the same reason it does
        # in `_to_stage`: this view draws over the staff view's RAW camera
        # feed, so a point that forgot the offset would land short of the
        # hand by the crop's corner and read as a tracking error rather
        # than as an arithmetic one.
        origin_x, origin_y = origin
        hands = []
        for det in detections:
            if not det.landmarks:
                continue
            hands.append({
                "handedness": det.handedness,
                "conf": round(det.conf, 2),
                "points": [[round(x * scale + origin_x, 1),
                            round(y * scale + origin_y, 1)]
                          for x, y in det.landmarks],
            })
        self.send_stat({"t": "landmarks", "hands": hands})

    # -- doc section 6.4's staleness ---------------------------------------

    def _on_stale(self) -> None:
        if self._stale:
            return
        self._stale = True
        _log.warning("tracker: no camera frames for %.1fs — going quiet "
                     "(doc 6.4)", STALE_S)
        # Doc section 6.4's second bullet, verbatim in shape.
        self.send_stat({"t": "stat", "who": "tracker", "frames_stale": True})
        # Roles do not survive an outage — see HandTracker.reset's docstring.
        self.tracker.reset()
        # Nor do committed acquisition windows (module docstring, decision
        # 7): a camera outage of unknown length means whatever they were
        # centred on may no longer be there, and a resumed camera can come
        # back at a different capture resolution (doc section 20.1) that
        # would make an old window's pixel coordinates meaningless anyway.
        self._hand_windows = [None] * self.max_hands

    def _on_frames_resumed(self) -> None:
        if not self._stale:
            return
        self._stale = False
        _log.info("tracker: camera frames resumed")
        self.send_stat({"t": "stat", "who": "tracker", "frames_stale": False})

    # -- doc section 11.2's probe ------------------------------------------

    def _count_probe_frame(self, now: float) -> None:
        if self.measured_fps is not None:
            return
        if self._probe_started is None:
            self._probe_started = now
            self._probe_frames = 0
            return
        self._probe_frames += 1
        elapsed = now - self._probe_started
        if elapsed < PROBE_SECONDS:
            return
        self.measured_fps = self._probe_frames / elapsed if elapsed > 0 else 0.0
        _log.info("tracker: %s held %.1f fps over %.0fs (%d frames)",
                  self.backend.name, self.measured_fps, elapsed,
                  self._probe_frames)
        self._report_rung()

    def _report_rung(self) -> None:
        """Doc section 11.2's "log which rung it settled on".

        Says plainly when there is nothing to climb to. The alternative —
        logging "settled on rung 0" for a ladder with one rung — reads like
        a probe that ran and would stop anyone ever asking why the higher
        model is not being used.
        """
        rungs = available_rungs()
        if self.measured_fps is None:
            return
        if len(rungs) < 2:
            _log.info("tracker: one model bundle available (%s) — nothing to "
                      "probe upward to. Doc 11.2's ladder needs a second "
                      "`.task` bundle in models/ to have a second rung.",
                      rungs[0] if rungs else "none")
            return
        if self.measured_fps > PROBE_CLIMB_ABOVE_FPS:
            _log.info("tracker: %.1f fps is above %.0f — a heavier bundle is "
                      "worth trying (doc 11.2). Set it in models/ and "
                      "restart; keep it only above %.0f fps.",
                      self.measured_fps, PROBE_CLIMB_ABOVE_FPS,
                      PROBE_KEEP_ABOVE_FPS)

    # -- the loop ----------------------------------------------------------

    def run_forever(self) -> None:
        while not self._stop.is_set():
            if not self.tick():
                # Nothing to do this instant. Every reason for that (no new
                # frame, no camera, no homography, rate-capped) is answered
                # by the same short wait.
                self._stop.wait(IDLE_SLEEP_S)

    def stop(self) -> None:
        self._stop.set()
        for b in self._all_backends():
            b.close()
        self.sender.close()


def _is_matrix3x3(h: Any) -> bool:
    if not isinstance(h, (list, tuple)) or len(h) != 3:
        return False
    for row in h:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return False
        for v in row:
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return False
            if v != v:      # NaN
                return False
    return True


def build_backend(cfg: Dict[str, Any],
                  models_dir: Optional[Path] = None) -> Backend:
    """The real detector if everything it needs is present, else the stub.

    Falling back rather than exiting is doc section 3.3's rule: this
    process's job is to come up and hold its link open. A tracker that
    refused to start over a missing model file would take its own pip red,
    which reads as a crash rather than as "download the bundle".

    `models_dir` is a parameter rather than a module read for the reason
    every other path in this repo is (`cal_path`, `camera_grid_path`): a
    default argument binds at definition time, so a test that reassigned
    the module constant would still be handed the real `models/` — and on
    this machine that directory has a real bundle in it, so the fallback
    test would have passed by loading MediaPipe rather than by falling
    back. Found by that test failing.
    """
    models_dir = Path(models_dir) if models_dir is not None else MODELS_DIR
    rungs = available_rungs(models_dir)
    if not rungs:
        _log.warning("tracker: no MediaPipe model bundle in %s — running the "
                     "stub backend, no hands will be tracked. See "
                     "models/README.md.", models_dir)
        return backend_stub.Stub()
    mirror = bool(config.get(cfg, "tracker.mirror_handedness", False))
    max_hands = int(config.get(cfg, "tracker.max_hands", 2) or 2)
    # RIG_FEEDBACK item 2's own third suspect ("check backend_mediapipe.py's
    # confidence thresholds against a logged conf value at the edge") —
    # config keys now, not hardcoded 0.5s, so they can be tuned from the
    # rig against the Developer tab's raw landmark view with no rebuild.
    # 0.5 here matches MediaPipe's own default exactly, so a system.json
    # with no opinion on these three keys changes nothing.
    min_detection = float(config.get(
        cfg, "tracker.min_hand_detection_confidence", 0.5))
    min_presence = float(config.get(
        cfg, "tracker.min_hand_presence_confidence", 0.5))
    min_tracking = float(config.get(
        cfg, "tracker.min_tracking_confidence", 0.5))
    real = backend_mediapipe.MediaPipeBackend.load(
        rungs[0], num_hands=max_hands, mirror_handedness=mirror,
        min_detection_confidence=min_detection,
        min_presence_confidence=min_presence,
        min_tracking_confidence=min_tracking)
    return real if real is not None else backend_stub.Stub()


def main() -> None:
    log.setup("tracker")
    cfg = config.load()
    host = config.get(cfg, "core.host", CORE_HOST)
    port = config.get(cfg, "core.control_port", CORE_PORT)

    # Module docstring, decision 7's per-instance isolation: a factory
    # rather than one `build_backend(cfg)` call, so `TrackerProcess` can
    # build an independent `MediaPipeBackend` per tracking slot plus one
    # for scanning, none of them sharing a crop-framing lock with another.
    proc = TrackerProcess(
        backend_factory=lambda: build_backend(cfg),
        sender=cursorbus.Sender([
            ("127.0.0.1", int(config.get(cfg, "cursor.of_port",
                                         cursorbus.OF_PORT))),
            ("127.0.0.1", int(config.get(cfg, "cursor.core_port",
                                         cursorbus.CORE_PORT))),
        ]),
        input_width=int(config.get(cfg, "tracker.input_width",
                                   DEFAULT_INPUT_WIDTH)),
        roi_margin_px=float(config.get(cfg, "tracker.roi_margin_px",
                                       DEFAULT_ROI_MARGIN_PX)),
        # Same key `build_backend` above reads for MediaPipe's own
        # `num_hands` — duplicated rather than threaded through `Backend`
        # (which has no `max_hands` of its own to ask), the same
        # duplication `_TABLE_H_MM` already argues for elsewhere in this
        # file: two processes reading one source of truth rather than one
        # process handing state to another that has no other use for it.
        max_hands=int(config.get(cfg, "tracker.max_hands", 2) or 2),
        emit_hz=float(config.get(cfg, "tracker.emit_hz", 60)),
        # RIG_FEEDBACK item 8. Config key, not a hardcoded constant, for
        # the same reason `min_hand_detection_confidence` above is one —
        # tunable from the rig against a real hand with no rebuild.
        smoothing_tau_s=float(config.get(
            cfg, "tracker.smoothing_tau_s", tracking.TRACK_SMOOTHING_TAU_S)),
    )

    client = wire.Client(host, port, "tracker",
                         on_connect=proc.apply_welcome,
                         on_message=lambda msg: _on_control(proc, msg))
    proc.send_stat = client.send
    beat = health.Heartbeat(client.send, who="tracker")

    client.start()
    beat.start()
    # Same readiness rule as every other client (common/stub.py): ready
    # means the link is open and the loop is running, not that core has
    # answered — doc section 3.3 makes start order an optimisation.
    log.ready("tracker")
    try:
        proc.run_forever()
    finally:
        beat.stop()
        client.stop()
        proc.stop()


def _on_control(proc: TrackerProcess, msg: Dict[str, Any]) -> None:
    """The one thing core tells the tracker after `welcome`: that a config
    it cares about changed (doc section 11.3's swap-hands button, which is
    "fastest to determine by trying it" and therefore has to apply without
    a restart).
    """
    if msg.get("t") == "cfg":
        proc.apply_welcome(msg.get("cfg") or {})


if __name__ == "__main__":
    main()
