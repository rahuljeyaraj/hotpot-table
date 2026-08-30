"""Tests for tracker/tracking.py — the single tracked hand's position,
smoothed.

Run from the repo root:

    python -m unittest discover -s python/tests -v

No camera, no model, no clock: `HandTracker.update()` takes `now` as an
argument and `Detection` is a plain dataclass. That is the whole reason
the module is pure.

2026-08-13: this file used to be doc section 11.3's full two-hand
acceptance suite — matching, a widening gate, handedness-based role
takeover, a 500ms+500ms retire/promote cycle. All of that is gone from
`tracking.py` (see its own module docstring — this rig only ever tracks
one hand, so none of those questions has a second answer to choose
between any more) and gone from here with it. The old test classes are
in git history if two-hand tracking is ever rebuilt; this file now only
has to prove what the simplified module actually claims: at most one
hand ever reaches the wire, it is always the pointer, and its position
is smoothed against per-frame jitter without inventing motion the raw
data never showed.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import cursorbus  # noqa: E402
from hotpot.tracker import tracking  # noqa: E402
from hotpot.tracker.backend import HAND_LEFT, HAND_RIGHT, Detection  # noqa: E402

POINTER = cursorbus.ROLE_POINTER


def det(x, y, handedness=None, conf=0.9):
    return Detection(x=float(x), y=float(y), conf=conf, handedness=handedness)


class TestOneHand(unittest.TestCase):

    def test_a_hand_is_always_the_pointer_whatever_hand_it_is(self):
        # Doc section 11.3's own rationale survives even though the
        # two-hand machinery that used to enforce it doesn't need to any
        # more: with one hand ever tracked, it is trivially always true.
        for label in (HAND_LEFT, HAND_RIGHT, None):
            with self.subTest(handedness=label):
                t = tracking.HandTracker()
                hands = t.update([det(500, 500, label)], now=0.0)
                self.assertEqual(len(hands), 1)
                self.assertEqual(hands[0].role, POINTER)

    def test_the_id_is_stable_across_frames(self):
        t = tracking.HandTracker()
        first = t.update([det(500, 500)], now=0.0)
        second = t.update([det(520, 505)], now=0.033)
        third = t.update([det(540, 510)], now=0.066)
        self.assertEqual(first[0].id, second[0].id)
        self.assertEqual(second[0].id, third[0].id)

    def test_the_position_follows_the_detection(self):
        # Smoothing disabled: this test's job is confirming the reported
        # position comes from the detection, not from filtering.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(500, 500)], now=0.0)
        hands = t.update([det(560, 480)], now=0.033)
        self.assertAlmostEqual(hands[0].x, 560.0)
        self.assertAlmostEqual(hands[0].y, 480.0)

    def test_only_the_first_detection_is_ever_used(self):
        # MediaPipe is configured with num_hands=1 (tracker/main.py), so
        # this should never actually receive two — but the module's own
        # contract (its docstring) is that it would not start choosing
        # between them if it did.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        hands = t.update([det(100, 100), det(900, 900)], now=0.0)
        self.assertEqual(len(hands), 1)
        self.assertAlmostEqual(hands[0].x, 100.0)


class TestPresence(unittest.TestCase):

    def test_no_detections_is_no_hands(self):
        t = tracking.HandTracker()
        self.assertEqual(t.update([], now=0.0), [])

    def test_pointer_reflects_the_last_update(self):
        t = tracking.HandTracker()
        self.assertIsNone(t.pointer())
        t.update([det(500, 500)], now=0.0)
        self.assertIsNotNone(t.pointer())
        t.update([], now=0.033)
        self.assertIsNone(t.pointer())

    def test_a_gap_is_not_bridged(self):
        # Doc's own rule (module docstring): a real gap is not a
        # smoothing problem and this module must not invent motion the
        # raw data never showed — see RIG_FEEDBACK item 11's reverted
        # handoff-glide mitigation for the shape of fix this rules out.
        t = tracking.HandTracker(smoothing_tau_s=0.1)
        t.update([det(0, 0)], now=0.0)
        t.update([], now=0.033)                    # hand briefly gone
        hands = t.update([det(900, 900)], now=0.066)
        # A fresh sighting after any gap appears at its own true
        # position, not blended in from the pre-gap one.
        self.assertAlmostEqual(hands[0].x, 900.0)
        self.assertAlmostEqual(hands[0].y, 900.0)


class TestReset(unittest.TestCase):

    def test_reset_forgets_the_filter(self):
        t = tracking.HandTracker(smoothing_tau_s=0.1)
        t.update([det(500, 500)], now=0.0)
        t.reset()
        self.assertIsNone(t.pointer())
        # After reset, the next sighting is unsmoothed — same "no motion
        # invented across a gap" rule a stale-camera outage relies on.
        hands = t.update([det(900, 900)], now=30.0)
        self.assertAlmostEqual(hands[0].x, 900.0)


class TestSmoothing(unittest.TestCase):
    """RIG_FEEDBACK item 8 — "pointer is jittery, needs smoothing." See
    `tracking.py`'s module docstring for why the filter is time-based.
    """

    def test_a_new_sighting_is_not_smoothed(self):
        # No history to blend against — a first sighting must appear at
        # its own true position, not creep in from zero.
        t = tracking.HandTracker()
        hands = t.update([det(500, 500)], now=0.0)
        self.assertAlmostEqual(hands[0].x, 500.0)
        self.assertAlmostEqual(hands[0].y, 500.0)

    def test_a_matched_update_blends_toward_the_detection(self):
        # Not snapped (that would be item 8 unfixed) and not stuck (that
        # would be a filter with the wrong sign) — strictly between the
        # old and new position, and matching the EMA formula exactly.
        t = tracking.HandTracker(smoothing_tau_s=0.1)
        t.update([det(0, 0)], now=0.0)
        hands = t.update([det(100, 0)], now=0.033)
        alpha = 1.0 - math.exp(-0.033 / 0.1)
        self.assertGreater(hands[0].x, 0.0)
        self.assertLess(hands[0].x, 100.0)
        self.assertAlmostEqual(hands[0].x, 100.0 * alpha, places=6)

    def test_repeated_updates_converge_on_a_steady_target(self):
        t = tracking.HandTracker(smoothing_tau_s=0.1)
        now = 0.0
        hands = t.update([det(0, 0)], now=now)
        for _ in range(60):          # ~2s at a 30Hz frame rate
            now += 0.033
            hands = t.update([det(100, 0)], now=now)
        self.assertAlmostEqual(hands[0].x, 100.0, delta=0.5)

    def test_a_bigger_time_gap_blends_more_than_a_smaller_one(self):
        # THE reason this is time-based rather than a fixed per-frame
        # blend (module docstring): this rig's own measured camera rate
        # has ranged 4-30Hz, so a constant alpha would mean a different
        # amount of real-world smoothing depending on what the frame rate
        # happened to be that tick.
        fast = tracking.HandTracker(smoothing_tau_s=0.1)
        fast.update([det(0, 0)], now=0.0)
        fast_hands = fast.update([det(100, 0)], now=0.01)

        slow = tracking.HandTracker(smoothing_tau_s=0.1)
        slow.update([det(0, 0)], now=0.0)
        slow_hands = slow.update([det(100, 0)], now=0.2)

        self.assertLess(fast_hands[0].x, slow_hands[0].x)

    def test_zero_tau_disables_smoothing(self):
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(0, 0)], now=0.0)
        hands = t.update([det(100, -50)], now=0.001)
        self.assertAlmostEqual(hands[0].x, 100.0)
        self.assertAlmostEqual(hands[0].y, -50.0)

    def test_default_tau_matches_the_module_constant(self):
        t = tracking.HandTracker()
        self.assertEqual(t.smoothing_tau_s, tracking.TRACK_SMOOTHING_TAU_S)


class TestWhatGoesOnTheWire(unittest.TestCase):

    def test_the_output_is_cursorbus_hands_ready_to_encode(self):
        t = tracking.HandTracker()
        hands = t.update([det(941.2, 510.8, HAND_RIGHT, conf=0.93)], now=0.0)
        frame = cursorbus.CursorFrame(seq=0, ts=0.0, hands=hands)
        decoded = cursorbus.decode(cursorbus.encode(frame))
        self.assertEqual(decoded.pointer().id, hands[0].id)
        self.assertAlmostEqual(decoded.pointer().x, 941.2, places=1)


if __name__ == "__main__":
    unittest.main()
