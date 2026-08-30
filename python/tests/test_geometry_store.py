"""Tests for core/geometry_store.py — the homography, corner points and
view rotation (doc sections 5.3, 8.5, 9.1).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Every store here is built against a throwaway directory, never the repo's
own `state/` — the same rule `test_calibrator.py` follows for
`loadcell_cal.json`, and for the same reason: this file decides where
every downstream frame warp lands.

Bin-rect/bin-grid tests live in `test_bin_grid.py` now, not here —
this module stopped owning bin rects; see `geometry_store.py`'s own
module docstring for where they went.

What is deliberately NOT tested here: that the homography points the
right way. Doc section 5.3 says outright that a reprojection check passes
by construction, and there is no code in `geometry_store.py` that claims
to do it. `test_the_store_has_no_verify_method` below is the check that
nobody adds one later.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import atomicio, geometry  # noqa: E402
from hotpot.core import geometry_store as gs  # noqa: E402


# Camera -> stage, with real perspective in it (see test_geometry.py).
CAM_TO_STAGE = [
    [1.1, 0.05, 30.0],
    [-0.04, 1.2, -20.0],
    [0.00012, 0.00007, 1.0],
]


class StoreCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.h_path = Path(self.dir.name) / "homography.json"
        self.v_path = Path(self.dir.name) / "view_rotation.json"

    def store(self):
        return gs.GeometryStore(homography_path=self.h_path,
                                view_rotation_path=self.v_path)

    def calibrated_store(self):
        s = self.store()
        s.set_homography(CAM_TO_STAGE, rms_px=1.4, n_points=15,
                         keystone_fingerprint="abc123",
                         camera_size=(1920, 1080))
        return s


class TestFirstBoot(StoreCase):
    """Doc section 9.1: "this is the first-boot path and it must work on a
    fresh clone with an empty `state/`."
    """

    def test_an_empty_state_dir_has_no_homography_and_does_not_raise(self):
        s = self.store()
        self.assertFalse(s.has_homography)
        self.assertIsNone(s.h)


class TestPersistence(StoreCase):

    def test_a_saved_homography_reloads_identically(self):
        s = self.calibrated_store()
        s.save_homography()
        again = self.store()
        self.assertTrue(again.has_homography)
        self.assertEqual(again.n_points, 15)
        self.assertAlmostEqual(again.rms_px, 1.4)
        self.assertEqual(again.keystone_fingerprint, "abc123")
        for row_a, row_b in zip(again.h, CAM_TO_STAGE):
            for a, b in zip(row_a, row_b):
                self.assertAlmostEqual(a, b)

    def test_the_homography_file_matches_doc_8_5s_schema(self):
        s = self.calibrated_store()
        s.save_homography()
        raw = json.loads(self.h_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], 3)
        for key in ("H_cam_to_stage", "computed_at", "n_points", "rms_px",
                    "keystone_fingerprint", "camera_size", "stage_size"):
            self.assertIn(key, raw)
        self.assertEqual(raw["stage_size"], [1920, 1080])

    def test_saving_with_no_homography_is_refused(self):
        with self.assertRaises(geometry.GeometryError):
            self.store().save_homography()

    def test_a_corrupt_homography_file_boots_with_no_homography_rather_than_crashing(self):
        atomicio.write_json(self.h_path, {"schema": 3,
                                          "H_cam_to_stage": "not a matrix"})
        s = self.store()
        self.assertFalse(s.has_homography)

    def test_a_singular_homography_on_disk_boots_with_no_homography(self):
        # Two identical rows: it loads, parses, and cannot be inverted.
        # "The file is there but nonsense" has to land in the same place as
        # "the file is not there", or a fresh clone crashes at boot.
        atomicio.write_json(self.h_path, {
            "schema": 3,
            "H_cam_to_stage": [[1, 2, 3], [1, 2, 3], [4, 5, 6]],
        })
        s = self.store()
        self.assertFalse(s.has_homography)


class TestCornerPoints(StoreCase):
    """The drag-corner rebuild's step 3: `corner_points` rides alongside
    the homography so step 4's UI can re-seed its drag handles from the
    last confirmed calibration instead of a blind default rect.
    """

    CORNERS = [(10.0, 20.0), (1900.0, 15.0), (1905.0, 1060.0), (12.0, 1055.0)]

    def test_a_fresh_store_has_no_corner_points(self):
        self.assertIsNone(self.store().corner_points)

    def test_set_homography_without_corner_points_leaves_it_unset(self):
        # Every call in this file's own fixtures (calibrated_store()
        # included) omits it — that must not crash or fabricate a value.
        s = self.calibrated_store()
        self.assertIsNone(s.corner_points)

    def test_corner_points_are_recorded_verbatim(self):
        s = self.store()
        s.set_homography(CAM_TO_STAGE, corner_points=self.CORNERS)
        self.assertEqual(s.corner_points, self.CORNERS)

    def test_corner_points_persist_through_save_and_load(self):
        s = self.store()
        s.set_homography(CAM_TO_STAGE, corner_points=self.CORNERS)
        s.save_homography()
        again = self.store()
        self.assertEqual(again.corner_points, self.CORNERS)

    def test_a_second_solve_without_corner_points_clears_the_first(self):
        # set_homography records what it is given, not what it remembers —
        # a caller that solves some other way must not inherit a stale
        # seed from the manual-corners flow.
        s = self.store()
        s.set_homography(CAM_TO_STAGE, corner_points=self.CORNERS)
        s.set_homography(CAM_TO_STAGE)
        self.assertIsNone(s.corner_points)

    def test_a_corrupt_corners_field_is_dropped_not_fatal(self):
        atomicio.write_json(self.h_path, {
            "schema": 3, "H_cam_to_stage": CAM_TO_STAGE,
            "corners": "not a list of points",
        })
        s = self.store()
        self.assertTrue(s.has_homography)
        self.assertIsNone(s.corner_points)


class TestViewRotation(StoreCase):
    """The Setup tab's future Rotate control (drag-corner rebuild step 4).
    A display preference, not calibration data — its own file, its own
    default, and it must survive with no homography ever solved.
    """

    def test_the_default_is_180_degrees(self):
        # This rig's measured mount (CLAUDE.md's M4i) — nobody who never
        # touches Rotate should see a regression from before this existed.
        self.assertEqual(self.store().view_rotation_deg, 180)

    def test_the_default_holds_with_no_homography_at_all(self):
        s = self.store()
        self.assertFalse(s.has_homography)
        self.assertEqual(s.view_rotation_deg, 180)

    def test_set_view_rotation_persists_and_reloads(self):
        s = self.store()
        s.set_view_rotation(90)
        self.assertEqual(self.store().view_rotation_deg, 90)

    def test_set_view_rotation_survives_before_any_homography_exists(self):
        s = self.store()
        s.set_view_rotation(270)
        again = self.store()
        self.assertFalse(again.has_homography)
        self.assertEqual(again.view_rotation_deg, 270)

    def test_set_view_rotation_rejects_an_invalid_degree(self):
        s = self.store()
        with self.assertRaises(ValueError):
            s.set_view_rotation(45)
        # Refused, not defaulted-and-saved: the bad value must not reach
        # disk or replace the value already in memory.
        self.assertEqual(s.view_rotation_deg, 180)
        self.assertFalse(self.v_path.exists())

    def test_set_view_rotation_rejects_a_bool(self):
        # isinstance(True, int) is True in Python — the exact trap
        # _handle_manual_calibrate's own point validation guards against.
        s = self.store()
        with self.assertRaises(ValueError):
            s.set_view_rotation(True)

    def test_a_corrupt_view_rotation_file_falls_back_to_the_default(self):
        atomicio.write_json(self.v_path, {"view_rotation_deg": "sideways"})
        s = self.store()
        self.assertEqual(s.view_rotation_deg, 180)


class TestManualCorners(StoreCase):
    """The manual 4-corner calibration flow (replaces the dot-pattern solve
    for the table's own boundary): the operator clicks the table's 4 real
    corners on the live feed, in a fixed physical order, instead of a
    pattern being projected and detected.

    Same discipline as `test_geometry.py`'s `TestFit`: every homography here
    is a hand-written ground truth, "clicks" are produced by projecting the
    known stage corners *back* through its inverse, and the check is a probe
    point never in the click set — never a reprojection of the fitted points
    themselves (doc section 5.3's TRAP).
    """

    def _corners_stage(self):
        # front(near)/back(far) in the front-left/front-right/back-right/
        # back-left order `fit_from_corners` expects — near is the HIGH-y
        # edge, matching `_manual_corners_stage`'s own convention (which
        # matches `BIN_ORIGINS_MM`: far row at y_mm=177, near row at
        # y_mm=482). Mirrors `_manual_corners_stage` deliberately rather
        # than importing it, the same "independently computed reference"
        # discipline `test_geometry.py`'s TestFit already uses.
        w, h = gs.STAGE_SIZE
        return [(0.0, float(h)), (float(w), float(h)),
                (float(w), 0.0), (0.0, 0.0)]

    def _recovers(self, cam_to_stage):
        s = self.store()
        stage_to_cam = geometry.invert(cam_to_stage)
        clicks = [geometry.apply(stage_to_cam, p) for p in self._corners_stage()]
        fit = s.fit_from_corners(clicks)
        probe_stage = (700.0, 400.0)
        probe_cam = geometry.apply(stage_to_cam, probe_stage)
        got = geometry.apply(fit.h, probe_cam)
        self.assertAlmostEqual(got[0], probe_stage[0], places=2)
        self.assertAlmostEqual(got[1], probe_stage[1], places=2)

    def test_recovers_a_known_homography(self):
        self._recovers(CAM_TO_STAGE)

    def test_recovers_the_homography_through_a_180_degree_mount(self):
        # This rig's actual measured mount (test_dotcal.py's own
        # FLIPPED_CAM_TO_STAGE, commit b847c0f). A screen-position-based
        # ordering (geometry.order_quad) pairs every corner with its
        # opposite here and still reports zero error -- the exact bug this
        # design exists to avoid. fit_from_corners never reorders by screen
        # position; it trusts the click order it was given, so it must
        # still recover the true matrix even though the feed is upside
        # down.
        self._recovers([
            [-1.0, 0.0, 1920.0],
            [0.0, -1.0, 1080.0],
            [0.0, 0.0, 1.0],
        ])

    def test_wrong_point_count_is_refused(self):
        s = self.store()
        with self.assertRaises(geometry.GeometryError):
            s.fit_from_corners([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)])

    def test_collinear_clicks_are_refused(self):
        s = self.store()
        with self.assertRaises(geometry.GeometryError):
            s.fit_from_corners([(0.0, 0.0), (100.0, 0.0),
                                (200.0, 0.0), (300.0, 0.0)])

    def test_front_clicks_land_on_the_high_y_near_edge(self):
        # The bug M5's tracker found on the rig: a live hand tracked
        # through H_cam->stage came out vertically inverted, because this
        # near/far pairing was backwards. `_corners_stage`'s own
        # round-trip tests above pass regardless of which way this is
        # wired (they only check self-consistency), so this test checks
        # the actual semantic meaning instead: a click made at the front
        # (near, operator's own side) must fit to the HIGH-y stage edge,
        # matching `BIN_ORIGINS_MM` (far row y_mm=177, near row y_mm=482)
        # — never assert this from a reprojection of the fitted points
        # themselves (doc section 5.3's TRAP), so it goes through a probe
        # point near the front edge that was never one of the 4 clicks.
        s = self.store()
        stage_to_cam = geometry.invert(CAM_TO_STAGE)
        clicks = [geometry.apply(stage_to_cam, p) for p in self._corners_stage()]
        fit = s.fit_from_corners(clicks)
        w, h = gs.STAGE_SIZE
        near_probe_stage = (w / 2.0, h - 50.0)
        far_probe_stage = (w / 2.0, 50.0)
        near_probe_cam = geometry.apply(stage_to_cam, near_probe_stage)
        far_probe_cam = geometry.apply(stage_to_cam, far_probe_stage)
        got_near = geometry.apply(fit.h, near_probe_cam)
        got_far = geometry.apply(fit.h, far_probe_cam)
        self.assertGreater(got_near[1], h / 2.0)
        self.assertLess(got_far[1], h / 2.0)

    def test_the_fit_is_returned_unsaved(self):
        # Matches core/dotcal.py's own split: fit_from_corners only
        # computes. Installing it is set_homography()'s job, so a caller
        # that never calls it must not find the store calibrated.
        s = self.store()
        stage_to_cam = geometry.invert(CAM_TO_STAGE)
        clicks = [geometry.apply(stage_to_cam, p) for p in self._corners_stage()]
        s.fit_from_corners(clicks)
        self.assertFalse(s.has_homography)


class TestKeystoneStaleness(StoreCase):
    """Doc section 8.5: "oF reports its fingerprint in `stat`; if it
    differs from the one recorded here, core raises 'calibration stale —
    keystone changed'."
    """

    def test_a_changed_fingerprint_is_stale(self):
        self.assertTrue(self.calibrated_store().keystone_is_stale("different"))

    def test_the_same_fingerprint_is_not_stale(self):
        self.assertFalse(self.calibrated_store().keystone_is_stale("abc123"))

    def test_an_unknown_live_fingerprint_is_not_stale(self):
        # oF has not connected yet. Shouting "calibration stale" every time
        # the table is slow to start teaches the operator to ignore it.
        s = self.calibrated_store()
        self.assertFalse(s.keystone_is_stale(None))
        self.assertFalse(s.keystone_is_stale(""))

    def test_an_uncalibrated_table_is_not_stale(self):
        self.assertFalse(self.store().keystone_is_stale("anything"))


class TestNoVerifyMethod(StoreCase):
    """Doc section 5.3's TRAP, restated as a guard rather than left to
    hope: nothing in this module may claim to check a homography's
    direction in code.
    """

    def test_the_store_has_no_verify_method(self):
        for name in ("verify", "check_homography", "verify_rects",
                     "self_check"):
            self.assertFalse(hasattr(gs.GeometryStore, name),
                             f"GeometryStore grew a {name}() — doc section "
                             "5.3's TRAP says this cannot be verified in code")


class TestTableGeometryMirror(unittest.TestCase):
    """The C++ header `TableGeometry.h` holds the same numbers and enforces
    them with `static_assert`. These are the Python half of that pair — an
    edit made on one side and not the other fails here rather than moving
    four trays 50 mm on the rig.

    `bin_grid.cad_bin_grid_stage()`'s own tests (`test_bin_grid.py`) cover
    the rects these numbers imply; this class only covers the mm chain
    itself, which both that module and `TableGeometry.h` derive from.
    """

    def test_the_x_chain_spans_the_table(self):
        # 92 + 200 + 50 + 200 + 440 + 200 + 50 + 200 + 92 = 1524
        left = gs.BIN_ORIGINS_MM[0][0]
        right_edge = gs.BIN_ORIGINS_MM[3][0] + gs.BIN_W_MM
        self.assertAlmostEqual(left, 92.0)
        self.assertAlmostEqual(right_edge + 92.0, gs.TABLE_W_MM, places=2)

    def test_the_y_chain_spans_the_table(self):
        # 177 + 255 + 50 + 255 + 177.4 = 914.4
        far_top = gs.BIN_ORIGINS_MM[0][1]
        near_bottom = gs.BIN_ORIGINS_MM[4][1] + gs.BIN_H_MM
        self.assertAlmostEqual(far_top, 177.0)
        self.assertAlmostEqual(near_bottom + 177.4, gs.TABLE_H_MM, places=2)

    def test_the_pot_gap_is_440mm(self):
        gap = gs.BIN_ORIGINS_MM[2][0] - (gs.BIN_ORIGINS_MM[1][0] + gs.BIN_W_MM)
        self.assertAlmostEqual(gap, 440.0)

    def test_the_row_gap_is_50mm(self):
        gap = gs.BIN_ORIGINS_MM[4][1] - (gs.BIN_ORIGINS_MM[0][1] + gs.BIN_H_MM)
        self.assertAlmostEqual(gap, 50.0)

    def test_mm_to_stage_uses_two_different_scales(self):
        # The table's aspect (1.667) is not the projector's (1.778). One
        # uniform scale would put the near row ~50 mm out.
        x, _ = gs.mm_to_stage(100.0, 0.0)
        _, y = gs.mm_to_stage(0.0, 100.0)
        self.assertNotAlmostEqual(x, y, places=3)


if __name__ == "__main__":
    unittest.main()
