"""Tests for core/fsm.py — M1 build item 2 (doc section 21).

Run from the repo root:

    python -m unittest discover -s python/tests -v

Scope matches the build item exactly: BOOT, IDLE, SELECTING only. Every
other state in doc section 9.1's diagram arrives with its own milestone
and has no test here yet.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.core.cart import Cart  # noqa: E402
from hotpot.core.fsm import Fsm, State  # noqa: E402


class TestStartsInBoot(unittest.TestCase):

    def test_initial_state(self):
        f = Fsm(Cart())
        self.assertEqual(f.state, State.BOOT)


class TestBootComplete(unittest.TestCase):

    def test_boot_to_idle(self):
        f = Fsm(Cart())
        self.assertTrue(f.boot_complete())
        self.assertEqual(f.state, State.IDLE)

    def test_not_legal_a_second_time(self):
        f = Fsm(Cart())
        f.boot_complete()
        self.assertFalse(f.boot_complete())
        self.assertEqual(f.state, State.IDLE)          # unchanged, not an error


class TestHandPresentAndStaffStart(unittest.TestCase):

    def test_hand_present_from_idle(self):
        f = Fsm(Cart())
        f.boot_complete()
        self.assertTrue(f.hand_present())
        self.assertEqual(f.state, State.SELECTING)

    def test_staff_start_from_idle(self):
        f = Fsm(Cart())
        f.boot_complete()
        self.assertTrue(f.staff_start())
        self.assertEqual(f.state, State.SELECTING)

    def test_hand_present_is_a_no_op_from_boot(self):
        """A hand event racing startup is ordinary traffic, not a bug —
        doc section 9.1 defines no BOOT -> SELECTING transition.
        """
        f = Fsm(Cart())
        self.assertFalse(f.hand_present())
        self.assertEqual(f.state, State.BOOT)

    def test_hand_present_is_a_no_op_once_already_selecting(self):
        f = Fsm(Cart())
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
        f = Fsm(cart)
        f.boot_complete()
        f.hand_present()
        self.assertTrue(f.cancel())
        self.assertEqual(f.state, State.IDLE)

    def test_cancel_calls_reset_session(self):
        cart = Cart()
        cart.start_g[0] = 500.0
        cart.set_live_grams(0, 350.0)       # a cart mid-order

        f = Fsm(cart)
        f.boot_complete()
        f.hand_present()
        f.cancel()

        self.assertEqual(cart.start_g[0], 350.0)   # re-baselined, not zeroed
        self.assertEqual(cart.removed_grams(0), 0.0)

    def test_cancel_is_a_no_op_from_idle_and_does_not_touch_the_cart(self):
        cart = Cart()
        cart.start_g[0] = 500.0
        cart.set_live_grams(0, 350.0)

        f = Fsm(cart)
        f.boot_complete()
        self.assertFalse(f.cancel())
        self.assertEqual(f.state, State.IDLE)
        self.assertEqual(cart.start_g[0], 500.0)    # untouched — reset_session not called

    def test_cancel_is_a_no_op_from_boot(self):
        f = Fsm(Cart())
        self.assertFalse(f.cancel())
        self.assertEqual(f.state, State.BOOT)


class TestOnTransitionCallback(unittest.TestCase):

    def test_fires_with_old_and_new_state(self):
        seen = []
        f = Fsm(Cart(), on_transition=lambda old, new: seen.append((old, new)))
        f.boot_complete()
        f.hand_present()
        self.assertEqual(seen, [(State.BOOT, State.IDLE), (State.IDLE, State.SELECTING)])

    def test_does_not_fire_on_a_rejected_transition(self):
        seen = []
        f = Fsm(Cart(), on_transition=lambda old, new: seen.append((old, new)))
        f.hand_present()                    # illegal from BOOT
        self.assertEqual(seen, [])


class TestFullLoop(unittest.TestCase):

    def test_boot_idle_selecting_idle_selecting(self):
        f = Fsm(Cart())
        self.assertTrue(f.boot_complete())
        self.assertTrue(f.hand_present())
        self.assertTrue(f.cancel())
        self.assertTrue(f.staff_start())
        self.assertEqual(f.state, State.SELECTING)


if __name__ == "__main__":
    unittest.main()
