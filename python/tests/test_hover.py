"""Tests for core/hover.py — doc section 9.4, M5 build item 4.

Run from the repo root:

    python -m unittest discover -s python/tests -v

Pure: `DwellTracker.update()` takes `now`, so "fires at 1200ms and not at
900ms" is a check rather than a stopwatch. Doc section 21's M5 acceptance
test — "dwell on Done → the ring fills over 1.2s and fires" — is
`test_a_dwell_fires_at_the_configured_time` below; the rig still has to
confirm a real hand can hold still enough, which no test can.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import cursorbus  # noqa: E402
from hotpot.core import hover  # noqa: E402


def pointer(x, y, hid=1):
    return cursorbus.Hand(id=hid, role=cursorbus.ROLE_POINTER, x=x, y=y,
                          conf=0.9)


def ambient(x, y, hid=2):
    return cursorbus.Hand(id=hid, role=cursorbus.ROLE_AMBIENT, x=x, y=y,
                          conf=0.9)


def frame(hands, seq=1):
    return cursorbus.CursorFrame(seq=seq, ts=0.0, hands=list(hands))


def build_widgets(*, selecting: bool = True, locales_available: int = 2):
    """The real widget set, straight out of production's own `widgets_for()`.

    Was a hand-built stand-in while `widgets_for()` returned `[]`
    (RIG_FEEDBACK_2026-08-12.md items 4-7 removed Done/Cancel/Language and
    nothing replaced them until 2026-08-24). Now that the cart's own
    Cancel/Confirm pair is real, the tests below exercise the shipped
    function instead of a mirror of it — a mirror can agree with itself
    while disagreeing with the table.

    `cart_active=True` because every test below is about a widget being
    dwellable, and an empty cart's buttons are deliberately disabled.
    """
    return hover.widgets_for(selecting=selecting,
                             locales_available=locales_available,
                             cart_active=True)


class TestAmbientIsolation(unittest.TestCase):
    """Doc section 11.4: core "discards [ambient hands] entirely before
    hit-testing"."""

    def test_an_ambient_hand_is_never_the_pointer(self):
        self.assertIsNone(hover.pick_pointer(frame([ambient(100, 100)])))

    def test_a_pointer_beside_an_ambient_hand_is_picked(self):
        h = hover.pick_pointer(frame([ambient(100, 100), pointer(500, 500)]))
        self.assertEqual(h.role, cursorbus.ROLE_POINTER)

    def test_no_frame_is_no_pointer(self):
        self.assertIsNone(hover.pick_pointer(None))

    def test_an_ambient_hand_cannot_hover_a_bin(self):
        rects = [(0.0, 0.0, 200.0, 200.0)] + [None] * 7
        # bin_under is only ever reached through pick_pointer, so this is
        # belt and braces — but it is the check that fails if someone
        # "simplifies" the call site to pass frame.hands[0].
        self.assertIsNone(hover.bin_under(rects, None))

    def test_an_ambient_hand_cannot_dwell(self):
        d = hover.DwellTracker()
        widgets = build_widgets()
        confirm = [w for w in widgets if w.id == hover.CONFIRM][0]
        cx = confirm.rect[0] + confirm.rect[2] / 2
        cy = confirm.rect[1] + confirm.rect[3] / 2
        # Ambient hand parked dead centre on Confirm for three seconds.
        now = 0.0
        for _ in range(180):
            now += 0.016
            fired = d.update(widgets, hover.pick_pointer(
                frame([ambient(cx, cy)])), now)
            self.assertIsNone(fired)
        self.assertEqual(d.fraction(hover.CONFIRM), 0.0)


