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

    def test_the_button_row_spans_the_carts_own_width(self):
        # The three slots plus the two gaps are exactly the cart's width —
        # this is the number `UiLayer.cpp`'s `kCartWidthPx` mirrors, and a
        # button band wider or narrower than the cart above it is visible
        # on the table and invisible in a diff.
        #
        # Measured on the SLOT GRID, not on whichever buttons a given
        # screen happens to use: a screen with two buttons fills the two
        # ends and leaves the middle slot empty rather than closing up
        # (see `hover.BUTTON_SLOTS` for the developer's own row table).
        slots = hover.button_slot_rects()
        self.assertEqual(len(slots), hover.BUTTON_SLOTS)
        span = slots[-1][0] + slots[-1][2] - slots[0][0]
        self.assertAlmostEqual(span, hover.CART_WIDTH_PX)

    def test_the_cart_screen_fills_both_ends_and_leaves_the_middle_empty(self):
        """Developer, 2026-08-25: "the button should be always fill the
        left and right slot if there is only 2 buttons." Cancel used to
        sit in the middle slot with the left one empty.
        """
        slots = hover.button_slot_rects()
        ws = {w.id: w for w in build_widgets()}
        self.assertEqual(ws[hover.CANCEL].rect, slots[hover.SLOT_LEFT])
        self.assertEqual(ws[hover.CONFIRM].rect, slots[hover.SLOT_RIGHT])

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


class TestSuppressUntilExit(unittest.TestCase):
    """The screen-change guard. See `DwellTracker.suppress_until_exit`.

    The fixed button grid means no button changes position, so in
    practice `update`'s ordinary latch already covers every crossing this
    table has today. This is the guarantee stated structurally instead of
    resting on that geometry — a future screen with a different row would
    otherwise reintroduce the way to void a diner's order by standing
    still.
    """

    def _hold(self, d, widgets, hand, seconds):
        now, fired = 0.0, []
        while now < seconds:
            now += 0.016
            got = d.update(widgets, hand, now)
            if got is not None:
                fired.append(got)
        return fired

    def test_a_hand_left_on_a_new_screens_button_does_not_fire_it(self):
        broths = hover.broth_widgets(BROTHS)
        cancel = [w for w in broths if w.id == hover.CANCEL][0]
        at = pointer(cancel.rect[0] + cancel.rect[2] / 2,
                     cancel.rect[1] + cancel.rect[3] / 2)

        d = hover.DwellTracker()
        d.suppress_until_exit(broths, at)
        # Four seconds of not moving — more than three dwell periods.
        self.assertEqual(self._hold(d, broths, at, 4.0), [])
        self.assertEqual(d.fraction(hover.CANCEL), 0.0)

    def test_moving_away_and_back_re_arms_it_normally(self):
        broths = hover.broth_widgets(BROTHS)
        cancel = [w for w in broths if w.id == hover.CANCEL][0]
        at = pointer(cancel.rect[0] + cancel.rect[2] / 2,
                     cancel.rect[1] + cancel.rect[3] / 2)

        d = hover.DwellTracker()
        d.suppress_until_exit(broths, at)
        self._hold(d, broths, at, 0.5)
        self._hold(d, broths, pointer(5.0, 5.0), 0.5)     # left
        self.assertEqual(self._hold(d, broths, at, 2.0), [hover.CANCEL])

    def test_a_hand_over_nothing_suppresses_nothing(self):
        broths = hover.broth_widgets(BROTHS)
        cancel = [w for w in broths if w.id == hover.CANCEL][0]
        at = pointer(cancel.rect[0] + cancel.rect[2] / 2,
                     cancel.rect[1] + cancel.rect[3] / 2)

        d = hover.DwellTracker()
        d.suppress_until_exit(broths, pointer(5.0, 5.0))
        self.assertEqual(self._hold(d, broths, at, 2.0), [hover.CANCEL])

    def test_no_pointer_at_all_is_not_a_crash(self):
        d = hover.DwellTracker()
        d.suppress_until_exit(hover.broth_widgets(BROTHS), None)
        self.assertIsNone(d.active_id)

    def test_it_clears_a_dwell_that_was_already_part_way(self):
        # A screen change mid-dwell must not leave banked time behind for
        # whatever occupies that spot next.
        broths = hover.broth_widgets(BROTHS)
        cancel = [w for w in broths if w.id == hover.CANCEL][0]
        at = pointer(cancel.rect[0] + cancel.rect[2] / 2,
                     cancel.rect[1] + cancel.rect[3] / 2)
        d = hover.DwellTracker()
        self._hold(d, broths, at, 1.0)
        self.assertGreater(d.fraction(hover.CANCEL), 0.5)
        d.suppress_until_exit(broths, at)
        self.assertEqual(d.fraction(hover.CANCEL), 0.0)


