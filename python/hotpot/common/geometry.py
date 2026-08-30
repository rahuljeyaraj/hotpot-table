"""common/geometry.py — the camera <-> stage homography (doc sections 5, 21
M4 build item 1).

Three jobs, deliberately split by what they depend on:

- fit() solves a homography from point correspondences. It is the only
  thing in this module that needs OpenCV, and `cv2` is imported *inside*
  the function for exactly the reason `core/scale.py` imports `pyserial`
  inside `_open_serial()`: everything else here has to stay importable and
  testable with nothing installed and no camera attached.
- apply() / apply_rect() / invert() are plain arithmetic on a 3x3. No
  cv2, no numpy. `core` runs these every time it derives a stage rect, and
  a 3x3 matrix-vector product is not worth a dependency or an array
  round-trip.
- rms_px() measures a fit against the points it was fitted from.

Read doc section 5.3's TRAP before using any of this to check anything.
Reprojecting a derived stage rect back through the same `H` returns the
camera rect it came from *regardless of whether H points the right way* —
it is the definition of an inverse, not evidence. There is no function in
this module that can verify the direction of a homography, and there is
deliberately no function added that pretends to. The only check that can
fail is a human looking at the projected outlines on the real trays
(doc section 12.6's Verify step). `rms_px()` is not that check either: it
says the fit is self-consistent with its own correspondences, which is a
different and much weaker claim.

Convention, fixed here and relied on everywhere else: a homography is a
list of 3 rows of 3 floats, row-major, applied to a column vector
`[x, y, 1]` and divided through by the resulting `w`. That is the same
convention `cv2.findHomography` returns and the same one doc section 8.5's
`H_cam_to_stage` stores, so nothing is transposed on the way to or from
disk.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple, Union

Point = Tuple[float, float]
Matrix = List[List[float]]
Rect = Tuple[float, float, float, float]

# Doc section 21's M4 acceptance test wants "RMS error reported, under ~3
# px", and the RANSAC inlier threshold is the same order of magnitude for
# the same reason: a correspondence more than a few pixels off the
# consensus is a mis-paired point rather than a noisy one. `fit()` accepts
# an override, but nothing currently passes one — the manual 4-corner flow
# (`GeometryStore.fit_from_corners`) always uses this default.
DEFAULT_RANSAC_REPROJ_PX = 3.0

# cv2's own default is 2000. Left explicit so a future "why is the fit
# different today" question has a number to look at rather than a library
# default that may move between versions.
DEFAULT_RANSAC_MAX_ITERS = 2000

# A homography needs 4 point correspondences. Fewer is not a bad fit, it
# is an unsolvable system, and it must fail loudly rather than return
# something shaped like an answer.
MIN_POINTS = 4


class GeometryError(Exception):
    """A fit that cannot be trusted: too few points, a degenerate
    configuration, or a singular matrix. Never raised for a merely *poor*
    fit — that is what `rms_px` is for, and judging it is the caller's job
    (and, per doc section 12.6, ultimately a human's).
    """


# ---------------------------------------------------------------------------
# Applying a homography — no cv2, no numpy
# ---------------------------------------------------------------------------

def apply(h: Sequence[Sequence[float]], point: Point) -> Point:
    """One point through `h`. Raises GeometryError if the point maps to
    infinity (w == 0), which is a real possibility for a badly conditioned
    matrix and must not come back as `inf` for someone to divide by later.
    """
    x, y = float(point[0]), float(point[1])
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    if w == 0.0 or not math.isfinite(w):
        raise GeometryError(
            f"point {point} maps to infinity through this homography")
    return ((h[0][0] * x + h[0][1] * y + h[0][2]) / w,
            (h[1][0] * x + h[1][1] * y + h[1][2]) / w)


def apply_all(h: Sequence[Sequence[float]],
              points: Iterable[Point]) -> List[Point]:
    return [apply(h, p) for p in points]


def apply_rect(h: Sequence[Sequence[float]], rect: Rect) -> Rect:
    """An axis-aligned rect through `h`, as the axis-aligned bounding box
    of its four transformed corners.

    A homography does not map a rectangle to a rectangle. It maps it to
    a general quadrilateral, and this returns the box around that quad, so
    the result is always a superset of the true projected shape. That is
    the right way round for the two things it is used for — the bin rect
    oF draws a plate on, and the cutout the light pass stamps white (doc
    section 13.2) — because a cutout patch that is slightly too big spills
    white onto the table around a tray, while one that is slightly too
    small leaves a dark crescent *on the food*, which starves the
    classifier (I9).

    On a near-vertical camera (I10) the quad is very nearly a rectangle
    anyway and the difference is a pixel or two. On a badly angled one it
    is not, and the outline will visibly not sit on the tray — which is
    doc section 12.6's Verify step doing its job, not this function
    hiding a problem.
    """
    x, y, w, h_ = (float(v) for v in rect)
    corners = [(x, y), (x + w, y), (x + w, y + h_), (x, y + h_)]
    mapped = apply_all(h, corners)
    xs = [p[0] for p in mapped]
    ys = [p[1] for p in mapped]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def invert(h: Sequence[Sequence[float]]) -> Matrix:
    """The inverse homography, by cofactors. Raises GeometryError on a
    singular matrix rather than returning something with `inf` in it.

    Normalised so `[2][2] == 1` when it can be, which is not cosmetic: it
    keeps the numbers in the same range as the forward matrix, so a human
    reading `state/homography.json` beside a debug print of the inverse
    can compare them at a glance.
    """
    a, b, c = (float(v) for v in h[0])
    d, e, f = (float(v) for v in h[1])
    g, i, j = (float(v) for v in h[2])

    det = a * (e * j - f * i) - b * (d * j - f * g) + c * (d * i - e * g)
    if det == 0.0 or not math.isfinite(det):
        raise GeometryError("homography is singular and cannot be inverted")

    inv = [
        [(e * j - f * i) / det, (c * i - b * j) / det, (b * f - c * e) / det],
        [(f * g - d * j) / det, (a * j - c * g) / det, (c * d - a * f) / det],
        [(d * i - e * g) / det, (b * g - a * i) / det, (a * e - b * d) / det],
    ]
    scale = inv[2][2]
    if scale != 0.0 and math.isfinite(scale):
        inv = [[v / scale for v in row] for row in inv]
    return inv


# ---------------------------------------------------------------------------
# Measuring a fit
# ---------------------------------------------------------------------------

def reproj_errors_px(h: Sequence[Sequence[float]],
                     src: Sequence[Point],
                     dst: Sequence[Point]) -> List[float]:
    """Per-correspondence distance, in destination pixels, between
    `apply(h, src[i])` and `dst[i]`.
    """
    if len(src) != len(dst):
        raise GeometryError(
            f"{len(src)} source points against {len(dst)} destination points")
    out = []
    for s, d in zip(src, dst):
        px, py = apply(h, s)
        out.append(math.hypot(px - float(d[0]), py - float(d[1])))
    return out


def rms_px(h: Sequence[Sequence[float]],
           src: Sequence[Point],
           dst: Sequence[Point]) -> float:
    """Root-mean-square reprojection error, the number doc section 8.5
    stores as `rms_px` and doc section 12.6 puts on the screen.

    This is a self-consistency number, not a correctness one. A
    homography fitted to a set of points that were all mis-paired the same
    way fits them beautifully and lands the rects in the wrong place. Doc
    section 5.3's TRAP, restated where the tempting number lives.
    """
    errs = reproj_errors_px(h, src, dst)
    if not errs:
        raise GeometryError("no correspondences to measure")
    return math.sqrt(sum(e * e for e in errs) / len(errs))


# ---------------------------------------------------------------------------
# Warping a whole frame — the table crop
# ---------------------------------------------------------------------------

def warp_frame_to_stage(frame, h: Sequence[Sequence[float]],
                        size: Tuple[int, int]):
    """The camera frame, perspective-corrected onto the same canvas the
    projector draws into.

    Once `h` is the corner-calibrated `H_cam_to_stage`, the result is a
    frame where pixel `(x, y)` sits at the table position the projector's
    own pixel `(x, y)` lights — camera space and stage space made the same
    canvas, not just related by a matrix. This is "the table crop": bin
    grids, MediaPipe, and the classifier's crop all work in this shared
    canvas from here on and never touch `h` again.

    `cv2` is imported inside the function, not at module scope, for the
    same reason `fit()`'s import is local below: `classifier` and `core`
    both import this module, and neither needs OpenCV installed just to
    load a saved homography or apply a point to it.

    `size` is `(width, height)` — cv2's own `dsize` convention for
    `warpPerspective`, and NOT the `(rows, cols)` a numpy `.shape` would
    give; passing a frame's own shape here transposes the result.
    """
    import numpy as np  # noqa: WPS433 - see module docstring
    import cv2          # noqa: WPS433

    matrix = np.array([[float(v) for v in row] for row in h], dtype=np.float64)
    return cv2.warpPerspective(frame, matrix, (int(size[0]), int(size[1])))


# ---------------------------------------------------------------------------
# Fitting — the other thing that needs OpenCV
# ---------------------------------------------------------------------------

class Fit:
    """A solved homography and everything doc section 8.5 wants recorded
    beside it.

    `inliers` is the RANSAC mask as a list of bools, one per input
    correspondence, and `rms_px` is measured over the inliers only —
    over all points it would include the outliers RANSAC just decided to
    ignore, which is the number moving for a reason that has nothing to do
    with the fit's quality.
    """

    def __init__(self, h: Matrix, rms_px: float, inliers: List[bool],
                 n_points: int) -> None:
        self.h = h
        self.rms_px = rms_px
        self.inliers = inliers
        self.n_points = n_points

    @property
    def n_inliers(self) -> int:
        return sum(1 for v in self.inliers if v)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<Fit rms={self.rms_px:.2f}px "
                f"inliers={self.n_inliers}/{self.n_points}>")


def fit(src: Sequence[Point], dst: Sequence[Point], *,
        ransac_reproj_px: float = DEFAULT_RANSAC_REPROJ_PX,
        max_iters: int = DEFAULT_RANSAC_MAX_ITERS) -> Fit:
    """Fit `src -> dst` with `cv2.findHomography` and RANSAC.

    RANSAC rather than the plain least-squares method (`method=0`) because
    the input is a list of detected dot centroids paired with expected
    ones, and the failure mode that actually happens on a rig is one dot
    mis-paired, not all of them slightly noisy: a reflection, a
    highlight on a tray rim, or a dot that straddled a cutout edge. Least
    squares spreads one bad pair across every point in the fit and quietly
    degrades all eight rects. RANSAC drops it and says how many it
    dropped.

    Raises GeometryError rather than returning `None` when the solve
    fails. `cv2.findHomography` returns `(None, None)` for a degenerate
    configuration — four collinear points, say — and a caller that treats
    that as "no homography today" would write `null` into
    `state/homography.json` and boot UNCALIBRATED forever with no
    explanation.

    VERIFIED against the installed OpenCV (5.0.0), not remembered:
    `findHomography(srcPoints, dstPoints[, method[, ransacReprojThreshold[,
    mask[, maxIters[, confidence]]]]]) -> retval, mask`, and `cv2.RANSAC`
    is 8. Both checked before this was written, per doc section 0 rule 3.
    """
    if len(src) != len(dst):
        raise GeometryError(
            f"{len(src)} source points against {len(dst)} destination points")
    if len(src) < MIN_POINTS:
        raise GeometryError(
            f"a homography needs at least {MIN_POINTS} point pairs, got {len(src)}")

    # Imported here, not at module scope: everything above this line works
    # with no OpenCV installed, and `core/geometry_store.py` leans on that
    # to load and apply a saved homography on a machine that has never had
    # a camera attached.
    import numpy as np  # noqa: WPS433 - deliberate local import, see above
    import cv2          # noqa: WPS433

    src_arr = np.array([[float(p[0]), float(p[1])] for p in src],
                       dtype=np.float32).reshape(-1, 1, 2)
    dst_arr = np.array([[float(p[0]), float(p[1])] for p in dst],
                       dtype=np.float32).reshape(-1, 1, 2)

    h_arr, mask = cv2.findHomography(src_arr, dst_arr, cv2.RANSAC,
                                     float(ransac_reproj_px),
                                     maxIters=int(max_iters))
    if h_arr is None:
        raise GeometryError(
            "findHomography found no solution — the points are degenerate "
            "(collinear, coincident, or too few distinct positions)")

    h: Matrix = [[float(v) for v in row] for row in h_arr]
    if mask is None:
        inliers = [True] * len(src)
    else:
        inliers = [bool(v) for v in mask.ravel().tolist()]

    in_src = [p for p, ok in zip(src, inliers) if ok]
    in_dst = [p for p, ok in zip(dst, inliers) if ok]
    if len(in_src) < MIN_POINTS:
        raise GeometryError(
            f"RANSAC kept only {len(in_src)} of {len(src)} points — that is "
            "not enough to trust a homography built from them")

    return Fit(h=h, rms_px=rms_px(h, in_src, in_dst), inliers=inliers,
               n_points=len(src))


# ---------------------------------------------------------------------------
# Point ordering — the half of calibration that is not arithmetic
# ---------------------------------------------------------------------------

def order_quad(points: Sequence[Point]) -> List[Point]:
    """Four detected points, ordered top-left, top-right, bottom-right,
    bottom-left.

    NOT used by dot calibration any more, and do not put it back — use
    `order_quad_marker_first`. The assumption below about the camera being
    roughly the right way up is false on this rig (measured at 180 degrees,
    commit b847c0f), and it fails silently: four points always fit a
    homography exactly, so the flipped pairing reports zero error. This
    remains only for callers that genuinely know their quad is upright.

    It is the one step in a solve that has no numerical safety net: pair
    them wrongly and everything downstream is self-consistent and completely
    wrong (doc section 5.3's TRAP, arriving by the front door).

    The method is the standard sum/difference one — the top-left corner has
    the smallest `x + y`, the bottom-right the largest; the top-right has
    the largest `x - y`, the bottom-left the smallest. It assumes the
    camera is not rotated more than about 45 degrees relative to the
    table, which is a real assumption and is why it is only ever used on
    four widely-separated dots rather than on a whole grid. I10 already
    requires a near-vertical camera; this additionally requires it to be
    roughly the right way up, which is a mounting fact a human can see.

    Raises GeometryError if the four points do not resolve to four
    distinct corners — that means two dots landed in the same quadrant,
    which is a detection failure, not something to average away.
    """
    if len(points) != 4:
        raise GeometryError(f"order_quad needs exactly 4 points, got {len(points)}")
    pts = [(float(p[0]), float(p[1])) for p in points]
    by_sum = sorted(pts, key=lambda p: p[0] + p[1])
    by_diff = sorted(pts, key=lambda p: p[0] - p[1])
    tl, br = by_sum[0], by_sum[-1]
    bl, tr = by_diff[0], by_diff[-1]
    out = [tl, tr, br, bl]
    if len({(round(p[0], 6), round(p[1], 6)) for p in out}) != 4:
        raise GeometryError(
            "the four points do not form four distinct corners — two dots "
            "landed on the same side, so the pass cannot be ordered")
    return out


# How much bigger the marker dot has to be than the median of the others
# before it is believed. Nominal is the area ratio of the two radii the
# pattern actually draws — at M4's 40 px marker against 24 px corners that
# is (40/24)^2 = 2.8. 1.6 leaves room for defocus, an oblique view, and a
# marker partly on a tray, while still being far outside the +-8% spread
# the old rig measured across eight nominally identical dots.
DEFAULT_MIN_MARKER_RATIO = 1.6


def identify_marker(areas: Sequence[float], *,
                    min_ratio: float = DEFAULT_MIN_MARKER_RATIO) -> int:
    """Index of the deliberately-oversized dot among a detected set.

    Compared against the median of the others, not the mean, so one fat
    or thin blob cannot drag the baseline. Raises GeometryError rather than
    returning a best guess: the marker is what fixes orientation, and a
    guessed orientation is the failure this whole mechanism exists to
    prevent (see `order_quad_marker_first`).
    """
    if len(areas) < 2:
        raise GeometryError(
            f"identify_marker needs at least 2 areas, got {len(areas)}")
    values = [float(a) for a in areas]
    marker = max(range(len(values)), key=lambda i: values[i])
    others = sorted(values[:marker] + values[marker + 1:])
    mid = len(others) // 2
    median_other = (others[mid] if len(others) % 2
                    else 0.5 * (others[mid - 1] + others[mid]))
    if median_other <= 0:
        raise GeometryError("the non-marker dots have no area")
    ratio = values[marker] / median_other
    if ratio < min_ratio:
        raise GeometryError(
            f"no dot stands out as the orientation marker: largest is "
            f"{values[marker]:.0f} px2 against a median of "
            f"{median_other:.0f} px2, a ratio of {ratio:.2f} and under the "
            f"{min_ratio} needed. Orientation cannot be resolved — check "
            f"that the marker dot is not clipped by a tray or blooming into "
            f"its neighbours.")
    return marker


def order_quad_marker_first(points: Sequence[Point],
                            marker_index: int) -> List[Point]:
    """Four points in cyclic order about their centroid, starting at the
    marker — the correspondence for a pattern whose first drawn corner is
    the oversized one.

    This replaces `order_quad` for calibration and the difference is not
    cosmetic. `order_quad` labels corners by their position in the camera
    image, which silently assumes the camera is mounted roughly the same way
    up as the projector. This rig's camera was measured at 180 degrees
    (commit b847c0f, 2026-08-08), and at 180 degrees that assumption does
    not merely degrade — it pairs every corner with the opposite one, and
    because four points always fit a homography exactly, the wrong answer
    comes back with ZERO error and no warning. The old solver hit this and
    reported a confident 0 degrees for a camera at 180.

    Two independent facts make this version immune:

    - Cyclic order comes from the angle about the centroid, which is
      rotation-invariant, so no mounting angle can reorder the ring. A
      camera looking down at a table cannot mirror the view, so the ring
      runs the same way round in both spaces and only the starting point is
      unknown.
    - The marker fixes the starting point, geometrically rather than by
      picking whichever hypothesis fits best. Error cannot arbitrate here
      and must never be asked to.

    `points` must be exactly four, and `marker_index` indexes into them as
    given, not into the returned order.
    """
    if len(points) != 4:
        raise GeometryError(
            f"order_quad_marker_first needs exactly 4 points, got {len(points)}")
    if not 0 <= marker_index < 4:
        raise GeometryError(f"marker_index {marker_index} is not one of the 4")
    pts = [(float(p[0]), float(p[1])) for p in points]
    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0
    # atan2 on a y-down axis increases clockwise on screen, which is the
    # direction the stage corners are conventionally listed in elsewhere in
    # this codebase, so the two rings run the same way round with no
    # special-casing.
    by_angle = sorted(range(4), key=lambda i: math.atan2(pts[i][1] - cy,
                                                         pts[i][0] - cx))
    if len({(round(p[0], 6), round(p[1], 6)) for p in pts}) != 4:
        raise GeometryError(
            "the four points are not four distinct corners — two dots landed "
            "on top of each other, so the pass cannot be ordered")
    start = by_angle.index(marker_index)
    rotated = by_angle[start:] + by_angle[:start]
    return [pts[i] for i in rotated]


def match_nearest(expected: Sequence[Point], found: Sequence[Point], *,
                  max_distance_px: Union[float, Sequence[float]]
                  ) -> List[Optional[int]]:
    """Pair each `expected` point with the closest unused point in `found`,
    or None if nothing is within its gate.

    Used for the second calibration pass: the coarse homography from pass
    one projects every expected stage dot into camera space, and each
    detected centroid is then matched to the projection it is nearest to.
    That is what removes the "sort the grid row by row" assumption, which
    breaks on a rotated camera and — worse — breaks *silently*, producing a
    perfect-looking fit with the rows shifted by one.

    Greedy in order of increasing distance, not in list order, so the
    unambiguous pairs are taken first and an ambiguous one cannot steal a
    point that another expected dot matched far better. One-to-one by
    construction: a detected point is consumed by the first expected point
    that claims it.

    `max_distance_px` is one number shared by every `expected` point, or a
    per-`expected`-point sequence the same length — RIG_FEEDBACK item 11
    (2026-08-13): `tracker/tracking.py`'s track gate has to widen with how
    long a given track has gone unseen, and every track can be at a
    different point in that clock on the same frame, so one shared number
    cannot express it. A single float keeps every existing caller (the
    calibration pass above) unchanged.
    """
    if isinstance(max_distance_px, (int, float)):
        gates: Sequence[float] = [float(max_distance_px)] * len(expected)
    else:
        gates = max_distance_px
        if len(gates) != len(expected):
            raise ValueError(
                "max_distance_px must be a single number or one per "
                "expected point")

    pairs = []
    for ei, e in enumerate(expected):
        for fi, f in enumerate(found):
            d = math.hypot(float(f[0]) - float(e[0]), float(f[1]) - float(e[1]))
            if d <= gates[ei]:
                pairs.append((d, ei, fi))
    pairs.sort()

    out: List[Optional[int]] = [None] * len(expected)
    used_found = set()
    for _d, ei, fi in pairs:
        if out[ei] is not None or fi in used_found:
            continue
        out[ei] = fi
        used_found.add(fi)
    return out
