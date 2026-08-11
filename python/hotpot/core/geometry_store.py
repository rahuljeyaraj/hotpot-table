"""core/geometry_store.py — who holds the geometry (doc sections 5.3, 8.4,
8.5; doc section 21 M4 build item 1).

Doc section 5.3, restated as the contract this class implements:

- Core computes and owns `H_cam->stage` (`state/homography.json`).
- Core owns bin rects **in both spaces**. The camera-space rects are the
  ground truth — staff dragged them there on the live feed — and the
  stage-space rects are derived through `H` at load time and **never
  persisted**. Doc section 8.4 is explicit about why: persisting a derived
  value invites the two copies to disagree, and the one that would be
  wrong is the one oF draws with.
- Core pushes camera-space rects to the classifier and stage-space rects
  to oF.

This module is the only writer of `state/homography.json` and
`state/bin_rects.json`, the same way `core/calibrator.py` is the only
writer of `state/loadcell_cal.json`. Both go through `atomicio` (doc
section 20.4) because a half-written homography does not fail visibly —
it mis-places every rect and the light pass with it.

**No cv2 anywhere in this file.** Fitting lives in `common/geometry.fit`
and is called by `core/dotcal.py`; everything here is loading, applying
and saving, so a core process on a machine with no OpenCV and no camera
still boots, still knows whether it is calibrated, and still derives its
stage rects.

**Doc section 5.3's TRAP lives here more than anywhere else.** There is no
`verify()` on this class and there must not be one. Reprojecting
`stage_rects` back through `H` returns `cam_rects` by construction — it is
what "inverse" means — so such a method would pass on a homography that is
upside down, mirrored, or fitted to mis-paired dots. The only verification
that can fail is doc section 12.6's: project the rects and have a human
look at the trays. `mark_verified()` below records that a human answered,
and records nothing about whether the geometry is right beyond their word.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hotpot.common import atomicio, geometry

_log = logging.getLogger("hotpot.geometry")

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]

NUM_BINS = 8

# core/geometry_store.py -> core -> hotpot -> python -> repo root.
_ROOT = Path(__file__).resolve().parents[3]
HOMOGRAPHY_PATH = _ROOT / "state" / "homography.json"
BIN_RECTS_PATH = _ROOT / "state" / "bin_rects.json"
LEGACY_OFFSETS_PATH = _ROOT / "docs" / "legacy" / "bin_offsets.json"

# Doc sections 8.4 and 8.5 both say `"schema": 3`.
SCHEMA = 3

# Doc section 5.1: stage space is 1920x1080 and is canonical.
STAGE_SIZE = (1920, 1080)

# Doc section 8.6's `camera.capture` default. Recorded in both state files
# so a later read can tell that a homography was solved against a
# different capture resolution than the one running now — which silently
# scales every camera-space rect if nobody notices.
DEFAULT_CAMERA_SIZE = (1920, 1080)


# ---------------------------------------------------------------------------
# The physical table — a mirror of of/hotpot-table/src/TableGeometry.h
# ---------------------------------------------------------------------------
#
# **These numbers exist twice and must change twice.** C++ cannot import
# this file and Python cannot import a header, so the CAD layout lives in
# `TableGeometry.h` (which oF draws from when core has sent it no rects)
# and here (which is what seeds `state/bin_rects.json` the first time).
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


def cad_bin_rects_stage() -> List[Rect]:
    """The eight nominal bin rects in stage pixels, straight off the CAD
    chain above with no measured nudge applied. This is what oF falls back
    to when core has never sent it a rect.
    """
    out = []
    for (x_mm, y_mm) in BIN_ORIGINS_MM:
        x0, y0 = mm_to_stage(x_mm, y_mm)
        x1, y1 = mm_to_stage(x_mm + BIN_W_MM, y_mm + BIN_H_MM)
        out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def legacy_bin_rects_stage(offsets: Optional[Dict[str, Any]] = None,
                           path: Optional[Path] = None) -> List[Rect]:
    """The eight *measured* bin rects in stage pixels, from
    `docs/legacy/bin_offsets.json` — doc section 7.1's "the measured values
    inside `bin_offsets.json` … encode real rig geometry. They become the
    **seed** for `state/bin_rects.json`, converted, not the live file."

    **The shape of that file is a reconstruction, not a specification, and
    this is the honest statement of that.** The Stage-1 openFrameworks code
    that wrote and consumed it was deleted in M0.1 (doc section 7.1's
    "delete outright" list — the alignment nudge grid), so the meaning of
    its four keys is inferred from their lengths and from the CAD chain
    they nudge:

        hLineDeltaMM  4 values — the 4 horizontal bin edges, far row top,
                      far row bottom, near row top, near row bottom
        vLineDeltaMM  8 values — the 8 vertical bin edges, left to right
        offsetXMM     one global shift of the whole grid
        offsetYMM     the same, vertically

    The evidence that this reading is right is circumstantial and is
    recorded rather than dressed up: 4 and 8 are exactly the numbers of
    horizontal and vertical bin edges, every delta is under 7 mm (a nudge,
    not a layout), and the reconstructed rects come out 192-197 mm wide
    and 245-247 mm tall against a 200 x 255 mm nominal — i.e. real cutouts
    a few millimetres inside the drawing, which is what a saw does.

    **It is a seed and nothing more.** These rects are a starting position
    for the operator to drag from on the Setup tab, so that the first
    calibration begins with eight rects roughly on the trays instead of
    eight rects in a heap at the origin. If the reconstruction is wrong,
    the symptom is that the starting rects are visibly offset and the
    operator drags them — not a mis-bill.
    """
    if offsets is None:
        offsets = atomicio.read_json(path or LEGACY_OFFSETS_PATH, {})
    h_delta = list(offsets.get("hLineDeltaMM") or [0.0] * 4)
    v_delta = list(offsets.get("vLineDeltaMM") or [0.0] * 8)
    off_x = float(offsets.get("offsetXMM") or 0.0)
    off_y = float(offsets.get("offsetYMM") or 0.0)
    if len(h_delta) != 4 or len(v_delta) != 8:
        _log.warning("geometry: legacy bin offsets have %d h-deltas and %d "
                     "v-deltas, expected 4 and 8 — seeding from the CAD "
                     "layout instead", len(h_delta), len(v_delta))
        return cad_bin_rects_stage()

    # The CAD edge positions the deltas nudge, derived from BIN_ORIGINS_MM
    # rather than restated, so a change to the layout above cannot leave
    # this holding stale line positions.
    h_lines = [BIN_ORIGINS_MM[0][1], BIN_ORIGINS_MM[0][1] + BIN_H_MM,
               BIN_ORIGINS_MM[4][1], BIN_ORIGINS_MM[4][1] + BIN_H_MM]
    v_lines: List[float] = []
    for col in range(4):
        v_lines.append(BIN_ORIGINS_MM[col][0])
        v_lines.append(BIN_ORIGINS_MM[col][0] + BIN_W_MM)

    out = []
    for i in range(NUM_BINS):
        col, row = i % 4, i // 4
        x0_mm = v_lines[2 * col] + float(v_delta[2 * col]) + off_x
        x1_mm = v_lines[2 * col + 1] + float(v_delta[2 * col + 1]) + off_x
        y0_mm = h_lines[2 * row] + float(h_delta[2 * row]) + off_y
        y1_mm = h_lines[2 * row + 1] + float(h_delta[2 * row + 1]) + off_y
        x0, y0 = mm_to_stage(x0_mm, y0_mm)
        x1, y1 = mm_to_stage(x1_mm, y1_mm)
        out.append((x0, y0, x1 - x0, y1 - y0))
    return out


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class GeometryStore:
    """Everything doc section 5.3 says core owns about geometry.

    Constructed with paths rather than reading module constants for the
    same reason `core/calibrator.py` takes a `path`: a test must never read
    or write the real `state/` files, and these two are exactly the files
    whose corruption would move every rect on the table.
    """

    def __init__(self, homography_path: Path = HOMOGRAPHY_PATH,
                 rects_path: Path = BIN_RECTS_PATH,
                 stage_size: Tuple[int, int] = STAGE_SIZE) -> None:
        self.homography_path = Path(homography_path)
        self.rects_path = Path(rects_path)
        self.stage_size = tuple(stage_size)

        self._h: Optional[List[List[float]]] = None
        self._h_inv: Optional[List[List[float]]] = None
        self.rms_px: Optional[float] = None
        self.n_points: int = 0
        self.computed_at: Optional[float] = None
        self.keystone_fingerprint: Optional[str] = None
        self.camera_size: Tuple[int, int] = DEFAULT_CAMERA_SIZE

        self._cam_rects: List[Optional[Rect]] = [None] * NUM_BINS
        self._stage_rects: List[Optional[Rect]] = [None] * NUM_BINS
        self.rects_written_at: Optional[float] = None
        self.verified_at: Optional[float] = None

        self.load()

    # -- what "calibrated" means (doc section 9.1) -------------------------

    @property
    def has_homography(self) -> bool:
        return self._h is not None

    @property
    def has_rects(self) -> bool:
        """All eight, not any. Seven rects and a hole is not a calibrated
        table: the eighth bin would render with no rect, and whichever
        fallback filled it in would be a guess nobody made deliberately.
        """
        return all(r is not None for r in self._cam_rects)

    @property
    def calibrated(self) -> bool:
        """Doc section 9.1: "BOOT always goes to UNCALIBRATED if
        `homography.json` or `bin_rects.json` is missing." Both, and both
        complete — this is the predicate `core/fsm.py` boots against.
        """
        return self.has_homography and self.has_rects

    @property
    def h(self) -> Optional[List[List[float]]]:
        return None if self._h is None else [list(row) for row in self._h]

    @property
    def h_inv(self) -> Optional[List[List[float]]]:
        return None if self._h_inv is None else [list(row) for row in self._h_inv]

    # -- rects (doc section 8.4) -------------------------------------------

    @property
    def cam_rects(self) -> List[Optional[Rect]]:
        """Camera space — the stored ground truth. This is what goes to the
        classifier (doc section 4.7: "`rects` are camera space — the
        classifier never sees stage space").
        """
        return list(self._cam_rects)

    @property
    def stage_rects(self) -> List[Optional[Rect]]:
        """Stage space — derived, never persisted. This is what goes to oF
        in the `state` message and what the light pass stamps white.

        `None` for a bin whose camera rect is unset, or for every bin while
        there is no homography: an unknown rect must reach oF as an absence
        so it can fall back to `TableGeometry.h`'s CAD layout, not as a
        plausible-looking rectangle at the origin.
        """
        return list(self._stage_rects)

    def set_cam_rect(self, i: int, rect: Optional[Rect]) -> None:
        """Set one bin's camera-space rect and re-derive its stage rect.

        Does **not** save. Doc section 12.6: "Save is explicit." The Setup
        tab's rect dragging streams a rect per pointer-move, and writing
        `state/bin_rects.json` on each one would both hammer the disk and
        make Undo meaningless.
        """
        if not (0 <= i < NUM_BINS):
            raise ValueError(f"bin index {i} out of range")
        if rect is None:
            self._cam_rects[i] = None
        else:
            x, y, w, h = (float(v) for v in rect)
            if w <= 0 or h <= 0:
                raise ValueError(
                    f"bin {i} rect has non-positive size {w}x{h} — a zero-width "
                    "rect crops nothing and would reach the classifier as an "
                    "empty image")
            self._cam_rects[i] = (x, y, w, h)
        self._derive_stage_rects()

    def set_cam_rects(self, rects: Sequence[Optional[Rect]]) -> None:
        if len(rects) != NUM_BINS:
            raise ValueError(f"expected {NUM_BINS} rects, got {len(rects)}")
        for i, r in enumerate(rects):
            self.set_cam_rect(i, r)

    def seed_cam_rects_from_table(self, *, legacy: bool = True) -> List[Rect]:
        """Doc section 21 M4 build item 5: seed `state/bin_rects.json` from
        the legacy measured geometry, **converted to camera space**.

        The conversion is stage -> camera, i.e. through `H^-1`, so this
        needs a homography and raises without one. That ordering is not an
        implementation detail — camera space does not exist as a meaningful
        place to put a rect until the camera has been solved against the
        projector, which is why doc section 21 lists dot calibration
        (build item 3) before this (build item 5).

        Does not save, for the same reason `set_cam_rect` does not: these
        are a starting position to drag from, and the operator's Save is
        what makes them real.

        **The seeded rect is slightly larger than the shape it came from,
        and that is inherent rather than a bug to chase.** Doc section
        8.4 stores a camera rect as an axis-aligned `[x, y, w, h]`, and a
        stage rectangle carried through a homography is a quadrilateral,
        not a rectangle. `apply_rect` boxes it, and deriving the stage
        rect back boxes it again — so the round trip grows each rect by
        roughly the amount of camera ROTATION in the solve — measured at
        26% against the tests' deliberately harsh synthetic camera, of
        which only about a third comes from perspective; the rest is
        ~2.5 degrees of rotation, because the bounding box of a rotated
        rectangle grows by roughly its own size times the sine of the
        angle.

        Only the seed is boxed twice. A rect the operator dragged is
        boxed once, on the way to stage space — but that single boxing
        has the same cause, so **expect the projected cutout to be a few
        percent larger than the tray whenever the camera is not square to
        the table.** That is the safe direction (I9) and oF already grows
        every cutout by `CUTOUT_MARGIN_MM` on top of it; it is recorded
        here so it is recognised as geometry rather than debugged as a
        rendering bug. Growth, never shrinkage, which is
        the safe direction for the light-pass cutout (I9) — and the
        operator drags these anyway.
        """
        if self._h_inv is None:
            raise geometry.GeometryError(
                "no homography yet — run dot calibration before seeding the "
                "bin rects, since camera space is what the homography defines")
        stage = (legacy_bin_rects_stage() if legacy else cad_bin_rects_stage())
        cam = [geometry.apply_rect(self._h_inv, r) for r in stage]
        self.set_cam_rects(cam)
        return cam

    def _derive_stage_rects(self) -> None:
        if self._h is None:
            self._stage_rects = [None] * NUM_BINS
            return
        out: List[Optional[Rect]] = []
        for r in self._cam_rects:
            if r is None:
                out.append(None)
                continue
            try:
                out.append(geometry.apply_rect(self._h, r))
            except geometry.GeometryError:
                # A rect that maps through infinity is not something to
                # substitute a guess for — oF falls back to the CAD layout
                # for a `None`, which is visibly wrong in the right way.
                _log.error("geometry: bin rect %r does not map through the "
                           "homography — leaving its stage rect unset", r)
                out.append(None)
        self._stage_rects = out

    # -- the homography (doc section 8.5) ----------------------------------

    def set_homography(self, h: Sequence[Sequence[float]], *,
                       rms_px: Optional[float] = None,
                       n_points: int = 0,
                       keystone_fingerprint: Optional[str] = None,
                       camera_size: Optional[Tuple[int, int]] = None,
                       computed_at: Optional[float] = None) -> None:
        """Install a solved homography and re-derive every stage rect.

        The inverse is computed once, here, rather than per call: it is
        needed on every classifier crop and every rect seed, and inverting
        a 3x3 per frame to save nine floats of memory would be a strange
        trade.
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
        self._derive_stage_rects()

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

    def mark_verified(self, when: Optional[float] = None) -> None:
        """Doc section 12.6's Verify step: a human looked at the projected
        outlines and said they sit on the trays.

        This records **that a human answered yes**, and nothing else. It is
        not derived from the geometry and cannot be — see this module's
        docstring and doc section 5.3.
        """
        self.verified_at = float(when if when is not None else time.time())

    def clear_verified(self) -> None:
        """A "No" answer, a re-solve, or a rect edit all invalidate the
        last human verdict. Cheaper to clear it than to argue about
        whether a 3 px nudge counts.
        """
        self.verified_at = None

    # -- persistence -------------------------------------------------------

    def load(self) -> None:
        self._load_homography()
        self._load_rects()
        self._derive_stage_rects()

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
        try:
            self.set_homography(
                h,
                rms_px=data.get("rms_px"),
                n_points=data.get("n_points", 0),
                keystone_fingerprint=data.get("keystone_fingerprint"),
                camera_size=tuple(data.get("camera_size", DEFAULT_CAMERA_SIZE)),
                computed_at=data.get("computed_at"),
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

    def _load_rects(self) -> None:
        data = atomicio.read_json(self.rects_path, None)
        if not isinstance(data, dict):
            return
        cam_size = data.get("camera_size")
        if isinstance(cam_size, list) and len(cam_size) == 2:
            self.camera_size = (int(cam_size[0]), int(cam_size[1]))
        self.rects_written_at = data.get("written")
        self.verified_at = data.get("verified_at")
        for entry in data.get("bins", []):
            if not isinstance(entry, dict):
                continue
            i = entry.get("i")
            cam = entry.get("cam")
            if not isinstance(i, int) or not (0 <= i < NUM_BINS):
                continue
            if not isinstance(cam, list) or len(cam) != 4:
                continue
            try:
                self.set_cam_rect(i, (float(cam[0]), float(cam[1]),
                                      float(cam[2]), float(cam[3])))
            except (TypeError, ValueError):
                _log.error("geometry: bin %s in %s has an unusable rect %r",
                           i, self.rects_path, cam)

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
        })

    def save_rects(self) -> None:
        """Doc section 8.4's schema plus `verified_at`.

        `verified_at` is an addition to the doc's example and is recorded
        in doc section 8.4 in the same commit that added it here. It is the
        timestamp of doc section 12.6's human answer, and it lives with the
        rects rather than with the homography because it is the *rects*
        the human was looking at when they said yes.

        Refuses to write a partial set. Eight rects or none: a file with
        six of them would load as "calibrated" on the next boot for every
        check that asks whether the file exists.
        """
        if not self.has_rects:
            missing = [i for i, r in enumerate(self._cam_rects) if r is None]
            raise geometry.GeometryError(
                f"bins {missing} have no rect yet — all {NUM_BINS} must be "
                "placed before the rects can be saved")
        self.rects_written_at = time.time()
        atomicio.write_json(self.rects_path, {
            "schema": SCHEMA,
            "written": self.rects_written_at,
            "camera_size": list(self.camera_size),
            "verified_at": self.verified_at,
            "bins": [{"i": i, "cam": [round(v, 2) for v in r]}
                     for i, r in enumerate(self._cam_rects)],
        })
