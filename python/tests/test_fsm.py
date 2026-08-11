"""Tests for core/fsm.py — M1 build item 2 and M2.6 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Scope matches the build items exactly: BOOT, IDLE, SELECTING (M1) and
SETTING (M2.6). Every other state in doc section 9.1's diagram arrives
with its own milestone and has no test here yet.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.core.binmap import BinMap  # noqa: E402
from hotpot.core.cart import Cart  # noqa: E402
from hotpot.core.fsm import Fsm, State  # noqa: E402


class TestStartsInBoot(unittest.TestCase):

    def test_initial_state(self):
        f = Fsm(Cart(), BinMap())
        self.assertEqual(f.state, State.BOOT)


class TestBootComplete(unittest.TestCase):

    def test_boot_to_idle(self):
        f = Fsm(Cart(), BinMap())
        self.assertTrue(f.boot_complete())
        self.assertEqual(f.state, State.IDLE)

    def test_not_legal_a_second_time(self):
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        self.assertFalse(f.boot_complete())
        self.assertEqual(f.state, State.IDLE)          # unchanged, not an error


class TestHandPresentAndStaffStart(unittest.TestCase):

    def test_hand_present_from_idle(self):
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        self.assertTrue(f.hand_present())
        self.assertEqual(f.state, State.SELECTING)

    def test_staff_start_from_idle(self):
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        self.assertTrue(f.staff_start())
        self.assertEqual(f.state, State.SELECTING)

    def test_hand_present_is_a_no_op_from_boot(self):
        """A hand event racing startup is ordinary traffic, not a bug —
        doc section 9.1 defines no BOOT -> SELECTING transition.
        """
        f = Fsm(Cart(), BinMap())
        self.assertFalse(f.hand_present())
        self.assertEqual(f.state, State.BOOT)

    def test_hand_present_is_a_no_op_once_already_selecting(self):
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        f.hand_present()
        self.assertFalse(f.hand_present())
        self.assertEqual(f.state, State.SELECTING)


class TestCancel(unittest.TestCase):
    """Doc section 9.1: cancel re-baselines and clears the cart (I6) through
    the shared reset_session(), and does so only from SELECTING.
    """

    def test_cancel_from_selecting_goes_to_idle(self):
        cart = Cart()
        f = Fsm(cart, BinMap())
        f.boot_complete()
        f.hand_present()
        self.assertTrue(f.cancel())
        self.assertEqual(f.state, State.IDLE)

    def test_cancel_calls_reset_session(self):
        cart = Cart()
        cart.start_g[0] = 500.0
        cart.set_live_grams(0, 350.0)       # a cart mid-order

        f = Fsm(cart, BinMap())
        f.boot_complete()
        f.hand_present()
        f.cancel()

        self.assertEqual(cart.start_g[0], 350.0)   # re-baselined, not zeroed
        self.assertEqual(cart.removed_grams(0), 0.0)

    def test_cancel_is_a_no_op_from_idle_and_does_not_touch_the_cart(self):
        cart = Cart()
        cart.start_g[0] = 500.0
        cart.set_live_grams(0, 350.0)

        f = Fsm(cart, BinMap())
        f.boot_complete()
        self.assertFalse(f.cancel())
        self.assertEqual(f.state, State.IDLE)
        self.assertEqual(cart.start_g[0], 500.0)    # untouched — reset_session not called

    def test_cancel_is_a_no_op_from_boot(self):
        f = Fsm(Cart(), BinMap())
        self.assertFalse(f.cancel())
        self.assertEqual(f.state, State.BOOT)


class TestOnTransitionCallback(unittest.TestCase):

    def test_fires_with_old_and_new_state(self):
        seen = []
        f = Fsm(Cart(), BinMap(), on_transition=lambda old, new: seen.append((old, new)))
        f.boot_complete()
        f.hand_present()
        self.assertEqual(seen, [(State.BOOT, State.IDLE), (State.IDLE, State.SELECTING)])

    def test_does_not_fire_on_a_rejected_transition(self):
        seen = []
        f = Fsm(Cart(), BinMap(), on_transition=lambda old, new: seen.append((old, new)))
        f.hand_present()                    # illegal from BOOT
        self.assertEqual(seen, [])


class TestCanEnterSetting(unittest.TestCase):
    """Doc section 9.1: "any (staff enter, cart empty) -> SETTING", and
    "Setting mode is refused while a cart is active... The staff view
    shows *why* it is refused."
    """

    def test_allowed_with_an_empty_cart(self):
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        self.assertIsNone(f.can_enter_setting())

    def test_refused_with_an_active_cart_and_says_why(self):
        cart = Cart()
        cart.set_live_grams(0, 500.0)
        cart.reset_session()
        cart.mock_pick(0, 45.0)                 # a real, visible pick
        f = Fsm(cart, BinMap())
        f.boot_complete()
        reason = f.can_enter_setting()
        self.assertIsInstance(reason, str)
        self.assertTrue(reason, "refused with an empty reason — doc 9.1 wants the why")

    def test_allowed_with_a_sub_deadband_pick_present(self):
        """The `shown_g` decision (cart.is_active's docstring), pinned as
        a test because the alternative reading is silently catastrophic.

        5 g is under doc section 9.2's 10 g display deadband, so nothing
        on the table has moved and `shown_g` is still 0 — but
        `removed_grams()` is 5.0. If is_active() read the raw number,
        ordinary load-cell noise (CLAUDE.md's per-channel table: four
        bins at 500-1500 counts rms) would hold this true permanently and
        **setting mode would be unreachable on the rig.**

        MUTATION CHECKED: switch cart.is_active() to
        `any(self.removed_grams(i) > 0.0 for i in ...)` and this goes red.
        """
        cart = Cart()
        cart.set_live_grams(0, 500.0)
        cart.reset_session()
        cart.mock_pick(0, 5.0)
        self.assertEqual(cart.shown_g[0], 0.0)      # nothing visible moved
        self.assertEqual(cart.removed_grams(0), 5.0)  # but raw weight did
        f = Fsm(cart, BinMap())
        f.boot_complete()
        self.assertIsNone(f.can_enter_setting())


class TestEnterSetting(unittest.TestCase):

    def test_from_idle(self):
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        self.assertTrue(f.enter_setting())
        self.assertEqual(f.state, State.SETTING)

    def test_from_selecting_too_because_the_source_is_any_state(self):
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        f.hand_present()
        self.assertTrue(f.enter_setting())
        self.assertEqual(f.state, State.SETTING)

    def test_from_boot_because_the_source_is_any_state(self):
        f = Fsm(Cart(), BinMap())
        self.assertTrue(f.enter_setting())
        self.assertEqual(f.state, State.SETTING)

    def test_refused_with_an_active_cart_and_does_not_move(self):
        cart = Cart()
        cart.set_live_grams(0, 500.0)
        cart.reset_session()
        cart.mock_pick(0, 45.0)
        f = Fsm(cart, BinMap())
        f.boot_complete()
        self.assertFalse(f.enter_setting())
        self.assertEqual(f.state, State.IDLE)

    def test_already_in_setting_is_a_no_op_not_a_refusal(self):
        """Two tablets tapping at once. Not a refusal — can_enter_setting
        still says None; there is simply nothing to do.
        """
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        f.enter_setting()
        self.assertFalse(f.enter_setting())
        self.assertIsNone(f.can_enter_setting())
        self.assertEqual(f.state, State.SETTING)


class TestExitSetting(unittest.TestCase):
    """Doc section 9.1's three exit steps, in order: refresh weights,
    re-baseline, lock the bin map.
    """

    def test_goes_to_idle(self):
        f = Fsm(Cart(), BinMap())
        f.boot_complete()
        f.enter_setting()
        self.assertTrue(f.exit_setting())
        self.assertEqual(f.state, State.IDLE)

    def test_is_a_no_op_from_any_other_state(self):
        bm = BinMap()
        f = Fsm(Cart(), bm)
        f.boot_complete()
        self.assertFalse(f.exit_setting())
        self.assertEqual(f.state, State.IDLE)
        self.assertFalse(bm.locked, "the bin map locked without ever entering setting mode")

    def test_locks_the_bin_map(self):
        """binmap.locked has been persisted and loaded since M1 and has
        never had a writer. This is it (doc section 8.2: locked is true
        in serving mode, false while setting mode is live-updating).

        MUTATION CHECKED: drop the `self.binmap.locked = True` line and
        this goes red.
        """
        bm = BinMap()
        self.assertFalse(bm.locked)
        f = Fsm(Cart(), bm)
        f.boot_complete()
        f.enter_setting()
        f.exit_setting()
        self.assertTrue(bm.locked)

    def test_refreshes_weights_BEFORE_re_baselining(self):
        """**The M2.6 trap, at the unit level.**

        Setting mode froze billing, so live_g still holds the weight each
        bin had when the mode was entered. A tray swapped during the mode
        — the entire point of the mode — means the real weight has since
        changed. reset_session() does `start_g[i] = live_g[i]`, so if the
        refresh does not happen *first*, start_g captures the stale
        weight and the difference bills the next diner.

        The refresh callback here does what core supplies for real: it
        moves the bin onto its current weight. Asserting start_g ends up
        at the NEW weight is what pins the ordering — a refresh that ran
        after reset_session() would leave start_g at the old one.

        MUTATION CHECKED: delete the `self._refresh_weights()` call from
        exit_setting() and this goes red — start_g stays 800, and
        removed_grams() becomes 700, the phantom tray-sized pick the next
        diner would have been billed for.

        This test does NOT catch the two steps being *swapped* — see
        test_the_refresh_runs_before_reset_session_not_after below for
        why that is, and for the test that does.
        """
        cart = Cart()
        cart.set_live_grams(0, 800.0)       # a full tray, session start
        cart.reset_session()

        def refresh():
            cart.seed_live_grams(0, 100.0)   # staff swapped in a near-empty one

        f = Fsm(cart, BinMap(), refresh_weights=refresh)
        f.boot_complete()
        f.enter_setting()
        f.exit_setting()

        self.assertEqual(cart.live_g[0], 100.0)
        self.assertEqual(cart.start_g[0], 100.0,
                          "start_g baselined against the tray that was swapped out")
        self.assertEqual(cart.removed_grams(0), 0.0,
                          "the tray swap billed as a pick")

    def test_the_refresh_runs_before_reset_session_not_after(self):
        """The order of exit's first two steps, pinned by call sequence
        rather than by outcome — because with the callback core actually
        supplies, **the outcome does not distinguish them.**

        Found while mutation-testing this milestone, and worth writing
        down: `_refresh_weights_from_scale` uses `cart.seed_live_grams()`,
        which sets start_g itself, so a refresh running *after*
        reset_session() lands on exactly the same three numbers. Every
        outcome assertion in this class passes with the two lines
        swapped. That is luck, not design — swap the callback to the
        ordinary `set_live_grams()` (a small, plausible edit: it is the
        method every other weight update in the codebase uses) and a
        refresh running second leaves start_g on the pre-swap weight and
        prices the whole tray.

        The M2.6 plan specifies the order, so the order is what gets
        pinned, independent of which setter the callback happens to use.

        MUTATION CHECKED: swap the two lines in exit_setting() and this
        goes red. It is the only test in the suite that does.
        """
        calls = []

        class RecordingCart(Cart):
            def reset_session(self):
                calls.append("reset_session")
                super().reset_session()

        cart = RecordingCart()
        f = Fsm(cart, BinMap(), refresh_weights=lambda: calls.append("refresh"))
        f.boot_complete()
        f.enter_setting()
        self.assertTrue(f.exit_setting())
        self.assertEqual(calls, ["refresh", "reset_session"])

    def test_exit_discards_the_sub_deadband_pick_entry_let_through(self):
        """The accepted cost of is_active() reading shown_g: a pick under
        the 10 g deadband does not refuse entry, so it is still sitting
        there at exit. reset_session() is what discards it.

        Deliberately a bin the refresh callback cannot weigh (no reading
        at all here), because for a weighable bin seed_live_grams() would
        have moved start_g anyway and this could pass without
        reset_session() ever being called.

        MUTATION CHECKED: drop `self.cart.reset_session()` from
        exit_setting() and this goes red (start_g stays 500, 5 g still
        owing).
        """
        cart = Cart()
        cart.set_live_grams(1, 500.0)
        cart.reset_session()
        cart.mock_pick(1, 5.0)
        f = Fsm(cart, BinMap())
        f.boot_complete()
        self.assertTrue(f.enter_setting(), "a sub-deadband pick refused entry")
        self.assertTrue(f.exit_setting())
        self.assertEqual(cart.start_g[1], 495.0)    # re-baselined, not zeroed
        self.assertEqual(cart.removed_grams(1), 0.0)
        self.assertEqual(cart.shown_g[1], 0.0)

    def test_a_bin_the_scale_cannot_weigh_keeps_its_placeholder(self):
        """core's refresh callback skips a bin reading None (uncalibrated,
        or no XIAO). Exit must still re-baseline that bin against
        whatever placeholder weight it holds rather than skipping it.
        """
        cart = Cart()
        for i in range(2):
            cart.set_live_grams(i, 500.0)
        cart.reset_session()
        cart.mock_pick(0, 5.0)
        cart.mock_pick(1, 5.0)

        def refresh():
            cart.seed_live_grams(0, 300.0)      # bin 0 is weighable
            # bin 1 reads None — left exactly where the mock left it

        f = Fsm(cart, BinMap(), refresh_weights=refresh)
        f.boot_complete()
        self.assertTrue(f.enter_setting())
        f.exit_setting()
        self.assertEqual(cart.start_g[0], 300.0)
        self.assertEqual(cart.start_g[1], 495.0)
        self.assertEqual(cart.removed_grams(1), 0.0)

    def test_transition_callback_fires_after_all_three_steps(self):
        """Nothing observing the transition may see a half-exited table.
        """
        bm = BinMap()
        cart = Cart()
        cart.set_live_grams(0, 800.0)
        cart.reset_session()
        seen = []

        def refresh():
            cart.seed_live_grams(0, 100.0)      # the tray swap

        def on_transition(old, new):
            if old is State.SETTING:
                seen.append((bm.locked, cart.start_g[0], cart.removed_grams(0)))

        f = Fsm(cart, bm, on_transition=on_transition, refresh_weights=refresh)
        f.boot_complete()
        f.enter_setting()
        f.exit_setting()
        self.assertEqual(seen, [(True, 100.0, 0.0)])


class TestFullLoop(unittest.TestCase):

    def test_boot_idle_selecting_idle_selecting(self):
        f = Fsm(Cart(), BinMap())
        self.assertTrue(f.boot_complete())
        self.assertTrue(f.hand_present())
        self.assertTrue(f.cancel())
        self.assertTrue(f.staff_start())
        self.assertEqual(f.state, State.SELECTING)


if __name__ == "__main__":
    unittest.main()
