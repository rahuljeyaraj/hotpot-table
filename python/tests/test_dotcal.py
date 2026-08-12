"""Tests for core/dotcal.py — the dot calibration wizard (doc section 21
M4 build item 3; doc sections 5.2, 8.5, 12.6, 24.1).

Run from the repo root:

    python -m unittest discover -s python/tests -v

**No projector, no camera, no classifier process.** `DotCalibrator` takes
`show_dots` and `ask_dots` as callables precisely so the whole two-pass
sequence can be driven by a fake camera that is nothing but a homography:
`FakeRig` below remembers the pattern core asked to draw, projects it
through a known stage->camera matrix, and hands the points back as if a
classifier had found them. That means the test can then check the solve
recovered the matrix the fake rig was built from — a reference the code
never saw, which is the whole difference between this and doc section
5.3's TRAP.

The tests that matter most are the failure ones: a rotated camera (which
is what breaks the single-pass row-sorting design this rejected), a
missing dot, a spurious reflection, and a classifier that does not answer.
"""

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import geometry  # noqa: E402
from hotpot.core import dotcal  # noqa: E402
from hotpot.core import geometry_store as gstore  # noqa: E402


# The "truth" a fake rig is built from: camera -> stage.
CAM_TO_STAGE = [
    [1.1, 0.05, 30.0],
    [-0.04, 1.2, -20.0],
    [0.00012, 0.00007, 1.0],
]

# A camera mounted ~25 degrees off square. This is the case the two-pass
# design exists for: sort a 5x3 grid row-major by y under this and the
# rows interleave, the pairing goes off by one, and the fit still reports
# a beautiful RMS.
ROTATED_CAM_TO_STAGE = [
    [0.906, -0.423, 300.0],
    [0.423, 0.906, -150.0],
    [0.00004, 0.00002, 1.0],
]

# A camera mounted UPSIDE DOWN, which is how this rig's actually is
# (measured at 180 degrees, commit b847c0f, 2026-08-08). Exactly 180, not
# approximately: this is the one angle where the four corners map onto the
# positions of the opposite four, so ordering them by their place in the
# camera image pairs every one of them with its opposite and the resulting
# homography still fits perfectly. It is the case that cannot be caught by
# looking at the error, which is why the marker dot exists.
FLIPPED_CAM_TO_STAGE = [
    [-1.0, 0.0, 1920.0],
    [0.0, -1.0, 1080.0],
    [0.0, 0.0, 1.0],
]


class FakeRig:
    """A projector and a camera that agree perfectly, made of one matrix.

    `show()` records what core asked to draw; `ask()` projects those same
    stage points into camera space and returns them **shuffled**, because
    a real classifier returns blobs largest-first, not in pattern order.
    Shuffling is what makes the ordering step load-bearing: a version that
    accidentally relied on the reply arriving in pattern order would pass
    against an unshuffled fake and fail on a rig.
    """

    def __init__(self, cam_to_stage=None, jitter=0.0):
        self.cam_to_stage = cam_to_stage or CAM_TO_STAGE
        self.stage_to_cam = geometry.invert(self.cam_to_stage)
        self.jitter = jitter
        self.shown = []
        self.asks = []
        self.drop_indices = set()
        # pass index (0 = coarse, 1 = fine) -> extra blobs the "camera"
        # reports that were never drawn. Per-pass rather than global
        # because the two passes fail differently: a stray blob in the
        # coarse pass corrupts the ordering, one in the fine pass is
        # simply never matched.
        self.extra_points = {}
        self.reply_override = None

    def show(self, dots):
        self.shown.append(dots)

    def ask(self, expect, min_area):
        self.asks.append((expect, min_area))
        if self.reply_override is not None:
            return self.reply_override
        pattern = self.shown[-1] or []
        points = []
        areas = []
        for i, (x, y, r) in enumerate(pattern):
            if i in self.drop_indices:
                continue
            cx, cy = geometry.apply(self.stage_to_cam, (x, y))
            if self.jitter:
                # Deterministic, sign-alternating: a fixed offset would be
                # absorbed by the fit as a translation and prove nothing.
                cx += self.jitter * (1 if i % 2 else -1)
                cy += self.jitter * (1 if i % 3 else -1)
            points.append([cx, cy])
            # Area from the radius core actually asked for, so the oversized
            # marker comes back oversized the way a real camera would report
            # it. pi*r^2 rather than a flat "bigger" constant: the ratio the
            # marker is identified by is then the real one the pattern
            # implies, so a test cannot pass on a marker that is only
            # nominally larger.
            areas.append(math.pi * float(r) * float(r))
        for extra in self.extra_points.get(len(self.asks) - 1, []):
            points.append(extra)
            # A stray reflection is dot-sized, not marker-sized — an extra
            # blob that outweighed the marker would be testing something
            # else entirely.
            areas.append(math.pi * dotcal.DOT_RADIUS_PX ** 2)
        # Reversed, not shuffled: deterministic, and still not pattern
        # order, which is the property that matters. Areas travel with their
        # points; a reply whose two lists disagreed would mis-identify the
        # marker, which is the one thing this fixture must not do by
        # accident.
        return {"t": "dots", "points": list(reversed(points)),
                "areas": list(reversed(areas))}


class DotCalCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.h_path = Path(self.dir.name) / "homography.json"
        self.r_path = Path(self.dir.name) / "bin_rects.json"
        self.store = gstore.GeometryStore(homography_path=self.h_path,
                                          rects_path=self.r_path)

    def calibrator(self, rig, **cfg):
        return dotcal.DotCalibrator(self.store, show_dots=rig.show,
                                    ask_dots=rig.ask, cfg=cfg or None,
                                    settle_s=0.0, sleep=lambda s: None)


class TestPattern(unittest.TestCase):
    """Doc section 24.1's decision, and the geometry it was derived from.

    These are the checks that would catch someone widening the grid or
    moving a row onto a tray — which is invisible until the rig produces a
    homography that is 19 px out for a reason nobody can find.
    """

    def test_the_default_grid_is_five_by_three(self):
        self.assertEqual(len(dotcal.grid_points()), 15)

    def test_no_row_lands_on_a_bin_cutout(self):
        # THE constraint. A dot on a tray is displaced by
        # height/tan(elevation) — 15 mm at I10's worst allowed angle for a
        # tray 40 mm down, which is six times the whole error budget.
        # Bands, in stage px, that contain a bin once TableGeometry.h's
        # 10 mm cutout margin is counted.
        cutout_bands = []
        for (_x_mm, y_mm) in gstore.BIN_ORIGINS_MM:
            top = gstore.mm_to_stage(0.0, y_mm - 10.0)[1]
            bottom = gstore.mm_to_stage(0.0, y_mm + gstore.BIN_H_MM + 10.0)[1]
            cutout_bands.append((top, bottom))
        radius = dotcal.DOT_RADIUS_PX
        for (_x, y) in dotcal.grid_points():
            for top, bottom in cutout_bands:
                self.assertFalse(top < y + radius and y - radius < bottom,
                                 f"a dot at y={y:.0f} overlaps a bin cutout "
                                 f"band {top:.0f}..{bottom:.0f}")

    def test_the_corner_dots_avoid_the_cutouts_too(self):
        radius = dotcal.CORNER_DOT_RADIUS_PX
        far_top = gstore.mm_to_stage(0.0, gstore.BIN_ORIGINS_MM[0][1] - 10.0)[1]
        near_bottom = gstore.mm_to_stage(
            0.0, gstore.BIN_ORIGINS_MM[4][1] + gstore.BIN_H_MM + 10.0)[1]
        for (_x, y) in dotcal.corner_points():
            self.assertTrue(y + radius < far_top or y - radius > near_bottom)

    def test_every_dot_is_inside_the_stage(self):
        for (x, y) in dotcal.grid_points() + dotcal.corner_points():
            self.assertGreater(x, 0)
            self.assertGreater(y, 0)
            self.assertLess(x, gstore.STAGE_SIZE[0])
            self.assertLess(y, gstore.STAGE_SIZE[1])

    def test_dots_are_far_enough_apart_not_to_merge(self):
        # Two dots that merge under projector defocus come back as ONE
        # blob in the wrong place — see test_dots.py. Ten radii of
        # separation is a wide margin.
        pts = dotcal.grid_points()
        worst = min(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                    for i, a in enumerate(pts) for b in pts[i + 1:])
        self.assertGreater(worst, 10 * dotcal.DOT_RADIUS_PX)

    def test_the_corners_are_the_corners_of_the_grid(self):
        # The coarse pass has to bracket the fine one, or the coarse
        # homography is extrapolating where the grid actually is.
        grid = dotcal.grid_points()
        xs = [p[0] for p in grid]
        ys = [p[1] for p in grid]
        for (x, y) in dotcal.corner_points():
            self.assertIn(round(x, 3), [round(v, 3) for v in (min(xs), max(xs))])
            self.assertIn(round(y, 3), [round(v, 3) for v in (min(ys), max(ys))])

    def test_corner_points_are_tl_tr_br_bl(self):
        # Positional pairing with geometry.order_quad's output. Get this
        # order wrong and the whole solve is rotated, with a perfect RMS.
        tl, tr, br, bl = dotcal.corner_points()
        self.assertLess(tl[0], tr[0])
        self.assertLess(tl[1], bl[1])
        self.assertGreater(br[0], bl[0])
        self.assertGreater(br[1], tr[1])

    def test_the_grid_can_be_narrowed_to_two_rows_by_config(self):
        # The middle row's band is only 30 mm tall — the documented thing
        # to check on the rig. Dropping to two rows must be an edit.
        self.assertEqual(len(dotcal.grid_points(cols=5, rows=2)), 10)

    def test_the_overlay_payload_is_x_y_r_in_stage_space(self):
        payload = dotcal.overlay_dots(dotcal.corner_points(), 24.0)
        self.assertEqual(len(payload), 4)
        self.assertEqual(len(payload[0]), 3)
        self.assertEqual(payload[0][2], 24.0)


