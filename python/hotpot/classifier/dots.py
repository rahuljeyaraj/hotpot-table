"""classifier/dots.py — projected-dot detection (doc sections 3.2, 4.7;
doc section 21 M4 build item 2).

Doc section 3.2 gives this job to the classifier and explains why: core
cannot look at a frame (I3), camera must stay dumb, and the classifier
already attaches to frames and already runs only in setting mode — which
is when calibration happens. "The classifier is therefore better
understood as 'the vision process': one process, all frame analysis
except hands."

What this module does, in one sentence: **threshold, find contours, take
centroids, filter by area, return camera-space points.** That is doc
section 21's build item verbatim, and there is deliberately nothing else
in here — no ordering, no pairing, no fitting. Ordering a detected set
against an expected one is `common/geometry.order_quad` /
`match_nearest`, and fitting is `common/geometry.fit`; both live on the
core side because core owns the geometry (doc section 5.3) and because
the classifier must not need to know what pattern was drawn.

**The lighting this runs under is the I9 exception and it is not
optional.** Dot calibration inverts the field completely: black table,
white dots, camera at a dark exposure. A white field would put the dots
on a background as bright as they are and the solve would find nothing.
So the thresholding here assumes bright blobs on a dark ground, which is
the *opposite* of every other frame this process will ever see, and it is
why `detect_dots` takes an explicit threshold rather than sharing one
with food classification.

Testable with no camera: every function takes an image array, and
`test_dots.py` builds those arrays with numpy — synthetic white discs on
a black field, plus the failure cases that matter (a highlight smaller
than a dot, two dots that merged, a dot cut by the frame edge).

**Root cause measured on the rig 2026-08-12 (M4i), ROI crop built and
confirmed 2026-08-12 (M4j) — this module itself is unchanged, the crop
happens one layer up:** a room lamp sitting outside the table, at the
extreme edge of the camera's field of view, is bright enough after the
field inverts to black that it fragments into several blobs any single
threshold has to either miss real dots to exclude, or admit alongside
them. `min_area`/`max_area` do not separate it — its fragments span the
same size range as a real dot. The touches-the-frame-edge filter does not
either — the lamp is fully in frame, just at the margin. **This module
does not crop anything itself** — doc §3.2's "the classifier must not
need to know what pattern was drawn" extends to not knowing the table's
footprint either. `classifier/main.py._detect_dots` crops the frame
before calling `detect_dots` here, when `core/dotcal.py`'s fine pass
sends an `roi` — computed from the coarse fit's own view of the table,
not a guess (CLAUDE.md's M4j). Measured result: 6 of 15 fine-grid dots
agreeing, up to 10-11 of 15, three consecutive real solves. **Not a
complete fix for every lighting condition**: a room light bright enough
to wash out the WHOLE black field (not just contaminate one corner) — a
different, newly-observed failure, also in M4j — collapses dot contrast
everywhere at once, which no crop of any size can restore.
`DEFAULT_THRESHOLD` below is tuned against real corner/marker dots.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

_log = logging.getLogger("hotpot.dots")

Point = Tuple[float, float]

# Doc section 4.7's `detect_dots` command carries `min_area`, with 40 as
# its example. A projected dot at M4's chosen radius covers a few hundred
# camera pixels, so 40 px^2 rejects specular pinpoints and sensor noise
# while being nowhere near the real dots.
DEFAULT_MIN_AREA_PX = 40.0

# The other end, which doc section 4.7 does not name and which matters
# just as much: on a black field, ANY large bright region is a blob — a
# reflection off a steel tray, a stray room light, or the whole table if
# somebody turns the lights on mid-solve. Without an upper bound the
# centroid of that region joins the fit as a confident, wildly wrong
# point. 20000 px^2 is ~140x140, several times the largest dot M4 draws.
DEFAULT_MAX_AREA_PX = 20000.0

# Bright-on-dark, on an inverted black field. 200 was the original guess,
# reasoned from "near saturation vs near zero" with no camera in front of
# it. **Measured wrong on the rig 2026-08-12, after exposure was locked
# (not auto-hunting)**: at 200, the coarse pass's large corner/marker dots
# still came through clean, but the smaller 13px grid dots did not clear
# 200 reliably — a real fine pass found only 11 of 15. 150 recovered most
# of the missing ones (14 of 15 plausible grid-sized blobs on the same
# frame) without corrupting the coarse pass, which is not sensitive to the
# difference at its dot size. Still not a full fix — see the module
# docstring's "not corrected here" note on the lamp/reflection problem
# this alone does not solve.
DEFAULT_THRESHOLD = 150

# A dot is a disc. A reflection off a tray rim is a sliver, and a light
# leak along a table edge is a long thin band; both can have a plausible
# area. Requiring the blob to be roughly as tall as it is wide throws
# those out on a shape argument rather than a size one. 0.45 tolerates a
# genuine dot squashed by an oblique camera (I10 allows down to ~70
# degrees of elevation, which foreshortens a circle to about 0.94 — so
# this is loose by a wide margin and is only catching slivers).
DEFAULT_MIN_ASPECT = 0.45

# ---------------------------------------------------------------------------
# Background flattening and the threshold sweep — both restored from
# tools/calibration/solve_homography.py, which is still in this repo and
# which earned every number below on this rig in 2026-08.
#
# **The single fixed threshold above is not enough, and that is measured,
# not suspected.** The old solver's own comments record what the rig
# actually looks like during a solve: the plywood runs from ~29 to ~58 grey
# ACROSS ONE FRAME, because the projector does not light the table evenly
# and the camera views it at an angle, while a dot sits only ~25-50 grey
# levels above whatever happens to be under it. Those two ranges overlap.
# No single global threshold separates every dot from the board, and the
# 2026-08-12 rig run showed exactly that failure: 4 of 15 dots not found.
#
# A white top-hat subtracts everything structurally larger than the kernel,
# which removes the board's gradient and leaves the dots standing on a flat
# near-zero floor. After it, one threshold does work everywhere in frame.
# ---------------------------------------------------------------------------

# **The kernel must be LARGER than the biggest dot's diameter or it eats
# the dot** — a top-hat keeps what is smaller than the structuring element
# and discards what is larger. At M4's marker radius of 40 px the largest
# blob is ~80 px across, so this default clears it with room to spare.
# `core/dotcal.py` sizes it from the pattern it just drew and sends it on
# the `detect_dots` command rather than trusting this default, because core
# is the only thing that knows how big the dots are (I2).
DEFAULT_TOPHAT_PX = 101

# The sweep, from the old solver verbatim. Ranges high to low; every level
# is cheap because the top-hat runs once and only the threshold-and-contour
# step repeats.
DEFAULT_SWEEP_MAX = 200
DEFAULT_SWEEP_MIN = 8
DEFAULT_SWEEP_STEP = 4


class DotDetectionError(Exception):
    """The frame could not be examined at all — wrong shape, wrong dtype.
    **Not** raised for "found the wrong number of dots": how many were
    expected is the caller's business (doc section 4.7 puts `expect` on
    the command), and a detector that raised on a count would make a
    partial result unavailable to the log line that explains the failure.
    """


class Dot:
    """One detected blob: its centroid in camera pixels, plus the numbers
    the staff view's `dots` overlay and the log line both want.
    """

    __slots__ = ("x", "y", "area", "w", "h")

    def __init__(self, x: float, y: float, area: float,
                 w: float, h: float) -> None:
        self.x = x
        self.y = y
        self.area = area
        self.w = w
        self.h = h

    @property
    def point(self) -> Point:
        return (self.x, self.y)

    def as_list(self) -> List[float]:
        """Doc section 4.7's wire shape: `"points":[[cx,cy], ...]`."""
        return [self.x, self.y]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Dot ({self.x:.1f},{self.y:.1f}) area={self.area:.0f}>"


