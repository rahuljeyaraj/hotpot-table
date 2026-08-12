"""core/bin_grid.py — the bin-boundary grid, and why there are two of them.

A bin boundary used to be 8 independently-dragged rectangles (one file,
`state/bin_rects.json`, see git history for the version this replaces).
That broke in two ways once the pieces around it were built out:

- **Independent rects cannot be kept level with each other.** A real gap
  separates bin from bin (the table has margins and a centre gap for the
  pot), but nothing stopped bin 0's top edge and bin 1's top edge — same
  row, meant to line up — from disagreeing by a few pixels, which is
  invisible in a list of 8 numbers and very visible as one bin sitting
  higher than its row-mates on the real table.
- **One rect list was being asked to serve two different physical
  systems** — the camera's view of the table and the projector's — related
  only by a single planar homography that cannot fully model either
  device's own lens distortion or mounting error. Doc section 5.3's TRAP
  is exactly this: a value transferred through a homography and never
  re-verified in the space it is actually used *looks* fine and can still
  be wrong in a way nothing catches.

So: a bin boundary is now a **grid** — 4 horizontal + 8 vertical line
positions, matching the physical layout (2 rows of 4 bins) — and there are
**two independent grids, never derived from each other**:

- `state/bin_grid_camera.json` — lines dragged on the camera's rectified
  view of the table (the "table crop": the raw frame warped through
  `H_cam_to_stage` so a pixel in it sits where the projector's own pixel
  of the same coordinate lights — see `common/geometry.warp_frame_to_stage`).
  This is the grid MediaPipe, the classifier's crop, and core's
  hand-entered-bin hit test all read. It needs the camera.
- `state/bin_grid_projector.json` (M4n) — lines dragged (nudged: there is
  no camera image to drag on) while watching the actual light on the
  actual table. This is the grid oF's ring/cutout/fluid interactions
  read (`core/main.py`'s `_bin_msg` sends its rects as `state.bins[].rect`).
  It needs no camera at all.

Both nominally address the same 1920x1080 canvas (`geometry_store.STAGE_SIZE`),
which is why `cad_bin_grid_stage()`/`legacy_bin_grid_stage()` below are a
reasonable starting SEED for either — but seeding is not deriving, and
nothing here ever computes one grid from the other's saved value. A grid
is only ever set by whoever is looking at the space it describes.

**Grid, not rects, fixes the old seeding bug for free.** The pre-existing
`docs/legacy/bin_offsets.json` already stores exactly `hLineDeltaMM` (4
values) and `vLineDeltaMM` (8 values) — this module's shape, not the old
rect-list's. The old rect-based reconstruction had to round-trip those
deltas through a rect-then-homography-then-rect box, which is what grew
the seeded rects ~26% on a harshly perspective camera (see
`geometry_store.py`'s own history). A grid seed is pure line-position
arithmetic, in whichever pixel space it is being seeded into — no
homography, no boxing, nothing to grow.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hotpot.common import atomicio
from hotpot.core import geometry_store as gs

Rect = Tuple[float, float, float, float]

NUM_BINS = 8
NUM_H_LINES = 4   # far-top, far-bottom, near-top, near-bottom
NUM_V_LINES = 8   # col0-left, col0-right, col1-left, col1-right, ... col3-right
SCHEMA = 1

# core/bin_grid.py -> core -> hotpot -> python -> repo root.
_ROOT = Path(__file__).resolve().parents[3]
CAMERA_GRID_PATH = _ROOT / "state" / "bin_grid_camera.json"
PROJECTOR_GRID_PATH = _ROOT / "state" / "bin_grid_projector.json"


class BinGridError(Exception):
    """A grid that cannot be trusted: the wrong number of lines, a pair
    that is not strictly increasing (which defines a zero-or-negative-size
    bin), or a non-finite value. Never raised for a grid that is merely
    ugly — a bin drawn too small to see is the operator's problem to look
    at and fix, not this module's to refuse.
    """


def _check_increasing_pairs(lines: List[float], which: str) -> None:
    """Lines `2i` and `2i+1` are one bin's near and far edge along that
    axis, so `2i+1` must be strictly greater than `2i` — anything else
    defines a bin with zero or negative size, which would crop an empty
    image that reaches the classifier as a blank and comes back as a
    confident wrong label (the same rule `geometry_store.set_cam_rect`
    used to enforce for a rect's own width and height).
    """
    for i in range(0, len(lines), 2):
        a, b = lines[i], lines[i + 1]
        if not (math.isfinite(a) and math.isfinite(b)):
            raise BinGridError(
                f"{which} line {i} or {i + 1} is not a finite number")
        if b <= a:
            raise BinGridError(
                f"{which} lines {i} and {i + 1} are not in increasing order "
                f"({a} then {b}) — that pair would crop a zero-or-negative "
                "size bin")


@dataclass
class BinGrid:
    """4 horizontal + 8 vertical line positions, in whichever pixel space
    they were dragged in. `rects()` is the only thing anything downstream
    needs — nothing else in this codebase should read `h_lines`/`v_lines`
    directly, the same way nothing reads `TableGeometry.h`'s BINS chain
    directly instead of going through its own derived rects.
    """

    h_lines: List[float]
    v_lines: List[float]

    def __post_init__(self) -> None:
        self.h_lines = [float(v) for v in self.h_lines]
        self.v_lines = [float(v) for v in self.v_lines]
        if len(self.h_lines) != NUM_H_LINES:
            raise BinGridError(
                f"a bin grid needs exactly {NUM_H_LINES} horizontal lines, "
                f"got {len(self.h_lines)}")
        if len(self.v_lines) != NUM_V_LINES:
            raise BinGridError(
                f"a bin grid needs exactly {NUM_V_LINES} vertical lines, "
                f"got {len(self.v_lines)}")
        _check_increasing_pairs(self.h_lines, "horizontal")
        _check_increasing_pairs(self.v_lines, "vertical")

    def rects(self) -> List[Rect]:
        """The 8 bin rects the grid implies. Bin `i` is column `i % 4`,
        row `i // 4` — far row first, left to right — the same order the
        calibration dots and `TableGeometry.h`'s BINS chain use, so a
        caller matching bin index against either of those still lines up.
        Every bin in the same row shares that row's top and bottom line;
        every bin in the same column shares that column's left and right
        line — a real physical gap still separates bin from bin, but
        there is no way to drag one bin's edge without the whole row (or
        column) it belongs to moving with it, which is the entire point
        of a grid over 8 independent rects.
        """
        out: List[Rect] = []
        for i in range(NUM_BINS):
            col, row = i % 4, i // 4
            x0, x1 = self.v_lines[2 * col], self.v_lines[2 * col + 1]
            y0, y1 = self.h_lines[2 * row], self.h_lines[2 * row + 1]
            out.append((x0, y0, x1 - x0, y1 - y0))
        return out

    def to_json(self) -> Dict[str, Any]:
        return {"h_lines": list(self.h_lines), "v_lines": list(self.v_lines)}

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "BinGrid":
        h = raw.get("h_lines")
        v = raw.get("v_lines")
        if not isinstance(h, list) or not isinstance(v, list):
            raise BinGridError(
                "bin grid json needs 'h_lines' and 'v_lines' lists")
        return cls(h_lines=h, v_lines=v)


# ---------------------------------------------------------------------------
# Seeding — CAD nominal and the legacy measured nudge, both pure line math
# ---------------------------------------------------------------------------

def cad_bin_grid_stage() -> BinGrid:
    """The nominal CAD grid lines, off `TableGeometry.h`'s own chain
    (mirrored in `geometry_store.BIN_ORIGINS_MM`/`BIN_W_MM`/`BIN_H_MM`),
    converted to stage px via `geometry_store.mm_to_stage`.

    This is the seed for a fresh, never-dragged grid in EITHER space —
    both nominally address the same 1920x1080 canvas (this module's
    docstring), so the same starting position is a reasonable place for an
    operator to start dragging from in either one, even though the two are
    then adjusted completely independently.
    """
    h_mm = [gs.BIN_ORIGINS_MM[0][1], gs.BIN_ORIGINS_MM[0][1] + gs.BIN_H_MM,
            gs.BIN_ORIGINS_MM[4][1], gs.BIN_ORIGINS_MM[4][1] + gs.BIN_H_MM]
    v_mm: List[float] = []
    for col in range(4):
        v_mm.append(gs.BIN_ORIGINS_MM[col][0])
        v_mm.append(gs.BIN_ORIGINS_MM[col][0] + gs.BIN_W_MM)
    h_px = [gs.mm_to_stage(0.0, y)[1] for y in h_mm]
    v_px = [gs.mm_to_stage(x, 0.0)[0] for x in v_mm]
    return BinGrid(h_lines=h_px, v_lines=v_px)


def legacy_bin_grid_stage(offsets: Optional[Dict[str, Any]] = None,
                          path: Optional[Path] = None) -> BinGrid:
    """The CAD grid, nudged by the measured deltas in
    `docs/legacy/bin_offsets.json` — doc section 7.1's "the measured
    values … encode real rig geometry. They become the seed."

    `hLineDeltaMM` (4 values) and `vLineDeltaMM` (8 values) are already
    exactly this module's shape — a horizontal and a vertical line grid —
    so this is direct line-position arithmetic with no rect, no
    homography, and nothing to box twice. That is a genuine change from
    the rect-based version this replaces, not just a rename: the old
    version's seed grew each rect by up to ~26% on a harshly perspective
    synthetic camera because it round-tripped the nudge through a rect and
    a homography inverse. A grid has no such round trip to take.
    """
    if offsets is None:
        offsets = atomicio.read_json(path or gs.LEGACY_OFFSETS_PATH, {})
    h_delta = list(offsets.get("hLineDeltaMM") or [0.0] * NUM_H_LINES)
    v_delta = list(offsets.get("vLineDeltaMM") or [0.0] * NUM_V_LINES)
    off_x = float(offsets.get("offsetXMM") or 0.0)
    off_y = float(offsets.get("offsetYMM") or 0.0)
    if len(h_delta) != NUM_H_LINES or len(v_delta) != NUM_V_LINES:
        return cad_bin_grid_stage()

    h_mm = [gs.BIN_ORIGINS_MM[0][1], gs.BIN_ORIGINS_MM[0][1] + gs.BIN_H_MM,
            gs.BIN_ORIGINS_MM[4][1], gs.BIN_ORIGINS_MM[4][1] + gs.BIN_H_MM]
    v_mm: List[float] = []
    for col in range(4):
        v_mm.append(gs.BIN_ORIGINS_MM[col][0])
        v_mm.append(gs.BIN_ORIGINS_MM[col][0] + gs.BIN_W_MM)

    h_px = [gs.mm_to_stage(0.0, y + float(d) + off_y)[1]
            for y, d in zip(h_mm, h_delta)]
    v_px = [gs.mm_to_stage(x + float(d) + off_x, 0.0)[0]
            for x, d in zip(v_mm, v_delta)]
    return BinGrid(h_lines=h_px, v_lines=v_px)


# ---------------------------------------------------------------------------
# The store — one grid, one file. Instantiated twice by `core/main.py`
# (M4n): once for the camera grid, once for the projector grid
# (`state/bin_grid_projector.json`), same class both times, each store
# knowing nothing about the other.
# ---------------------------------------------------------------------------

class BinGridStore:
    """One bin grid, persisted to one file. No cv2, no homography — a
    grid is authored directly in whatever pixel space its own file's
    docstring says it is, and this class never converts it into any other
    space.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.grid: Optional[BinGrid] = None
        self.written_at: Optional[float] = None
        self.load()

    @property
    def has_grid(self) -> bool:
        return self.grid is not None

    def rects(self) -> List[Optional[Rect]]:
        """The 8 derived rects, or 8 `None`s before any grid is set — the
        same "absence, not a guess" rule `geometry_store.stage_rects` used
        to follow: a consumer with no grid yet must be able to tell that
        apart from a grid that happens to sit at the origin.
        """
        if self.grid is None:
            return [None] * NUM_BINS
        return list(self.grid.rects())

    def set_grid(self, h_lines: List[float], v_lines: List[float]) -> None:
        """Replace the grid wholesale. Does **not** save — doc section
        12.6's "Save is explicit" applies here exactly as it did to the
        old rect drag: the Setup tab streams a grid per pointer-move, and
        writing the file on every one would both hammer the disk and make
        Undo meaningless.
        """
        self.grid = BinGrid(h_lines=list(h_lines), v_lines=list(v_lines))

    def seed_from_table(self, *, legacy: bool = True) -> BinGrid:
        """Doc section 21 M4 build item 5's successor: put a starting grid
        on screen, in the SAME space this store's file already lives in —
        no homography needed (see this module's docstring on why the old
        rect version needed one and this does not).

        Does not save, for the same reason `set_grid` does not.
        """
        grid = legacy_bin_grid_stage() if legacy else cad_bin_grid_stage()
        self.grid = grid
        return grid

    # -- persistence ---------------------------------------------------

    def load(self) -> None:
        data = atomicio.read_json(self.path, None)
        if not isinstance(data, dict):
            return
        self.written_at = data.get("written")
        try:
            self.grid = BinGrid.from_json(data)
        except BinGridError:
            self.grid = None

    def save(self) -> None:
        if self.grid is None:
            raise BinGridError("nothing to save — no grid is set")
        self.written_at = time.time()
        payload: Dict[str, Any] = {
            "schema": SCHEMA,
            "written": self.written_at,
        }
        payload.update(self.grid.to_json())
        atomicio.write_json(self.path, payload)