class TestTheSolve(DotCalCase):

    def test_a_clean_rig_recovers_the_matrix_it_was_built_from(self):
        # The reference is the fake rig's own matrix, which the code under
        # test never saw — NOT a reprojection of the fit's own points.
        # That distinction is doc section 5.3's TRAP.
        rig = FakeRig()
        result = self.calibrator(rig).run()
        self.assertTrue(result.good)
        self.assertLess(result.rms_px, 0.5)
        probe = (700.0, 400.0)
        want = geometry.apply(CAM_TO_STAGE, probe)
        got = geometry.apply(result.h, probe)
        self.assertAlmostEqual(got[0], want[0], places=1)
        self.assertAlmostEqual(got[1], want[1], places=1)

    def test_it_runs_two_passes_with_the_right_dot_counts(self):
        rig = FakeRig()
        self.calibrator(rig).run()
        self.assertEqual(len(rig.asks), 2)
        self.assertEqual(rig.asks[0][0], 4)
        self.assertEqual(rig.asks[1][0], 15)
        self.assertEqual(len(rig.shown[0]), 4)
        self.assertEqual(len(rig.shown[1]), 15)

    def test_the_corner_dots_are_drawn_larger_than_the_grid_dots(self):
        rig = FakeRig()
        self.calibrator(rig).run()
        self.assertGreater(rig.shown[0][0][2], rig.shown[1][0][2])

    def test_a_rotated_camera_still_solves(self):
        # The reason there are two passes at all. Under 25 degrees of
        # rotation a row-major sort of the fine grid interleaves the rows
        # and pairs off by one — and the resulting fit reports an
        # excellent RMS while putting every rect in the wrong place.
        rig = FakeRig(ROTATED_CAM_TO_STAGE)
        result = self.calibrator(rig).run()
        self.assertLess(result.rms_px, 0.5)
        probe = (700.0, 400.0)
        want = geometry.apply(ROTATED_CAM_TO_STAGE, probe)
        got = geometry.apply(result.h, probe)
        self.assertAlmostEqual(got[0], want[0], places=1)
        self.assertAlmostEqual(got[1], want[1], places=1)

    def test_an_upside_down_camera_solves_the_right_way_up(self):
        """The 180-degree trap, and the only test here that a low RMS
        cannot pass by itself.

        A flipped solve fits its own correspondences EXACTLY — four points
        always do — so `rms_px` is ~0 whether the pairing is right or
        inverted, and asserting on it would prove nothing. The check has to
        be a probe point the solver never saw, compared against the rig's
        own matrix. Under the old `order_quad` pairing this returns the
        point rotated about the table centre: (1220, 680) instead of
        (700, 400), which is a whole table away and the exact error a human
        would have found at the Verify step and nowhere earlier.
        """
        rig = FakeRig(FLIPPED_CAM_TO_STAGE)
        result = self.calibrator(rig).run()
        probe = (700.0, 400.0)
        want = geometry.apply(FLIPPED_CAM_TO_STAGE, probe)
        got = geometry.apply(result.h, probe)
        self.assertAlmostEqual(got[0], want[0], places=1)
        self.assertAlmostEqual(got[1], want[1], places=1)

    def test_the_marker_corner_is_drawn_larger_than_the_other_corners(self):
        # The mechanism the test above depends on. If the marker is not
        # physically bigger there is nothing to identify it by, and the
        # flip becomes undetectable again.
        rig = FakeRig()
        self.calibrator(rig).run()
        marker_r = rig.shown[0][0][2]
        other_r = [d[2] for d in rig.shown[0][1:]]
        self.assertTrue(all(marker_r > r for r in other_r))
        # And by enough to survive identify_marker's ratio test.
        self.assertGreaterEqual((marker_r / max(other_r)) ** 2,
                                geometry.DEFAULT_MIN_MARKER_RATIO)

    def test_corner_dots_of_equal_size_are_refused_not_guessed(self):
        # Orientation must never fall back to "pick one". A pattern with no
        # marker is a pattern whose orientation is unknowable, and the
        # honest answer is a refusal the operator can act on.
        rig = FakeRig()
        cal = self.calibrator(rig, marker_dot_radius_px=dotcal.CORNER_DOT_RADIUS_PX)
        with self.assertRaises(dotcal.DotCalError) as ctx:
            cal.run()
        self.assertIn("marker", str(ctx.exception))

    def test_a_reply_in_a_different_order_does_not_matter(self):
        # FakeRig reverses every reply. If it did not, a bug that assumed
        # pattern order would pass here and fail on the rig.
        rig = FakeRig()
        self.assertLess(self.calibrator(rig).run().rms_px, 0.5)

    def test_the_overlay_is_taken_down_afterwards(self):
        rig = FakeRig()
        self.calibrator(rig).run()
        self.assertIsNone(rig.shown[-1])

    def test_the_overlay_is_taken_down_even_when_the_solve_fails(self):
        # A failed solve that left the table black with white dots on it
        # looks exactly like a crashed renderer, and the operator's next
        # move is to restart something.
        rig = FakeRig()
        rig.reply_override = {"ok": False, "error": "no frames"}
        with self.assertRaises(dotcal.DotCalError):
            self.calibrator(rig).run()
        self.assertIsNone(rig.shown[-1])