def to_grey(image):
    """BGR/BGRA/grey array in, uint8 grey out. Raises DotDetectionError on
    anything that is not an image."""
    import cv2      # noqa: WPS433 - local, same reason as geometry.fit
    import numpy as np    # noqa: WPS433

    arr = np.asarray(image)
    if arr.ndim == 3:
        if arr.shape[2] not in (3, 4):
            raise DotDetectionError(
                f"expected a BGR or BGRA frame, got {arr.shape[2]} channels")
        grey = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_BGR2GRAY)
    elif arr.ndim == 2:
        grey = arr
    else:
        raise DotDetectionError(
            f"expected a 2-D or 3-D image array, got {arr.ndim} dimensions")
    if grey.dtype != np.uint8:
        grey = np.clip(grey, 0, 255).astype(np.uint8)
    return grey


def flatten_background(grey, tophat_px: int = DEFAULT_TOPHAT_PX):
    """White top-hat: `grey` minus its morphological opening, which is
    `grey` minus everything structurally larger than `tophat_px`.

    What survives is small bright things — the dots — standing on a flat
    floor, with the projector's uneven illumination and the camera's
    oblique view of the plywood subtracted out. `tophat_px <= 0` returns
    the image untouched, which is the escape hatch for a frame that is
    already flat (every synthetic test image is).

    **Sizing it is the one way to get this wrong.** A kernel smaller than a
    dot removes the dot along with the background, and the symptom is the
    biggest dot vanishing while the small ones survive — the exact opposite
    of what an operator would expect from "the marker is too big".
    """
    import cv2      # noqa: WPS433
    if tophat_px <= 0:
        return grey
    size = int(tophat_px) | 1   # cv2 wants an odd kernel
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    return cv2.morphologyEx(grey, cv2.MORPH_TOPHAT, kernel)


