"""Tests for tracker/tracking.py — doc section 11.3's two hands, two roles.

Run from the repo root:

    python -m unittest discover -s python/tests -v

No camera, no model, no clock: `HandTracker.update()` takes `now` as an
argument and `Detection` is a plain dataclass, so every scenario in doc
section 21's M5 acceptance list is expressible here as a sequence of
positions and timestamps. That is the whole reason the module is pure.

**Doc section 21's "Left hand over bin 3 → nothing happens to the UI. Try
hard to make it select. It must not." is the acceptance test this file is
trying to make impossible to fail.** The rig check still has to happen —
these tests cannot tell you MediaPipe labelled the hand correctly — but
every way the ROLE LOGIC could hand selection to the wrong hand is here.
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
AMBIENT = cursorbus.ROLE_AMBIENT


def det(x, y, handedness=None, conf=0.9):
    return Detection(x=float(x), y=float(y), conf=conf, handedness=handedness)


def roles(hands):
    return {h.id: h.role for h in hands}


class TestOneHand(unittest.TestCase):

    def test_a_lone_hand_is_the_pointer_whatever_hand_it_is(self):
        # Doc section 11.3's own rationale: "with one hand on the table,
        # that hand is the pointer regardless of which hand it is. A
        # left-handed diner alone at the table must not be locked out."
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
        # Smoothing disabled (RIG_FEEDBACK item 8, see TestSmoothing below
        # for that behaviour): this test's job is confirming a matched
        # track takes its position from the detection it was matched to,
        # not from filtering — the two are separate concerns since M5.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(500, 500)], now=0.0)
        hands = t.update([det(560, 480)], now=0.033)
        self.assertAlmostEqual(hands[0].x, 560.0)
        self.assertAlmostEqual(hands[0].y, 480.0)


class TestTheGate(unittest.TestCase):
    """Doc section 11.3 step 1: nearest-neighbour, gate 150px in stage
    space."""

    def test_a_move_inside_the_gate_keeps_the_id(self):
        t = tracking.HandTracker()
        first = t.update([det(500, 500)], now=0.0)
        second = t.update([det(500 + 149, 500)], now=0.033)
        self.assertEqual(first[0].id, second[0].id)

    def test_a_jump_past_the_gate_is_a_new_hand(self):
        # Two hands, not a renamed one: the original track is unmatched but
        # still inside its 500ms grace, so it is still believed present.
        # That is the same grace that stops a detector blink from minting
        # an id, and it cannot be selective about why a track went unseen.
        t = tracking.HandTracker()
        first = t.update([det(500, 500)], now=0.0)
        second = t.update([det(500 + 151, 500)], now=0.033)
        self.assertEqual(len(second), 2)
        new = [h for h in second if h.id != first[0].id]
        self.assertEqual(len(new), 1)
        self.assertAlmostEqual(new[0].x, 651.0)

    def test_the_abandoned_track_retires_and_leaves_only_the_new_hand(self):
        t = tracking.HandTracker()
        first = t.update([det(500, 500)], now=0.0)
        t.update([det(500 + 151, 500)], now=0.033)
        later = t.update([det(500 + 151, 500)], now=0.6)
        self.assertEqual([h.id for h in later],
                         [h.id for h in later if h.id != first[0].id])
        self.assertEqual(len(later), 1)

    def test_a_fast_move_on_a_slow_frame_keeps_the_id(self):
        # RIG_FEEDBACK item 11: at this rig's measured low end (4Hz,
        # dt=0.25s) an ordinary fast reach covers more than the fixed
        # 150px gate. Before the fix this minted a second, unsmoothed
        # track and left the old one frozen — the "stuck, then reappears
        # at the new spot" report. 400px in 0.25s is 1.6 m/s, well under
        # the 3 m/s budget `MATCH_SPEED_PX_S` is reasoned against.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        first = t.update([det(500, 500)], now=0.0)
        second = t.update([det(900, 500)], now=0.25)
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0].id, second[0].id)

    def test_a_move_beyond_even_the_widened_gate_is_still_a_new_hand(self):
        # The widening has an upper bound — it must not turn into "any
        # jump at all is the same hand".
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        first = t.update([det(500, 500)], now=0.0)
        second = t.update([det(500 + 5000, 500)], now=0.25)
        self.assertEqual(len(second), 2)
        new = [h for h in second if h.id != first[0].id]
        self.assertEqual(len(new), 1)

    def test_the_gate_does_not_widen_at_a_normal_camera_rate(self):
        # Pins the floor half of max(match_gate_px, speed*dt): at a 30Hz
        # frame interval the speed term (3000 * 0.033 ≈ 99px) must stay
        # under the 150px floor, so a jump just past 150px is still a new
        # hand exactly as TestTheGate's own gate tests above assume.
        t = tracking.HandTracker()
        first = t.update([det(500, 500)], now=0.0)
        second = t.update([det(500 + 151, 500)], now=0.033)
        self.assertEqual(len(second), 2)
        self.assertTrue(any(h.id != first[0].id for h in second))

    def test_two_hands_do_not_swap_ids_when_they_pass_close(self):
        # THE matching test. Greedy in list order rather than in distance
        # order lets the first track claim the detection that belonged much
        # more clearly to the second — and an id swap is a ROLE swap, i.e.
        # the bowl hand silently becoming the pointer. Smoothing disabled
        # (RIG_FEEDBACK item 8) so the asserted positions check MATCHING,
        # not how far a filter let the position move this frame.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        first = t.update([det(400, 500, HAND_RIGHT), det(600, 500, HAND_LEFT)],
                         now=0.0)
        pointer_id = [h.id for h in first if h.role == POINTER][0]
        ambient_id = [h.id for h in first if h.role == AMBIENT][0]

        # They close to 60px apart. Track A's true position is 470, track
        # B's is 530. In list order, A would grab 530 (60px away, inside
        # the gate) before B ever got a chance at it.
        second = t.update([det(530, 500), det(470, 500)], now=0.033)
        by_id = {h.id: h for h in second}
        self.assertAlmostEqual(by_id[pointer_id].x, 470.0)
        self.assertAlmostEqual(by_id[ambient_id].x, 530.0)
        self.assertEqual(by_id[pointer_id].role, POINTER)
        self.assertEqual(by_id[ambient_id].role, AMBIENT)


class TestRoleAssignment(unittest.TestCase):
    """Doc section 11.3 step 2."""

    def test_a_right_hand_arriving_second_takes_over_and_demotes_the_first(self):
        # Keyed by id, not by rounded x: RIG_FEEDBACK item 11's handoff
        # glide (TestPointerHandoff) means the new pointer's DISPLAYED x on
        # this very tick still reads close to the outgoing pointer's own
        # position — x is no longer a reliable stand-in for "which physical
        # track is this" the instant a takeover happens, only role/id are.
        t = tracking.HandTracker()
        first = t.update([det(400, 500, HAND_LEFT)], now=0.0)
        original_id = first[0].id
        hands = t.update([det(400, 500, HAND_LEFT),
                          det(900, 500, HAND_RIGHT)], now=0.033)
        by_id = {h.id: h for h in hands}
        self.assertEqual(by_id[original_id].role, AMBIENT)
        new_hand = [h for h in hands if h.id != original_id][0]
        self.assertEqual(new_hand.role, POINTER)

    def test_a_left_hand_arriving_second_stays_ambient(self):
        t = tracking.HandTracker()
        t.update([det(400, 500, HAND_RIGHT)], now=0.0)
        hands = t.update([det(400, 500, HAND_RIGHT),
                          det(900, 500, HAND_LEFT)], now=0.033)
        by_pos = {round(h.x): h for h in hands}
        self.assertEqual(by_pos[400].role, POINTER)
        self.assertEqual(by_pos[900].role, AMBIENT)

    def test_an_unlabelled_hand_arriving_second_stays_ambient(self):
        # Not "promote it anyway" — an unknown hand beside a working
        # pointer has given no reason to take control away.
        t = tracking.HandTracker()
        t.update([det(400, 500, HAND_RIGHT)], now=0.0)
        hands = t.update([det(400, 500, HAND_RIGHT), det(900, 500, None)],
                         now=0.033)
        by_pos = {round(h.x): h for h in hands}
        self.assertEqual(by_pos[900].role, AMBIENT)

    def test_there_is_never_more_than_one_pointer(self):
        t = tracking.HandTracker()
        t.update([det(300, 500, HAND_RIGHT)], now=0.0)
        hands = t.update([det(300, 500, HAND_RIGHT),
                          det(700, 500, HAND_RIGHT)], now=0.033)
        self.assertEqual(sum(1 for h in hands if h.role == POINTER), 1)


class TestTheRoleLock(unittest.TestCase):
    """Doc section 11.3 step 3: "Role is LOCKED for the lifetime of that
    tracked id. It never flips mid-gesture."

    This is the class that stands in for "try hard to make the left hand
    select." Everything below is a way the role could flip without a new
    hand appearing, and none of them may.
    """

    def test_handedness_changing_its_mind_does_not_flip_the_role(self):
        # MediaPipe re-classifies every frame and an overhead view makes it
        # unreliable (doc section 11.3's premise). A tracker that recomputed
        # the role from the current label would hand selection to the bowl
        # hand for exactly as long as one bad frame lasts.
        t = tracking.HandTracker()
        t.update([det(400, 500, HAND_RIGHT), det(900, 500, HAND_LEFT)], now=0.0)
        hands = t.update([det(400, 500, HAND_LEFT),
                          det(900, 500, HAND_RIGHT)], now=0.033)
        by_pos = {round(h.x): h for h in hands}
        self.assertEqual(by_pos[400].role, POINTER)
        self.assertEqual(by_pos[900].role, AMBIENT)

    def test_a_confident_left_hand_never_becomes_the_pointer_by_moving(self):
        t = tracking.HandTracker()
        t.update([det(400, 500, HAND_RIGHT), det(900, 500, HAND_LEFT)], now=0.0)
        now = 0.0
        # The left hand waves all over the table for two seconds while the
        # right hand sits still. Nothing it can do may promote it.
        for step in range(60):
            now += 0.033
            x = 900 + (step % 5) * 30
            hands = t.update([det(400, 500, HAND_RIGHT), det(x, 500, HAND_LEFT)],
                             now=now)
            ambient = [h for h in hands if round(h.x) != 400]
            self.assertEqual(ambient[0].role, AMBIENT,
                             f"the left hand became the pointer at step {step}")

    def test_a_one_frame_detection_dropout_does_not_mint_a_new_id(self):
        # A new id re-runs step 2, and step 2 with a right-labelled hand
        # demotes the incumbent — so a detector blink would rotate the
        # roles. The 500ms grace is what stops that.
        t = tracking.HandTracker()
        first = t.update([det(400, 500, HAND_RIGHT), det(900, 500, HAND_LEFT)],
                         now=0.0)
        pointer_id = [h.id for h in first if h.role == POINTER][0]
        t.update([det(900, 500, HAND_LEFT)], now=0.1)          # right hand blinks
        hands = t.update([det(400, 500, HAND_RIGHT), det(900, 500, HAND_LEFT)],
                         now=0.2)
        by_id = {h.id: h for h in hands}
        self.assertIn(pointer_id, by_id)
        self.assertEqual(by_id[pointer_id].role, POINTER)


class TestReleaseAndPromotion(unittest.TestCase):
    """Doc section 11.3 step 4: "When the pointer hand disappears for
    >500ms, its role is released. The remaining ambient hand is promoted to
    pointer only after a further 500ms."
    """

    def setUp(self):
        self.t = tracking.HandTracker()
        first = self.t.update([det(400, 500, HAND_RIGHT),
                               det(900, 500, HAND_LEFT)], now=0.0)
        self.pointer_id = [h.id for h in first if h.role == POINTER][0]
        self.ambient_id = [h.id for h in first if h.role == AMBIENT][0]

    def ambient_only(self, now):
        return self.t.update([det(900, 500, HAND_LEFT)], now=now)

    def test_the_pointer_survives_the_first_500ms_of_absence(self):
        hands = self.ambient_only(now=0.4)
        self.assertIn(self.pointer_id, {h.id for h in hands})
        self.assertEqual(roles(hands)[self.pointer_id], POINTER)

    def test_the_pointer_retires_after_500ms(self):
        hands = self.ambient_only(now=0.6)
        self.assertNotIn(self.pointer_id, {h.id for h in hands})

    def test_the_ambient_is_not_promoted_immediately_on_release(self):
        hands = self.ambient_only(now=0.6)
        self.assertEqual(roles(hands)[self.ambient_id], AMBIENT)

    def test_the_ambient_is_not_promoted_before_a_further_500ms(self):
        self.ambient_only(now=0.6)
        hands = self.ambient_only(now=1.05)
        self.assertEqual(roles(hands)[self.ambient_id], AMBIENT)

    def test_the_ambient_is_promoted_after_a_further_500ms(self):
        self.ambient_only(now=0.6)
        hands = self.ambient_only(now=1.2)
        self.assertEqual(roles(hands)[self.ambient_id], POINTER)

    def test_the_promotion_clock_does_not_restart_every_frame(self):
        # The bug this catches: stamping the release time on every frame
        # where no pointer exists, rather than once when the role became
        # vacant. Promotion would then never arrive, because the deadline
        # moves away as fast as the clock advances.
        now = 0.6
        self.ambient_only(now=now)
        while now < 1.19:
            now += 0.033
            self.ambient_only(now=now)
        hands = self.ambient_only(now=1.25)
        self.assertEqual(roles(hands)[self.ambient_id], POINTER)

    def test_a_returning_pointer_cancels_the_promotion(self):
        self.ambient_only(now=0.6)          # released
        # The diner's right hand comes back before the promotion lands. It
        # is a new id (its old one retired) and step 2 gives it the pointer
        # role immediately, because no pointer exists.
        hands = self.t.update([det(900, 500, HAND_LEFT),
                               det(400, 500, HAND_RIGHT)], now=0.8)
        self.assertEqual(roles(hands)[self.ambient_id], AMBIENT)
        self.assertEqual(sum(1 for h in hands if h.role == POINTER), 1)
        # ...and the ambient hand must not be promoted later either: the
        # deadline was cancelled, not merely postponed.
        hands = self.t.update([det(900, 500, HAND_LEFT),
                               det(400, 500, HAND_RIGHT)], now=2.0)
        self.assertEqual(roles(hands)[self.ambient_id], AMBIENT)

    def test_an_empty_table_ends_with_no_hands_and_no_roles(self):
        hands = self.t.update([], now=2.0)
        self.assertEqual(hands, [])
        self.assertIsNone(self.t.pointer())

    def test_the_longest_lived_ambient_is_the_one_promoted(self):
        t = tracking.HandTracker()
        t.update([det(100, 100, HAND_RIGHT)], now=0.0)          # pointer
        for i in range(1, 20):
            t.update([det(100, 100, HAND_RIGHT), det(600, 600, HAND_LEFT)],
                     now=i * 0.033)                              # old ambient
        t.update([det(100, 100, HAND_RIGHT), det(600, 600, HAND_LEFT),
                  det(1200, 600, HAND_LEFT)], now=0.7)           # new ambient
        old_ambient = [h for h in t.update(
            [det(100, 100, HAND_RIGHT), det(600, 600, HAND_LEFT),
             det(1200, 600, HAND_LEFT)], now=0.73)
            if round(h.x) == 600][0]
        # The pointer leaves; both ambients stay.
        t.update([det(600, 600, HAND_LEFT), det(1200, 600, HAND_LEFT)], now=1.4)
        hands = t.update([det(600, 600, HAND_LEFT), det(1200, 600, HAND_LEFT)],
                         now=2.1)
        self.assertEqual(roles(hands)[old_ambient.id], POINTER)


class TestReset(unittest.TestCase):

    def test_reset_forgets_every_track_and_role(self):
        t = tracking.HandTracker()
        t.update([det(400, 500, HAND_RIGHT), det(900, 500, HAND_LEFT)], now=0.0)
        t.reset()
        self.assertEqual(t.tracks, [])
        self.assertIsNone(t.pointer())

    def test_after_reset_the_first_hand_back_is_the_pointer(self):
        # A camera outage of unknown length: the bowl hand must not keep a
        # pointer role it inherited before the gap, and the first hand seen
        # after it starts the assignment over.
        t = tracking.HandTracker()
        t.update([det(400, 500, HAND_RIGHT), det(900, 500, HAND_LEFT)], now=0.0)
        t.reset()
        hands = t.update([det(900, 500, HAND_LEFT)], now=30.0)
        self.assertEqual(hands[0].role, POINTER)


class TestSmoothing(unittest.TestCase):
    """RIG_FEEDBACK item 8 — "pointer is jittery, needs smoothing." See
    `tracking.py`'s module docstring for why the filter lives here (in
    `HandTracker`, downstream of `tracker/main.py`'s shadow-clearance
    offset, and why that placement is equivalent to filtering upstream of
    it) rather than in `tracker/main.py` itself.
    """

    def test_a_new_track_is_not_smoothed(self):
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
        # 100px is inside the 150px match gate (doc section 11.3 step 1) —
        # this has to stay the SAME track converging, not a gate-breaking
        # jump that mints a fresh, unsmoothed one at the target.
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
        # Move within the 150px match gate (doc section 11.3 step 1) so
        # this stays the same track matching a new detection, not a jump
        # past the gate minting a second one.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(0, 0)], now=0.0)
        hands = t.update([det(100, -50)], now=0.001)
        self.assertAlmostEqual(hands[0].x, 100.0)
        self.assertAlmostEqual(hands[0].y, -50.0)

    def test_default_tau_matches_the_module_constant(self):
        t = tracking.HandTracker()
        self.assertEqual(t.smoothing_tau_s, tracking.TRACK_SMOOTHING_TAU_S)


class TestMatchingAgainstTheRawPosition(unittest.TestCase):
    """RIG_FEEDBACK item 11 (2026-08-13), the confirmed root cause: a new
    detection used to be matched against a track's SMOOTHED `x`/`y` —
    which lags the true hand position by design, that being the entire
    point of an EMA — rather than its last REAL detection. Under
    sustained motion the lag itself grows past the match gate, which has
    nothing to do with the hand's actual frame-to-frame movement, and a
    second track spawns to compete for the same physical hand. Confirmed
    on the rig (two independent reproductions, `docs/RIG_FEEDBACK_
    2026-08-12.md`'s item 11) and reproduced here at unit-test scale.
    """

    def test_sustained_motion_stays_one_track_not_a_spawning_cascade(self):
        # 2 m/s (2000 px/s at this rig's ~1px=1mm stage scale) for a third
        # of a second at a realistic 30Hz — a brisk, plausible "I moved my
        # hand quickly", not an extreme one. Before this fix, matching
        # against the lagging smoothed position spawned a second,
        # competing track by the 5th tick of exactly this scenario.
        t = tracking.HandTracker()
        now = 0.0
        x = 0.0
        first_id = None
        for _ in range(10):
            now += 0.033
            x += 2000.0 * 0.033
            hands = t.update([det(x, 500.0)], now=now)
            # The direct regression check: a ghost track would show up
            # here as a second hand (ambient), not just as a different id.
            self.assertEqual(len(hands), 1)
            if first_id is None:
                first_id = hands[0].id
            self.assertEqual(hands[0].id, first_id)
        self.assertEqual(len(t.tracks), 1)

    def test_the_raw_position_is_never_smoothed_even_though_x_y_is(self):
        t = tracking.HandTracker(smoothing_tau_s=0.1)
        t.update([det(0, 0)], now=0.0)
        t.update([det(100, 0)], now=0.033)
        track = t.tracks[0]
        self.assertAlmostEqual(track.raw_x, 100.0)
        self.assertAlmostEqual(track.raw_y, 0.0)
        # The smoothed display position is still genuinely smoothed —
        # this fix changes what MATCHING compares against, not the EMA
        # itself, which item 8 still wants for a jitter-free cursor.
        self.assertLess(track.x, 100.0)


class TestPointerHandoff(unittest.TestCase):
    """RIG_FEEDBACK item 11 (2026-08-13), the fourth fix on this item — see
    `tracking.py`'s own `POINTER_HANDOFF_S` comment for why this one does
    not try to be a fourth theory of WHY the pointer track's id churns.
    It targets what a diner actually sees: whichever track currently holds
    the pointer role, its x/y should glide from the outgoing pointer's
    last position rather than jump, whenever the role moves to a
    different track id.
    """

    def test_ordinary_continuous_tracking_gets_no_glide_at_all(self):
        # The regression check that matters most: this must add ZERO lag
        # to the common case where the same track keeps being the
        # pointer tick after tick — only an id CHANGE should ever glide.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        now = 0.0
        for i in range(10):
            now += 0.033
            hands = t.update([det(100.0 * i, 500)], now=now)
            self.assertAlmostEqual(hands[0].x, 100.0 * i)
            self.assertAlmostEqual(hands[0].y, 500.0)

    def test_a_direct_role_takeover_starts_from_the_old_position_not_a_snap(self):
        # The exact scenario TestRoleAssignment's own
        # test_a_right_hand_arriving_second_takes_over_and_demotes_the_
        # first uses — a new Right hand steals the pointer role on the
        # tick it arrives. Before this fix the reported x jumped straight
        # from 400 to 900 on this very tick; the glide only starts
        # counting from here, so this tick must still read the OUTGOING
        # pointer's own last position, not the new track's true one.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(400, 500, HAND_LEFT)], now=0.0)
        hands = t.update([det(400, 500, HAND_LEFT),
                          det(900, 500, HAND_RIGHT)], now=0.033)
        pointer = [h for h in hands if h.role == POINTER][0]
        self.assertAlmostEqual(pointer.x, 400.0)

    def test_a_later_tick_is_partway_through_the_glide(self):
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(400, 500, HAND_LEFT)], now=0.0)
        t.update([det(400, 500, HAND_LEFT), det(900, 500, HAND_RIGHT)],
                 now=0.033)                                    # takeover
        half = 0.033 + tracking.POINTER_HANDOFF_S / 2.0
        hands = t.update([det(400, 500, HAND_LEFT), det(900, 500, HAND_RIGHT)],
                         now=half)
        pointer = [h for h in hands if h.role == POINTER][0]
        self.assertGreater(pointer.x, 400.0)
        self.assertLess(pointer.x, 900.0)

    def test_the_glide_converges_on_the_true_position(self):
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(400, 500, HAND_LEFT)], now=0.0)
        t.update([det(400, 500, HAND_LEFT), det(900, 500, HAND_RIGHT)],
                 now=0.033)
        # Hold the new pointer still well past POINTER_HANDOFF_S.
        hands = t.update([det(400, 500, HAND_LEFT), det(900, 500, HAND_RIGHT)],
                         now=0.033 + tracking.POINTER_HANDOFF_S + 0.05)
        pointer = [h for h in hands if h.role == POINTER][0]
        self.assertAlmostEqual(pointer.x, 900.0)

    def test_a_long_gap_snaps_instead_of_gliding_from_a_stale_position(self):
        # A diner who left and a different diner arriving much later must
        # see their own hand's true position immediately — not a slide in
        # from across the table where the last pointer used to be.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(100, 100)], now=0.0)
        t.reset()
        hands = t.update([det(900, 900)],
                         now=tracking.POINTER_HANDOFF_MAX_GAP_S + 10.0)
        self.assertAlmostEqual(hands[0].x, 900.0)
        self.assertAlmostEqual(hands[0].y, 900.0)

    def test_the_retire_then_promote_cycle_also_glides(self):
        # The OTHER churn shape this item's rig log shows (not the direct
        # `_appear` takeover above): the pointer track retires, and an
        # already-existing ambient is promoted `PROMOTE_DELAY_S` later.
        # That promotion is a role change on an EXISTING track too, and it
        # must glide from the outgoing pointer's own last position.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(400, 500, HAND_RIGHT), det(900, 500, HAND_LEFT)],
                 now=0.0)
        # The right hand vanishes; only the ambient (left) is seen.
        t.update([det(900, 500, HAND_LEFT)], now=0.4)
        t.update([det(900, 500, HAND_LEFT)], now=0.6)          # retired
        hands = t.update([det(900, 500, HAND_LEFT)], now=1.2)  # promoted
        pointer = [h for h in hands if h.role == POINTER][0]
        # Same reasoning as the direct-takeover test above: the promotion
        # tick itself must still read the outgoing pointer's own last
        # position (400), not have already snapped to 900.
        self.assertAlmostEqual(pointer.x, 400.0)

    def test_rapid_rechurn_continues_from_the_displayed_position_not_the_raw_one(self):
        # A second role change mid-glide must not re-derive its start from
        # the FIRST outgoing track's raw position — it should continue
        # from wherever the diner's eye currently is (the partially-glided
        # displayed position), so back-to-back churn still reads as one
        # continuous glide rather than two disconnected ones.
        t = tracking.HandTracker(smoothing_tau_s=0.0)
        t.update([det(0, 0, HAND_LEFT)], now=0.0)
        t.update([det(0, 0, HAND_LEFT), det(1000, 0, HAND_RIGHT)],
                 now=0.01)                                    # takeover #1
        half = 0.01 + tracking.POINTER_HANDOFF_S / 2.0
        hands = t.update([det(0, 0, HAND_LEFT), det(1000, 0, HAND_RIGHT)],
                         now=half)
        mid_glide_x = [h for h in hands if h.role == POINTER][0].x
        self.assertGreater(mid_glide_x, 0.0)
        self.assertLess(mid_glide_x, 1000.0)
        # A second, different Right hand steals it again immediately after.
        hands = t.update([det(0, 0, HAND_LEFT), det(1000, 0, HAND_RIGHT),
                          det(-1000, 0, HAND_RIGHT)], now=half + 0.001)
        pointer2 = [h for h in hands if h.role == POINTER][0]
        self.assertAlmostEqual(pointer2.x, mid_glide_x)


class TestWhatGoesOnTheWire(unittest.TestCase):

    def test_hands_come_back_in_stable_id_order(self):
        t = tracking.HandTracker()
        t.update([det(400, 500, HAND_RIGHT)], now=0.0)
        t.update([det(400, 500, HAND_RIGHT), det(900, 500, HAND_LEFT)], now=0.03)
        # The detections arrive in the opposite order on the next frame;
        # the output order must not follow them.
        hands = t.update([det(900, 500, HAND_LEFT), det(400, 500, HAND_RIGHT)],
                         now=0.06)
        self.assertEqual([h.id for h in hands], sorted(h.id for h in hands))

    def test_the_output_is_cursorbus_hands_ready_to_encode(self):
        t = tracking.HandTracker()
        hands = t.update([det(941.2, 510.8, HAND_RIGHT, conf=0.93)], now=0.0)
        frame = cursorbus.CursorFrame(seq=0, ts=0.0, hands=hands)
        decoded = cursorbus.decode(cursorbus.encode(frame))
        self.assertEqual(decoded.pointer().id, hands[0].id)
        self.assertAlmostEqual(decoded.pointer().x, 941.2, places=1)


if __name__ == "__main__":
    unittest.main()