class TestWidgetLayout(unittest.TestCase):

    def test_the_widgets_sit_in_the_centre_column(self):
        x0, width = hover.centre_column_px()
        for w in build_widgets():
            with self.subTest(widget=w.id):
                self.assertGreaterEqual(w.rect[0], x0)
                self.assertLessEqual(w.rect[0] + w.rect[2], x0 + width)

    def test_no_widget_overlaps_a_bin(self):
        # The failure this catches is a button drawn over a tray: a diner
        # reaching for food would dwell it. The centre column exists
        # precisely because it is the one span with no bin in it.
        from hotpot.core import bin_grid
        cad = bin_grid.cad_bin_grid_stage().rects()
        for w in build_widgets():
            wx, wy, ww, wh = w.rect
            for i, (bx, by, bw, bh) in enumerate(cad):
                overlaps = (wx < bx + bw and bx < wx + ww
                            and wy < by + bh and by < wy + wh)
                self.assertFalse(overlaps,
                                 f"widget {w.id} overlaps bin {i}")

    def test_no_two_widgets_overlap_each_other(self):
        ws = build_widgets()
        for i, a in enumerate(ws):
            for b in ws[i + 1:]:
                ax, ay, aw, ah = a.rect
                bx, by, bw, bh = b.rect
                overlaps = (ax < bx + bw and bx < ax + aw
                            and ay < by + bh and by < ay + ah)
                self.assertFalse(overlaps, f"{a.id} overlaps {b.id}")

    def test_cancel_comes_first(self):
        # Developer, 2026-08-24: "cancel button should come first." Left to
        # right, so Cancel's x is the smaller one, and the two share a row.
        ws = {w.id: w for w in build_widgets()}
        self.assertLess(ws[hover.CANCEL].rect[0], ws[hover.CONFIRM].rect[0])
        self.assertEqual(ws[hover.CANCEL].rect[1], ws[hover.CONFIRM].rect[1])

    def test_both_buttons_are_the_same_size(self):
        # Neither is the "big" one: Cancel is as reachable as Confirm, and
        # a smaller Cancel would be a harder dwell target for the action a
        # diner is most likely to want in a hurry.
        ws = {w.id: w for w in build_widgets()}
        self.assertEqual(ws[hover.CANCEL].rect[2:], ws[hover.CONFIRM].rect[2:])

    def test_the_buttons_span_the_carts_own_width(self):
        # The pair plus the gap between them is exactly the cart's width —
        # this is the number `UiLayer.cpp`'s `kCartWidthPx` mirrors, and a
        # button band wider or narrower than the cart above it is visible
        # on the table and invisible in a diff.
        ws = {w.id: w for w in build_widgets()}
        left = ws[hover.CANCEL].rect[0]
        right = ws[hover.CONFIRM].rect[0] + ws[hover.CONFIRM].rect[2]
        self.assertAlmostEqual(right - left, hover.CART_WIDTH_PX)

    def test_the_buttons_stay_on_the_table(self):
        # 1080px is the stage's near edge. A dwell target hanging off it is
        # one a hand cannot finish.
        for w in build_widgets():
            with self.subTest(widget=w.id):
                self.assertLess(w.rect[1] + w.rect[3], 1080.0)


class TestWhichWidgetsExist(unittest.TestCase):
    """2026-08-24: the cart's Cancel/Confirm pair, always both, always
    drawn — `enabled` carries whether there is anything to act on, because
    doc-section-8's cart "never moves" and a button that disappears is the
    same broken promise as a row that does.

    Done/Language stay removed (RIG_FEEDBACK_2026-08-12.md items 4-7):
    Done has nowhere to go until M6, and there is still one locale file.
    """

    def test_both_buttons_exist_outside_selecting(self):
        ids = [w.id for w in hover.widgets_for(selecting=False,
                                               locales_available=2)]
        self.assertEqual(ids, [hover.CANCEL, hover.CONFIRM])

    def test_both_buttons_exist_in_selecting_too(self):
        ids = [w.id for w in hover.widgets_for(selecting=True,
                                               locales_available=2)]
        self.assertEqual(ids, [hover.CANCEL, hover.CONFIRM])

    def test_an_empty_cart_disables_both(self):
        for w in hover.widgets_for(selecting=True, locales_available=2,
                                   cart_active=False):
            with self.subTest(widget=w.id):
                self.assertFalse(w.enabled)

    def test_a_picked_cart_enables_both(self):
        for w in hover.widgets_for(selecting=True, locales_available=2,
                                   cart_active=True):
            with self.subTest(widget=w.id):
                self.assertTrue(w.enabled)

    def test_the_default_is_disabled_not_enabled(self):
        # `cart_active` is defaulted so an un-updated caller cannot crash.
        # It must default to the SAFE side: a caller that forgot to pass it
        # gets buttons that cannot fire, never buttons that can.
        for w in hover.widgets_for(selecting=True, locales_available=2):
            with self.subTest(widget=w.id):
                self.assertFalse(w.enabled)

    def test_locale_count_does_not_resurrect_language(self):
        ids = [w.id for w in hover.widgets_for(selecting=True,
                                               locales_available=1)]
        self.assertNotIn(hover.LANGUAGE, ids)

    def test_confirm_is_not_done(self):
        # M6's SELECTING -> BROTH edge is written against `DONE`; the cart's
        # own button is a separate id so M6 is an addition, not a rename.
        ids = [w.id for w in build_widgets()]
        self.assertNotIn(hover.DONE, ids)
        self.assertNotEqual(hover.CONFIRM, hover.DONE)