def detect_dots(image,
                *,
                threshold: int = DEFAULT_THRESHOLD,
                tophat_px: int = 0,
                min_area: float = DEFAULT_MIN_AREA_PX,
                max_area: float = DEFAULT_MAX_AREA_PX,
                min_aspect: float = DEFAULT_MIN_ASPECT,
                touching_edge_ok: bool = False) -> List[Dot]:
    """Find projected white dots in a camera frame.

    `image` is a numpy array: BGR (H, W, 3) as it comes out of the frame
    ring, or already-grey (H, W). Returns camera-space `Dot`s, **sorted
    by area, largest first** — not by position. Sorting by position would
    be the beginning of an ordering assumption, and ordering is
    deliberately not this module's job (see the module docstring). Largest
    first is useful for a different reason: if the caller got more blobs
    than it expected, the extras are usually the small ones.

    `touching_edge_ok=False` drops any blob that touches the frame border.
    A dot cut by the edge has a centroid pulled inward by however much of
    it is missing, and that error is invisible — the blob still looks like
    a dot, the area still passes, and the fit absorbs a point that is
    several pixels off in a known direction. Dropping it costs one
    correspondence out of fifteen; keeping it biases the solve.

    **Centroids come from image moments, not from bounding-box centres.**
    A bounding box centre is the middle of the extremes, so a single
    bright speck attached to a dot's edge moves it by half the speck's
    reach. The moment centroid is intensity-weighted over the whole blob
    and moves by the speck's *share of the area*, which for a speck is
    almost nothing. Sub-pixel too, which is where the "under ~3 px RMS"
    in doc section 21's acceptance test has to come from.
    """
    grey = flatten_background(to_grey(image), tophat_px)
    return _blobs_at(grey, threshold, min_area=min_area, max_area=max_area,
                     min_aspect=min_aspect,
                     touching_edge_ok=touching_edge_ok)


def _blobs_at(grey, threshold: int, *, min_area: float, max_area: float,
              min_aspect: float, touching_edge_ok: bool) -> List[Dot]:
    """One threshold level against an already-grey, already-flattened
    image. Split out from `detect_dots` so `detect_best`'s sweep can run
    dozens of levels without repeating the top-hat, which is by far the
    expensive step."""
    import cv2      # noqa: WPS433 - local, same reason as geometry.fit

    height, width = grey.shape[:2]
    _ret, mask = cv2.threshold(grey, int(threshold), 255, cv2.THRESH_BINARY)

    # RETR_EXTERNAL: a dot is solid, so any hole inside one is a
    # compression artefact or a dead pixel and is not a second dot.
    # VERIFIED against the installed OpenCV (5.0.0): findContours returns
    # (contours, hierarchy) — two values, not the three the 3.x API
    # returned, which is the exact kind of remembered-API error doc
    # section 0 rule 3 exists for.
    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                            cv2.CHAIN_APPROX_SIMPLE)

    out: List[Dot] = []
    for contour in contours:
        m = cv2.moments(contour)
        area = float(m["m00"])
        if area <= 0.0:
            # A degenerate contour — a single pixel or a line. m00 is 0,
            # so the centroid division below would raise, and there is
            # nothing here worth recovering.
            continue
        if area < min_area or area > max_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if not touching_edge_ok and (x <= 0 or y <= 0
                                     or x + w >= width or y + h >= height):
            continue
        longer = float(max(w, h))
        shorter = float(min(w, h))
        if longer <= 0 or (shorter / longer) < min_aspect:
            continue
        out.append(Dot(x=m["m10"] / m["m00"], y=m["m01"] / m["m00"],
                       area=area, w=float(w), h=float(h)))

    out.sort(key=lambda d: d.area, reverse=True)
    return out


