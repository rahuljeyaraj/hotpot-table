"""core/dotcal.py — the dot calibration wizard (doc section 21, M4 build
item 3; doc sections 5.2, 8.5, 12.6, 24.1).

The seam where a projected pattern becomes a saved homography, the same
way `core/calibrator.py` is the seam where a capture window becomes a
saved load-cell calibration. It drives the sequence and nothing else:

    1. tell oF to draw a known dot pattern (`overlay.kind = "calibrating"`)
    2. tell the classifier to detect dots      (doc section 4.7)
    3. pair what came back with what was drawn (`common/geometry`)
    4. fit H, measure rms                      (`common/geometry.fit`)
    5. hand it to `GeometryStore` to write     (doc section 8.5)

It owns no state file. `GeometryStore` is the only writer of
`state/homography.json`.

================================================================
THE DOT PATTERN — doc section 24.1's open decision, decided here
================================================================
Doc section 24.1 left "count, spacing, and whether to run two passes at
different densities" open, to be decided "at M4 with the real camera field
of view in front of you." **There is no camera and no rig in front of this
implementation**, so what follows is a reasoned default derived from the
table's own geometry rather than from an observed field of view. It is
flagged in CLAUDE.md as needing a sanity check on real hardware. The
reasoning, so it can be argued with rather than merely replaced:

**Two passes, not one.** Pass 1 draws four large dots at the corners of
the usable area; pass 2 draws a 5x3 grid over the same rectangle. The
first pass exists purely to order the second. Ordering is the one step in
the whole solve with no numerical safety net — pair the dots wrongly and
every downstream number is self-consistent and completely wrong (doc
section 5.3's TRAP arriving by the front door). The single-pass
alternative is to sort the detected grid row-major by y and then x, which
works right up until the camera is mounted a few degrees off square, at
which point a row's rightmost dot outranks the next row's leftmost, the
grid pairs off by one, and the fit still looks excellent. Four
widely-separated corners can be ordered with no such assumption, and the
coarse homography they give is then good enough to project each expected
grid position into camera space and match by nearest neighbour
(`geometry.match_nearest`). The second pass therefore carries no ordering
assumption at all.

**The first corner is drawn oversized, and the whole solve rests on it.**
Ordering the four corners by their position in the camera image — which
is what `geometry.order_quad` does and what this module used to call —
assumes the camera is mounted roughly the same way up as the projector.
**That assumption is false on this rig: the camera was measured at 180
degrees** (commit b847c0f, 2026-08-08, which added a marker dot to the
then-current solver for exactly this reason). At 180 degrees every corner
pairs with the one opposite it, and because four points always fit a
homography exactly, the result is a completely inverted calibration
reported with ZERO error. Nothing downstream can catch it — the fine pass
inherits the flip and matches happily, the RMS stays beautiful, and only
the human Verify step ever notices. So the marker is back:
`geometry.identify_marker` finds it by area and
`geometry.order_quad_marker_first` takes the cyclic order from angles
about the centroid, which no mounting angle can disturb. Orientation is
decided geometrically, never by which hypothesis fits best.

**The dots avoid the bin cutouts, and this is the constraint that
actually shapes the pattern.** A bin is a hole in the plywood with a tray
in it, and a tray's surface is not the table's plane. A dot landing on a
tray is displaced by `height / tan(elevation)` — at I10's own worst
allowed angle of 70 degrees, a tray sitting 40 mm below the table top
moves the dot 15 mm, about 19 px, which is six times the whole error
budget doc section 21 allows for the solve. So the rows sit in the three
horizontal bands that contain no bin by construction:

    y = 85 mm    the far edge margin      (bins start at 177 mm)
    y = 457 mm   the row gap              (442..472 mm once the 10 mm
                                           cutout margin is counted)
    y = 830 mm   the near edge margin     (bins end at 747 mm)

Columns need no such care: all three bands are bin-free across the whole
width. They are spread evenly from 130 mm to 1394 mm, which keeps every
dot well onto the plywood and away from the edge of the projected image,
where a projector's own geometry is least trustworthy.

**Five columns by three rows = 15 points.** A homography has 8 degrees of
freedom and needs 4 correspondences; 15 is comfortably over-determined,
which is what lets RANSAC drop a bad one and still have plenty. Denser
grids buy very little for a planar fit and cost real robustness: dots too
close together merge under projector defocus, and a merged pair comes
back as one blob in the wrong place.

**The middle row is the tight one and is the thing to check on the rig.**
Its band is only 30 mm tall (442..472 mm). At the chosen 13 px radius the
dot spans about 22 mm of it, leaving ~4 mm each side. If the projector's
alignment is off by more than that, the middle row's dots will clip the
tray edges and should be dropped — `calibration.grid_rows` is config
(doc section 8.6) so going to two rows is an edit, not a rebuild.

**Radii: 13 px for the grid, 24 px for the corners, 40 px for the marker
corner.** The corner dots sit in the wide margins where there is room, and
being large makes the coarse pass robust — it is the pass that must not
fail. 13 px is ~10 mm on the table, which is what fits the middle band.
The marker's 40 px against 24 px is an area ratio of 2.8, comfortably over
the 1.6 `geometry.identify_marker` insists on and close to the 2.25 the
old solver used and confirmed on this rig.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hotpot.common import geometry
from hotpot.core import geometry_store as gstore

_log = logging.getLogger("hotpot.dotcal")

Point = Tuple[float, float]

# ---------------------------------------------------------------------------
# Pattern defaults — every one of these is a `calibration.*` config key
# (doc section 8.6). They are here as constants only so this module is
# usable without a config file, the same way core/main.py's port defaults
# work.
# ---------------------------------------------------------------------------

GRID_COLS = 5
GRID_ROWS = 3
# Inset from the stage edge, in stage pixels. 164 px is ~130 mm and 100 px
# is ~85 mm — see the module docstring for where those come from.
GRID_INSET_PX = (164.0, 100.0)
DOT_RADIUS_PX = 13.0
CORNER_DOT_RADIUS_PX = 24.0

# The first corner, drawn oversized so orientation is a geometric fact
# rather than a guess. See the module docstring — this is what stops a
# 180-degree camera producing a perfectly-fitting inverted calibration.
MARKER_DOT_RADIUS_PX = 40.0

# Top-hat kernel as a multiple of the biggest dot's diameter. The kernel
# must exceed the dot or the background flattening removes the dot with the
# background — see `classifier/dots.flatten_background`.
TOPHAT_SCALE = 1.25

# Camera frames averaged into the one the dots are found in. The old
# solver used 40 and measured why: dot-over-board contrast is the same
# order as this sensor's frame-to-frame noise, so averaging "is what makes
# the outer dots separable at all". At doc section 8.6's 30 fps that is
# ~1.3 s per pass, which is time an operator is already waiting through.
AVERAGE_FRAMES = 40

# How long the pattern is up before the classifier is asked to look. The
# projector has a frame to draw it and the camera has a frame to expose
# it, and neither is instantaneous: at doc section 8.6's 30 fps capture
# and a 60 Hz state stream, 0.6 s is ~18 camera frames of margin. It is a
# guess with a rationale, not a measurement — see CLAUDE.md.
SETTLE_S = 0.6

# How long to wait for the classifier's `dots` reply before giving up.
# Generous: the classifier may be reconnecting (doc section 20.2's ladder
# tops out at 10 s), and a wizard that failed at 2 s would report "the
# classifier is not answering" for something that was about to work.
REPLY_TIMEOUT_S = 12.0

# The nearest-neighbour gate for pass 2, in camera pixels. A detected dot
# further than this from where the coarse homography said it would be is
# not that dot. Wide, because the coarse fit is exactly that — four
# corners, no distortion model — and its error in the middle of the table
# is easily tens of pixels.
MATCH_GATE_PX = 120.0

# The fine pass's ROI margin, in camera pixels — CLAUDE.md's M4i fix for
# the room lamp that fragments into several 13px-dot-sized blobs once the
# field inverts to black. **Tied to MATCH_GATE_PX, not picked
# independently**: a real dot can legitimately land up to the match gate
# away from where the coarse fit expected it, so a tighter ROI would crop
# away a dot the matcher was still willing to accept. Anything outside the
# gate could never have matched anyway, so cropping it off costs nothing.
ROI_MARGIN_PX = MATCH_GATE_PX

# Doc section 21's M4 acceptance test: "RMS error reported, under ~3 px."
RMS_WARN_PX = 3.0


class DotCalError(Exception):
    """A calibration that could not be completed, with a sentence for the
    operator. Doc section 12.1: plain language, no jargon — "the projector
    may not be showing the pattern", not "n_points < MIN_POINTS".
    """


# ---------------------------------------------------------------------------
# The pattern
# ---------------------------------------------------------------------------

def grid_points(cols: int = GRID_COLS, rows: int = GRID_ROWS,
                inset: Sequence[float] = GRID_INSET_PX,
                stage_size: Sequence[int] = gstore.STAGE_SIZE) -> List[Point]:
    """The fine pass's dot centres, in **stage** coordinates, row-major
    from the far edge inward — the same order `TableGeometry.h` numbers
    the bins in, so a human reading a log line can follow it.

    Evenly spaced from `inset` to `stage - inset` in both axes. A single
    row or column lands on the midpoint rather than the near edge, which
    is the only sensible reading of "evenly spaced" for one of them.
    """
    if cols < 2 or rows < 1:
        raise DotCalError("a dot grid needs at least 2 columns and 1 row")
    ix, iy = float(inset[0]), float(inset[1])
    sw, sh = float(stage_size[0]), float(stage_size[1])
    xs = [ix + (sw - 2 * ix) * i / (cols - 1) for i in range(cols)]
    if rows == 1:
        ys = [sh / 2.0]
    else:
        ys = [iy + (sh - 2 * iy) * j / (rows - 1) for j in range(rows)]
    return [(x, y) for y in ys for x in xs]


def corner_points(inset: Sequence[float] = GRID_INSET_PX,
                  stage_size: Sequence[int] = gstore.STAGE_SIZE) -> List[Point]:
    """The coarse pass's four dots, ordered **top-left, top-right,
    bottom-right, bottom-left** — the same order `geometry.order_quad`
    returns, so the pairing is positional and there is no lookup to get
    backwards.
    """
    ix, iy = float(inset[0]), float(inset[1])
    sw, sh = float(stage_size[0]), float(stage_size[1])
    return [(ix, iy), (sw - ix, iy), (sw - ix, sh - iy), (ix, sh - iy)]


def overlay_dots(points: Sequence[Point], radius: float,
                 first_radius: Optional[float] = None) -> List[List[float]]:
    """Doc section 4.3's overlay payload: `[[x, y, r], ...]` in stage
    space.

    `first_radius`, when given, applies to point 0 only — the orientation
    marker. Per-dot radius is already how the payload is shaped and how
    `UiLayer::drawCalibrationDots` consumes it, so the marker needs no wire
    change and no second message.

    **Core sends the pattern; oF does not know it.** I2 says oF "computes
    nothing it could be told", and this is the sharpest case of that
    rule in the whole system: if oF held the pattern and core assumed it,
    the two could disagree by one edit and the homography would be solved
    against dots that were never where core thought they were — with a
    beautiful RMS, because the fit only ever sees core's copy.
    """
    return [[round(float(x), 2), round(float(y), 2),
             float(first_radius if (i == 0 and first_radius is not None)
                   else radius)]
            for i, (x, y) in enumerate(points)]


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

def pad_rect(rect: Sequence[float], margin_px: float) -> List[float]:
    """`rect` grown by `margin_px` on every side.

    Not clamped to the camera frame here — a negative `x`/`y` is fine, the
    classifier's `crop_rect` clamps to whatever frame it actually has (see
    `classifier/main.py`), and this module has no camera size to clamp
    against in the general case (`camera_size` is optional and often
    unset in tests). Clamping twice would just be two places that could
    disagree about where the edge is.
    """
    x, y, w, h = (float(v) for v in rect)
    return [x - margin_px, y - margin_px,
            max(0.0, w + 2.0 * margin_px), max(0.0, h + 2.0 * margin_px)]


class DotCalResult:
    def __init__(self, h: List[List[float]], rms_px: float, n_points: int,
                 n_inliers: int, message: str, good: bool) -> None:
        self.h = h
        self.rms_px = rms_px
        self.n_points = n_points
        self.n_inliers = n_inliers
        self.message = message
        self.good = good


class DotCalibrator:
    """Runs the two-pass solve. Blocking, and meant to be called from a
    worker thread — `core/main.py` runs it off the tablet's own WebSocket
    thread, the same place `Calibrator`'s 2 s capture windows already
    block (doc section 12.4's wizard is a thing the operator is watching
    and waiting for).

    `show_dots` is how the pattern reaches oF: core hands in a callback
    that sets the overlay on the next `state` broadcast. `ask_dots` is how
    the request reaches the classifier. Both are callables rather than
    objects so this module knows nothing about the wire, the FSM, or the
    60 Hz loop — which is what makes the whole two-pass sequence testable
    with no oF, no classifier and no camera.
    """

    def __init__(self, store: gstore.GeometryStore, *,
                 show_dots: Callable[[Optional[List[List[float]]]], None],
                 ask_dots: Callable[[int, float, int, int,
                                    Optional[Sequence[float]]],
                                   Dict[str, Any]],
                 cfg: Optional[Dict[str, Any]] = None,
                 settle_s: float = SETTLE_S,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store
        self._show = show_dots
        self._ask = ask_dots
        self._sleep = sleep
        self.settle_s = settle_s
        cfg = cfg or {}
        self.cols = int(cfg.get("grid_cols", GRID_COLS))
        self.rows = int(cfg.get("grid_rows", GRID_ROWS))
        self.inset = tuple(cfg.get("grid_inset_px", GRID_INSET_PX))
        self.dot_radius = float(cfg.get("dot_radius_px", DOT_RADIUS_PX))
        self.corner_radius = float(cfg.get("corner_dot_radius_px",
                                           CORNER_DOT_RADIUS_PX))
        self.marker_radius = float(cfg.get("marker_dot_radius_px",
                                           MARKER_DOT_RADIUS_PX))
        self.min_marker_ratio = float(cfg.get("min_marker_ratio",
                                              geometry.DEFAULT_MIN_MARKER_RATIO))
        # Multiplier on the biggest dot's DIAMETER for the top-hat kernel.
        # 1.25 leaves a quarter of a dot's width of margin, enough for
        # projector defocus spreading the disc without shrinking the kernel
        # so far that the board's own gradient starts surviving it.
        self.tophat_scale = float(cfg.get("tophat_scale", TOPHAT_SCALE))
        self.average_frames = int(cfg.get("average_frames", AVERAGE_FRAMES))
        self.min_area = float(cfg.get("min_dot_area_px", 40.0))
        self.max_area = float(cfg.get("max_dot_area_px", 20000.0))
        self.match_gate_px = float(cfg.get("match_gate_px", MATCH_GATE_PX))
        self.roi_margin_px = float(cfg.get("roi_margin_px", ROI_MARGIN_PX))
        self.ransac_reproj_px = float(cfg.get("ransac_reproj_px",
                                              geometry.DEFAULT_RANSAC_REPROJ_PX))
        self.rms_warn_px = float(cfg.get("rms_warn_px", RMS_WARN_PX))

        # One at a time, for the same reason `Calibrator` has `_busy`: two
        # overlapping solves would fight over the overlay and each would
        # detect the other's pattern.
        self._busy = threading.Lock()

    # -- the two passes ----------------------------------------------------

    def run(self, *, keystone_fingerprint: Optional[str] = None,
            camera_size: Optional[Tuple[int, int]] = None,
            save: bool = True) -> DotCalResult:
        if not self._busy.acquire(blocking=False):
            raise DotCalError("a calibration is already running")
        try:
            return self._run(keystone_fingerprint, camera_size, save)
        finally:
            # The overlay comes down whatever happened. A failed solve that
            # left the table black with white dots on it would look exactly
            # like a crashed renderer, and the operator's next move would be
            # to restart something.
            self._show(None)
            self._busy.release()

    def _run(self, keystone_fingerprint, camera_size, save) -> DotCalResult:
        coarse_stage = corner_points(self.inset, self.store.stage_size)
        coarse_cam, coarse_areas = self._pass(
            coarse_stage, self.corner_radius, "coarse", self.marker_radius)
        if len(coarse_cam) < 4:
            raise DotCalError(
                f"the first pass found only {len(coarse_cam)} of 4 corner "
                "dots — check that the table is showing the pattern and the "
                "camera can see the whole table")
        if len(coarse_cam) > 4:
            # Salvage, and only here: a wrong choice is caught by the fine
            # pass a second later, which is not true anywhere else in this
            # sequence.
            #
            # **Chosen by measured area, not by position in the reply.**
            # `classifier/dots.py` does sort largest-first, but taking the
            # first four would make this the only place in the module that
            # depends on that, and it is the place that can least afford to:
            # trimming the marker off the end costs the orientation, which
            # then cannot be recovered from anything else in the frame.
            _log.warning("dotcal: coarse pass found %d blobs, taking the 4 "
                         "largest by area", len(coarse_cam))
            keep = sorted(range(len(coarse_cam)),
                          key=lambda i: coarse_areas[i], reverse=True)[:4]
            coarse_cam = [coarse_cam[i] for i in keep]
            coarse_areas = [coarse_areas[i] for i in keep]

        # Orientation, geometrically. A GeometryError here is a real refusal
        # to guess, not a hiccup — see the module docstring on the 180-degree
        # camera — so it becomes an operator-facing message rather than a
        # traceback, and the solve stops.
        try:
            marker = geometry.identify_marker(
                coarse_areas, min_ratio=self.min_marker_ratio)
            ordered = geometry.order_quad_marker_first(coarse_cam, marker)
        except geometry.GeometryError as e:
            raise DotCalError(str(e)) from e
        _log.info("dotcal: marker dot is blob %d of 4 (area %.0f px2) — "
                  "camera orientation resolved from it, not from error",
                  marker, coarse_areas[marker])
        coarse_fit = geometry.fit(ordered, coarse_stage,
                                  ransac_reproj_px=self.ransac_reproj_px)

        # Where the coarse fit says each expected dot should appear in the
        # camera. This is what removes the row-sorting assumption entirely.
        stage_to_cam = geometry.invert(coarse_fit.h)

        # **The ROI, CLAUDE.md's M4i fix.** The table's own footprint in
        # camera pixels — not a config guess, not a hardcoded margin — is
        # whatever the coarse fit (just confirmed 4/4, marker resolved)
        # says the FULL stage rectangle projects to. A room lamp outside
        # the table, at the edge of the camera's field of view, is outside
        # this box by construction and never reaches the fine pass's
        # detector, which is what removes it rather than merely hoping a
        # threshold does. Padded by `roi_margin_px` (= the match gate) so
        # nothing the matcher would have accepted gets cropped away first.
        sw, sh = self.store.stage_size
        table_bbox_cam = geometry.apply_rect(stage_to_cam, (0.0, 0.0, sw, sh))
        roi = pad_rect(table_bbox_cam, self.roi_margin_px)

        fine_stage = grid_points(self.cols, self.rows, self.inset,
                                 self.store.stage_size)
        fine_cam, _fine_areas = self._pass(fine_stage, self.dot_radius, "fine",
                                           roi=roi)

        expected_cam = [geometry.apply(stage_to_cam, p) for p in fine_stage]
        pairing = geometry.match_nearest(expected_cam, fine_cam,
                                         max_distance_px=self.match_gate_px)

        src: List[Point] = []
        dst: List[Point] = []
        for expected_index, found_index in enumerate(pairing):
            if found_index is None:
                continue
            src.append(fine_cam[found_index])
            dst.append(fine_stage[expected_index])
        missing = len(fine_stage) - len(src)
        if len(src) < geometry.MIN_POINTS:
            raise DotCalError(
                f"only {len(src)} of {len(fine_stage)} dots could be matched "
                "to the pattern — the projector and camera may be looking at "
                "different parts of the table")

        fit = geometry.fit(src, dst, ransac_reproj_px=self.ransac_reproj_px)

        # **The RMS alone is not a verdict, and this was found by a test
        # rather than reasoned out in advance.** Feed a noisy rig — 6 px
        # of centroid jitter against a 3 px RANSAC threshold — and RANSAC
        # does exactly what it is designed to do: it finds the largest
        # subset that agrees to within 3 px, which on noisy input can be
        # five points, and reports a beautiful sub-pixel RMS over them.
        # A solve fitted to five of fifteen dots is a solve with no
        # redundancy anywhere, and it would have passed doc section 21's
        # "under ~3 px" acceptance test while being the worst calibration
        # the rig could produce. So the verdict needs both numbers.
        #
        # The floor is 70% of the pattern, never fewer than 6 — two above
        # the 4 a homography strictly needs, so there is always something
        # left over to disagree.
        min_good_inliers = max(geometry.MIN_POINTS + 2,
                               int(round(0.7 * len(fine_stage))))
        enough = fit.n_inliers >= min_good_inliers
        good = fit.rms_px <= self.rms_warn_px and enough
        parts = [f"{fit.rms_px:.1f} px average error",
                 f"{fit.n_inliers} of {len(fine_stage)} dots used"]
        if missing:
            parts.append(f"{missing} not found")
        if good:
            message = "Calibration looks good — " + ", ".join(parts) + "."
        elif not enough:
            message = ("Calibration used too few dots — " + ", ".join(parts)
                       + f". At least {min_good_inliers} have to agree with "
                       "each other; a low error over a handful of dots is "
                       "not a good calibration. Check the projector focus "
                       "and that nothing is sitting on the table.")
        else:
            message = ("Calibration is worse than expected — "
                       + ", ".join(parts)
                       + f". Anything over {self.rms_warn_px:.0f} px usually "
                       "means the camera moved, the projector is out of "
                       "focus, or a dot landed on a tray.")

        self.store.set_homography(fit.h, rms_px=fit.rms_px,
                                  n_points=fit.n_inliers,
                                  keystone_fingerprint=keystone_fingerprint,
                                  camera_size=camera_size)
        # A new solve invalidates the last human Verify answer: the rects
        # have just moved under it (doc section 12.6).
        self.store.clear_verified()
        if save:
            self.store.save_homography()

        return DotCalResult(h=fit.h, rms_px=fit.rms_px,
                            n_points=len(fine_stage), n_inliers=fit.n_inliers,
                            message=message, good=good)

    def _pass(self, stage_points: Sequence[Point], radius: float,
              name: str, first_radius: Optional[float] = None,
              roi: Optional[Sequence[float]] = None
              ) -> Tuple[List[Point], List[float]]:
        """Draw a pattern, wait for it to be on the table and exposed, ask
        the classifier what it sees, return camera-space points **and their
        blob areas**.

        The areas are what identify the oversized marker in the coarse pass.
        They travel on the reply `classifier/main.py` already sends, so
        nothing about the wire changed to get them here.

        `roi`, when given, is a camera-space `[x, y, w, h]` the classifier
        crops to before it looks for anything — see `_run`'s comment on
        where it comes from. **Never passed for the coarse pass**: there is
        no table footprint to crop to until the coarse pass has found one.
        """
        self._show(overlay_dots(stage_points, radius, first_radius))
        self._sleep(self.settle_s)
        # The top-hat kernel has to clear the biggest dot in THIS pass or
        # the flattening removes it (see `dots.flatten_background`). Core
        # sizes it because core is the only side that knows the pattern —
        # the same I2 argument that has core send dot positions rather than
        # a "draw the pattern" flag.
        #
        # **Still computed and sent, currently ignored by the classifier.**
        # `classifier/main.py._detect_dots` reverted to plain fixed-
        # threshold detection 2026-08-12 — ground truth on the rig showed
        # the sweep+top-hat combination doing WORSE on the coarse pass (0-1
        # of 4 real corners against 4 of 4 for a fixed threshold), because
        # a tray reflection or the room lamp outweighs a real dot under
        # this top-hat sizing. `tophat` is left wired here, harmless and
        # unread, in case a *correctly* sized top-hat is worth revisiting
        # once the ROI above has been confirmed sufficient on its own — see
        # CLAUDE.md's M4h/M4i.
        biggest = max(radius, first_radius or 0.0)
        tophat = int(self.tophat_scale * 2.0 * biggest) | 1
        reply = self._ask(len(stage_points), self.min_area, tophat,
                          self.average_frames, roi)
        if not isinstance(reply, dict):
            raise DotCalError(
                "the classifier did not answer — is it running?")
        if reply.get("ok") is False:
            raise DotCalError(str(reply.get("error")
                                  or "the classifier could not look at a frame"))
        points = reply.get("points")
        if not isinstance(points, list):
            raise DotCalError("the classifier's answer made no sense")
        raw_areas = reply.get("areas")
        if not isinstance(raw_areas, list):
            raw_areas = []
        out: List[Point] = []
        areas: List[float] = []
        for i, p in enumerate(points):
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                continue
            out.append((float(p[0]), float(p[1])))
            # Areas are positional against `points`; a reply without them
            # (or a short list) yields 0.0, which `identify_marker` then
            # refuses rather than silently treating as a tie.
            areas.append(float(raw_areas[i])
                         if i < len(raw_areas) else 0.0)
        _log.info("dotcal: %s pass drew %d dots, camera saw %d",
                  name, len(stage_points), len(out))
        return out, areas