class _FakeBroth:
    def __init__(self, bid, name, swatch="#ABCDEF"):
        self.id, self._name, self.swatch = bid, name, swatch
        self.diet, self.meta, self.note = "veg", "Not spicy", "A note."

    def display_name(self, locale=None):
        return self._name


class _FakeSpice:
    def __init__(self, level, name):
        self.level, self._name = level, name
        self.meta, self.note = "Level %d" % level, "A note."

    def display_name(self, locale=None):
        return self._name


BROTHS = [_FakeBroth("mala", "Classic Mala Broth"),
          _FakeBroth("mushroom", "Mushroom Vegan Broth"),
          _FakeBroth("collagen", "Collagen Bone Broth"),
          _FakeBroth("miso", "Miso Broth")]
SPICES = [_FakeSpice(0, "No Spice"), _FakeSpice(1, "Mild"),
          _FakeSpice(2, "Medium"), _FakeSpice(3, "Hot")]


class TestTheNavRow(unittest.TestCase):
    """2026-08-25: every screen after the cart offers Back, Cancel and one
    forward action, in that reading order and always in the same band.

    Developer: "so the user can really navigate to and fro without any
    issues." What these pin is the part a diner learns once and then
    relies on — that the row does not move, reorder or change size
    between screens.
    """

    def _row(self, widgets):
        return [w for w in widgets if w.kind == "button"]

    def test_every_screen_after_the_cart_offers_back(self):
        for name, ws in (("broth", hover.broth_widgets(BROTHS)),
                         ("spice", hover.spice_widgets(SPICES)),
                         ("checkout", hover.checkout_widgets())):
            with self.subTest(screen=name):
                self.assertIn(hover.BACK, [w.id for w in ws])

    def test_the_cart_screen_has_no_back_because_nothing_is_behind_it(self):
        self.assertNotIn(hover.BACK, [w.id for w in build_widgets()])

    def test_the_row_reads_back_cancel_forward(self):
        # Reversible actions first, the committing one last — the order
        # every checkout a diner has already used puts them in.
        row = self._row(hover.spice_widgets(SPICES))
        self.assertEqual([w.id for w in row],
                         [hover.BACK, hover.CANCEL, hover.CONFIRM])
        xs = [w.rect[0] for w in row]
        self.assertEqual(xs, sorted(xs))

    def test_every_button_lands_on_one_of_the_three_slots(self):
        """The grid never re-centres and never closes a gap.

        This is what survives of the old "no button ever moves" test.
        Since 2026-08-25 a two-button row fills the two ENDS, so a given
        id no longer keeps one rect across every screen — but every
        button on every screen still sits on one of exactly three x
        positions, which is the part a diner learns.
        """
        slots = hover.button_slot_rects()
        # The paid screen is NOT in this list, and that is deliberate
        # since 2026-08-25 — its lone Done is centred and two slots wide
        # (see the next test). Every screen with a ROW in it is here.
        for name, ws in (("cart", build_widgets()),
                         ("broth", hover.broth_widgets(BROTHS)),
                         ("spice", hover.spice_widgets(SPICES)),
                         ("payment", hover.checkout_widgets())):
            for w in self._row(ws):
                with self.subTest(screen=name, widget=w.id):
                    self.assertIn(w.rect, slots)

    def test_the_paid_screens_done_is_centred_and_two_slots_wide(self):
        """Developer, 2026-08-25: "the last done button shout be center
        aligned and double width."

        The one exception to the fixed-slot grid, and only because the
        row is a single button — there is no second button for it to line
        up with. Pinned three ways because "centred and double width" can
        be got wrong in three different ways and two of them still look
        plausible on the rig: the width has to be two slots PLUS the gap
        they would have had between them (not 2x a slot, which is
        narrower), and the centre has to be the row's centre.
        """
        row = self._row(hover.checkout_widgets(paid=True))
        self.assertEqual(len(row), 1)
        x, y, w, h = row[0].rect
        slots = hover.button_slot_rects()
        self.assertNotIn(row[0].rect, slots)
        self.assertAlmostEqual(w, slots[0][2] * 2 + hover.BUTTON_GAP_PX)
        # Same band and same height as every other button on the table.
        self.assertAlmostEqual(y, slots[0][1])
        self.assertAlmostEqual(h, slots[0][3])
        # Centred on the row, i.e. its centre is the middle slot's centre.
        self.assertAlmostEqual(x + w * 0.5, slots[1][0] + slots[1][2] * 0.5)

    def test_the_row_on_every_screen_is_the_one_the_developer_asked_for(self):
        """Developer, 2026-08-25, verbatim (— is an empty slot):

            cart      Cancel · —      · Next
            broth     Back   · Cancel · Next
            spice     Back   · Cancel · Pay
            payment   Back   · —      · Cancel
            paid      —      · —      · Done

        The paid row moved on the same day, later: its Done is centred
        and spans two slots, so it is covered by
        `test_the_paid_screens_done_is_centred_and_two_slots_wide`
        instead of by this table. The other four are unchanged.
        """
        slots = hover.button_slot_rects()
        expected = {
            "cart": [hover.CANCEL, None, hover.CONFIRM],
            "broth": [hover.BACK, hover.CANCEL, hover.CONFIRM],
            "spice": [hover.BACK, hover.CANCEL, hover.CONFIRM],
            "payment": [hover.BACK, None, hover.CANCEL],
        }
        rows = {
            "cart": build_widgets(),
            "broth": hover.broth_widgets(BROTHS),
            "spice": hover.spice_widgets(SPICES),
            "payment": hover.checkout_widgets(),
        }
        for name, want in expected.items():
            by_rect = {w.rect: w.id for w in self._row(rows[name])}
            got = [by_rect.get(slot) for slot in slots]
            self.assertEqual(got, want, f"{name}'s row")

    def test_a_two_button_row_never_leaves_an_end_empty(self):
        """The rule behind the table above, stated on its own so a new
        screen cannot quietly break it: with two buttons, the empty slot
        is the MIDDLE one.
        """
        slots = hover.button_slot_rects()
        for name, ws in (("cart", build_widgets()),
                         ("payment", hover.checkout_widgets())):
            row = self._row(ws)
            with self.subTest(screen=name):
                self.assertEqual(len(row), 2)
                self.assertEqual(sorted(w.rect for w in row),
                                 sorted([slots[hover.SLOT_LEFT],
                                         slots[hover.SLOT_RIGHT]]))

    def test_each_action_owns_its_own_slot_on_a_three_button_row(self):
        slots = hover.button_slot_rects()
        spice = {w.id: w for w in hover.spice_widgets(SPICES)}
        self.assertEqual(spice[hover.BACK].rect, slots[hover.SLOT_BACK])
        self.assertEqual(spice[hover.CANCEL].rect, slots[hover.SLOT_CANCEL])
        self.assertEqual(spice[hover.CONFIRM].rect, slots[hover.SLOT_FORWARD])

    def test_cancel_lands_under_pay_and_is_disarmed_by_the_screen_change(self):
        """**The crossing the fixed roles used to prevent by geometry, and
        the one thing that prevents it now.**

        The payment screen's Cancel occupies the slot the spice screen's
        Pay just fired from, so a hand that has not moved is sitting on
        Cancel. `DwellTracker.suppress_until_exit` — which `core/main.py`
        calls whenever the widget shape changes — is what stops that hand
        from voiding the order 1.2s later.

        Both halves are asserted: that the overlap is real (so this test
        cannot pass by the layout quietly reverting) and that a full
        dwell's worth of time on it fires nothing.
        """
        spice = hover.spice_widgets(SPICES, selected_level=1)
        pay = {w.id: w for w in spice}[hover.CONFIRM]
        hand = pointer(pay.rect[0] + pay.rect[2] / 2,
                       pay.rect[1] + pay.rect[3] / 2)
        payment = hover.checkout_widgets()
        cancel = {w.id: w for w in payment}[hover.CANCEL]
        self.assertTrue(cancel.contains(hand.x, hand.y),
                        "the crossing this test exists for is not happening")

        d = hover.DwellTracker(dwell_ms=1200.0)
        self.assertEqual(d.update(spice, hand, 0.0), None)
        self.assertEqual(d.update(spice, hand, 1.3), hover.CONFIRM)
        d.suppress_until_exit(payment, hand)
        for t in (1.4, 2.0, 3.0, 5.0):
            self.assertIsNone(d.update(payment, hand, t),
                              "a hand that never moved fired Cancel")

    def test_a_row_that_is_not_the_slot_count_is_refused(self):
        # Positional slots: a short list would silently shift Cancel into
        # the forward position.
        with self.assertRaises(ValueError):
            hover.button_row([hover.CANCEL, hover.CONFIRM])

    def test_the_forward_button_is_labelled_per_screen_not_per_id(self):
        # One id (CONFIRM) so `_fire_confirm` can dispatch on the FSM
        # state; different label keys so the diner is told what it does.
        broth = {w.id: w for w in hover.broth_widgets(BROTHS)}
        spice = {w.id: w for w in hover.spice_widgets(SPICES)}
        cart = {w.id: w for w in build_widgets()}
        self.assertEqual(cart[hover.CONFIRM].label_key, "next")
        self.assertEqual(broth[hover.CONFIRM].label_key, "next")
        self.assertEqual(spice[hover.CONFIRM].label_key, "pay")

    def test_the_payment_screen_offers_no_way_forward(self):
        # A "Done" there would be a way to clear the table without paying.
        ids = [w.id for w in hover.checkout_widgets()]
        self.assertEqual(ids, [hover.BACK, hover.CANCEL])