def detect_best(image, expect: int, *,
                tophat_px: int = DEFAULT_TOPHAT_PX,
                sweep_min: int = DEFAULT_SWEEP_MIN,
                sweep_max: int = DEFAULT_SWEEP_MAX,
                sweep_step: int = DEFAULT_SWEEP_STEP,
                min_area: float = DEFAULT_MIN_AREA_PX,
                max_area: float = DEFAULT_MAX_AREA_PX,
                min_aspect: float = DEFAULT_MIN_ASPECT,
                touching_edge_ok: bool = False) -> Tuple[List[Dot], int]:
    """Flatten the background once, then sweep the threshold and return the
    set of blobs from the most stable level that found `expect` of them,
    along with the level chosen.

    **Why a sweep rather than a number.** After the top-hat a dot's height
    above the floor is its local contrast against the plywood, and on this
    rig that was measured at 25-50 grey levels — which moves with the
    projector's field level, the camera's exposure, and where in the frame
    the dot is. Any constant picked here would be right for one rig state.
    The sweep asks the only question that has a stable answer: at which
    level do exactly the expected number of round blobs appear?

    **Stability decides between levels, not luck.** Several levels usually
    find `expect` blobs, and the old solver handed every such set to the
    homography and let the fit choose. Detection cannot fit anything (see
    the module docstring), so the tie is broken on a property detection
    can see: the LONGEST UNBROKEN RUN of levels agreeing on the count, with
    the middle of that run taken as the operating point. A run is evidence
    the answer is insensitive to the threshold; a lone level that happens
    to hit the right count — a reflection appearing just as a real dot
    drops out — is exactly what that outvotes.

    Falls back to the level whose count is closest to `expect` when no
    level hits it exactly, preferring more blobs over fewer on a tie: a
    spurious extra is dropped downstream by area or by the match gate,
    whereas a dot that was never detected cannot be recovered at all.
    """
    grey = flatten_background(to_grey(image), tophat_px)
    levels = list(range(int(sweep_max), int(sweep_min) - 1,
                        -abs(int(sweep_step)) or -1))
    found_by_level: List[Tuple[int, List[Dot]]] = []
    for level in levels:
        found_by_level.append(
            (level, _blobs_at(grey, level, min_area=min_area,
                              max_area=max_area, min_aspect=min_aspect,
                              touching_edge_ok=touching_edge_ok)))

    best_run: Optional[Tuple[int, int]] = None   # (length, start index)
    run_start: Optional[int] = None
    for i, (_level, found) in enumerate(found_by_level + [(0, [])]):
        hit = i < len(found_by_level) and len(found) == expect
        if hit and run_start is None:
            run_start = i
        elif not hit and run_start is not None:
            length = i - run_start
            if best_run is None or length > best_run[0]:
                best_run = (length, run_start)
            run_start = None

    if best_run is not None:
        length, start = best_run
        chosen = start + length // 2
        _log.info("dots: threshold %d found %d dots (stable over %d levels)",
                  found_by_level[chosen][0], expect, length)
        return found_by_level[chosen][1], found_by_level[chosen][0]

    if not found_by_level:
        return [], int(sweep_max)
    # Closest count wins; more beats fewer at equal distance.
    chosen = min(range(len(found_by_level)),
                 key=lambda i: (abs(len(found_by_level[i][1]) - expect),
                                -len(found_by_level[i][1])))
    level, found = found_by_level[chosen]
    _log.warning("dots: no threshold found exactly %d dots; best was %d at "
                 "level %d", expect, len(found), level)
    return found, level


def detect_points(image, **kwargs) -> List[List[float]]:
    """`detect_dots` reduced to doc section 4.7's wire shape —
    `[[cx, cy], ...]`. What `classifier/main.py` puts on the link.
    """
    return [d.as_list() for d in detect_dots(image, **kwargs)]


def best_n(dots: Sequence[Dot], n: int) -> List[Dot]:
    """The `n` largest dots, when more blobs were found than expected.

    Used only after a count mismatch has already been logged. It is a
    salvage path, not a normal one: taking the biggest `n` is right when
    the extras are specks the area filter did not quite catch, and wrong
    when the extras are a reflection larger than a dot. The caller decides
    whether salvaging is appropriate — `core/dotcal.py` only does it for
    the coarse pass, where a wrong answer is caught by the fine pass
    immediately afterwards.
    """
    return sorted(dots, key=lambda d: d.area, reverse=True)[:max(0, n)]


def summarise(dots: Sequence[Dot], expected: Optional[int] = None) -> str:
    """One line for the log and for the staff view's plain-language
    verdict. Doc section 12.1: no jargon in the operator-facing layer, and
    "found 3 of 4 dots" is the sentence an operator can act on.
    """
    if expected is None:
        return f"found {len(dots)} dots"
    if len(dots) == expected:
        return f"found all {expected} dots"
    if len(dots) < expected:
        return (f"found only {len(dots)} of {expected} dots — the projector "
                "may not be showing the pattern, or the camera exposure is "
                "too dark to see it")
    return (f"found {len(dots)} blobs where {expected} dots were expected — "
            "something reflective is in frame")
