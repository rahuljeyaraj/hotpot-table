"""Tests for common/phantom.py — the idle-table attract loop's own
wandering-path math. Pure functions, no camera, no clock ownership: see
the module's own docstring for why that is the point.

Run from the repo root:

    python -m unittest discover -s python/tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import phantom  # noqa: E402


class TestPhantomHand(unittest.TestCase):

    def test_position_is_deterministic_given_the_same_seed(self):
        a = phantom.PhantomHand((1920.0, 1080.0),
                                [(100.0, 100.0), (1800.0, 900.0)], seed=42.0)
        b = phantom.PhantomHand((1920.0, 1080.0),
                                [(100.0, 100.0), (1800.0, 900.0)], seed=42.0)
        for t in (0.0, 1.3, 7.9, 40.2):
            self.assertEqual(a.position(t), b.position(t))

    def test_a_different_seed_gives_a_different_visiting_order(self):
        centres = [(x * 200.0, 500.0) for x in range(1, 8)]
        a = phantom.PhantomHand((1920.0, 1080.0), centres, seed=1.0)
        b = phantom.PhantomHand((1920.0, 1080.0), centres, seed=2.0)
        self.assertNotEqual(a._waypoints, b._waypoints)

    def test_position_never_leaves_the_stage(self):
        p = phantom.PhantomHand((1920.0, 1080.0),
                                [(0.0, 0.0), (1920.0, 1080.0)], seed=3.0)
        t = 0.0
        while t < 60.0:
            x, y = p.position(t)
            self.assertGreaterEqual(x, 0.0)
            self.assertLessEqual(x, 1920.0)
            self.assertGreaterEqual(y, 0.0)
            self.assertLessEqual(y, 1080.0)
            t += 0.37

    def test_a_negative_elapsed_time_does_not_raise(self):
        p = phantom.PhantomHand((1920.0, 1080.0), [(500.0, 500.0)], seed=0.0)
        p.position(-5.0)   # must not raise

    def test_no_bin_centres_falls_back_to_the_stage_centre(self):
        p = phantom.PhantomHand((1920.0, 1080.0), [], seed=0.0)
        x, y = p.position(0.0)
        self.assertAlmostEqual(x, 960.0, delta=phantom.WOBBLE_AMPLITUDE_PX)
        self.assertAlmostEqual(y, 540.0, delta=phantom.WOBBLE_AMPLITUDE_PX)

    def test_none_entries_in_bin_centres_are_skipped(self):
        p = phantom.PhantomHand((1920.0, 1080.0),
                                [None, (500.0, 500.0), None], seed=0.0)
        self.assertEqual(p._waypoints, [(500.0, 500.0)])

    def test_the_hand_actually_reaches_a_waypoint_during_its_dwell(self):
        # Somewhere in the leg it must be AT the destination, not just
        # forever easing toward it — the dwell half of each leg holds
        # position exactly, which is what lets a bin's fire-ring
        # crossfade (UiLayer's own 0.35s spring) fully settle in.
        dest = (1500.0, 300.0)
        p = phantom.PhantomHand((1920.0, 1080.0), [dest], seed=0.0)
        x, y = p.position(phantom.LEG_TRAVEL_S + 0.1)
        wob = phantom.WOBBLE_AMPLITUDE_PX
        self.assertAlmostEqual(x, dest[0], delta=wob)
        self.assertAlmostEqual(y, dest[1], delta=wob)


if __name__ == "__main__":
    unittest.main()