class TestSelectionIsNotAPageTurn(unittest.TestCase):
    """Developer, 2026-08-25: "each option button doesnt select and move to
    the next page... only when the button progress completes the previous
    button gets unselected and this button get selected."
    """

    def test_nothing_chosen_means_no_way_forward(self):
        ws = {w.id: w for w in hover.broth_widgets(BROTHS, selected_id="")}
        self.assertFalse(ws[hover.CONFIRM].enabled)
        ws = {w.id: w for w in hover.spice_widgets(SPICES, selected_level=None)}
        self.assertFalse(ws[hover.CONFIRM].enabled)

    def test_a_choice_opens_the_way_forward(self):
        ws = {w.id: w for w in hover.broth_widgets(BROTHS, selected_id="miso")}
        self.assertTrue(ws[hover.CONFIRM].enabled)

    def test_exactly_one_option_is_ever_marked_selected(self):
        ws = hover.broth_widgets(BROTHS, selected_id="collagen")
        sel = [w.id for w in ws if w.selected]
        self.assertEqual(sel, [hover.broth_widget_id("collagen")])

    def test_back_and_cancel_never_carry_a_selection(self):
        ws = hover.spice_widgets(SPICES, selected_level=3)
        for w in ws:
            if w.kind == "button":
                with self.subTest(widget=w.id):
                    self.assertFalse(w.selected)

    def test_level_zero_is_not_offered_by_the_picker(self):
        # **Supersedes the old "level zero is selectable" trap check.**
        # 2026-08-25's chili-strip drops "No Spice" (level 0) as an
        # orderable choice — developer's own call, confirmed the same
        # session: the reference picture it is modelled on has no
        # zero-chilli tier. `menu.Menu.load` still requires level 0 to
        # EXIST in `data/menu.json` (doc section 17's genuine-no-spice
        # guarantee is about the data, not the picker) — see
        # `hover.spice_widgets`'s own docstring.
        ws = {w.id: w for w in hover.spice_widgets(SPICES, selected_level=1)}
        self.assertNotIn(hover.spice_widget_id(0), ws)

    def test_an_option_never_stops_being_dwellable(self):
        # Switching choices means dwelling a DIFFERENT plate, so every
        # plate stays a live target including the one already chosen.
        for w in hover.broth_widgets(BROTHS, selected_id="mala"):
            if w.kind == "option":
                with self.subTest(widget=w.id):
                    self.assertTrue(w.enabled)


