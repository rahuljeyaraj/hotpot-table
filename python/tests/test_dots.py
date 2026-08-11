"""Tests for classifier/dots.py — M4 build item 2 (doc sections 3.2, 4.7).

Run from the repo root:

    python -m unittest discover -s python/tests -v

**No camera and no projector.** Every frame here is a numpy array built
by `field()` and `draw_dot()` below — white discs on a black ground,
which is the I9 lighting inversion dot calibration runs under. That is
the same hardware-optional discipline `camera/capture.py` gets from a
fake `cv2.VideoCapture` and `core/scale.py` gets from `feed()`.

The interesting tests are the ones for things that are NOT dots: a
specular pinpoint, a tray reflection bigger than any dot, a sliver of
light along an edge, two dots that merged, and a dot cut by the frame
border. Each of those is a real thing a rig produces, and each would
otherwise reach the homography fit as a confident wrong point.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from hotpot.classifier import dots  # noqa: E402


def field(w=640, h=480, channels=3):
    """A black field — what the table looks like during dot calibration."""
    if channels == 1:
        return np.zeros((h, w), dtype=np.uint8)
    return np.zeros((h, w, channels), dtype=np.uint8)


def draw_dot(img, cx, cy, r, value=255):
    """A filled white disc, drawn by hand rather than with cv2 so the test
    fixture cannot share a bug with the code under test.
    """
    h, w = img.shape[:2]
    ys, xs = np.ogrid[:h, :w]
    inside = (xs - cx) ** 2 + (ys - cy) ** 2 <= r * r
    if img.ndim == 3:
        img[inside] = value
    else:
        img[inside] = value
    return img


def draw_box(img, x, y, bw, bh, value=255):
    img[y:y + bh, x:x + bw] = value
    return img


class TestFinding(unittest.TestCase):

    def test_one_dot_is_found_at_its_centre(self):
        img = draw_dot(field(), 320, 240, 12)
        found = dots.detect_dots(img)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].x, 320.0, delta=0.6)
        self.assertAlmostEqual(found[0].y, 240.0, delta=0.6)

    def test_a_grid_of_dots_is_all_found(self):
        img = field(1280, 720)
        want = [(x, y) for y in (120, 360, 600)
                for x in (160, 480, 800, 1120)]
        for x, y in want:
            draw_dot(img, x, y, 10)
        found = dots.detect_dots(img)
        self.assertEqual(len(found), len(want))
        got = sorted((round(d.x), round(d.y)) for d in found)
        self.assertEqual(got, sorted(want))

    def test_a_greyscale_frame_works_as_well_as_bgr(self):
        grey = draw_dot(field(channels=1), 100, 100, 10)
        bgr = draw_dot(field(), 100, 100, 10)
        self.assertEqual(len(dots.detect_dots(grey)),
                         len(dots.detect_dots(bgr)))

    def test_the_centroid_is_sub_pixel(self):
        # A disc centred on a half-pixel. A bounding-box centre would
        # quantise to whole pixels; the moment centroid does not, and the
        # "under ~3 px RMS" acceptance number depends on that.
        img = field()
        h, w = img.shape[:2]
        ys, xs = np.ogrid[:h, :w]
        img[(xs - 200.5) ** 2 + (ys - 150.5) ** 2 <= 144] = 255
        found = dots.detect_dots(img)
        self.assertEqual(len(found), 1)
        self.assertNotEqual(found[0].x, round(found[0].x))

    def test_a_speck_stuck_to_a_dot_barely_moves_the_centroid(self):
        # The argument for moments over bounding-box centres. The speck
        # reaches 20 px past the dot's edge, so a box centre would shift
        # by ~10 px; the moment centroid shifts by the speck's share of
        # the area, which is small.
        clean = draw_dot(field(), 320, 240, 14)
        dirty = draw_box(draw_dot(field(), 320, 240, 14), 334, 239, 20, 2)
        a = dots.detect_dots(clean)[0]
        b = dots.detect_dots(dirty)[0]
        self.assertLess(abs(a.x - b.x), 2.0)

    def test_dots_come_back_largest_first(self):
        img = field()
        draw_dot(img, 100, 100, 8)
        draw_dot(img, 300, 100, 20)
        draw_dot(img, 500, 100, 14)
        found = dots.detect_dots(img)
        self.assertEqual([round(d.x) for d in found], [300, 500, 100])


class TestRejecting(unittest.TestCase):
    """Each of these is a real thing a rig produces on an inverted field."""

    def test_a_specular_pinpoint_is_below_the_area_floor(self):
        img = draw_box(draw_dot(field(), 320, 240, 12), 100, 100, 3, 3)
        found = dots.detect_dots(img)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].x, 320.0, delta=1.0)

    def test_a_big_reflection_is_above_the_area_ceiling(self):
        # A steel tray catching the projector, or somebody turning the
        # room lights on mid-solve. Without a ceiling its centroid joins
        # the fit as a confident, wildly wrong point.
        img = draw_box(draw_dot(field(), 320, 240, 12), 20, 20, 200, 200)
        found = dots.detect_dots(img)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].x, 320.0, delta=1.0)

    def test_a_sliver_of_light_is_rejected_on_shape_not_size(self):
        # 200x4 = 800 px^2, comfortably inside the area window, and not a
        # dot. This is why there is an aspect test at all.
        img = draw_box(draw_dot(field(), 320, 240, 12), 40, 400, 200, 4)
        found = dots.detect_dots(img)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].x, 320.0, delta=1.0)

    def test_a_dot_touching_the_frame_edge_is_dropped(self):
        # Its centroid is pulled inward by the missing part, and nothing
        # about the blob shows that: right area, right shape, wrong
        # position. One correspondence lost beats a biased solve.
        img = field()
        draw_dot(img, 320, 240, 12)
        draw_dot(img, 2, 240, 12)
        found = dots.detect_dots(img)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].x, 320.0, delta=1.0)

    def test_an_edge_dot_can_be_kept_deliberately(self):
        img = draw_dot(field(), 2, 240, 12)
        self.assertEqual(len(dots.detect_dots(img, touching_edge_ok=True)), 1)

    def test_two_merged_dots_come_back_as_one_blob(self):
        # Not something this module can fix — it is a pattern that is too
        # dense for the camera, and the fix is fewer dots. What matters is
        # that it reports ONE dot so the count check upstream fails, rather
        # than reporting two centroids that are both wrong.
        img = field()
        draw_dot(img, 300, 240, 14)
        draw_dot(img, 318, 240, 14)
        found = dots.detect_dots(img)
        self.assertEqual(len(found), 1)

    def test_a_dim_grey_field_is_not_a_dot(self):
        # No real projector puts out true black. A field at 40/255 with
        # nothing on it must find nothing at all.
        img = field()
        img[:] = 40
        self.assertEqual(dots.detect_dots(img), [])

    def test_a_dot_dimmer_than_the_threshold_is_not_found(self):
        img = draw_dot(field(), 320, 240, 12, value=150)
        self.assertEqual(dots.detect_dots(img), [])
        self.assertEqual(len(dots.detect_dots(img, threshold=100)), 1)


class TestBadInput(unittest.TestCase):

    def test_a_1d_array_raises(self):
        with self.assertRaises(dots.DotDetectionError):
            dots.detect_dots(np.zeros(10, dtype=np.uint8))

    def test_a_two_channel_image_raises(self):
        with self.assertRaises(dots.DotDetectionError):
            dots.detect_dots(np.zeros((10, 10, 2), dtype=np.uint8))

    def test_a_float_frame_is_coerced_rather_than_refused(self):
        img = draw_dot(field(), 320, 240, 12).astype(np.float32)
        self.assertEqual(len(dots.detect_dots(img)), 1)


class TestWireShapeAndHelpers(unittest.TestCase):

    def test_detect_points_matches_doc_4_7s_shape(self):
        img = draw_dot(field(), 320, 240, 12)
        pts = dots.detect_points(img)
        self.assertEqual(len(pts), 1)
        self.assertEqual(len(pts[0]), 2)
        self.assertIsInstance(pts[0][0], float)

    def test_best_n_takes_the_largest(self):
        img = field()
        for i, r in enumerate((8, 20, 14, 10)):
            draw_dot(img, 100 + 150 * i, 100, r)
        got = dots.best_n(dots.detect_dots(img), 2)
        self.assertEqual(len(got), 2)
        self.assertGreater(got[0].area, got[1].area)

    def test_best_n_of_zero_is_empty_rather_than_everything(self):
        # `[:0]` and `[:-1]` differ by the whole list. A negative n must
        # not quietly return all but one.
        img = draw_dot(field(), 320, 240, 12)
        self.assertEqual(dots.best_n(dots.detect_dots(img), 0), [])
        self.assertEqual(dots.best_n(dots.detect_dots(img), -3), [])

    def test_summarise_says_what_an_operator_can_act_on(self):
        self.assertIn("all 4", dots.summarise([1, 2, 3, 4], expected=4))
        self.assertIn("exposure", dots.summarise([1, 2], expected=4))
        self.assertIn("reflective", dots.summarise([1] * 6, expected=4))


if __name__ == "__main__":
    unittest.main()