class TestDegradedRigs(DotCalCase):

    def test_a_missing_grid_dot_costs_one_correspondence_not_the_solve(self):
        rig = FakeRig()
        rig.drop_indices = {7}
        result = self.calibrator(rig).run()
        self.assertLess(result.rms_px, 0.5)
        self.assertEqual(result.n_inliers, 14)
        self.assertIn("1 not found", result.message)

    def test_a_spurious_reflection_in_the_fine_pass_is_ignored(self):
        # A blob nowhere near any expected position never gets matched,
        # because pairing is nearest-neighbour against the coarse fit's
        # projection, not a positional zip.
        rig = FakeRig()
        rig.extra_points = {1: [[5.0, 5.0]]}
        result = self.calibrator(rig).run()
        self.assertLess(result.rms_px, 0.5)
        self.assertEqual(result.n_inliers, 15)

    def test_a_missing_corner_dot_fails_with_a_sentence(self):
        rig = FakeRig()
        rig.drop_indices = {0}
        with self.assertRaises(dotcal.DotCalError) as ctx:
            self.calibrator(rig).run()
        self.assertIn("3 of 4 corner dots", str(ctx.exception))

    def test_a_fifth_corner_blob_is_salvaged_by_taking_the_largest(self):
        # The stray is dot-sized and the four real corner blobs are bigger,
        # so area alone separates them. Deliberately NOT a statement about
        # where the stray sits in the reply: the salvage picks by measured
        # area precisely so the reply's order cannot decide which blob
        # survives, and FakeRig reverses its reply to keep that honest.
        rig = FakeRig()
        rig.extra_points = {0: [[3.0, 3.0]]}
        rig.show([])
        result = self.calibrator(rig).run()
        self.assertLess(result.rms_px, 0.5)

    def test_a_classifier_that_does_not_answer_fails_with_a_sentence(self):
        rig = FakeRig()
        rig.reply_override = None

        def silent(expect, min_area):
            return None
        cal = dotcal.DotCalibrator(self.store, show_dots=rig.show,
                                   ask_dots=silent, settle_s=0.0,
                                   sleep=lambda s: None)
        with self.assertRaises(dotcal.DotCalError) as ctx:
            cal.run()
        self.assertIn("is it running", str(ctx.exception))

    def test_a_classifier_error_reaches_the_operator_verbatim(self):
        rig = FakeRig()
        rig.reply_override = {"ok": False,
                              "error": "the camera stopped sending frames"}
        with self.assertRaises(dotcal.DotCalError) as ctx:
            self.calibrator(rig).run()
        self.assertIn("stopped sending frames", str(ctx.exception))

    def test_a_noisy_rig_is_refused_even_when_ransac_reports_a_low_rms(self):
        # **Found by this test, not reasoned out in advance.** With 6 px
        # of centroid jitter against a 3 px RANSAC threshold, RANSAC does
        # its job: it finds the largest subset agreeing to within 3 px and
        # reports a beautiful sub-pixel RMS over it — which was five of
        # fifteen dots. That would have passed doc section 21's "under
        # ~3 px" acceptance while being the worst solve the rig could
        # produce. The verdict needs BOTH numbers.
        rig = FakeRig(jitter=6.0)
        result = self.calibrator(rig).run()
        self.assertFalse(result.good)
        self.assertLess(result.n_inliers, 11)
        self.assertIn("too few dots", result.message)

    def test_a_verdict_needs_the_dot_count_as_well_as_the_error(self):
        # The same rule stated from the other side: an rms under the
        # threshold is not on its own enough to say "looks good".
        rig = FakeRig(jitter=6.0)
        result = self.calibrator(rig).run()
        self.assertLessEqual(result.rms_px, 3.0)
        self.assertFalse(result.good)

    def test_a_blank_field_fails_rather_than_solving_from_nothing(self):
        rig = FakeRig()
        rig.reply_override = {"t": "dots", "points": []}
        with self.assertRaises(dotcal.DotCalError):
            self.calibrator(rig).run()

    def test_two_calibrations_at_once_are_refused(self):
        rig = FakeRig()
        cal = self.calibrator(rig)
        seen = []

        def reentrant(expect, min_area):
            if not seen:
                seen.append(1)
                with self.assertRaises(dotcal.DotCalError):
                    cal.run()
            return rig.ask(expect, min_area)
        cal._ask = reentrant
        cal.run()
        self.assertTrue(seen)


class TestWhatItSaves(DotCalCase):

    def test_it_writes_doc_8_5s_file_through_the_store(self):
        rig = FakeRig()
        self.calibrator(rig).run(keystone_fingerprint="deadbeef",
                                 camera_size=(1280, 720))
        self.assertTrue(self.h_path.exists())
        again = gstore.GeometryStore(homography_path=self.h_path,
                                     rects_path=self.r_path)
        self.assertTrue(again.has_homography)
        self.assertEqual(again.keystone_fingerprint, "deadbeef")
        self.assertEqual(again.camera_size, (1280, 720))
        self.assertEqual(again.n_points, 15)

    def test_save_false_installs_without_writing(self):
        rig = FakeRig()
        self.calibrator(rig).run(save=False)
        self.assertTrue(self.store.has_homography)
        self.assertFalse(self.h_path.exists())

    def test_a_new_solve_clears_the_last_human_verify_answer(self):
        # Doc section 12.6: the rects have just moved under the operator's
        # last "yes, they sit on the trays".
        self.store.mark_verified()
        self.calibrator(FakeRig()).run()
        self.assertIsNone(self.store.verified_at)


if __name__ == "__main__":
    unittest.main()