class TestTheSpiceScreen(unittest.TestCase):
    """2026-08-25, later still: the chili-strip and the vertical slider it
    became are both gone. Developer: "no need chilli icon, no need slider
    which was never implemented, instead a 2 button was implemented,
    remove that and follow exactly what is done with broth do the same
    for spice boxes as well. just 3 boxes." `hover.spice_widgets` now
    returns one full-width card per level — the same shape
    `hover.broth_widgets` already draws through `UiLayer::
    drawOptionPlate`.
    """

    def _options(self, ws):
        return [w for w in ws if w.kind == "option"]

    def test_three_boxes_one_per_level(self):
        # "just 3 boxes" — one per non-zero level, not a stop plus a card.
        self.assertEqual(len(self._options(hover.spice_widgets(SPICES))), 3)

    def test_mild_is_nearest_and_hot_is_farthest(self):
        # "Near" is the diner's own edge (the module docstring's "primary
        # action nearest the diner") — mild sits at the BOTTOM of the
        # stack, closest to the nav row, and hot at the TOP. Level 0
        # ("No Spice") is excluded — see
        # `test_level_zero_is_not_offered_by_the_picker`.
        options = self._options(hover.spice_widgets(SPICES))
        levels = [hover.parse_spice_level(w.id) for w in options]
        self.assertEqual(levels, [3, 2, 1], "top to bottom is not hot to mild")
        ys = [w.rect[1] for w in options]
        self.assertEqual(ys, sorted(ys), "top to bottom is not ascending y")

    def test_the_source_order_is_not_mutated(self):
        # `menu.Menu.load` sorts ascending and the staff view reads that
        # order; reversing in place here would silently reorder it.
        before = [s.level for s in SPICES]
        hover.spice_widgets(SPICES)
        self.assertEqual([s.level for s in SPICES], before)

    def test_a_spice_card_carries_no_icon(self):
        # Supersedes the old chilli-gauge assertions — there is no gauge
        # left to carry a count.
        for w in self._options(hover.spice_widgets(SPICES)):
            with self.subTest(widget=w.id):
                self.assertEqual(w.icon, "")
                self.assertEqual(w.icon_count, 0)

    def test_each_card_carries_its_own_note(self):
        for w in self._options(hover.spice_widgets(SPICES)):
            with self.subTest(widget=w.id):
                self.assertEqual(w.info.get("desc"), "A note.")

    def test_a_broth_carries_no_swatch_and_no_icon(self):
        # **Supersedes the old "a broth carries a swatch" test.**
        # Developer, 2026-08-25: "the coloured circle infront of the
        # broth name has to be removed." Broth cards draw the name/diet/
        # note directly (`UiLayer::drawOptionPlate`) and never read
        # `w.swatch`.
        for w in hover.broth_widgets(BROTHS):
            if w.kind != "option":
                continue
            with self.subTest(widget=w.id):
                self.assertEqual(w.swatch, "")
                self.assertEqual(w.icon, "")


