"""Tests for common/geometry.py — M4 build item 1 (doc sections 5, 8.5).

Run from the repo root:

    python -m unittest discover -s python/tests -v

**No camera and no projector.** Every homography here is either written
out by hand or built by projecting a known synthetic point set, the same
discipline `core/loadcell_cal.py`'s tests use for the other number in this
system that can silently go wrong: the maths has to be reachable from a
test with no hardware attached, because on a rig you cannot tell a wrong
answer from a right one by looking at it.

**Doc section 5.3's TRAP is what most of this file is written around.**
A test that fits a homography to points and then checks the fit reproduces
those points passes by construction — it is the definition of a fit. So
the tests below check the things that can actually be wrong: that a
deliberately mis-paired correspondence is *rejected* rather than absorbed,
that a known matrix produces known coordinates, and that a matrix that
maps a point to infinity raises instead of returning `inf`.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import geometry  # noqa: E402


IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

# A homography with real perspective in it, used as the "truth" the fit
# tests try to recover. Not identity and not a pure affine: the last row
# is what separates a homography from an affine transform, and a test set
# that leaves it at [0,0,1] would pass against code that only ever
# implemented an affine.
PERSPECTIVE = [
    [1.1, 0.05, 30.0],
    [-0.04, 1.2, -20.0],
    [0.00012, 0.00007, 1.0],
]


def project(h, points):
    return [geometry.apply(h, p) for p in points]


class TestApply(unittest.TestCase):

    def test_identity_leaves_a_point_alone(self):
        self.assertEqual(geometry.apply(IDENTITY, (12.0, 34.0)), (12.0, 34.0))

    def test_a_translation_moves_a_point_by_the_translation(self):
        h = [[1, 0, 100], [0, 1, -50], [0, 0, 1]]
        self.assertEqual(geometry.apply(h, (10, 10)), (110.0, -40.0))

    def test_the_last_row_actually_divides(self):
        # The whole difference between a homography and an affine
        # transform. w = 0.5 at x=y=0 halves nothing; at (100, 0) it is
        # 1 + 0.01*100 = 2, so x/w must be 50, not 100.
        h = [[1, 0, 0], [0, 1, 0], [0.01, 0, 1]]
        x, y = geometry.apply(h, (100.0, 0.0))
        self.assertAlmostEqual(x, 50.0)
        self.assertAlmostEqual(y, 0.0)

    def test_a_point_at_infinity_raises_rather_than_returning_inf(self):
        # w = 1 - 0.01*100 = 0. Returning inf here would put an inf into a
        # bin rect, which reaches oF as a NaN-shaped rectangle and draws
        # nothing, with no message anywhere saying why.
        h = [[1, 0, 0], [0, 1, 0], [-0.01, 0, 1]]
        with self.assertRaises(geometry.GeometryError):
            geometry.apply(h, (100.0, 0.0))


class TestApplyRect(unittest.TestCase):

    def test_identity_returns_the_same_rect(self):
        self.assertEqual(geometry.apply_rect(IDENTITY, (10, 20, 30, 40)),
                         (10.0, 20.0, 30.0, 40.0))

    def test_a_scale_scales_both_origin_and_size(self):
        h = [[2, 0, 0], [0, 3, 0], [0, 0, 1]]
        self.assertEqual(geometry.apply_rect(h, (10, 20, 30, 40)),
                         (20.0, 60.0, 60.0, 120.0))

    def test_a_rotated_rect_comes_back_as_its_bounding_box(self):
        # 90 degrees about the origin: (x, y) -> (-y, x). The bounding box
        # of the rotated rect is a real rect again, and it is the *box*,
        # not the quad — which is the documented behaviour, and the reason
        # a cutout patch is never smaller than the tray it lights.
        h = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        x, y, w, hh = geometry.apply_rect(h, (10, 20, 30, 40))
        self.assertAlmostEqual(x, -60.0)
        self.assertAlmostEqual(y, 10.0)
        self.assertAlmostEqual(w, 40.0)
        self.assertAlmostEqual(hh, 30.0)

    def test_a_sheared_rect_box_is_never_smaller_than_the_rect(self):
        box = geometry.apply_rect(PERSPECTIVE, (100, 100, 200, 200))
        corners = project(PERSPECTIVE, [(100, 100), (300, 100),
                                        (300, 300), (100, 300)])
        for cx, cy in corners:
            self.assertGreaterEqual(cx, box[0] - 1e-9)
            self.assertLessEqual(cx, box[0] + box[2] + 1e-9)
            self.assertGreaterEqual(cy, box[1] - 1e-9)
            self.assertLessEqual(cy, box[1] + box[3] + 1e-9)


class TestInvert(unittest.TestCase):

    def test_identity_inverts_to_identity(self):
        for row, want in zip(geometry.invert(IDENTITY), IDENTITY):
            for got, w in zip(row, want):
                self.assertAlmostEqual(got, w)

    def test_a_point_survives_a_round_trip(self):
        inv = geometry.invert(PERSPECTIVE)
        x, y = geometry.apply(inv, geometry.apply(PERSPECTIVE, (640.0, 360.0)))
        self.assertAlmostEqual(x, 640.0, places=6)
        self.assertAlmostEqual(y, 360.0, places=6)

    def test_a_singular_matrix_raises(self):
        # Two identical rows: determinant 0. Silently returning a matrix
        # full of inf here would be written to state/homography.json.
        with self.assertRaises(geometry.GeometryError):
            geometry.invert([[1, 2, 3], [1, 2, 3], [4, 5, 6]])

    def test_the_inverse_is_normalised_so_the_corner_is_one(self):
        inv = geometry.invert(PERSPECTIVE)
        self.assertAlmostEqual(inv[2][2], 1.0)


class TestRms(unittest.TestCase):

    def test_a_perfect_correspondence_set_measures_zero(self):
        src = [(0, 0), (100, 0), (100, 100), (0, 100), (50, 50)]
        dst = project(PERSPECTIVE, src)
        self.assertAlmostEqual(geometry.rms_px(PERSPECTIVE, src, dst), 0.0,
                               places=9)

    def test_one_point_off_by_a_known_amount_shows_up_scaled_by_root_n(self):
        # Four points, one of them 4 px out: rms = sqrt(16/4) = 2.
        src = [(0, 0), (10, 0), (10, 10), (0, 10)]
        dst = list(src)
        dst[2] = (14.0, 10.0)
        self.assertAlmostEqual(geometry.rms_px(IDENTITY, src, dst), 2.0)

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(geometry.GeometryError):
            geometry.rms_px(IDENTITY, [(0, 0)], [(0, 0), (1, 1)])

    def test_no_points_raises_rather_than_reporting_a_perfect_fit(self):
        # 0/0 would be a ZeroDivisionError at best and "rms 0.0, excellent"
        # at worst — which is what the staff view would then print.
        with self.assertRaises(geometry.GeometryError):
            geometry.rms_px(IDENTITY, [], [])


class TestWarpFrameToStage(unittest.TestCase):
    """Needs OpenCV and numpy, same note as `TestFit` below."""

    def test_size_is_width_height_not_the_frame_s_own_shape(self):
        # A 40 (wide) x 20 (tall) source, warped by identity into a 100x50
        # canvas. If `size` were quietly treated as (rows, cols) instead of
        # cv2's own (width, height), the output would come back 50x100 and
        # this shape check would catch it before any pixel content does.
        import numpy as np
        frame = np.zeros((20, 40, 3), dtype=np.uint8)
        out = geometry.warp_frame_to_stage(frame, IDENTITY, (100, 50))
        self.assertEqual(out.shape[:2], (50, 100))

    def test_identity_places_the_source_at_the_origin_unscaled(self):
        import numpy as np
        frame = np.zeros((20, 40, 3), dtype=np.uint8)
        frame[5, 10] = (1, 2, 3)
        out = geometry.warp_frame_to_stage(frame, IDENTITY, (40, 20))
        self.assertEqual(tuple(out[5, 10]), (1, 2, 3))

    def test_a_translation_moves_the_content_by_the_translation(self):
        import numpy as np
        h = [[1, 0, 10], [0, 1, 5], [0, 0, 1]]
        frame = np.zeros((20, 40, 3), dtype=np.uint8)
        frame[5, 10] = (9, 9, 9)
        out = geometry.warp_frame_to_stage(frame, h, (60, 30))
        self.assertEqual(tuple(out[10, 20]), (9, 9, 9))


class TestFit(unittest.TestCase):
    """These need OpenCV. It is already a hard dependency of this repo
    (`opencv-python-headless`, added at M3.2 for the camera), so this is
    not an optional path — but note that everything above this class runs
    without it.
    """

    # A synthetic dot grid in stage space, the same shape M4's calibration
    # actually draws: 5 columns by 3 rows, inset from the stage edges.
    STAGE_DOTS = [(x, y)
                  for y in (100.0, 540.0, 980.0)
                  for x in (164.0, 562.0, 960.0, 1358.0, 1756.0)]

    def camera_dots(self, h=None):
        """Where those stage dots land in camera space — i.e. the points a
        camera would report. The fit then has to recover stage from camera,
        which is the direction `H_cam->stage` actually runs.
        """
        forward = h or PERSPECTIVE
        return project(geometry.invert(forward), self.STAGE_DOTS)

    def test_a_clean_grid_recovers_the_matrix_that_generated_it(self):
        cam = self.camera_dots()
        f = geometry.fit(cam, self.STAGE_DOTS)
        self.assertLess(f.rms_px, 0.5)
        self.assertEqual(f.n_inliers, len(self.STAGE_DOTS))
        # Not "the matrix is equal" — a homography is only defined up to
        # scale, so two correct answers can differ elementwise. What must
        # match is where it sends a camera point that was NOT one of the
        # fifteen it was fitted from. Checking a fitted point instead would
        # be the TRAP: that one passes by construction.
        probe_cam = (700.0, 400.0)
        want = geometry.apply(PERSPECTIVE, probe_cam)
        got = geometry.apply(f.h, probe_cam)
        self.assertAlmostEqual(got[0], want[0], places=2)
        self.assertAlmostEqual(got[1], want[1], places=2)

    def test_one_mis_paired_dot_is_dropped_rather_than_absorbed(self):
        # THE failure that actually happens on a rig: one dot paired with
        # the wrong expected position (a reflection, a tray highlight, an
        # off-by-one row). Least squares would spread it over all fifteen
        # points and quietly move every bin rect; RANSAC must drop it.
        cam = self.camera_dots()
        cam[7] = (cam[7][0] + 120.0, cam[7][1] - 90.0)
        f = geometry.fit(cam, self.STAGE_DOTS)
        self.assertFalse(f.inliers[7])
        self.assertEqual(f.n_inliers, len(self.STAGE_DOTS) - 1)
        self.assertLess(f.rms_px, 1.0)

    def test_fewer_than_four_points_raises(self):
        with self.assertRaises(geometry.GeometryError):
            geometry.fit([(0, 0), (1, 0), (0, 1)], [(0, 0), (1, 0), (0, 1)])

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(geometry.GeometryError):
            geometry.fit([(0, 0), (1, 0), (0, 1), (1, 1)], [(0, 0)])

    def test_four_collinear_points_raise_rather_than_returning_a_matrix(self):
        # cv2 returns (None, None) here. A caller that took that as "no
        # homography today" would write null into state/homography.json.
        line = [(0.0, 0.0), (10.0, 10.0), (20.0, 20.0), (30.0, 30.0)]
        with self.assertRaises(geometry.GeometryError):
            geometry.fit(line, line)

    def test_rms_is_measured_over_inliers_only(self):
        # With the outlier included, rms would be dominated by it (~38 px
        # over 15 points). Over the inliers it is sub-pixel. The number the
        # staff view prints has to be the second one, or every rig with one
        # bad dot reads as a failed calibration.
        cam = self.camera_dots()
        cam[3] = (cam[3][0] + 150.0, cam[3][1])
        f = geometry.fit(cam, self.STAGE_DOTS)
        self.assertLess(f.rms_px, 1.0)
        naive = geometry.rms_px(f.h, cam, self.STAGE_DOTS)
        self.assertGreater(naive, 10.0)


class TestOrderQuad(unittest.TestCase):

    def test_points_in_any_input_order_come_back_tl_tr_br_bl(self):
        tl, tr, br, bl = (10.0, 20.0), (900.0, 30.0), (890.0, 700.0), (20.0, 690.0)
        for shuffled in ([tl, tr, br, bl], [br, bl, tl, tr], [tr, br, bl, tl]):
            self.assertEqual(geometry.order_quad(shuffled), [tl, tr, br, bl])

    def test_a_moderately_rotated_quad_still_orders_correctly(self):
        # ~20 degrees. The documented limit is about 45; this checks the
        # method survives a real-world mounting that is not quite square,
        # which is what a camera on a bracket actually looks like.
        cx, cy, r = 500.0, 400.0, 300.0
        pts = []
        for deg in (225, 315, 45, 135):     # tl, tr, br, bl before rotation
            a = math.radians(deg + 20)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        ordered = geometry.order_quad([pts[2], pts[0], pts[3], pts[1]])
        self.assertEqual(ordered, pts)

    def test_wrong_count_raises(self):
        with self.assertRaises(geometry.GeometryError):
            geometry.order_quad([(0, 0), (1, 1), (2, 2)])

    def test_two_dots_in_the_same_corner_raise(self):
        # A duplicate means detection found the same blob twice or missed
        # one entirely; averaging it into a corner would produce a fit that
        # looks fine and is rotated.
        with self.assertRaises(geometry.GeometryError):
            geometry.order_quad([(0, 0), (0, 0), (100, 0), (100, 100)])


class TestMatchNearest(unittest.TestCase):

    def test_each_expected_point_takes_its_own_nearest(self):
        expected = [(0, 0), (100, 0), (0, 100)]
        found = [(101, 2), (-1, 99), (2, -3)]
        self.assertEqual(geometry.match_nearest(expected, found,
                                                max_distance_px=20),
                         [2, 0, 1])

    def test_a_point_beyond_the_gate_is_left_unmatched(self):
        got = geometry.match_nearest([(0, 0), (500, 500)], [(1, 1)],
                                     max_distance_px=20)
        self.assertEqual(got, [0, None])

    def test_the_closer_claim_wins_when_two_expected_points_want_one_dot(self):
        # Greedy in list order would give the dot to expected[0] at 10 px
        # and leave expected[1] — 1 px away — unmatched. Greedy in distance
        # order gives it to the one that actually fits.
        got = geometry.match_nearest([(0, 0), (10, 0)], [(11, 0)],
                                     max_distance_px=20)
        self.assertEqual(got, [None, 0])

    def test_a_detected_point_is_never_used_twice(self):
        got = geometry.match_nearest([(0, 0), (1, 0)], [(0, 0)],
                                     max_distance_px=20)
        self.assertEqual(sorted(v for v in got if v is not None), [0])

    def test_a_per_point_gate_list_is_honoured_per_point(self):
        # RIG_FEEDBACK item 11: expected[0]'s own gate (5px) is too tight
        # for its 10px-away match, but expected[1]'s (20px) is not — a
        # single shared number could not express that.
        expected = [(0, 0), (100, 0)]
        found = [(10, 0), (110, 0)]
        got = geometry.match_nearest(expected, found,
                                     max_distance_px=[5.0, 20.0])
        self.assertEqual(got, [None, 1])

    def test_a_scalar_gate_still_applies_to_every_point(self):
        # The single-float call shape existing callers use must keep
        # working identically once a sequence is also accepted.
        expected = [(0, 0), (100, 0)]
        found = [(10, 0), (110, 0)]
        got = geometry.match_nearest(expected, found, max_distance_px=20.0)
        self.assertEqual(got, [0, 1])

    def test_a_mismatched_gate_list_length_raises(self):
        with self.assertRaises(ValueError):
            geometry.match_nearest([(0, 0), (100, 0)], [(0, 0)],
                                   max_distance_px=[20.0])


if __name__ == "__main__":
    unittest.main()
