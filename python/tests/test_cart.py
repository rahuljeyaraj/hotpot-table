"""Tests for core/cart.py — M1 build item 2 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

The doc section 9.2 arithmetic (I4/I5) is the thing this file exists to
pin down: removed_grams() is an absolute-weight difference, never a sum
of events, and the display deadband snaps rather than creeps and never
touches the underlying grams.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.core.cart import Cart  # noqa: E402


class TestFreshCart(unittest.TestCase):

    def test_all_zero(self):
        c = Cart()
        self.assertEqual(c.start_g, [0.0] * 8)
        self.assertEqual(c.live_g, [0.0] * 8)
        self.assertEqual(c.shown_g, [0.0] * 8)
        self.assertEqual(c.removed_grams(0), 0.0)

    def test_bin_index_is_checked(self):
        c = Cart()
        with self.assertRaises(IndexError):
            c.removed_grams(8)
        with self.assertRaises(IndexError):
            c.set_live_grams(-1, 100.0)


class TestRemovedGrams(unittest.TestCase):
    """I4: price[i] is derived from an absolute-weight difference."""

    def test_removed_is_start_minus_live(self):
        c = Cart()
        c.start_g[0] = 500.0
        c.set_live_grams(0, 380.0)
        self.assertEqual(c.removed_grams(0), 120.0)

    def test_removed_never_goes_negative(self):
        """A bin that gained weight — put-back, or food added — has
        removed nothing. There is no put-back branch (I4): this clamp is
        the entire refund story.
        """
        c = Cart()
        c.start_g[0] = 500.0
        c.set_live_grams(0, 620.0)          # more than start_g
        self.assertEqual(c.removed_grams(0), 0.0)

    def test_live_grams_itself_never_goes_negative(self):
        c = Cart()
        c.set_live_grams(0, -50.0)
        self.assertEqual(c.live_g[0], 0.0)


class TestDeadbandSnap(unittest.TestCase):
    """Doc section 9.2: shown_g SNAPS to truth, it does not increment and
    it does not ignore sub-deadband events (I5's forbidden alternative).
    """

    def setUp(self):
        self.c = Cart(deadband_g=10.0)
        self.c.start_g[0] = 500.0
        self.c.live_g[0] = 500.0

    def test_a_pick_under_the_deadband_does_not_move_shown_g(self):
        self.c.set_live_grams(0, 495.0)     # removed = 5g, under 10g
        self.assertEqual(self.c.removed_grams(0), 5.0)
        self.assertEqual(self.c.shown_g[0], 0.0)

    def test_a_pick_at_the_deadband_snaps_to_the_exact_truth(self):
        self.c.set_live_grams(0, 480.0)     # removed = 20g
        self.assertEqual(self.c.shown_g[0], 20.0)

    def test_matches_the_doc_section_21_acceptance_sequence(self):
        """Pick 45g, then 6g, then 120g: the total is 171g exactly, whether
        or not any individual step crossed the deadband on its own —
        verified by arithmetic (removed_grams), not by watching shown_g.
        """
        self.c.mock_pick(0, 45.0)
        self.c.mock_pick(0, 6.0)
        self.c.mock_pick(0, 120.0)
        self.assertEqual(self.c.removed_grams(0), 171.0)
        # The 6g step alone is under the 10g deadband, so shown_g lagged
        # at 45 for one step — this is what proves it snaps, not creeps.
        self.assertEqual(self.c.shown_g[0], 171.0)

    def test_shown_g_lags_behind_a_sub_deadband_pick(self):
        self.c.mock_pick(0, 45.0)
        self.assertEqual(self.c.shown_g[0], 45.0)
        self.c.mock_pick(0, 6.0)            # removed now 51g, gap is 6g
        self.assertEqual(self.c.removed_grams(0), 51.0)
        self.assertEqual(self.c.shown_g[0], 45.0)   # unmoved: this is the point


class TestMockControls(unittest.TestCase):
    """Doc section 12.8: the developer panel's pick/put-back buttons."""

    def setUp(self):
        self.c = Cart()
        self.c.start_g[3] = 200.0
        self.c.live_g[3] = 200.0

    def test_mock_pick_lowers_live_grams(self):
        self.c.mock_pick(3, 45.0)
        self.assertEqual(self.c.live_g[3], 155.0)
        self.assertEqual(self.c.removed_grams(3), 45.0)

    def test_mock_putback_raises_live_grams_back(self):
        self.c.mock_pick(3, 45.0)
        self.c.mock_putback(3, 45.0)
        self.assertEqual(self.c.live_g[3], 200.0)
        self.assertEqual(self.c.removed_grams(3), 0.0)


class TestResetSession(unittest.TestCase):
    """I6: re-baseline, never re-tare. Nothing becomes zero."""

    def test_rebaselines_to_current_live_grams(self):
        c = Cart()
        c.start_g[0] = 500.0
        c.set_live_grams(0, 350.0)          # 150g removed, mid-order
        c.reset_session()
        self.assertEqual(c.start_g[0], 350.0)   # not 0 — I6
        self.assertEqual(c.live_g[0], 350.0)
        self.assertEqual(c.removed_grams(0), 0.0)
        self.assertEqual(c.shown_g[0], 0.0)

    def test_resets_every_bin(self):
        c = Cart()
        for i in range(8):
            c.live_g[i] = float(i * 10)
        c.reset_session()
        self.assertEqual(c.start_g, c.live_g)
        self.assertEqual(c.shown_g, [0.0] * 8)


class TestFinalize(unittest.TestCase):
    """Doc section 9.2's fix for open debt #5: shown_g snaps unconditionally
    at order finalisation, deadband or not.
    """

    def test_snaps_even_a_sub_deadband_remainder(self):
        c = Cart(deadband_g=10.0)
        c.start_g[0] = 500.0
        c.set_live_grams(0, 495.0)          # 5g removed, under the deadband
        self.assertEqual(c.shown_g[0], 0.0)
        c.finalize()
        self.assertEqual(c.shown_g[0], 5.0)

    def test_finalizes_every_bin(self):
        c = Cart()
        c.start_g = [100.0] * 8
        c.live_g = [90.0] * 8
        c.finalize()
        self.assertEqual(c.shown_g, [10.0] * 8)


if __name__ == "__main__":
    unittest.main()