class TestTheOptionsSitWhereTheCartWas(unittest.TestCase):
    """Developer, 2026-08-25: "the buttons should only take the space which
    was previously once consumedby the cart are, the top info area should
    be left to there for broth info."

    The old band straddled the info box AND the cart, which is why the
    broth plates landed on top of a cart that was still being drawn.
    """

    def test_the_option_row_clears_both_bins_idle_halo(self):
        # Developer, 2026-08-25: "the broth buttons are too long and it
        # overlaps with the halo, need to be made smaller." UiLayer's
        # idle halo reaches kHaloMarginPx(14) + kHaloRingCount(24) *
        # kHaloRingPitchPx(1.5) = 50px past a bin's own edge — mirrored
        # here as `_HALO_REACH_PX` since oF and this module cannot share
        # a constant.
        x0, col_w = hover.centre_column_px()
        left = x0 + (col_w - hover.OPTION_W_PX) * 0.5
        clearance = left - x0
        self.assertGreaterEqual(clearance, hover._HALO_REACH_PX)

    def test_no_option_overlaps_a_bin(self):
        from hotpot.core import bin_grid
        cad = bin_grid.cad_bin_grid_stage().rects()
        for w in hover.broth_widgets(BROTHS) + hover.spice_widgets(SPICES):
            wx, wy, ww, wh = w.rect
            for i, (bx, by, bw, bh) in enumerate(cad):
                overlaps = (wx < bx + bw and bx < wx + ww
                            and wy < by + bh and by < wy + wh)
                self.assertFalse(overlaps, f"widget {w.id} overlaps bin {i}")

    def test_no_two_widgets_on_a_screen_overlap(self):
        for name, ws in (("broth", hover.broth_widgets(BROTHS)),
                         ("spice", hover.spice_widgets(SPICES)),
                         ("checkout", hover.checkout_widgets())):
            for i, a in enumerate(ws):
                for b in ws[i + 1:]:
                    ax, ay, aw, ah = a.rect
                    bx, by, bw, bh = b.rect
                    overlaps = (ax < bx + bw and bx < ax + aw
                                and ay < by + bh and by < ay + ah)
                    self.assertFalse(
                        overlaps, f"{name}: {a.id} overlaps {b.id}")


