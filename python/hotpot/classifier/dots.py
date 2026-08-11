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

# Bright-on-dark, and high: with the field inverted to black, a projected
# white dot is near saturation and everything else is near zero, so there
# is a very wide valley to put this in. 200 sits in it. Lower values start
# picking up the grey the projector leaks onto a "black" field, which is
# never truly black on any real projector.
DEFAULT_THRESHOLD = 200

# A dot is a disc. A reflection off a tray rim is a sliver, and a light
# leak along a table edge is a long thin band; both can have a plausible
# area. Requiring the blob to be roughly as tall as it is wide throws
# those out on a shape argument rather than a size one. 0.45 tolerates a
# genuine dot squashed by an oblique camera (I10 allows down to ~70
# degrees of elevation, which foreshortens a circle to about 0.94 — so
# this is loose by a wide margin and is only catching slivers).
DEFAULT_MIN_ASPECT = 0.45


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


def detect_dots(image,
                *,
                threshold: int = DEFAULT_THRESHOLD,
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