class DwellCase(unittest.TestCase):

    def setUp(self):
        self.widgets = build_widgets()
        self.confirm = [w for w in self.widgets if w.id == hover.CONFIRM][0]
        self.cancel = [w for w in self.widgets if w.id == hover.CANCEL][0]
        self.d = hover.DwellTracker()

    def centre(self, widget):
        return (widget.rect[0] + widget.rect[2] / 2,
                widget.rect[1] + widget.rect[3] / 2)

    def hold(self, widget, seconds, start=0.0, step=0.016):
        """Hold the pointer on `widget` and return (fired_ids, end_time)."""
        cx, cy = self.centre(widget)
        fired = []
        now = start
        end = start + seconds
        while now < end:
            now += step
            got = self.d.update(self.widgets, pointer(cx, cy), now)
            if got:
                fired.append(got)
        return fired, now

    def idle(self, seconds, start, step=0.016):
        fired = []
        now = start
        while now < start + seconds:
            now += step
            got = self.d.update(self.widgets, None, now)
            if got:
                fired.append(got)
        return fired, now


class TestDwell(DwellCase):

    def test_a_dwell_fires_at_the_configured_time(self):
        # Doc section 21's M5 acceptance test, minus the hand.
        fired, _ = self.hold(self.confirm, 1.1)
        self.assertEqual(fired, [])
        fired, _ = self.hold(self.confirm, 0.2, start=1.1)
        self.assertEqual(fired, [hover.CONFIRM])

    def test_the_fraction_fills_from_zero_to_one(self):
        self.assertEqual(self.d.fraction(hover.CONFIRM), 0.0)
        self.hold(self.confirm, 0.6)
        self.assertGreater(self.d.fraction(hover.CONFIRM), 0.4)
        self.assertLess(self.d.fraction(hover.CONFIRM), 0.6)

    def test_the_fraction_is_zero_for_a_widget_not_being_dwelled(self):
        self.hold(self.confirm, 0.6)
        self.assertEqual(self.d.fraction(hover.CANCEL), 0.0)

    def test_it_fires_once_not_repeatedly(self):
        # A hand resting on Done must not walk the whole checkout flow.
        # This failed on the first version — resetting the accumulator on
        # fire is not enough, because the hand is still inside on the very
        # next tick. It needs a latch that only an exit clears.
        fired, _ = self.hold(self.confirm, 4.0)
        self.assertEqual(fired, [hover.CONFIRM])

    def test_leaving_and_returning_re_arms_it(self):
        # The other half of the latch: it must not be a one-shot for the
        # life of the session. A diner who fires Done, changes their mind
        # and cancels, then picks more food, has to be able to fire it
        # again.
        fired, now = self.hold(self.confirm, 1.5)
        self.assertEqual(fired, [hover.CONFIRM])
        _, now = self.idle(0.5, start=now)
        fired, _ = self.hold(self.confirm, 1.5, start=now)
        self.assertEqual(fired, [hover.CONFIRM])

    def test_a_jitter_out_and_back_does_not_re_arm_a_fired_widget(self):
        # The latch is cleared by the pointer being ANYWHERE else, so this
        # is the case worth pinning: a single frame of jitter off the
        # button after a fire re-arms it, and then a hand that never
        # really left fires a second time. 1200ms of continued rest after
        # one jittery frame must still produce nothing.
        fired, now = self.hold(self.confirm, 1.5)
        self.assertEqual(fired, [hover.CONFIRM])
        now += 0.016
        self.d.update(self.widgets, pointer(5.0, 5.0), now)     # one frame off
        fired, _ = self.hold(self.confirm, 1.0, start=now)
        self.assertEqual(fired, [])

    def test_a_short_leave_inside_the_grace_does_not_reset(self):
        # Doc section 9.4: "leaving resets to 0 after a 150ms grace (so a
        # jittery frame does not reset a nearly-complete dwell)".
        self.hold(self.confirm, 1.1)
        _, now = self.idle(0.1, start=1.1)
        fired, _ = self.hold(self.confirm, 0.2, start=now)
        self.assertEqual(fired, [hover.CONFIRM])

    def test_a_long_leave_past_the_grace_resets_to_zero(self):
        self.hold(self.confirm, 1.1)
        self.idle(0.4, start=1.1)
        self.assertEqual(self.d.fraction(hover.CONFIRM), 0.0)
        self.assertIsNone(self.d.active_id)

    def test_moving_to_another_widget_does_not_carry_the_dwell_over(self):
        # 1.1s over Cancel then 0.2s over Done must not fire Done. The bug
        # this catches would let a hand sweeping across the buttons fire
        # whichever one it happened to land on.
        self.hold(self.cancel, 1.1)
        fired, _ = self.hold(self.confirm, 0.2, start=1.1)
        self.assertEqual(fired, [])

    def test_a_disabled_widget_never_accumulates(self):
        # An empty cart's Confirm: drawn (so the diner sees it exists) but
        # not dwellable. Used to be aimed at Language with one locale
        # loaded; that widget is gone, and the empty cart is now the real
        # case this rule protects — a ring that filled over Confirm with
        # nothing picked would promise something that cannot happen.
        widgets = hover.widgets_for(selecting=True, locales_available=1,
                                    cart_active=False)
        confirm = [w for w in widgets if w.id == hover.CONFIRM][0]
        self.assertFalse(confirm.enabled)
        cx = confirm.rect[0] + confirm.rect[2] / 2
        cy = confirm.rect[1] + confirm.rect[3] / 2
        d = hover.DwellTracker()
        now = 0.0
        for _ in range(200):
            now += 0.016
            self.assertIsNone(d.update(widgets, pointer(cx, cy), now))
        self.assertEqual(d.fraction(hover.CONFIRM), 0.0)

    def test_the_first_tick_does_not_bank_time(self):
        # `update` measures the gap since the LAST call. With no previous
        # call there is no elapsed time to credit, and crediting `now`
        # itself would fire instantly on a monotonic clock that is already
        # large.
        d = hover.DwellTracker()
        cx, cy = self.centre(self.confirm)
        self.assertIsNone(d.update(self.widgets, pointer(cx, cy), 987654.0))
        self.assertEqual(d.fraction(hover.CONFIRM), 0.0)

    def test_a_custom_dwell_time_is_honoured(self):
        d = hover.DwellTracker(dwell_ms=400.0)
        cx, cy = self.centre(self.confirm)
        now, fired = 0.0, None
        while now < 0.5 and fired is None:
            now += 0.016
            fired = d.update(self.widgets, pointer(cx, cy), now)
        self.assertEqual(fired, hover.CONFIRM)
        self.assertLess(now, 0.5)

    def test_a_pointer_outside_every_widget_accumulates_nothing(self):
        d = hover.DwellTracker()
        now = 0.0
        for _ in range(200):
            now += 0.016
            self.assertIsNone(d.update(self.widgets, pointer(5.0, 5.0), now))


class TestBinHover(unittest.TestCase):

    def setUp(self):
        from hotpot.core import bin_grid
        self.rects = bin_grid.cad_bin_grid_stage().rects()

    def test_a_pointer_inside_a_bin_reports_it(self):
        x, y, w, h = self.rects[3]
        got = hover.bin_under(self.rects, pointer(x + w / 2, y + h / 2))
        self.assertEqual(got, 3)

    def test_a_pointer_between_bins_reports_none(self):
        # The 440mm centre gap. Hovering the pot must not highlight a tray.
        self.assertIsNone(hover.bin_under(self.rects, pointer(960.0, 700.0)))

    def test_an_unset_bin_is_skipped_not_treated_as_the_whole_table(self):
        rects = [None] * 8
        self.assertIsNone(hover.bin_under(rects, pointer(500.0, 500.0)))

    def test_every_bin_is_reachable(self):
        for i, (x, y, w, h) in enumerate(self.rects):
            with self.subTest(bin=i):
                self.assertEqual(
                    hover.bin_under(self.rects, pointer(x + w / 2, y + h / 2)),
                    i)


if __name__ == "__main__":
    unittest.main()