class TestTheBrothCardsFillTheReclaimedBand(unittest.TestCase):
    """Developer, 2026-08-25 (same day, later): "there is no info box,
    instead the whole button is inlarged to contain the info about
    respective brothes, so u can use the complete vertical space above
    the next button row."

    **Supersedes the three `option_rects`-based tests this class used to
    have above it.** `option_rects` itself is deleted (it had no
    production caller left once spice moved to `spice_cell_rects` and
    broth to `broth_card_rects`, this codebase's standing "don't leave
    dead code dormant" rule) — these test the function that actually
    replaced its broth role.
    """

    def test_broth_cards_reclaim_the_old_info_box_band(self):
        # The whole point of the redesign: the FIRST (topmost) card
        # starts where the shared info box used to, not where the old
        # option row did.
        rects = hover.broth_card_rects(3)
        self.assertAlmostEqual(
            rects[0][1],
            hover._INFO_BOX_TOP_PX + hover._PAGE_HEADER_PX_ESTIMATE)

    def test_broth_cards_stay_above_the_button_row(self):
        # A card overlapping Next would mean a hand reaching for the
        # button chose a broth instead.
        for r in hover.broth_card_rects(3):
            self.assertLessEqual(r[1] + r[3], hover.BUTTONS_TOP_PX)

    def test_broth_cards_never_overlap_each_other(self):
        rects = hover.broth_card_rects(3)
        ys = [r[1] for r in rects]
        self.assertEqual(ys, sorted(ys))
        for a, b in zip(rects, rects[1:]):
            self.assertLessEqual(a[1] + a[3], b[1])

    def test_too_many_broth_cards_fail_loudly_rather_than_overflowing(self):
        # A silent overflow would put a dwellable card under the button
        # row — obvious on the table, invisible in a diff.
        with self.assertRaises(ValueError):
            hover.broth_card_rects(20)


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
