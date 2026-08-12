"""core/geometry_store.py — the camera<->stage homography, corner points,
and the display-only view rotation (doc sections 5.3, 8.5, 9.1).

**This module used to also own the 8 bin rects, in both spaces
(`state/bin_rects.json`).** That moved to `core/bin_grid.py` — a bin
boundary is a 4-horizontal + 8-vertical line grid now, not 8 independently
dragged rects, and there are two independent grids (camera-space,
projector-space) instead of one rect list derived through this module's
homography into a second space. See `bin_grid.py`'s module docstring for
the full reasoning; this module is left holding only what doc section 5.3
still assigns to it: `H_cam_to_stage` itself, the corner points it was
solved from, and the keystone fingerprint it is checked against.

This module is the only writer of `state/homography.json`, the same way
`core/calibrator.py` is the only writer of `state/loadcell_cal.json`. Both
go through `atomicio` (doc section 20.4) because a half-written homography
does not fail visibly — it mis-places every downstream frame warp.

**No cv2 anywhere in this file.** Fitting lives in `common/geometry.fit`
and is called by `fit_from_corners()` below; everything else here is
loading, applying and saving, so a core process on a machine with no
OpenCV and no camera still boots, still knows whether it is calibrated,
and still holds the last solved homography.

**Doc section 5.3's TRAP lives here more than anywhere else.** There is no
`verify()` on this class and there must not be one. A homography that
LOOKS perfect (`rms_px: 0.0`, `n_points: 4`) can still be solved from a
mis-paired or degenerate click order and point nowhere near the real
table — the only check that can fail is a human looking at what the
homography actually produces in the space it is used (the warped table
frame `common/geometry.warp_frame_to_stage` builds from it, and — once
`bin_grid.py`'s camera grid is dragged onto that frame — the Setup tab's
own Verify step for the grid, not for this matrix).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hotpot.common import atomicio, geometry

_log = logging.getLogger("hotpot.geometry")

Point = Tuple[float, float]
Matrix = List[List[float]]

NUM_BINS = 8

# core/geometry_store.py -> core -> hotpot -> python -> repo root.
_ROOT = Path(__file__).resolve().parents[3]
HOMOGRAPHY_PATH = _ROOT / "state" / "homography.json"
VIEW_ROTATION_PATH = _ROOT / "state" / "view_rotation.json"
LEGACY_OFFSETS_PATH = _ROOT / "docs" / "legacy" / "bin_offsets.json"

# The Setup tab's Rotate control (drag-corner rebuild, step 4 — not yet
# built). A display preference, not calibration data, so it lives in its
# own tiny file and must survive even before any homography exists.
VALID_VIEW_ROTATIONS = (0, 90, 180, 270)
# This rig's measured mount (CLAUDE.md's M4i / commit b847c0f) — the
# default so nobody who never touches the Rotate button sees a regression.
DEFAULT_VIEW_ROTATION_DEG = 180

# Doc section 8.5 says `"schema": 3`.
SCHEMA = 3

# Doc section 5.1: stage space is 1920x1080 and is canonical.
STAGE_SIZE = (1920, 1080)

# Doc section 8.6's `camera.capture` default. Recorded so a later read can
# tell that a homography was solved against a different capture resolution
# than the one running now.
DEFAULT_CAMERA_SIZE = (1920, 1080)


# ---------------------------------------------------------------------------
# The physical table — a mirror of of/hotpot-table/src/TableGeometry.h
# ---------------------------------------------------------------------------
#
# **These numbers exist twice and must change twice.** C++ cannot import
# this file and Python cannot import a header, so the CAD layout lives in
# `TableGeometry.h` (which oF draws from when core has sent it no rects)
# and here, which is what `bin_grid.cad_bin_grid_stage()` seeds a fresh
# grid from — camera-space and projector-space alike, since both
# nominally address this same stage canvas (`bin_grid.py`'s docstring).
# `test_geometry_store.py` mirrors that header's own `static_assert`
# chains — the X and Y walks across the table must each sum to the table
# dimension — so an edit to one side that is not made to the other fails a
# test rather than moving four trays by 50 mm on the rig.

TABLE_W_MM = 1524.0
TABLE_H_MM = 914.4
BIN_W_MM = 200.0
BIN_H_MM = 255.0

# X: 92 + 200 + 50 + 200 + 440 + 200 + 50 + 200 + 92 = 1524
# Y: 177 + 255 + 50 + 255 + 177.4 = 914.4
BIN_ORIGINS_MM: Tuple[Tuple[float, float], ...] = (
    (92.0, 177.0),      # 0  far left
    (342.0, 177.0),     # 1  far centre-left
    (982.0, 177.0),     # 2  far centre-right
    (1232.0, 177.0),    # 3  far right
    (92.0, 482.0),      # 4  near left
    (342.0, 482.0),     # 5  near centre-left
    (982.0, 482.0),     # 6  near centre-right
    (1232.0, 482.0),    # 7  near right
)


def mm_to_stage(x_mm: float, y_mm: float) -> Point:
    """Table millimetres to stage pixels, the same two axis-independent
    scales `TableGeometry.h`'s `mmToPxX`/`mmToPxY` use. Independent because
    the table's aspect (1.667) is not the projector's (1.778) — a single
    uniform scale would put the near row 50 mm out.
    """
    return (x_mm * STAGE_SIZE[0] / TABLE_W_MM,
            y_mm * STAGE_SIZE[1] / TABLE_H_MM)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class GeometryStore:
    """Doc section 5.3's homography, and nothing else this module used to
    also carry — see the module docstring for where the bin rects went.

    Constructed with paths rather than reading module constants for the
    same reason `core/calibrator.py` takes a `path`: a test must never read
    or write the real `state/` files, and this is exactly the file whose
    corruption would move every downstream frame warp.
    """

    def __init__(self, homography_path: Path = HOMOGRAPHY_PATH,
                 view_rotation_path: Path = VIEW_ROTATION_PATH,
                 stage_size: Tuple[int, int] = STAGE_SIZE) -> None:
        self.homography_path = Path(homography_path)
        self.view_rotation_path = Path(view_rotation_path)
        self.stage_size = tuple(stage_size)

        self._h: Optional[List[List[float]]] = None
        self._h_inv: Optional[List[List[float]]] = None
        self.rms_px: Optional[float] = None
        self.n_points: int = 0
        self.computed_at: Optional[float] = None
        self.keystone_fingerprint: Optional[str] = None
        self.camera_size: Tuple[int, int] = DEFAULT_CAMERA_SIZE
        # The last 4 confirmed raw camera-space corners, in
        # fit_from_corners()'s fixed front-left/front-right/back-right/
        # back-left order. Set only alongside a confirmed set_homography()
        # call (see its docstring) — never a mid-drag position.
        self.corner_points: Optional[List[Point]] = None

        # A display preference, not calibration data (see set_view_rotation
        # below) — defaulted here so it is correct even before load() runs.
        self.view_rotation_deg: int = DEFAULT_VIEW_ROTATION_DEG

        self.load()

    # -- what "a homography exists" means (doc section 9.1) ----------------

    @property
    def has_homography(self) -> bool:
        return self._h is not None

    @property
    def h(self) -> Optional[List[List[float]]]:
        return None if self._h is None else [list(row) for row in self._h]

    @property
    def h_inv(self) -> Optional[List[List[float]]]:
        return None if self._h_inv is None else [list(row) for row in self._h_inv]

    # -- the homography (doc section 8.5) ----------------------------------

    def set_homography(self, h: Sequence[Sequence[float]], *,
                       rms_px: Optional[float] = None,
                       n_points: int = 0,
                       keystone_fingerprint: Optional[str] = None,
                       camera_size: Optional[Tuple[int, int]] = None,
                       computed_at: Optional[float] = None,
                       corner_points: Optional[Sequence[Point]] = None) -> None:
        """Install a solved homography.

        The inverse is computed once, here, rather than per call: it is
        needed by every table-crop warp, and inverting a 3x3 per frame to
        save nine floats of memory would be a strange trade.

        `corner_points`, when given, is recorded as-is (not re-derived from
        `h`) so the Setup tab's drag-corner UI can re-seed its handles from
        the last confirmed calibration instead of a blind default rect next
        time it opens. A call that omits it — every call in this file's own
        tests, and any future caller that installs a homography some other
        way — leaves it unset rather than guessing.
        """
        matrix = [[float(v) for v in row] for row in h]
        if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
            raise geometry.GeometryError("a homography is a 3x3 matrix")
        self._h_inv = geometry.invert(matrix)   # raises on singular, before commit
        self._h = matrix
        self.rms_px = None if rms_px is None else float(rms_px)
        self.n_points = int(n_points)
        self.keystone_fingerprint = keystone_fingerprint
        if camera_size is not None:
            self.camera_size = (int(camera_size[0]), int(camera_size[1]))
        self.computed_at = float(computed_at if computed_at is not None
                                 else time.time())
        self.corner_points = (None if corner_points is None
                              else [(float(p[0]), float(p[1]))
                                    for p in corner_points])

    def fit_from_corners(self, cam_points: Sequence[Point]) -> geometry.Fit:
        """The manual calibration flow: the operator clicks the table's 4
        real corners on the live feed, in a **fixed physical order** — from
        where the operator stands, front-left, front-right, back-right,
        back-left — and this pairs them against the matching stage corners.

        Order is pinned to the click *sequence*, never inferred from where a
        point lands on screen. `common.geometry.order_quad` does the latter
        and is explicitly banned for calibration: on this rig's 180-degree-
        mounted camera, a screen-position labelling silently pairs every
        corner with its opposite, and four points always fit a homography
        exactly, so the wrong answer comes back with zero error and no
        warning. An operator standing at the table knows which corner is
        physically "front-left" regardless of how the feed looks on screen —
        the code never has to guess, so it never has to get it wrong.

        Returns the `Fit` **unsaved** — installing it is `set_homography()`'s
        job; `core/main.py`'s `_handle_manual_calibrate` is the caller that
        does both.
        """
        if len(cam_points) != 4:
            raise geometry.GeometryError(
                f"table calibration needs exactly 4 corners, got {len(cam_points)}")
        return geometry.fit(list(cam_points), self._manual_corners_stage())

    def _manual_corners_stage(self) -> List[Point]:
        """The 4 stage corners, in the same front-left / front-right /
        back-right / back-left order `fit_from_corners()` expects its camera
        points in.
        """
        w, h = self.stage_size
        return [(0.0, 0.0), (float(w), 0.0),
                (float(w), float(h)), (0.0, float(h))]

    # -- view rotation (Setup tab Rotate control, drag-corner rebuild step 4) -

    def set_view_rotation(self, deg: Any) -> None:
        """The Setup tab's Rotate button: cycles the operator's display of
        the live feed through 0/90/180/270 degrees.

        A **display preference, not calibration data** — it does not touch
        `H_cam->stage` or anything the classifier/oF receive, so it lives
        in its own file and saves immediately rather than waiting on a
        Confirm the way a dragged corner does. Validated here rather than
        left to whatever calls this, because doc section 20.4's rule for
        every state file applies just as much to a 4-way enum as to a
        homography: a bad value on disk must never look like a plausible
        rotation.
        """
        if (not isinstance(deg, int) or isinstance(deg, bool)
                or deg not in VALID_VIEW_ROTATIONS):
            raise ValueError(
                f"view rotation must be one of {VALID_VIEW_ROTATIONS}, "
                f"got {deg!r}")
        self.view_rotation_deg = deg
        atomicio.write_json(self.view_rotation_path,
                            {"view_rotation_deg": self.view_rotation_deg})

    def keystone_is_stale(self, live_fingerprint: Optional[str]) -> bool:
        """Doc section 8.5: oF reports its keystone fingerprint in `stat`;
        if it differs from the one recorded beside the homography, the
        calibration is stale and the staff view says so.

        Unknown on either side is **not** stale. Before oF has ever
        connected there is no fingerprint to compare, and a startup that
        shouted "calibration stale" every time the table was slow to come
        up would train the operator to ignore the one message that matters.
        """
        if not self.has_homography:
            return False
        if not live_fingerprint or not self.keystone_fingerprint:
            return False
        return live_fingerprint != self.keystone_fingerprint

    # -- persistence -------------------------------------------------------

    def load(self) -> None:
        self._load_homography()
        self._load_view_rotation()

    def _load_homography(self) -> None:
        data = atomicio.read_json(self.homography_path, None)
        if not isinstance(data, dict):
            return
        h = data.get("H_cam_to_stage")
        if (not isinstance(h, list) or len(h) != 3
                or any(not isinstance(row, list) or len(row) != 3 for row in h)):
            _log.error("geometry: %s has no usable H_cam_to_stage — treating "
                       "the table as uncalibrated", self.homography_path)
            return
        corners = data.get("corners")
        if corners is not None and (
                not isinstance(corners, list) or len(corners) != 4
                or not all(isinstance(p, list) and len(p) == 2
                           and all(isinstance(v, (int, float)) for v in p)
                           for p in corners)):
            # Dropped, not fatal — corners are informational (Step 4's UI
            # seed), not something the homography's own validity depends
            # on. A dropped dot in the old dot-cal parser got the same
            # tolerance for the same reason: losing this must not cost the
            # calibration itself.
            _log.error("geometry: %s has an unusable corners field — "
                       "dropping it", self.homography_path)
            corners = None
        try:
            self.set_homography(
                h,
                rms_px=data.get("rms_px"),
                n_points=data.get("n_points", 0),
                keystone_fingerprint=data.get("keystone_fingerprint"),
                camera_size=tuple(data.get("camera_size", DEFAULT_CAMERA_SIZE)),
                computed_at=data.get("computed_at"),
                corner_points=corners,
            )
        except (geometry.GeometryError, TypeError, ValueError):
            # A singular or malformed matrix on disk is UNCALIBRATED, not a
            # crash at boot: doc section 9.1's first-boot path has to work
            # from a fresh clone, and "the file is there but nonsense" must
            # land in the same place as "the file is not there".
            _log.exception("geometry: %s could not be loaded — treating the "
                           "table as uncalibrated", self.homography_path)
            self._h = None
            self._h_inv = None
            self.corner_points = None

    def _load_view_rotation(self) -> None:
        """Doc section 12.6's Rotate control must survive even before any
        homography exists, so this reads its own file rather than piggy-
        backing on `_load_homography()`. A missing file is a normal first
        boot (`self.view_rotation_deg` already holds the default, set in
        `__init__` before `load()` runs) and is not logged; a present-but-
        malformed file falls back to the same default, logged, the same
        tolerance `_load_homography` gives a bad matrix.
        """
        data = atomicio.read_json(self.view_rotation_path, None)
        if data is None:
            return
        deg = data.get("view_rotation_deg") if isinstance(data, dict) else None
        if (isinstance(deg, int) and not isinstance(deg, bool)
                and deg in VALID_VIEW_ROTATIONS):
            self.view_rotation_deg = deg
            return
        _log.error("geometry: %s has no usable view_rotation_deg — keeping "
                   "the default (%d)", self.view_rotation_path,
                   DEFAULT_VIEW_ROTATION_DEG)
        self.view_rotation_deg = DEFAULT_VIEW_ROTATION_DEG

    def save_homography(self) -> None:
        """Doc section 8.5's exact schema, written atomically."""
        if self._h is None:
            raise geometry.GeometryError("nothing to save — no homography is set")
        atomicio.write_json(self.homography_path, {
            "schema": SCHEMA,
            "H_cam_to_stage": self._h,
            "computed_at": self.computed_at,
            "n_points": self.n_points,
            "rms_px": self.rms_px,
            "keystone_fingerprint": self.keystone_fingerprint,
            "camera_size": list(self.camera_size),
            "stage_size": list(self.stage_size),
            "corners": (None if self.corner_points is None
                       else [list(p) for p in self.corner_points]),
        })
